"""Single source of truth for product version."""
import logging
import os
import pathlib
import re
from functools import lru_cache

logger = logging.getLogger(__name__)

# src/shared/version.py -> src/shared -> src -> <project_root>
# 与 src/shared/config.py 的 BASE_DIR 约定保持一致（3 个 .parent）
_VERSION_FILE = pathlib.Path(__file__).resolve().parent.parent.parent / "VERSION"

# 语义化版本格式：MAJOR.MINOR.PATCH（可选预发布后缀）
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+].+)?$")


@lru_cache(maxsize=1)
def get_product_version() -> str:
    """Read product version from VERSION file. Falls back to '0.0.0' if missing."""
    try:
        return _VERSION_FILE.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return "0.0.0"


def get_build_commit() -> str:
    """Get build commit from env var (injected by Dockerfile ARG)."""
    return os.environ.get("BUILD_COMMIT", "unknown")


def get_build_time() -> str:
    """Get build time from env var (injected by Dockerfile ARG)."""
    return os.environ.get("BUILD_TIME", "unknown")


def get_runtime_profiles() -> list[str]:
    """Return enabled runtime profiles without exposing provider configuration."""
    from src.shared.config import settings

    if not settings.AGENT_RUNTIME_ENABLED:
        return ["disabled"]
    profiles: list[str] = []
    if settings.CONVERSATIONAL_AGENT_ENABLED:
        profiles.append("conversational")
    if settings.WORKING_AGENT_ACTIVE_ENABLED:
        profiles.append("working-active")
    elif settings.WORKING_AGENT_SHADOW_ENABLED:
        profiles.append("working-shadow")
    return profiles or ["disabled"]


def check_compatibility(expected_frontend_version: str | None = None) -> dict:
    """Check version compatibility at startup.

    返回 ``{"compatible": bool, "warnings": list[str]}``。
    仅产生告警，不阻断启动。检查项：
      - VERSION 文件可读且符合 semver 格式
      - 前端版本（若提供）与后端一致
      - runtime_profiles 是否被完整禁用

    Schema revision 检查由 /health 端点负责（需要异步 DB 访问），
    本函数仅做同步检查，避免在 lifespan 中引入异步依赖。
    """
    warnings: list[str] = []
    backend_version = get_product_version()

    # 1. VERSION 文件格式检查
    if backend_version == "0.0.0":
        warnings.append(
            "VERSION file not found or empty; falling back to '0.0.0'. "
            "Create a VERSION file at the project root."
        )
    elif not _SEMVER_RE.match(backend_version):
        warnings.append(
            f"Product version '{backend_version}' does not follow semver format "
            "(MAJOR.MINOR.PATCH). Some features may behave unexpectedly."
        )

    # 2. 前端版本一致性检查（若提供）
    if expected_frontend_version is not None:
        if expected_frontend_version != backend_version:
            warnings.append(
                f"Frontend version '{expected_frontend_version}' does not match "
                f"backend version '{backend_version}'. "
                "Rebuild the frontend or update VERSION file."
            )

    # 3. Runtime profiles 检查
    profiles = get_runtime_profiles()
    if profiles == ["disabled"]:
        warnings.append(
            "Agent runtime is disabled; conversational and working Agent features "
            "are unavailable until the V2 runtime flags are enabled."
        )

    return {"compatible": len(warnings) == 0, "warnings": warnings}
