import asyncio
from datetime import datetime, timedelta, timezone
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.execution.models.conversation import (
    ConversationAttentionCandidate,
    ConversationEpisode,
)
from src.execution.runtime.conversation_agent import reset_conversational_session, run_conversational_turn
from src.execution.runtime.workspace import AgentWorkspaceService
from src.platform.channels.wecom import WeComBotMessage, get_wecom_bot
from src.platform.services.media_ingestion import (
    create_wecom_media_placeholder,
    normalize_wecom_media_type,
    save_media_bytes_to_artifact,
)
from src.platform.services.wecom_event_ingestion import handle_memory_control_command
from src.platform.services.wecom_contacts import upsert_wecom_contact
from src.shared.db.database import async_session
from src.shared.security.dependencies import _get_or_create_solo_user


async def ingest_wecom_media_placeholder(db: AsyncSession, msg: WeComBotMessage) -> dict:
    user = await _get_or_create_solo_user(db)
    await upsert_wecom_contact(
        db,
        user_id=user.id,
        wecom_user_id=msg.from_user,
        chat_id=msg.chat_id,
        chat_type=msg.chat_type,
        aibot_id=msg.aibot_id,
        message_id=msg.msg_id,
        metadata={"last_source": "wecom_media_message"},
    )
    event, artifact = await create_wecom_media_placeholder(db, user_id=user.id, msg=msg)
    download_result = await _try_download_wecom_media(db, msg=msg, artifact=artifact)
    await db.commit()
    return {
        "event_id": event.id,
        "artifact_id": artifact.id,
        "media_type": artifact.media_type,
        "status": artifact.status,
        "downloaded": download_result.get("downloaded", False),
        "download_error": download_result.get("error"),
    }


