from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
import hashlib
import ipaddress
import json
import mimetypes
from pathlib import Path
import re
from socket import gaierror, getaddrinfo
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.memory.models.raw_event import (
    ProcessingStatus,
    RawEvent,
    SensitivityLevel,
    SourceType,
    VisibilityScope,
)
from src.memory.services.event_ingestion import EventIngestionService
from src.platform.models.media_artifact import MediaArtifact
from src.platform.services.media_extractors import select_extractor
from src.platform.services.media_extractors.base import LocalExtractionInput
from src.shared.config import MEDIA_STORAGE_DIR, settings
from src.shared.ids.id_generator import generate_id
from src.shared.utils.hash import compute_content_hash


URL_PATTERN = re.compile(r"https?://[^\s<>'\"，。！？、）)]+", re.IGNORECASE)
MAX_LINK_TEXT_CHARS = 20_000
MAX_EXTRACTED_TEXT_CHARS = 20_000
CHUNK_SIZE = 1024 * 1024
_extraction_semaphore: asyncio.Semaphore | None = None
_extraction_semaphore_limit: int | None = None


@dataclass(frozen=True)
class ExtractedNote:
    title: str
    summary: str
    text: str
    structured_data: dict
    source_url: str
    confidence: float
    warnings: list[str]


def extract_urls(text: str) -> list[str]:
    seen: set[str] = set()
    urls: list[str] = []
    for match in URL_PATTERN.finditer(text or ""):
        url = match.group(0).rstrip(".,;:!?，。；：！？")
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


async def create_wecom_media_placeholder(
    db: AsyncSession,
    *,
    user_id: str,
    msg,
) -> tuple[RawEvent, MediaArtifact]:
    media_type = normalize_wecom_media_type(msg.msg_type)
    raw_payload = sanitize_wecom_payload(msg.raw)
    content = f"企业微信收到{media_type}消息，等待后续解析。"
    event = (await EventIngestionService(db).append(
        source_type=SourceType.MANUAL,
        source_id="wecom",
        user_id=user_id,
        occurred_at=datetime.now(timezone.utc),
        content=content,
        content_hash=compute_content_hash(content + msg.msg_id),
        event_metadata={
            "channel": "wecom",
            "event_kind": "media_note",
            "media_type": media_type,
            "extraction_status": "received",
            "wecom_msg_id": msg.msg_id,
            "wecom_user_id": msg.from_user,
            "wecom_chat_id": msg.chat_id,
            "wecom_chat_type": msg.chat_type,
            "wecom_raw_payload_shape": payload_shape(raw_payload),
        },
        sensitivity=SensitivityLevel.NORMAL,
        visibility_scope=VisibilityScope.PERSONAL,
        processing_status=ProcessingStatus.QUEUED,
    )).event
    artifact = MediaArtifact(
        id=generate_id("media"),
        user_id=user_id,
        raw_event_id=event.id,
        source_channel="wecom",
        message_id=msg.msg_id,
        media_type=media_type,
        original_name=_extract_original_name(msg.raw),
        mime_type=_extract_mime_type(msg.raw),
        size_bytes=_extract_size(msg.raw),
        wecom_media_id=_extract_wecom_media_id(msg.raw),
        status="received",
        artifact_metadata={"raw_payload_shape": payload_shape(raw_payload)},
    )
    db.add(artifact)
    event.event_metadata = {**(event.event_metadata or {}), "media_artifact_id": artifact.id}
    return event, artifact


async def create_uploaded_media_artifact(
    db: AsyncSession,
    *,
    user_id: str,
    fileobj,
    filename: str,
    source_channel: str = "api",
    mime_type: str | None = None,
    media_type: str | None = None,
) -> tuple[RawEvent, MediaArtifact]:
    safe_name = sanitize_filename(filename)
    guessed_mime = normalize_mime_type(mime_type or mimetypes.guess_type(safe_name)[0])
    assert_mime_allowed(guessed_mime)
    assert_filename_matches_mime(safe_name, guessed_mime)
    artifact_id = generate_id("media")
    relative_path = Path("uploads") / artifact_id[:12] / safe_name
    target_path = MEDIA_STORAGE_DIR / relative_path
    target_path.parent.mkdir(parents=True, exist_ok=True)

    size_bytes, sha256 = _copy_file_with_limit(fileobj, target_path, settings.MEDIA_MAX_FILE_SIZE_BYTES)
    duplicate = await _find_duplicate_artifact(db, user_id=user_id, sha256=sha256)
    if duplicate:
        target_path.unlink(missing_ok=True)
        duplicate.artifact_metadata = {
            **(duplicate.artifact_metadata or {}),
            "duplicate_upload_last_seen_at": datetime.now(timezone.utc).isoformat(),
        }
        event = await db.get(RawEvent, duplicate.raw_event_id)
        if event:
            return event, duplicate
    inferred_type = media_type or infer_media_type(filename=safe_name, mime_type=guessed_mime)
    content = f"上传媒体素材：{safe_name} ({inferred_type})"
    event = (await EventIngestionService(db).append(
        source_type=SourceType.FILE_IMPORT,
        source_id=source_channel,
        user_id=user_id,
        occurred_at=datetime.now(timezone.utc),
        content=content,
        content_hash=compute_content_hash(f"{safe_name}\n{sha256}"),
        event_metadata={
            "channel": source_channel,
            "event_kind": "media_note",
            "media_type": inferred_type,
            "extraction_status": "downloaded",
            "original_name": safe_name,
        },
        sensitivity=SensitivityLevel.NORMAL,
        visibility_scope=VisibilityScope.PERSONAL,
        processing_status=ProcessingStatus.QUEUED,
    )).event
    artifact = MediaArtifact(
        id=artifact_id,
        user_id=user_id,
        raw_event_id=event.id,
        source_channel=source_channel,
        media_type=inferred_type,
        original_name=safe_name,
        mime_type=guessed_mime,
        size_bytes=size_bytes,
        sha256=sha256,
        storage_path=str(relative_path).replace("\\", "/"),
        status="downloaded",
        artifact_metadata={"upload_source": source_channel},
    )
    db.add(artifact)
    event.event_metadata = {**(event.event_metadata or {}), "media_artifact_id": artifact.id}
    return event, artifact


