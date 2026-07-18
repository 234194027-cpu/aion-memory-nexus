from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "smoke_media_ingestion.py"
SPEC = importlib.util.spec_from_file_location("smoke_media_ingestion", SCRIPT_PATH)
smoke = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(smoke)


def test_smoke_write_requires_agent_token(monkeypatch) -> None:
    calls = []

    def fake_request(*args, **kwargs):
        calls.append((args, kwargs))
        return 200, {"status": "healthy"}

    monkeypatch.delenv("LIFE_MEMORY_AGENT_TOKEN", raising=False)
    monkeypatch.delenv("LIFE_MEMORY_SYSTEM_API_TOKEN", raising=False)
    monkeypatch.setattr(sys, "argv", ["smoke", "--create-link", "https://example.com"])
    monkeypatch.setattr(smoke, "_request", fake_request)

    assert smoke.main() == 1
    assert len(calls) == 1


def test_smoke_sync_link_and_text_payloads(monkeypatch) -> None:
    seen_payloads = []

    def fake_request(base_url, method, path, token=None, payload=None):
        if path == "/health":
            return 200, {"status": "healthy"}
        if path.startswith("/api/media/artifacts"):
            return 200, {"items": []}
        seen_payloads.append((path, payload))
        if path == "/api/media/link":
            return 200, {
                "memory_id": "mem_link",
                "artifact": {"id": "media_link", "media_type": "link", "status": "extracted"},
            }
        if path == "/api/media/upload-base64":
            return 200, {
                "memory_id": "mem_upload",
                "artifact": {"id": "media_upload", "media_type": "file", "status": "extracted"},
            }
        raise AssertionError(path)

    monkeypatch.setenv("LIFE_MEMORY_AGENT_TOKEN", "agent-token")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "smoke",
            "--create-link",
            "https://example.com",
            "--upload-text",
            "hello media note",
            "--sync-extract",
        ],
    )
    monkeypatch.setattr(smoke, "_request", fake_request)

    assert smoke.main() == 0
    assert [path for path, _ in seen_payloads] == ["/api/media/link", "/api/media/upload-base64"]
    assert all(payload["extract"] is True and payload["sync"] is True for _, payload in seen_payloads)
    upload_payload = seen_payloads[1][1]
    assert upload_payload["filename"] == "smoke-note.txt"
    assert upload_payload["content_base64"]


def test_smoke_checks_media_payload_safety(monkeypatch) -> None:
    requested_paths = []

    def fake_request(base_url, method, path, token=None, payload=None):
        requested_paths.append(path)
        if path == "/health":
            return 200, {"status": "healthy"}
        if path.startswith("/api/media/artifacts?"):
            return 200, {
                "items": [
                    {
                        "id": "media_safe",
                        "media_type": "image",
                        "status": "received",
                        "has_extracted_text": False,
                    }
                ]
            }
        if path == "/api/media/artifacts/media_safe":
            return 200, {
                "id": "media_safe",
                "media_type": "image",
                "status": "received",
                "has_extracted_text": False,
                "extracted_note": None,
            }
        raise AssertionError(path)

    monkeypatch.setenv("LIFE_MEMORY_AGENT_TOKEN", "agent-token")
    monkeypatch.setattr(sys, "argv", ["smoke"])
    monkeypatch.setattr(smoke, "_request", fake_request)

    assert smoke.main() == 0
    assert "/api/media/artifacts/media_safe" in requested_paths


def test_smoke_fails_when_media_payload_leaks_paths(monkeypatch) -> None:
    def fake_request(base_url, method, path, token=None, payload=None):
        if path == "/health":
            return 200, {"status": "healthy"}
        if path.startswith("/api/media/artifacts?"):
            return 200, {
                "items": [
                    {
                        "id": "media_leaky",
                        "media_type": "image",
                        "status": "received",
                        "storage_path": "uploads/private/image.png",
                    }
                ]
            }
        if path == "/api/media/artifacts/media_leaky":
            return 200, {"id": "media_leaky", "status": "received"}
        raise AssertionError(path)

    monkeypatch.setenv("LIFE_MEMORY_AGENT_TOKEN", "agent-token")
    monkeypatch.setattr(sys, "argv", ["smoke"])
    monkeypatch.setattr(smoke, "_request", fake_request)

    assert smoke.main() == 1


def test_smoke_wecom_debug_requires_jwt(monkeypatch) -> None:
    def fake_request(*args, **kwargs):
        return 200, {"status": "healthy"}

    monkeypatch.delenv("LIFE_MEMORY_AGENT_TOKEN", raising=False)
    monkeypatch.delenv("LIFE_MEMORY_SYSTEM_API_TOKEN", raising=False)
    monkeypatch.delenv("LIFE_MEMORY_JWT_TOKEN", raising=False)
    monkeypatch.setattr(sys, "argv", ["smoke", "--check-wecom-debug"])
    monkeypatch.setattr(smoke, "_request", fake_request)

    assert smoke.main() == 1


def test_smoke_wecom_debug_uses_bearer_token_and_checks_safety(monkeypatch) -> None:
    seen = []

    def fake_request(base_url, method, path, token=None, payload=None, bearer_token=None):
        seen.append((path, token, bearer_token))
        if path == "/health":
            return 200, {"status": "healthy"}
        if path == "/api/wecom/media-debug/events?limit=5":
            return 200, {
                "items": [
                    {
                        "id": "media_wecom",
                        "media_type": "image",
                        "status": "received",
                        "has_wecom_media_id": True,
                        "raw_payload_shape": {"image": {"media_id": "str"}},
                    }
                ]
            }
        raise AssertionError(path)

    monkeypatch.delenv("LIFE_MEMORY_AGENT_TOKEN", raising=False)
    monkeypatch.delenv("LIFE_MEMORY_SYSTEM_API_TOKEN", raising=False)
    monkeypatch.setenv("LIFE_MEMORY_JWT_TOKEN", "jwt-token")
    monkeypatch.setattr(sys, "argv", ["smoke", "--check-wecom-debug"])
    monkeypatch.setattr(smoke, "_request", fake_request)

    assert smoke.main() == 0
    assert ("/api/wecom/media-debug/events?limit=5", None, "jwt-token") in seen


def test_smoke_wecom_debug_fails_when_payload_leaks_media_id(monkeypatch) -> None:
    def fake_request(base_url, method, path, token=None, payload=None, bearer_token=None):
        if path == "/health":
            return 200, {"status": "healthy"}
        if path == "/api/wecom/media-debug/events?limit=5":
            return 200, {"items": [{"id": "media_wecom", "wecom_media_id": "secret-media-id"}]}
        raise AssertionError(path)

    monkeypatch.delenv("LIFE_MEMORY_AGENT_TOKEN", raising=False)
    monkeypatch.delenv("LIFE_MEMORY_SYSTEM_API_TOKEN", raising=False)
    monkeypatch.setenv("LIFE_MEMORY_JWT_TOKEN", "jwt-token")
    monkeypatch.setattr(sys, "argv", ["smoke", "--check-wecom-debug"])
    monkeypatch.setattr(smoke, "_request", fake_request)

    assert smoke.main() == 1
