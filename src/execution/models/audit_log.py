from sqlalchemy import Column, String, DateTime, Text
from src.shared.db.database import Base
from datetime import datetime


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(String(64), primary_key=True, index=True)
    user_id = Column(String(64), index=True, nullable=False)
    actor_type = Column(String(20), nullable=False, default="user")   # "user" | "agent" | "system"
    actor_id = Column(String(64), nullable=True)                      # user_id 或 agent_id
    action = Column(String(64), nullable=False, index=True)           # "memory_merge" | "task_auto_extract" | "decision_track" | "persona_rebuild" | "simulation_run" | "permission_grant" | "permission_revoke" | ...
    target_type = Column(String(32), nullable=True)                   # "memory" | "task" | "decision" | "agent_permission" | "simulation_run" | "weekly_review" | "persona_snapshot"
    target_id = Column(String(64), nullable=True, index=True)
    detail = Column(Text, nullable=True)                              # JSON blob with extra context
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
