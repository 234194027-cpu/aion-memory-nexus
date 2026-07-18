"""Add the V2.2 Working Agent memory case and evidence ledger.

Revision ID: 024
Revises: 023
"""
from __future__ import annotations

import hashlib
import json
import uuid

from alembic import op
import sqlalchemy as sa


revision = "024"
down_revision = "023"
branch_labels = None
depends_on = None


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _json_list(value: object) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _proposition_key(memory_type: object, title: object, body: object) -> str:
    normalized = " ".join(f"{memory_type or 'fact'} {title or ''} {body or ''}".lower().split())
    return hashlib.sha256(normalized[:2000].encode("utf-8")).hexdigest()


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "memory_work_cases" not in tables:
        op.create_table(
            "memory_work_cases",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("user_id", sa.String(length=64), nullable=False),
            sa.Column("proposition_key", sa.String(length=64), nullable=False),
            sa.Column("case_type", sa.String(length=32), nullable=False, server_default="fact"),
            sa.Column("title", sa.String(length=240), nullable=False),
            sa.Column("summary", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="open"),
            sa.Column("sensitivity", sa.String(length=16), nullable=False, server_default="normal"),
            sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
            sa.Column(
                "active_candidate_id",
                sa.String(length=64),
                sa.ForeignKey("candidate_memories.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("case_metadata", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", "proposition_key", name="uq_memory_work_case_user_proposition"),
        )
        op.create_index("ix_memory_work_cases_user_id", "memory_work_cases", ["user_id"])
        op.create_index("ix_memory_work_cases_active_candidate_id", "memory_work_cases", ["active_candidate_id"])
        op.create_index("ix_memory_work_case_user_status", "memory_work_cases", ["user_id", "status", "updated_at"])

    if "memory_work_evidence" not in tables:
        op.create_table(
            "memory_work_evidence",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column(
                "case_id",
                sa.String(length=64),
                sa.ForeignKey("memory_work_cases.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("user_id", sa.String(length=64), nullable=False),
            sa.Column(
                "raw_event_id",
                sa.String(length=64),
                sa.ForeignKey("raw_events.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("source_turn_id", sa.String(length=64), nullable=True),
            sa.Column("episode_id", sa.String(length=64), nullable=True),
            sa.Column("quote", sa.Text(), nullable=True),
            sa.Column("relationship", sa.String(length=24), nullable=False, server_default="supports"),
            sa.Column("source_type", sa.String(length=32), nullable=False),
            sa.Column("trust_class", sa.String(length=32), nullable=False, server_default="unclassified"),
            sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("evidence_metadata", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "case_id",
                "raw_event_id",
                "relationship",
                name="uq_memory_work_evidence_case_event_relation",
            ),
        )
        for name, columns in (
            ("ix_memory_work_evidence_case_id", ["case_id"]),
            ("ix_memory_work_evidence_user_id", ["user_id"]),
            ("ix_memory_work_evidence_raw_event_id", ["raw_event_id"]),
            ("ix_memory_work_evidence_source_turn_id", ["source_turn_id"]),
            ("ix_memory_work_evidence_episode_id", ["episode_id"]),
            ("ix_memory_work_evidence_user_case", ["user_id", "case_id"]),
        ):
            op.create_index(name, "memory_work_evidence", columns)

    if "memory_work_decisions" not in tables:
        op.create_table(
            "memory_work_decisions",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column(
                "case_id",
                sa.String(length=64),
                sa.ForeignKey("memory_work_cases.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("user_id", sa.String(length=64), nullable=False),
            sa.Column(
                "source_run_id",
                sa.String(length=64),
                sa.ForeignKey("agent_runs.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("source_event_id", sa.String(length=64), nullable=True),
            sa.Column("state", sa.String(length=40), nullable=False),
            sa.Column("rationale", sa.Text(), nullable=True),
            sa.Column("rationale_codes", sa.JSON(), nullable=False),
            sa.Column("duplicate_refs", sa.JSON(), nullable=False),
            sa.Column("conflict_refs", sa.JSON(), nullable=False),
            sa.Column("candidate_ids", sa.JSON(), nullable=False),
            sa.Column("policy_result", sa.JSON(), nullable=False),
            sa.Column("model", sa.String(length=128), nullable=True),
            sa.Column("prompt_id", sa.String(length=96), nullable=True),
            sa.Column("prompt_version", sa.String(length=32), nullable=True),
            sa.Column("idempotency_key", sa.String(length=64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("idempotency_key"),
        )
        for name, columns in (
            ("ix_memory_work_decisions_case_id", ["case_id"]),
            ("ix_memory_work_decisions_user_id", ["user_id"]),
            ("ix_memory_work_decisions_source_run_id", ["source_run_id"]),
            ("ix_memory_work_decisions_source_event_id", ["source_event_id"]),
            ("ix_memory_work_decisions_idempotency_key", ["idempotency_key"]),
            ("ix_memory_work_decision_user_state", ["user_id", "state", "created_at"]),
        ):
            op.create_index(name, "memory_work_decisions", columns)

    candidate_columns = {column["name"] for column in sa.inspect(bind).get_columns("candidate_memories")}
    if "idempotency_key" in candidate_columns:
        bind.execute(
            sa.text(
                """
                UPDATE candidate_memories
                SET idempotency_key = NULL
                WHERE id IN (
                    SELECT id FROM (
                        SELECT id,
                               ROW_NUMBER() OVER (
                                   PARTITION BY user_id, idempotency_key
                                   ORDER BY created_at, id
                               ) AS duplicate_rank
                        FROM candidate_memories
                        WHERE idempotency_key IS NOT NULL
                    ) ranked
                    WHERE duplicate_rank > 1
                )
                """
            )
        )
    with op.batch_alter_table("candidate_memories") as batch:
        if "work_case_id" not in candidate_columns:
            batch.add_column(sa.Column("work_case_id", sa.String(length=64), nullable=True))
            batch.create_foreign_key(
                "fk_candidate_work_case",
                "memory_work_cases",
                ["work_case_id"],
                ["id"],
                ondelete="SET NULL",
            )
            batch.create_index("ix_candidate_memories_work_case_id", ["work_case_id"])
        if "source_decision_id" not in candidate_columns:
            batch.add_column(sa.Column("source_decision_id", sa.String(length=64), nullable=True))
            batch.create_foreign_key(
                "fk_candidate_source_decision",
                "memory_work_decisions",
                ["source_decision_id"],
                ["id"],
                ondelete="SET NULL",
            )
            batch.create_index("ix_candidate_memories_source_decision_id", ["source_decision_id"])
        if "source_run_id" not in candidate_columns:
            batch.add_column(sa.Column("source_run_id", sa.String(length=64), nullable=True))
            batch.create_foreign_key(
                "fk_candidate_source_run",
                "agent_runs",
                ["source_run_id"],
                ["id"],
                ondelete="SET NULL",
            )
            batch.create_index("ix_candidate_memories_source_run_id", ["source_run_id"])
        if "proposition_key" not in candidate_columns:
            batch.add_column(sa.Column("proposition_key", sa.String(length=64), nullable=True))
            batch.create_index("ix_candidate_memories_proposition_key", ["proposition_key"])
        if "revision" not in candidate_columns:
            batch.add_column(sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))
        if "origin_kind" not in candidate_columns:
            batch.add_column(sa.Column("origin_kind", sa.String(length=32), nullable=False, server_default="legacy"))
        batch.create_unique_constraint(
            "uq_candidate_user_idempotency",
            ["user_id", "idempotency_key"],
        )

    handoff_columns = {column["name"] for column in sa.inspect(bind).get_columns("agent_handoffs")}
    with op.batch_alter_table("agent_handoffs") as batch:
        if "case_id" not in handoff_columns:
            batch.add_column(sa.Column("case_id", sa.String(length=64), nullable=True))
            batch.create_foreign_key(
                "fk_agent_handoff_case",
                "memory_work_cases",
                ["case_id"],
                ["id"],
                ondelete="CASCADE",
            )
            batch.create_index("ix_agent_handoffs_case_id", ["case_id"])
        if "evidence_requirements" not in handoff_columns:
            batch.add_column(sa.Column("evidence_requirements", sa.JSON(), nullable=False, server_default="[]"))
        if "resolution_condition" not in handoff_columns:
            batch.add_column(sa.Column("resolution_condition", sa.Text(), nullable=True))
        if "sensitivity_limit" not in handoff_columns:
            batch.add_column(sa.Column("sensitivity_limit", sa.String(length=16), nullable=False, server_default="normal"))
        if "attempt_count" not in handoff_columns:
            batch.add_column(sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"))
        if "next_eligible_at" not in handoff_columns:
            batch.add_column(sa.Column("next_eligible_at", sa.DateTime(timezone=True), nullable=True))
        if "asked_at" not in handoff_columns:
            batch.add_column(sa.Column("asked_at", sa.DateTime(timezone=True), nullable=True))
        if "responded_at" not in handoff_columns:
            batch.add_column(sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True))

    candidates = sa.table(
        "candidate_memories",
        sa.column("id", sa.String()),
        sa.column("user_id", sa.String()),
        sa.column("raw_event_ids", sa.JSON()),
        sa.column("memory_type", sa.String()),
        sa.column("title", sa.String()),
        sa.column("body", sa.Text()),
        sa.column("confidence_score", sa.Float()),
        sa.column("sensitivity", sa.String()),
        sa.column("status", sa.String()),
        sa.column("work_case_id", sa.String()),
        sa.column("source_decision_id", sa.String()),
        sa.column("proposition_key", sa.String()),
        sa.column("origin_kind", sa.String()),
    )
    cases = sa.table(
        "memory_work_cases",
        sa.column("id", sa.String()),
        sa.column("user_id", sa.String()),
        sa.column("proposition_key", sa.String()),
        sa.column("case_type", sa.String()),
        sa.column("title", sa.String()),
        sa.column("summary", sa.Text()),
        sa.column("status", sa.String()),
        sa.column("sensitivity", sa.String()),
        sa.column("confidence", sa.Float()),
        sa.column("active_candidate_id", sa.String()),
        sa.column("version", sa.Integer()),
        sa.column("case_metadata", sa.JSON()),
    )
    decisions = sa.table(
        "memory_work_decisions",
        sa.column("id", sa.String()),
        sa.column("case_id", sa.String()),
        sa.column("user_id", sa.String()),
        sa.column("source_event_id", sa.String()),
        sa.column("state", sa.String()),
        sa.column("rationale", sa.Text()),
        sa.column("rationale_codes", sa.JSON()),
        sa.column("duplicate_refs", sa.JSON()),
        sa.column("conflict_refs", sa.JSON()),
        sa.column("candidate_ids", sa.JSON()),
        sa.column("policy_result", sa.JSON()),
        sa.column("prompt_id", sa.String()),
        sa.column("prompt_version", sa.String()),
        sa.column("idempotency_key", sa.String()),
    )
    unresolved = bind.execute(
        sa.select(candidates).where(
            candidates.c.status.in_(("pending", "deferred", "needs_more_evidence"))
        )
    ).mappings()
    for row in unresolved:
        proposition_key = _proposition_key(row["memory_type"], row["title"], row["body"])
        existing_case = bind.execute(
            sa.select(cases.c.id).where(
                cases.c.user_id == row["user_id"],
                cases.c.proposition_key == proposition_key,
            )
        ).scalar_one_or_none()
        case_id = existing_case or _id("mwc")
        if existing_case is None:
            case_status = "awaiting_evidence" if row["status"] == "needs_more_evidence" else "candidate_ready"
            bind.execute(
                cases.insert().values(
                    id=case_id,
                    user_id=row["user_id"],
                    proposition_key=proposition_key,
                    case_type=row["memory_type"] or "fact",
                    title=(row["title"] or "历史候选")[:240],
                    summary=(row["body"] or "")[:2000],
                    status=case_status,
                    sensitivity=row["sensitivity"] or "normal",
                    confidence=float(row["confidence_score"] or 0.0),
                    active_candidate_id=row["id"],
                    version=1,
                    case_metadata={"migrated_from": "candidate_memory", "legacy_candidate_id": row["id"]},
                )
            )
        decision_id = _id("mwd")
        decision_key = hashlib.sha256(f"migration-024:{row['id']}".encode("utf-8")).hexdigest()
        bind.execute(
            decisions.insert().values(
                id=decision_id,
                case_id=case_id,
                user_id=row["user_id"],
                source_event_id=(_json_list(row["raw_event_ids"]) or [None])[0],
                state="MIGRATED_UNRESOLVED",
                rationale="Migration 024 linked an unresolved legacy candidate without reinterpreting its content.",
                rationale_codes=["legacy_unresolved_backfill"],
                duplicate_refs=[],
                conflict_refs=[],
                candidate_ids=[row["id"]],
                policy_result={"review_required": True, "migration": True},
                prompt_id="migration-024",
                prompt_version="v1",
                idempotency_key=decision_key,
            )
        )
        bind.execute(
            candidates.update()
            .where(candidates.c.id == row["id"])
            .values(
                work_case_id=case_id,
                source_decision_id=decision_id,
                proposition_key=proposition_key,
                origin_kind="legacy_migrated",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("agent_handoffs") as batch:
        batch.drop_index("ix_agent_handoffs_case_id")
        batch.drop_constraint("fk_agent_handoff_case", type_="foreignkey")
        for column in (
            "responded_at",
            "asked_at",
            "next_eligible_at",
            "attempt_count",
            "sensitivity_limit",
            "resolution_condition",
            "evidence_requirements",
            "case_id",
        ):
            batch.drop_column(column)

    with op.batch_alter_table("candidate_memories") as batch:
        batch.drop_constraint("uq_candidate_user_idempotency", type_="unique")
        batch.drop_index("ix_candidate_memories_work_case_id")
        batch.drop_index("ix_candidate_memories_source_decision_id")
        batch.drop_index("ix_candidate_memories_source_run_id")
        batch.drop_index("ix_candidate_memories_proposition_key")
        batch.drop_constraint("fk_candidate_work_case", type_="foreignkey")
        batch.drop_constraint("fk_candidate_source_decision", type_="foreignkey")
        batch.drop_constraint("fk_candidate_source_run", type_="foreignkey")
        for column in (
            "origin_kind",
            "revision",
            "proposition_key",
            "source_run_id",
            "source_decision_id",
            "work_case_id",
        ):
            batch.drop_column(column)

    op.drop_table("memory_work_decisions")
    op.drop_table("memory_work_evidence")
    op.drop_table("memory_work_cases")
