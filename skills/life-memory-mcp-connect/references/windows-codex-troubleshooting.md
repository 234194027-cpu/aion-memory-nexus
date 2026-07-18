# Windows And Codex Troubleshooting

Use this reference when onboarding from Codex on Windows, when `rtk` is available, or when MCP tools do not appear immediately after editing config files.

## RTK And PowerShell

`rtk` resolves executable files, not PowerShell cmdlets. Do not run cmdlets directly through `rtk`.

Use:

```powershell
rtk powershell -NoProfile -Command "Expand-Archive -LiteralPath 'skill.zip' -DestinationPath 'skill' -Force"
rtk powershell -NoProfile -Command "Get-Content -LiteralPath 'skills\life-memory-mcp-connect\SKILL.md' -Encoding UTF8"
```

Avoid complex one-line PowerShell that contains `$`, `$_`, or here-strings. If a command needs those, write and run a small `.ps1` or use the bundled Python scripts.

## Codex MCP Config

Current Codex sessions may not hot-load a new MCP server after `config.toml` changes. Treat config-file success and current-session tool availability as separate checks.

Recommended verification order:

1. Write the MCP config with a script, not a one-line PowerShell here-string.
2. Run `scripts/smoke_test_mcp.py` against the bundled stdio proxy.
3. If the smoke test passes but tools are absent in the current Codex tool list, restart or refresh the Codex session.
4. Do not report MCP failure solely because the current session did not hot-load new tools.

Use `scripts/write_codex_mcp_config.py` to append a Codex `config.toml` block safely.

## Full Sync Timeout

For the first `memory_sync_existing` import, prefer:

```json
{"trigger_extraction": false}
```

This verifies the RawEvent sync path quickly. After that, use `memory_sync_status` to inspect counts and enable extraction in smaller batches when the server-side policy is ready.

## 500 From `/api/agent/memory-sync`

If a minimal one-item payload works but a batch returns 500, the MCP bridge and API endpoint are available. Debug the batch, not the connection.

Use these batch rules:

- Keep `source_name`, `default_project_id`, `project_id`, `repo_id`, `workspace_id`, and `external_id` as stable ASCII slugs.
- Avoid raw Windows paths, backslashes, drive letters, and mojibake in identifier fields.
- Put human-readable paths in sanitized metadata only if needed.
- Start with one item, then 5-20 items per batch.
- Set `trigger_extraction=false` until batch import is stable.

## Governance Pending

External uploads create RawEvents. A work case can remain pending until the Working Agent has enough evidence or resolves a conflict; this is not an MCP error.

Report it as a governance state. Use `memory_sync_status` and include `raw_event_count`, `work_case_count`, `committed_count`, `case_counts`, and `processing_counts`.
