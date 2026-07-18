#!/usr/bin/env python3
"""Append a Life Memory MCP server block to Codex config.toml without PowerShell quoting."""

from __future__ import annotations

import argparse
import os
from pathlib import Path


# The API endpoint belongs in a private environment or client secret store.
DEFAULT_API_URL = os.environ.get("LIFE_MEMORY_API_URL", "").strip()
MARKER_START = "# BEGIN life-memory-mcp-connect"
MARKER_END = "# END life-memory-mcp-connect"
LIFE_MEMORY_TABLES = {
    "[mcp_servers.life-memory]",
    "[mcp_servers.life-memory.env]",
    "[mcp_servers.'life-memory']",
    "[mcp_servers.'life-memory'.env]",
    '[mcp_servers."life-memory"]',
    '[mcp_servers."life-memory".env]',
}


def _toml_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _block(api_url: str, agent_id: str, token: str, server_path: Path) -> str:
    return "\n".join(
        [
            MARKER_START,
            "[mcp_servers.life-memory]",
            'command = "python"',
            f"args = [{_toml_string(str(server_path))}]",
            "[mcp_servers.life-memory.env]",
            f"LIFE_MEMORY_API_URL = {_toml_string(api_url.rstrip('/'))}",
            f"LIFE_MEMORY_AGENT_ID = {_toml_string(agent_id)}",
            f"LIFE_MEMORY_AGENT_TOKEN = {_toml_string(token)}",
            MARKER_END,
            "",
        ]
    )


def _redact_block(block: str) -> str:
    lines = []
    for line in block.splitlines():
        if line.startswith("LIFE_MEMORY_AGENT_TOKEN"):
            lines.append('LIFE_MEMORY_AGENT_TOKEN = "<redacted>"')
        else:
            lines.append(line)
    return "\n".join(lines) + "\n"


def _remove_marked_block(text: str) -> str:
    start = text.find(MARKER_START)
    end = text.find(MARKER_END)
    if start == -1 or end == -1 or end < start:
        return text
    end += len(MARKER_END)
    return text[:start].rstrip() + "\n\n" + text[end:].lstrip("\r\n")


def _strip_existing_life_memory_tables(text: str) -> str:
    kept: list[str] = []
    skipping = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped in LIFE_MEMORY_TABLES:
            skipping = True
            continue
        if skipping and stripped.startswith("[") and stripped.endswith("]"):
            skipping = False
        if not skipping:
            kept.append(line)
    return "\n".join(kept).rstrip()


def _upsert_block(text: str, block: str) -> str:
    text = _remove_marked_block(text)
    text = _strip_existing_life_memory_tables(text)
    return text.rstrip() + "\n\n" + block


def main() -> None:
    parser = argparse.ArgumentParser(description="Safely write Life Memory MCP config into Codex config.toml.")
    parser.add_argument("--config-path", default=str(Path.home() / ".codex" / "config.toml"))
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--token")
    parser.add_argument("--token-env", default="LIFE_MEMORY_AGENT_TOKEN")
    parser.add_argument("--server-path", default=str(Path(__file__).with_name("life_memory_mcp_server.py")))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not args.api_url:
        parser.error("--api-url is required (or set LIFE_MEMORY_API_URL privately)")

    token = args.token if args.token is not None else os.environ.get(args.token_env)
    if not token:
        raise SystemExit(f"Missing token. Pass --token or set {args.token_env}.")

    config_path = Path(args.config_path).expanduser()
    server_path = Path(args.server_path).resolve()
    block = _block(args.api_url, args.agent_id, token, server_path)
    if args.dry_run:
        print(f"Would write Life Memory MCP config to {config_path}")
        print(_redact_block(block), end="")
        return

    old_text = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    new_text = _upsert_block(old_text, block)

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(new_text, encoding="utf-8")
    print(f"Wrote Life Memory MCP config to {config_path}")
    print("Restart or refresh Codex if memory_map does not appear in the current session.")


if __name__ == "__main__":
    main()
