"""Value-driven proactive conversation delivery with hard safety quotas."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.execution.models.agent_runtime import AgentRole, AgentSession, AgentSessionStatus
from src.execution.models.conversation import (
    ConversationAttentionCandidate,
    ConversationTurn,
)
from src.execution.runtime.conversation_ledger import ConversationLedger
from src.platform.services.conversation_preferences import (
    get_conversation_preferences,
    is_quiet_hour,
)
from src.platform.services.wecom_contacts import (
    get_default_wecom_contact,
    get_wecom_recipient_id,
)
from src.shared.security.dependencies import SOLO_USER_ID


logger = logging.getLogger(__name__)
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
VALUE_THRESHOLDS = {"low": 0.85, "normal": 0.75, "high": 0.70}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


async def _expire_unanswered(
    db: AsyncSession,
    *,
    contact,
    now: datetime,
) -> str | None:
    waiting = (
        await db.execute(
            select(ConversationAttentionCandidate)
            .where(
                ConversationAttentionCandidate.user_id == contact.user_id,
                ConversationAttentionCandidate.status == "sent",
                ConversationAttentionCandidate.responded_at.is_(None),
            )
            .order_by(ConversationAttentionCandidate.sent_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if waiting is None:
        return None
    sent_at = waiting.sent_at
    if sent_at is not None and sent_at.tzinfo is None:
        sent_at = sent_at.replace(tzinfo=timezone.utc)
    if sent_at is None or sent_at > now - timedelta(hours=24):
        return "awaiting_response"

    waiting.status = "expired"
    metadata = dict(contact.contact_metadata or {})
    unanswered = int(metadata.get("conversation_proactive_unanswered_count") or 0) + 1
    metadata["conversation_proactive_unanswered_count"] = unanswered
    cooldown = timedelta(days=3) if unanswered >= 2 else timedelta(hours=24)
    metadata["conversation_proactive_cooldown_until"] = (now + cooldown).isoformat()
    contact.contact_metadata = metadata
    contact.updated_at = now
    await db.commit()
    return "cooldown_after_unanswered"


async def _maybe_create_exploration_candidate(
    db: AsyncSession,
    *,
    user_id: str,
    session: AgentSession,
    now: datetime,
) -> bool:
    latest_user_turn = (
        await db.execute(
            select(ConversationTurn)
            .where(
                ConversationTurn.user_id == user_id,
                ConversationTurn.role == "user",
            )
            .order_by(ConversationTurn.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if latest_user_turn is None:
        return False
    created_at = latest_user_turn.created_at
    if created_at is not None and created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    if created_at is None or created_at > now - timedelta(days=7):
        return False
    recent_exploration = (
        await db.execute(
            select(ConversationAttentionCandidate.id).where(
                ConversationAttentionCandidate.user_id == user_id,
                ConversationAttentionCandidate.source == "seven_day_exploration",
                ConversationAttentionCandidate.created_at >= now - timedelta(days=7),
            )
        )
    ).scalar_one_or_none()
    if recent_exploration is not None:
        return False
    db.add(
        ConversationAttentionCandidate(
            id=f"cac_{latest_user_turn.id[-16:]}",
            user_id=user_id,
            session_id=session.id,
            episode_id=None,
            kind="life_exploration",
            prompt="最近这段时间，有没有一件你想认真聊聊、但一直没顾上说的事？",
            value_score=0.76,
            source="seven_day_exploration",
            sensitivity="normal",
            status="pending",
            due_at=now,
            expires_at=now + timedelta(days=2),
            source_turn_ids=[],
            proactive_allowed=True,
            candidate_metadata={},
        )
    )
    await db.commit()
    return True


async def run_conversation_heartbeat(
    db: AsyncSession,
    *,
    user_id: str = SOLO_USER_ID,
) -> dict:
    contact = await get_default_wecom_contact(db, user_id=user_id)
    if contact is None:
        return {"status": "no_contact"}
    recipient = get_wecom_recipient_id(contact)
    if not recipient:
        return {"status": "no_recipient", "contact_id": contact.id}
    preferences = get_conversation_preferences(contact)
    if not preferences["enabled"]:
        return {"status": "user_disabled", "contact_id": contact.id}

    now = utcnow()
    local_now = now.astimezone(SHANGHAI_TZ)
    if is_quiet_hour(preferences, hour=local_now.hour):
        return {"status": "quiet_hours", "contact_id": contact.id}
    metadata = dict(contact.contact_metadata or {})
    pause_until = _parse_time(metadata.get("conversation_proactive_pause_until"))
    if pause_until and pause_until > now:
        return {"status": "paused", "contact_id": contact.id}
    cooldown_until = _parse_time(metadata.get("conversation_proactive_cooldown_until"))
    if cooldown_until and cooldown_until > now:
        return {"status": "cooldown", "contact_id": contact.id}
    unanswered_status = await _expire_unanswered(db, contact=contact, now=now)
    if unanswered_status:
        return {"status": unanswered_status, "contact_id": contact.id}

    start_local = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    sent_today = int(
        (
            await db.execute(
                select(func.count(ConversationAttentionCandidate.id)).where(
                    ConversationAttentionCandidate.user_id == user_id,
                    ConversationAttentionCandidate.sent_at >= start_local.astimezone(
                        timezone.utc
                    ),
                )
            )
        ).scalar_one()
        or 0
    )
    if sent_today >= 2:
        return {"status": "daily_limit", "contact_id": contact.id}

    last_sent = (
        await db.execute(
            select(func.max(ConversationAttentionCandidate.sent_at)).where(
                ConversationAttentionCandidate.user_id == user_id
            )
        )
    ).scalar_one_or_none()
    if last_sent is not None and last_sent.tzinfo is None:
        last_sent = last_sent.replace(tzinfo=timezone.utc)
    if last_sent and last_sent > now - timedelta(hours=6):
        return {"status": "minimum_interval", "contact_id": contact.id}

    session = (
        await db.execute(
            select(AgentSession)
            .where(
                AgentSession.user_id == user_id,
                AgentSession.agent_role == AgentRole.CONVERSATIONAL,
                AgentSession.status == AgentSessionStatus.ACTIVE,
            )
            .order_by(AgentSession.updated_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if session is None:
        return {"status": "no_conversation_session", "contact_id": contact.id}

    threshold = VALUE_THRESHOLDS[preferences["intensity"]]
    candidate = (
        await db.execute(
            select(ConversationAttentionCandidate)
            .where(
                ConversationAttentionCandidate.user_id == user_id,
                ConversationAttentionCandidate.status == "pending",
                ConversationAttentionCandidate.proactive_allowed.is_(True),
                ConversationAttentionCandidate.sensitivity.in_(("public", "normal")),
                ConversationAttentionCandidate.value_score >= threshold,
                ConversationAttentionCandidate.due_at <= now,
                (
                    ConversationAttentionCandidate.expires_at.is_(None)
                    | (ConversationAttentionCandidate.expires_at > now)
                ),
                (
                    ConversationAttentionCandidate.cooldown_until.is_(None)
                    | (ConversationAttentionCandidate.cooldown_until <= now)
                ),
            )
            .order_by(
                ConversationAttentionCandidate.value_score.desc(),
                ConversationAttentionCandidate.due_at.asc(),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if candidate is None:
        created = await _maybe_create_exploration_candidate(
            db, user_id=user_id, session=session, now=now
        )
        return {
            "status": "exploration_candidate_created" if created else "no_candidate",
            "contact_id": contact.id,
        }

    from src.platform.channels.wecom import get_wecom_bot

    bot = get_wecom_bot()
    if bot is None:
        return {"status": "bot_not_configured", "contact_id": contact.id}
    result = await bot.send_text_message(recipient, candidate.prompt)
    if result.get("errcode") != 0:
        return {
            "status": "send_failed",
            "contact_id": contact.id,
            "candidate_id": candidate.id,
        }

    candidate.status = "sent"
    candidate.sent_at = now
    metadata["conversation_proactive_last_sent_at"] = now.isoformat()
    contact.contact_metadata = metadata
    contact.updated_at = now
    await ConversationLedger(db).append_proactive_assistant_turn(
        session=session,
        content=candidate.prompt,
        metadata={
            "proactive": True,
            "attention_candidate_id": candidate.id,
            "source": candidate.source,
        },
    )
    await db.commit()
    return {
        "status": "sent",
        "contact_id": contact.id,
        "candidate_id": candidate.id,
    }
