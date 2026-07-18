"""User-owned bounds for proactive conversation delivery."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.platform.models.wecom_contact import WeComContact


PREFERENCES_KEY = "conversation_proactivity"
DEFAULTS = {
    "enabled": True,
    "quiet_hours_start": 22,
    "quiet_hours_end": 8,
    "intensity": "normal",
    "daily_limit": 2,
    "min_interval_hours": 6,
}
INTENSITIES = {"low", "normal", "high"}
_UNSET = object()


def get_conversation_preferences(contact: WeComContact | None) -> dict[str, Any]:
    metadata = dict(getattr(contact, "contact_metadata", None) or {})
    raw = dict(metadata.get(PREFERENCES_KEY) or {})
    preferences = {**DEFAULTS, **raw}
    preferences.pop("mode", None)
    preferences["enabled"] = preferences.get("enabled") is not False
    if preferences.get("intensity") not in INTENSITIES:
        preferences["intensity"] = "normal"
    for key in ("quiet_hours_start", "quiet_hours_end"):
        value = preferences.get(key)
        if value is not None and (not isinstance(value, int) or not 0 <= value <= 23):
            preferences[key] = DEFAULTS[key]
    # Product safety caps are not user-expandable settings.
    preferences["daily_limit"] = 2
    preferences["min_interval_hours"] = 6
    return preferences


def is_quiet_hour(preferences: dict[str, Any], *, hour: int) -> bool:
    start = preferences.get("quiet_hours_start")
    end = preferences.get("quiet_hours_end")
    if not isinstance(start, int) or not isinstance(end, int) or start == end:
        return False
    return start <= hour < end if start < end else hour >= start or hour < end


async def update_conversation_preferences(
    db: AsyncSession,
    *,
    contact: WeComContact,
    enabled: bool | None = None,
    quiet_hours_start: int | None | object = _UNSET,
    quiet_hours_end: int | None | object = _UNSET,
    intensity: str | None = None,
) -> dict[str, Any]:
    if enabled is not None and not isinstance(enabled, bool):
        raise ValueError("enabled must be a boolean")
    for name, value in (
        ("quiet_hours_start", quiet_hours_start),
        ("quiet_hours_end", quiet_hours_end),
    ):
        if value is not _UNSET and value is not None and (
            not isinstance(value, int) or not 0 <= value <= 23
        ):
            raise ValueError(f"{name} must be between 0 and 23 or null")
    if intensity is not None and intensity not in INTENSITIES:
        raise ValueError("intensity must be low, normal or high")

    metadata = dict(contact.contact_metadata or {})
    preferences = dict(metadata.get(PREFERENCES_KEY) or {})
    if enabled is not None:
        preferences["enabled"] = enabled
    if quiet_hours_start is not _UNSET:
        preferences["quiet_hours_start"] = quiet_hours_start
    if quiet_hours_end is not _UNSET:
        preferences["quiet_hours_end"] = quiet_hours_end
    if intensity is not None:
        preferences["intensity"] = intensity
    preferences.pop("mode", None)
    preferences["updated_at"] = datetime.now(timezone.utc).isoformat()
    metadata[PREFERENCES_KEY] = preferences
    metadata.pop("agent_interaction_mode", None)
    metadata.pop("memory_question_preferences", None)
    contact.contact_metadata = metadata
    contact.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return get_conversation_preferences(contact)


async def pause_proactive_conversation(
    db: AsyncSession,
    *,
    contact: WeComContact,
    until: datetime,
    reason: str,
) -> None:
    metadata = dict(contact.contact_metadata or {})
    metadata["conversation_proactive_pause_until"] = until.astimezone(timezone.utc).isoformat()
    metadata["conversation_proactive_pause_reason"] = reason[:100]
    contact.contact_metadata = metadata
    contact.updated_at = datetime.now(timezone.utc)
    await db.commit()
