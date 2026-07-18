"""企业微信与通用媒体摄取（media ingestion）测试。

覆盖：
- URL 提取与私有地址拦截、payload 形状/脱敏、媒体笔记自动治理
- 媒体候选不可自动提交
- 链接 artifact 入队不抓取 URL
- trafilatura / yt_dlp 链接笔记元数据抽取
- 链接 artifact 后台抽取生成候选
- 企业微信图片消息回复与 media artifact 状态
- 本地媒体抽取器（text/csv/docx/image OCR/audio wav）
- 上传媒体 artifact 服务（MIME 校验、去重、超时失败）
- 上传/链接媒体 API 入队抽取
"""

from sqlalchemy import select


def test_media_ingestion_url_helpers_and_working_proposal():
    import asyncio
    from types import SimpleNamespace

    from src.memory.models.raw_event import SensitivityLevel
    from src.platform.models.media_artifact import MediaArtifact
    from src.platform.services.media_ingestion import (
        assert_public_http_url,
        build_media_memory_proposal,
        extract_urls,
        normalize_wecom_media_type,
        payload_shape,
        sanitize_wecom_payload,
    )

    assert extract_urls("参考 https://example.com/a?x=1，然后看 http://example.org。") == [
        "https://example.com/a?x=1",
        "http://example.org",
    ]
    assert normalize_wecom_media_type("voice") == "audio"
    assert payload_shape({"image": {"media_id": "x", "size": 1}}) == {"image": {"media_id": "str", "size": "int"}}
    assert sanitize_wecom_payload({"secret": "abc", "text": "x"})["secret"] == "***"

    async def private_url_rejected():
        try:
            await assert_public_http_url("http://127.0.0.1/private")
        except ValueError as exc:
            assert "private" in str(exc) or "reserved" in str(exc)
        else:
            raise AssertionError("private URL should be rejected")

    asyncio.run(private_url_rejected())

    event = SimpleNamespace(
        id="evt_media",
        user_id="solo-user",
        sensitivity=SensitivityLevel.NORMAL,
        event_metadata={"note_title": "Example"},
    )
    artifact = MediaArtifact(
        id="media_test",
        user_id="solo-user",
        raw_event_id="evt_media",
        source_channel="wecom",
        media_type="link",
        source_url="https://example.com",
        status="extracted",
        extractor_name="test",
    )
    proposal = build_media_memory_proposal(
        event=event,
        artifact=artifact,
        note={"title": "Example", "summary": "摘要", "text": "正文", "confidence": 0.8},
    )
    assert proposal["memory_type"] == "insight"
    assert "media_note" in proposal["entities"]
    assert proposal["confidence"] == 0.8
    assert proposal["importance"] == 0.58


def test_media_proposal_has_no_direct_commit_capability():
    from types import SimpleNamespace

    from src.memory.models.raw_event import SensitivityLevel
    from src.platform.models.media_artifact import MediaArtifact
    from src.platform.services.media_ingestion import build_media_memory_proposal

    event = SimpleNamespace(
        id="evt_media_high_confidence",
        user_id="solo-user",
        sensitivity=SensitivityLevel.NORMAL,
        event_metadata={"note_title": "高置信图片 OCR"},
    )
    artifact = MediaArtifact(
        id="media_high_confidence",
        user_id="solo-user",
        raw_event_id="evt_media_high_confidence",
        source_channel="wecom",
        media_type="image",
        original_name="screenshot.png",
        status="extracted",
        extractor_name="rapidocr",
    )
    proposal = build_media_memory_proposal(
        event=event,
        artifact=artifact,
        note={
            "title": "高置信图片 OCR",
            "summary": "识别到一段清晰截图文字。",
            "text": "这是 OCR 识别到的文本。",
            "confidence": 0.95,
        },
    )

    assert proposal["confidence"] == 0.85
    assert "proposed_action" not in proposal
    assert "status" not in proposal


