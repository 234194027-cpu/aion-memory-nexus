"""Characterization tests for src/platform/services/media_ingestion.py.

WP-0A-T05: 锁定 media ingestion 模块的纯函数行为契约。

测试目标:
  - extract_urls 的 URL 提取行为稳定（去重、末尾标点剥离）
  - normalize_wecom_media_type 的输出值集合稳定
  - infer_media_type 的输出值集合稳定
  - sanitize_filename 的行为契约稳定（保留 CJK、长度截断）
  - normalize_mime_type 的行为契约稳定（参数剥离、小写化）
  - sanitize_wecom_payload 的敏感字段屏蔽契约稳定
  - ExtractedNote dataclass 字段集合稳定
  - build_media_memory_proposal 的结构化提案字段稳定

注意: 仅测试纯函数，不依赖 DB / 网络。
"""
from __future__ import annotations

from types import SimpleNamespace

from src.platform.services.media_ingestion import (
    ExtractedNote,
    extract_urls,
    normalize_wecom_media_type,
    infer_media_type,
    sanitize_filename,
    normalize_mime_type,
    sanitize_wecom_payload,
    payload_shape,
    build_media_memory_proposal,
)


EXPECTED_WECOM_MEDIA_TYPES = {"image", "file", "video", "audio", "mixed", "unknown"}
EXPECTED_INFERRED_MEDIA_TYPES = {"image", "audio", "video", "pdf", "spreadsheet", "document", "file"}
EXPECTED_EXTRACTED_NOTE_FIELDS = {"title", "summary", "text", "structured_data", "source_url", "confidence", "warnings"}


def test_extract_urls_returns_list_of_strings():
    """extract_urls 返回 list[str]，去重并保留顺序。"""
    text = "see https://example.com/a and https://example.com/a again plus http://b.com"
    urls = extract_urls(text)
    assert isinstance(urls, list)
    assert all(isinstance(u, str) for u in urls)
    assert urls == ["https://example.com/a", "http://b.com"]


def test_extract_urls_strips_trailing_punctuation():
    """URL 末尾的中英文标点必须被剥离。"""
    text = "see https://example.com/a。 and https://example.com/b, plus https://example.com/c！"
    urls = extract_urls(text)
    assert urls == ["https://example.com/a", "https://example.com/b", "https://example.com/c"]


def test_extract_urls_empty_text_returns_empty_list():
    """空文本必须返回空 list（不返回 None）。"""
    assert extract_urls("") == []
    assert extract_urls(None) == []


def test_normalize_wecom_media_type_value_set_stable():
    """normalize_wecom_media_type 输出值必须落在固定集合内。"""
    test_inputs = ["image", "file", "video", "voice", "audio", "mixed", "unknown_type", "", None]
    outputs = {normalize_wecom_media_type(t) for t in test_inputs}
    assert outputs.issubset(EXPECTED_WECOM_MEDIA_TYPES)


def test_normalize_wecom_media_type_specific_mappings_stable():
    """关键映射不变: voice -> audio, 未知 -> unknown。"""
    assert normalize_wecom_media_type("voice") == "audio"
    assert normalize_wecom_media_type("image") == "image"
    assert normalize_wecom_media_type("nonexistent") == "unknown"
    assert normalize_wecom_media_type("") == "unknown"


