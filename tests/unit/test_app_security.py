import asyncio

from fastapi.responses import FileResponse
from fastapi.testclient import TestClient

from src.app import main as app_main


def test_serve_spa_rejects_static_path_traversal(tmp_path, monkeypatch):
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("index", encoding="utf-8")
    secret = tmp_path / "secret.txt"
    secret.write_text("secret", encoding="utf-8")

    monkeypatch.setattr(app_main, "static_dir", static_dir)

    response = asyncio.run(app_main.serve_spa("../secret.txt"))

    assert isinstance(response, FileResponse)
    assert response.path == str(static_dir / "index.html")


def test_security_headers_are_set():
    client = TestClient(app_main.app)

    response = client.get("/")

    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["permissions-policy"] == "camera=(), microphone=(), geolocation=()"
    assert "x-xss-protection" not in response.headers


def test_production_csp_does_not_allow_inline_scripts(monkeypatch):
    monkeypatch.setattr(app_main, "is_production", True)

    csp = app_main._build_content_security_policy()

    assert "script-src 'self';" in csp
    assert "script-src 'self' 'unsafe-inline'" not in csp
    assert "object-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "trae-api-cn.mchost.guru" not in csp
    assert "connect-src 'self';" in csp


def test_metrics_can_require_system_token(monkeypatch):
    monkeypatch.setattr(app_main.settings, "METRICS_REQUIRE_TOKEN", True)
    monkeypatch.setattr(app_main, "get_system_api_token", lambda: "metrics-token")
    client = TestClient(app_main.app)

    unauthorized = client.get("/metrics")
    authorized = client.get("/metrics", headers={"Authorization": "Bearer metrics-token"})

    assert unauthorized.status_code == 401
    assert authorized.status_code == 200


def test_production_rejects_default_solo_mode(monkeypatch):
    monkeypatch.setattr(app_main, "is_production", True)
    monkeypatch.setattr(app_main.settings, "SECRET_KEY", "not-default")
    monkeypatch.setattr(app_main.settings, "SOLO_MODE", True)
    monkeypatch.setattr(app_main.settings, "ALLOW_SOLO_PRODUCTION", False)

    try:
        app_main._validate_production_security()
    except RuntimeError as exc:
        assert "SOLO_MODE" in str(exc)
    else:
        raise AssertionError("production SOLO_MODE should be rejected by default")
