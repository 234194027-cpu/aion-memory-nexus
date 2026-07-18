"""Windows-only development launcher compatibility helpers."""
from __future__ import annotations

import asyncio
import sys


def configure_windows_psycopg_event_loop() -> bool:
    """Select the loop supported by psycopg before uvicorn creates an event loop."""
    policy_type = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
    if sys.platform != "win32" or policy_type is None:
        return False
    asyncio.set_event_loop_policy(policy_type())
    return True
