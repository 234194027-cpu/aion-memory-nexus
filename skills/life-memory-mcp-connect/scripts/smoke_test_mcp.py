#!/usr/bin/env python3
"""Smoke test the portable Life Memory MCP proxy without exposing tokens."""

from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any


def run_mcp(server_path: Path, env: dict[str, str], messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    proc = subprocess.Popen(
        [sys.executable, str(server_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    output_queue: queue.Queue[dict[str, Any]] = queue.Queue()
    error_queue: queue.Queue[str] = queue.Queue()
    responses: list[dict[str, Any]] = []

    def reader() -> None:
        assert proc.stdout is not None
        try:
            for line in proc.stdout:
                if line.strip():
                    output_queue.put(json.loads(line))
        except Exception as exc:
            error_queue.put(f"Failed to read MCP JSON output: {exc}")

    threading.Thread(target=reader, daemon=True).start()

    def send(message: dict[str, Any]) -> None:
        assert proc.stdin is not None
        proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        proc.stdin.flush()

    def wait_for_id(message_id: int) -> dict[str, Any]:
        deadline = time.time() + 90
        while time.time() < deadline:
            if proc.poll() is not None and output_queue.empty():
                stderr = proc.stderr.read() if proc.stderr else ""
                reader_error = error_queue.get_nowait() if not error_queue.empty() else ""
                raise RuntimeError(f"MCP server exited before response id={message_id}. {reader_error} {stderr}".strip())
            if not error_queue.empty():
                stderr = proc.stderr.read() if proc.stderr else ""
                raise RuntimeError(f"{error_queue.get()} {stderr}".strip())
            try:
                response = output_queue.get(timeout=1)
            except queue.Empty:
                continue
            responses.append(response)
            if response.get("id") == message_id:
                return response
        raise TimeoutError(f"Timed out waiting for MCP response id={message_id}")

    send(messages[0])
    wait_for_id(1)
    send(messages[1])
    for message in messages[2:]:
        send(message)
        wait_for_id(message["id"])

    assert proc.stdin is not None
    proc.stdin.close()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.terminate()
        proc.wait(timeout=10)
    if proc.returncode not in (0, None):
        stderr = proc.stderr.read() if proc.stderr else ""
        raise RuntimeError(f"MCP server failed: {stderr}")
    return responses


def tool_data(response: dict[str, Any]) -> dict[str, Any]:
    text = response["result"]["content"][0]["text"]
    return json.loads(text)


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke test Life Memory MCP.")
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--token-env", default="LIFE_MEMORY_AGENT_TOKEN")
    parser.add_argument("--project-id", default="life-memory-system")
    parser.add_argument("--source-name", default="life_memory_mcp_skill_smoke")
    parser.add_argument("--write-test", action="store_true")
    parser.add_argument("--server-path", default=str(Path(__file__).with_name("life_memory_mcp_server.py")))
    args = parser.parse_args()

    token = os.environ.get(args.token_env)
    if not token:
        raise SystemExit(f"Missing token env var: {args.token_env}")

    env = os.environ.copy()
    env.update(
        {
            "LIFE_MEMORY_API_URL": args.api_url.rstrip("/"),
            "LIFE_MEMORY_AGENT_ID": args.agent_id,
            "LIFE_MEMORY_AGENT_TOKEN": token,
        }
    )

    messages: list[dict[str, Any]] = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "smoke", "version": "1"}},
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "memory_map", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "memory_policy_status", "arguments": {}}},
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "memory_sync_status",
                "arguments": {"source_name": args.source_name, "project_id": args.project_id, "limit": 20},
            },
        },
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "memory_search",
                "arguments": {"query": "Life Memory MCP smoke test", "project_id": args.project_id, "top_k": 5},
            },
        },
    ]
    if args.write_test:
        messages.insert(
            4,
            {
                "jsonrpc": "2.0",
                "id": 6,
                "method": "tools/call",
                "params": {
                    "name": "memory_test_roundtrip",
                    "arguments": {
                        "project_id": args.project_id,
                        "source_name": args.source_name,
                        "content": "Life Memory MCP skill smoke test: portable proxy can write and search a low-risk fact.",
                        "metadata": {"test_case": "life_memory_mcp_skill_smoke"},
                    },
                },
            },
        )

    responses = run_mcp(Path(args.server_path).resolve(), env, messages)
    by_id = {response.get("id"): response for response in responses}
    tools = [tool["name"] for tool in by_id[2]["result"]["tools"]]
    access_map = tool_data(by_id[7])
    policy = tool_data(by_id[3])
    status = tool_data(by_id[4])
    search = tool_data(by_id[5])
    summary = {
        "tools_available": {
            "memory_map": "memory_map" in tools,
            "memory_sync_existing": "memory_sync_existing" in tools,
            "memory_upload_daily_delta": "memory_upload_daily_delta" in tools,
            "memory_sync_status": "memory_sync_status" in tools,
            "memory_test_roundtrip": "memory_test_roundtrip" in tools,
            "memory_list_types": "memory_list_types" in tools,
            "memory_create_link_artifact": "memory_create_link_artifact" in tools,
            "memory_upload_media_base64": "memory_upload_media_base64" in tools,
            "memory_list_media_artifacts": "memory_list_media_artifacts" in tools,
        },
        "access_map": {
            "version": access_map.get("version"),
            "has_recommended_flow": bool(access_map.get("recommended_flow")),
            "has_context_fields": bool(access_map.get("context_fields")),
            "has_daily_sync": any(
                item.get("tool") == "memory_upload_daily_delta"
                for item in access_map.get("recommended_flow", [])
                if isinstance(item, dict)
            ),
            "error": access_map.get("error"),
        },
        "policy": {
            "autonomous_memory_enabled": policy.get("autonomous_memory_enabled"),
            "token_leaked": token in json.dumps(policy, ensure_ascii=False),
            "error": policy.get("error"),
        },
        "status": {
            "raw_event_count": status.get("raw_event_count"),
            "work_case_count": status.get("work_case_count"),
            "committed_count": status.get("committed_count"),
            "duplicate_skipped_count": status.get("duplicate_skipped_count"),
            "processing_counts": status.get("processing_counts"),
            "error": status.get("error"),
        },
        "search": {
            "total_found": search.get("meta", {}).get("total_found"),
            "error": search.get("error"),
        },
    }
    if args.write_test and 6 in by_id:
        roundtrip = tool_data(by_id[6])
        summary["roundtrip"] = {
            "event_id": roundtrip.get("event", {}).get("event_id"),
            "created": roundtrip.get("event", {}).get("created"),
            "work_case_count": roundtrip.get("work_case_count"),
            "committed_count": roundtrip.get("committed_count"),
            "search_total": roundtrip.get("search", {}).get("total_found"),
            "error": roundtrip.get("error"),
        }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
