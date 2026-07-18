---
name: life-memory-mcp-connect
description: Set up an external AI agent to connect to the Life Memory System through MCP, including verified installation, retrieval, RawEvent writes, sync, diagnostics, and Working-Agent governance. Use when an agent needs to install or verify the Life Memory MCP bridge, onboard Codex/Claude/Cursor/Windsurf style clients, or prepare automatic memory synchronization.
---

# Life Memory MCP Connect

Use this skill to onboard an agent into the Life Memory System through a portable stdio MCP bridge. Prefer the full automatic bootstrap script first; use the lower-level scripts only when the target environment needs custom control.

## What This Gives Agents

Life Memory is the shared long-term memory layer for connected agents. It helps agents start with prior user/project context, avoid making the user repeat decisions, preserve useful task outcomes, and sync memories from other agent systems into one reviewable pipeline.

Use it as a habit loop:

1. Read memory before substantial work.
2. Search again when scope changes.
3. Write a short task-end summary after work.
4. Sync old memories once and daily deltas on a schedule.

## Architecture

This skill has three layers:

1. **Skill layer**: teaches an external agent how to install, configure, test, and operate the bridge.
2. **MAP layer**: `memory_map` returns the Memory Access Protocol: tool order, context fields, write rules, and daily sync strategy.
3. **MCP layer**: exposes concrete tools for search, before/after task hooks, event upload, daily delta sync, media upload, and smoke tests.

Graphiti/Neo4j, when enabled by an operator, is an internal, disposable retrieval projection. This Skill exposes no Graphiti write, replay, administration, or cross-project tool; if Graphiti is disabled or unavailable, the existing retrieval chain remains the source of MCP results.

After a client connects, call `memory_map` before other memory tools. Use `references/memory-access-map.md` when you need the complete operating contract.

For copy-paste onboarding, use `references/agent-bootstrap-prompt.md`. Keep that prompt short: it should only start the bootstrap flow, while detailed operating rules live in this skill, `references/memory-access-map.md`, and the live `memory_map` tool response.

For Codex on Windows or RTK environments, read `references/windows-codex-troubleshooting.md` before writing config or debugging sync failures.
For recurring sync setup, use `references/automation-templates.md`.

## Full Automatic Bootstrap

### Give another Agent this short prompt

```text
接入 Life Memory MCP V3。先阅读本 Skill 的 SKILL.md；用安装器执行只读 smoke，成功后依次调用 memory_map、memory_list_types、memory_policy_status。每个非简单任务前调用 memory_before_start，完成后只能通过 memory_after_end 或 memory_upload_event 追加 RawEvent。不得直接写正式记忆、不得操作 Graphiti/Neo4j 或管理接口，不得泄露 Token。链接和文件使用媒体工具。失败时报告脱敏错误，不要绕过权限或把 HTML 当 ZIP 安装。
```

详细安装、同步、媒体和故障处理规则均在本 Skill 内；不要把这些规则重复塞入外部 Agent 的系统提示词。

For a new external agent, run:

```bash
python scripts/bootstrap_life_memory_agent.py
```

This creates a dedicated Life Memory agent, receives the one-time token in that process, starts the bundled MCP proxy, runs tools/list, policy/status/search checks, and optionally writes a low-risk RawEvent roundtrip. It does not print the token unless `--show-token` is explicitly set.

To also write a private MCP config for the target client:

```bash
python scripts/bootstrap_life_memory_agent.py \
  --client cursor \
  --config-output .mcp.json \
  --write-test
```

Only use `--config-output` in a private workspace because the generated MCP config must contain the token for most clients to launch the server later.

## Required Inputs

Collect or infer these values before configuring a client:

- `LIFE_MEMORY_API_URL`: production or local API base URL. Obtain it from the deployment owner through a private channel; it is intentionally not published in this repository.
- `LIFE_MEMORY_AGENT_ID`: the agent profile id returned by the restricted bootstrap endpoint.
- `LIFE_MEMORY_AGENT_TOKEN`: the token shown once when the agent is created or regenerated.
- `project_id`: a stable scope for syncs. Use `codex-global-memory` for Codex global memories.
- `source_name`: a stable source label, for example `codex_global_memory`.

Never paste tokens into reports, screenshots, logs, or committed files. Use environment variables or the target client's secret storage when available.

## Workflow

1. Prefer `scripts/bootstrap_life_memory_agent.py` for new agents.
2. Call `memory_map` after connection so the target agent learns the current MAP contract and why/when to use the system.
3. Use `memory_before_start` before nontrivial user/project work.
4. Use `memory_search` during work when scope changes or more evidence is needed.
5. Use `memory_after_end` after work to preserve summary, decisions, actions, and artifacts.
6. If reusing an existing agent, collect `LIFE_MEMORY_API_URL`, `LIFE_MEMORY_AGENT_ID`, and `LIFE_MEMORY_AGENT_TOKEN`.
7. Treat all external writes as RawEvents. Only the internal Working Agent may create or revise formal memory after evidence governance.
8. Generate MCP client config with `scripts/configure_mcp.py` when bootstrap is not enough.
9. Run `scripts/smoke_test_mcp.py` against the generated bridge.
10. Sync existing memory with stable `external_id` values through `memory_sync_existing`.
11. For daily automation, reuse the same agent id/token and call `memory_upload_daily_delta`; do not create a new agent per run.
12. Verify with `memory_sync_status` and `memory_search`.

