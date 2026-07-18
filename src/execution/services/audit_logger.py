import json
import inspect
from sqlalchemy.ext.asyncio import AsyncSession
from src.execution.models.audit_log import AuditLog
from src.shared.ids.id_generator import generate_audit_log_id
from datetime import datetime


class AuditLogger:
    @staticmethod
    async def log(
        db: AsyncSession,
        *,
        user_id: str,
        action: str,
        actor_type: str = "user",
        actor_id: str = None,
        target_type: str = None,
        target_id: str = None,
        detail: dict = None,
    ) -> AuditLog:
        """创建一条审计日志并 commit。"""
        entry = AuditLog(
            id=generate_audit_log_id(),
            user_id=user_id,
            actor_type=actor_type,
            actor_id=actor_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            detail=json.dumps(detail, ensure_ascii=False) if detail else None,
            created_at=datetime.utcnow(),
        )
        add_result = db.add(entry)
        if inspect.isawaitable(add_result):
            await add_result
        await db.commit()
        await db.refresh(entry)
        return entry
