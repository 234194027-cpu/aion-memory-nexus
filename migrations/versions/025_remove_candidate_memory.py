"""Replace candidate review with autonomous Working-Agent memory commits.

Revision ID: 025
Revises: 024
"""
from __future__ import annotations

import json

from alembic import op
import sqlalchemy as sa


revision = "025"
down_revision = "024"
branch_labels = None
depends_on = None


def _json(value: object, fallback: object) -> object:
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return fallback
        return parsed if isinstance(parsed, type(fallback)) else fallback
    return fallback


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    committed_columns = {item["name"] for item in inspector.get_columns("committed_memories")}
    with op.batch_alter_table("committed_memories") as batch:
        if "source_work_case_id" not in committed_columns:
            batch.add_column(sa.Column("source_work_case_id", sa.String(length=64), nullable=True))
            batch.create_foreign_key(
                "fk_committed_memory_work_case",
                "memory_work_cases",
                ["source_work_case_id"],
                ["id"],
                ondelete="SET NULL",
            )
            batch.create_index("ix_committed_memories_source_work_case_id", ["source_work_case_id"])
        if "source_work_decision_id" not in committed_columns:
            batch.add_column(sa.Column("source_work_decision_id", sa.String(length=64), nullable=True))
            batch.create_foreign_key(
                "fk_committed_memory_work_decision",
                "memory_work_decisions",
                ["source_work_decision_id"],
                ["id"],
                ondelete="SET NULL",
            )
            batch.create_index("ix_committed_memories_source_work_decision_id", ["source_work_decision_id"], unique=True)
        if "origin_kind" not in committed_columns:
            batch.add_column(sa.Column("origin_kind", sa.String(length=32), nullable=False, server_default="manual_legacy"))
        if "revision" not in committed_columns:
            batch.add_column(sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))
        if "automation_metadata" not in committed_columns:
            batch.add_column(sa.Column("automation_metadata", sa.JSON(), nullable=False, server_default="{}"))

    case_columns = {item["name"] for item in sa.inspect(bind).get_columns("memory_work_cases")}
    with op.batch_alter_table("memory_work_cases") as batch:
        if "active_memory_id" not in case_columns:
            batch.add_column(sa.Column("active_memory_id", sa.String(length=64), nullable=True))
            batch.create_foreign_key(
                "fk_memory_work_case_active_memory",
                "committed_memories",
                ["active_memory_id"],
                ["id"],
                ondelete="SET NULL",
            )
            batch.create_index("ix_memory_work_cases_active_memory_id", ["active_memory_id"])

    decision_columns = {item["name"] for item in sa.inspect(bind).get_columns("memory_work_decisions")}
    with op.batch_alter_table("memory_work_decisions") as batch:
        if "memory_ids" not in decision_columns:
            batch.add_column(sa.Column("memory_ids", sa.JSON(), nullable=False, server_default="[]"))

    candidates = {
        row["id"]: row
        for row in bind.execute(sa.text("SELECT * FROM candidate_memories")).mappings()
    }
    memories = list(
        bind.execute(
            sa.text("SELECT id, candidate_id FROM committed_memories WHERE candidate_id IS NOT NULL")
        ).mappings()
    )
    memory_by_candidate = {row["candidate_id"]: row["id"] for row in memories}

    for candidate_id, row in candidates.items():
        memory_id = memory_by_candidate.get(candidate_id)
        work_case_id = row.get("work_case_id")
        decision_id = row.get("source_decision_id")
        if memory_id:
            bind.execute(
                sa.text(
                    """
                    UPDATE committed_memories
                    SET source_work_case_id=:case_id,
                        source_work_decision_id=:decision_id,
                        origin_kind=:origin_kind,
                        revision=:revision,
                        automation_metadata=:metadata
                    WHERE id=:memory_id
                    """
                ),
                {
                    "case_id": work_case_id,
                    "decision_id": decision_id,
                    "origin_kind": "working_agent" if row.get("origin_kind") == "working_agent" else "manual_legacy",
                    "revision": int(row.get("revision") or 1),
                    "metadata": json.dumps(
                        {"migration": "025", "legacy_candidate_id": candidate_id}
                    ),
                    "memory_id": memory_id,
                },
            )

        if work_case_id:
            status = str(row.get("status") or "")
            if memory_id:
                case_status = "resolved"
            elif status == "needs_more_evidence":
                case_status = "awaiting_evidence"
            elif status in {"rejected", "deleted"}:
                case_status = "discarded"
            else:
                case_status = "ready_to_commit"
            bind.execute(
                sa.text(
                    """
                    UPDATE memory_work_cases
                    SET active_memory_id=:memory_id,
                        status=:status,
                        case_metadata=:metadata
                    WHERE id=:case_id
                    """
                ),
                {
                    "memory_id": memory_id,
                    "status": case_status,
                    "metadata": json.dumps(
                        {
                            **dict(_json(row.get("case_metadata"), {})),
                            "migration": "025",
                            "legacy_candidate_id": candidate_id,
                        }
                    ),
                    "case_id": work_case_id,
                },
            )

        if decision_id:
            policy = dict(_json(row.get("policy_result"), {}))
            policy.update(
                {
                    "governance": "working-agent-v2.4",
                    "commit_allowed": bool(memory_id),
                    "reason": "migrated_committed" if memory_id else "migrated_unresolved",
                    "memory_proposal": {
                        "memory_type": row.get("memory_type") or "fact",
                        "title": row.get("title") or "",
                        "content": row.get("body") or "",
                        "importance": float(row.get("importance_score") or 0.0),
                        "confidence": float(row.get("confidence_score") or 0.0),
                        "sensitivity": row.get("sensitivity") or "normal",
                        "entities": list(_json(row.get("tags"), [])),
                    },
                    "migration": "025",
                }
            )
            state = "MEMORY_READY"
            if row.get("status") == "needs_more_evidence":
                state = "NEEDS_MORE_EVIDENCE"
            elif row.get("status") in {"rejected", "deleted"}:
                state = "DISCARDED"
            bind.execute(
                sa.text(
                    """
                    UPDATE memory_work_decisions
                    SET state=:state, memory_ids=:memory_ids, policy_result=:policy
                    WHERE id=:decision_id
                    """
                ),
                {
                    "state": state,
                    "memory_ids": json.dumps([memory_id] if memory_id else []),
                    "policy": json.dumps(policy, ensure_ascii=False),
                    "decision_id": decision_id,
                },
            )

    bind.execute(
        sa.text(
            "UPDATE memory_work_decisions SET state='MEMORY_READY' WHERE state='CANDIDATE_READY'"
        )
    )

    with op.batch_alter_table("memory_work_cases") as batch:
        if "active_candidate_id" in case_columns:
            batch.drop_index("ix_memory_work_cases_active_candidate_id")
            batch.drop_column("active_candidate_id")
    with op.batch_alter_table("memory_work_decisions") as batch:
        if "candidate_ids" in decision_columns:
            batch.drop_column("candidate_ids")
    with op.batch_alter_table("committed_memories") as batch:
        if "candidate_id" in committed_columns:
            batch.drop_column("candidate_id")

    op.drop_table("candidate_memories")


