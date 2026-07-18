"""MCP server for Aion Memory Nexus.

The server exposes a small set of workflow tools for external agents:
search memory, fetch context before work, append task events, and sync an
agent's existing memory store into RawEvent entries.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sys
from urllib.parse import urlencode, quote
from typing import Any

import httpx

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import TextContent, Tool

    HAS_MCP_SDK = True
except ImportError:
    HAS_MCP_SDK = False
    Server = None
    TextContent = None
    Tool = None


API_URL = os.environ.get("LIFE_MEMORY_API_URL", "http://127.0.0.1:8000").rstrip("/")
AGENT_TOKEN = os.environ.get("LIFE_MEMORY_AGENT_TOKEN", "")
AGENT_ID = os.environ.get("LIFE_MEMORY_AGENT_ID", "")
MOJIBAKE_MARKERS = ("Ã", "Â", "�")
IDENTIFIER_FIELDS = ("project_id", "repo_id", "workspace_id", "external_id")


def _schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
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


TOOL_DEFS: list[dict[str, Any]] = [
    {
        "name": "memory_search",
        "description": "Search Aion Memory Nexus for context relevant to a question or task.",
        "inputSchema": _schema(
            {
                "query": {"type": "string", "description": "Question or search query."},
                "project_id": {"type": "string", "description": "Optional project scope."},
                "recall_level": {
                    "type": "string",
                    "description": "task_only, work_context, personal_context, or full_trusted.",
                    "default": "work_context",
                },
                "top_k": {"type": "integer", "description": "Maximum memories to return.", "default": 10},
            },
            ["query"],
        ),
    },
    {
        "name": "memory_before_start",
        "description": "Call before starting work to retrieve the most relevant memory context.",
        "inputSchema": _schema(
            {
                "task": {"type": "string", "description": "Current task description."},
                "project_id": {"type": "string", "description": "Optional project scope."},
                "recall_level": {
                    "type": "string",
                    "description": "task_only, work_context, personal_context, or full_trusted.",
                    "default": "work_context",
                },
                "top_k": {"type": "integer", "description": "Maximum memories to return.", "default": 10},
            },
            ["task"],
        ),
    },
    {
        "name": "memory_after_end",
        "description": "Call after finishing work to save a task summary, decisions, actions, and artifacts.",
        "inputSchema": _schema(
            {
                "task": {"type": "string", "description": "Task description."},
                "summary": {"type": "string", "description": "Completed work summary."},
                "decisions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Important decisions made during the task.",
                    "default": [],
                },
                "actions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Actions performed during the task.",
                    "default": [],
                },
                "artifacts": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Files, URLs, or other artifacts produced.",
                    "default": [],
                },
                "project_id": {"type": "string", "description": "Optional project scope."},
                "repo_id": {"type": "string", "description": "Optional repository scope."},
                "workspace_id": {"type": "string", "description": "Optional workspace scope."},
            },
            ["task", "summary"],
        ),
    },
    {
        "name": "memory_upload_event",
        "description": "Append one raw event. The Working Agent will govern it asynchronously through a work case.",
        "inputSchema": _schema(
            {
                "content": {"type": "string", "description": "Raw event content to preserve."},
                "source_type": {
                    "type": "string",
                    "description": "agent, mcp, codex, openclaw, chatgpt, or obsidian.",
                    "default": "mcp",
                },
                "project_id": {"type": "string", "description": "Optional project scope."},
                "repo_id": {"type": "string", "description": "Optional repository scope."},
                "workspace_id": {"type": "string", "description": "Optional workspace scope."},
                "metadata": {"type": "object", "description": "Additional event metadata.", "default": {}},
                "dedupe": {"type": "boolean", "description": "Skip if this agent already uploaded the same content.", "default": True},
            },
            ["content"],
        ),
    },
    {
        "name": "memory_sync_existing",
        "description": "Import an external agent's existing memory store as RawEvents for review/extraction.",
        "inputSchema": _schema(
            {
                "source_name": {"type": "string", "description": "Name of the source memory store.", "default": "agent_memory"},
                "default_project_id": {"type": "string", "description": "Project applied when an item has no project_id."},
                "memories": {
                    "type": "array",
                    "description": "Memory items to import. Keep batches under 100 items.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string"},
                            "title": {"type": "string"},
                            "memory_type": {"type": "string"},
                            "project_id": {"type": "string"},
                            "repo_id": {"type": "string"},
                            "workspace_id": {"type": "string"},
                            "external_id": {"type": "string"},
                            "created_at": {"type": "string"},
                            "updated_at": {"type": "string"},
                            "metadata": {"type": "object"},
                        },
                        "required": ["content"],
                    },
                },
                "dedupe": {"type": "boolean", "description": "Skip duplicate content per agent.", "default": True},
                "trigger_extraction": {"type": "boolean", "description": "Start Memory Agent extraction after import. For first full imports, prefer false and verify RawEvents first.", "default": False},
            },
            ["memories"],
        ),
    },
    {
        "name": "memory_upload_daily_delta",
        "description": "Upload a daily batch of new/changed memories from an external agent.",
        "inputSchema": _schema(
            {
                "source_name": {"type": "string", "description": "Name of the source memory store.", "default": "daily_agent_memory"},
                "since": {"type": "string", "description": "Lower-bound timestamp used by the caller for this delta."},
                "default_project_id": {"type": "string", "description": "Project applied when an item has no project_id."},
                "memories": {
                    "type": "array",
                    "description": "New or changed memory items since the previous upload.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string"},
                            "title": {"type": "string"},
                            "memory_type": {"type": "string"},
                            "project_id": {"type": "string"},
                            "repo_id": {"type": "string"},
                            "workspace_id": {"type": "string"},
                            "external_id": {"type": "string"},
                            "created_at": {"type": "string"},
                            "updated_at": {"type": "string"},
                            "metadata": {"type": "object"},
                        },
                        "required": ["content"],
                    },
                },
            },
            ["memories"],
        ),
    },
    {
        "name": "memory_sync_status",
        "description": "Show recent sync status for the authenticated agent, including RawEvent, work-case, committed, duplicate, and error counts.",
        "inputSchema": _schema(
            {
                "source_name": {"type": "string", "description": "Optional source memory store filter."},
                "project_id": {"type": "string", "description": "Optional project filter."},
                "limit": {"type": "integer", "description": "Recent RawEvents to inspect.", "default": 50},
            },
        ),
    },
    {
        "name": "memory_policy_status",
        "description": "Show the authenticated agent's server-side write and auto-commit policy summary without exposing tokens.",
        "inputSchema": _schema({}),
    },
    {
        "name": "memory_map",
        "description": "Return the Life Memory access map: recommended tool order, context fields, sync strategy, and write rules for agents.",
        "inputSchema": _schema({}),
    },
    {
        "name": "memory_test_roundtrip",
        "description": "Run a real test chain: upload RawEvent, run Working-Agent governance, report work cases/formal memories, then search for the result.",
        "inputSchema": _schema(
            {
                "content": {
                    "type": "string",
                    "description": "Low-risk test content to write as a RawEvent.",
                    "default": "Agent Memory Bridge roundtrip test: store a low-risk fact for MCP verification.",
                },
                "project_id": {"type": "string", "description": "Project scope for the test.", "default": "life-memory-system"},
                "source_name": {"type": "string", "description": "Sync source label for policy matching.", "default": "agent_memory_bridge_roundtrip"},
                "metadata": {"type": "object", "description": "Additional test metadata.", "default": {}},
                "recall_level": {"type": "string", "description": "Recall level for search verification.", "default": "work_context"},
                "top_k": {"type": "integer", "description": "Search result limit.", "default": 5},
            },
        ),
    },
    {
        "name": "memory_list_types",
        "description": "List supported agent types and the current authenticated agent type.",
        "inputSchema": _schema({}),
    },
    {
        "name": "memory_create_link_artifact",
        "description": "Create a link MediaArtifact for asynchronous webpage extraction. Use this for URLs instead of uploading them as facts.",
        "inputSchema": _schema(
            {
                "url": {"type": "string", "description": "Public http/https URL."},
                "source_text": {"type": "string", "description": "Optional surrounding text from the agent/user."},
                "source_channel": {"type": "string", "description": "Source label.", "default": "mcp"},
                "extract": {"type": "boolean", "description": "Queue extraction after creating the artifact.", "default": True},
                "sync": {"type": "boolean", "description": "Extract synchronously before returning.", "default": False},
            },
            ["url"],
        ),
    },
    {
        "name": "memory_upload_media_base64",
        "description": "Upload a file, image, table, audio, or video as base64 media for asynchronous extraction.",
        "inputSchema": _schema(
            {
                "filename": {"type": "string", "description": "Original filename."},
                "content_base64": {"type": "string", "description": "Base64-encoded file bytes."},
                "mime_type": {"type": "string", "description": "Optional MIME type."},
                "media_type": {"type": "string", "description": "Optional media type override."},
                "source_channel": {"type": "string", "description": "Source label.", "default": "mcp"},
                "extract": {"type": "boolean", "description": "Queue extraction after upload.", "default": True},
                "sync": {"type": "boolean", "description": "Extract synchronously before returning.", "default": False},
            },
            ["filename", "content_base64"],
        ),
    },
    {
        "name": "memory_list_media_artifacts",
        "description": "List recent media artifacts and extraction status.",
        "inputSchema": _schema({"limit": {"type": "integer", "description": "Max artifacts.", "default": 20}}),
    },
    {
        "name": "memory_get_media_artifact",
        "description": "Get safe details for one media artifact, including extracted note summary when available.",
        "inputSchema": _schema({"artifact_id": {"type": "string", "description": "MediaArtifact id."}}, ["artifact_id"]),
    },
    {
        "name": "memory_extract_media_artifact",
        "description": "Synchronously trigger extraction for one media artifact when the caller needs immediate status.",
        "inputSchema": _schema({"artifact_id": {"type": "string", "description": "MediaArtifact id."}}, ["artifact_id"]),
    },
]


async def _call_api(method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    if not AGENT_TOKEN:
        raise RuntimeError("LIFE_MEMORY_AGENT_TOKEN is not set")

    headers = {
        "Content-Type": "application/json",
        "X-Agent-Token": AGENT_TOKEN,
    }
    url = f"{API_URL}{path}"

    async with httpx.AsyncClient(timeout=60, trust_env=False) as client:
        if method == "GET":
            response = await client.get(url, headers=headers)
        else:
            response = await client.post(url, headers=headers, json=body or {})

    response.raise_for_status()
    return response.json()


def _json_text(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


def _base_agent_body(arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "agent_id": AGENT_ID,
        "project_id": arguments.get("project_id"),
        "repo_id": arguments.get("repo_id"),
        "workspace_id": arguments.get("workspace_id"),
    }


def memory_access_map() -> dict[str, Any]:
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
            "working_agent_governance": "Uploads become RawEvents and only the Working Agent may govern them into formal memories.",
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


async def handle_tool(name: str, arguments: dict[str, Any] | None) -> str:
    args = arguments or {}
    try:
        if name == "memory_search":
            body = {
                "agent_id": AGENT_ID,
                "task": args["query"],
                "project_id": args.get("project_id"),
                "recall_level": args.get("recall_level", "work_context"),
                "top_k": args.get("top_k", 10),
            }
            return _json_text(await _call_api("POST", "/api/agent/search", body))

        if name == "memory_before_start":
            body = {
                "agent_id": AGENT_ID,
                "task": args["task"],
                "project_id": args.get("project_id"),
                "recall_level": args.get("recall_level", "work_context"),
                "top_k": args.get("top_k", 10),
            }
            return _json_text(await _call_api("POST", "/api/agent/before-start", body))

        if name == "memory_after_end":
            body = _base_agent_body(args)
            body.update(
                {
                    "session_summary": args["summary"],
                    "decisions": [{"content": item} for item in args.get("decisions", [])],
                    "actions": [{"content": item} for item in args.get("actions", [])],
                    "artifacts": [{"name": item} for item in args.get("artifacts", [])],
                }
            )
            return _json_text(await _call_api("POST", "/api/agent/after-end", body))

        if name == "memory_upload_event":
            body = _base_agent_body(args)
            body.update(
                {
                    "content": args["content"],
                    "source_type": args.get("source_type", "mcp"),
                    "metadata": args.get("metadata", {}),
                    "dedupe": args.get("dedupe", True),
                }
            )
            return _json_text(await _call_api("POST", "/api/agent/events", body))

        if name in {"memory_sync_existing", "memory_upload_daily_delta"}:
            cleaned_args = _clean_memory_sync_args(name, args)
            body = {
                "agent_id": AGENT_ID,
                "source_name": cleaned_args.get("source_name", "agent_memory"),
                "default_project_id": cleaned_args.get("default_project_id"),
                "memories": cleaned_args["memories"],
                "dedupe": cleaned_args.get("dedupe", True),
                "trigger_extraction": cleaned_args.get("trigger_extraction", name == "memory_upload_daily_delta"),
                "client_validation_warnings": cleaned_args["_validation_warnings"],
            }
            return _json_text(await _call_api("POST", "/api/agent/memory-sync", body))

        if name == "memory_sync_status":
            body = {
                "agent_id": AGENT_ID,
                "source_name": args.get("source_name"),
                "project_id": args.get("project_id"),
                "limit": args.get("limit", 50),
            }
            return _json_text(await _call_api("POST", "/api/agent/sync-status", body))

        if name == "memory_policy_status":
            return _json_text(await _call_api("GET", "/api/agent/policy-status"))

        if name == "memory_map":
            return _json_text(memory_access_map())

        if name == "memory_test_roundtrip":
            body = {
                "agent_id": AGENT_ID,
                "content": args.get("content", "Agent Memory Bridge roundtrip test: store a low-risk fact for MCP verification."),
                "project_id": args.get("project_id", "life-memory-system"),
                "source_name": args.get("source_name", "agent_memory_bridge_roundtrip"),
                "metadata": args.get("metadata", {}),
                "recall_level": args.get("recall_level", "work_context"),
                "top_k": args.get("top_k", 5),
            }
            return _json_text(await _call_api("POST", "/api/agent/test-roundtrip", body))

        if name == "memory_list_types":
            return _json_text(await _call_api("GET", "/api/agent/types"))

        if name == "memory_create_link_artifact":
            body = {
                "url": args["url"],
                "source_text": args.get("source_text") or args["url"],
                "source_channel": args.get("source_channel", "mcp"),
                "extract": args.get("extract", True),
                "sync": args.get("sync", False),
            }
            return _json_text(await _call_api("POST", "/api/media/link", body))

        if name == "memory_upload_media_base64":
            body = {
                "filename": args["filename"],
                "content_base64": args["content_base64"],
                "mime_type": args.get("mime_type"),
                "media_type": args.get("media_type"),
                "source_channel": args.get("source_channel", "mcp"),
                "extract": args.get("extract", True),
                "sync": args.get("sync", False),
            }
            return _json_text(await _call_api("POST", "/api/media/upload-base64", body))

        if name == "memory_list_media_artifacts":
            return _json_text(await _call_api("GET", f"/api/media/artifacts?{urlencode({'limit': args.get('limit', 20)})}"))

        if name == "memory_get_media_artifact":
            return _json_text(await _call_api("GET", f"/api/media/artifacts/{quote(str(args['artifact_id']), safe='')}"))

        if name == "memory_extract_media_artifact":
            return _json_text(await _call_api("POST", f"/api/media/artifacts/{quote(str(args['artifact_id']), safe='')}/extract", {}))

        return _json_text({"error": f"Unknown tool: {name}"})

    except httpx.HTTPStatusError as exc:
        return _json_text(
            {
                "error": "api_error",
                "status_code": exc.response.status_code,
                "body": exc.response.text,
                "suggestion": "Check LIFE_MEMORY_API_URL, LIFE_MEMORY_AGENT_TOKEN, and agent_id.",
            }
        )
    except Exception as exc:
        return _json_text(
            {
                "error": type(exc).__name__,
                "message": str(exc),
                "suggestion": "Verify the Life Memory API is running and MCP environment variables are set.",
            }
        )


def _tool_result(tool_def: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": tool_def["name"],
        "description": tool_def["description"],
        "inputSchema": tool_def["inputSchema"],
    }


if HAS_MCP_SDK:
    server = Server("life-memory")

    @server.list_tools()
    async def list_tools() -> list[Any]:
        return [Tool(**_tool_result(tool_def)) for tool_def in TOOL_DEFS]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[Any]:
        return [TextContent(type="text", text=await handle_tool(name, arguments))]

    async def main() -> None:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

else:

    def _make_response(message_id: Any, result: Any = None, error: Any = None) -> dict[str, Any]:
        response = {"jsonrpc": "2.0", "id": message_id}
        if error is not None:
            response["error"] = error
        else:
            response["result"] = result
        return response

    async def _handle_jsonrpc(message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method", "")
        message_id = message.get("id")
        params = message.get("params") or {}

        if method == "initialize":
            return _make_response(
                message_id,
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "life-memory", "version": "1.1.0"},
                },
            )

        if method == "notifications/initialized":
            return None

        if method == "tools/list":
            return _make_response(message_id, {"tools": [_tool_result(tool_def) for tool_def in TOOL_DEFS]})

        if method == "tools/call":
            result = await handle_tool(params.get("name", ""), params.get("arguments") or {})
            return _make_response(message_id, {"content": [{"type": "text", "text": result}]})

        return _make_response(
            message_id,
            error={"code": -32601, "message": f"Method not found: {method}"},
        )

    async def main() -> None:
        loop = asyncio.get_event_loop()
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)

        writer_transport, writer_protocol = await loop.connect_write_pipe(
            asyncio.streams.FlowControlMixin,
            sys.stdout,
        )
        writer = asyncio.StreamWriter(writer_transport, writer_protocol, reader, loop)

        while True:
            line = await reader.readline()
            if not line:
                break
            try:
                message = json.loads(line.decode("utf-8"))
                response = await _handle_jsonrpc(message)
            except Exception as exc:
                response = _make_response(
                    None,
                    error={"code": -32603, "message": f"{type(exc).__name__}: {exc}"},
                )
            if response is not None:
                writer.write((json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8"))
                await writer.drain()


if __name__ == "__main__":
    asyncio.run(main())
