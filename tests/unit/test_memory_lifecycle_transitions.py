import asyncio
from uuid import uuid4

from sqlalchemy import delete, select

from src.memory.models.memory_state_transition import MemoryStateTransition
from src.memory.services.memory_lifecycle import record_memory_state_transition
from src.shared.db.database import async_session, init_db


def test_memory_state_transition_is_append_only_and_transactional() -> None:
    async def run() -> None:
        await init_db()
        user_id = f"transition-user-{uuid4().hex}"
        subject_id = f"memory-{uuid4().hex}"
        async with async_session() as session:
            await record_memory_state_transition(
                session,
                user_id=user_id,
                subject_type="committed_memory",
                subject_id=subject_id,
                from_state="active",
                to_state="superseded",
                actor_type="user",
                actor_id=user_id,
                reason="user_correction",
                evidence_refs=["evt-test"],
            )
            assert any(
                isinstance(item, MemoryStateTransition) and item.subject_id == subject_id
                for item in session.new
            )
            await session.commit()
            row = await session.scalar(
                select(MemoryStateTransition).where(MemoryStateTransition.subject_id == subject_id)
            )
            assert row is not None
            assert row.from_state == "active"
            assert row.to_state == "superseded"
            assert row.evidence_refs == ["evt-test"]
            await session.execute(delete(MemoryStateTransition).where(MemoryStateTransition.user_id == user_id))
            await session.commit()

    asyncio.run(run())
