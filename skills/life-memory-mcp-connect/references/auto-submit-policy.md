# Working-Agent Governance Policy

External clients must not write `CommittedMemory` directly. They append RawEvents through MCP. The internal Working Agent creates a work case, links evidence, records a decision, and then alone may create, correct, retire, or delete formal memory.

## External-client boundary

- Bind every public-bootstrap token to one project and the minimum `task_only` read scope.
- Allow only RawEvent ingestion, task summaries, and idempotent imports from external MCP tools.
- Keep credentials, secrets, raw logs, private connection strings, and full transcripts out of imported content.
- Never expose Graphiti write, replay, administrator, or cross-project operations through this Skill.

## Formal-memory decision

The Working Agent may automatically commit only a high-confidence, traceable, non-conflicting proposal. Low-confidence, sensitive, identity-inferred, or conflicting material remains in `awaiting_evidence` or `conflict_review`. Verify ingestion with `memory_sync_status`; use `work_case_count`, `case_counts`, `committed_count`, and `processing_counts`.
