from pathlib import Path
import secrets
import logging

from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

_runtime_system_api_token: str | None = None

class Settings(BaseSettings):
    ENVIRONMENT: str = "development"
    TESTING: bool = False
    POSTGRES_URL: str = "sqlite+aiosqlite:///./life_memory.db"
    REDIS_URL: str = "redis://localhost:6379/0"
    SECRET_KEY: str = "your-secret-key-change-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days
    CORS_ORIGINS: str = "http://127.0.0.1:8000,http://localhost:8000"
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_API_URL: str = "https://api.deepseek.com/v1"
    OBSIDIAN_VAULT_PATH: str = "./obsidian-vault"
    
    # 系统级 API Token：任何外部 Agent 都可以用这个 Token 接入系统
    SYSTEM_API_TOKEN: str = ""
    
    WECOM_BOT_ID: str = ""
    WECOM_BOT_SECRET: str = ""
    WECOM_DEFAULT_AGENT_ID: str = ""

    MEDIA_MAX_FILE_SIZE_BYTES: int = 25 * 1024 * 1024
    MEDIA_STORAGE_DIR: str = "./data/media-artifacts"
    AGENT_WORKSPACE_DIR: str = "./data/agent-workspaces"
    MEDIA_ALLOWED_MIME_TYPES: str = (
        "image/jpeg,image/png,image/webp,"
        "text/plain,text/markdown,text/html,text/csv,application/pdf,"
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,"
        "application/vnd.ms-excel,"
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document,"
        "application/vnd.openxmlformats-officedocument.presentationml.presentation,"
        "audio/mpeg,audio/wav,video/mp4"
    )
    MEDIA_ENABLE_MARKITDOWN: bool = False
    MEDIA_ENABLE_RAPIDOCR: bool = False
    MEDIA_ENABLE_WHISPER: bool = False
    MEDIA_ENABLE_FFMPEG: bool = False
    MEDIA_ENABLE_YTDLP: bool = False
    MEDIA_MAX_TRANSCRIBE_SECONDS: int = 600
    MEDIA_EXTRACTION_TIMEOUT_SECONDS: int = 120
    MEDIA_EXTRACTION_CONCURRENCY: int = 1

    ENABLE_SCHEDULER: bool = True
    BOOTSTRAP_DEFAULTS: bool = True
    AUTO_PATCH_SCHEMA: bool = False
    CREATE_SCHEMA_ON_STARTUP: bool = True
    SOLO_MODE: bool = True
    # Public bootstrap is intentionally opt-in. It is only a convenience for
    # a single-owner deployment and is not a replacement for tenant auth.
    PUBLIC_MCP_BOOTSTRAP_ENABLED: bool = False
    ALLOW_SOLO_PRODUCTION: bool = False
    ALLOW_DEV_AUTH_FALLBACK: bool = False
    METRICS_REQUIRE_TOKEN: bool = False

    # V2 is the only supported Agent runtime. The Working Agent can create a
    # formal memory only through the evidence-gated MemoryCommitService.
    AGENT_RUNTIME_ENABLED: bool = True
    CONVERSATIONAL_AGENT_ENABLED: bool = True
    WORKING_AGENT_SHADOW_ENABLED: bool = False
    WORKING_AGENT_ACTIVE_ENABLED: bool = True
    # Resource budgets are operational controls, not governance permissions.
    # Keep them configurable so production backlogs can drain without code edits.
    WORKING_AGENT_DAILY_MODEL_CALL_LIMIT: int = 96
    WORKING_AGENT_DAILY_PRIORITY_RESERVE: int = 32
    WORKING_AGENT_DAILY_MAINTENANCE_CALL_LIMIT: int = 8
    WORKING_AGENT_SCAN_BATCH_SIZE: int = 20
    # An explicit admin drain is isolated from the daily conversational budget.
    # The cap is measured in Working-Agent model batches, not raw events; an
    # ordinary batch can contain up to eight source events.
    WORKING_AGENT_FAST_DRAIN_MAX_BATCHES: int = 512
    WORKING_AGENT_BUDGET_TIMEZONE: str = "Asia/Shanghai"

    # V3 Graphiti/Neo4j is a disposable, internal-only projection.  It is
    # deliberately disabled until the deployment has passed Shadow replay.
    GRAPHITI_ENABLED: bool = False
    GRAPHITI_SHADOW_MODE: bool = True
    GRAPHITI_NEO4J_URI: str = "bolt://neo4j:7687"
    GRAPHITI_NEO4J_USER: str = "neo4j"
    GRAPHITI_NEO4J_PASSWORD: str = ""
    GRAPHITI_NEO4J_DATABASE: str = "neo4j"
    GRAPHITI_LLM_API_KEY: str = ""
    GRAPHITI_LLM_BASE_URL: str = ""
    GRAPHITI_LLM_MODEL: str = "deepseek-chat"
    GRAPHITI_EMBEDDING_MODEL: str = ""
    GRAPHITI_PROJECTION_CONCURRENCY: int = 1
    GRAPHITI_BACKFILL_BATCH_SIZE_MAX: int = 200
    # Graphiti 0.29 maps Neo4j group_id to a database.  Community Neo4j cannot
    # safely provide a database per tenant, so multi-tenant graph writes fail
    # closed until dedicated tenant isolation is provisioned.
    GRAPHITI_REQUIRE_SOLO_MODE: bool = True
    # Comma-separated user IDs permitted to run graph operations outside
    # solo mode. Empty is deliberately fail-closed.
    GRAPHITI_ADMIN_USER_IDS: str = ""

    # --- 新架构特性（第三轮迭代开启）---
    # LLM/embedding 结果缓存（OrderedDict LRU，各 1000 条）
    ENABLE_LLM_CACHE: bool = True
    LLM_CACHE_MAX_SIZE: int = 1000

    # Embedding 维度标准化：所有 embedding（API / BGE-M3 / fallback）统一到此维度。
    # 与 memory_embeddings.embedding_vector 列定义保持一致；不一致时零填充或截断。
    EMBEDDING_DIMENSION: int = 1024

    # Reranker 精排（LLM-based，参考 mem0）
    # 单用户本地系统可接受额外延迟，默认开启以提升检索质量
    ENABLE_RERANKER: bool = True
    RERANKER_TOP_K: int = 5
    RERANKER_MAX_CANDIDATES: int = 20

    # 混合检索模式：'fallback'（串行降级）| 'parallel'（三信号并行融合）
    # parallel 模式：vector(0.6) + BM25(0.3) + recency(0.1) 通过 asyncio.gather 并行计算
    HYBRID_SEARCH_MODE: str = "parallel"
    HYBRID_WEIGHT_VECTOR: float = 0.6
    HYBRID_WEIGHT_BM25: float = 0.3
    HYBRID_WEIGHT_RECENCY: float = 0.1

    # --- Zvec 向量索引（可选）---
    # 向量索引后端：'python'（Python 全量计算）| 'pgvector'（PostgreSQL pgvector）| 'zvec'（Zvec 本地索引）
    # 默认 'python'，保持现有行为，不强制启用 zvec
    VECTOR_INDEX_BACKEND: str = "python"
    # Zvec 索引文件存储路径
    ZVEC_INDEX_PATH: str = "./data/zvec/memory_embeddings"
    # Zvec collection 名称
    ZVEC_COLLECTION_NAME: str = "memory_embeddings"
    # 是否启用 Zvec 全文检索（第一阶段默认关闭）
    ZVEC_ENABLE_FTS: bool = False
    # 是否在启动时自动重建 Zvec 索引（默认关闭，避免生产启动时意外长时间重建）
    ZVEC_REBUILD_ON_STARTUP: bool = False
    # 查询候选乘数：先召回更多 id，再让数据库过滤
    ZVEC_QUERY_CANDIDATE_MULTIPLIER: int = 3

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    def model_post_init(self, __context):
        if self.ENVIRONMENT == "production" and self.SECRET_KEY == "your-secret-key-change-in-production":
            logger.warning(
                "SECRET_KEY is using default value in production! "
                "Please set a strong secret key."
            )

settings = Settings()

def get_system_api_token() -> str:
    """获取系统级 API Token，如果未配置则生成进程内临时值。"""
    global _runtime_system_api_token

    if settings.SYSTEM_API_TOKEN:
        return settings.SYSTEM_API_TOKEN
    if _runtime_system_api_token is None:
        _runtime_system_api_token = secrets.token_urlsafe(32)
    return _runtime_system_api_token

def _save_system_token_to_env(token: str):
    """兼容旧调用点；运行时不再写回 .env。"""
    global _runtime_system_api_token
    settings.SYSTEM_API_TOKEN = token
    _runtime_system_api_token = token

def get_cors_origins() -> list[str]:
    return [origin.strip() for origin in settings.CORS_ORIGINS.split(",") if origin.strip()]

BASE_DIR = Path(__file__).resolve().parent.parent.parent
OBSIDIAN_VAULT_DIR = BASE_DIR / settings.OBSIDIAN_VAULT_PATH
MEDIA_STORAGE_DIR = BASE_DIR / settings.MEDIA_STORAGE_DIR
AGENT_WORKSPACE_DIR = BASE_DIR / settings.AGENT_WORKSPACE_DIR
