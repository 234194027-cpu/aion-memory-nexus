"""Internal append-only lifecycle audit helpers; no API contract is exposed here."""

from __future__ import annotations

from typing import Iterable

from sqlalchemy.ext.asyncio import AsyncSession

from src.memory.models.memory_state_transition import MemoryStateTransition
from src.shared.ids.id_generator import generate_id
from src.memory.services.governance_policy import POLICY_VERSION


def _state_value(value: object) -> str | None:
    if value is None:
        return None
    return str(getattr(value, "value", value))


async def record_memory_state_transition(
    db: AsyncSession,
    *,
    user_id: str,
    subject_type: str,
    subject_id: str,
    from_state: object,
    to_state: object,
    actor_type: str,
    actor_id: str | None = None,
    reason: str | None = None,
    evidence_refs: Iterable[str] | None = None,
) -> None:
    """Queue a lifecycle record in the caller's transaction.

    The helper never commits. Callers can therefore preserve the existing
    all-or-nothing transaction between a state change and its audit evidence.
    """
    db.add(
        MemoryStateTransition(
            id=generate_id("mst"),
            user_id=user_id,
            subject_type=subject_type,
            subject_id=subject_id,
            from_state=_state_value(from_state),
            to_state=_state_value(to_state) or "unknown",
            actor_type=actor_type,
            actor_id=actor_id,
            reason=reason,
            evidence_refs=list(evidence_refs or []),
            policy_version=POLICY_VERSION,
        )
    )
