import asyncio
from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from src.app.main import app
from src.cognition.models.knowledge_page import KnowledgePage, KnowledgePageMemory, KnowledgePageVersion
from src.execution.models.memory_relation import MemoryRelation
from src.execution.models.user import User
from src.memory.models.committed_memory import CommittedMemory, CommittedStatus
from src.memory.models.memory_type import MemoryType
from src.memory.models.data_lifecycle_audit import DataLifecycleAudit
from src.memory.models.memory_embedding import MemoryEmbedding
from src.memory.models.memory_source import MemorySource
from src.memory.models.raw_event import ProcessingStatus, RawEvent, SensitivityLevel, SourceType, VisibilityScope
from src.shared.db.database import async_session, init_db


def _register(client: TestClient) -> tuple[str, str]:
    email = f"portability-{uuid4().hex}@example.com"
    response = client.post("/api/auth/register", json={"email": email, "password": "test-password-123"})
    assert response.status_code == 200, response.text
    return email, response.json()["access_token"]


async def _user_id(email: str) -> str:
    async with async_session() as session:
        return (await session.scalar(select(User.id).where(User.email == email)))


async def _seed_memory(session, *, user_id: str, memory_id: str, raw_event_id: str, title: str) -> None:
    session.add(RawEvent(
        id=raw_event_id,
        source_type=SourceType.MANUAL,
        user_id=user_id,
        occurred_at=datetime.now(timezone.utc),
        content=f"raw {title}",
        content_hash=uuid4().hex,
        sensitivity=SensitivityLevel.NORMAL,
        visibility_scope=VisibilityScope.PERSONAL,
        processing_status=ProcessingStatus.COMPLETED,
    ))
    session.add(CommittedMemory(
        id=memory_id,
        user_id=user_id,
        memory_type=MemoryType.FACT,
        title=title,
        body=f"memory {title}",
        confidence=0.9,
        importance=0.8,
        sensitivity=SensitivityLevel.NORMAL,
        visibility_scope=VisibilityScope.PERSONAL,
        status=CommittedStatus.ACTIVE,
        valid_from=datetime.now(timezone.utc),
        tags=["可移植性"],
    ))
    session.add(MemorySource(
        id=f"src-{uuid4().hex}", memory_id=memory_id, raw_event_id=raw_event_id,
        quote=f"quote {title}", source_type=SourceType.MANUAL,
    ))
    session.add(MemoryEmbedding(
        id=f"emb-{uuid4().hex}", memory_id=memory_id, embedding_model="fallback",
        embedding_vector=[0.0] * 1024, content_snapshot=f"snapshot {title}", dimension=1024,
    ))


async def _cleanup(user_id: str) -> None:
    async with async_session() as session:
        page_ids = list((await session.execute(select(KnowledgePage.id).where(KnowledgePage.user_id == user_id))).scalars())
        if page_ids:
            await session.execute(delete(KnowledgePageMemory).where(KnowledgePageMemory.page_id.in_(page_ids)))
        await session.execute(delete(KnowledgePageVersion).where(KnowledgePageVersion.user_id == user_id))
        await session.execute(delete(KnowledgePage).where(KnowledgePage.user_id == user_id))
        await session.execute(delete(DataLifecycleAudit).where(DataLifecycleAudit.user_id == user_id))
        await session.execute(delete(MemoryRelation).where(MemoryRelation.user_id == user_id))
        memory_ids = list((await session.execute(select(CommittedMemory.id).where(CommittedMemory.user_id == user_id))).scalars())
        if memory_ids:
            await session.execute(delete(MemoryEmbedding).where(MemoryEmbedding.memory_id.in_(memory_ids)))
            await session.execute(delete(MemorySource).where(MemorySource.memory_id.in_(memory_ids)))
        await session.execute(delete(CommittedMemory).where(CommittedMemory.user_id == user_id))
        await session.execute(delete(RawEvent).where(RawEvent.user_id == user_id))
        await session.execute(delete(User).where(User.id == user_id))
        await session.commit()