async def save_media_bytes_to_artifact(
    db: AsyncSession,
    *,
    artifact: MediaArtifact,
    data: bytes,
    filename: str | None = None,
    mime_type: str | None = None,
) -> MediaArtifact:
    if not data:
        raise ValueError("media_data_empty")
    if len(data) > settings.MEDIA_MAX_FILE_SIZE_BYTES:
        raise ValueError("media_file_too_large")
    safe_name = sanitize_filename(filename or artifact.original_name or f"{artifact.id}.bin")
    guessed_mime = normalize_mime_type(mime_type or artifact.mime_type or mimetypes.guess_type(safe_name)[0])
    assert_mime_allowed(guessed_mime)
    assert_filename_matches_mime(safe_name, guessed_mime)
    relative_path = Path("uploads") / artifact.id[:12] / safe_name
    target_path = MEDIA_STORAGE_DIR / relative_path
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_bytes(data)

    artifact.original_name = artifact.original_name or safe_name
    artifact.mime_type = guessed_mime
    artifact.size_bytes = len(data)
    artifact.sha256 = hashlib.sha256(data).hexdigest()
    artifact.storage_path = str(relative_path).replace("\\", "/")
    artifact.media_type = infer_media_type(filename=safe_name, mime_type=guessed_mime)
    artifact.status = "downloaded"
    artifact.error_message = None
    artifact.artifact_metadata = {
        **(artifact.artifact_metadata or {}),
        "downloaded": True,
    }
    await db.flush()
    return artifact


async def create_link_note_from_text(
    db: AsyncSession,
    *,
    user_id: str,
    text: str,
    source_raw_event_id: str | None,
    source_channel: str = "wecom",
    message_id: str | None = None,
) -> list[str]:
    memory_ids: list[str] = []
    for url in extract_urls(text):
        event, artifact = await create_link_note(
            db,
            user_id=user_id,
            url=url,
            source_text=text,
            source_raw_event_id=source_raw_event_id,
            source_channel=source_channel,
            message_id=message_id,
        )
        note = artifact.artifact_metadata.get("extracted_note") if artifact.artifact_metadata else None
        memory_id = await _materialize_media_memory(
            db,
            event=event,
            artifact=artifact,
            note=note,
        )
        if memory_id:
            memory_ids.append(memory_id)
    return memory_ids


async def create_link_artifacts_from_text(
    db: AsyncSession,
    *,
    user_id: str,
    text: str,
    source_raw_event_id: str | None,
    source_channel: str = "wecom",
    message_id: str | None = None,
) -> list[MediaArtifact]:
    artifacts: list[MediaArtifact] = []
    for url in extract_urls(text):
        try:
            event, artifact = await create_link_artifact(
                db,
                user_id=user_id,
                url=url,
                source_text=text,
                source_raw_event_id=source_raw_event_id,
                source_channel=source_channel,
                message_id=message_id,
            )
        except ValueError:
            continue
        event.event_metadata = {**(event.event_metadata or {}), "media_artifact_id": artifact.id}
        artifacts.append(artifact)
    return artifacts


