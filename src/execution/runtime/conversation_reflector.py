"""Asynchronous reflection loop for conversation episodes and memory signals."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import logging
import re
import threading
from typing import Any

from sqlalchemy import or_, select, update

from src.execution.models.conversation import (
    ConversationAttentionCandidate,
    ConversationEpisode,
    ConversationReflectionCursor,
    ConversationTurn,
)
from src.execution.runtime.workspace import AgentWorkspaceService
from src.memory.models.raw_event import (
    ProcessingStatus,
    RawEvent,
    SensitivityLevel,
    SourceType,
    VisibilityScope,
)
from src.shared.db.database import async_session
from src.shared.ids.id_generator import generate_event_id, generate_id
from src.shared.llm.model_gateway import ModelGateway
from src.shared.llm.providers import get_llm_provider
from src.shared.utils.hash import compute_content_hash


logger = logging.getLogger(__name__)
REFLECTION_VERSION = "conversation-reflection-v1"
REFLECTION_STALE_MINUTES = 30
MEMORY_KINDS = {
    "fact",
    "preference",
    "plan",
    "commitment",
    "correction",
    "relationship",
    "goal",
    "experience",
}
SENSITIVE_MARKERS = (
    "密码",
    "验证码",
    "身份证",
    "银行卡",
    "私钥",
    "token",
    "secret",
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_json_object(raw: str) -> dict[str, Any] | None:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        value = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            value = json.loads(text[start : end + 1])
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
    return value if isinstance(value, dict) else None


def _bounded_strings(value: object, *, limit: int, chars: int = 300) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            result.append(item.strip()[:chars])
        if len(result) >= limit:
            break
    return result


def _asked_questions(turns: list[ConversationTurn]) -> list[str]:
    questions: list[str] = []
    for turn in turns:
        if turn.role != "assistant":
            continue
        for sentence in re.split(r"(?<=[？?])", turn.content):
            sentence = sentence.strip()
            if sentence.endswith(("？", "?")) and 2 <= len(sentence) <= 300:
                questions.append(sentence)
    return list(dict.fromkeys(questions))[-20:]


def _declined_questions(turns: list[ConversationTurn]) -> list[str]:
    declined: list[str] = []
    markers = ("不想回答", "不回答", "跳过", "别问", "停止追问", "换个话题", "不聊这个")
    for turn in turns:
        if turn.role == "user" and any(marker in turn.content for marker in markers):
            declined.append(turn.content.strip()[:300])
    return list(dict.fromkeys(declined))[-20:]


def _fallback_reflection(turns: list[ConversationTurn]) -> dict[str, Any]:
    user_turns = [turn for turn in turns if turn.role == "user"]
    snippets = [turn.content.strip()[:180] for turn in user_turns if turn.content.strip()]
    summary = "；".join(snippets[-4:])[:1200] or "本片段没有可总结的用户内容。"
    signals: list[dict[str, Any]] = []
    attention: list[dict[str, Any]] = []
    durable_markers = (
        "我喜欢",
        "我不喜欢",
        "我住",
        "我叫",
        "我决定",
        "我计划",
        "我准备",
        "请记住",
        "帮我记住",
        "纠正一下",
        "应该改成",
    )
    plan_markers = ("计划", "准备", "要去", "要做", "明天", "后天", "下周", "截止")
    for turn in user_turns:
        content = turn.content.strip()
        if len(content) >= 4 and any(marker in content for marker in durable_markers):
            kind = "plan" if any(marker in content for marker in plan_markers) else "fact"
            signals.append(
                {
                    "kind": kind,
                    "quote": content[:1000],
                    "source_turn_id": turn.id,
                    "durable": True,
                    "confidence": 0.65,
                    "sensitivity": "normal",
                }
            )
        if len(content) >= 4 and any(marker in content for marker in plan_markers):
            attention.append(
                {
                    "kind": "plan_follow_up",
                    "prompt": f"你之前提到“{content[:80]}”，现在进展怎么样？",
                    "value_score": 0.72,
                    "source_turn_id": turn.id,
                    "quote": content[:1000],
                    "sensitivity": "normal",
                }
            )
    return {
        "summary": summary,
        "topics": [],
        "emotional_context": None,
        "open_loops": [],
        "memory_signals": signals,
        "attention_candidates": attention,
    }


async def _model_reflection(turns: list[ConversationTurn]) -> dict[str, Any]:
    transcript = [
        {
            "turn_id": turn.id,
            "role": turn.role,
            "content": turn.content[:3000],
            "created_at": turn.created_at.isoformat() if turn.created_at else None,
        }
        for turn in turns
    ]
    prompt = (
        "你是对话反思器，不直接回复用户。请只输出一个 JSON 对象，不要 Markdown。\n"
        "目标：把一段自然对话整理为 Episode，但不要把闲聊强行变成长期记忆。\n"
        "严格规则：\n"
        "1. memory_signals 只能引用 role=user 的原话，quote 必须逐字出现在对应 source_turn_id 的 content 中；Agent 回复不能作为用户事实。\n"
        "2. 只有跨时间仍有价值的事实、偏好、关系、经历、目标、计划、承诺或纠正，durable 才能为 true。\n"
        "3. 无价值闲聊可以有摘要，但 memory_signals 必须为空。\n"
        "4. attention_candidates 必须有明确用户原话来源、实际跟进价值且不敏感；不得基于模型推断主动联系。\n"
        "5. 用户拒绝、敏感内容、密码密钥和纯情绪推断不能生成主动候选。\n"
        "JSON schema: "
        '{"summary":"string","topics":["string"],"emotional_context":"string|null",'
        '"open_loops":[{"text":"string","source_turn_id":"string","kind":"string"}],'
        '"memory_signals":[{"kind":"fact|preference|plan|commitment|correction|relationship|goal|experience",'
        '"quote":"exact user quote","source_turn_id":"string","durable":true,'
        '"confidence":0.0,"sensitivity":"public|normal|private|sensitive"}],'
        '"attention_candidates":[{"kind":"follow_up|plan_follow_up|clarification",'
        '"prompt":"string","value_score":0.0,"source_turn_id":"string",'
        '"quote":"exact user quote","sensitivity":"public|normal"}]}.\n\n'
        f"对话：{json.dumps(transcript, ensure_ascii=False)}"
    )
    try:
        raw = await ModelGateway(get_llm_provider()).generate_text(
            prompt,
            temperature=0.1,
            max_tokens=2200,
            prompt_id="conversation-reflector",
            prompt_version=REFLECTION_VERSION,
        )
    except Exception:
        logger.warning("conversation reflection model unavailable; using grounded fallback")
        return _fallback_reflection(turns)
    return _parse_json_object(raw) or _fallback_reflection(turns)


def _user_turn_map(turns: list[ConversationTurn]) -> dict[str, ConversationTurn]:
    return {turn.id: turn for turn in turns if turn.role == "user"}


def _validated_signals(
    payload: dict[str, Any],
    *,
    user_turns: dict[str, ConversationTurn],
) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    raw_signals = payload.get("memory_signals")
    if not isinstance(raw_signals, list):
        return signals
    for item in raw_signals[:20]:
        if not isinstance(item, dict):
            continue
        turn = user_turns.get(str(item.get("source_turn_id") or ""))
        quote = str(item.get("quote") or "").strip()
        kind = str(item.get("kind") or "").strip().lower()
        sensitivity = str(item.get("sensitivity") or "normal").lower()
        try:
            confidence = float(item.get("confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        if (
            turn is None
            or not quote
            or quote not in turn.content
            or kind not in MEMORY_KINDS
            or item.get("durable") is not True
            or confidence < 0.55
        ):
            continue
        if any(marker.lower() in quote.lower() for marker in SENSITIVE_MARKERS):
            sensitivity = "sensitive"
        if sensitivity not in {"public", "normal", "private", "sensitive"}:
            sensitivity = "normal"
        signals.append(
            {
                "kind": kind,
                "quote": quote[:2000],
                "source_turn_id": turn.id,
                "confidence": min(1.0, max(0.0, confidence)),
                "sensitivity": sensitivity,
            }
        )
    return signals


def _validated_attention(
    payload: dict[str, Any],
    *,
    user_turns: dict[str, ConversationTurn],
    declined: list[str],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    # Refusals usually point at the preceding Agent question without repeating
    # its text. Suppress proactive output for this reflection window unless a
    # future structured link can prove the candidate is unrelated.
    if declined:
        return candidates
    raw_candidates = payload.get("attention_candidates")
    if not isinstance(raw_candidates, list):
        return candidates
    for item in raw_candidates[:10]:
        if not isinstance(item, dict):
            continue
        turn = user_turns.get(str(item.get("source_turn_id") or ""))
        quote = str(item.get("quote") or "").strip()
        prompt = str(item.get("prompt") or "").strip()
        sensitivity = str(item.get("sensitivity") or "normal").lower()
        try:
            score = float(item.get("value_score") or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        if (
            turn is None
            or not quote
            or quote not in turn.content
            or not prompt
            or score < 0.7
            or sensitivity not in {"public", "normal"}
            or any(marker.lower() in quote.lower() for marker in SENSITIVE_MARKERS)
        ):
            continue
        candidates.append(
            {
                "kind": str(item.get("kind") or "follow_up")[:32],
                "prompt": prompt[:1000],
                "value_score": min(1.0, max(0.0, score)),
                "source_turn_id": turn.id,
                "quote": quote[:1000],
                "sensitivity": sensitivity,
            }
        )
    return candidates


async def reflect_session(session_id: str, *, force: bool = False) -> str | None:
    now = utcnow()
    async with async_session() as db:
        claim = await db.execute(
            update(ConversationReflectionCursor)
            .where(
                ConversationReflectionCursor.session_id == session_id,
                or_(
                    ConversationReflectionCursor.running.is_(False),
                    ConversationReflectionCursor.updated_at
                    < now - timedelta(minutes=REFLECTION_STALE_MINUTES),
                ),
                or_(
                    ConversationReflectionCursor.next_reflection_at <= now,
                    ConversationReflectionCursor.pending_user_turns >= 4,
                    force,
                ),
            )
            .values(
                running=True,
                attempts=ConversationReflectionCursor.attempts + 1,
                error=None,
                updated_at=now,
            )
        )
        if claim.rowcount != 1:
            await db.rollback()
            return None
        await db.commit()

        cursor = (
            await db.execute(
                select(ConversationReflectionCursor).where(
                    ConversationReflectionCursor.session_id == session_id
                )
            )
        ).scalar_one()
        turns = list(
            (
                await db.execute(
                    select(ConversationTurn)
                    .where(
                        ConversationTurn.session_id == session_id,
                        ConversationTurn.reflection_state == "pending",
                    )
                    .order_by(ConversationTurn.created_at.asc(), ConversationTurn.id.asc())
                    .limit(100)
                )
            ).scalars()
        )
        if not turns:
            cursor.running = False
            cursor.pending_user_turns = 0
            cursor.next_reflection_at = None
            cursor.error = None
            await db.commit()
            return None

        end_turn = turns[-1]
        existing = (
            await db.execute(
                select(ConversationEpisode).where(
                    ConversationEpisode.session_id == session_id,
                    ConversationEpisode.end_turn_id == end_turn.id,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            for turn in turns:
                turn.reflection_state = "reflected"
            cursor.running = False
            cursor.pending_user_turns = 0
            cursor.last_reflected_turn_id = end_turn.id
            cursor.last_reflected_at = now
            cursor.next_reflection_at = None
            cursor.error = None
            await db.commit()
            return existing.id

        try:
            payload = await _model_reflection(turns)
            user_turns = _user_turn_map(turns)
            asked = _asked_questions(turns)
            declined = _declined_questions(turns)
            signals = _validated_signals(payload, user_turns=user_turns)
            attention = _validated_attention(
                payload,
                user_turns=user_turns,
                declined=declined,
            )
            open_loops = payload.get("open_loops")
            if not isinstance(open_loops, list):
                open_loops = []
            safe_open_loops: list[dict[str, Any]] = []
            for item in open_loops[:20]:
                if not isinstance(item, dict):
                    continue
                source_turn_id = str(item.get("source_turn_id") or "")
                text = str(item.get("text") or "").strip()
                if source_turn_id in user_turns and text:
                    safe_open_loops.append(
                        {
                            "text": text[:500],
                            "source_turn_id": source_turn_id,
                            "kind": str(item.get("kind") or "open_loop")[:32],
                        }
                    )
            episode = ConversationEpisode(
                id=generate_id("cep"),
                session_id=session_id,
                user_id=cursor.user_id,
                start_turn_id=turns[0].id,
                end_turn_id=end_turn.id,
                summary=str(payload.get("summary") or _fallback_reflection(turns)["summary"])[
                    :4000
                ],
                topics=_bounded_strings(payload.get("topics"), limit=10, chars=100),
                emotional_context=(
                    str(payload.get("emotional_context"))[:1000]
                    if payload.get("emotional_context")
                    else None
                ),
                open_loops=safe_open_loops,
                asked_questions=asked,
                declined_questions=declined,
                memory_signals=signals,
                source_turn_ids=[turn.id for turn in turns],
                status="active",
                reflection_version=REFLECTION_VERSION,
                working_state="queued" if signals else "not_dispatched",
                handoff_ids=[],
            )
            db.add(episode)
            await db.flush()

            event_ids: list[str] = []
            for signal in signals:
                turn = user_turns[signal["source_turn_id"]]
                stable_source_id = (
                    f"conversation:{episode.id}:{turn.id}:"
                    f"{compute_content_hash(signal['quote'])[:16]}"
                )
                existing_event = (
                    await db.execute(
                        select(RawEvent).where(RawEvent.source_id == stable_source_id)
                    )
                ).scalar_one_or_none()
                if existing_event is not None:
                    event_ids.append(existing_event.id)
                    signal["raw_event_id"] = existing_event.id
                    continue
                sensitivity = SensitivityLevel(signal["sensitivity"])
                from src.memory.services.event_ingestion import EventIngestionService
                event = (
                    await EventIngestionService(db).append(
                        user_id=cursor.user_id,
                        content=signal["quote"],
                        source_type=SourceType.CONVERSATION,
                        source_id=stable_source_id,
                        occurred_at=turn.created_at or now,
                        event_metadata={
                            "episode_id": episode.id,
                            "source_turn_id": turn.id,
                            "source_turn_ids": [turn.id],
                            "quote": signal["quote"],
                            "signal_kind": signal["kind"],
                            "reflection_version": REFLECTION_VERSION,
                            "confidence": signal["confidence"],
                            "handoff_id": (turn.turn_metadata or {}).get("handoff_id"),
                            "memory_case_id": (turn.turn_metadata or {}).get("memory_case_id"),
                            "runtime_handoff_response": bool(
                                (turn.turn_metadata or {}).get("runtime_handoff_response")
                            ),
                            "response_to_attention_id": (turn.turn_metadata or {}).get(
                                "response_to_attention_id"
                            ),
                        },
                        sensitivity=sensitivity,
                        visibility_scope=VisibilityScope.PERSONAL,
                        processing_status=ProcessingStatus.QUEUED,
                    )
                ).event
                event_ids.append(event.id)
                signal["raw_event_id"] = event.id

            for candidate in attention:
                db.add(
                    ConversationAttentionCandidate(
                        id=generate_id("cac"),
                        user_id=cursor.user_id,
                        session_id=session_id,
                        episode_id=episode.id,
                        kind=candidate["kind"],
                        prompt=candidate["prompt"],
                        value_score=candidate["value_score"],
                        source="reflection",
                        sensitivity=candidate["sensitivity"],
                        status="pending",
                        due_at=now + timedelta(hours=6),
                        expires_at=now + timedelta(days=14),
                        source_turn_ids=[candidate["source_turn_id"]],
                        proactive_allowed=True,
                        candidate_metadata={"quote": candidate["quote"]},
                    )
                )

            # Multiple durable signals from one Episode share a single
            # Working-Agent invocation, while retaining each RawEvent as
            # independent provenance evidence.
            if event_ids:
                primary_event = await db.get(RawEvent, event_ids[0])
                if primary_event is not None:
                    primary_metadata = dict(primary_event.event_metadata or {})
                    # WorkingCoordinator handles at most one eight-event micro-batch.
                    # Remaining signals stay queued for their own grounded pass.
                    primary_metadata["batch_source_event_ids"] = event_ids[:8]
                    primary_metadata["batch_kind"] = "conversation_episode"
                    primary_event.event_metadata = primary_metadata

            for turn in turns:
                turn.reflection_state = "reflected"
            cursor.running = False
            cursor.pending_user_turns = 0
            cursor.last_reflected_turn_id = end_turn.id
            cursor.last_reflected_at = now
            cursor.next_reflection_at = None
            cursor.error = None
            episode.memory_signals = list(signals)
            await db.commit()

            try:
                AgentWorkspaceService().project_conversation_episode(
                    user_id=cursor.user_id,
                    episode_id=episode.id,
                    summary=episode.summary,
                    topics=list(episode.topics or []),
                    open_loops=list(episode.open_loops or []),
                    asked_questions=list(episode.asked_questions or []),
                    declined_questions=list(episode.declined_questions or []),
                    reflected_at=now,
                )
            except OSError:
                logger.exception("conversation workspace projection failed")

            if event_ids:
                from src.memory.tasks.memory_extraction import trigger_extraction
                trigger_extraction(event_ids[0])
            return episode.id
        except Exception as exc:
            await db.rollback()
            cursor = (
                await db.execute(
                    select(ConversationReflectionCursor).where(
                        ConversationReflectionCursor.session_id == session_id
                    )
                )
            ).scalar_one()
            cursor.running = False
            cursor.error = type(exc).__name__[:256]
            cursor.next_reflection_at = now + timedelta(minutes=min(60, 5 * cursor.attempts))
            await db.commit()
            logger.exception("conversation reflection failed session_id=%s", session_id)
            return None


async def reflect_due_conversations(*, force_overdue: bool = False, limit: int = 100) -> int:
    now = utcnow()
    async with async_session() as db:
        rows = list(
            (
                await db.execute(
                    select(ConversationReflectionCursor.session_id)
                    .where(
                        ConversationReflectionCursor.next_reflection_at.is_not(None),
                        ConversationReflectionCursor.next_reflection_at <= now,
                    )
                    .order_by(ConversationReflectionCursor.next_reflection_at.asc())
                    .limit(max(1, min(limit, 500)))
                )
            ).scalars()
        )
    completed = 0
    for session_id in rows:
        if await reflect_session(session_id, force=force_overdue):
            completed += 1
    return completed


def trigger_conversation_reflection(session_id: str) -> None:
    thread = threading.Thread(
        target=lambda: asyncio.run(reflect_session(session_id, force=True)),
        daemon=True,
    )
    thread.start()
