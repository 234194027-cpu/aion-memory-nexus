"""Rotate SECRET_KEY-protected database fields without logging secret values."""
from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
import sys

# Docker invokes this file by path, which otherwise places only /app/scripts
# on sys.path and prevents imports from the application package.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import select

from src.execution.models.agent_profile import AgentProfile
from src.execution.models.custom_llm_provider import CustomLLMProvider
from src.shared.config import settings
from src.shared.db.database import async_session
from src.shared.security.key_rotation import rotate_header_values, rotate_secret_value


async def rotate(*, new_secret: str, apply: bool) -> dict[str, int]:
    if new_secret == settings.SECRET_KEY:
        raise ValueError("new secret key must differ from the current secret key")
    stats = {"agent_api_keys": 0, "provider_api_keys": 0, "provider_headers": 0}
    async with async_session() as session:
        agents = (await session.execute(select(AgentProfile))).scalars().all()
        providers = (await session.execute(select(CustomLLMProvider))).scalars().all()

        for agent in agents:
            if agent.llm_api_key is not None:
                agent.llm_api_key = rotate_secret_value(
                    agent.llm_api_key, old_secret=settings.SECRET_KEY, new_secret=new_secret
                )
                stats["agent_api_keys"] += 1
        for provider in providers:
            if provider.api_key is not None:
                provider.api_key = rotate_secret_value(
                    provider.api_key, old_secret=settings.SECRET_KEY, new_secret=new_secret
                )
                stats["provider_api_keys"] += 1
            if provider.headers:
                provider.headers = rotate_header_values(
                    provider.headers, old_secret=settings.SECRET_KEY, new_secret=new_secret
                )
                stats["provider_headers"] += 1

        if apply:
            await session.commit()
        else:
            await session.rollback()
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="Rotate database values encrypted with SECRET_KEY.")
    parser.add_argument("--apply", action="store_true", help="Commit changes. Omit for dry-run.")
    args = parser.parse_args()
    new_secret = os.environ.get("NEW_SECRET_KEY", "")
    if not new_secret:
        raise SystemExit("NEW_SECRET_KEY is required")
    stats = asyncio.run(rotate(new_secret=new_secret, apply=args.apply))
    mode = "applied" if args.apply else "dry_run"
    print(f"mode={mode} agent_api_keys={stats['agent_api_keys']} provider_api_keys={stats['provider_api_keys']} provider_headers={stats['provider_headers']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