async def create_link_artifact(
    db: AsyncSession,
    *,
    user_id: str,
    url: str,
    source_text: str,
    source_raw_event_id: str | None,
    source_channel: str,
    message_id: str | None,
) -> tuple[RawEvent, MediaArtifact]:
    assert_http_url_fast(url)
    artifact_id = generate_id("media")
    title = _fallback_title_from_url(url)
    content = f"链接素材：{title}\n来源：{url}\n状态：等待后台抓取并整理成笔记。"
    event = (await EventIngestionService(db).append(
        source_type=SourceType.MANUAL,
        source_id=source_channel,
        user_id=user_id,
        occurred_at=datetime.now(timezone.utc),
        content=content,
        content_hash=compute_content_hash(f"{url}\n{source_raw_event_id or ''}"),
        event_metadata={
            "channel": source_channel,
            "event_kind": "media_note",
            "media_type": "link",
            "source_url": url,
            "source_raw_event_id": source_raw_event_id,
            "extraction_status": "received",
            "extractor_name": "trafilatura_or_fallback",
            "wecom_msg_id": message_id,
            "source_text_preview": (source_text or "")[:500],
        },
        sensitivity=SensitivityLevel.NORMAL,
        visibility_scope=VisibilityScope.PERSONAL,
        processing_status=ProcessingStatus.QUEUED,
    )).event
    artifact = MediaArtifact(
        id=artifact_id,
        user_id=user_id,
        raw_event_id=event.id,
        source_channel=source_channel,
        message_id=message_id,
        media_type="link",
        source_url=url,
        status="received",
        extractor_name="trafilatura_or_fallback",
        artifact_metadata={
            "source_raw_event_id": source_raw_event_id,
            "source_text_preview": (source_text or "")[:500],
        },
    )
    db.add(artifact)
    return event, artifact


async def create_link_note(
    db: AsyncSession,
    *,
    user_id: str,
    url: str,
    source_text: str,
    source_raw_event_id: str | None,
    source_channel: str,
    message_id: str | None,
) -> tuple[RawEvent, MediaArtifact]:
    artifact_id = generate_id("media")
    artifact_metadata: dict = {"source_raw_event_id": source_raw_event_id}

    try:
        note = await extract_link_note(url)
        status = "extracted"
        error_message = None
        artifact_metadata["extracted_note"] = {
            "title": note.title,
            "summary": note.summary,
            "text": note.text[:MAX_LINK_TEXT_CHARS],
            "structured_data": note.structured_data,
            "source_url": note.source_url,
            "confidence": note.confidence,
            "warnings": note.warnings,
        }
    except Exception as exc:
        note = ExtractedNote(
            title=_fallback_title_from_url(url),
            summary="链接内容暂时无法自动抓取，已保存 URL，后续可手动补充。",
            text="",
            structured_data={},
            source_url=url,
            confidence=0.25,
            warnings=[str(exc)],
        )
        status = "failed"
        error_message = str(exc)[:1000]
        artifact_metadata["extracted_note"] = {
            "title": note.title,
            "summary": note.summary,
            "text": "",
            "structured_data": {},
            "source_url": url,
            "confidence": note.confidence,
            "warnings": note.warnings,
        }

    content = _format_link_raw_event_content(note)
    event = (await EventIngestionService(db).append(
        source_type=SourceType.MANUAL,
        source_id=source_channel,
        user_id=user_id,
        occurred_at=datetime.now(timezone.utc),
        content=content,
        content_hash=compute_content_hash(f"{url}\n{content}"),
        event_metadata={
            "channel": source_channel,
            "event_kind": "media_note",
            "media_type": "link",
            "media_artifact_id": artifact_id,
            "source_url": url,
            "source_raw_event_id": source_raw_event_id,
            "note_title": note.title,
            "extraction_status": status,
            "extractor_name": "trafilatura_or_fallback",
            "wecom_msg_id": message_id,
            "quality_score": note.confidence,
            "needs_follow_up": note.confidence < 0.55,
        },
        sensitivity=SensitivityLevel.NORMAL,
        visibility_scope=VisibilityScope.PERSONAL,
        processing_status=ProcessingStatus.COMPLETED if status == "extracted" else ProcessingStatus.FAILED,
    )).event
    artifact = MediaArtifact(
        id=artifact_id,
        user_id=user_id,
        raw_event_id=event.id,
        source_channel=source_channel,
        message_id=message_id,
        media_type="link",
        source_url=url,
        status=status,
        extractor_name="trafilatura_or_fallback",
        artifact_metadata=artifact_metadata,
        error_message=error_message,
    )
    db.add(artifact)
    return event, artifact


