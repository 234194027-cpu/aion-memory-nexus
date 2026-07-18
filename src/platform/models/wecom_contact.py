from sqlalchemy import Boolean, Column, DateTime, Index, JSON, String, func

from src.shared.db.database import Base


class WeComContact(Base):
    __tablename__ = "wecom_contacts"

    id = Column(String, primary_key=True, index=True)
    user_id = Column(String, nullable=False, index=True)
    wecom_user_id = Column(String, nullable=True, index=True)
    chat_id = Column(String, nullable=True, index=True)
    chat_type = Column(String, nullable=True)
    aibot_id = Column(String, nullable=True)
    is_default = Column(Boolean, nullable=False, default=False, index=True)
    last_message_id = Column(String, nullable=True)
    last_seen_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    contact_metadata = Column(JSON, default=dict)

    __table_args__ = (
        Index("ix_wecom_contacts_user_default", "user_id", "is_default"),
        Index("ix_wecom_contacts_user_wecom", "user_id", "wecom_user_id"),
        Index("ix_wecom_contacts_user_chat", "user_id", "chat_id"),
    )