def test_link_artifact_queue_does_not_fetch_url(monkeypatch):
    import asyncio

    import src.platform.services.media_ingestion as media_ingestion

    async def fail_if_fetched(url):
        raise AssertionError("link placeholder creation must not fetch URL")

    monkeypatch.setattr(media_ingestion, "extract_link_note", fail_if_fetched)

    async def run_flow():
        from sqlalchemy import select

        from src.memory.models.committed_memory import CommittedMemory
        from src.platform.models.media_artifact import MediaArtifact
        from src.shared.db.database import async_session, init_db

        await init_db()
        async with async_session() as db:
            artifacts = await media_ingestion.create_link_artifacts_from_text(
                db,
                user_id="solo-user",
                text="这篇文章后面要读：https://example.com/article",
                source_raw_event_id="evt_source_link_queue",
                source_channel="wecom",
                message_id="msg_link_queue",
            )
            await db.commit()
            assert len(artifacts) == 1
            artifact = await db.get(MediaArtifact, artifacts[0].id)
            assert artifact.media_type == "link"
            assert artifact.status == "received"
            assert artifact.source_url == "https://example.com/article"
            memories = await db.execute(select(CommittedMemory))
            assert [
                item for item in memories.scalars().all()
                if item.source_work_case_id
            ] == []

    asyncio.run(run_flow())


def test_extract_link_note_keeps_trafilatura_metadata(monkeypatch):
    import asyncio
    import sys
    from types import SimpleNamespace

    import src.platform.services.media_ingestion as media_ingestion

    class FakeResponse:
        headers = {"content-type": "text/html; charset=utf-8"}
        text = "<html><title>Fallback Title</title><body>fallback body</body></html>"

        def raise_for_status(self):
            return None

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url):
            return FakeResponse()

    def fake_bare_extraction(*args, **kwargs):
        return {
            "title": "Trafilatura Title",
            "text": "这是通过 trafilatura 抽取出来的正文。" * 20,
            "author": "作者A",
            "date": "2026-07-05",
            "sitename": "示例站点",
            "description": "示例描述",
        }

    async def fake_assert_public_http_url(url):
        return None

    monkeypatch.setattr(media_ingestion, "assert_public_http_url", fake_assert_public_http_url)
    monkeypatch.setattr(media_ingestion.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setitem(sys.modules, "trafilatura", SimpleNamespace(bare_extraction=fake_bare_extraction))

    note = asyncio.run(media_ingestion.extract_link_note("https://example.com/article"))

    assert note.title == "Trafilatura Title"
    assert "trafilatura 抽取" in note.text
    assert note.structured_data["author"] == "作者A"
    assert note.structured_data["published_at"] == "2026-07-05"
    assert note.structured_data["site_name"] == "示例站点"
    assert note.structured_data["description"] == "示例描述"
    assert note.structured_data["extractor_name"] == "trafilatura"


def test_extract_link_note_can_merge_ytdlp_metadata_without_video_download(monkeypatch):
    import asyncio
    import sys
    from types import SimpleNamespace

    import src.platform.services.media_ingestion as media_ingestion
    from src.shared.config import settings

    calls = []

    class FakeResponse:
        headers = {"content-type": "text/html"}
        text = "<html><title>Video Page</title><body>普通页面正文足够长。</body></html>"

        def raise_for_status(self):
            return None

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url):
            return FakeResponse()

    class FakeYoutubeDL:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def extract_info(self, url, download=False):
            calls.append({"url": url, "download": download, "options": self.options})
            return {
                "title": "公开视频标题",
                "uploader": "频道A",
                "duration": 123,
                "webpage_url": url,
                "description": "公开视频描述",
                "subtitles": {"zh-Hans": []},
                "automatic_captions": {"en": []},
            }

    async def fake_assert_public_http_url(url):
        return None

    monkeypatch.setattr(settings, "MEDIA_ENABLE_YTDLP", True)
    monkeypatch.setattr(media_ingestion, "assert_public_http_url", fake_assert_public_http_url)
    monkeypatch.setattr(media_ingestion.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setitem(sys.modules, "trafilatura", SimpleNamespace(extract=lambda *args, **kwargs: "普通页面正文足够长。" * 30))
    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=FakeYoutubeDL))

    note = asyncio.run(media_ingestion.extract_link_note("https://video.example/watch/1"))

    assert calls == [{
        "url": "https://video.example/watch/1",
        "download": False,
        "options": calls[0]["options"],
    }]
    assert calls[0]["options"]["skip_download"] is True
    metadata = note.structured_data["video_metadata"]
    assert metadata["extractor_name"] == "yt_dlp"
    assert metadata["title"] == "公开视频标题"
    assert metadata["subtitle_languages"] == ["zh-Hans"]
    assert metadata["automatic_caption_languages"] == ["en"]
    assert "公开视频元信息" in note.text
    assert "频道A" in note.text


