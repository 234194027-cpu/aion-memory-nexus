#!/usr/bin/env python3
"""Generate MCP client config snippets for the Life Memory portable proxy."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


TOKEN_PLACEHOLDER = "<set-agent-token-here>"


def build_config(api_url: str, agent_id: str, token_value: str, server_path: Path) -> dict:
    return {
        "mcpServers": {
            "life-memory": {
                "command": "python",
                "args": [str(server_path)],
                "env": {
                    "LIFE_MEMORY_API_URL": api_url.rstrip("/"),
                    "LIFE_MEMORY_AGENT_ID": agent_id,
                    "LIFE_MEMORY_AGENT_TOKEN": token_value,
                },
            }
        }
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a Life Memory MCP config snippet.")
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--token-env", default="LIFE_MEMORY_AGENT_TOKEN")
    parser.add_argument(
        "--include-token-from-env",
        action="store_true",
        help="Inline the token from --token-env. Use only when writing to a private local config.",
    )
    parser.add_argument("--client", choices=["claude", "cursor", "windsurf", "generic"], default="generic")
    parser.add_argument("--server-path", default=str(Path(__file__).with_name("life_memory_mcp_server.py")))
    parser.add_argument("--output")
    args = parser.parse_args()

    token_value = TOKEN_PLACEHOLDER
    if args.include_token_from_env:
        token_value = os.environ.get(args.token_env, "")
        if not token_value:
            raise SystemExit(f"Missing token env var: {args.token_env}")

    config = build_config(args.api_url, args.agent_id, token_value, Path(args.server_path).resolve())
    text = json.dumps(config, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
        print(f"Wrote {args.client} MCP config to {args.output}")
    else:
        print(text)
        print()
        if args.include_token_from_env:
            print("Token was inlined from the local environment. Keep this config private.")
        else:
            print(f"Replace {TOKEN_PLACEHOLDER} with the agent token or use the target client's secret store.")


if __name__ == "__main__":
    main()
