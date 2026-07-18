"""End-to-end coverage for the source-backed knowledge workspace."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from src.cognition.models.knowledge_page import KnowledgePage, KnowledgePageMemory
from src.execution.models.memory_relation import MemoryRelation
from src.execution.models.user import User
from src.main import app
from src.memory.models.memory_type import MemoryType
from src.memory.models.committed_memory import CommittedMemory, CommittedStatus
from src.memory.models.memory_source import MemorySource
from src.memory.models.raw_event import ProcessingStatus, RawEvent, SensitivityLevel, SourceType, VisibilityScope
from src.shared.db.database import async_session, init_db


def _register(client: TestClient) -> tuple[str, str]:
    email = f"workspace-{uuid4().hex}@example.com"
    response = client.post("/api/auth/register", json={"email": email, "password": "test123456"})
    assert response.status_code == 200, response.text
    return email, response.json()["access_token"]


async def _user_id(email: str) -> str:
    async with async_session() as session:
        return (await session.scalar(select(User.id).where(User.email == email)))


async def _seed_memory(session, *, user_id: str, memory_id: str, title: str, tags: list[str], occurred_at: datetime) -> RawEvent:
    event = RawEvent(
        id=f"evt-{memory_id}",
        source_type=SourceType.MANUAL,
        user_id=user_id,
        occurred_at=occurred_at,
        content=f"source for {title}",
        content_hash=uuid4().hex,
        sensitivity=SensitivityLevel.NORMAL,
        visibility_scope=VisibilityScope.PERSONAL,
        processing_status=ProcessingStatus.COMPLETED,
    )
    session.add(event)
    session.add(
        CommittedMemory(
            id=memory_id,
            user_id=user_id,
            memory_type=MemoryType.FACT,
            title=title,
            body=f"body for {title}",
            confidence=0.9,
            importance=0.8,
            sensitivity=SensitivityLevel.NORMAL,
            visibility_scope=VisibilityScope.PERSONAL,
            status=CommittedStatus.ACTIVE,
            valid_from=occurred_at,
            tags=tags,
        )
    )
    session.add(
        MemorySource(
            id=f"src-{memory_id}",
            memory_id=memory_id,
            raw_event_id=event.id,
            source_type=SourceType.MANUAL,
            quote=f"quote for {title}",
        )
    )
    return event


async def _cleanup(user_id: str) -> None:
    async with async_session() as session:
        memory_ids = select(CommittedMemory.id).where(CommittedMemory.user_id == user_id)
        page_ids = select(KnowledgePage.id).where(KnowledgePage.user_id == user_id)
        await session.execute(delete(KnowledgePageMemory).where(KnowledgePageMemory.user_id == user_id))
        await session.execute(delete(KnowledgePage).where(KnowledgePage.user_id == user_id))
        await session.execute(delete(MemoryRelation).where(MemoryRelation.user_id == user_id))
        await session.execute(delete(MemorySource).where(MemorySource.memory_id.in_(memory_ids)))
        await session.execute(delete(CommittedMemory).where(CommittedMemory.user_id == user_id))
        await session.execute(delete(RawEvent).where(RawEvent.user_id == user_id))
        await session.execute(delete(User).where(User.id == user_id))
        await session.commit()


def test_knowledge_workspace_is_user_scoped_and_source_backed() -> None:
    async def run() -> None:
        await init_db()
        client = TestClient(app)
        owner_email, owner_token = _register(client)
        other_email, other_token = _register(client)
        owner_id, other_id = await _user_id(owner_email), await _user_id(other_email)
        old_time = datetime.now(timezone.utc) - timedelta(days=10)
        new_time = datetime.now(timezone.utc) - timedelta(days=2)
        owner_old = f"mem-{uuid4().hex}"
        owner_new = f"mem-{uuid4().hex}"
        other_memory = f"mem-{uuid4().hex}"
        async with async_session() as session:
            await _seed_memory(session, user_id=owner_id, memory_id=owner_old, title="早期关系", tags=["关系", "成长"], occurred_at=old_time)
            await _seed_memory(session, user_id=owner_id, memory_id=owner_new, title="近期关系", tags=["关系"], occurred_at=new_time)
            await _seed_memory(session, user_id=other_id, memory_id=other_memory, title="他人关系", tags=["关系"], occurred_at=new_time)
            session.add(
                MemoryRelation(
                    id=f"rel-{uuid4().hex}",
                    user_id=owner_id,
                    source_memory_id=owner_old,
                    target_memory_id=owner_new,
                    relation_type="supports",
                    confidence=0.8,
                )
            )
            await session.commit()

        headers = {"Authorization": f"Bearer {owner_token}"}
        rebuild = client.post("/api/knowledge-workspace/wiki/rebuild", headers=headers)
        assert rebuild.status_code == 200, rebuild.text
        assert rebuild.json()["page_count"] >= 2

        graph = client.get("/api/knowledge-workspace/graph", headers=headers)
        assert graph.status_code == 200, graph.text
        graph_ids = {node["id"] for node in graph.json()["nodes"]}
        assert graph_ids == {owner_old, owner_new}
        assert len(graph.json()["edges"]) == 1
        edge = graph.json()["edges"][0]
        assert {key: edge[key] for key in ("source", "target", "relation_type", "confidence")} == {
            "source": owner_old,
            "target": owner_new,
            "relation_type": "supports",
            "confidence": 0.8,
        }
        assert edge["reason"] is None
        assert edge["valid_from"] is None
        assert edge["created_at"]

        timeline = client.get("/api/knowledge-workspace/timeline", headers=headers)
        assert timeline.status_code == 200, timeline.text
        assert [item["memory_id"] for item in timeline.json()["entries"]] == [owner_new, owner_old]
        assert {item["time_basis"] for item in timeline.json()["entries"]} == {"occurred_at"}

        wiki = client.get("/api/knowledge-workspace/wiki", headers=headers)
        assert wiki.status_code == 200, wiki.text
        relationship_page = next(item for item in wiki.json() if item["title"] == "关系")
        detail = client.get(f"/api/knowledge-workspace/wiki/{relationship_page['slug']}", headers=headers)
        assert detail.status_code == 200, detail.text
        assert {item["id"] for item in detail.json()["memories"]} == {owner_old, owner_new}
        assert {item["raw_event_id"] for item in detail.json()["source_refs"]} == {f"evt-{owner_old}", f"evt-{owner_new}"}
        assert detail.json()["version_history"]
        assert detail.json()["memories"][0]["relation_basis"] == "tag"

        other_headers = {"Authorization": f"Bearer {other_token}"}
        other_nodes = client.get("/api/knowledge-workspace/graph", headers=other_headers).json()["nodes"]
        assert len(other_nodes) == 1
        assert other_nodes[0]["id"] == other_memory
        assert other_nodes[0]["title"] == "他人关系"
        assert other_nodes[0]["occurred_at"]

        await _cleanup(owner_id)
        await _cleanup(other_id)

    asyncio.run(run())
