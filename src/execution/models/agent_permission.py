from sqlalchemy import Column, String, DateTime, ForeignKey, Index
from src.shared.db.database import Base
from datetime import datetime


class AgentPermission(Base):
    __tablename__ = "agent_permissions"

    id = Column(String(64), primary_key=True, index=True)
    user_id = Column(String(64), ForeignKey("users.id"), nullable=False, index=True)
    agent_id = Column(String(64), nullable=False, index=True)
    tool_name = Column(String(64), nullable=False, index=True)
    scope = Column(String(20), nullable=False, default="allow")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index(
            "uq_agent_permissions_agent_tool",
            "agent_id",
            "tool_name",
            unique=True,
        ),
        Index("ix_agent_permissions_user_agent", "user_id", "agent_id"),
    )
