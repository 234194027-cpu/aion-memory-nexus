from datetime import datetime

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text
from src.shared.db.database import Base


class LifeTask(Base):
    __tablename__ = "life_tasks"

    id = Column(String(64), primary_key=True)
    user_id = Column(String(64), ForeignKey("users.id"), index=True)
    title = Column(String(255))
    description = Column(Text, nullable=True)
    status = Column(String(20))
    priority = Column(String(10))
    project_id = Column(String(128), nullable=True)
    parent_task_id = Column(String(64), nullable=True)
    linked_memory_ids = Column(Text, nullable=True)
    linked_decision_ids = Column(Text, nullable=True)
    due_at = Column(DateTime, nullable=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    assigned_agent_id = Column(String(64), nullable=True)
    priority_score = Column(Float, nullable=False, default=0.5)
    sub_tasks_count = Column(Integer, nullable=False, default=0)
