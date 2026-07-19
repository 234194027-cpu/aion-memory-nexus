from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy import create_engine as create_sync_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy import select
from src.shared.config import settings
import logging
import asyncio

logger = logging.getLogger(__name__)
_init_db_lock = asyncio.Lock()
_db_initialized = False

if settings.POSTGRES_URL.startswith("sqlite"):
    engine = create_async_engine(settings.POSTGRES_URL, echo=False, connect_args={"check_same_thread": False})
    sync_engine = create_sync_engine(settings.POSTGRES_URL.replace("+aiosqlite", ""), echo=False, connect_args={"check_same_thread": False})
else:
    # PostgreSQL: 配置连接池防止生产环境连接耗尽
    _pg_url = settings.POSTGRES_URL.replace("postgresql://", "postgresql+psycopg://")
    _pg_pool_kwargs = dict(
        pool_size=10,
        max_overflow=20,
        pool_recycle=3600,
        pool_pre_ping=True,
    )
    engine = create_async_engine(
        _pg_url,
        echo=False,
        **_pg_pool_kwargs,
    )
    sync_engine = create_sync_engine(
        _pg_url,
        echo=False,
        **_pg_pool_kwargs,
    )

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
sync_session = sessionmaker(sync_engine, expire_on_commit=False)

class Base(DeclarativeBase):
    pass

async def get_db():
    async with async_session() as session:
        yield session

def get_db_sync():
    with sync_session() as session:
        yield session

async def init_db():
    global _db_initialized

    async with _init_db_lock:
        if _db_initialized:
            return

        if settings.CREATE_SCHEMA_ON_STARTUP:
            _import_all_models()

            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

        if settings.AUTO_PATCH_SCHEMA:
            await _add_missing_columns()

        await insert_preset_providers()
        if settings.BOOTSTRAP_DEFAULTS:
            await ensure_default_agent()

        _db_initialized = True


def _import_all_models() -> None:
    """Register all SQLAlchemy models before metadata.create_all."""
    from src.cognition.models.advisor_session import AdvisorSession  # noqa: F401
    from src.cognition.models.belief_system import BeliefSystem  # noqa: F401
    from src.cognition.models.conflict_graph import ConflictGraphEdge  # noqa: F401
    from src.cognition.models.conflict_record import ConflictRecord  # noqa: F401
    from src.cognition.models.decision_record import DecisionRecord  # noqa: F401
    from src.cognition.models.decision_review import DecisionReview  # noqa: F401
    from src.cognition.models.persona_snapshot import PersonaSnapshot  # noqa: F401
    from src.cognition.models.knowledge_page import KnowledgePage, KnowledgePageMemory, KnowledgePageVersion  # noqa: F401
    from src.cognition.models.insight_proposal import InsightProposal  # noqa: F401
    from src.cognition.models.weekly_review import WeeklyReview  # noqa: F401
    from src.execution.models.agent_permission import AgentPermission  # noqa: F401
    from src.execution.models.agent_profile import AgentProfile  # noqa: F401
    from src.execution.models.agent_runtime import AgentHandoff, AgentRun, AgentSession, AgentStep  # noqa: F401
    from src.execution.models.conversation import (  # noqa: F401
        ConversationAttentionCandidate,
        ConversationEpisode,
        ConversationReflectionCursor,
        ConversationTurn,
    )
    from src.execution.models.memory_work import MemoryWorkCase, MemoryWorkDecision, MemoryWorkEvidence  # noqa: F401
    from src.execution.models.memory_operations import EvidenceSeal, MemoryMaintenanceAction, MemoryMaintenanceControl, MemoryMaintenanceRun, UserMemoryBrief  # noqa: F401
    from src.execution.models.audit_log import AuditLog  # noqa: F401
    from src.execution.models.custom_llm_provider import CustomLLMProvider  # noqa: F401
    from src.execution.models.life_task import LifeTask  # noqa: F401
    from src.execution.models.life_timeline_entry import LifeTimelineEntry  # noqa: F401
    from src.execution.models.memory_relation import MemoryRelation  # noqa: F401
    from src.execution.models.simulation_run import SimulationRun  # noqa: F401
    from src.execution.models.user import User  # noqa: F401
    from src.memory.models.committed_memory import CommittedMemory  # noqa: F401
    from src.memory.models.data_lifecycle_audit import DataLifecycleAudit  # noqa: F401
    from src.memory.models.graph_projection import GraphProjection, GraphReplayCheckpoint, GraphShadowObservation  # noqa: F401
    from src.memory.models.memory_embedding import MemoryEmbedding  # noqa: F401
    from src.memory.models.memory_state_transition import MemoryStateTransition  # noqa: F401
    from src.memory.models.memory_source import MemorySource  # noqa: F401
    from src.memory.models.obsidian_sync_record import ObsidianSyncRecord  # noqa: F401
    from src.memory.models.raw_event import RawEvent  # noqa: F401
    from src.platform.models.media_artifact import MediaArtifact  # noqa: F401
    from src.platform.models.wecom_contact import WeComContact  # noqa: F401

