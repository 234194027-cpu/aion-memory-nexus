# Daily Automation Templates

Use these templates after MCP smoke tests pass. Reuse the same Life Memory agent id and token for every run.

## Codex Scheduled Task Prompt

```text
每天固定时间执行 Life Memory 增量同步：
1. 读取你自己的长期记忆源里自上次成功同步以来新增或变更的记忆。
2. 将每条记忆整理为稳定 external_id、title、content、memory_type、metadata。
3. 调用 memory_upload_daily_delta，source_name 使用稳定英文 slug，default_project_id 使用稳定英文项目 id。
4. 首次或大批量同步时 trigger_extraction=false；小批量稳定后可以开启抽取。
5. 调用 memory_sync_status，汇报 raw_event_count、work_case_count、committed_count、case_counts、processing_counts。
6. 如果事件仍在 Working-Agent 治理中，只报告状态，不把尚未形成正式记忆当成失败。
```

## Windows Task Scheduler

Create a `.ps1` that runs the agent/client automation. Avoid inline here-strings for MCP config.

```powershell
$env:LIFE_MEMORY_API_URL = "https://memory.example.invalid"
$env:LIFE_MEMORY_AGENT_ID = "<agent-id>"
$env:LIFE_MEMORY_AGENT_TOKEN = "<agent-token>"
python "C:\path\to\life-memory-mcp-connect\scripts\doctor.py" --agent-id $env:LIFE_MEMORY_AGENT_ID
```

Schedule it:

```powershell
schtasks /Create /SC DAILY /TN "LifeMemoryDailyDelta" /TR "powershell -NoProfile -ExecutionPolicy Bypass -File C:\path\to\life-memory-daily.ps1" /ST 09:00
```

## Cron

```bash
LIFE_MEMORY_API_URL=https://memory.example.invalid
LIFE_MEMORY_AGENT_ID=<agent-id>
LIFE_MEMORY_AGENT_TOKEN=<agent-token>
0 9 * * * cd /path/to/life-memory-mcp-connect && python scripts/doctor.py --agent-id "$LIFE_MEMORY_AGENT_ID" >> life-memory-daily.log 2>&1
```

Replace `doctor.py` with the client-specific daily delta command when the external agent exposes one. Keep `doctor.py` as a health check.

## Payload Safety

Before daily upload, run:

```bash
python scripts/validate_memory_batch.py payload.json --output payload.cleaned.json
```

Upload `payload.cleaned.json` through MCP or the Agent API.