def test_link_artifact_background_extraction_creates_formal_memory(monkeypatch):
    import asyncio

    import src.platform.services.media_ingestion as media_ingestion

    async def fake_extract(url):
        return media_ingestion.ExtractedNote(
            title="Example Article",
            summary="这是一篇用于后台链接解析测试的文章摘要。",
            text="正文内容足够交给工作 Agent 治理。",
            structured_data={"title": "Example Article"},
            source_url=url,
            confidence=0.76,
            warnings=[],
        )

    monkeypatch.setattr(media_ingestion, "extract_link_note", fake_extract)

    async def run_flow():
        from src.memory.models.committed_memory import CommittedMemory
        from src.platform.models.media_artifact import MediaArtifact
        from src.shared.db.database import async_session, init_db

        await init_db()
        async with async_session() as db:
            artifacts = await media_ingestion.create_link_artifacts_from_text(
                db,
                user_id="solo-user",
                text="资料：https://example.com/background",
                source_raw_event_id="evt_source_link_extract",
                source_channel="wecom",
                message_id="msg_link_extract",
            )
            await db.commit()
            event, memory_id = await media_ingestion.extract_stored_artifact(db, artifact_id=artifacts[0].id)
            memory = await db.get(CommittedMemory, memory_id)
            loaded = await db.get(MediaArtifact, artifacts[0].id)
            assert loaded.status == "extracted"
            assert loaded.artifact_metadata["extracted_event_id"] == event.id
            assert loaded.artifact_metadata["memory_id"] == memory_id
            assert "链接笔记" in memory.title
            assert memory.status.value == "active"
            second_event, second_memory_id = await media_ingestion.extract_stored_artifact(
                db, artifact_id=artifacts[0].id
            )
            assert second_event.id == event.id
            assert second_memory_id == memory_id

    asyncio.run(run_flow())


def test_handle_wecom_image_replies_with_media_artifact_status(monkeypatch):
    import asyncio

    from src.platform.channels.wecom import WeComBotMessage
    import src.platform.channels.wecom_handlers as wecom_handlers

    queued: list[str] = []
    monkeypatch.setattr(wecom_handlers, "_trigger_media_extraction", lambda artifact_id: queued.append(artifact_id))

    async def run_flow():
        from sqlalchemy import select

        from src.platform.models.media_artifact import MediaArtifact
        from src.shared.db.database import async_session, init_db

        await init_db()
        msg = WeComBotMessage({
            "msgtype": "image",
            "from_userid": "zhangsan",
            "chatid": "chat_x",
            "chattype": "single",
            "aibotid": "bot_x",
            "msgid": "img_no_url",
            "image": {"media_id": "secret_media_id", "size": 123},
        })
        reply = await wecom_handlers.handle_wecom_message(msg)
        assert "图片我收到了" in reply
        assert "先帮你归档成笔记素材" in reply
        assert "当前状态：已接收，但暂时没拿到原文件，先保存元数据" in reply
        assert "素材ID：media_" in reply
        assert queued == []

        async with async_session() as db:
            result = await db.execute(select(MediaArtifact).where(MediaArtifact.message_id == "img_no_url"))
            artifact = result.scalar_one()
            assert artifact.media_type == "image"
            assert artifact.status == "received"
            assert artifact.wecom_media_id == "secret_media_id"
            assert artifact.artifact_metadata["download_skipped"] == "no_url_in_payload"

    asyncio.run(run_flow())


