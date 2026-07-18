# Memory Access Map

This reference defines how an external agent should use the Life Memory MCP bridge after the skill is installed.

## Layers

The Life Memory System is a shared long-term memory layer for agents. It should make agents easier to work with: they can remember stable user preferences, reuse prior project context, preserve decisions and artifacts, and share useful memory across multiple tools without asking the user to repeat everything.

The operating habit is simple:

1. Read before work.
2. Search during work when context is missing.
3. Write a concise task-end note after work.
4. Sync existing memories once and daily deltas on a schedule.

1. Skill layer
   - Installs or configures the bridge.
   - Runs smoke tests.
   - Teaches the agent when to read, write, sync, and verify memory.

2. MAP layer
   - Exposed through the `memory_map` MCP tool.
   - Returns the current operating contract: recommended tool order, context fields, write rules, automation policy, and tool groups.
   - Call it first after MCP connection and again after skill/server upgrades.

3. MCP layer
   - Executes concrete tools.
   - Uses the authenticated agent token.
   - Never writes durable committed memory directly.

## Required First Calls

1. `memory_map`
   - Confirm available workflow and field contract.
   - Check `recommended_flow`, `context_fields`, `write_rules`, and `automation_policy`.

2. `memory_policy_status`
   - Confirm the RawEvent-only external write boundary and read scope.
   - If governance is pending, RawEvents remain traceable while the Working Agent gathers evidence.

3. `memory_sync_status`
   - Confirm the agent can read its current processing state.

## Normal Task Flow

Before work:

```text
memory_before_start(task, project_id, recall_level="work_context")
```

Use the returned `context_pack` in this order:

1. `context_tiers.L0.compressed_text` as the prompt header.
2. `context_tiers.L1.layer_summaries` as normal working context.
3. `context_tree` to narrow by project, layer, and memory type.
4. `relation_graph` when evidence relationships matter.
5. `retrieval_trace` when deciding why a memory was included.
6. `memory_evolution` only as maintenance hints, not as task truth.

During work:

```text
memory_search(query, project_id, recall_level, top_k)
```

Use this when the task scope changes, when a fact is missing, or when the agent needs deeper evidence.

After work:

```text
memory_after_end(summary, decisions, actions, artifacts, project_id)
```

Write a concise end-of-task record. Include decisions and artifacts because they are easier to promote into procedural or semantic memory later.

## Existing Memory Import

Use `memory_sync_existing` for one-time imports from another agent memory store.

Rules:

- Upload curated summaries, not raw full transcripts.
- Every item should have a stable `external_id`.
- Use a stable `source_name`, for example `codex_global_memory`.
- Use stable ASCII slugs for `source_name`, `default_project_id`, `project_id`, `repo_id`, `workspace_id`, and `external_id`.
- Avoid raw Windows paths, backslashes, drive letters, and mojibake in identifier fields.
- For first full import, set `trigger_extraction=false`, verify RawEvent counts, then enable extraction in smaller batches if needed.
- Run `scripts/validate_memory_batch.py` before large imports when operating from the Skill package.
- Include metadata such as `sync_source`, `curated`, `source_file`, and `category`.
- Rerunning the same import should be idempotent.

## Daily Delta Automation

Use `memory_upload_daily_delta` for scheduled jobs.

Required behavior:

- Reuse the same agent id and token.
- Keep a last-success timestamp in the external agent or scheduler.
- Upload only new or changed memories.
- Give every item a stable `external_id`.
- Run `memory_sync_status` after upload.
- Alert on API errors, extraction failures, or unexpected duplicate/create ratios.

Recommended payload shape:

```json
{
  "source_name": "codex_daily_delta",
  "since": "2026-07-05T00:00:00+08:00",
  "default_project_id": "codex-global-memory",
  "memories": [
    {
      "external_id": "codex-memory:abc123",
      "title": "User preference",
      "content": "The user prefers real verification over untested success reports.",
      "memory_type": "preference",
      "metadata": {
        "sync_source": "codex_daily_delta",
        "curated": true
      }
    }
  ]
}
```

## Media Flow

Use media tools for URLs and files.

- URL: `memory_create_link_artifact`
- File/image/table/audio/video: `memory_upload_media_base64`
- Status: `memory_list_media_artifacts` or `memory_get_media_artifact`
- Immediate extraction if needed: `memory_extract_media_artifact`

Do not turn extracted media content into a permanent fact on the client side. Let the server create source evidence and route it through Working-Agent governance.

## Write Boundaries

Agents must not:

- Write directly to committed memory.
- Upload secrets, access tokens, private keys, raw credential files, or payment identifiers.
- Treat uncertain extracted content as confirmed fact.
- Create a new Life Memory agent for every daily run.
- Rotate tokens unless the automation can update its secret store immediately.

Agents should:

- Write RawEvents.
- Use stable ids.
- Verify after writes.
- Preserve provenance through metadata.
- Keep task-end summaries short and structured.

## Known Client Failure Modes

- In RTK environments, run PowerShell cmdlets through `rtk powershell -NoProfile -Command ...`.
- Avoid one-line PowerShell here-strings for Codex `config.toml`; use `scripts/write_codex_mcp_config.py`.
- If Codex does not hot-load a new MCP server, run the bundled stdio smoke test and restart or refresh Codex.
- If `memory_sync_existing` times out with extraction enabled, retry with `trigger_extraction=false`.
- If a batch import returns 500 while a one-item payload succeeds, simplify identifiers and split the batch.
- If a work case remains pending, report RawEvent/work-case status and wait for Working-Agent governance rather than retrying as a formal-memory write.
- Use `references/automation-templates.md` for Codex scheduled tasks, Windows Task Scheduler, and cron examples.
