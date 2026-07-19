"""Retired compatibility surface for the former direct MemoryRewriter.

V2.5 moves every autonomous formal-memory mutation into
``MemoryOperationsCoordinator`` and ``MemoryCommitService``.  This class is
kept only so that an older client receives a deterministic, non-mutating
response while it migrates; it never loads memories or calls a model.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession


RETIRED_REASON = "memory_rewriter_retired_use_working_agent"
# Kept for the independent, user-driven relation API validation.  It is not a
# capability of the retired rewriter.
VALID_RELATION_TYPES = {
    "supports", "contradicts", "supersedes", "duplicates", "updates",
    "explains", "belongs_to", "caused_by", "resulted_in",
}


class MemoryRewriter:
    """Read-only compatibility adapter; it cannot alter formal memories."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def rewrite(
        self,
        user_id: str,
        *,
        target_types: Sequence[str] | None = None,
        max_clusters: int = 20,
    ) -> dict[str, Any]:
        del target_types, max_clusters
        return {
            "user_id": user_id,
            "rewritten_count": 0,
            "merges_proposed": 0,
            "proposals": [],
            "applied": False,
            "generated_at": _now_iso(),
            "warnings": [RETIRED_REASON],
        }

    async def apply_proposals(self, user_id: str, proposals: Sequence[dict[str, Any]] | None) -> dict[str, Any]:
        return {
            "user_id": user_id,
            "applied_count": 0,
            "failed": [{"reason": RETIRED_REASON} for _ in (proposals or [])],
            "applied_at": _now_iso(),
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
