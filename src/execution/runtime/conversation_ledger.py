"""Persistence facade for the database-authoritative conversation ledger."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.execution.models.agent_runtime import AgentRole, AgentSession, AgentSessionStatus
from src.execution.models.conversation import (
    ConversationAttentionCandidate,
    ConversationEpisode,
    ConversationReflectionCursor,
    ConversationTurn,
)
from src.shared.ids.id_generator import generate_id


RECENT_TURN_LIMIT = 24
RECENT_EPISODE_DAYS = 7
IDLE_REFLECTION_MINUTES = 10


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ConversationLedger:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_or_create_session(
        self,
        *,
        user_id: str,
        channel: str,
        channel_session_key: str,
    ) -> AgentSession:
        session = (
            await self.db.execute(
                select(AgentSession)
                .where(
                    AgentSession.user_id == user_id,
                    AgentSession.agent_role == AgentRole.CONVERSATIONAL,
                    AgentSession.channel == channel,
                    AgentSession.channel_session_key == channel_session_key,
                    AgentSession.status == AgentSessionStatus.ACTIVE,
                )
                .order_by(AgentSession.updated_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if session is None:
            session = AgentSession(
                id=generate_id("ases"),
                user_id=user_id,
                agent_role=AgentRole.CONVERSATIONAL,
                channel=channel,
                channel_session_key=channel_session_key,
                status=AgentSessionStatus.ACTIVE,
                context_payload=None,
                context_version="conv-ledger-v1",
            )
            self.db.add(session)
            await self.db.flush()
        elif session.context_payload:
            # The ledger owns message content. AgentSession now stores identity
            # and lifecycle only; old bounded payloads are cleared on first use.
            session.context_payload = None
            session.context_version = "conv-ledger-v1"
        return session

    async def reset_session(
        self,
        *,
        user_id: str,
        channel: str,
        channel_session_key: str,
    ) -> None:
        sessions = (
            await self.db.execute(
                select(AgentSession).where(
                    AgentSession.user_id == user_id,
                    AgentSession.agent_role == AgentRole.CONVERSATIONAL,
                    AgentSession.channel == channel,
                    AgentSession.channel_session_key == channel_session_key,
                    AgentSession.status == AgentSessionStatus.ACTIVE,
                )
            )
        ).scalars()
        now = utcnow()
        for session in sessions:
            session.status = AgentSessionStatus.CANCELLED
            session.ended_at = now
            session.updated_at = now

    async def append_user_turn(
        self,
        *,
        session: AgentSession,
        content: str,
        message_id: str | None,
        sensitivity: str = "normal",
    ) -> tuple[ConversationTurn, bool]:
        normalized_message_id = (message_id or "").strip()[:128] or None
        if normalized_message_id:
            existing = (
                await self.db.execute(
                    select(ConversationTurn).where(
                        ConversationTurn.user_id == session.user_id,
                        ConversationTurn.channel == session.channel,
                        ConversationTurn.channel_message_id == normalized_message_id,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return existing, False

        turn = ConversationTurn(
            id=generate_id("ctn"),
            session_id=session.id,
            user_id=session.user_id,
            channel=session.channel,
            channel_message_id=normalized_message_id,
            role="user",
            content=content[:16_000],
            sensitivity=sensitivity,
            reflection_state="pending",
            turn_metadata={},
        )
        try:
            async with self.db.begin_nested():
                self.db.add(turn)
                await self.db.flush()
        except IntegrityError:
            if not normalized_message_id:
                raise
            existing = (
                await self.db.execute(
                    select(ConversationTurn).where(
                        ConversationTurn.user_id == session.user_id,
                        ConversationTurn.channel == session.channel,
                        ConversationTurn.channel_message_id == normalized_message_id,
                    )
                )
            ).scalar_one()
            return existing, False
        session.updated_at = utcnow()
        return turn, True

    async def append_assistant_turn(
        self,
        *,
        session: AgentSession,
        reply_to: ConversationTurn,
        content: str,
        metadata: Mapping[str, Any],
    ) -> ConversationTurn:
        turn = ConversationTurn(
            id=generate_id("ctn"),
            session_id=session.id,
            user_id=session.user_id,
            channel=session.channel,
            role="assistant",
            content=content[:16_000],
            reply_to_turn_id=reply_to.id,
            sensitivity="normal",
            reflection_state="pending",
            turn_metadata=dict(metadata),
        )
        try:
            async with self.db.begin_nested():
                self.db.add(turn)
                session.updated_at = utcnow()
                await self.db.flush()
            return turn
        except IntegrityError:
            existing = await self.reply_for(reply_to.id)
            if existing is None:
                raise
            return existing

    async def append_proactive_assistant_turn(
        self,
        *,
        session: AgentSession,
        content: str,
        metadata: Mapping[str, Any],
    ) -> ConversationTurn:
        turn = ConversationTurn(
            id=generate_id("ctn"),
            session_id=session.id,
            user_id=session.user_id,
            channel=session.channel,
            role="assistant",
            content=content[:16_000],
            reply_to_turn_id=None,
            sensitivity="normal",
            reflection_state="reflected",
            turn_metadata=dict(metadata),
        )
        self.db.add(turn)
        session.updated_at = utcnow()
        await self.db.flush()
        return turn

    async def reply_for(self, user_turn_id: str) -> ConversationTurn | None:
        return (
            await self.db.execute(
                select(ConversationTurn)
                .where(
                    ConversationTurn.reply_to_turn_id == user_turn_id,
                    ConversationTurn.role == "assistant",
                )
                .order_by(ConversationTurn.created_at.asc())
                .limit(1)
            )
        ).scalar_one_or_none()

    async def recent_messages(
        self,
        *,
        session_id: str,
        limit: int = RECENT_TURN_LIMIT,
    ) -> tuple[Mapping[str, str], ...]:
        rows = list(
            (
                await self.db.execute(
                    select(ConversationTurn)
                    .where(ConversationTurn.session_id == session_id)
                    .order_by(
                        ConversationTurn.created_at.desc(),
                        ConversationTurn.id.desc(),
                    )
                    .limit(max(1, min(limit, 100)))
                )
            ).scalars()
        )
        rows.reverse()
        return tuple({"role": row.role, "content": row.content} for row in rows)

    async def recent_episodes(
        self,
        *,
        user_id: str,
        days: int = RECENT_EPISODE_DAYS,
        limit: int = 12,
    ) -> list[ConversationEpisode]:
        since = utcnow() - timedelta(days=max(1, days))
        return list(
            (
                await self.db.execute(
                    select(ConversationEpisode)
                    .where(
                        ConversationEpisode.user_id == user_id,
                        ConversationEpisode.created_at >= since,
                    )
                    .order_by(ConversationEpisode.created_at.desc())
                    .limit(max(1, min(limit, 50)))
                )
            ).scalars()
        )

    async def pending_attention(
        self,
        *,
        user_id: str,
        limit: int = 10,
    ) -> list[ConversationAttentionCandidate]:
        return list(
            (
                await self.db.execute(
                    select(ConversationAttentionCandidate)
                    .where(
                        ConversationAttentionCandidate.user_id == user_id,
                        ConversationAttentionCandidate.status.in_(("pending", "sent")),
                    )
                    .order_by(
                        ConversationAttentionCandidate.value_score.desc(),
                        ConversationAttentionCandidate.due_at.asc(),
                    )
                    .limit(max(1, min(limit, 50)))
                )
            ).scalars()
        )

    async def mark_inbound_response(
        self, *, user_id: str
    ) -> ConversationAttentionCandidate | None:
        candidate = (
            await self.db.execute(
                select(ConversationAttentionCandidate)
                .where(
                    ConversationAttentionCandidate.user_id == user_id,
                    ConversationAttentionCandidate.status == "sent",
                    ConversationAttentionCandidate.responded_at.is_(None),
                )
                .order_by(ConversationAttentionCandidate.sent_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if candidate is not None:
            candidate.status = "responded"
            candidate.responded_at = utcnow()
        return candidate

    async def advance_reflection_cursor(
        self,
        *,
        session: AgentSession,
        immediate: bool,
    ) -> ConversationReflectionCursor:
        cursor = (
            await self.db.execute(
                select(ConversationReflectionCursor).where(
                    ConversationReflectionCursor.session_id == session.id
                )
            )
        ).scalar_one_or_none()
        if cursor is None:
            cursor = ConversationReflectionCursor(
                id=generate_id("crc"),
                session_id=session.id,
                user_id=session.user_id,
                pending_user_turns=0,
                next_reflection_at=None,
                running=False,
            )
            self.db.add(cursor)
        cursor.pending_user_turns = int(cursor.pending_user_turns or 0) + 1
        now = utcnow()
        cursor.next_reflection_at = (
            now
            if immediate or cursor.pending_user_turns >= 4
            else now + timedelta(minutes=IDLE_REFLECTION_MINUTES)
        )
        cursor.error = None
        await self.db.flush()
        return cursor

    async def conversation_state(self, *, user_id: str) -> dict[str, Any]:
        latest_episode = (
            await self.db.execute(
                select(ConversationEpisode)
                .where(ConversationEpisode.user_id == user_id)
                .order_by(ConversationEpisode.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        open_items: list[dict[str, Any]] = []
        if latest_episode is not None:
            for item in list(latest_episode.open_loops or [])[:10]:
                if isinstance(item, dict):
                    if (
                        item.get("status") == "closed"
                        or item.get("proactive_allowed") is False
                    ):
                        continue
                    open_items.append(item)
                elif isinstance(item, str):
                    open_items.append({"text": item})

        last_reflected_at = (
            await self.db.execute(
                select(func.max(ConversationReflectionCursor.last_reflected_at)).where(
                    ConversationReflectionCursor.user_id == user_id
                )
            )
        ).scalar_one_or_none()
        local_now = utcnow().astimezone(ZoneInfo("Asia/Shanghai"))
        start_local = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        sent_today = (
            await self.db.execute(
                select(func.count(ConversationAttentionCandidate.id)).where(
                    ConversationAttentionCandidate.user_id == user_id,
                    ConversationAttentionCandidate.status.in_(("sent", "responded")),
                    ConversationAttentionCandidate.sent_at
                    >= start_local.astimezone(timezone.utc),
                )
            )
        ).scalar_one()
        return {
            "summary": latest_episode.summary if latest_episode else None,
            "open_items": open_items,
            "last_reflected_at": last_reflected_at,
            "proactive_sent_today": int(sent_today or 0),
            "proactive_daily_limit": 2,
            "proactive_remaining_today": max(0, 2 - int(sent_today or 0)),
        }


def should_reflect_immediately(message: str) -> bool:
    text = message.strip()
    markers = (
        "记住",
        "记录",
        "改成",
        "纠正",
        "不是",
        "以后",
        "我决定",
        "我承诺",
        "答应",
        "计划",
        "准备",
        "截止",
        "之前完成",
        "明天",
        "后天",
        "下周",
        "下个月",
    )
    return any(marker in text for marker in markers)
