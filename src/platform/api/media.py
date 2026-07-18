from __future__ import annotations

import base64
import binascii
import logging
from io import BytesIO

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.platform.models.media_artifact import MediaArtifact
from src.platform.services.media_ingestion import create_link_artifact, create_uploaded_media_artifact, extract_stored_artifact
from src.platform.tasks.media_extraction import trigger_media_extraction
from src.shared.db.database import get_db
from src.shared.security.dependencies import get_current_user_or_agent_owner


router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/upload")
async def upload_media_note(
    file: UploadFile = File(...),
    source_channel: str = Form("api"),
    media_type: str | None = Form(None),
    extract: bool = Form(True),
    sync: bool = Form(False),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user_or_agent_owner),
):
    try:
        event, artifact = await create_uploaded_media_artifact(
            db,
            user_id=user.id,
            fileobj=file.file,
            filename=file.filename or "upload.bin",
            source_channel=source_channel,
            mime_type=file.content_type,
            media_type=media_type,
        )
        response = await _finalize_upload_response(
            db,
            event=event,
            artifact=artifact,
            extract=extract,
            sync=sync,
        )
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        await file.close()
    return response


@router.post("/upload-base64")
async def upload_media_note_base64(
    request: dict,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user_or_agent_owner),
):
    filename = str(request.get("filename") or "upload.bin")
    content_base64 = request.get("content_base64")
    if not isinstance(content_base64, str) or not content_base64.strip():
        raise HTTPException(status_code=400, detail="content_base64 is required")
    try:
        content = base64.b64decode(content_base64, validate=True)
        if not content:
            raise ValueError("media_data_empty")
        event, artifact = await create_uploaded_media_artifact(
            db,
            user_id=user.id,
            fileobj=BytesIO(content),
            filename=filename,
            source_channel=str(request.get("source_channel") or "api"),
            mime_type=request.get("mime_type"),
            media_type=request.get("media_type"),
        )
        response = await _finalize_upload_response(
            db,
            event=event,
            artifact=artifact,
            extract=bool(request.get("extract", True)),
            sync=bool(request.get("sync", False)),
        )
    except (ValueError, binascii.Error) as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return response


@router.post("/link")
async def create_link_media_note(
    request: dict,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user_or_agent_owner),
):
    url = str(request.get("url") or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="url is required")
    try:
        event, artifact = await create_link_artifact(
            db,
            user_id=user.id,
            url=url,
            source_text=str(request.get("source_text") or url),
            source_raw_event_id=request.get("source_raw_event_id"),
            source_channel=str(request.get("source_channel") or "api"),
            message_id=request.get("message_id"),
        )
        event.event_metadata = {**(event.event_metadata or {}), "media_artifact_id": artifact.id}
        response = await _finalize_upload_response(
            db,
            event=event,
            artifact=artifact,
            extract=bool(request.get("extract", True)),
            sync=bool(request.get("sync", False)),
        )
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return response


async def _finalize_upload_response(
    db: AsyncSession,
    *,
    event,
    artifact: MediaArtifact,
    extract: bool,
    sync: bool,
) -> dict:
    duplicate = bool((artifact.artifact_metadata or {}).get("duplicate_upload_last_seen_at"))
    await db.commit()
    memory_id = None
    extracted_event_id = None
    queued_for_extraction = False
    if extract and sync and artifact.status != "extracted":
        extracted_event, extracted_memory_id = await extract_stored_artifact(db, artifact_id=artifact.id)
        extracted_event_id = extracted_event.id
        memory_id = extracted_memory_id
    elif extract and artifact.status in {"downloaded", "received"}:
        trigger_media_extraction(artifact.id)
        queued_for_extraction = True
    refreshed = await db.get(MediaArtifact, artifact.id)
    return {
        "success": True,
        "raw_event_id": event.id,
        "artifact": media_artifact_payload(refreshed),
        "extracted_event_id": extracted_event_id,
        "memory_id": memory_id,
        "queued_for_extraction": queued_for_extraction,
        "duplicate": duplicate,
    }


