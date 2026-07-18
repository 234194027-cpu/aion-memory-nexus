#!/usr/bin/env python3
"""Bootstrap a Life Memory MCP agent without manually copying credentials."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from configure_mcp import build_config


# Public Skill packages must not disclose a deployment endpoint. Operators pass
# their private endpoint explicitly or provide it through the process env.
DEFAULT_API_URL = os.environ.get("LIFE_MEMORY_API_URL", "").strip()


def post_json(url: str, payload: dict) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def create_agent(api_url: str, agent_name: str, project_id: str) -> dict:
    """Use the restricted V3 bootstrap endpoint, never the admin API."""
    return post_json(
        f"{api_url.rstrip('/')}/api/agent/public-bootstrap",
        {
            "agent_name": agent_name,
            "project_id": project_id,
        },
    )


def run_smoke(api_url: str, agent_id: str, token: str, project_id: str, write_test: bool) -> dict:
    script_dir = Path(__file__).resolve().parent
    smoke_script = script_dir / "smoke_test_mcp.py"
    env = os.environ.copy()
    env["LIFE_MEMORY_BOOTSTRAP_TOKEN"] = token
    cmd = [
        sys.executable,
        str(smoke_script),
        "--api-url",
        api_url.rstrip("/"),
        "--agent-id",
        agent_id,
        "--token-env",
        "LIFE_MEMORY_BOOTSTRAP_TOKEN",
        "--project-id",
        project_id,
    ]
    if write_test:
        cmd.append("--write-test")
    completed = subprocess.run(cmd, check=True, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    return json.loads(completed.stdout)


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a Life Memory agent and verify MCP automatically.")
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    parser.add_argument("--agent-name", default=f"auto-mcp-agent-{int(time.time())}")
    parser.add_argument("--project-id", default="life-memory-system")
    parser.add_argument("--client", choices=["claude", "cursor", "windsurf", "generic"], default="generic")
    parser.add_argument("--config-output", help="Optional private MCP config file to write with the generated token.")
    parser.add_argument("--write-test", action="store_true", help="Run a low-risk RawEvent write/search roundtrip.")
    parser.add_argument(
        "--show-token",
        action="store_true",
        help="Print the one-time token in stdout. Use only inside a private agent-to-agent handoff.",
    )
    args = parser.parse_args()
    if not args.api_url:
        parser.error("--api-url is required (or set LIFE_MEMORY_API_URL privately)")

    agent = create_agent(args.api_url, args.agent_name, args.project_id)
    agent_id = agent["agent_id"]
    token = agent["api_token"]
    smoke = run_smoke(args.api_url, agent_id, token, args.project_id, args.write_test)

    config_path = None
    if args.config_output:
        server_path = Path(__file__).with_name("life_memory_mcp_server.py").resolve()
        config = build_config(args.api_url, agent_id, token, server_path)
        Path(args.config_output).write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        config_path = str(Path(args.config_output).resolve())

    summary = {
        "api_url": args.api_url.rstrip("/"),
        "agent_id": agent_id,
        "token": token if args.show_token else "<created-not-printed>",
        "token_handling": (
            "printed because --show-token was set"
            if args.show_token
            else "kept in process memory; use --config-output to write a private MCP config"
        ),
        "config_output": config_path,
        "smoke": smoke,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
