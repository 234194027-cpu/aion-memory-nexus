from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from src.platform.models.media_artifact import MediaArtifact
from src.memory.models.raw_event import RawEvent
from src.shared.db.database import get_db
from src.platform.channels.wecom import get_wecom_bot
from src.platform.api.media import media_artifact_payload
from src.shared.security.dependencies import get_current_user

import logging

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/conversation/preferences")
async def get_conversation_proactivity_preferences(
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    from src.platform.services.conversation_preferences import get_conversation_preferences
    from src.platform.services.wecom_contacts import get_default_wecom_contact

    contact = await get_default_wecom_contact(db, user_id=user.id)
    return get_conversation_preferences(contact)


@router.put("/conversation/preferences")
async def update_conversation_proactivity_preferences(
    request: dict,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    from src.platform.services.conversation_preferences import update_conversation_preferences
    from src.platform.services.wecom_contacts import get_default_wecom_contact

    allowed = {"enabled", "quiet_hours_start", "quiet_hours_end", "intensity"}
    unsupported = sorted(set(request).difference(allowed))
    if unsupported:
        raise HTTPException(status_code=400, detail=f"Unsupported preference fields: {', '.join(unsupported)}")
    contact = await get_default_wecom_contact(db, user_id=user.id)
    if contact is None:
        raise HTTPException(status_code=404, detail="Default WeCom contact not found")
    kwargs = {key: request[key] for key in allowed if key in request}
    try:
        return await update_conversation_preferences(db, contact=contact, **kwargs)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/status")
async def wecom_status(
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    bot = get_wecom_bot()
    if not bot:
        return JSONResponse({
            "enabled": False,
            "message": "WeCom bot is not configured (missing WECOM_BOT_ID or WECOM_BOT_SECRET)"
        })

    from src.platform.services.wecom_contacts import get_default_wecom_contact, get_wecom_recipient_id

    contact = await get_default_wecom_contact(db, user_id=user.id)
    status = bot.get_status()
    status["default_contact"] = None
    if contact:
        status["default_contact"] = {
            "id": contact.id,
            "wecom_user_id": contact.wecom_user_id,
            "chat_id": contact.chat_id,
            "chat_type": contact.chat_type,
            "recipient_id": get_wecom_recipient_id(contact),
            "last_seen_at": contact.last_seen_at.isoformat() if contact.last_seen_at else None,
        }

    return JSONResponse(status)


@router.post("/connect")
async def wecom_connect(
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    from src.platform.channels.wecom_handlers import start_wecom_long_connection

    result = await start_wecom_long_connection()
    if result["status"] == "not_configured":
        raise HTTPException(status_code=400, detail="WeCom bot is not configured")
    return result


@router.post("/disconnect")
async def wecom_disconnect(
    user = Depends(get_current_user),
):
    bot = get_wecom_bot()
    if not bot:
        raise HTTPException(status_code=400, detail="WeCom bot is not configured")

    from src.platform.channels.wecom_handlers import stop_wecom_long_connection

    result = await stop_wecom_long_connection()
    if result["status"] == "not_configured":
        raise HTTPException(status_code=400, detail="WeCom bot is not configured")
    return result


@router.post("/test-message")
async def test_wecom_message(
    request: dict,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    bot = get_wecom_bot()
    if not bot:
        raise HTTPException(status_code=400, detail="WeCom bot is not configured")

    user_id = request.get("user_id")
    content = request.get("content", "这是一条测试消息")

    if not user_id:
        from src.platform.services.wecom_contacts import get_default_wecom_contact, get_wecom_recipient_id

        contact = await get_default_wecom_contact(db, user_id=user.id)
        user_id = get_wecom_recipient_id(contact) if contact else ""

    if not user_id:
        raise HTTPException(status_code=400, detail="user_id is required and no default WeCom contact exists yet")

    result = await bot.send_text_message(user_id, content)

    return {
        "success": result.get("errcode") == 0,
        "result": result,
    }


@router.post("/conversation/heartbeat/run")
async def run_wecom_conversation_heartbeat(
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    from src.platform.services.conversation_heartbeat import run_conversation_heartbeat

    result = await run_conversation_heartbeat(db, user_id=user.id)
    return {
        "success": result.get("status") == "sent",
        "result": result,
    }


@router.get("/ingest-events")
async def list_wecom_ingest_events(
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    from src.platform.services.wecom_event_ingestion import build_ingest_quality_metrics

    capped_limit = max(1, min(limit, 200))
    result = await db.execute(
        select(RawEvent)
        .where(RawEvent.user_id == user.id, RawEvent.source_id == "wecom")
        .order_by(RawEvent.ingested_at.desc().nullslast(), RawEvent.occurred_at.desc())
        .limit(capped_limit)
    )
    events = result.scalars().all()
    return {
        "items": [_wecom_ingest_event_payload(event) for event in events],
        "metrics": build_ingest_quality_metrics(events),
    }


@router.get("/ingest-quality")
async def get_wecom_ingest_quality(
    limit: int = 200,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    from src.platform.services.wecom_contacts import get_default_wecom_contact
    from src.platform.services.wecom_event_ingestion import build_ingest_quality_metrics, get_ingest_preferences

    capped_limit = max(1, min(limit, 500))
    result = await db.execute(
        select(RawEvent)
        .where(RawEvent.user_id == user.id, RawEvent.source_id == "wecom")
        .order_by(RawEvent.ingested_at.desc().nullslast(), RawEvent.occurred_at.desc())
        .limit(capped_limit)
    )
    events = result.scalars().all()
    contact = await get_default_wecom_contact(db, user_id=user.id)
    return {
        "metrics": build_ingest_quality_metrics(events),
        "preferences": get_ingest_preferences(contact),
    }


@router.get("/ingest-groups")
async def list_wecom_ingest_groups(
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    capped_limit = max(1, min(limit, 300))
    result = await db.execute(
        select(RawEvent)
        .where(RawEvent.user_id == user.id, RawEvent.source_id == "wecom")
        .order_by(RawEvent.ingested_at.desc().nullslast(), RawEvent.occurred_at.desc())
        .limit(capped_limit)
    )
    groups: dict[str, dict] = {}
    for event in result.scalars().all():
        metadata = dict(event.event_metadata or {})
        group_id = metadata.get("wecom_event_group_id") or event.id
        group = groups.setdefault(
            group_id,
            {
                "group_id": group_id,
                "root_event_id": metadata.get("wecom_event_group_root_id") or event.id,
                "message_count": 0,
                "kinds": {},
                "events": [],
            },
        )
        group["message_count"] += 1
        kind = metadata.get("event_kind") or "unknown"
        group["kinds"][kind] = group["kinds"].get(kind, 0) + 1
        group["events"].append(_wecom_ingest_event_payload(event))
    return {"items": list(groups.values())}


@router.get("/media-debug/events")
async def list_wecom_media_debug_events(
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    capped_limit = max(1, min(limit, 200))
    result = await db.execute(
        select(MediaArtifact)
        .where(MediaArtifact.user_id == user.id, MediaArtifact.source_channel == "wecom")
        .order_by(MediaArtifact.created_at.desc())
        .limit(capped_limit)
    )
    return {
        "items": [_media_artifact_debug_payload(item) for item in result.scalars().all()],
    }


@router.post("/media-debug/events/{artifact_id}/extract")
async def extract_wecom_media_debug_event(
    artifact_id: str,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    artifact = await db.get(MediaArtifact, artifact_id)
    if not artifact or artifact.user_id != user.id or artifact.source_channel != "wecom":
        raise HTTPException(status_code=404, detail="Media artifact not found")
    from src.platform.services.media_ingestion import extract_stored_artifact

    try:
        event, memory_id = await extract_stored_artifact(db, artifact_id=artifact_id)
    except ValueError as exc:
        # ValueError 是受控的校验错误，消息面向用户
        raise HTTPException(status_code=400, detail=str(exc)[:300]) from exc
    except Exception as exc:
        # 安全：其它异常仅记录内部日志，不向调用方泄露细节
        logger.error("extract_stored_artifact failed for wecom artifact %s: %s", artifact_id, exc)
        raise HTTPException(status_code=500, detail="media extraction failed") from exc
    refreshed = await db.get(MediaArtifact, artifact_id)
    return {
        "success": True,
        "event_id": event.id,
        "memory_id": memory_id,
        "artifact": _media_artifact_debug_payload(refreshed),
    }


def _wecom_ingest_event_payload(event: RawEvent) -> dict:
    metadata = dict(event.event_metadata or {})
    return {
        "id": event.id,
        "content": event.content,
        "processing_status": event.processing_status.value if hasattr(event.processing_status, "value") else str(event.processing_status),
        "event_kind": metadata.get("event_kind"),
        "classification_confidence": metadata.get("classification_confidence"),
        "wecom_ingest_status": metadata.get("wecom_ingest_status"),
        "needs_follow_up": metadata.get("needs_follow_up"),
        "follow_up_question": metadata.get("follow_up_question"),
        "quality_score": metadata.get("quality_score"),
        "quality_missing": metadata.get("quality_missing"),
        "quality_reasons": metadata.get("quality_reasons"),
        "follow_up_priority": metadata.get("follow_up_priority"),
        "wecom_event_group_id": metadata.get("wecom_event_group_id"),
        "wecom_event_group_root_id": metadata.get("wecom_event_group_root_id"),
        "wecom_event_group_index": metadata.get("wecom_event_group_index"),
        "wecom_event_group_count": metadata.get("wecom_event_group_count"),
        "wecom_event_group_status": metadata.get("wecom_event_group_status"),
        "wecom_event_group_summary": metadata.get("wecom_event_group_summary"),
        "wecom_event_grouped": metadata.get("wecom_event_grouped"),
        "correction_of_event_id": metadata.get("correction_of_event_id"),
        "replacement_event_id": metadata.get("replacement_event_id"),
        "occurred_at": event.occurred_at.isoformat() if event.occurred_at else None,
        "ingested_at": event.ingested_at.isoformat() if event.ingested_at else None,
    }


def _media_artifact_debug_payload(artifact: MediaArtifact) -> dict:
    metadata = dict(artifact.artifact_metadata or {})
    payload = media_artifact_payload(artifact) or {}
    payload["has_wecom_media_id"] = bool(artifact.wecom_media_id)
    payload["raw_payload_shape"] = metadata.get("raw_payload_shape")
    payload.pop("sha256", None)
    return payload
