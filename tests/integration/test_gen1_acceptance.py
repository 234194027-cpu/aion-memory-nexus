"""V2.4 product contract: no candidate-memory or manual-review API remains."""

from uuid import uuid4

from fastapi.testclient import TestClient

from src.main import app
from src.shared.config import settings
from src.shared.security.auth import decode_access_token


def test_candidate_memory_and_manual_commit_routes_are_removed():
    client = TestClient(app)
    email = f"v24-{uuid4().hex}@example.com"
    registered = client.post(
        "/api/auth/register",
        json={"email": email, "password": "test123456"},
    )
    assert registered.status_code == 200, registered.text
    headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}

    for method, path in (
        ("get", "/api/candidates/"),
        ("post", "/api/candidates/legacy/accept"),
        ("post", "/api/memory/commit"),
        ("post", "/api/memory/commit/batch"),
    ):
        response = getattr(client, method)(path, headers=headers)
        # FastAPI's SPA GET fallback can make an unknown POST return 405. Both
        # codes prove that no callable candidate/manual-review operation exists.
        assert response.status_code in {404, 405}, (path, response.status_code, response.text)


def test_system_stats_exposes_formal_memory_counts_only():
    client = TestClient(app)
    email = f"v24-stats-{uuid4().hex}@example.com"
    registered = client.post(
        "/api/auth/register",
        json={"email": email, "password": "test123456"},
    )
    headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}
    response = client.get("/api/system/stats", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert {"memory_count", "today_memory_count", "event_count", "agent_count"} <= body.keys()
    assert "candidate_count" not in body
    assert "pending_candidate_count" not in body


def test_graph_operations_require_the_configured_owner(monkeypatch):
    client = TestClient(app)
    owner = client.post(
        "/api/auth/register",
        json={"email": f"graph-owner-{uuid4().hex}@example.com", "password": "test123456"},
    )
    other = client.post(
        "/api/auth/register",
        json={"email": f"graph-other-{uuid4().hex}@example.com", "password": "test123456"},
    )
    assert owner.status_code == 200, owner.text
    assert other.status_code == 200, other.text
    owner_token = owner.json()["access_token"]
    other_token = other.json()["access_token"]
    owner_id = decode_access_token(owner_token)["user_id"]
    monkeypatch.setattr(settings, "SOLO_MODE", False)
    monkeypatch.setattr(settings, "GRAPHITI_ADMIN_USER_IDS", owner_id)

    denied = client.post(
        "/api/graph/replay",
        headers={"Authorization": f"Bearer {other_token}"},
        json={"dry_run": True},
    )
    assert denied.status_code == 403, denied.text

    allowed = client.post(
        "/api/graph/replay",
        headers={"Authorization": f"Bearer {owner_token}"},
        json={"dry_run": True},
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["dry_run"] is True
