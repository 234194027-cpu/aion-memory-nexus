from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.memory.models.raw_event import ProcessingStatus, RawEvent
from src.platform.models.wecom_contact import WeComContact


UNDO_LAST_COMMANDS = {"撤回上一条", "刚才那条删掉", "上一条删掉", "上一条不要记", "删除上一条"}
DO_NOT_REMEMBER_COMMANDS = {"不要记", "这条不要记", "别记", "不用记", "忽略这条"}
LAST_WRONG_COMMANDS = {"上一条不对", "刚才那条不对", "上一条错了", "刚才说错了"}
FORCE_GROUP_COMMANDS = {"这条和刚才是一件事", "和刚才是一件事", "并到上一条", "合并到上一条"}
STANDALONE_COMMANDS = {"这条单独记", "单独记这条", "新开一条"}
CLOSE_GROUP_COMMANDS = {"上一条结束", "这组结束", "刚才那组结束"}
SUPPLEMENT_PREFIXES = ("补充上一条", "补充一下", "还有一点", "补充：", "补充:")
INGEST_PREFERENCES_KEY = "wecom_ingest_preferences"
PENDING_INGEST_DIRECTIVE_KEY = "wecom_pending_ingest_directive"
EVENT_GROUP_WINDOW_SECONDS = 300


@dataclass(frozen=True)
class EventClassification:
    kind: str
    confidence: float
    should_store: bool
    needs_follow_up: bool
    follow_up_question: str | None


@dataclass(frozen=True)
class IngestDirective:
    content: str
    force_group: bool = False
    standalone: bool = False
    close_group: bool = False
    label: str | None = None


@dataclass(frozen=True)
class WeComIngestResult:
    event_id: str | None
    classification: EventClassification
    reply: str
    grouped: bool = False
    group_summary: str | None = None


@dataclass(frozen=True)
class EventGroupInfo:
    group_id: str
    group_index: int
    grouped: bool
    root_event_id: str | None


@dataclass(frozen=True)
class QualityAssessment:
    score: float
    missing: list[str]
    reasons: list[str]
    follow_up_priority: str


def classify_wecom_event(content: str) -> EventClassification:
    text = content.strip()
    if _is_noise(text):
        return EventClassification(
            kind="noise",
            confidence=0.9,
            should_store=False,
            needs_follow_up=False,
            follow_up_question=None,
        )

    kind = "event"
    if _looks_like_correction(text):
        kind = "correction"
    elif _looks_like_task(text):
        kind = "task"
    elif _looks_like_preference(text):
        kind = "preference"
    elif _looks_like_thought(text):
        kind = "thought"

    follow_up = _single_follow_up_question(text=text, kind=kind)
    return EventClassification(
        kind=kind,
        confidence=0.72 if follow_up else 0.82,
        should_store=True,
        needs_follow_up=bool(follow_up),
        follow_up_question=follow_up,
    )