async def extract_stored_artifact(
    db: AsyncSession,
    *,
    artifact_id: str,
) -> tuple[RawEvent, str | None]:
    artifact = await db.get(MediaArtifact, artifact_id)
    if not artifact:
        raise ValueError("media_artifact_not_found")
    existing = await _existing_extraction_result(db, artifact)
    if existing:
        return existing
    if artifact.media_type == "link":
        return await extract_link_artifact(db, artifact=artifact)
    path = _resolve_artifact_path(artifact.storage_path)
    _validate_local_media_file(path)
    if not artifact.sha256:
        artifact.sha256 = _sha256_file(path)
    if not artifact.size_bytes:
        artifact.size_bytes = path.stat().st_size
    if not artifact.mime_type:
        artifact.mime_type = mimetypes.guess_type(path.name)[0]

    extractor = select_extractor(artifact, path)
    artifact.status = "processing"
    await db.flush()

    try:
        note = await _run_extraction_with_limits(
            extractor.extract(LocalExtractionInput(artifact=artifact, path=path))
        )
        text_path, json_path = _write_extraction_outputs(artifact.id, note)
        artifact.status = _extraction_status_for_note(note)
        artifact.extractor_name = extractor.name
        artifact.extractor_version = extractor.version
        artifact.extracted_text_path = _to_storage_relative_path(text_path)
        artifact.extracted_json_path = _to_storage_relative_path(json_path)
        artifact.error_message = None
        note_payload = _note_to_dict(note)
        event = await create_raw_event_for_extracted_note(db, artifact=artifact, note=note)
        memory_id = await _materialize_media_memory(
            db,
            event=event,
            artifact=artifact,
            note=note_payload,
        )
        artifact.artifact_metadata = {
            **(artifact.artifact_metadata or {}),
            "extracted_note": note_payload,
            "extracted_event_id": event.id,
            "memory_id": memory_id,
        }
        await db.commit()
        return event, memory_id
    except Exception as exc:
        # 先回滚，丢弃 try 块中可能已 add 的事件和未提交治理事务
        await db.rollback()
        # rollback 后对象属性已过期，重新加载以避免触发同步懒加载（async 模式下会 MissingGreenlet）
        artifact = await db.get(MediaArtifact, artifact_id)
        artifact.status = "failed"
        artifact.error_message = str(exc)[:1000]
        artifact.artifact_metadata = {
            **(artifact.artifact_metadata or {}),
            "extraction_error": artifact.error_message,
        }
        await db.commit()
        raise


async def extract_link_artifact(
    db: AsyncSession,
    *,
    artifact: MediaArtifact,
) -> tuple[RawEvent, str | None]:
    if not artifact.source_url:
        raise ValueError("source_url_required")
    artifact.status = "processing"
    await db.flush()
    try:
        note = await _run_extraction_with_limits(extract_link_note(artifact.source_url))
        status = "extracted"
        error_message = None
    except Exception as exc:
        note = ExtractedNote(
            title=_fallback_title_from_url(artifact.source_url),
            summary="链接内容暂时无法自动抓取，已保存 URL，后续可手动补充。",
            text="",
            structured_data={},
            source_url=artifact.source_url,
            confidence=0.25,
            warnings=[str(exc)],
        )
        status = "failed"
        error_message = str(exc)[:1000]

    note_payload = _note_to_dict(note)
    artifact.status = status
    artifact.extractor_name = "trafilatura_or_fallback"
    artifact.error_message = error_message
    event = await create_raw_event_for_extracted_note(db, artifact=artifact, note=note)
    memory_id = await _materialize_media_memory(
        db,
        event=event,
        artifact=artifact,
        note=note_payload,
    )
    artifact.artifact_metadata = {
        **(artifact.artifact_metadata or {}),
        "extracted_note": note_payload,
        "extracted_event_id": event.id,
        "memory_id": memory_id,
    }
    if error_message:
        artifact.artifact_metadata = {
            **(artifact.artifact_metadata or {}),
            "extraction_error": error_message,
        }
    await db.commit()
    return event, memory_id


async def create_raw_event_for_extracted_note(
    db: AsyncSession,
    *,
    artifact: MediaArtifact,
    note: ExtractedNote,
) -> RawEvent:
    content = _format_media_raw_event_content(artifact, note)
    return (await EventIngestionService(db).append(
        source_type=SourceType.FILE_IMPORT,
        source_id=artifact.source_channel,
        user_id=artifact.user_id,
        occurred_at=datetime.now(timezone.utc),
        content=content,
        content_hash=compute_content_hash(f"{artifact.id}\n{content}"),
        event_metadata={
            "channel": artifact.source_channel,
            "event_kind": "media_note",
            "media_type": artifact.media_type,
            "media_artifact_id": artifact.id,
            "source_raw_event_id": artifact.raw_event_id,
            "source_url": artifact.source_url,
            "note_title": note.title,
            "extraction_status": artifact.status,
            "extractor_name": artifact.extractor_name,
            "quality_score": note.confidence,
            "needs_follow_up": note.confidence < 0.55 or bool(note.warnings),
            "warnings": note.warnings,
        },
        sensitivity=SensitivityLevel.NORMAL,
        visibility_scope=VisibilityScope.PERSONAL,
        processing_status=ProcessingStatus.COMPLETED if note.text or note.summary else ProcessingStatus.FAILED,
    )).event


