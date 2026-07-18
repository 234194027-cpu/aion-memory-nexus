"""Backward-compatible MCP entry point.

Use either:
  python -m src.mcp_server
  python -m src.platform.mcp.server
"""

from src.platform.mcp.server import main


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