async def handle_memory_control_command(
    db: AsyncSession,
    *,
    user_id: str,
    contact: WeComContact,
    text: str,
) -> str | None:
    if text in DO_NOT_REMEMBER_COMMANDS:
        return "好，这条我不记。放心，我不是监控摄像头 🫡"

    if text in CLOSE_GROUP_COMMANDS:
        event = await find_last_active_wecom_event(db, user_id=user_id, contact_id=contact.id)
        if not event:
            return "我没找到正在整理的上一组记录。"
        group_id = (event.event_metadata or {}).get("wecom_event_group_id") or event.id
        await close_event_group(db, user_id=user_id, contact_id=contact.id, group_id=group_id)
        await db.commit()
        return "好，上一组我先收口了 📦。后面再发的内容会按新事件处理。"

    if text in UNDO_LAST_COMMANDS:
        event = await find_last_active_wecom_event(db, user_id=user_id, contact_id=contact.id)
        if not event:
            return "我没找到可以撤回的上一条企业微信记录。可能已经被处理过了。"
        _mark_event_revoked(event, reason=text, replacement_event_id=None)
        await db.commit()
        return f"已撤回上一条记录 🧹\n事件ID: {event.id}"

    if text in LAST_WRONG_COMMANDS:
        event = await find_last_active_wecom_event(db, user_id=user_id, contact_id=contact.id)
        if not event:
            return "我没找到可以标记为错误的上一条记录。你可以直接发正确版本，我按新记录保存。"
        metadata = dict(event.event_metadata or {})
        metadata["wecom_ingest_status"] = "needs_correction"
        metadata["wecom_ingest_control_reason"] = text
        event.event_metadata = metadata
        await db.commit()
        return "收到，上一条我先标成“需要纠正”了。你直接把正确版本发我就行。"

    replacement = _extract_replacement_text(text)
    if replacement:
        event = await find_last_active_wecom_event(db, user_id=user_id, contact_id=contact.id)
        if not event:
            return None
        return await replace_last_wecom_event(
            db,
            user_id=user_id,
            contact=contact,
            previous_event=event,
            replacement_content=replacement,
            reason=text,
        )

    return None


def parse_ingest_directive(text: str) -> IngestDirective:
    stripped = text.strip()
    if stripped in FORCE_GROUP_COMMANDS:
        return IngestDirective(content="", force_group=True, label="force_group")
    if stripped in STANDALONE_COMMANDS:
        return IngestDirective(content="", standalone=True, label="standalone")
    for prefix in SUPPLEMENT_PREFIXES:
        if stripped.startswith(prefix):
            content = stripped[len(prefix):].lstrip(" ：:，,")
            return IngestDirective(content=content, force_group=True, label="supplement")
    for command in FORCE_GROUP_COMMANDS:
        if stripped.startswith(command):
            content = stripped[len(command):].lstrip(" ：:，,")
            return IngestDirective(content=content, force_group=True, label="force_group")
    for command in STANDALONE_COMMANDS:
        if stripped.startswith(command):
            content = stripped[len(command):].lstrip(" ：:，,")
            return IngestDirective(content=content, standalone=True, label="standalone")
    return IngestDirective(content=stripped)


def store_pending_ingest_directive(contact: WeComContact, directive: IngestDirective) -> None:
    if not directive.label or directive.content:
        return
    if not (directive.force_group or directive.standalone):
        return
    metadata = dict(contact.contact_metadata or {})
    metadata[PENDING_INGEST_DIRECTIVE_KEY] = {
        "label": directive.label,
        "force_group": directive.force_group,
        "standalone": directive.standalone,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    contact.contact_metadata = metadata


def apply_pending_ingest_directive(contact: WeComContact, directive: IngestDirective) -> IngestDirective:
    if directive.label:
        _clear_pending_ingest_directive(contact)
        return directive

    metadata = dict(contact.contact_metadata or {})
    pending = metadata.get(PENDING_INGEST_DIRECTIVE_KEY)
    if not isinstance(pending, dict):
        return directive

    _clear_pending_ingest_directive(contact)
    return IngestDirective(
        content=directive.content,
        force_group=bool(pending.get("force_group")),
        standalone=bool(pending.get("standalone")),
        label=str(pending.get("label") or "pending"),
    )


async def prepare_event_group(
    db: AsyncSession,
    *,
    user_id: str,
    contact: WeComContact,
    classification: EventClassification,
    force_group: bool = False,
    standalone: bool = False,
) -> EventGroupInfo:
    if standalone or classification.kind in {"noise", "correction"}:
        return EventGroupInfo(group_id="", group_index=0, grouped=False, root_event_id=None)

    recent = await find_last_active_wecom_event(db, user_id=user_id, contact_id=contact.id)
    if not recent or not recent.occurred_at:
        return EventGroupInfo(group_id="", group_index=0, grouped=False, root_event_id=None)

    now = datetime.now(timezone.utc)
    occurred_at = recent.occurred_at
    if occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=timezone.utc)
    if now - occurred_at > timedelta(seconds=EVENT_GROUP_WINDOW_SECONDS) and not force_group:
        return EventGroupInfo(group_id="", group_index=0, grouped=False, root_event_id=None)

    metadata = dict(recent.event_metadata or {})
    if metadata.get("wecom_event_group_status") == "closed" and not force_group:
        return EventGroupInfo(group_id="", group_index=0, grouped=False, root_event_id=None)
    recent_kind = metadata.get("event_kind")
    if recent_kind not in {classification.kind, "event", "thought"} and not force_group:
        return EventGroupInfo(group_id="", group_index=0, grouped=False, root_event_id=None)

    group_id = metadata.get("wecom_event_group_id") or recent.id
    root_event_id = metadata.get("wecom_event_group_root_id") or recent.id
    previous_count = await _count_events_in_group(
        db,
        user_id=user_id,
        contact_id=contact.id,
        group_id=group_id,
    )
    previous_count = max(previous_count, 1)
    metadata["wecom_event_group_id"] = group_id
    metadata["wecom_event_group_root_id"] = root_event_id
    metadata["wecom_event_group_count"] = previous_count + 1
    metadata["wecom_event_group_status"] = "open"
    metadata["wecom_event_group_last_at"] = now.isoformat()
    recent.event_metadata = metadata
    return EventGroupInfo(
        group_id=group_id,
        group_index=previous_count,
        grouped=True,
        root_event_id=root_event_id,
    )