async def extract_link_note(url: str) -> ExtractedNote:
    await assert_public_http_url(url)
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=httpx.Timeout(10.0),
        headers={"User-Agent": "LifeMemoryBot/1.0"},
    ) as client:
        response = await client.get(url)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if "text/html" not in content_type and "application/xhtml" not in content_type:
            raise ValueError(f"unsupported_content_type:{content_type[:80]}")
        html = response.text[:1_000_000]

    extracted = _extract_with_trafilatura(html, url)
    extracted_text = str(extracted.get("text") or "")
    title = str(extracted.get("title") or _extract_title(html) or _fallback_title_from_url(url))
    warnings = list(extracted.get("warnings") or [])
    if not extracted_text:
        extracted_text = _strip_html_text(html)
        warnings.append("trafilatura_empty_used_html_fallback")
    text = (extracted_text or "").strip()[:MAX_LINK_TEXT_CHARS]
    if not text:
        raise ValueError("empty_extracted_text")
    summary = _build_summary(text)
    video_metadata = await _extract_video_link_metadata(url)
    if video_metadata:
        video_text = _format_video_link_metadata_text(video_metadata)
        if video_text and video_text not in text:
            text = f"{text}\n\n公开视频元信息：\n{video_text}"[:MAX_LINK_TEXT_CHARS]
        if video_metadata.get("description") and not summary:
            summary = _build_summary(str(video_metadata["description"]))
    structured_data = {
        "content_length": len(text),
        "title": title,
        "site_name": extracted.get("site_name") or "",
        "author": extracted.get("author") or "",
        "published_at": extracted.get("published_at") or "",
        "description": extracted.get("description") or "",
        "extractor_name": extracted.get("extractor_name") or "html_fallback",
    }
    if video_metadata:
        structured_data["video_metadata"] = video_metadata
    return ExtractedNote(
        title=title[:180],
        summary=summary,
        text=text,
        structured_data=structured_data,
        source_url=url,
        confidence=0.78 if len(text) >= 300 else 0.55,
        warnings=warnings if len(text) >= 300 else [*warnings, "正文较短，可能抽取不完整"],
    )


async def _extract_video_link_metadata(url: str) -> dict:
    if not settings.MEDIA_ENABLE_YTDLP:
        return {}
    try:
        return await asyncio.to_thread(_extract_video_link_metadata_sync, url)
    except Exception as exc:
        return {"extractor_name": "yt_dlp", "error": str(exc)[:160]}


def _extract_video_link_metadata_sync(url: str) -> dict:
    try:
        from yt_dlp import YoutubeDL
    except Exception as exc:
        raise RuntimeError("yt_dlp_not_installed") from exc

    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "extract_flat": False,
        "socket_timeout": 10,
    }
    with YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=False)
    if not isinstance(info, dict):
        return {}
    subtitles = info.get("subtitles") if isinstance(info.get("subtitles"), dict) else {}
    automatic_captions = (
        info.get("automatic_captions")
        if isinstance(info.get("automatic_captions"), dict)
        else {}
    )
    return {
        "extractor_name": "yt_dlp",
        "title": str(info.get("title") or "")[:300],
        "uploader": str(info.get("uploader") or info.get("channel") or "")[:200],
        "duration_seconds": info.get("duration"),
        "webpage_url": str(info.get("webpage_url") or url)[:1000],
        "description": str(info.get("description") or "")[:5000],
        "subtitle_languages": sorted(str(key) for key in subtitles.keys())[:30],
        "automatic_caption_languages": sorted(str(key) for key in automatic_captions.keys())[:30],
    }


def _format_video_link_metadata_text(metadata: dict) -> str:
    if metadata.get("error"):
        return f"yt-dlp 元信息提取失败：{metadata['error']}"
    lines: list[str] = []
    if metadata.get("title"):
        lines.append(f"标题：{metadata['title']}")
    if metadata.get("uploader"):
        lines.append(f"作者/频道：{metadata['uploader']}")
    if metadata.get("duration_seconds"):
        lines.append(f"时长：{metadata['duration_seconds']} 秒")
    if metadata.get("subtitle_languages"):
        lines.append("字幕语言：" + ", ".join(metadata["subtitle_languages"]))
    if metadata.get("automatic_caption_languages"):
        lines.append("自动字幕语言：" + ", ".join(metadata["automatic_caption_languages"]))
    if metadata.get("description"):
        lines.append("描述：" + str(metadata["description"])[:1000])
    return "\n".join(lines)


async def _run_extraction_with_limits(coro):
    semaphore = _get_extraction_semaphore()
    timeout = max(1e-3, float(settings.MEDIA_EXTRACTION_TIMEOUT_SECONDS))
    try:
        async with semaphore:
            return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError as exc:
        raise RuntimeError("media_extraction_timeout") from exc


def _get_extraction_semaphore() -> asyncio.Semaphore:
    global _extraction_semaphore, _extraction_semaphore_limit
    limit = max(1, int(settings.MEDIA_EXTRACTION_CONCURRENCY or 1))
    if _extraction_semaphore is None or _extraction_semaphore_limit != limit:
        _extraction_semaphore = asyncio.Semaphore(limit)
        _extraction_semaphore_limit = limit
    return _extraction_semaphore