def test_local_media_extractors_for_text_and_csv(tmp_path, monkeypatch):
    import asyncio

    from src.platform.models.media_artifact import MediaArtifact
    from src.platform.services.media_extractors import select_extractor
    from src.platform.services.media_extractors.base import LocalExtractionInput
    import src.platform.services.media_ingestion as media_ingestion

    monkeypatch.setattr(media_ingestion, "MEDIA_STORAGE_DIR", tmp_path)
    text_file = tmp_path / "note.txt"
    text_file.write_text("今天确认企业微信媒体笔记解析方案。", encoding="utf-8")
    text_artifact = MediaArtifact(
        id="media_text",
        user_id="solo-user",
        raw_event_id="evt_text",
        source_channel="wecom",
        media_type="file",
        original_name="note.txt",
        storage_path="note.txt",
        mime_type="text/plain",
        status="downloaded",
    )
    text_extractor = select_extractor(text_artifact, text_file)
    text_note = asyncio.run(text_extractor.extract(LocalExtractionInput(artifact=text_artifact, path=text_file)))
    assert "企业微信媒体笔记" in text_note.text
    assert text_note.confidence >= 0.8

    csv_file = tmp_path / "items.csv"
    csv_file.write_text("name,amount\n苹果,3\n香蕉,5\n", encoding="utf-8")
    csv_artifact = MediaArtifact(
        id="media_csv",
        user_id="solo-user",
        raw_event_id="evt_csv",
        source_channel="wecom",
        media_type="spreadsheet",
        original_name="items.csv",
        storage_path="items.csv",
        mime_type="text/csv",
        status="downloaded",
    )
    csv_extractor = select_extractor(csv_artifact, csv_file)
    csv_note = asyncio.run(csv_extractor.extract(LocalExtractionInput(artifact=csv_artifact, path=csv_file)))
    assert csv_note.structured_data["headers"] == ["name", "amount"]
    assert ["苹果", "3"] in csv_note.structured_data["preview_rows"]

    outside = tmp_path.parent / "outside.txt"
    outside.write_text("nope", encoding="utf-8")
    try:
        media_ingestion._resolve_artifact_path(str(outside))
    except ValueError as exc:
        assert "outside" in str(exc)
    else:
        raise AssertionError("outside storage path should be rejected")

    class _InMemorySession:
        def add(self, _row):
            pass

        async def flush(self):
            pass

    event = asyncio.run(
        media_ingestion.create_raw_event_for_extracted_note(
            _InMemorySession(), artifact=csv_artifact, note=csv_note
        )
    )
    proposal = media_ingestion.build_media_memory_proposal(event=event, artifact=csv_artifact, note={
        "title": csv_note.title,
        "summary": csv_note.summary,
        "text": csv_note.text,
        "confidence": csv_note.confidence,
    })
    assert event.event_metadata["media_type"] == "spreadsheet"
    assert "表格笔记" in proposal["title"]
    assert "spreadsheet" in proposal["entities"]


def test_builtin_docx_extractor_without_markitdown(tmp_path):
    import asyncio
    import zipfile

    from src.platform.models.media_artifact import MediaArtifact
    from src.platform.services.media_extractors.base import LocalExtractionInput
    from src.platform.services.media_extractors.document_converter import DocumentExtractor

    docx_file = tmp_path / "note.docx"
    document_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>这是一个 Word 笔记。</w:t></w:r></w:p>
    <w:p><w:r><w:t>它应该被内置解析器提取。</w:t></w:r></w:p>
  </w:body>
