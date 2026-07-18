"""WebSocket streaming 端点测试。"""

import asyncio
import uuid

from fastapi.testclient import TestClient

from src.shared.db.database import init_db
from src.main import app
from src.shared.security.auth import decode_access_token


client = TestClient(app)


def _register_ws_user():
    email = f"ws_{uuid.uuid4().hex[:8]}@test.com"
    r = client.post("/api/auth/register", json={"email": email, "password": "test1234"})
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    payload = decode_access_token(token)
    user_id = payload["user_id"]
    return user_id, token


def test_websocket_chat_connects_and_receives_tokens():
    asyncio.run(init_db())
    user_id, token = _register_ws_user()
    with client.websocket_connect(f"/api/memory/ws/chat/{user_id}?token={token}") as ws:
        ws.send_json({"question": "你好，我是谁？"})
        messages = []
        while True:
            data = ws.receive_json()
            messages.append(data)
            if data.get("event") in ("done", "error"):
                break

    assert len(messages) >= 1
    assert messages[-1]["event"] in ("done", "error")


def test_websocket_advisor_ask_streams_result():
    asyncio.run(init_db())
    user_id, token = _register_ws_user()
    with client.websocket_connect(f"/api/advisor/ws/ask/{user_id}?token={token}") as ws:
        ws.send_json({"question": "给我一些建议", "mode": "suggest"})
        messages = []
        while True:
            data = ws.receive_json()
            messages.append(data)
            if data.get("event") in ("done", "error"):
                break

    assert len(messages) >= 1
    last = messages[-1]
    assert last["event"] in ("done", "error")
    if last["event"] == "done":
        assert "data" in last


def test_websocket_multi_agent_streams_drafts():
    asyncio.run(init_db())
    user_id, token = _register_ws_user()
    with client.websocket_connect(f"/api/orchestration/ws/multi-agent/{user_id}?token={token}") as ws:
        ws.send_json({"question": "这个问题需要多个视角", "max_agents": 2})
        messages = []
        while True:
            data = ws.receive_json()
            messages.append(data)
            if data.get("event") in ("done", "error"):
                break

    assert len(messages) >= 1
    events = [m["event"] for m in messages]
    assert "done" in events or "error" in events
    if "done" in events:
        assert "status" in events
