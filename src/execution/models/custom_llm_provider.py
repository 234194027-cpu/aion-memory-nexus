from sqlalchemy import Column, String, DateTime, func, Boolean, JSON, UniqueConstraint
from src.shared.db.database import Base

class CustomLLMProvider(Base):
    __tablename__ = "custom_llm_providers"
    __table_args__ = (
        UniqueConstraint("user_id", "provider_name", name="uq_custom_provider_user_name"),
        UniqueConstraint("user_id", "provider_key", name="uq_custom_provider_user_key"),
    )
    
    id = Column(String, primary_key=True, index=True)
    user_id = Column(String, nullable=False, default="default", index=True)
    provider_name = Column(String, nullable=False)
    provider_key = Column(String, nullable=False)
    base_url = Column(String, nullable=False)
    api_key = Column(String, nullable=True)
    model_name = Column(String, nullable=True)
    api_format = Column(String, nullable=False, default="openai")
    headers = Column(JSON, default={})
    is_preset = Column(Boolean, default=False)
    icon = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    status = Column(Boolean, default=True)
