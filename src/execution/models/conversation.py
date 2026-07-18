"""Database-authoritative conversation ledger and reflection records."""
from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)

from src.shared.db.database import Base


class ConversationTurn(Base):
    __tablename__ = "conversation_turns"

    id = Column(String(64), primary_key=True)
    session_id = Column(
        String(64),
        ForeignKey("agent_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(String(64), nullable=False, index=True)
    channel = Column(String(32), nullable=False)
    channel_message_id = Column(String(128), nullable=True)
    role = Column(String(16), nullable=False)
    content = Column(Text, nullable=False)
    reply_to_turn_id = Column(
        String(64),
        ForeignKey("conversation_turns.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    sensitivity = Column(String(16), nullable=False, default="normal")
    reflection_state = Column(String(16), nullable=False, default="pending")
    turn_metadata = Column(JSON, nullable=False, default=dict)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "channel",
            "channel_message_id",
            name="uq_conversation_turn_channel_message",
        ),
        UniqueConstraint(
            "reply_to_turn_id",
            name="uq_conversation_turn_single_reply",
        ),
        Index(
            "ix_conversation_turn_session_created",
            "session_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_conversation_turn_user_role_created",
            "user_id",
            "role",
            "created_at",
        ),
    )


class ConversationEpisode(Base):
    __tablename__ = "conversation_episodes"

    id = Column(String(64), primary_key=True)
    session_id = Column(
        String(64),
        ForeignKey("agent_sessions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    user_id = Column(String(64), nullable=False, index=True)
    start_turn_id = Column(
        String(64),
        ForeignKey("conversation_turns.id", ondelete="SET NULL"),
        nullable=True,
    )
    end_turn_id = Column(
        String(64),
        ForeignKey("conversation_turns.id", ondelete="SET NULL"),
        nullable=True,
    )
    summary = Column(Text, nullable=False)
    topics = Column(JSON, nullable=False, default=list)
    emotional_context = Column(Text, nullable=True)
    open_loops = Column(JSON, nullable=False, default=list)
    asked_questions = Column(JSON, nullable=False, default=list)
    declined_questions = Column(JSON, nullable=False, default=list)
    memory_signals = Column(JSON, nullable=False, default=list)
    source_turn_ids = Column(JSON, nullable=False, default=list)
    status = Column(String(24), nullable=False, default="active")
    reflection_version = Column(String(32), nullable=False, default="conversation-reflection-v1")
    working_state = Column(String(24), nullable=False, default="not_dispatched")
    handoff_ids = Column(JSON, nullable=False, default=list)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "end_turn_id",
            name="uq_conversation_episode_reflection_boundary",
        ),
        Index(
            "ix_conversation_episode_user_created",
            "user_id",
            "created_at",
        ),
        Index(
            "ix_conversation_episode_user_status",
            "user_id",
            "status",
        ),
    )


class ConversationReflectionCursor(Base):
    __tablename__ = "conversation_reflection_cursors"

    id = Column(String(64), primary_key=True)
    session_id = Column(
        String(64),
        ForeignKey("agent_sessions.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    user_id = Column(String(64), nullable=False, index=True)
    last_reflected_turn_id = Column(
        String(64),
        ForeignKey("conversation_turns.id", ondelete="SET NULL"),
        nullable=True,
    )
    pending_user_turns = Column(Integer, nullable=False, default=0)
    next_reflection_at = Column(DateTime(timezone=True), nullable=True, index=True)
    last_reflected_at = Column(DateTime(timezone=True), nullable=True)
    attempts = Column(Integer, nullable=False, default=0)
    error = Column(String(256), nullable=True)
    running = Column(Boolean, nullable=False, default=False)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        Index(
            "ix_conversation_reflection_due",
            "running",
            "next_reflection_at",
        ),
    )


class ConversationAttentionCandidate(Base):
    __tablename__ = "conversation_attention_candidates"

    id = Column(String(64), primary_key=True)
    user_id = Column(String(64), nullable=False, index=True)
    session_id = Column(
        String(64),
        ForeignKey("agent_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    episode_id = Column(
        String(64),
        ForeignKey("conversation_episodes.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    kind = Column(String(32), nullable=False, default="follow_up")
    prompt = Column(Text, nullable=False)
    value_score = Column(Float, nullable=False, default=0.0)
    source = Column(String(32), nullable=False, default="reflection")
    sensitivity = Column(String(16), nullable=False, default="normal")
    status = Column(String(24), nullable=False, default="pending", index=True)
    due_at = Column(DateTime(timezone=True), nullable=False, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    responded_at = Column(DateTime(timezone=True), nullable=True)
    cooldown_until = Column(DateTime(timezone=True), nullable=True)
    source_turn_ids = Column(JSON, nullable=False, default=list)
    proactive_allowed = Column(Boolean, nullable=False, default=True)
    candidate_metadata = Column(JSON, nullable=False, default=dict)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        Index(
            "ix_conversation_attention_due",
            "status",
            "proactive_allowed",
            "due_at",
        ),
        Index(
            "ix_conversation_attention_user_sent",
            "user_id",
            "sent_at",
        ),
    )