async def apply_event_group_summary(
    db: AsyncSession,
    *,
    user_id: str,
    contact_id: str,
    event: RawEvent,
    group_id: str,
) -> str:
    result = await db.execute(
        select(RawEvent)
        .where(RawEvent.user_id == user_id, RawEvent.source_id == "wecom")
        .order_by(RawEvent.occurred_at.asc())
        .limit(80)
    )
    group_events: list[RawEvent] = []
    for item in result.scalars().all():
        metadata = dict(item.event_metadata or {})
        if metadata.get("wecom_contact_id") != contact_id:
            continue
        if metadata.get("wecom_ingest_status") in {"revoked", "superseded", "ignored"}:
            continue
        item_group_id = metadata.get("wecom_event_group_id") or item.id
        if item_group_id == group_id:
            group_events.append(item)
    if event not in group_events:
        group_events.append(event)

    ordered = sorted(group_events, key=lambda item: (item.event_metadata or {}).get("wecom_event_group_index", 0))
    summary = summarize_event_group(ordered)
    now = datetime.now(timezone.utc).isoformat()
    for item in ordered:
        metadata = dict(item.event_metadata or {})
        metadata["wecom_event_group_summary"] = summary
        metadata["wecom_event_group_summary_updated_at"] = now
        metadata["wecom_event_group_count"] = len(ordered)
        item.event_metadata = metadata
    return summary


def assess_ingest_quality(*, content: str, classification: EventClassification) -> QualityAssessment:
    text = content.strip()
    missing: list[str] = []
    reasons: list[str] = []
    score = 1.0

    if len(text) <= 12:
        score -= 0.25
        missing.append("context")
        reasons.append("内容偏短，缺少上下文")
    if classification.kind in {"event", "thought"} and not _has_time_signal(text):
        score -= 0.15
        missing.append("time")
        reasons.append("缺少明确时间")
    if classification.kind == "event" and not _has_result_signal(text):
        score -= 0.2
        missing.append("result")
        reasons.append("缺少结果或下一步")
    if _has_pronoun_without_person(text):
        score -= 0.2
        missing.append("people")
        reasons.append("代词指向不清")
    if classification.kind == "task" and not _has_time_signal(text):
        score -= 0.25
        missing.append("due_time")
        reasons.append("待办缺少处理时间")

    score = round(max(0.0, min(1.0, score)), 2)
    if score < 0.55:
        priority = "P0"
    elif score < 0.75:
        priority = "P1"
    else:
        priority = "P2"
    return QualityAssessment(score=score, missing=missing, reasons=reasons, follow_up_priority=priority)


