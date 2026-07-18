"""Application entry point with lifespan management"""
import os
import time
import logging
import traceback
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException as FastAPIHTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.exceptions import RequestValidationError
from pathlib import Path
from datetime import datetime

# Logging configuration
logging.basicConfig(
    level=logging.INFO if os.getenv("ENVIRONMENT") != "production" else logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("life_memory")

from src.shared.db.database import init_db, async_session  # noqa: E402
from src.app.settings import get_cors_origins, get_system_api_token, settings  # noqa: E402
from src.app.wiring import register_routes  # noqa: E402
from src.shared.utils.runtime_metrics import runtime_metrics  # noqa: E402
from src.shared.version import get_product_version, check_compatibility  # noqa: E402

is_production = settings.ENVIRONMENT.lower() == "production"
is_testing = settings.TESTING


metrics = runtime_metrics


def _build_content_security_policy() -> str:
    if not is_production:
        return (
            "default-src 'self' 'unsafe-inline' 'unsafe-eval' data: blob: ws: wss:"
        )
    return (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "connect-src 'self'; "
        "img-src 'self' data:; "
        "font-src 'self' data:; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "frame-ancestors 'none'"
    )


def _extract_request_token(request: Request) -> str:
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        return auth_header.split(" ", 1)[1].strip()
    return request.headers.get("x-agent-token", "").strip()


def _is_within_directory(target: Path, base: Path) -> bool:
    try:
        target.relative_to(base)
        return True
    except ValueError:
        return False


def _validate_production_security() -> None:
    if not is_production:
        return
    if settings.SECRET_KEY == "your-secret-key-change-in-production":
        raise RuntimeError("SECRET_KEY must be changed in production")
    if settings.SOLO_MODE and not settings.ALLOW_SOLO_PRODUCTION:
        raise RuntimeError(
            "SOLO_MODE is not allowed in production unless ALLOW_SOLO_PRODUCTION=true"
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── startup ──────────────────────────────────────────────────────
    _validate_production_security()
    if not is_testing:
        await init_db()

    # 版本与兼容性检查（仅告警，不阻断启动）
    compat = check_compatibility()
    if not compat["compatible"]:
        logger.warning(f"Version compatibility warnings: {compat['warnings']}")

    if settings.ENABLE_SCHEDULER and not is_testing:
        from src.shared.db.scheduler import start_scheduler, update_scheduler_from_config
        if start_scheduler():
            update_scheduler_from_config()
    if settings.WECOM_BOT_ID and settings.WECOM_BOT_SECRET and not is_testing:
        from src.platform.channels.wecom_handlers import start_wecom_long_connection
        await start_wecom_long_connection()

    yield

    # ── shutdown ─────────────────────────────────────────────────────
    if settings.ENABLE_SCHEDULER and not is_testing:
        from src.shared.db.scheduler import stop_scheduler
        stop_scheduler()
    if settings.WECOM_BOT_ID and settings.WECOM_BOT_SECRET and not is_testing:
        from src.platform.channels.wecom_handlers import stop_wecom_long_connection
        await stop_wecom_long_connection()
    # 释放复用的 httpx 连接（LLM/embedding provider 单例）
    try:
        from src.shared.llm.providers import close_all_providers
        await close_all_providers()
    except Exception as e:
        logger.warning(f"close_all_providers failed during shutdown: {e}")


def create_app() -> FastAPI:
    from slowapi import _rate_limit_exceeded_handler
    from slowapi.errors import RateLimitExceeded
    from src.shared.security.rate_limit import limiter

    app = FastAPI(
        title="Aion Memory Nexus",
        version=get_product_version(),
        docs_url=None if is_production else "/docs",
        redoc_url=None if is_production else "/redoc",
        openapi_url=None if is_production else "/openapi.json",
        lifespan=lifespan,
    )
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_cors_origins(),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Agent-Token", "X-Request-ID"],
    )

    # 请求级链路追踪（注入 X-Request-ID，传播到结构化日志）
    from src.shared.utils.request_id import RequestIDMiddleware
    app.add_middleware(RequestIDMiddleware)

    # 全局异常处理器：防止未捕获异常泄露堆栈给客户端
    @app.exception_handler(FastAPIHTTPException)
    async def http_exception_handler(request: Request, exc: FastAPIHTTPException):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={"detail": "Validation Error", "errors": exc.errors()},
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        logger.error(
            f"Unhandled exception on {request.method} {request.url.path}: {exc}\n{traceback.format_exc()}"
        )
        if is_production:
            return JSONResponse(status_code=500, content={"detail": "Internal Server Error"})
        return JSONResponse(
            status_code=500,
            content={"detail": str(exc), "type": type(exc).__name__},
        )

    return app


app = create_app()


@app.middleware("http")
async def add_security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = _build_content_security_policy()
    return response


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    """记录每个请求的耗时与错误状态，供 /metrics 端点暴露。"""
    start = time.perf_counter()
    error = False
    try:
        response = await call_next(request)
        if response.status_code >= 500:
            error = True
        return response
    except Exception:
        error = True
        raise
    finally:
        duration = time.perf_counter() - start
        metrics.record_request(duration, error)


static_dir = Path(__file__).parent.parent.parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir), check_dir=True), name="static")