def downgrade() -> None:
    bind = op.get_bind()

    op.create_table(
        "candidate_memories",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("raw_event_ids", sa.JSON(), nullable=False),
        sa.Column("memory_type", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("importance_score", sa.Float(), nullable=True),
        sa.Column("confidence_score", sa.Float(), nullable=True),
        sa.Column("sensitivity", sa.String(), nullable=True),
        sa.Column("epistemic_status", sa.String(length=32), nullable=False, server_default="legacy_unclassified"),
        sa.Column("proposed_action", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("conflict_memory_ids", sa.JSON(), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by", sa.String(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=64), nullable=True),
        sa.Column("work_case_id", sa.String(length=64), sa.ForeignKey("memory_work_cases.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_decision_id", sa.String(length=64), sa.ForeignKey("memory_work_decisions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_run_id", sa.String(length=64), sa.ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("proposition_key", sa.String(length=64), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("origin_kind", sa.String(length=32), nullable=False, server_default="legacy"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_candidate_user_idempotency"),
    )
    for name, columns in (
        ("ix_candidate_memories_id", ["id"]),
        ("ix_candidate_memories_idempotency_key", ["idempotency_key"]),
        ("ix_candidate_memories_work_case_id", ["work_case_id"]),
        ("ix_candidate_memories_source_decision_id", ["source_decision_id"]),
        ("ix_candidate_memories_source_run_id", ["source_run_id"]),
        ("ix_candidate_memories_proposition_key", ["proposition_key"]),
        ("ix_candidate_user_status_created", ["user_id", "status", "created_at"]),
    ):
        op.create_index(name, "candidate_memories", columns)

    with op.batch_alter_table("committed_memories") as batch:
        batch.add_column(sa.Column("candidate_id", sa.String(), nullable=True))
        batch.create_foreign_key(
            "fk_committed_memory_candidate",
            "candidate_memories",
            ["candidate_id"],
            ["id"],
        )
    with op.batch_alter_table("memory_work_cases") as batch:
        batch.add_column(sa.Column("active_candidate_id", sa.String(length=64), nullable=True))
        batch.create_foreign_key(
            "fk_memory_work_case_active_candidate",
            "candidate_memories",
            ["active_candidate_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_memory_work_cases_active_candidate_id", ["active_candidate_id"])
    with op.batch_alter_table("memory_work_decisions") as batch:
        batch.add_column(sa.Column("candidate_ids", sa.JSON(), nullable=False, server_default="[]"))

    rows = list(
        bind.execute(
            sa.text(
                """
                SELECT id, user_id, memory_type, title, body, importance, confidence,
                       sensitivity, epistemic_status, source_work_case_id,
                       source_work_decision_id, revision, origin_kind, created_at
                FROM committed_memories
                WHERE source_work_case_id IS NOT NULL OR source_work_decision_id IS NOT NULL
                """
            )
        ).mappings()
    )
    for row in rows:
        candidate_id = f"legacy_{row['id']}"[:64]
        bind.execute(
            sa.text(
                """
                INSERT INTO candidate_memories
                    (id, user_id, raw_event_ids, memory_type, title, body,
                     importance_score, confidence_score, sensitivity,
                     epistemic_status, proposed_action, status, rationale,
                     conflict_memory_ids, tags, idempotency_key, work_case_id,
                     source_decision_id, revision, origin_kind, created_at)
                VALUES
                    (:id, :user_id, :raw_event_ids, :memory_type, :title, :body,
                     :importance, :confidence, :sensitivity, :epistemic_status,
                     'review', 'accepted', 'Restored by migration 025 downgrade',
                     :conflicts, :tags, NULL, :case_id, :decision_id, :revision,
                     :origin_kind, :created_at)
                """
            ),
            {
                "id": candidate_id,
                "user_id": row["user_id"],
                "raw_event_ids": json.dumps([]),
                "memory_type": row["memory_type"],
                "title": row["title"],
                "body": row["body"],
                "importance": row["importance"],
                "confidence": row["confidence"],
                "sensitivity": row["sensitivity"],
                "epistemic_status": row["epistemic_status"],
                "conflicts": json.dumps([]),
                "tags": json.dumps([]),
                "case_id": row["source_work_case_id"],
                "decision_id": row["source_work_decision_id"],
                "revision": row["revision"] or 1,
                "origin_kind": row["origin_kind"] or "legacy",
                "created_at": row["created_at"],
            },
        )
        bind.execute(
            sa.text("UPDATE committed_memories SET candidate_id=:candidate_id WHERE id=:memory_id"),
            {"candidate_id": candidate_id, "memory_id": row["id"]},
        )
        if row["source_work_case_id"]:
            bind.execute(
                sa.text("UPDATE memory_work_cases SET active_candidate_id=:candidate_id WHERE id=:case_id"),
                {"candidate_id": candidate_id, "case_id": row["source_work_case_id"]},
            )
        if row["source_work_decision_id"]:
            bind.execute(
                sa.text("UPDATE memory_work_decisions SET candidate_ids=:ids WHERE id=:decision_id"),
                {"ids": json.dumps([candidate_id]), "decision_id": row["source_work_decision_id"]},
            )

    with op.batch_alter_table("memory_work_cases") as batch:
        batch.drop_index("ix_memory_work_cases_active_memory_id")
        batch.drop_constraint("fk_memory_work_case_active_memory", type_="foreignkey")
        batch.drop_column("active_memory_id")
    with op.batch_alter_table("memory_work_decisions") as batch:
        batch.drop_column("memory_ids")
    with op.batch_alter_table("committed_memories") as batch:
        batch.drop_index("ix_committed_memories_source_work_case_id")
        batch.drop_index("ix_committed_memories_source_work_decision_id")
        batch.drop_constraint("fk_committed_memory_work_case", type_="foreignkey")
        batch.drop_constraint("fk_committed_memory_work_decision", type_="foreignkey")
        for column in (
            "automation_metadata",
            "revision",
            "origin_kind",
            "source_work_decision_id",
            "source_work_case_id",
        ):
            batch.drop_column(column)