@router.get("/artifacts")
async def list_media_artifacts(
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user_or_agent_owner),
):
    capped_limit = max(1, min(limit, 200))
    result = await db.execute(
        select(MediaArtifact)
        .where(MediaArtifact.user_id == user.id)
        .order_by(MediaArtifact.created_at.desc())
        .limit(capped_limit)
    )
    return {"items": [media_artifact_payload(item) for item in result.scalars().all()]}


@router.get("/artifacts/{artifact_id}")
async def get_media_artifact_detail(
    artifact_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user_or_agent_owner),
):
    artifact = await db.get(MediaArtifact, artifact_id)
    if not artifact or artifact.user_id != user.id:
        raise HTTPException(status_code=404, detail="Media artifact not found")
    return media_artifact_detail_payload(artifact)


@router.post("/artifacts/{artifact_id}/extract")
async def extract_media_artifact_now(
    artifact_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user_or_agent_owner),
):
    artifact = await db.get(MediaArtifact, artifact_id)
    if not artifact or artifact.user_id != user.id:
        raise HTTPException(status_code=404, detail="Media artifact not found")
    try:
        event, memory_id = await extract_stored_artifact(db, artifact_id=artifact_id)
    except ValueError as exc:
        # ValueError 是受控的校验错误，消息面向用户
        raise HTTPException(status_code=400, detail=str(exc)[:300]) from exc
    except Exception as exc:
        # 安全：其它异常仅记录内部日志，不向调用方泄露细节（可能含路径、模块名等）
        logger.error("extract_stored_artifact failed for artifact %s: %s", artifact_id, exc)
        raise HTTPException(status_code=500, detail="media extraction failed") from exc
    refreshed = await db.get(MediaArtifact, artifact_id)
    return {
        "success": True,
        "event_id": event.id,
        "memory_id": memory_id,
        "artifact": media_artifact_payload(refreshed),
    }


def media_artifact_payload(artifact: MediaArtifact | None) -> dict | None:
    if artifact is None:
        return None
    metadata = dict(artifact.artifact_metadata or {})
    extracted_note = metadata.get("extracted_note") if isinstance(metadata.get("extracted_note"), dict) else {}
    return {
        "id": artifact.id,
        "raw_event_id": artifact.raw_event_id,
        "source_channel": artifact.source_channel,
        "message_id": artifact.message_id,
        "media_type": artifact.media_type,
        "original_name": artifact.original_name,
        "mime_type": artifact.mime_type,
        "size_bytes": artifact.size_bytes,
        "sha256": getattr(artifact, "sha256", None),
        "source_url": artifact.source_url,
        "status": artifact.status,
        "extractor_name": artifact.extractor_name,
        "extractor_version": artifact.extractor_version,
        "has_extracted_text": bool(artifact.extracted_text_path),
        "has_extracted_json": bool(artifact.extracted_json_path),
        "error_message": artifact.error_message,
        "note_title": extracted_note.get("title"),
        "note_summary": extracted_note.get("summary"),
        "warnings": extracted_note.get("warnings") or [],
        "created_at": artifact.created_at.isoformat() if artifact.created_at else None,
        "updated_at": artifact.updated_at.isoformat() if artifact.updated_at else None,
    }


def media_artifact_detail_payload(artifact: MediaArtifact) -> dict:
    payload = media_artifact_payload(artifact) or {}
    metadata = dict(artifact.artifact_metadata or {})
    extracted_note = metadata.get("extracted_note") if isinstance(metadata.get("extracted_note"), dict) else None
    payload["extracted_note"] = _safe_extracted_note(extracted_note)
    payload["extracted_event_id"] = metadata.get("extracted_event_id")
    payload["memory_id"] = metadata.get("memory_id")
    payload["duplicate_upload_last_seen_at"] = metadata.get("duplicate_upload_last_seen_at")
    return payload


def _safe_extracted_note(note: dict | None) -> dict | None:
    if not note:
        return None
    text = str(note.get("text") or "")
    return {
        "title": note.get("title"),
        "summary": note.get("summary"),
        "text": text[:20_000],
        "structured_data": note.get("structured_data") if isinstance(note.get("structured_data"), dict) else {},
        "source_url": note.get("source_url"),
        "confidence": note.get("confidence"),
        "warnings": note.get("warnings") if isinstance(note.get("warnings"), list) else [],
    }
