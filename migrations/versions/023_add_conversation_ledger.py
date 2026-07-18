"""Add the database-authoritative conversation ledger and retire question sessions.

Revision ID: 023
Revises: 022
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import re
import uuid

from alembic import op
import sqlalchemy as sa


revision = "023"
down_revision = "022"
branch_labels = None
depends_on = None


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _as_list(value: object) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _add_conversation_source_type() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    source_type = bind.execute(
        sa.text(
            """
            SELECT type_info.typname, type_info.typtype
            FROM pg_attribute column_info
            JOIN pg_class table_info ON table_info.oid = column_info.attrelid
            JOIN pg_namespace schema_info ON schema_info.oid = table_info.relnamespace
            JOIN pg_type type_info ON type_info.oid = column_info.atttypid
            WHERE table_info.relname = 'raw_events'
              AND column_info.attname = 'source_type'
              AND column_info.attnum > 0
              AND NOT column_info.attisdropped
              AND schema_info.nspname = ANY (current_schemas(false))
            LIMIT 1
            """
        )
    ).mappings().first()
    if not source_type or source_type["typtype"] != "e":
        # Older production schemas store SQLAlchemy enums as VARCHAR. The new
        # value needs no DDL in that shape.
        return
    type_name = str(source_type["typname"])
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", type_name):
        raise RuntimeError("unsafe PostgreSQL enum type name")
    # PostgreSQL enum values are append-only here. Keeping the value after a
    # downgrade avoids a table rewrite and is harmless.
    op.execute(
        f'ALTER TYPE "{type_name}" ADD VALUE IF NOT EXISTS \'conversation\''
    )


def upgrade() -> None:
    bind = op.get_bind()
    existing = _tables()
    _add_conversation_source_type()

    if "conversation_turns" not in existing:
        op.create_table(
            "conversation_turns",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column(
                "session_id",
                sa.String(length=64),
                sa.ForeignKey("agent_sessions.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("user_id", sa.String(length=64), nullable=False),
            sa.Column("channel", sa.String(length=32), nullable=False),
            sa.Column("channel_message_id", sa.String(length=128), nullable=True),
            sa.Column("role", sa.String(length=16), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column(
                "reply_to_turn_id",
                sa.String(length=64),
                sa.ForeignKey("conversation_turns.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("sensitivity", sa.String(length=16), nullable=False, server_default="normal"),
            sa.Column("reflection_state", sa.String(length=16), nullable=False, server_default="pending"),
            sa.Column("turn_metadata", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "user_id",
                "channel",
                "channel_message_id",
                name="uq_conversation_turn_channel_message",
            ),
            sa.UniqueConstraint(
                "reply_to_turn_id",
                name="uq_conversation_turn_single_reply",
            ),
        )
        op.create_index("ix_conversation_turns_session_id", "conversation_turns", ["session_id"])
        op.create_index("ix_conversation_turns_user_id", "conversation_turns", ["user_id"])
        op.create_index("ix_conversation_turns_reply_to_turn_id", "conversation_turns", ["reply_to_turn_id"])
        op.create_index("ix_conversation_turns_created_at", "conversation_turns", ["created_at"])
        op.create_index("ix_conversation_turn_session_created", "conversation_turns", ["session_id", "created_at", "id"])
        op.create_index("ix_conversation_turn_user_role_created", "conversation_turns", ["user_id", "role", "created_at"])

    if "conversation_episodes" not in existing:
        op.create_table(
            "conversation_episodes",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column(
                "session_id",
                sa.String(length=64),
                sa.ForeignKey("agent_sessions.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("user_id", sa.String(length=64), nullable=False),
            sa.Column(
                "start_turn_id",
                sa.String(length=64),
                sa.ForeignKey("conversation_turns.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "end_turn_id",
                sa.String(length=64),
                sa.ForeignKey("conversation_turns.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("summary", sa.Text(), nullable=False),
            sa.Column("topics", sa.JSON(), nullable=False),
            sa.Column("emotional_context", sa.Text(), nullable=True),
            sa.Column("open_loops", sa.JSON(), nullable=False),
            sa.Column("asked_questions", sa.JSON(), nullable=False),
            sa.Column("declined_questions", sa.JSON(), nullable=False),
            sa.Column("memory_signals", sa.JSON(), nullable=False),
            sa.Column("source_turn_ids", sa.JSON(), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="active"),
            sa.Column(
                "reflection_version",
                sa.String(length=32),
                nullable=False,
                server_default="conversation-reflection-v1",
            ),
            sa.Column("working_state", sa.String(length=24), nullable=False, server_default="not_dispatched"),
            sa.Column("handoff_ids", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "session_id",
                "end_turn_id",
                name="uq_conversation_episode_reflection_boundary",
            ),
        )
        op.create_index("ix_conversation_episodes_session_id", "conversation_episodes", ["session_id"])
        op.create_index("ix_conversation_episodes_user_id", "conversation_episodes", ["user_id"])
        op.create_index("ix_conversation_episodes_created_at", "conversation_episodes", ["created_at"])
        op.create_index("ix_conversation_episode_user_created", "conversation_episodes", ["user_id", "created_at"])
        op.create_index("ix_conversation_episode_user_status", "conversation_episodes", ["user_id", "status"])

    if "conversation_reflection_cursors" not in existing:
        op.create_table(
            "conversation_reflection_cursors",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column(
                "session_id",
                sa.String(length=64),
                sa.ForeignKey("agent_sessions.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("user_id", sa.String(length=64), nullable=False),
            sa.Column(
                "last_reflected_turn_id",
                sa.String(length=64),
                sa.ForeignKey("conversation_turns.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("pending_user_turns", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("next_reflection_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_reflected_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("error", sa.String(length=256), nullable=True),
            sa.Column("running", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("session_id"),
        )
        op.create_index("ix_conversation_reflection_cursors_session_id", "conversation_reflection_cursors", ["session_id"])
        op.create_index("ix_conversation_reflection_cursors_user_id", "conversation_reflection_cursors", ["user_id"])
        op.create_index("ix_conversation_reflection_cursors_next_reflection_at", "conversation_reflection_cursors", ["next_reflection_at"])
        op.create_index("ix_conversation_reflection_due", "conversation_reflection_cursors", ["running", "next_reflection_at"])

    if "conversation_attention_candidates" not in existing:
        op.create_table(
            "conversation_attention_candidates",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("user_id", sa.String(length=64), nullable=False),
            sa.Column(
                "session_id",
                sa.String(length=64),
                sa.ForeignKey("agent_sessions.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "episode_id",
                sa.String(length=64),
                sa.ForeignKey("conversation_episodes.id", ondelete="CASCADE"),
                nullable=True,
            ),
            sa.Column("kind", sa.String(length=32), nullable=False, server_default="follow_up"),
            sa.Column("prompt", sa.Text(), nullable=False),
            sa.Column("value_score", sa.Float(), nullable=False, server_default="0"),
            sa.Column("source", sa.String(length=32), nullable=False, server_default="reflection"),
            sa.Column("sensitivity", sa.String(length=16), nullable=False, server_default="normal"),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="pending"),
            sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("cooldown_until", sa.DateTime(timezone=True), nullable=True),
            sa.Column("source_turn_ids", sa.JSON(), nullable=False),
            sa.Column("proactive_allowed", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("candidate_metadata", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_conversation_attention_candidates_user_id", "conversation_attention_candidates", ["user_id"])
        op.create_index("ix_conversation_attention_candidates_session_id", "conversation_attention_candidates", ["session_id"])
        op.create_index("ix_conversation_attention_candidates_episode_id", "conversation_attention_candidates", ["episode_id"])
        op.create_index("ix_conversation_attention_candidates_status", "conversation_attention_candidates", ["status"])
        op.create_index("ix_conversation_attention_candidates_due_at", "conversation_attention_candidates", ["due_at"])
        op.create_index("ix_conversation_attention_candidates_created_at", "conversation_attention_candidates", ["created_at"])
        op.create_index("ix_conversation_attention_due", "conversation_attention_candidates", ["status", "proactive_allowed", "due_at"])
        op.create_index("ix_conversation_attention_user_sent", "conversation_attention_candidates", ["user_id", "sent_at"])

    sessions = sa.table(
        "agent_sessions",
        sa.column("id", sa.String()),
        sa.column("user_id", sa.String()),
        sa.column("agent_role", sa.String()),
        sa.column("channel", sa.String()),
        sa.column("context_payload", sa.JSON()),
        sa.column("context_version", sa.String()),
        sa.column("started_at", sa.DateTime(timezone=True)),
    )
    turns = sa.table(
        "conversation_turns",
        sa.column("id", sa.String()),
        sa.column("session_id", sa.String()),
        sa.column("user_id", sa.String()),
        sa.column("channel", sa.String()),
        sa.column("channel_message_id", sa.String()),
        sa.column("role", sa.String()),
        sa.column("content", sa.Text()),
        sa.column("reply_to_turn_id", sa.String()),
        sa.column("sensitivity", sa.String()),
        sa.column("reflection_state", sa.String()),
        sa.column("turn_metadata", sa.JSON()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    for session_row in bind.execute(
        sa.select(sessions).where(sessions.c.agent_role == "conversational")
    ).mappings():
        payload = session_row.get("context_payload")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (TypeError, ValueError):
                payload = {}
        messages = payload.get("messages", []) if isinstance(payload, dict) else []
        started_at = session_row.get("started_at") or datetime.now(timezone.utc)
        for index, message in enumerate(messages if isinstance(messages, list) else []):
            if not isinstance(message, dict):
                continue
            role = message.get("role")
            content = message.get("content")
            if role not in {"user", "assistant"} or not isinstance(content, str) or not content:
                continue
            bind.execute(
                turns.insert().values(
                    id=_id("ctn"),
                    session_id=session_row["id"],
                    user_id=session_row["user_id"],
                    channel=session_row.get("channel") or "system",
                    channel_message_id=None,
                    role=role,
                    content=content[:16000],
                    reply_to_turn_id=None,
                    sensitivity="normal",
                    reflection_state="pending",
                    turn_metadata={"migrated_from": "agent_session_context"},
                    created_at=started_at + timedelta(microseconds=index),
                )
            )
        bind.execute(
            sessions.update()
            .where(sessions.c.id == session_row["id"])
            .values(context_payload=None, context_version="conv-ledger-v1")
        )

    if "wecom_contacts" in existing:
        contacts = sa.table(
            "wecom_contacts",
            sa.column("id", sa.String()),
            sa.column("contact_metadata", sa.JSON()),
        )
        for contact_row in bind.execute(sa.select(contacts)).mappings():
            metadata = contact_row.get("contact_metadata")
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except (TypeError, ValueError):
                    metadata = {}
            metadata = dict(metadata or {})
            old_preferences = dict(metadata.pop("memory_question_preferences", {}) or {})
            metadata.pop("agent_interaction_mode", None)
            metadata.pop("questioning_profile", None)
            metadata.pop("daily_question_pause_until", None)
            metadata.pop("daily_question_pause_reason", None)
            metadata.pop("daily_question_unanswered_count", None)
            metadata.pop("daily_question_reminder_date", None)
            if old_preferences:
                mode = old_preferences.get("mode")
                metadata["conversation_proactivity"] = {
                    "enabled": old_preferences.get("daily_questioning_enabled") is not False,
                    "quiet_hours_start": old_preferences.get("quiet_hours_start"),
                    "quiet_hours_end": old_preferences.get("quiet_hours_end"),
                    "intensity": "high" if mode in {"deep", "decision"} else "normal",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            bind.execute(
                contacts.update()
                .where(contacts.c.id == contact_row["id"])
                .values(contact_metadata=metadata)
            )

    if "memory_question_sessions" in existing:
        legacy = sa.table(
            "memory_question_sessions",
            sa.column("id", sa.String()),
            sa.column("user_id", sa.String()),
            sa.column("status", sa.String()),
            sa.column("questions", sa.JSON()),
            sa.column("answers", sa.JSON()),
            sa.column("current_index", sa.Integer()),
            sa.column("source_summary", sa.Text()),
            sa.column("created_at", sa.DateTime(timezone=True)),
            sa.column("updated_at", sa.DateTime(timezone=True)),
        )
        episodes = sa.table(
            "conversation_episodes",
            sa.column("id", sa.String()),
            sa.column("session_id", sa.String()),
            sa.column("user_id", sa.String()),
            sa.column("summary", sa.Text()),
            sa.column("topics", sa.JSON()),
            sa.column("emotional_context", sa.Text()),
            sa.column("open_loops", sa.JSON()),
            sa.column("asked_questions", sa.JSON()),
            sa.column("declined_questions", sa.JSON()),
            sa.column("memory_signals", sa.JSON()),
            sa.column("source_turn_ids", sa.JSON()),
            sa.column("status", sa.String()),
            sa.column("reflection_version", sa.String()),
            sa.column("working_state", sa.String()),
            sa.column("handoff_ids", sa.JSON()),
            sa.column("created_at", sa.DateTime(timezone=True)),
            sa.column("updated_at", sa.DateTime(timezone=True)),
        )
        for row in bind.execute(sa.select(legacy)).mappings():
            questions = _as_list(row.get("questions"))
            answers = _as_list(row.get("answers"))
            current_index = max(0, int(row.get("current_index") or 0))
            unanswered = questions[max(len(answers), current_index) :]
            open_loops = [
                {
                    "kind": "legacy_unanswered_question",
                    "text": str(question)[:1000],
                    "proactive_allowed": False,
                    "priority": "low",
                }
                for question in unanswered
                if str(question).strip()
            ]
            now = datetime.now(timezone.utc)
            bind.execute(
                episodes.insert().values(
                    id=_id("cep"),
                    session_id=None,
                    user_id=row["user_id"],
                    summary=(row.get("source_summary") or "历史提问会话归档")[:8000],
                    topics=["legacy_question_session"],
                    emotional_context=None,
                    open_loops=open_loops,
                    asked_questions=questions,
                    declined_questions=[],
                    memory_signals=[],
                    source_turn_ids=[],
                    status="archived",
                    reflection_version="legacy-question-v1",
                    working_state="not_dispatched",
                    handoff_ids=[f"legacy-session:{row['id']}"],
                    created_at=row.get("created_at") or now,
                    updated_at=row.get("updated_at") or now,
                )
            )
        op.drop_table("memory_question_sessions")


def downgrade() -> None:
    bind = op.get_bind()
    existing = _tables()

    if "conversation_turns" in existing and "agent_sessions" in existing:
        sessions = sa.table(
            "agent_sessions",
            sa.column("id", sa.String()),
            sa.column("agent_role", sa.String()),
            sa.column("context_payload", sa.JSON()),
            sa.column("context_version", sa.String()),
        )
        turns = sa.table(
            "conversation_turns",
            sa.column("session_id", sa.String()),
            sa.column("role", sa.String()),
            sa.column("content", sa.Text()),
            sa.column("created_at", sa.DateTime(timezone=True)),
            sa.column("id", sa.String()),
        )
        for session_row in bind.execute(
            sa.select(sessions).where(sessions.c.agent_role == "conversational")
        ).mappings():
            messages = [
                {"role": row["role"], "content": row["content"]}
                for row in bind.execute(
                    sa.select(turns)
                    .where(
                        turns.c.session_id == session_row["id"],
                        turns.c.role.in_(("user", "assistant")),
                    )
                    .order_by(turns.c.created_at.asc(), turns.c.id.asc())
                ).mappings()
            ]
            bind.execute(
                sessions.update()
                .where(sessions.c.id == session_row["id"])
                .values(
                    context_payload={
                        "messages": messages[-20:],
                        "compacted_message_count": max(0, len(messages) - 20),
                    },
                    context_version="runtime-v1",
                )
            )

    if "wecom_contacts" in existing:
        contacts = sa.table(
            "wecom_contacts",
            sa.column("id", sa.String()),
            sa.column("contact_metadata", sa.JSON()),
        )
        for contact_row in bind.execute(sa.select(contacts)).mappings():
            metadata = contact_row.get("contact_metadata")
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except (TypeError, ValueError):
                    metadata = {}
            metadata = dict(metadata or {})
            preferences = dict(metadata.pop("conversation_proactivity", {}) or {})
            if preferences:
                metadata["memory_question_preferences"] = {
                    "daily_questioning_enabled": preferences.get("enabled") is not False,
                    "quiet_hours_start": preferences.get("quiet_hours_start"),
                    "quiet_hours_end": preferences.get("quiet_hours_end"),
                    "mode": "deep" if preferences.get("intensity") == "high" else "relaxed",
                }
            metadata["agent_interaction_mode"] = "chat"
            bind.execute(
                contacts.update()
                .where(contacts.c.id == contact_row["id"])
                .values(contact_metadata=metadata)
            )

    if "memory_question_sessions" not in existing:
        op.create_table(
            "memory_question_sessions",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("user_id", sa.String(), nullable=False),
            sa.Column("wecom_contact_id", sa.String(), nullable=True),
            sa.Column("status", sa.String(), nullable=False, server_default="active"),
            sa.Column("questions", sa.JSON(), nullable=False),
            sa.Column("answers", sa.JSON(), nullable=False),
            sa.Column("current_index", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("source_summary", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_memory_question_sessions_id", "memory_question_sessions", ["id"])
        op.create_index("ix_memory_question_sessions_user_id", "memory_question_sessions", ["user_id"])
        op.create_index("ix_memory_question_sessions_wecom_contact_id", "memory_question_sessions", ["wecom_contact_id"])
        op.create_index("ix_memory_question_sessions_status", "memory_question_sessions", ["status"])
        op.create_index("ix_question_sessions_user_status", "memory_question_sessions", ["user_id", "status"])
        op.create_index("ix_question_sessions_contact_status", "memory_question_sessions", ["wecom_contact_id", "status"])

        legacy = sa.table(
            "memory_question_sessions",
            sa.column("id", sa.String()),
            sa.column("user_id", sa.String()),
            sa.column("wecom_contact_id", sa.String()),
            sa.column("status", sa.String()),
            sa.column("questions", sa.JSON()),
            sa.column("answers", sa.JSON()),
            sa.column("current_index", sa.Integer()),
            sa.column("source_summary", sa.Text()),
            sa.column("created_at", sa.DateTime(timezone=True)),
            sa.column("updated_at", sa.DateTime(timezone=True)),
            sa.column("completed_at", sa.DateTime(timezone=True)),
        )
        episodes = sa.table(
            "conversation_episodes",
            sa.column("id", sa.String()),
            sa.column("user_id", sa.String()),
            sa.column("summary", sa.Text()),
            sa.column("asked_questions", sa.JSON()),
            sa.column("handoff_ids", sa.JSON()),
            sa.column("created_at", sa.DateTime(timezone=True)),
            sa.column("updated_at", sa.DateTime(timezone=True)),
            sa.column("reflection_version", sa.String()),
        )
        rows = bind.execute(
            sa.select(episodes).where(episodes.c.reflection_version == "legacy-question-v1")
        ).mappings()
        for row in rows:
            handoffs = _as_list(row.get("handoff_ids"))
            marker = next(
                (item for item in handoffs if isinstance(item, str) and item.startswith("legacy-session:")),
                None,
            )
            questions = _as_list(row.get("asked_questions"))
            bind.execute(
                legacy.insert().values(
                    id=marker.split(":", 1)[1] if marker else _id("mqs"),
                    user_id=row["user_id"],
                    wecom_contact_id=None,
                    status="archived",
                    questions=questions,
                    answers=[],
                    current_index=len(questions),
                    source_summary=row.get("summary"),
                    created_at=row.get("created_at"),
                    updated_at=row.get("updated_at"),
                    completed_at=row.get("updated_at"),
                )
            )

    for table in (
        "conversation_attention_candidates",
        "conversation_reflection_cursors",
        "conversation_episodes",
        "conversation_turns",
    ):
        if table in _tables():
            op.drop_table(table)