async def handle_wecom_message(msg: WeComBotMessage) -> str:
    if msg.msg_type != "text":
        async with async_session() as db:
            result = await ingest_wecom_media_placeholder(db, msg)
        media_label = _media_label(result.get("media_type"))
        return (
            f"{media_label}我收到了，先帮你归档成笔记素材 📎\n"
            f"当前状态：{_media_status_label(result)}\n"
            f"素材ID：{result.get('artifact_id')}"
        )

    text = msg.content.strip()
    if not text:
        return "我收到了一条空消息，先不记账啦。你再发点内容给我 🫡"

    if text in {"/help", "帮助", "/?"}:
        return (
            "你可以直接像和一个长期对话伙伴一样跟我说话。\n\n"
            "/help - 显示帮助\n"
            "/status - 查看机器人状态\n"
            "/reset - 结束当前会话并从新会话继续\n\n"
            "“提问” - 结合当前脉络问你一个有价值的问题\n"
            "“停止追问” - 关闭当前开放问题\n"
            "“今天别问 / 明天再说 / 本周别问” - 暂停主动联系\n"
            "“撤回上一条 / 把上一条改成... / 不要记” - 管理已形成的记忆来源\n\n"
            "不需要切换聊天模式或提问模式，也不需要用“回答：”开头。"
        )

    if text in {"/status", "状态"}:
        bot = get_wecom_bot()
        status = bot.get_status() if bot else {}
        return (
            f"我现在的状态：{'在线，耳朵竖着呢 👂' if status.get('connected') else '暂时没连上 😵'}\n"
            f"运行中：{'是' if status.get('running') else '否'}\n"
            f"重连次数：{status.get('reconnect_count', 0)}"
        )

    if text in {"/reset", "重置", "/clear", "清空"}:
        async with async_session() as db:
            user = await _get_or_create_solo_user(db)
            await reset_conversational_session(
                db,
                user_id=user.id,
                channel="wecom",
                channel_session_key=_wecom_session_key(msg),
            )
        return "当前会话已经结束。接下来你正常说，我们会从一个新会话继续。"

    async with async_session() as db:
        user = await _get_or_create_solo_user(db)
        contact = await upsert_wecom_contact(
            db,
            user_id=user.id,
            wecom_user_id=msg.from_user,
            chat_id=msg.chat_id,
            chat_type=msg.chat_type,
            aibot_id=msg.aibot_id,
            message_id=msg.msg_id,
            metadata={"last_source": "wecom_message_seen"},
        )
        contact_metadata = dict(contact.contact_metadata or {})
        contact_metadata["conversation_proactive_unanswered_count"] = 0
        contact_metadata.pop("conversation_proactive_cooldown_until", None)
        contact.contact_metadata = contact_metadata
        contact.updated_at = datetime.now(timezone.utc)

        control_reply = await handle_memory_control_command(
            db,
            user_id=user.id,
            contact=contact,
            text=text,
        )
        if control_reply:
            return control_reply

        requested_name = _requested_assistant_name(text)
        if requested_name:
            try:
                name = AgentWorkspaceService().set_assistant_name(
                    user_id=user.id, name=requested_name
                )
            except (OSError, ValueError):
                return "这个名字我暂时没能保存。你可以换一个 1 到 16 个字的称呼再试。"
            return f"好，以后你可以叫我{name}。"

        if text in {"停止追问", "别再问了", "不问了"}:
            candidates = (
                await db.execute(
                    select(ConversationAttentionCandidate).where(
                        ConversationAttentionCandidate.user_id == user.id,
                        ConversationAttentionCandidate.status.in_(("pending", "sent")),
                    )
                )
            ).scalars()
            for candidate in candidates:
                candidate.status = "cancelled"
                candidate.proactive_allowed = False
            latest_episode = (
                await db.execute(
                    select(ConversationEpisode)
                    .where(ConversationEpisode.user_id == user.id)
                    .order_by(ConversationEpisode.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if latest_episode is not None:
                latest_episode.open_loops = [
                    {
                        **item,
                        "status": "closed",
                        "proactive_allowed": False,
                    }
                    if isinstance(item, dict)
                    else {
                        "text": str(item),
                        "status": "closed",
                        "proactive_allowed": False,
                    }
                    for item in list(latest_episode.open_loops or [])
                ]
                latest_episode.declined_questions = list(
                    dict.fromkeys(
                        [*(latest_episode.declined_questions or []), text]
                    )
                )
            await db.flush()

        pause_durations = {
            "今天别问": timedelta(days=1),
            "今天不问": timedelta(days=1),
            "明天再说": timedelta(days=2),
            "明天再问": timedelta(days=2),
            "本周别问": timedelta(days=7),
            "这周别问": timedelta(days=7),
        }
        if text in pause_durations:
            from src.platform.services.conversation_preferences import (
                pause_proactive_conversation,
            )

            await pause_proactive_conversation(
                db,
                contact=contact,
                until=datetime.now(timezone.utc) + pause_durations[text],
                reason=text,
            )
            return "好，我会按你的意思暂停主动联系。你随时发消息，我仍然正常回应。"

        return await _reply_to_chat(db, user_id=user.id, msg=msg, text=text)


def _requested_assistant_name(text: str) -> str | None:
    """Recognize an explicit rename request without treating ordinary chat as a command."""
    matched = re.search(r"(?:改名(?:字)?(?:叫|为)?|改叫|叫你)\s*([\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9_-]{0,15}?)(?:吗|么)?$", text.strip())
    return matched.group(1) if matched else None


def _wecom_session_key(msg: WeComBotMessage) -> str:
    return f"{msg.chat_id or 'direct'}:{msg.from_user}"


async def _reply_to_chat(db: AsyncSession, *, user_id: str, msg: WeComBotMessage, text: str) -> str:
    answer = await run_conversational_turn(
        db,
        user_id=user_id,
        channel="wecom",
        channel_session_key=_wecom_session_key(msg),
        message=text,
        message_id=msg.msg_id or None,
    )
    citations = _format_conversation_citations(
        answer.citations, getattr(answer, "citation_evidence", ())
    )
    return f"{answer.text}{citations}"


def _format_conversation_citations(citation_ids, evidence) -> str:
    """Render traceable citation metadata without memory/source body text."""
    if not citation_ids:
        return ""
    if not evidence:
        return f"\n\n依据：{', '.join(citation_ids)}"
    items = []
    resolved_ids = set()
    for item in evidence:
        resolved_ids.add(item.memory_id)
        valid_from = item.valid_from.date().isoformat() if item.valid_from else "未知"
        valid_until = item.valid_until.date().isoformat() if item.valid_until else "至今"
        items.append(
            f"{item.memory_id}（来源 {len(item.source_event_ids)} 条；{item.epistemic_status}；有效 {valid_from} 至 {valid_until}）"
        )
    source_ids = [item for item in citation_ids if item not in resolved_ids]
    if source_ids:
        items.append(f"来源ID {', '.join(source_ids)}")
    return f"\n\n依据：{'；'.join(items)}"


def _media_label(media_type: str | None) -> str:
    return {
        "image": "图片",
        "file": "文件",
        "video": "视频",
        "audio": "音频",
        "mixed": "混合消息",
    }.get(normalize_wecom_media_type(media_type or ""), "这条非文本消息")


async def _try_download_wecom_media(db: AsyncSession, *, msg: WeComBotMessage, artifact) -> dict:
    info = _wecom_download_info(msg.raw, msg.msg_type)
    if not info.get("url"):
        artifact.artifact_metadata = {
            **(artifact.artifact_metadata or {}),
            "download_skipped": "no_url_in_payload",
        }
        return {"downloaded": False, "error": "no_url_in_payload"}
    bot = get_wecom_bot()
    if not bot:
        artifact.artifact_metadata = {
            **(artifact.artifact_metadata or {}),
            "download_skipped": "wecom_bot_not_configured",
        }
        return {"downloaded": False, "error": "wecom_bot_not_configured"}
    try:
        data, filename, content_type = await bot.download_file(info["url"], info.get("aeskey"))
        await save_media_bytes_to_artifact(
            db,
            artifact=artifact,
            data=data,
            filename=filename or info.get("filename") or artifact.original_name,
            mime_type=content_type or artifact.mime_type,
        )
        _trigger_media_extraction(artifact.id)
        return {"downloaded": True}
    except Exception as exc:
        artifact.status = "failed"
        artifact.error_message = f"wecom_media_download_failed:{str(exc)[:260]}"
        artifact.artifact_metadata = {
            **(artifact.artifact_metadata or {}),
            "download_error": artifact.error_message,
        }
        return {"downloaded": False, "error": artifact.error_message}


def _wecom_download_info(payload: dict, msg_type: str) -> dict:
    section = payload.get(msg_type) if isinstance(payload, dict) else None
    if not isinstance(section, dict):
        section = payload
    return {
        "url": _first_nested_value(section, ("url", "download_url", "file_url")),
        "aeskey": _first_nested_value(section, ("aeskey", "aes_key")),
        "filename": _first_nested_value(section, ("filename", "file_name", "name", "title")),
    }


def _first_nested_value(payload, keys: tuple[str, ...]):
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
        for value in payload.values():
            found = _first_nested_value(value, keys)
            if found:
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = _first_nested_value(item, keys)
            if found:
                return found
    return None


def _trigger_media_extraction(artifact_id: str) -> None:
    try:
        from src.platform.tasks.media_extraction import trigger_media_extraction

        trigger_media_extraction(artifact_id)
    except Exception:
        pass


def _media_status_label(result: dict) -> str:
    if result.get("downloaded"):
        return "已下载，正在整理成笔记"
    if result.get("download_error"):
        return "已接收，但暂时没拿到原文件，先保存元数据"
    return "已接收，等待解析能力接入"


async def start_wecom_long_connection() -> dict:
    bot = get_wecom_bot()
    if not bot:
        return {"status": "not_configured"}

    bot.set_message_handler(handle_wecom_message)
    if bot.is_connected():
        return {"status": "already_connected", "bot_status": bot.get_status()}

    asyncio.create_task(bot.connect())
    return {"status": "connecting", "bot_status": bot.get_status()}


async def stop_wecom_long_connection() -> dict:
    bot = get_wecom_bot()
    if not bot:
        return {"status": "not_configured"}
    if not bot.is_connected():
        return {"status": "already_disconnected", "bot_status": bot.get_status()}
    await bot.disconnect()
    return {"status": "disconnected", "bot_status": bot.get_status()}