async def close_event_group(
    db: AsyncSession,
    *,
    user_id: str,
    contact_id: str,
    group_id: str,
) -> None:
    result = await db.execute(
        select(RawEvent)
        .where(RawEvent.user_id == user_id, RawEvent.source_id == "wecom")
        .order_by(RawEvent.occurred_at.asc())
        .limit(80)
    )
    now = datetime.now(timezone.utc).isoformat()
    for event in result.scalars().all():
        metadata = dict(event.event_metadata or {})
        if metadata.get("wecom_contact_id") != contact_id:
            continue
        if (metadata.get("wecom_event_group_id") or event.id) != group_id:
            continue
        metadata["wecom_event_group_status"] = "closed"
        metadata["wecom_event_group_closed_at"] = now
        event.event_metadata = metadata


async def update_ingest_preferences(
    db: AsyncSession,
    *,
    contact: WeComContact,
    classification: EventClassification,
    content: str,
) -> None:
    metadata = dict(contact.contact_metadata or {})
    prefs = dict(metadata.get(INGEST_PREFERENCES_KEY) or {})

    kind_counts = dict(prefs.get("kind_counts") or {})
    kind_counts[classification.kind] = int(kind_counts.get(classification.kind, 0)) + 1
    prefs["kind_counts"] = kind_counts

    now = datetime.now(timezone.utc)
    hour_counts = dict(prefs.get("write_hour_counts") or {})
    hour_key = f"{now.hour:02d}"
    hour_counts[hour_key] = int(hour_counts.get(hour_key, 0)) + 1
    prefs["write_hour_counts"] = hour_counts

    length = len(content.strip())
    total_count = int(prefs.get("message_count") or 0) + 1
    total_length = int(prefs.get("total_message_length") or 0) + length
    prefs["message_count"] = total_count
    prefs["total_message_length"] = total_length
    prefs["average_message_length"] = round(total_length / total_count, 2)
    prefs["short_message_count"] = int(prefs.get("short_message_count") or 0) + (1 if length <= 12 else 0)
    prefs["follow_up_needed_count"] = int(prefs.get("follow_up_needed_count") or 0) + (1 if classification.needs_follow_up else 0)
    prefs["noise_count"] = int(prefs.get("noise_count") or 0) + (1 if classification.kind == "noise" else 0)
    prefs["last_kind"] = classification.kind
    prefs["last_updated_at"] = now.isoformat()

    metadata[INGEST_PREFERENCES_KEY] = prefs
    contact.contact_metadata = metadata


async def find_last_active_wecom_event(
    db: AsyncSession,
    *,
    user_id: str,
    contact_id: str,
) -> RawEvent | None:
    result = await db.execute(
        select(RawEvent)
        .where(
            RawEvent.user_id == user_id,
            RawEvent.source_id == "wecom",
        )
        .order_by(RawEvent.ingested_at.desc().nullslast(), RawEvent.occurred_at.desc())
        .limit(12)
    )
    for event in result.scalars().all():
        metadata = dict(event.event_metadata or {})
        if metadata.get("wecom_contact_id") != contact_id:
            continue
        if metadata.get("wecom_ingest_status") in {"revoked", "superseded", "ignored"}:
            continue
        if metadata.get("event_kind") == "noise":
            continue
        return event
    return None