In Codex, use `scripts/write_codex_mcp_config.py` instead of one-line PowerShell here-strings when writing `config.toml`. If the config is written but `memory_map` does not appear in the current session, verify through `scripts/smoke_test_mcp.py` and then restart or refresh Codex.

For full one-command onboarding, run `scripts/install_life_memory_skill.py` with a privately supplied manifest URL and API URL. For diagnostics, run `scripts/doctor.py`.

## Media And Link Notes

Use media tools when the source is a URL, file, image, table, audio, or video. Do not paste the full extracted content into `memory_upload_event` as if it were a confirmed fact.

Available tools:

- `memory_create_link_artifact`: create a link artifact from a public `http/https` URL and queue webpage extraction.
- `memory_upload_media_base64`: upload file bytes as base64 and queue extraction.
- `memory_list_media_artifacts`: inspect recent media extraction status.
- `memory_get_media_artifact`: inspect one artifact safely.
- `memory_extract_media_artifact`: force synchronous extraction when immediate status is required.

Recommended usage:

- For links: call `memory_create_link_artifact` with `url`, optional `source_text`, and `source_channel="mcp"`.
- For tables/documents/images/audio/video: call `memory_upload_media_base64` with `filename`, `content_base64`, and `mime_type`.
- Keep `extract=true` and `sync=false` by default so the Life Memory worker extracts content asynchronously.
- Treat extracted media as source evidence, not a permanent fact. The server routes it through RawEvent, work case, evidence, decision, and formal-memory governance.
- Never upload secrets, private keys, access tokens, raw credential files, or payment identifiers.

Example link call:

```json
{
  "url": "https://example.com/article",
  "source_text": "User asked me to remember this article for later reading.",
  "source_channel": "mcp",
  "extract": true,
  "sync": false
}
```

Example base64 file call:

```json
{
  "filename": "notes.csv",
  "mime_type": "text/csv",
  "source_channel": "mcp",
  "content_base64": "<base64 file bytes>",
  "extract": true,
  "sync": false
}
```

## Working-Agent Governance

External MCP clients can read within their configured scope and append RawEvents only. The internal Working Agent creates work cases, gathers evidence, detects conflicts, and is the sole creator of formal memories. High-confidence, traceable, non-conflicting proposals may be committed automatically by that internal service; all other cases remain pending or enter conflict review. See `references/auto-submit-policy.md`.

## MCP Configuration

Generate a config snippet:

```bash
python scripts/configure_mcp.py \
  --api-url https://memory.example.invalid \
  --agent-id <agent_id> \
  --token-env LIFE_MEMORY_AGENT_TOKEN \
  --client cursor
```

By default the generated config contains `"<set-agent-token-here>"`. Replace it in the target client's private config, or run with `--include-token-from-env` only when writing to a private local config.

For clients that support MCP config files, set:

- `command`: `python`
- `args`: absolute path to `scripts/life_memory_mcp_server.py`
- env: `LIFE_MEMORY_API_URL`, `LIFE_MEMORY_AGENT_ID`, `LIFE_MEMORY_AGENT_TOKEN`

Use `references/mcp-config-examples.md` when the target client needs a concrete JSON shape.

## Smoke Test

Run a read-only test first:

```bash
python scripts/smoke_test_mcp.py \
  --api-url https://memory.example.invalid \
  --agent-id <agent_id> \
  --token-env LIFE_MEMORY_AGENT_TOKEN \
  --project-id life-memory-system
```

Then run a low-risk write/search test:

```bash
python scripts/smoke_test_mcp.py \
  --api-url https://memory.example.invalid \
  --agent-id <agent_id> \
  --token-env LIFE_MEMORY_AGENT_TOKEN \
  --project-id life-memory-system \
  --write-test
```

Report only availability, created/skipped counts, sync status, search count, and exact errors. Do not report tokens.

The smoke test must show `tools_available.memory_map=true` and `access_map.has_daily_sync=true`.

## Existing Memory Sync

When importing another agent's existing memories:

- Upload curated summaries, not raw transcripts or full logs.
- For the first full import, set `trigger_extraction=false` and verify the RawEvent path before enabling extraction.
- Run `scripts/validate_memory_batch.py` before large imports, or rely on the MCP proxy's built-in identifier cleanup.
- Keep `source_name`, project ids, repo ids, workspace ids, and `external_id` values as stable ASCII slugs.
- Avoid raw Windows paths, backslashes, drive letters, and mojibake in identifier fields.
- Strip secrets, passwords, API keys, access tokens, private connection strings, and personal contact/payment identifiers.
- Use stable `external_id` values so reruns are idempotent.
- Set metadata such as `sync_source`, `curated`, `source_file`, `category`, and `sensitivity`.
- Verify rerun behavior: same agent + same `external_id` should return `created_count=0` and `skipped_count>0`.

## Daily Delta Automation

Daily jobs must reuse the same agent profile. The job should:

1. Load the last successful sync timestamp from the external source.
2. Extract only new or changed memories.
3. Call `memory_upload_daily_delta` with `since`, `source_name`, `default_project_id`, and stable `external_id` values.
4. Call `memory_sync_status` after the upload.
5. Alert on API/MCP errors, nonzero processing errors, or unexpected duplicate/create ratios.

Do not rotate the token every day unless the automation can update its secret store immediately.