async def _add_missing_columns():
    """自动添加模型中定义但数据库中缺失的列"""
    from sqlalchemy import inspect, text
    
    def _add_columns_sync(conn):
        inspector = inspect(conn)
        
        # agent_profiles 表需要 api_token_hash 列
        if 'agent_profiles' in inspector.get_table_names():
            columns = [col['name'] for col in inspector.get_columns('agent_profiles')]
            if 'api_token_hash' not in columns:
                conn.execute(text("ALTER TABLE agent_profiles ADD COLUMN api_token_hash VARCHAR"))
                logger.info("Added api_token_hash column to agent_profiles")
            if 'is_default' not in columns:
                conn.execute(text("ALTER TABLE agent_profiles ADD COLUMN is_default BOOLEAN DEFAULT 0"))
                logger.info("Added is_default column to agent_profiles")
        
        # committed_memories 表需要 content_hash 列
        if 'committed_memories' in inspector.get_table_names():
            columns = [col['name'] for col in inspector.get_columns('committed_memories')]
            if 'content_hash' not in columns:
                conn.execute(text("ALTER TABLE committed_memories ADD COLUMN content_hash VARCHAR"))
                logger.info("Added content_hash column to committed_memories")
    
    async with engine.begin() as conn:
        await conn.run_sync(_add_columns_sync)

async def insert_preset_providers():
    pass

async def ensure_default_agent():
    from src.execution.models.agent_profile import AgentProfile, AgentType, RecallLevel, LLMProvider
    from src.shared.security.auth import hash_token
    from src.shared.ids.id_generator import generate_id
    from src.shared.security.dependencies import SOLO_USER_ID
    import secrets

    async with async_session() as session:
        result = await session.execute(
            select(AgentProfile).where(AgentProfile.user_id == SOLO_USER_ID)
        )
        agents = result.scalars().all()

        # Find existing default agent (by is_default flag or by name "记忆整理助手")
        default_agent = next((a for a in agents if a.is_default), None)
        if default_agent is None:
            default_agent = next((a for a in agents if a.agent_name == "记忆整理助手"), None)

        if default_agent is not None:
            # Mark it as default if not already
            if not default_agent.is_default:
                default_agent.is_default = True
            # Restore if it was deleted
            if not default_agent.status:
                default_agent.status = True
            await session.commit()
            logger.info(f"Default agent ensured: {default_agent.agent_name} (ID: {default_agent.id}, status={default_agent.status})")
            return

        if len(agents) == 0:
            # No agents at all — create default
            token = secrets.token_urlsafe(32)
            token_hash = hash_token(token)

            default_agent = AgentProfile(
                id=generate_id(),
                user_id=SOLO_USER_ID,
                agent_name="记忆整理助手",
                agent_type=AgentType.CUSTOM,
                default_recall_level=RecallLevel.WORK_CONTEXT,
                token_hash=token_hash,
                api_token_hash=token_hash,
                status=True,
                is_default=True,
                llm_provider=LLMProvider.DEEPSEEK,
                llm_model="deepseek-chat",
                llm_temperature=0.3,
                llm_max_tokens=8192,
                role="人生记忆管家",
                mission="负责管理和利用用户的个人记忆，帮助用户记录、整理、回忆重要信息",
                goals=[
                    "在每次对话开始时自动检索相关记忆作为上下文",
                    "在对话结束时自动保存关键信息到记忆系统",
                    "自动识别对话中的决策、事实、项目上下文等有价值的信息",
                    "根据记忆内容为用户提供有价值的建议和提醒",
                    "保护用户隐私，敏感信息需用户确认后再保存"
                ],
                constraints=[
                    "严格保护用户隐私，不得泄露任何记忆内容",
                    "保存记忆前必须确认信息的准确性和重要性",
                    "敏感信息需要用户明确确认后才能保存",
                    "不得编造或篡改记忆内容",
                    "记忆检索结果仅作为上下文参考，不作为绝对事实"
                ],
                instructions="作为 Aion Memory Nexus（永识中枢）的智能助手，你需要在对话中主动管理记忆：\n1. 对话开始时调用before-start接口获取相关记忆\n2. 对话过程中注意收集有价值的信息\n3. 对话结束时调用after-end接口保存关键信息\n4. 随时可以调用search接口搜索相关记忆辅助回答"
            )

            session.add(default_agent)
            await session.commit()
            logger.info(f"Default agent created: {default_agent.agent_name} (ID: {default_agent.id})")
            logger.warning("Default agent token generated. Retrieve it via the admin API; it is NOT logged here for security reasons.")