async def replace_last_wecom_event(
    db: AsyncSession,
    *,
    user_id: str,
    contact: WeComContact,
    previous_event: RawEvent,
    replacement_content: str,
    reason: str,
) -> str:
    from src.memory.models.raw_event import SensitivityLevel, SourceType, VisibilityScope
    classification = classify_wecom_event(replacement_content)
    from src.memory.services.event_ingestion import EventIngestionService, trigger_ingested_event

    event = (
        await EventIngestionService(db).append(
            user_id=user_id,
            content=replacement_content,
            source_type=SourceType.MANUAL,
            source_id="wecom",
            event_metadata={
            "channel": "wecom",
            "wecom_contact_id": contact.id,
            "wecom_user_id": contact.wecom_user_id,
            "wecom_chat_id": contact.chat_id,
            "wecom_chat_type": contact.chat_type,
            "event_kind": "correction",
            "classification_confidence": classification.confidence,
            "correction_of_event_id": previous_event.id,
            "correction_reason": reason,
            "wecom_ingest_status": "active",
            },
            sensitivity=SensitivityLevel.NORMAL,
            visibility_scope=VisibilityScope.PERSONAL,
        )
    ).event
    _mark_event_revoked(previous_event, reason=reason, replacement_event_id=event.id)
    await db.commit()
    trigger_ingested_event(event.id)
    return f"已按新版本保存，并把上一条标记为被修正 🛠️\n新事件ID: {event.id}"


def build_ingest_reply(result: WeComIngestResult) -> str:
    if not result.event_id:
        return result.reply
    if result.grouped:
        summary = f"\n组摘要：{result.group_summary}" if result.group_summary else ""
        return f"我先并到刚才那组了 🧩\n事件ID: {result.event_id}{summary}"
    follow_up = ""
    if result.classification.follow_up_question:
        follow_up = f"\n\n顺手确认 1 个关键点：{result.classification.follow_up_question}"
    return f"{result.reply}\n事件ID: {result.event_id}{follow_up}"


def confirmation_reply(classification: EventClassification, contact: WeComContact | None = None) -> str:
    suffix = _reply_style_suffix(contact)
    if classification.kind == "task":
        return f"记下了 ✅ 这像是一条待办/跟进事项。{suffix}"
    if classification.kind == "correction":
        return f"收到 🛠️ 这像是纠错信息，我会按修正线索处理，不直接乱覆盖。{suffix}"
    if classification.kind == "preference":
        return f"记下了 🧭 这像是你的偏好，以后我会尽量参考。{suffix}"
    if classification.kind == "thought":
        return f"收到了 💭 这更像是一条想法/感受，我先帮你留住。{suffix}"
    if classification.kind == "noise":
        return "这条有点像随手测试，我先不进正式记忆库。要记的话你补一句背景就行 🙂"
    return f"记下了 🧠 这像是一条事件记录。{suffix}"


def build_ingest_quality_metrics(events: list[RawEvent]) -> dict:
    total = len(events)
    kind_counts: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    grouped_count = 0
    follow_up_count = 0
    quality_scores: list[float] = []
    low_quality_count = 0
    for event in events:
        metadata = dict(event.event_metadata or {})
        kind = metadata.get("event_kind") or "unknown"
        status = metadata.get("wecom_ingest_status") or "unknown"
        kind_counts[kind] = kind_counts.get(kind, 0) + 1
        status_counts[status] = status_counts.get(status, 0) + 1
        grouped_count += 1 if metadata.get("wecom_event_group_id") else 0
        follow_up_count += 1 if metadata.get("needs_follow_up") else 0
        if metadata.get("quality_score") is not None:
            score = float(metadata.get("quality_score") or 0)
            quality_scores.append(score)
            low_quality_count += 1 if score < 0.75 else 0
    return {
        "event_count": total,
        "kind_counts": kind_counts,
        "status_counts": status_counts,
        "grouped_count": grouped_count,
        "grouped_rate": round(grouped_count / total, 4) if total else 0,
        "follow_up_needed_count": follow_up_count,
        "follow_up_rate": round(follow_up_count / total, 4) if total else 0,
        "average_quality_score": round(sum(quality_scores) / len(quality_scores), 4) if quality_scores else None,
        "low_quality_count": low_quality_count,
    }


