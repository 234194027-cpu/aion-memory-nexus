"""Admin system-level API: About endpoint.

返回稳定 schema，作为前后端、Agent SDK 与发布流程
对齐的权威信息源。所有字段均不包含敏感信息（密钥、绝对路径、主机名、
数据库 DSN、内部 Prompt 等）。
"""
from __future__ import annotations

import logging
import pathlib
from typing import Any

import yaml
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.settings import settings
from src.shared.db.database import async_session
from src.shared.security.dependencies import get_current_user
from src.shared.version import (
    get_build_commit,
    get_build_time,
    get_product_version,
    get_runtime_profiles,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# 项目根目录：src/platform/api/admin/system.py -> admin -> api -> platform -> src -> <root>
_PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent.parent.parent
_RELEASES_DIR = _PROJECT_ROOT / "docs" / "releases"


def _read_release_notes_metadata(version: str) -> dict[str, Any] | None:
    """读取 docs/releases/<version>.md 的 metadata 头。

    支持的 frontmatter 字段：version、date、title、summary、highlights[]。
    若文件不存在或解析失败，返回 None（不抛异常，About 端点仍可正常返回）。
    """
    path = _RELEASES_DIR / f"{version}.md"
    if not path.exists():
        return None
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("about: failed to read %s: %s", path.name, exc)
        return None

    # 解析 YAML frontmatter（--- 包裹的头部）
    if not content.startswith("---"):
        return None
    parts = content.split("---", 2)
    if len(parts) < 3:
        return None
    try:
        data = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError as exc:
        logger.warning("about: failed to parse YAML frontmatter in %s: %s", path.name, exc)
        return None

    if not isinstance(data, dict):
        return None

    # 仅返回白名单字段，避免泄露未受控内容
    return {
        "version": str(data.get("version", version)),
        "date": str(data.get("date", "")) if data.get("date") else None,
        "title": str(data.get("title", "")) if data.get("title") else None,
        "summary": str(data.get("summary", "")) if data.get("summary") else None,
        "highlights": list(data.get("highlights") or []) if isinstance(data.get("highlights"), list) else [],
    }


async def _read_schema_revision() -> str | None:
    """从 alembic_version 表读取当前 schema revision。失败返回 None。"""
    try:
        async with async_session() as session:
            revision = await session.scalar(text("SELECT version_num FROM alembic_version LIMIT 1"))
        return str(revision) if revision else None
    except Exception as exc:
        logger.warning("about: failed to read alembic_version: %s", exc)
        return None


@router.get("/about")
async def get_about(user=Depends(get_current_user)):
    """返回系统 About 信息。

    返回字段（共 9 个）：
      - product_name: 产品名
      - product_version: 产品版本（来自 VERSION 文件）
      - api_version: API 主版本号（v1）
      - schema_revision: 数据库 schema revision（alembic_version.version_num）
      - build_commit: 构建对应的 git commit（由 Dockerfile ARG 注入）
      - built_at: 构建时间（由 Dockerfile ARG 注入）
      - environment: 部署环境标识
      - runtime_profiles: 激活的运行时 profile 列表
      - release_notes: 当前版本发布说明 metadata（可能为 null）

    安全约束：
      - 不返回绝对路径、主机名、Git remote、密钥、数据库 DSN 或内部 Prompt
      - 所有字段均可安全暴露给前端 / Agent SDK
    """
    version = get_product_version()
    return {
        "product_name": "Aion Memory Nexus · 永识中枢",
        "product_version": version,
        "api_version": "v1",
        "schema_revision": await _read_schema_revision(),
        "build_commit": get_build_commit(),
        "built_at": get_build_time(),
        "environment": settings.ENVIRONMENT,
        "runtime_profiles": get_runtime_profiles(),
        "release_notes": _read_release_notes_metadata(version),
    }