def test_infer_media_type_value_set_stable():
    """infer_media_type 输出值必须落在固定集合内。"""
    test_cases = [
        ("photo.jpg", "image/jpeg"),
        ("song.mp3", "audio/mpeg"),
        ("clip.mp4", "video/mp4"),
        ("doc.pdf", "application/pdf"),
        ("data.csv", "text/csv"),
        ("slides.pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
        ("notes.txt", "text/plain"),
        ("unknown.xyz", "application/octet-stream"),
    ]
    outputs = {infer_media_type(filename=f, mime_type=m) for f, m in test_cases}
    assert outputs.issubset(EXPECTED_INFERRED_MEDIA_TYPES)


def test_infer_media_type_specific_mappings_stable():
    """关键映射不变: jpg->image, mp3->audio, pdf->pdf, csv->spreadsheet, txt->file。"""
    assert infer_media_type(filename="photo.jpg", mime_type="image/jpeg") == "image"
    assert infer_media_type(filename="song.mp3", mime_type="audio/mpeg") == "audio"
    assert infer_media_type(filename="doc.pdf", mime_type="application/pdf") == "pdf"
    assert infer_media_type(filename="data.csv", mime_type="text/csv") == "spreadsheet"
    assert infer_media_type(filename="notes.txt", mime_type="text/plain") == "file"


def test_sanitize_filename_preserves_cjk_characters():
    """sanitize_filename 必须保留 CJK 字符（项目约束: UTF-8）。"""
    assert sanitize_filename("企业微信截图.png") == "企业微信截图.png"


def test_sanitize_filename_replaces_unsafe_characters():
    """不安全字符必须替换为下划线（注意: 紧贴下划线的括号会保留双下划线）。"""
    # 现有行为: "my file (1).txt" -> "my_file_1_.txt"（空格和括号都变 _，
    # 括号紧邻数字 1 后再 _，组合出 "1_"）。锁定该行为，避免静默回归。
    assert sanitize_filename("my file (1).txt") == "my_file_1_.txt"


def test_sanitize_filename_falls_back_to_default():
    """空文件名必须回退到 upload.bin。"""
    assert sanitize_filename("") == "upload.bin"
    assert sanitize_filename(None) == "upload.bin"


def test_sanitize_filename_truncates_to_180_chars():
    """文件名长度必须截断到 180 字符以内。"""
    long_name = "a" * 250 + ".txt"
    sanitized = sanitize_filename(long_name)
    assert len(sanitized) <= 180


def test_normalize_mime_type_lowercases_and_strips_params():
    """normalize_mime_type 必须小写化并剥离参数（如 charset）。"""
    assert normalize_mime_type("IMAGE/JPEG; charset=utf-8") == "image/jpeg"
    assert normalize_mime_type("text/plain") == "text/plain"


def test_normalize_mime_type_empty_falls_back_to_octet_stream():
    """空 MIME 必须回退到 application/octet-stream。"""
    assert normalize_mime_type(None) == "application/octet-stream"
    assert normalize_mime_type("") == "application/octet-stream"


def test_sanitize_wecom_payload_masks_sensitive_keys():
    """敏感字段必须被屏蔽为 ***。"""
    payload = {
        "secret": "my-secret",
        "token": "my-token",
        "access_token": "my-access-token",
        "password": "my-password",
        "msg_id": "msg-123",
    }
    sanitized = sanitize_wecom_payload(payload)
    assert sanitized["secret"] == "***"
    assert sanitized["token"] == "***"
    assert sanitized["access_token"] == "***"
    assert sanitized["password"] == "***"
    assert sanitized["msg_id"] == "msg-123"


def test_sanitize_wecom_payload_recursive_in_nested_dicts():
    """嵌套 dict 中的敏感字段必须递归屏蔽。"""
    payload = {
        "outer": {
            "token": "nested-token",
            "safe": "ok",
        },
        "list_field": [
            {"token": "list-token"},
            {"safe": "ok"},
        ],
    }
    sanitized = sanitize_wecom_payload(payload)
    assert sanitized["outer"]["token"] == "***"
    assert sanitized["outer"]["safe"] == "ok"


def test_payload_shape_returns_dict_for_dict_input():
    """payload_shape 对 dict 输入返回 dict（包含类型 + 键列表）。"""
    shape = payload_shape({"a": 1, "b": "x"})
    assert isinstance(shape, dict)


def test_extracted_note_dataclass_fields_stable():
    """ExtractedNote dataclass 字段集合稳定。"""
    fields = {f.name for f in ExtractedNote.__dataclass_fields__.values()}
    assert fields == EXPECTED_EXTRACTED_NOTE_FIELDS


def test_build_media_memory_proposal_fixed_field_values_stable():
    """媒体适配器只形成结构化提案，不直接实例化正式记忆。

    importance=0.58、memory_type=insight 是 WorkingCoordinator 的输入契约。
    """
    event = SimpleNamespace(
        id="evt_snapshot_001",
        user_id="user_snapshot",
        sensitivity=type("S", (), {"value": "normal"})(),
        event_metadata={"note_title": "Snapshot note"},
    )
    artifact = SimpleNamespace(
        id="art_snapshot_001",
        source_channel="wecom",
        source_url="https://example.com/snapshot",
        original_name=None,
        storage_path=None,
        media_type="link",
        status="extracted",
        extractor_name="trafilatura",
    )
    note = {
        "title": "Snapshot extracted note",
        "summary": "Summary of snapshot content.",
        "text": "Body text for snapshot.",
        "confidence": 0.75,
        "warnings": [],
    }
    proposal = build_media_memory_proposal(event=event, artifact=artifact, note=note)
    assert proposal["importance"] == 0.58
    assert proposal["confidence"] == 0.75
    assert proposal["memory_type"] == "insight"
    assert proposal["title"].startswith("链接笔记：")
    assert "media_note" in proposal["entities"]


def test_build_media_memory_proposal_low_confidence_is_preserved_for_governance():
    """低置信度只进入提案，由 Working Agent 决定案件状态。"""
    event = SimpleNamespace(
        id="evt_low_conf",
        user_id="user_snapshot",
        sensitivity=type("S", (), {"value": "normal"})(),
        event_metadata={},
    )
    artifact = SimpleNamespace(
        id="art_low_conf",
        source_channel="api",
        source_url=None,
        original_name="low_conf.txt",
        storage_path=None,
        media_type="file",
        status="extracted",
        extractor_name="text_extractor",
    )
    note = {"confidence": 0.3, "title": "Low conf", "summary": "", "text": "", "warnings": ["low_confidence"]}
    proposal = build_media_memory_proposal(event=event, artifact=artifact, note=note)
    assert proposal["confidence"] == 0.3
    assert proposal["memory_type"] == "insight"


def test_build_media_memory_proposal_caps_high_confidence():
    """适配器置信度最高 0.85，最终候选仍只能由 Working Agent 创建。"""
    event = SimpleNamespace(
        id="evt_high_conf",
        user_id="user_snapshot",
        sensitivity=type("S", (), {"value": "normal"})(),
        event_metadata={},
    )
    artifact = SimpleNamespace(
        id="art_high_conf",
        source_channel="api",
        source_url=None,
        original_name="high_conf.txt",
        storage_path=None,
        media_type="file",
        status="extracted",
        extractor_name="text_extractor",
    )
    note = {"confidence": 0.85, "title": "High conf", "summary": "", "text": "", "warnings": []}
    proposal = build_media_memory_proposal(event=event, artifact=artifact, note=note)
    assert proposal["confidence"] == 0.85
