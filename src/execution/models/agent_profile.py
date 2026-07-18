from sqlalchemy import Column, String, DateTime, Enum, func, Boolean, JSON, Float, Integer, Index
from src.shared.db.database import Base
from enum import Enum as PyEnum

class AgentType(PyEnum):
    CODEX = "codex"
    OPENCLAW = "openclaw"
    CLAUDE_CODE = "claude_code"
    WECOM = "wecom"
    ADVISOR = "advisor"
    CUSTOM = "custom"

class RecallLevel(PyEnum):
    TASK_ONLY = "task_only"
    WORK_CONTEXT = "work_context"
    PERSONAL_CONTEXT = "personal_context"
    FULL_TRUSTED = "full_trusted"

class LLMProvider(PyEnum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    TOGETHER = "together"
    OLLAMA = "ollama"
    QWEN = "qwen"
    DEEPSEEK = "deepseek"
    CUSTOM = "custom"

class AgentProfile(Base):
    __tablename__ = "agent_profiles"
    
    id = Column(String, primary_key=True, index=True)
    user_id = Column(String, nullable=False, default="default", index=True)
    agent_name = Column(String, nullable=False)
    agent_type = Column(Enum(AgentType, values_callable=lambda x: [e.value for e in x]), nullable=False)
    allowed_write_scopes = Column(JSON, default=[])
    allowed_read_scopes = Column(JSON, default=[])
    default_recall_level = Column(Enum(RecallLevel, values_callable=lambda x: [e.value for e in x]), default=RecallLevel.TASK_ONLY)
    token_hash = Column(String, nullable=False, index=True)
    api_token_hash = Column(String, nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_seen_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(Boolean, default=True)
    is_default = Column(Boolean, default=False)  # 默认助手不可删除

    llm_provider = Column(Enum(LLMProvider, values_callable=lambda x: [e.value for e in x]), nullable=True)
    llm_model = Column(String, nullable=True)
    llm_api_key = Column(String, nullable=True)
    llm_api_base = Column(String, nullable=True)
    llm_temperature = Column(Float, default=0.7)
    llm_max_tokens = Column(Integer, default=4096)
    custom_provider_key = Column(String, nullable=True)
    
    mission = Column(String, nullable=True)
    role = Column(String, nullable=True)
    goals = Column(JSON, default=[])
    constraints = Column(JSON, default=[])
    instructions = Column(String, nullable=True)
    
    schedule_enabled = Column(Boolean, default=True)
    event_extraction_interval = Column(Integer, default=5)
    memory_organize_hour = Column(Integer, default=2)
    weekly_summary_day = Column(Integer, default=0)
    obsidian_sync_interval = Column(Integer, default=60)

    __table_args__ = (
        Index("ix_agent_token_hash", "token_hash", unique=True),
    )
