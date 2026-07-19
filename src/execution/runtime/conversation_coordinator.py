"""Unified conversational entrypoint backed by the Conversation Ledger."""
from __future__ import annotations

from dataclasses import replace
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from src.execution.models.agent_runtime import AgentRole
from src.shared.ids.id_generator import generate_id

from .conversation_agent import (
    ConversationAnswer,
    generate_conversational_answer,
)
from .conversation_ledger import ConversationLedger, should_reflect_immediately
from .feature_flags import require_runtime_enabled


logger = logging.getLogger(__name__)

SAFE_CONVERSATION_FALLBACK = "我刚才没能组织出可靠的回复，但你这句话已经保留了。你可以继续说，我会从这里接着聊。"


class ConversationCoordinator:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.ledger = ConversationLedger(db)

    async def handle_turn(
        self,
        *,
        user_id: str,
        channel: str,
        channel_session_key: str,
        message: str,
        message_id: str | None = None,
        model=None,
    ) -> ConversationAnswer:
        require_runtime_enabled(AgentRole.CONVERSATIONAL)
        session = await self.ledger.get_or_create_session(
            user_id=user_id,
            channel=channel,
            channel_session_key=channel_session_key,
        )
        user_turn, created = await self.ledger.append_user_turn(
            session=session,
            content=message,
            message_id=message_id,
        )
        if not created:
            cached = await self.ledger.reply_for(user_turn.id)
            if cached is not None:
                metadata = cached.turn_metadata or {}
                return ConversationAnswer(
                    text=cached.content,
                    run_id=str(metadata.get("run_id") or ""),
                    response_mode=str(metadata.get("response_mode") or "ANSWER"),
                    confidence=str(metadata.get("confidence") or "LOW"),
                    citations=tuple(metadata.get("citations") or ()),
                    created_event_ids=(),
                    turn_id=cached.id,
                    session_id=session.id,
                )

        responded_attention = await self.ledger.mark_inbound_response(user_id=user_id)
        if responded_attention is not None:
            user_turn.turn_metadata = {
                **(user_turn.turn_metadata or {}),
                "response_to_attention_id": responded_attention.id,
                "runtime_handoff_response": bool(
                    (responded_attention.candidate_metadata or {}).get("handoff_id")
                ),
                "handoff_id": (responded_attention.candidate_metadata or {}).get(
                    "handoff_id"
                ),
                "memory_case_id": (responded_attention.candidate_metadata or {}).get(
                    "case_id"
                ),
            }
        immediate_reflection = (
            should_reflect_immediately(message) or responded_attention is not None
        )
        cursor = None
        if created:
            cursor = await self.ledger.advance_reflection_cursor(
                session=session,
                immediate=immediate_reflection,
            )
        # The user turn is durable before any provider call. A model timeout,
        # worker restart, or process crash must never erase the user's message.
        await self.db.commit()

        messages = await self.ledger.recent_messages(session_id=session.id)
        episodes = await self.ledger.recent_episodes(user_id=user_id)
        attention = await self.ledger.pending_attention(user_id=user_id)
        memory_brief = await self.ledger.memory_brief(user_id=user_id)
        context = self._render_context(
            episodes=episodes,
            attention=attention,
            memory_brief=memory_brief.content if memory_brief is not None else None,
        )

        try:
            answer = await generate_conversational_answer(
                self.db,
                user_id=user_id,
                channel=channel,
                channel_session_key=channel_session_key,
                session_id=session.id,
                trigger_id=user_turn.id,
                source_message=message,
                messages=messages,
                ledger_context=context,
                model=model,
            )
        except Exception:
            logger.exception("conversation coordinator model call failed")
            answer = None

        if answer is None:
            answer = ConversationAnswer(
                text=SAFE_CONVERSATION_FALLBACK,
                run_id=generate_id("arnf"),
                response_mode="SAFE_REFUSAL",
                confidence="LOW",
                citations=(),
                created_event_ids=(),
            )

        assistant_turn = await self.ledger.append_assistant_turn(
            session=session,
            reply_to=user_turn,
            content=answer.text,
            metadata={
                "run_id": answer.run_id,
                "response_mode": answer.response_mode,
                "confidence": answer.confidence,
                "citations": list(answer.citations),
            },
        )
        if assistant_turn.content != answer.text:
            metadata = assistant_turn.turn_metadata or {}
            answer = ConversationAnswer(
                text=assistant_turn.content,
                run_id=str(metadata.get("run_id") or ""),
                response_mode=str(metadata.get("response_mode") or "ANSWER"),
                confidence=str(metadata.get("confidence") or "LOW"),
                citations=tuple(metadata.get("citations") or ()),
                created_event_ids=(),
            )
        await self.db.commit()

        if cursor is not None and cursor.next_reflection_at is not None and (
            immediate_reflection or cursor.pending_user_turns >= 4
        ):
            try:
                from .conversation_reflector import trigger_conversation_reflection

                trigger_conversation_reflection(session.id)
            except Exception:
                logger.exception("failed to schedule conversation reflection")

        return replace(
            answer,
            turn_id=assistant_turn.id,
            session_id=session.id,
        )

    @staticmethod
    def _render_context(*, episodes, attention, memory_brief: str | None = None) -> str:
        sections: list[str] = []
        if memory_brief:
            sections.append(
                "Working Agent 已治理的正式记忆（仅作背景，若不确定请自然确认）：\n"
                + memory_brief[:4_000]
            )
        if episodes:
            rendered_episodes = []
            for episode in episodes[:12]:
                topics = "、".join(str(item) for item in list(episode.topics or [])[:5])
                rendered_episodes.append(
                    f"- {episode.summary[:800]}"
                    + (f"（主题：{topics}）" if topics else "")
                )
            sections.append("最近七天的对话片段：\n" + "\n".join(rendered_episodes))

            open_items: list[str] = []
            asked: list[str] = []
            declined: list[str] = []
            for episode in episodes:
                for item in list(episode.open_loops or []):
                    if isinstance(item, dict) and (
                        item.get("proactive_allowed") is False
                        or item.get("status") == "closed"
                    ):
                        continue
                    text = item.get("text") if isinstance(item, dict) else item
                    if isinstance(text, str) and text.strip():
                        open_items.append(text.strip())
                for item in list(episode.asked_questions or []):
                    if isinstance(item, str) and item.strip():
                        asked.append(item.strip())
                for item in list(episode.declined_questions or []):
                    if isinstance(item, str) and item.strip():
                        declined.append(item.strip())
            if open_items:
                sections.append("当前开放事项：\n- " + "\n- ".join(dict.fromkeys(open_items[:10])))
            if asked:
                sections.append("近期已经问过的问题，避免重复：\n- " + "\n- ".join(dict.fromkeys(asked[:10])))
            if declined:
                sections.append("用户跳过或拒绝的话题，不要主动重提：\n- " + "\n- ".join(dict.fromkeys(declined[:10])))

        pending = [
            item.prompt[:300]
            for item in attention
            if item.status == "pending" and item.proactive_allowed
        ]
        if pending:
            sections.append("可在当前对话自然承接、但不要强行推进的线索：\n- " + "\n- ".join(pending[:5]))
        if not sections:
            return "暂无已反思的历史片段。自然回应当前消息，不要为了收集信息而提问。"
        return "\n\n".join(sections)[:8_000]


async def reset_conversation(
    db: AsyncSession,
    *,
    user_id: str,
    channel: str,
    channel_session_key: str,
) -> None:
    await ConversationLedger(db).reset_session(
        user_id=user_id,
        channel=channel,
        channel_session_key=channel_session_key,
    )
    await db.commit()