async def assert_public_http_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("invalid_url")
    if parsed.username or parsed.password:
        raise ValueError("url_credentials_not_allowed")
    hostname = parsed.hostname
    try:
        infos = getaddrinfo(hostname, None)
    except gaierror as exc:
        raise ValueError("dns_lookup_failed") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
            raise ValueError("private_or_reserved_address_not_allowed")


def assert_http_url_fast(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("invalid_url")
    if parsed.username or parsed.password:
        raise ValueError("url_credentials_not_allowed")
    try:
        ip = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        if parsed.hostname.lower() in {"localhost", "localhost.localdomain"}:
            raise ValueError("localhost_not_allowed")
        return
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
        raise ValueError("private_or_reserved_address_not_allowed")


def build_media_memory_proposal(
    *,
    event: RawEvent,
    artifact: MediaArtifact,
    note: dict | None,
) -> dict:
    note = note or {}
    confidence = float(note.get("confidence") or 0.35)
    media_type = artifact.media_type or "media"
    title = str(note.get("title") or event.event_metadata.get("note_title") or "企业微信媒体笔记")[:180]
    summary = str(note.get("summary") or "")
    source = artifact.source_url or artifact.original_name or artifact.storage_path or artifact.id
    body = f"{summary}\n\n来源：{source}".strip()
    if note.get("text"):
        body = f"{body}\n\n摘录：\n{str(note.get('text'))[:1200]}"
    if note.get("warnings"):
        body = f"{body}\n\n注意：{'；'.join(str(item) for item in note.get('warnings', [])[:3])}"
    return {
        "memory_type": "insight",
        "title": f"{_media_memory_label(media_type)}：{title}",
        "content": body,
        "importance": 0.58,
        "confidence": max(0.25, min(0.85, confidence)),
        "sensitivity": getattr(event.sensitivity, "value", event.sensitivity),
        "reason": (
            f"Created from {artifact.source_channel} link media note. "
            f"artifact={artifact.id}; status={artifact.status}"
        ),
        "entities": [
            artifact.source_channel,
            "media_note",
            media_type,
            artifact.extractor_name or "media_extractor",
        ],
    }


async def _materialize_media_memory(
    db: AsyncSession,
    *,
    event: RawEvent,
    artifact: MediaArtifact,
    note: dict | None,
) -> str | None:
    from src.execution.runtime.working_coordinator import WorkingCoordinator

    proposal = build_media_memory_proposal(event=event, artifact=artifact, note=note)
    memory_ids = await WorkingCoordinator(db).materialize_preclassified(
        event=event,
        proposals=(proposal,),
        origin="media_extraction",
    )
    return memory_ids[0] if memory_ids else None


def normalize_wecom_media_type(msg_type: str) -> str:
    mapping = {
        "image": "image",
        "file": "file",
        "video": "video",
        "voice": "audio",
        "audio": "audio",
        "mixed": "mixed",
    }
    return mapping.get((msg_type or "").lower(), "unknown")


def infer_media_type(*, filename: str, mime_type: str | None) -> str:
    suffix = Path(filename or "").suffix.lower()
    mime = normalize_mime_type(mime_type)
    if mime.startswith("image/") or suffix in {".jpg", ".jpeg", ".png", ".webp"}:
        return "image"
    if mime.startswith("audio/") or suffix in {".mp3", ".wav", ".m4a"}:
        return "audio"
    if mime.startswith("video/") or suffix in {".mp4", ".mov"}:
        return "video"
    if mime == "application/pdf" or suffix == ".pdf":
        return "pdf"
    if mime in {
        "text/csv",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-excel",
    } or suffix in {".csv", ".xlsx", ".xlsm"}:
        return "spreadsheet"
    if mime in {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "text/html",
    } or suffix in {".docx", ".pptx", ".html", ".htm"}:
        return "document"
    if mime.startswith("text/") or suffix in {".txt", ".md", ".markdown"}:
        return "file"
    return "file"


def sanitize_filename(filename: str) -> str:
    name = Path(filename or "upload.bin").name.strip()
    name = re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+", "_", name)
    name = name.strip("._") or "upload.bin"
    return name[:180]


def assert_mime_allowed(mime_type: str | None) -> None:
    allowed = {item.strip().lower() for item in settings.MEDIA_ALLOWED_MIME_TYPES.split(",") if item.strip()}
    mime = normalize_mime_type(mime_type)
    if allowed and mime not in allowed:
        raise ValueError(f"unsupported_mime_type:{mime}")


def assert_filename_matches_mime(filename: str, mime_type: str | None) -> None:
    suffix = Path(filename or "").suffix.lower()
    if not suffix:
        return
    mime = normalize_mime_type(mime_type)
    allowed_suffixes = _mime_allowed_suffixes().get(mime)
    if allowed_suffixes is None:
        return
    if suffix not in allowed_suffixes:
        raise ValueError(f"mime_extension_mismatch:{mime}:{suffix}")


def _mime_allowed_suffixes() -> dict[str, set[str]]:
    return {
        "image/jpeg": {".jpg", ".jpeg"},
        "image/png": {".png"},
        "image/webp": {".webp"},
        "text/plain": {".txt"},
        "text/markdown": {".md", ".markdown"},
        "text/html": {".html", ".htm"},
        "text/csv": {".csv"},
        "application/pdf": {".pdf"},
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {".xlsx"},
        "application/vnd.ms-excel": {".xls"},
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": {".docx"},
        "application/vnd.openxmlformats-officedocument.presentationml.presentation": {".pptx"},
        "audio/mpeg": {".mp3"},
        "audio/wav": {".wav"},
        "video/mp4": {".mp4"},
    }


def normalize_mime_type(mime_type: str | None) -> str:
    raw = (mime_type or "application/octet-stream").strip().lower()
    return raw.split(";", 1)[0].strip() or "application/octet-stream"


def sanitize_wecom_payload(payload: dict) -> dict:
    sensitive_keys = {"secret", "token", "access_token", "password"}
    output = {}
    for key, value in (payload or {}).items():
        if key.lower() in sensitive_keys:
            output[key] = "***"
        elif isinstance(value, dict):
            output[key] = sanitize_wecom_payload(value)
        elif isinstance(value, list):
            output[key] = [sanitize_wecom_payload(item) if isinstance(item, dict) else _safe_scalar(item) for item in value[:20]]
        else:
            output[key] = _safe_scalar(value)
    return output


def payload_shape(payload) -> dict | str:
    if isinstance(payload, dict):
        return {key: payload_shape(value) for key, value in payload.items()}
    if isinstance(payload, list):
        return [payload_shape(payload[0])] if payload else []
    return type(payload).__name__


def _safe_scalar(value):
    if isinstance(value, str):
        return value[:300]
    return value


def _extract_with_trafilatura(html: str, url: str) -> dict:
    try:
        import trafilatura
    except Exception as exc:
        return {
            "text": "",
            "extractor_name": "html_fallback",
            "warnings": [f"trafilatura_unavailable:{type(exc).__name__}"],
        }
    try:
        extracted = None
        if hasattr(trafilatura, "bare_extraction"):
            extracted = trafilatura.bare_extraction(
                html,
                url=url,
                include_comments=False,
                include_tables=True,
                with_metadata=True,
            )
        payload = _trafilatura_payload_to_dict(extracted)
        text = str(payload.get("text") or payload.get("raw_text") or "").strip()
        if not text:
            text = trafilatura.extract(html, url=url, include_comments=False, include_tables=True) or ""
        return {
            "text": text,
            "title": payload.get("title") or "",
            "site_name": payload.get("sitename") or payload.get("hostname") or "",
            "author": payload.get("author") or "",
            "published_at": payload.get("date") or "",
            "description": payload.get("description") or "",
            "extractor_name": "trafilatura",
            "warnings": [],
        }
    except Exception as exc:
        return {
            "text": "",
            "extractor_name": "html_fallback",
            "warnings": [f"trafilatura_failed:{type(exc).__name__}"],
        }


def _trafilatura_payload_to_dict(payload) -> dict:
    if not payload:
        return {}
    if isinstance(payload, dict):
        return payload
    return {
        key: getattr(payload, key, "")
        for key in ("text", "raw_text", "title", "sitename", "hostname", "author", "date", "description")
        if getattr(payload, key, None)
    }


class _TitleParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_title = False
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self.in_title = True
        if tag in {"script", "style", "noscript"}:
            self.skip_depth += 1

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        if tag in {"script", "style", "noscript"} and self.skip_depth:
            self.skip_depth -= 1

    def handle_data(self, data):
        text = " ".join((data or "").split())
        if not text:
            return
        if self.in_title:
            self.title_parts.append(text)
        elif not self.skip_depth:
            self.text_parts.append(text)


def _extract_title(html: str) -> str:
    parser = _TitleParser()
    parser.feed(html)
    return " ".join(parser.title_parts).strip()


def _strip_html_text(html: str) -> str:
    parser = _TitleParser()
    parser.feed(html)
    return "\n".join(parser.text_parts)


def _build_summary(text: str) -> str:
    compact = " ".join(text.split())
    return compact[:500] + ("..." if len(compact) > 500 else "")


def _format_link_raw_event_content(note: ExtractedNote) -> str:
    return (
        f"链接笔记：{note.title}\n"
        f"来源：{note.source_url}\n"
        f"摘要：{note.summary}"
    )


def _format_media_raw_event_content(artifact: MediaArtifact, note: ExtractedNote) -> str:
    lines = [
        f"{_media_memory_label(artifact.media_type)}：{note.title}",
        f"素材ID：{artifact.id}",
        f"摘要：{note.summary}",
    ]
    if note.text:
        lines.append("正文摘录：")
        lines.append(note.text[:1200])
    return "\n".join(lines)


def _media_memory_label(media_type: str | None) -> str:
    mapping = {
        "link": "链接笔记",
        "image": "图片 OCR 笔记",
        "spreadsheet": "表格笔记",
        "file": "文件笔记",
        "pdf": "PDF 笔记",
        "audio": "音频转写笔记",
        "video": "视频转写笔记",
    }
    return mapping.get(media_type or "", "媒体笔记")


def _note_to_dict(note: ExtractedNote) -> dict:
    return {
        "title": note.title,
        "summary": note.summary,
        "text": note.text[:MAX_EXTRACTED_TEXT_CHARS],
        "structured_data": note.structured_data,
        "source_url": note.source_url,
        "confidence": note.confidence,
        "warnings": note.warnings,
    }


def _extraction_status_for_note(note: ExtractedNote) -> str:
    if (note.text or "").strip():
        return "extracted"
    if note.confidence < 0.35:
        return "skipped"
    return "extracted" if (note.summary or "").strip() else "skipped"


def _resolve_artifact_path(storage_path: str | None) -> Path:
    if not storage_path:
        raise ValueError("storage_path_required")
    base = MEDIA_STORAGE_DIR.resolve()
    path = Path(storage_path)
    if not path.is_absolute():
        path = base / path
    resolved = path.resolve()
    if base != resolved and base not in resolved.parents:
        raise ValueError("storage_path_outside_media_dir")
    return resolved


def _validate_local_media_file(path: Path) -> None:
    if not path.exists() or not path.is_file():
        raise ValueError("media_file_not_found")
    size = path.stat().st_size
    if size > settings.MEDIA_MAX_FILE_SIZE_BYTES:
        raise ValueError("media_file_too_large")


def _write_extraction_outputs(artifact_id: str, note: ExtractedNote) -> tuple[Path, Path]:
    output_dir = MEDIA_STORAGE_DIR / "extracted"
    output_dir.mkdir(parents=True, exist_ok=True)
    text_path = output_dir / f"{artifact_id}.txt"
    json_path = output_dir / f"{artifact_id}.json"
    text_path.write_text(note.text[:MAX_EXTRACTED_TEXT_CHARS], encoding="utf-8")
    json_path.write_text(json.dumps(_note_to_dict(note), ensure_ascii=False, indent=2), encoding="utf-8")
    return text_path, json_path


def _to_storage_relative_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(MEDIA_STORAGE_DIR.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


async def _find_duplicate_artifact(db: AsyncSession, *, user_id: str, sha256: str) -> MediaArtifact | None:
    result = await db.execute(
        select(MediaArtifact)
        .where(
            MediaArtifact.user_id == user_id,
            MediaArtifact.sha256 == sha256,
            MediaArtifact.status != "failed",
        )
        .order_by(MediaArtifact.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _existing_extraction_result(
    db: AsyncSession,
    artifact: MediaArtifact,
) -> tuple[RawEvent, str | None] | None:
    metadata = artifact.artifact_metadata or {}
    event_id = metadata.get("extracted_event_id")
    memory_id = metadata.get("memory_id")
    if not event_id:
        return None
    event = await db.get(RawEvent, event_id)
    if event:
        return event, str(memory_id) if memory_id else None
    return None


def _copy_file_with_limit(fileobj, target_path: Path, max_bytes: int) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    try:
        with target_path.open("wb") as output:
            while True:
                chunk = fileobj.read(CHUNK_SIZE)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise ValueError("media_file_too_large")
                digest.update(chunk)
                output.write(chunk)
    except Exception:
        target_path.unlink(missing_ok=True)
        raise
    return size, digest.hexdigest()


def _fallback_title_from_url(url: str) -> str:
    parsed = urlparse(url)
    return parsed.netloc or url[:80]


def _extract_original_name(payload: dict) -> str | None:
    for key in ("filename", "file_name", "name", "title"):
        value = _find_nested_key(payload, key)
        if isinstance(value, str) and value:
            return value[:255]
    return None


def _extract_mime_type(payload: dict) -> str | None:
    value = _find_nested_key(payload, "mime_type") or _find_nested_key(payload, "content_type")
    return str(value)[:120] if value else None


def _extract_size(payload: dict) -> int | None:
    value = _find_nested_key(payload, "size") or _find_nested_key(payload, "file_size")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _extract_wecom_media_id(payload: dict) -> str | None:
    value = _find_nested_key(payload, "media_id") or _find_nested_key(payload, "fileid") or _find_nested_key(payload, "file_id")
    return str(value)[:255] if value else None


def _find_nested_key(payload, target: str):
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key == target:
                return value
            found = _find_nested_key(value, target)
            if found is not None:
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = _find_nested_key(item, target)
            if found is not None:
                return found
    return None
