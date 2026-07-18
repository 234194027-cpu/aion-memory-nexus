#!/usr/bin/env python3
"""Diagnose Life Memory MCP connectivity and common client setup issues."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path


# Never embed an operator endpoint in a distributable Skill package.
DEFAULT_API_URL = os.environ.get("LIFE_MEMORY_API_URL", "").strip()


def _check_api(api_url: str) -> dict:
    try:
        with urllib.request.urlopen(api_url.rstrip("/") + "/docs", timeout=10) as response:
            return {"ok": response.status < 500, "status": response.status}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _run_smoke(args) -> dict:
    token = os.environ.get(args.token_env)
    if not args.agent_id or not token:
        return {"ok": False, "skipped": True, "reason": "missing agent id or token env"}
    script = Path(__file__).with_name("smoke_test_mcp.py")
    cmd = [
        sys.executable,
        str(script),
        "--api-url",
        args.api_url,
        "--agent-id",
        args.agent_id,
        "--token-env",
        args.token_env,
        "--project-id",
        args.project_id,
    ]
    completed = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if completed.returncode != 0:
        return {"ok": False, "stdout": completed.stdout, "stderr": completed.stderr}
    return {"ok": True, "summary": json.loads(completed.stdout)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose Life Memory MCP setup.")
    parser.add_argument("--api-url", default=os.environ.get("LIFE_MEMORY_API_URL", DEFAULT_API_URL))
    parser.add_argument("--agent-id", default=os.environ.get("LIFE_MEMORY_AGENT_ID", ""))
    parser.add_argument("--token-env", default="LIFE_MEMORY_AGENT_TOKEN")
    parser.add_argument("--project-id", default="life-memory-system")
    args = parser.parse_args()
    if not args.api_url:
        parser.error("--api-url is required (or set LIFE_MEMORY_API_URL privately)")

    server_path = Path(__file__).with_name("life_memory_mcp_server.py")
    checks = {
        "python": {"ok": True, "executable": sys.executable, "version": sys.version.split()[0]},
        "api": _check_api(args.api_url),
        "env": {
            "has_agent_id": bool(args.agent_id),
            "has_token_env": bool(os.environ.get(args.token_env)),
            "token_env": args.token_env,
        },
        "files": {
            "server_exists": server_path.exists(),
            "smoke_test_exists": Path(__file__).with_name("smoke_test_mcp.py").exists(),
            "config_writer_exists": Path(__file__).with_name("write_codex_mcp_config.py").exists(),
        },
    }
    checks["smoke"] = _run_smoke(args)
    print(json.dumps(checks, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
