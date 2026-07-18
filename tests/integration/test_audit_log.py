"""审计日志集成测试。"""
from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from src.shared.db.database import async_session, init_db
from src.main import app
from src.execution.models.agent_permission import AgentPermission
from src.execution.models.audit_log import AuditLog
from src.memory.models.committed_memory import CommittedMemory
from src.memory.models.memory_source import MemorySource
from src.execution.models.user import User
from src.memory.services.deduplicator import MemoryDeduplicator
from src.execution.services.tool_permission import ToolPermissionService
from src.shared.ids.id_generator import generate_agent_id
from test_gen3_os import get_user_id, register_user, seed_committed_memory


async def cleanup_audit_data(user_id: str) -> None:
    async with async_session() as session:
        await session.execute(delete(AuditLog).where(AuditLog.user_id == user_id))
        await session.execute(delete(AgentPermission).where(AgentPermission.user_id == user_id))
        await session.execute(
            delete(MemorySource).where(
                MemorySource.memory_id.in_(
                    select(CommittedMemory.id).where(CommittedMemory.user_id == user_id)
                )
            )
        )
        await session.execute(delete(CommittedMemory).where(CommittedMemory.user_id == user_id))
        await session.execute(User.__table__.delete().where(User.id == user_id))
        await session.commit()


def test_audit_log_grant_permission_creates_log():
    async def run():
        await init_db()
        client = TestClient(app)
        email, _ = register_user(client)
        user_id = await get_user_id(email)
        agent_id = generate_agent_id()

        async with async_session() as db:
            svc = ToolPermissionService(db)
            perm = await svc.grant(user_id, agent_id, "read_memory", scope="allow")
            result = await db.execute(
                select(AuditLog).where(
                    AuditLog.user_id == user_id,
                    AuditLog.action == "permission_grant",
                )
            )
            logs = result.scalars().all()

        assert len(logs) >= 1
        log = logs[0]
        assert log.target_type == "agent_permission"
        assert log.target_id == perm.id
        assert log.actor_type == "user"
        assert log.actor_id == user_id
        assert "read_memory" in (log.detail or "")
        await cleanup_audit_data(user_id)

    asyncio.run(run())


def test_audit_log_merge_creates_log():
    async def run():
        await init_db()
        client = TestClient(app)
        email, _ = register_user(client)
        user_id = await get_user_id(email)

        mem1_id = await seed_committed_memory(user_id, title="记忆 A", body="内容 A", importance=0.8)
        mem2_id = await seed_committed_memory(user_id, title="记忆 B", body="内容 B", importance=0.7)

        async with async_session() as db:
            dedup = MemoryDeduplicator(db)
            primary_id = await dedup.merge(mem1_id, mem2_id)
            result = await db.execute(
                select(AuditLog).where(
                    AuditLog.user_id == user_id,
                    AuditLog.action == "memory_merge",
                )
            )
            logs = result.scalars().all()

        assert len(logs) >= 1
        log = logs[0]
        assert log.target_type == "memory"
        assert log.target_id == primary_id
        assert log.actor_type == "user"
        assert mem2_id in (log.detail or "")
        await cleanup_audit_data(user_id)

    asyncio.run(run())