def test_account_export_is_user_scoped_and_deletion_cleans_derivatives() -> None:
    async def run() -> None:
        await init_db()
        client = TestClient(app)
        owner_email, owner_token = _register(client)
        other_email, other_token = _register(client)
        owner_id, other_id = await _user_id(owner_email), await _user_id(other_email)
        owner_memory, other_memory = f"mem-{uuid4().hex}", f"mem-{uuid4().hex}"
        owner_event, other_event = f"evt-{uuid4().hex}", f"evt-{uuid4().hex}"
        async with async_session() as session:
            await _seed_memory(session, user_id=owner_id, memory_id=owner_memory, raw_event_id=owner_event, title="我的可移植记忆")
            await _seed_memory(session, user_id=other_id, memory_id=other_memory, raw_event_id=other_event, title="他人的记忆")
            session.add(MemoryRelation(
                id=f"rel-{uuid4().hex}", user_id=owner_id, source_memory_id=owner_memory,
                target_memory_id=owner_memory, relation_type="supports", reason="derived reason", confidence=0.8,
            ))
            await session.commit()

        owner_headers = {"Authorization": f"Bearer {owner_token}"}
        other_headers = {"Authorization": f"Bearer {other_token}"}
        rebuilt = client.post("/api/knowledge-workspace/wiki/rebuild", headers=owner_headers)
        assert rebuilt.status_code == 200, rebuilt.text

        exported = client.get("/api/data-portability/export", headers=owner_headers)
        assert exported.status_code == 200, exported.text
        assert "attachment; filename=\"life-memory-export-" in exported.headers["content-disposition"]
        payload = exported.json()
        assert payload["format"] == "life-memory-export/v3"
        assert "candidate_memories" not in payload["data"]
        assert payload["account"]["id"] == owner_id
        assert {item["id"] for item in payload["data"]["committed_memories"]} == {owner_memory}
        assert {item["id"] for item in payload["data"]["raw_events"]} == {owner_event}
        assert "memory_embeddings" not in payload["data"]
        assert "conversation_turns" in payload["data"]
        assert "conversation_episodes" in payload["data"]
        assert "conversation_attention_candidates" in payload["data"]
        assert "agent_workspace_projection" in payload["data"]
        assert "hashed_password" not in str(payload)
        assert "embedding_vector" not in str(payload)
        assert "content_snapshot" not in str(payload)

        other_export = client.get("/api/data-portability/export", headers=other_headers).json()
        assert {item["id"] for item in other_export["data"]["committed_memories"]} == {other_memory}

        forgotten = client.post(f"/api/memory/{owner_memory}/forget", json={"action": "delete"}, headers=owner_headers)
        assert forgotten.status_code == 200, forgotten.text
        async with async_session() as session:
            assert await session.scalar(
                select(KnowledgePageMemory.id).where(KnowledgePageMemory.user_id == owner_id, KnowledgePageMemory.memory_id == owner_memory)
            ) is None
            assert await session.scalar(
                select(MemoryRelation.id).where(MemoryRelation.user_id == owner_id, MemoryRelation.source_memory_id == owner_memory)
            ) is None
            versions = list((await session.execute(
                select(KnowledgePageVersion).where(KnowledgePageVersion.user_id == owner_id)
            )).scalars())
            assert all(owner_memory not in version.memory_ids for version in versions)
            audit = await session.scalar(
                select(DataLifecycleAudit).where(
                    DataLifecycleAudit.user_id == owner_id,
                    DataLifecycleAudit.action == "delete",
                    DataLifecycleAudit.target_id == owner_memory,
                )
            )
            assert audit is not None
            assert "memory" not in str(audit.affected_counts).lower()
            assert "我的可移植记忆" not in str(audit.affected_counts)

        await _cleanup(owner_id)
        await _cleanup(other_id)

    asyncio.run(run())