</w:document>
"""
    with zipfile.ZipFile(docx_file, "w") as package:
        package.writestr("word/document.xml", document_xml)
    artifact = MediaArtifact(
        id="media_docx",
        user_id="solo-user",
        raw_event_id="evt_docx",
        source_channel="api",
        media_type="document",
        original_name="note.docx",
        storage_path="note.docx",
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        status="downloaded",
    )
    note = asyncio.run(DocumentExtractor().extract(LocalExtractionInput(artifact=artifact, path=docx_file)))
    assert "Word 笔记" in note.text
    assert note.structured_data["converter"] == "builtin_ooxml"
    assert note.confidence >= 0.6


def test_image_ocr_empty_result_is_low_confidence_and_skipped(tmp_path, monkeypatch):
    import asyncio
    from datetime import datetime, timezone

    from PIL import Image

    from src.memory.models.raw_event import ProcessingStatus, RawEvent, SensitivityLevel, SourceType, VisibilityScope
    from src.platform.models.media_artifact import MediaArtifact
    from src.platform.services.media_extractors.base import LocalExtractionInput
    from src.platform.services.media_extractors.image_ocr import ImageOcrExtractor
    import src.platform.services.media_ingestion as media_ingestion
    from src.platform.services.media_ingestion import extract_stored_artifact

    monkeypatch.setattr(media_ingestion, "MEDIA_STORAGE_DIR", tmp_path)
    image_file = tmp_path / "blank.png"
    Image.new("RGB", (8, 6), color="white").save(image_file)
    artifact = MediaArtifact(
        id="media_image_blank",
        user_id="solo-user",
        raw_event_id="evt_image_blank_parent",
        source_channel="api",
        media_type="image",
        original_name="blank.png",
        storage_path="blank.png",
        mime_type="image/png",
        status="downloaded",
    )
    note = asyncio.run(ImageOcrExtractor().extract(LocalExtractionInput(artifact=artifact, path=image_file)))
    assert note.structured_data["width"] == 8
    assert note.structured_data["height"] == 6
    assert note.confidence <= 0.35
    assert note.warnings

    async def run_extract():
        from src.memory.models.memory_source import MemorySource
        from src.shared.db.database import async_session, init_db

        await init_db()
        async with async_session() as db:
            parent = RawEvent(
                id="evt_image_blank_parent",
                source_type=SourceType.FILE_IMPORT,
                source_id="api",
                user_id="solo-user",
                occurred_at=datetime.now(timezone.utc),
                content="image parent",
                content_hash="image-parent",
                sensitivity=SensitivityLevel.NORMAL,
                visibility_scope=VisibilityScope.PERSONAL,
                processing_status=ProcessingStatus.COMPLETED,
            )
            db.add(parent)
            db.add(artifact)
            await db.commit()
            extracted_event, memory_id = await extract_stored_artifact(db, artifact_id=artifact.id)
            loaded = await db.get(MediaArtifact, artifact.id)
            assert loaded.status == "skipped"
            assert memory_id is None
            assert (
                await db.execute(
                    select(MemorySource).where(MemorySource.raw_event_id == extracted_event.id)
                )
            ).scalars().all() == []

    asyncio.run(run_extract())


def test_audio_wav_metadata_fallback_is_low_confidence_and_skipped(tmp_path, monkeypatch):
    import asyncio
    from datetime import datetime, timezone
    import wave

    from src.memory.models.raw_event import ProcessingStatus, RawEvent, SensitivityLevel, SourceType, VisibilityScope
    from src.platform.models.media_artifact import MediaArtifact
    from src.platform.services.media_extractors.audio_video_transcriber import AudioVideoTranscriber
    from src.platform.services.media_extractors.base import LocalExtractionInput
    import src.platform.services.media_ingestion as media_ingestion
    from src.platform.services.media_ingestion import extract_stored_artifact

    monkeypatch.setattr(media_ingestion, "MEDIA_STORAGE_DIR", tmp_path)
    wav_file = tmp_path / "silent.wav"
    with wave.open(str(wav_file), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"\x00\x00" * 800)
    artifact = MediaArtifact(
        id="media_audio_silent",
        user_id="solo-user",
        raw_event_id="evt_audio_silent_parent",
        source_channel="api",
        media_type="audio",
        original_name="silent.wav",
        storage_path="silent.wav",
        mime_type="audio/wav",
        status="downloaded",
    )
    note = asyncio.run(AudioVideoTranscriber().extract(LocalExtractionInput(artifact=artifact, path=wav_file)))
    assert note.structured_data["duration_seconds"] == 0.1
    assert note.structured_data["sample_rate"] == 8000
    assert note.confidence == 0.25
    assert note.warnings

    async def run_extract():
        from src.memory.models.memory_source import MemorySource
        from src.shared.db.database import async_session, init_db

        await init_db()
        async with async_session() as db:
            parent = RawEvent(
                id="evt_audio_silent_parent",
                source_type=SourceType.FILE_IMPORT,
                source_id="api",
                user_id="solo-user",
                occurred_at=datetime.now(timezone.utc),
                content="audio parent",
                content_hash="audio-parent",
                sensitivity=SensitivityLevel.NORMAL,
                visibility_scope=VisibilityScope.PERSONAL,
                processing_status=ProcessingStatus.COMPLETED,
            )
            db.add(parent)
            db.add(artifact)
            await db.commit()
            extracted_event, memory_id = await extract_stored_artifact(db, artifact_id=artifact.id)
            loaded = await db.get(MediaArtifact, artifact.id)
            assert loaded.status == "skipped"
            assert memory_id is None
            assert (
                await db.execute(
                    select(MemorySource).where(MemorySource.raw_event_id == extracted_event.id)
                )
            ).scalars().all() == []

    asyncio.run(run_extract())


def test_uploaded_media_artifact_service_limits_and_extracts(tmp_path, monkeypatch):
    import asyncio
    from datetime import datetime, timezone
    from io import BytesIO

    from src.platform.services.media_ingestion import (
        assert_mime_allowed,
        assert_filename_matches_mime,
        create_uploaded_media_artifact,
        extract_stored_artifact,
        infer_media_type,
        normalize_mime_type,
    )
    import src.platform.services.media_ingestion as media_ingestion

    monkeypatch.setattr(media_ingestion, "MEDIA_STORAGE_DIR", tmp_path)

    async def run_upload_flow():
        from sqlalchemy import select

        from src.memory.models.committed_memory import CommittedMemory
        from src.memory.models.memory_source import MemorySource
        from src.shared.db.database import async_session, init_db

        await init_db()
        async with async_session() as db:
            event, artifact = await create_uploaded_media_artifact(
                db,
                user_id="solo-user",
                fileobj=BytesIO("name,amount\n苹果,3\n".encode("utf-8")),
                filename="../明细.csv",
                source_channel="api",
                mime_type="text/csv; charset=utf-8",
            )
            await db.commit()
            assert event.occurred_at <= datetime.now(timezone.utc)
            assert artifact.original_name == "明细.csv"
            assert artifact.media_type == "spreadsheet"
            assert artifact.mime_type == "text/csv"
            assert artifact.status == "downloaded"
            assert artifact.sha256
            extracted_event, memory_id = await extract_stored_artifact(db, artifact_id=artifact.id)
            memory = await db.get(CommittedMemory, memory_id)
            assert extracted_event.event_metadata["media_artifact_id"] == artifact.id
            assert "表格笔记" in memory.title
            loaded = await db.get(type(artifact), artifact.id)
            assert loaded.status == "extracted"
            assert loaded.extracted_json_path
            assert loaded.artifact_metadata["extracted_event_id"] == extracted_event.id
            assert loaded.artifact_metadata["memory_id"] == memory_id
            second_event, second_memory_id = await extract_stored_artifact(db, artifact_id=artifact.id)
            assert second_event.id == extracted_event.id
            assert second_memory_id == memory_id
            sources = await db.execute(
                select(MemorySource).where(MemorySource.raw_event_id == extracted_event.id)
            )
            assert len(sources.scalars().all()) == 1
            duplicate_event, duplicate_artifact = await create_uploaded_media_artifact(
                db,
                user_id="solo-user",
                fileobj=BytesIO("name,amount\n苹果,3\n".encode("utf-8")),
                filename="重复.csv",
                source_channel="api",
                mime_type="text/csv",
            )
            await db.commit()
            assert duplicate_artifact.id == artifact.id
            assert duplicate_event.id == event.id
            assert duplicate_artifact.artifact_metadata["duplicate_upload_last_seen_at"]

    asyncio.run(run_upload_flow())

    try:
        assert_mime_allowed("application/x-msdownload")
    except ValueError as exc:
        assert "unsupported_mime_type" in str(exc)
    else:
        raise AssertionError("unsupported MIME should be rejected")
    assert_mime_allowed("application/vnd.openxmlformats-officedocument.presentationml.presentation")
    assert_mime_allowed("text/html")
    assert_mime_allowed("text/csv; charset=utf-8")
    assert_filename_matches_mime("safe.csv", "text/csv")
    try:
        assert_filename_matches_mime("fake.exe", "text/plain")
    except ValueError as exc:
        assert "mime_extension_mismatch" in str(exc)
    else:
        raise AssertionError("dangerous mismatched extension should be rejected")
    assert normalize_mime_type("Text/HTML; Charset=UTF-8") == "text/html"
    assert infer_media_type(
        filename="slides.unknown",
        mime_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ) == "document"
    assert infer_media_type(filename="page.bin", mime_type="text/html; charset=utf-8") == "document"


def test_upload_rejects_mime_extension_mismatch_without_writing_file(tmp_path, monkeypatch):
    import asyncio
    from io import BytesIO

    from src.platform.services.media_ingestion import create_uploaded_media_artifact
    import src.platform.services.media_ingestion as media_ingestion

    monkeypatch.setattr(media_ingestion, "MEDIA_STORAGE_DIR", tmp_path)

    async def run_upload():
        from src.shared.db.database import async_session, init_db

        await init_db()
        async with async_session() as db:
            try:
                await create_uploaded_media_artifact(
                    db,
                    user_id="solo-user",
                    fileobj=BytesIO(b"pretend text"),
                    filename="fake.exe",
                    source_channel="api",
                    mime_type="text/plain",
                )
            except ValueError as exc:
                assert "mime_extension_mismatch" in str(exc)
            else:
                raise AssertionError("mismatched extension should be rejected")
            assert list(tmp_path.rglob("*")) == []

    asyncio.run(run_upload())


def test_media_extraction_timeout_marks_artifact_failed(tmp_path, monkeypatch):
    import asyncio
    from datetime import datetime, timezone

    from src.memory.models.raw_event import ProcessingStatus, RawEvent, SensitivityLevel, SourceType, VisibilityScope
    from src.platform.models.media_artifact import MediaArtifact
    import src.platform.services.media_ingestion as media_ingestion

    class SlowExtractor:
        name = "slow_extractor"
        version = "1"

        async def extract(self, payload):
            await asyncio.sleep(0.05)
            raise AssertionError("timeout should cancel before this point")

    monkeypatch.setattr(media_ingestion, "MEDIA_STORAGE_DIR", tmp_path)
    monkeypatch.setattr(media_ingestion.settings, "MEDIA_EXTRACTION_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(media_ingestion, "select_extractor", lambda artifact, path: SlowExtractor())
    source_file = tmp_path / "slow.txt"
    source_file.write_text("slow extraction", encoding="utf-8")

    async def run_extract():
        from src.shared.db.database import async_session, init_db

        await init_db()
        async with async_session() as db:
            db.add(RawEvent(
                id="evt_slow_media_parent",
                source_type=SourceType.FILE_IMPORT,
                source_id="api",
                user_id="solo-user",
                occurred_at=datetime.now(timezone.utc),
                content="slow parent",
                content_hash="slow-parent",
                sensitivity=SensitivityLevel.NORMAL,
                visibility_scope=VisibilityScope.PERSONAL,
                processing_status=ProcessingStatus.COMPLETED,
            ))
            artifact = MediaArtifact(
                id="media_slow_timeout",
                user_id="solo-user",
                raw_event_id="evt_slow_media_parent",
                source_channel="api",
                media_type="file",
                original_name="slow.txt",
                storage_path="slow.txt",
                mime_type="text/plain",
                status="downloaded",
            )
            db.add(artifact)
            await db.commit()
            try:
                await media_ingestion.extract_stored_artifact(db, artifact_id=artifact.id)
            except RuntimeError as exc:
                assert str(exc) == "media_extraction_timeout"
            else:
                raise AssertionError("slow extractor should time out")
            loaded = await db.get(MediaArtifact, artifact.id)
            assert loaded.status == "failed"
            assert loaded.error_message == "media_extraction_timeout"
            assert loaded.artifact_metadata["extraction_error"] == "media_extraction_timeout"

    asyncio.run(run_extract())


def test_upload_media_api_queues_extraction_by_default(tmp_path, monkeypatch):
    import asyncio
    from io import BytesIO
    from types import SimpleNamespace

    from fastapi import UploadFile
    from starlette.datastructures import Headers

    import src.platform.api.media as media_api
    import src.platform.services.media_ingestion as media_ingestion

    queued: list[str] = []
    monkeypatch.setattr(media_ingestion, "MEDIA_STORAGE_DIR", tmp_path)
    monkeypatch.setattr(media_api, "trigger_media_extraction", lambda artifact_id: queued.append(artifact_id))

    async def run_upload():
        from src.shared.db.database import async_session, init_db

        await init_db()
        async with async_session() as db:
            upload = UploadFile(
                file=BytesIO(b"name,amount\napple,3\n"),
                filename="api.csv",
                headers=Headers({"content-type": "text/csv"}),
            )
            response = await media_api.upload_media_note(
                file=upload,
                source_channel="api",
                media_type=None,
                extract=True,
                sync=False,
                db=db,
                user=SimpleNamespace(id="solo-user"),
            )
            assert response["memory_id"] is None
            assert response["queued_for_extraction"] is True
            assert response["artifact"]["status"] == "downloaded"
            assert queued == [response["artifact"]["id"]]

    asyncio.run(run_upload())


def test_upload_media_base64_api_queues_and_validates(tmp_path, monkeypatch):
    import asyncio
    import base64
    from types import SimpleNamespace

    from fastapi import HTTPException

    import src.platform.api.media as media_api
    import src.platform.services.media_ingestion as media_ingestion

    queued: list[str] = []
    monkeypatch.setattr(media_ingestion, "MEDIA_STORAGE_DIR", tmp_path)
    monkeypatch.setattr(media_api, "trigger_media_extraction", lambda artifact_id: queued.append(artifact_id))

    async def run_upload():
        from src.shared.db.database import async_session, init_db

        await init_db()
        async with async_session() as db:
            response = await media_api.upload_media_note_base64(
                {
                    "filename": "agent.csv",
                    "mime_type": "text/csv",
                    "source_channel": "mcp",
                    "content_base64": base64.b64encode(b"name,amount\nagent,7\n").decode("ascii"),
                },
                db=db,
                user=SimpleNamespace(id="solo-user"),
            )
            assert response["artifact"]["source_channel"] == "mcp"
            assert response["artifact"]["media_type"] == "spreadsheet"
            assert response["queued_for_extraction"] is True
            assert queued == [response["artifact"]["id"]]

            try:
                await media_api.upload_media_note_base64(
                    {"filename": "bad.csv", "content_base64": "not@@base64"},
                    db=db,
                    user=SimpleNamespace(id="solo-user"),
                )
            except HTTPException as exc:
                assert exc.status_code == 400
            else:
                raise AssertionError("invalid base64 should be rejected")

    asyncio.run(run_upload())


def test_create_link_media_api_queues_extraction(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    import src.platform.api.media as media_api
    import src.platform.services.media_ingestion as media_ingestion

    queued: list[str] = []

    async def fail_if_fetched(url):
        raise AssertionError("link API must queue extraction instead of fetching synchronously")

    monkeypatch.setattr(media_ingestion, "extract_link_note", fail_if_fetched)
    monkeypatch.setattr(media_api, "trigger_media_extraction", lambda artifact_id: queued.append(artifact_id))

    async def run_create_link():
        from src.shared.db.database import async_session, init_db

        await init_db()
        async with async_session() as db:
            response = await media_api.create_link_media_note(
                {
                    "url": "https://example.com/mcp-link",
                    "source_text": "Agent 发现一个链接素材",
                    "source_channel": "mcp",
                },
                db=db,
                user=SimpleNamespace(id="solo-user"),
            )
            assert response["memory_id"] is None
            assert response["queued_for_extraction"] is True
            assert response["artifact"]["media_type"] == "link"
            assert response["artifact"]["status"] == "received"
            assert response["artifact"]["source_url"] == "https://example.com/mcp-link"
            assert queued == [response["artifact"]["id"]]

    asyncio.run(run_create_link())