def summarize_event_group(events: list[RawEvent]) -> str:
    contents = [event.content.strip() for event in events if event.content and event.content.strip()]
    if not contents:
        return ""
    if len(contents) == 1:
        return contents[0][:220]
    joined = "；".join(contents)
    return joined[:320]


def get_ingest_preferences(contact: WeComContact | None) -> dict:
    metadata = dict(getattr(contact, "contact_metadata", None) or {})
    return dict(metadata.get(INGEST_PREFERENCES_KEY) or {})


def _clear_pending_ingest_directive(contact: WeComContact) -> None:
    metadata = dict(contact.contact_metadata or {})
    if PENDING_INGEST_DIRECTIVE_KEY in metadata:
        metadata.pop(PENDING_INGEST_DIRECTIVE_KEY, None)
        contact.contact_metadata = metadata


async def build_daily_ingest_review(db: AsyncSession, *, user_id: str) -> dict:
    from zoneinfo import ZoneInfo

    shanghai = ZoneInfo("Asia/Shanghai")
    now_local = datetime.now(shanghai)
    start_utc = now_local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    result = await db.execute(
        select(RawEvent)
        .where(RawEvent.user_id == user_id, RawEvent.source_id == "wecom", RawEvent.ingested_at >= start_utc)
        .order_by(RawEvent.ingested_at.asc())
    )
    events = result.scalars().all()
    metrics = build_ingest_quality_metrics(events)
    active_events = [
        event for event in events
        if (event.event_metadata or {}).get("wecom_ingest_status") not in {"revoked", "superseded", "ignored"}
    ]
    group_ids = {
        (event.event_metadata or {}).get("wecom_event_group_id") or event.id
        for event in active_events
    }
    low_quality = [
        event for event in active_events
        if (event.event_metadata or {}).get("quality_score") is not None
        and float((event.event_metadata or {}).get("quality_score") or 0) < 0.75
    ]
    kind_counts = metrics.get("kind_counts", {})
    text = (
        f"今天企业微信写入回顾 🧾\n"
        f"共记录 {len(active_events)} 条，整理成 {len(group_ids)} 组。\n"
        f"类型：事件 {kind_counts.get('event', 0)}，待办 {kind_counts.get('task', 0)}，偏好 {kind_counts.get('preference', 0)}，纠错 {kind_counts.get('correction', 0)}，想法 {kind_counts.get('thought', 0)}。\n"
        f"需要补充：{len(low_quality)} 条；平均质量：{metrics.get('average_quality_score') if metrics.get('average_quality_score') is not None else '暂无'}。\n"
        f"如果你想继续整理，发 `/追问` 就行。"
    )
    return {"text": text, "metrics": metrics, "event_count": len(active_events), "group_count": len(group_ids)}


def _mark_event_revoked(event: RawEvent, *, reason: str, replacement_event_id: str | None) -> None:
    metadata = dict(event.event_metadata or {})
    metadata["wecom_ingest_status"] = "superseded" if replacement_event_id else "revoked"
    metadata["wecom_ingest_control_reason"] = reason
    metadata["replacement_event_id"] = replacement_event_id
    metadata["revoked_at"] = datetime.now(timezone.utc).isoformat()
    event.event_metadata = metadata
    event.processing_status = ProcessingStatus.FAILED


def _candidate_title_for_event(content: str, kind: str) -> str:
    labels = {
        "task": "企业微信待办",
        "preference": "企业微信偏好",
        "correction": "企业微信纠错",
        "thought": "企业微信想法",
    }
    return f"{labels.get(kind, '企业微信写入')}：{content[:32]}"


def _candidate_importance_for_kind(kind: str) -> float:
    return {
        "task": 0.72,
        "preference": 0.78,
        "correction": 0.82,
        "thought": 0.62,
    }.get(kind, 0.55)


def _extract_replacement_text(text: str) -> str | None:
    patterns = [
        r"^把上一条改成[:：]?\s*(.+)$",
        r"^上一条改成[:：]?\s*(.+)$",
        r"^刚才那条改成[:：]?\s*(.+)$",
    ]
    for pattern in patterns:
        match = re.match(pattern, text.strip())
        if match and match.group(1).strip():
            return match.group(1).strip()
    return None


