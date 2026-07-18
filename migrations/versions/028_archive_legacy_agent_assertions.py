"""Archive legacy cases that only contain Agent assertions.

Revision ID: 028
Revises: 027
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "028"
down_revision = "027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    # This is a PostgreSQL JSONB data cleanup. SQLite is used only to verify
    # the portable schema chain and has no legacy PostgreSQL rows to archive.
    if bind.dialect.name != "postgresql":
        return

    bind.execute(
        sa.text(
            """
            UPDATE memory_work_decisions AS decision
            SET state = 'DISCARDED',
                policy_result = (
                    COALESCE(decision.policy_result, '{}'::json)::jsonb
                    || jsonb_build_object(
                        'commit_allowed', false,
                        'reason', 'legacy_agent_assertion_archived',
                        'governance', 'working-agent-v2.4.2'
                    )
                )::json
            FROM memory_work_cases AS work_case
            WHERE work_case.id = decision.case_id
              AND work_case.user_id = decision.user_id
              AND work_case.case_metadata ->> 'migration' = '025'
              AND decision.state = 'MEMORY_READY'
              AND NOT EXISTS (
                  SELECT 1
                  FROM memory_work_evidence AS user_evidence
                  WHERE user_evidence.case_id = work_case.id
                    AND user_evidence.source_type IN (
                        'conversation', 'manual', 'obsidian', 'file_import'
                    )
              )
              AND EXISTS (
                  SELECT 1
                  FROM memory_work_evidence AS agent_evidence
                  WHERE agent_evidence.case_id = work_case.id
                    AND agent_evidence.source_type IN (
                        'agent_api', 'codex', 'openclaw', 'chatgpt'
                    )
              )
            """
        )
    )
    bind.execute(
        sa.text(
            """
            UPDATE memory_work_cases AS work_case
            SET status = 'discarded',
                resolved_at = COALESCE(work_case.resolved_at, now()),
                updated_at = now(),
                case_metadata = (
                    COALESCE(work_case.case_metadata, '{}'::json)::jsonb
                    || jsonb_build_object(
                        'automatic_disposition', 'legacy_agent_assertion_archived',
                        'automatic_disposition_version', '028'
                    )
                )::json
            WHERE work_case.case_metadata ->> 'migration' = '025'
              AND work_case.status IN ('ready_to_commit', 'awaiting_evidence')
              AND EXISTS (
                  SELECT 1
                  FROM memory_work_decisions AS decision
                  WHERE decision.case_id = work_case.id
                    AND decision.user_id = work_case.user_id
                    AND decision.state = 'DISCARDED'
                    AND decision.policy_result ->> 'reason'
                        = 'legacy_agent_assertion_archived'
              )
            """
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    bind.execute(
        sa.text(
            """
            UPDATE memory_work_decisions AS decision
            SET state = 'MEMORY_READY',
                policy_result = (
                    COALESCE(decision.policy_result, '{}'::json)::jsonb
                    || jsonb_build_object(
                        'commit_allowed', false,
                        'reason', 'non_user_assertion_cannot_become_user_memory',
                        'governance', 'working-agent-v2.4'
                    )
                )::json
            FROM memory_work_cases AS work_case
            WHERE work_case.id = decision.case_id
              AND work_case.user_id = decision.user_id
              AND work_case.case_metadata ->> 'automatic_disposition_version' = '028'
              AND decision.state = 'DISCARDED'
              AND decision.policy_result ->> 'reason'
                  = 'legacy_agent_assertion_archived'
            """
        )
    )
    bind.execute(
        sa.text(
            """
            UPDATE memory_work_cases
            SET status = 'awaiting_evidence',
                resolved_at = NULL,
                updated_at = now(),
                case_metadata = (
                    COALESCE(case_metadata, '{}'::json)::jsonb
                    - 'automatic_disposition'
                    - 'automatic_disposition_version'
                )::json
            WHERE case_metadata ->> 'automatic_disposition_version' = '028'
              AND status = 'discarded'
            """
        )
    )
