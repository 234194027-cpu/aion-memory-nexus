"""Recover source evidence for legacy Working-Agent cases.

Revision ID: 027
Revises: 026
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "027"
down_revision = "026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    # This is a data-only recovery migration over PostgreSQL JSONB fields. The
    # development/CI SQLite chain has no legacy PostgreSQL rows to recover and
    # cannot execute the JSONB operators below, so keep its schema round-trip
    # portable while preserving the production recovery path.
    if bind.dialect.name != "postgresql":
        return

    # Migration 024 kept the first legacy RawEvent on the decision, while 025
    # removed the review candidate. Rebuild the evidence edge from those two
    # durable sources so autonomous governance can resume without inventing a
    # fact or requiring a human review step.
    bind.execute(
        sa.text(
            """
            INSERT INTO memory_work_evidence (
                id,
                case_id,
                user_id,
                raw_event_id,
                source_turn_id,
                episode_id,
                quote,
                relationship,
                source_type,
                trust_class,
                occurred_at,
                evidence_metadata
            )
            SELECT
                'mwe_' || substr(
                    md5(d.case_id || ':' || d.source_event_id || ':' || 'migration-027'),
                    1,
                    16
                ),
                d.case_id,
                d.user_id,
                event.id,
                COALESCE(
                    event.event_metadata ->> 'source_turn_id',
                    event.event_metadata ->> 'turn_id'
                ),
                event.event_metadata ->> 'episode_id',
                left(event.content, 8000),
                'supports',
                event.source_type::text,
                CASE
                    WHEN event.source_type::text IN ('conversation', 'manual')
                        THEN 'user_asserted'
                    WHEN event.source_type::text IN ('obsidian', 'file_import')
                        THEN 'user_imported'
                    ELSE 'legacy_source'
                END,
                event.occurred_at,
                json_build_object(
                    'migration', '027',
                    'recovered_from', 'decision_source_event'
                )
            FROM memory_work_decisions AS d
            JOIN memory_work_cases AS c
              ON c.id = d.case_id
             AND c.user_id = d.user_id
            JOIN raw_events AS event
              ON event.id = d.source_event_id
             AND event.user_id = d.user_id
            WHERE d.source_event_id IS NOT NULL
              AND d.state = 'MEMORY_READY'
              AND c.status = 'awaiting_evidence'
              AND c.case_metadata ->> 'migration' = '025'
              AND COALESCE(d.policy_result ->> 'reason', '') = 'evidence_missing'
              AND NOT EXISTS (
                  SELECT 1
                  FROM memory_work_evidence AS existing
                  WHERE existing.case_id = d.case_id
                    AND existing.raw_event_id = d.source_event_id
                    AND existing.relationship = 'supports'
              )
            ON CONFLICT (case_id, raw_event_id, relationship) DO NOTHING
            """
        )
    )

    bind.execute(
        sa.text(
            """
            UPDATE memory_work_cases AS c
            SET status = 'ready_to_commit',
                updated_at = now(),
                case_metadata = (
                    COALESCE(c.case_metadata, '{}'::json)::jsonb
                    || jsonb_build_object('evidence_recovered_by', '027')
                )::json
            WHERE c.status = 'awaiting_evidence'
              AND c.case_metadata ->> 'migration' = '025'
              AND EXISTS (
                  SELECT 1
                  FROM memory_work_decisions AS d
                  JOIN memory_work_evidence AS evidence
                    ON evidence.case_id = d.case_id
                   AND evidence.raw_event_id = d.source_event_id
                  WHERE d.case_id = c.id
                    AND d.user_id = c.user_id
                    AND d.state = 'MEMORY_READY'
                    AND COALESCE(d.policy_result ->> 'reason', '') = 'evidence_missing'
                    AND evidence.evidence_metadata ->> 'migration' = '027'
              )
            """
        )
    )

    bind.execute(
        sa.text(
            """
            UPDATE memory_work_decisions AS d
            SET policy_result = (
                COALESCE(d.policy_result, '{}'::json)::jsonb
                || jsonb_build_object(
                    'commit_allowed', true,
                    'reason', 'evidence_recovered'
                )
            )::json
            FROM memory_work_cases AS c
            WHERE c.id = d.case_id
              AND c.user_id = d.user_id
              AND c.status = 'ready_to_commit'
              AND c.case_metadata ->> 'evidence_recovered_by' = '027'
              AND d.state = 'MEMORY_READY'
              AND EXISTS (
                  SELECT 1
                  FROM memory_work_evidence AS evidence
                  WHERE evidence.case_id = d.case_id
                    AND evidence.raw_event_id = d.source_event_id
                    AND evidence.evidence_metadata ->> 'migration' = '027'
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
            UPDATE memory_work_decisions AS d
            SET policy_result = (
                COALESCE(d.policy_result, '{}'::json)::jsonb
                || jsonb_build_object(
                    'commit_allowed', false,
                    'reason', 'evidence_missing'
                )
            )::json
            FROM memory_work_cases AS c
            WHERE c.id = d.case_id
              AND c.user_id = d.user_id
              AND c.case_metadata ->> 'migration' = '025'
              AND d.state = 'MEMORY_READY'
              AND COALESCE(d.policy_result ->> 'reason', '') = 'evidence_recovered'
            """
        )
    )
    bind.execute(
        sa.text(
            """
            UPDATE memory_work_cases
            SET status = 'awaiting_evidence',
                updated_at = now(),
                case_metadata = (
                    COALESCE(case_metadata, '{}'::json)::jsonb
                    - 'evidence_recovered_by'
                )::json
            WHERE case_metadata ->> 'evidence_recovered_by' = '027'
              AND status = 'ready_to_commit'
            """
        )
    )
    bind.execute(
        sa.text(
            """
            DELETE FROM memory_work_evidence
            WHERE evidence_metadata ->> 'migration' = '027'
              AND evidence_metadata ->> 'recovered_from' = 'decision_source_event'
            """
        )
    )