def _single_follow_up_question(*, text: str, kind: str) -> str | None:
    if kind == "noise":
        return None
    if kind == "task" and not any(marker in text for marker in ("今天", "明天", "后天", "周", "月", "点", "前")):
        return "这个待办大概什么时候需要处理？"
    if kind == "correction":
        return None
    if len(text) <= 12:
        return "这条有点短，要不要补一下背景或结果？"
    if _has_pronoun_without_person(text):
        return "这里的“他/她/他们”具体是谁？"
    if kind == "event" and not any(marker in text for marker in ("结果", "完成", "定了", "失败", "成功", "后续")):
        return "这件事目前有结果或下一步吗？"
    return None


def _has_time_signal(text: str) -> bool:
    return any(marker in text for marker in ("今天", "昨天", "明天", "后天", "周", "月", "点", "早上", "晚上", "下午", "刚才"))


def _has_result_signal(text: str) -> bool:
    return any(marker in text for marker in ("结果", "完成", "定了", "失败", "成功", "后续", "结论", "下一步", "决定"))


def _is_noise(text: str) -> bool:
    lowered = text.lower()
    if lowered in {"hi", "hello", "test", "测试", "1", "ok", "收到"}:
        return True
    if len(text) <= 3 and not re.search(r"[\u4e00-\u9fff]{2,}", text):
        return True
    return False


def _looks_like_task(text: str) -> bool:
    return any(marker in text for marker in ("待办", "记得", "提醒", "明天要", "今天要", "下周要", "需要跟进", "todo"))


def _looks_like_preference(text: str) -> bool:
    return any(marker in text for marker in ("我喜欢", "我不喜欢", "以后不要", "以后尽量", "我希望", "偏好", "习惯"))


def _looks_like_thought(text: str) -> bool:
    return any(marker in text for marker in ("我觉得", "我感觉", "我意识到", "我担心", "有点焦虑", "压力", "开心", "难过"))


def _looks_like_correction(text: str) -> bool:
    return any(marker in text for marker in ("不是", "不对", "说错", "纠正", "更正", "应该是", "改成"))


def _has_pronoun_without_person(text: str) -> bool:
    if not any(marker in text for marker in ("他", "她", "他们", "她们")):
        return False
    return not any(marker in text for marker in ("张", "王", "李", "赵", "同事", "朋友", "客户", "家人"))


async def _count_events_in_group(
    db: AsyncSession,
    *,
    user_id: str,
    contact_id: str,
    group_id: str,
) -> int:
    result = await db.execute(
        select(RawEvent)
        .where(RawEvent.user_id == user_id, RawEvent.source_id == "wecom")
        .order_by(RawEvent.ingested_at.desc().nullslast(), RawEvent.occurred_at.desc())
        .limit(50)
    )
    count = 0
    for event in result.scalars().all():
        metadata = dict(event.event_metadata or {})
        if metadata.get("wecom_contact_id") != contact_id:
            continue
        if metadata.get("wecom_ingest_status") in {"revoked", "superseded", "ignored"}:
            continue
        event_group_id = metadata.get("wecom_event_group_id") or event.id
        if event_group_id == group_id:
            count += 1
    return count


def _reply_style_suffix(contact: WeComContact | None) -> str:
    prefs = get_ingest_preferences(contact)
    message_count = int(prefs.get("message_count") or 0)
    if message_count < 5:
        return ""
    average_length = float(prefs.get("average_message_length") or 0)
    follow_up_needed = int(prefs.get("follow_up_needed_count") or 0)
    if average_length <= 14:
        return " 我会尽量短句确认，不打扰你。"
    if follow_up_needed / max(message_count, 1) >= 0.5:
        return " 如果关键信息缺口明显，我只补问一个点。"
    return ""
