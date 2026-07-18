#!/usr/bin/env python3
"""Portable stdio MCP proxy for the Life Memory System.

Environment:
  LIFE_MEMORY_API_URL
  LIFE_MEMORY_AGENT_ID
  LIFE_MEMORY_AGENT_TOKEN
"""

from __future__ import annotations

import json
import os
import hashlib
import re
import sys
from copy import deepcopy
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


API_URL = os.environ.get("LIFE_MEMORY_API_URL", "http://127.0.0.1:8000").rstrip("/")
AGENT_ID = os.environ.get("LIFE_MEMORY_AGENT_ID", "")
AGENT_TOKEN = os.environ.get("LIFE_MEMORY_AGENT_TOKEN", "")
MOJIBAKE_MARKERS = ("Ã", "Â", "�")
IDENTIFIER_FIELDS = ("project_id", "repo_id", "workspace_id", "external_id")


def schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": True,
    }


def _looks_risky_identifier(value: Any) -> bool:
    if value is None:
        return False
    text = str(value)
    if any(marker in text for marker in MOJIBAKE_MARKERS):
        return True
    if "\\" in text or "/" in text:
        return True
    if re.search(r"^[A-Za-z]:", text):
        return True
    try:
        text.encode("ascii")
    except UnicodeEncodeError:
        return True
    return False


def _ascii_slug(value: Any, prefix: str) -> str:
    text = str(value or "").strip()
    ascii_text = text.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9_.-]+", "-", ascii_text).strip("-._")
    if not slug:
        digest = hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:12]
        slug = f"{prefix}-{digest}"
    return slug[:128]


def _clean_memory_sync_args(name: str, args: dict[str, Any]) -> dict[str, Any]:
    cleaned_args = dict(args)
    warnings: list[dict[str, Any]] = []
    for field in ("source_name", "default_project_id"):
        value = cleaned_args.get(field)
        if _looks_risky_identifier(value):
            cleaned_args[field] = _ascii_slug(value, field)
            warnings.append({"field": field, "reason": "sanitized_risky_identifier", "sanitized": cleaned_args[field]})

    metadata_suffix = {}
    if name == "memory_upload_daily_delta":
        metadata_suffix["daily_delta_since"] = args.get("since")

    memories = []
    for index, item in enumerate(args.get("memories", [])):
        item_copy = dict(item)
        item_metadata = dict(item_copy.get("metadata") or {})
        item_metadata.update(metadata_suffix)
        for field in IDENTIFIER_FIELDS:
            value = item_copy.get(field)
            if _looks_risky_identifier(value):
                item_metadata[f"original_{field}"] = str(value)
                item_copy[field] = _ascii_slug(value, field)
                warnings.append(
                    {
                        "index": index,
                        "field": field,
                        "reason": "sanitized_risky_identifier",
                        "sanitized": item_copy[field],
                    }
                )
        if not item_copy.get("external_id"):
            content = str(item_copy.get("content") or "")
            digest = hashlib.sha1(content.encode("utf-8", "replace")).hexdigest()[:16]
            item_copy["external_id"] = f"memory-{digest}"
            warnings.append({"index": index, "field": "external_id", "reason": "generated_missing_external_id"})
        item_copy["metadata"] = item_metadata
        memories.append(item_copy)

    cleaned_args["memories"] = memories
    cleaned_args["_validation_warnings"] = warnings
    return cleaned_args


