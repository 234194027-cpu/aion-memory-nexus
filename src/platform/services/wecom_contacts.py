from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.platform.models.wecom_contact import WeComContact
from src.shared.ids.id_generator import generate_id


async def upsert_wecom_contact(
    db: AsyncSession,
    *,
    user_id: str,
    wecom_user_id: str = "",
    chat_id: str = "",
    chat_type: str = "",
    aibot_id: str = "",
    message_id: str = "",
    metadata: Optional[dict] = None,
) -> WeComContact:
    filters = [WeComContact.user_id == user_id]
    identifiers = []
    if wecom_user_id:
        identifiers.append(WeComContact.wecom_user_id == wecom_user_id)
    if chat_id:
        identifiers.append(WeComContact.chat_id == chat_id)

    contact = None
    if identifiers:
        result = await db.execute(select(WeComContact).where(*filters, or_(*identifiers)).limit(1))
        contact = result.scalar_one_or_none()

    now = datetime.now(timezone.utc)
    if contact is None:
        contact = WeComContact(
            id=generate_id("wecom_contact"),
            user_id=user_id,
            is_default=True,
            created_at=now,
        )
        db.add(contact)

    if contact.is_default:
        await _clear_other_defaults(db, user_id=user_id, keep_id=contact.id)
    else:
        existing_default = await get_default_wecom_contact(db, user_id=user_id)
        if existing_default is None:
            contact.is_default = True

    contact.wecom_user_id = wecom_user_id or contact.wecom_user_id
    contact.chat_id = chat_id or contact.chat_id
    contact.chat_type = chat_type or contact.chat_type
    contact.aibot_id = aibot_id or contact.aibot_id
    contact.last_message_id = message_id or contact.last_message_id
    contact.last_seen_at = now
    contact.updated_at = now
    contact.contact_metadata = {**(contact.contact_metadata or {}), **(metadata or {})}

    await db.commit()
    await db.refresh(contact)
    return contact


async def get_default_wecom_contact(db: AsyncSession, *, user_id: str) -> Optional[WeComContact]:
    result = await db.execute(
        select(WeComContact)
        .where(WeComContact.user_id == user_id, WeComContact.is_default.is_(True))
        .order_by(WeComContact.last_seen_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


def get_wecom_recipient_id(contact: WeComContact) -> str:
    return contact.chat_id or contact.wecom_user_id or ""


async def _clear_other_defaults(db: AsyncSession, *, user_id: str, keep_id: str) -> None:
    result = await db.execute(
        select(WeComContact).where(
            WeComContact.user_id == user_id,
            WeComContact.id != keep_id,
            WeComContact.is_default.is_(True),
        )
    )
    for other in result.scalars().all():
        other.is_default = False