@app.get("/")
async def root():
    if (static_dir / "index.html").exists():
        index_path = str(static_dir / "index.html")
        last_modified = datetime.fromtimestamp(Path(index_path).stat().st_mtime)
        return FileResponse(
            index_path,
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
                "Last-Modified": last_modified.strftime("%a, %d %b %Y %H:%M:%S GMT"),
            }
        )
    return {"message": "Aion Memory Nexus API", "version": get_product_version()}


@app.get("/health")
async def health_check():
    """Health check endpoint for container orchestration and load balancer probing.

    检查项：
    - DB（SELECT 1）— 决定整体 status 是否 healthy
    - Redis（如配置了 REDIS_URL 则尝试 ping；不配置返回 not_configured）
    - LLM provider（仅判断是否配置了 API key，不实际调用以避免成本）
    - Scheduler（apscheduler 是否 running）
    """
    from sqlalchemy import text

    # ── DB ───────────────────────────────────────────────────────────
    db_status = "disconnected"
    try:
        async with async_session() as session:
            await session.execute(text("SELECT 1"))
        db_status = "connected"
    except Exception as e:
        logger.warning(f"health check: DB ping failed: {e}")

    # ── Redis ────────────────────────────────────────────────────────
    redis_status = "not_configured"
    if settings.REDIS_URL:
        try:
            import redis.asyncio as aioredis
            client = aioredis.from_url(settings.REDIS_URL, socket_connect_timeout=2)
            try:
                await client.ping()
                redis_status = "connected"
            finally:
                await client.aclose()
        except Exception as e:
            logger.warning(f"health check: Redis ping failed: {e}")
            redis_status = "disconnected"

    # ── LLM（仅检查是否配置 API key，不实际调用以避免成本）────────────
    llm_status = "not_configured"
    try:
        if settings.DEEPSEEK_API_KEY:
            llm_status = "configured"
        else:
            # 兜底：查询数据库中是否有启用的自定义 provider
            from src.shared.db.database import get_db_sync
            from src.execution.models.custom_llm_provider import CustomLLMProvider
            from sqlalchemy import select as _select
            try:
                db = next(get_db_sync())
                result = db.execute(_select(CustomLLMProvider).where(CustomLLMProvider.status == True).limit(1))  # noqa: E712
                if result.scalar_one_or_none() is not None:
                    llm_status = "configured"
            except Exception:
                pass
    except Exception as e:
        logger.warning(f"health check: LLM config check failed: {e}")

    # ── Scheduler ────────────────────────────────────────────────────
    scheduler_status = "stopped"
    try:
        from src.shared.db.scheduler import scheduler as _scheduler
        if getattr(_scheduler, "running", False):
            scheduler_status = "running"
    except Exception as e:
        logger.warning(f"health check: scheduler state check failed: {e}")

    # ── WeCom / vector capability / migrations ──────────────────────
    # These are local capability checks only: health probes must not initiate
    # an external bot connection, send a message, or expose endpoint details.
    wecom_status = "not_configured"
    try:
        from src.platform.channels.wecom import get_wecom_bot
        bot = get_wecom_bot()
        if bot:
            wecom_status = "connected" if bot.is_connected() else "disconnected"
    except Exception:
        wecom_status = "unknown"

    try:
        from src.shared.db.vector_store import vector_store_mode
        vector_status = vector_store_mode()
    except Exception:
        vector_status = "unknown"

    migration_status = "not_initialized"
    try:
        async with async_session() as session:
            revision = await session.scalar(text("SELECT version_num FROM alembic_version LIMIT 1"))
        if revision:
            migration_status = str(revision)
    except Exception:
        # CREATE_SCHEMA_ON_STARTUP environments can intentionally have no
        # Alembic version table; do not convert this informational component
        # into a core availability failure.
        migration_status = "not_initialized"

    # status 为 healthy 当且仅当 database=connected（其他降级不影响核心功能）
    overall = "healthy" if db_status == "connected" else "degraded"

    return {
        "status": overall,
        "environment": settings.ENVIRONMENT,
        "components": {
            "database": db_status,
            "redis": redis_status,
            "llm": llm_status,
            "scheduler": scheduler_status,
            "wecom": wecom_status,
            "vector_store": vector_status,
            "migrations": migration_status,
        },
        "version": get_product_version(),
    }


@app.get("/metrics")
async def metrics_endpoint(request: Request):
    """Prometheus 文本格式指标暴露端点。"""
    if is_production or settings.METRICS_REQUIRE_TOKEN:
        if _extract_request_token(request) != get_system_api_token():
            raise FastAPIHTTPException(status_code=401, detail="Metrics token required")
    return PlainTextResponse(
        metrics.format_prometheus(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


# Register all routes
register_routes(app)


# SPA fallback - serve static files or index.html for all non-API routes
@app.get("/{path:path}")
async def serve_spa(path: str):
    # Skip API routes
    if path.startswith("api/"):
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    # Try to serve static file from static_dir (e.g. /assets/*.js, /favicon.svg)
    if static_dir.exists():
        static_root = static_dir.resolve()
        file_path = (static_root / path).resolve()
        if _is_within_directory(file_path, static_root) and file_path.is_file():
            return FileResponse(str(file_path))
    # SPA fallback to index.html
    if (static_dir / "index.html").exists():
        return FileResponse(str(static_dir / "index.html"))
    return {"message": "Aion Memory Nexus API", "version": get_product_version()}