TOOLS = [
    {
        "name": "memory_search",
        "description": "Search relevant committed memories for the authenticated agent.",
        "inputSchema": schema(
            {
                "query": {"type": "string"},
                "project_id": {"type": "string"},
                "recall_level": {"type": "string", "default": "work_context"},
                "top_k": {"type": "integer", "default": 10},
            },
            ["query"],
        ),
    },
    {
        "name": "memory_before_start",
        "description": "Retrieve a context pack before starting work.",
        "inputSchema": schema(
            {
                "task": {"type": "string"},
                "project_id": {"type": "string"},
                "repo_id": {"type": "string"},
                "recall_level": {"type": "string", "default": "work_context"},
                "top_k": {"type": "integer", "default": 8},
            },
            ["task"],
        ),
    },
    {
        "name": "memory_after_end",
        "description": "Upload an end-of-session summary as RawEvent inputs.",
        "inputSchema": schema(
            {
                "summary": {"type": "string"},
                "decisions": {"type": "array", "items": {"type": "string"}, "default": []},
                "actions": {"type": "array", "items": {"type": "string"}, "default": []},
                "artifacts": {"type": "array", "items": {"type": "string"}, "default": []},
                "project_id": {"type": "string"},
                "repo_id": {"type": "string"},
                "workspace_id": {"type": "string"},
            },
            ["summary"],
        ),
    },
    {
        "name": "memory_upload_event",
        "description": "Upload one RawEvent for server-side extraction.",
        "inputSchema": schema(
            {
                "content": {"type": "string"},
                "source_type": {"type": "string", "default": "mcp"},
                "project_id": {"type": "string"},
                "repo_id": {"type": "string"},
                "workspace_id": {"type": "string"},
                "metadata": {"type": "object", "default": {}},
                "dedupe": {"type": "boolean", "default": True},
            },
            ["content"],
        ),
    },
    {
        "name": "memory_sync_existing",
        "description": "Import an external agent's existing memory store as idempotent RawEvents.",
        "inputSchema": schema(
            {
                "source_name": {"type": "string", "default": "agent_memory"},
                "default_project_id": {"type": "string"},
                "memories": {"type": "array", "items": {"type": "object"}},
                "dedupe": {"type": "boolean", "default": True},
                "trigger_extraction": {"type": "boolean", "default": False},
            },
            ["memories"],
        ),
    },
    {
        "name": "memory_upload_daily_delta",
        "description": "Upload a daily batch of new or changed external memories.",
        "inputSchema": schema(
            {
                "source_name": {"type": "string", "default": "daily_agent_memory"},
                "since": {"type": "string"},
                "default_project_id": {"type": "string"},
                "memories": {"type": "array", "items": {"type": "object"}},
            },
            ["memories"],
        ),
    },
    {
        "name": "memory_sync_status",
        "description": "Inspect recent RawEvent, work-case, and committed-memory sync status.",
        "inputSchema": schema(
            {
                "source_name": {"type": "string"},
                "project_id": {"type": "string"},
                "limit": {"type": "integer", "default": 50},
            }
        ),
    },
    {
        "name": "memory_policy_status",
        "description": "Inspect the authenticated agent's write policy without exposing tokens.",
        "inputSchema": schema({}),
    },
    {
        "name": "memory_map",
        "description": "Return the Life Memory access map: recommended tool order, context fields, sync strategy, and write rules for agents.",
        "inputSchema": schema({}),
    },
    {
        "name": "memory_test_roundtrip",
        "description": "Run a low-risk RawEvent -> extraction -> search roundtrip.",
        "inputSchema": schema(
            {
                "content": {"type": "string"},
                "project_id": {"type": "string", "default": "life-memory-system"},
                "source_name": {"type": "string", "default": "agent_memory_bridge_roundtrip"},
                "metadata": {"type": "object", "default": {}},
                "recall_level": {"type": "string", "default": "work_context"},
                "top_k": {"type": "integer", "default": 5},
            }
        ),
    },
    {
        "name": "memory_create_link_artifact",
        "description": "Create a link MediaArtifact for asynchronous webpage extraction. Use this for URLs instead of uploading them as facts.",
        "inputSchema": schema(
            {
                "url": {"type": "string"},
                "source_text": {"type": "string"},
                "source_channel": {"type": "string", "default": "mcp"},
                "extract": {"type": "boolean", "default": True},
                "sync": {"type": "boolean", "default": False},
            },
            ["url"],
        ),
    },
    {
        "name": "memory_upload_media_base64",
        "description": "Upload a file, image, table, audio, or video as base64 media for asynchronous extraction.",
        "inputSchema": schema(
            {
                "filename": {"type": "string"},
                "content_base64": {"type": "string"},
                "mime_type": {"type": "string"},
                "media_type": {"type": "string"},
                "source_channel": {"type": "string", "default": "mcp"},
                "extract": {"type": "boolean", "default": True},
                "sync": {"type": "boolean", "default": False},
            },
            ["filename", "content_base64"],
        ),
    },
    {
        "name": "memory_list_media_artifacts",
        "description": "List recent media artifacts and extraction status.",
        "inputSchema": schema({"limit": {"type": "integer", "default": 20}}),
    },
    {
        "name": "memory_get_media_artifact",
        "description": "Get safe details for one media artifact, including extracted note summary when available.",
        "inputSchema": schema({"artifact_id": {"type": "string"}}, ["artifact_id"]),
    },
    {
        "name": "memory_extract_media_artifact",
        "description": "Synchronously trigger extraction for one media artifact when the caller needs immediate status.",
        "inputSchema": schema({"artifact_id": {"type": "string"}}, ["artifact_id"]),
    },
]


def api(method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    if not AGENT_TOKEN:
        raise RuntimeError("LIFE_MEMORY_AGENT_TOKEN is not set")
    if not AGENT_ID:
        raise RuntimeError("LIFE_MEMORY_AGENT_ID is not set")
    data = None if method == "GET" else json.dumps(body or {}).encode("utf-8")
    request = urllib.request.Request(
        f"{API_URL}{path}",
        data=data,
        headers={"Content-Type": "application/json", "X-Agent-Token": AGENT_TOKEN},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", "replace")
        return {"error": "api_error", "status_code": exc.code, "body": error_body}


def base_body(args: dict[str, Any]) -> dict[str, Any]:
    return {
        "agent_id": AGENT_ID,
        "project_id": args.get("project_id"),
        "repo_id": args.get("repo_id"),
        "workspace_id": args.get("workspace_id"),
    }


def _legacy_memory_access_map() -> dict[str, Any]:
    return {
        "name": "Life Memory Access Map",
        "version": "3.0.0",
        "system_goal": "A shared long-term memory layer for agents: read prior user/project context before work, write useful outcomes after work, and sync external agent memories without duplicating or losing provenance.",
        "principle": "Agents read committed memory through search/context tools and write only RawEvent-style inputs; the server owns extraction, policy, and durable commits.",
        "agent_benefits": [
            "Start tasks with the user's durable preferences, project context, and prior decisions.",
            "Avoid asking the user to repeat context that another connected agent already learned.",
            "Preserve decisions, corrections, artifacts, and daily deltas as RawEvents for Working-Agent governance.",
            "Share useful memory across Codex, Claude, Cursor, Windsurf, and other MCP-capable agents.",
        ],
        "quickstart": [
            "Run the bootstrap script from the skill package.",
            "Call memory_map immediately after MCP connects.",
            "Use memory_before_start before substantial work and memory_after_end after finishing.",
            "Use memory_sync_existing once for old memories and memory_upload_daily_delta for scheduled updates.",
        ],
        "habit_loop": {
            "before": "Call memory_before_start for nontrivial user/project work.",
            "during": "Call memory_search when scope changes or facts are missing.",
            "after": "Call memory_after_end with concise summary, decisions, actions, and artifacts.",
            "scheduled": "Call memory_upload_daily_delta from daily automation, then verify with memory_sync_status.",
        },
        "recommended_flow": [
            {"step": "connect", "tool": "memory_map", "purpose": "Learn tool contracts and context fields."},
            {"step": "pre_task", "tool": "memory_before_start", "purpose": "Load context_pack before substantial work."},
            {"step": "during_task", "tool": "memory_search", "purpose": "Search again when scope changes or details are needed."},
            {"step": "write_note", "tool": "memory_upload_event", "purpose": "Preserve raw observations that may become memory."},
            {"step": "finish_task", "tool": "memory_after_end", "purpose": "Write summary, decisions, actions, and artifacts."},
            {"step": "daily_sync", "tool": "memory_upload_daily_delta", "purpose": "Upload new or changed external memories with stable external_id values."},
            {"step": "verify", "tool": "memory_sync_status", "purpose": "Check processing, duplicate, work-case, and committed counts."},
        ],
        "context_fields": {
            "context_tiers": "L0 compressed prompt header, L1 layer summaries, L2 deep refs.",
            "context_tree": "Directory-style paths for narrowing project/layer/type/memory leaves.",
            "memory_layers": "working, episodic, semantic, procedural counts and policy.",
            "relation_graph": "Connected memory nodes and edges for evidence chaining.",
            "memory_evolution": "Review, promotion, validity, and compaction hints.",
            "retrieval_trace": "Why each memory was retrieved and scored.",
        },
        "write_rules": [
            "Never write CommittedMemory directly.",
            "Use memory_upload_event or memory_after_end for raw session facts.",
            "Use memory_sync_existing for one-time imports and memory_upload_daily_delta for recurring imports.",
            "Use stable external_id for every imported memory so reruns are idempotent.",
            "Use media tools for URLs/files/images/audio/video instead of storing extracted content as facts.",
        ],
        "automation_policy": {
            "reuse_agent_profile": True,
            "daily_tool": "memory_upload_daily_delta",
            "status_tool": "memory_sync_status",
            "required_metadata": ["sync_source", "external_memory_id", "curated"],
        },
        "troubleshooting": {
            "rtk_powershell": "Run PowerShell cmdlets through rtk powershell -NoProfile -Command; do not call cmdlets directly through rtk.",
            "codex_hot_reload": "If config.toml is written but tools are not visible, verify with the bundled stdio smoke test and restart or refresh Codex.",
            "codex_config": "Use scripts/write_codex_mcp_config.py instead of one-line PowerShell here-strings.",
            "sync_timeout": "For first full memory_sync_existing import, set trigger_extraction=false and verify RawEvents first.",
            "sync_500": "If one item succeeds but a batch fails, use ASCII slug ids, remove raw Windows paths/mojibake from identifier fields, and split batches.",
            "governance_pending": "RawEvents may remain pending while Working-Agent evidence governance is incomplete.",
        },
        "packaged_helpers": {
            "installer": "scripts/install_life_memory_skill.py downloads the Skill, creates an agent, writes optional MCP config, and runs smoke tests.",
            "doctor": "scripts/doctor.py checks Python, API reachability, env vars, MCP stdio, memory_map, policy, and sync status.",
            "payload_validator": "scripts/validate_memory_batch.py cleans risky identifiers before memory_sync_existing or memory_upload_daily_delta.",
            "codex_config_writer": "scripts/write_codex_mcp_config.py writes Codex config.toml without PowerShell here-string quoting.",
            "automation_templates": "references/automation-templates.md provides Codex scheduled task, Windows Task Scheduler, and cron templates.",
        },
        "tool_groups": {
            "read": ["memory_map", "memory_before_start", "memory_search", "memory_sync_status", "memory_policy_status"],
            "write": ["memory_upload_event", "memory_after_end", "memory_sync_existing", "memory_upload_daily_delta"],
            "test": ["memory_test_roundtrip"],
            "media": [
                "memory_create_link_artifact",
                "memory_upload_media_base64",
                "memory_list_media_artifacts",
                "memory_get_media_artifact",
                "memory_extract_media_artifact",
            ],
        },
    }


def _load_packaged_contract() -> dict[str, Any]:
    contract_path = Path(__file__).with_name("mcp_contract.json")
    try:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError("Missing packaged MCP contract: mcp_contract.json") from exc
    if contract.get("skill_version") != "3.0.0":
        raise RuntimeError("Unsupported packaged MCP contract version")
    if not isinstance(contract.get("tool_definitions"), list) or not isinstance(contract.get("memory_access_map"), dict):
        raise RuntimeError("Invalid packaged MCP contract")
    return contract


_PACKAGED_CONTRACT = _load_packaged_contract()
TOOLS = _PACKAGED_CONTRACT["tool_definitions"]


def memory_access_map() -> dict[str, Any]:
    """Return the same V3 MAP exposed by the repository MCP server."""
    return deepcopy(_PACKAGED_CONTRACT["memory_access_map"])


def call_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    if name == "memory_search":
        return api(
            "POST",
            "/api/agent/search",
            {
                "agent_id": AGENT_ID,
                "task": args["query"],
                "project_id": args.get("project_id"),
                "recall_level": args.get("recall_level", "work_context"),
                "top_k": args.get("top_k", 10),
            },
        )
    if name == "memory_before_start":
        return api(
            "POST",
            "/api/agent/before-start",
            {
                "agent_id": AGENT_ID,
                "task": args["task"],
                "project_id": args.get("project_id"),
                "repo_id": args.get("repo_id"),
                "recall_level": args.get("recall_level", "work_context"),
                "top_k": args.get("top_k", 8),
            },
        )
    if name == "memory_after_end":
        body = base_body(args)
        body.update(
            {
                "session_summary": args["summary"],
                "decisions": [{"content": item} for item in args.get("decisions", [])],
                "actions": [{"content": item} for item in args.get("actions", [])],
                "artifacts": [{"name": item} for item in args.get("artifacts", [])],
            }
        )
        return api("POST", "/api/agent/after-end", body)
    if name == "memory_upload_event":
        body = base_body(args)
        body.update(
            {
                "content": args["content"],
                "source_type": args.get("source_type", "mcp"),
                "metadata": args.get("metadata", {}),
                "dedupe": args.get("dedupe", True),
            }
        )
        return api("POST", "/api/agent/events", body)
    if name in {"memory_sync_existing", "memory_upload_daily_delta"}:
        cleaned_args = _clean_memory_sync_args(name, args)
        return api(
            "POST",
            "/api/agent/memory-sync",
            {
                "agent_id": AGENT_ID,
                "source_name": cleaned_args.get("source_name", "agent_memory"),
                "default_project_id": cleaned_args.get("default_project_id"),
                "memories": cleaned_args["memories"],
                "dedupe": cleaned_args.get("dedupe", True),
                "trigger_extraction": cleaned_args.get("trigger_extraction", name == "memory_upload_daily_delta"),
                "client_validation_warnings": cleaned_args["_validation_warnings"],
            },
        )
    if name == "memory_sync_status":
        return api(
            "POST",
            "/api/agent/sync-status",
            {
                "agent_id": AGENT_ID,
                "source_name": args.get("source_name"),
                "project_id": args.get("project_id"),
                "limit": args.get("limit", 50),
            },
        )
    if name == "memory_policy_status":
        return api("GET", "/api/agent/policy-status")
    if name == "memory_list_types":
        return api("GET", "/api/agent/types")
    if name == "memory_map":
        return memory_access_map()
    if name == "memory_test_roundtrip":
        return api(
            "POST",
            "/api/agent/test-roundtrip",
            {
                "agent_id": AGENT_ID,
                "content": args.get(
                    "content",
                    "Life Memory MCP bridge roundtrip test: low-risk connectivity fact.",
                ),
                "project_id": args.get("project_id", "life-memory-system"),
                "source_name": args.get("source_name", "agent_memory_bridge_roundtrip"),
                "metadata": args.get("metadata", {}),
                "recall_level": args.get("recall_level", "work_context"),
                "top_k": args.get("top_k", 5),
            },
        )
    if name == "memory_create_link_artifact":
        return api(
            "POST",
            "/api/media/link",
            {
                "url": args["url"],
                "source_text": args.get("source_text") or args["url"],
                "source_channel": args.get("source_channel", "mcp"),
                "extract": args.get("extract", True),
                "sync": args.get("sync", False),
            },
        )
    if name == "memory_upload_media_base64":
        return api(
            "POST",
            "/api/media/upload-base64",
            {
                "filename": args["filename"],
                "content_base64": args["content_base64"],
                "mime_type": args.get("mime_type"),
                "media_type": args.get("media_type"),
                "source_channel": args.get("source_channel", "mcp"),
                "extract": args.get("extract", True),
                "sync": args.get("sync", False),
            },
        )
    if name == "memory_list_media_artifacts":
        query = urllib.parse.urlencode({"limit": args.get("limit", 20)})
        return api("GET", f"/api/media/artifacts?{query}")
    if name == "memory_get_media_artifact":
        artifact_id = urllib.parse.quote(str(args["artifact_id"]), safe="")
        return api("GET", f"/api/media/artifacts/{artifact_id}")
    if name == "memory_extract_media_artifact":
        artifact_id = urllib.parse.quote(str(args["artifact_id"]), safe="")
        return api("POST", f"/api/media/artifacts/{artifact_id}/extract", {})
    return {"error": f"unknown_tool: {name}"}


def send(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def result(message_id: Any, value: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "result": value}


def error(message_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": message}}


def main() -> None:
    for line in sys.stdin:
        if not line.strip():
            continue
        message_id = None
        try:
            message = json.loads(line)
            method = message.get("method")
            message_id = message.get("id")
            if method == "initialize":
                send(
                    result(
                        message_id,
                        {
                            "protocolVersion": "2024-11-05",
                            "capabilities": {"tools": {}},
                            "serverInfo": {"name": "life-memory-mcp-proxy", "version": "3.0.0"},
                        },
                    )
                )
            elif method == "tools/list":
                send(result(message_id, {"tools": TOOLS}))
            elif method == "tools/call":
                params = message.get("params") or {}
                data = call_tool(params.get("name", ""), params.get("arguments") or {})
                send(
                    result(
                        message_id,
                        {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, indent=2)}]},
                    )
                )
            elif method and method.startswith("notifications/"):
                continue
            else:
                send(error(message_id, -32601, f"Method not found: {method}"))
        except Exception as exc:
            send(error(message_id, -32000, str(exc)))


if __name__ == "__main__":
    main()
