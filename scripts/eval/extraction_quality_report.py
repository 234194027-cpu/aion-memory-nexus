"""Read-only aggregate report for the autonomous Working-Agent memory loop.

The output contains counts and opaque identifiers only. It never includes raw
user text, titles, bodies, credentials, or sensitive memory content.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.shared.config import settings
from src.shared.security.dependencies import SOLO_USER_ID


def _resolve_db_path() -> Path:
    url = settings.POSTGRES_URL
    if url.startswith("sqlite"):
        parsed = urlparse(url)
        raw_path = parsed.path.lstrip("/")
        db_path = Path(raw_path)
        if not db_path.is_absolute():
            db_path = PROJECT_ROOT / db_path
    else:
        db_path = PROJECT_ROOT / "life_memory.db"
    if not db_path.exists():
        raise FileNotFoundError(
            f"SQLite database not found at {db_path}; this script reads local SQLite copies only."
        )
    return db_path


def _opaque_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12] if value else ""


def _json_list(value: object) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
    return []


def aggregate_extraction_quality(*, user_id: str) -> dict:
    conn = sqlite3.connect(str(_resolve_db_path()))
    try:
        decisions = conn.execute(
            """
            SELECT id, state, conflict_refs, memory_ids
            FROM memory_work_decisions
            WHERE user_id = ?
            """,
            (user_id,),
        ).fetchall()
        memories = conn.execute(
            """
            SELECT id, memory_type, status, content_hash, origin_kind
            FROM committed_memories
            WHERE user_id = ?
            """,
            (user_id,),
        ).fetchall()

        state_distribution: dict[str, int] = {}
        conflict_count = 0
        materialized_decisions = 0
        decision_hashes: list[str] = []
        for decision_id, state, conflicts, memory_ids in decisions:
            decision_hashes.append(_opaque_hash(str(decision_id)))
            state_value = str(state or "unknown")
            state_distribution[state_value] = state_distribution.get(state_value, 0) + 1
            conflict_count += int(bool(_json_list(conflicts)))
            materialized_decisions += int(bool(_json_list(memory_ids)))

        type_distribution: dict[str, int] = {}
        status_distribution: dict[str, int] = {}
        hashes: dict[str, int] = {}
        automatic_count = 0
        for _memory_id, memory_type, status, content_hash, origin_kind in memories:
            type_value = str(memory_type or "unknown")
            status_value = str(status or "unknown")
            type_distribution[type_value] = type_distribution.get(type_value, 0) + 1
            status_distribution[status_value] = status_distribution.get(status_value, 0) + 1
            automatic_count += int(origin_kind == "working_agent")
            if content_hash:
                hashes[str(content_hash)] = hashes.get(str(content_hash), 0) + 1

        duplicate_extra_count = sum(count - 1 for count in hashes.values() if count > 1)
        ready_count = state_distribution.get("MEMORY_READY", 0)
        decision_total = len(decisions)
        formal_total = len(memories)
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "user_id_opaque": _opaque_hash(user_id),
            "metrics": {
                "decision_total": decision_total,
                "materialized_decision_count": materialized_decisions,
                "materialization_rate": round(materialized_decisions / ready_count, 4)
                if ready_count
                else 0.0,
                "conflict_decision_count": conflict_count,
                "conflict_rate": round(conflict_count / decision_total, 4)
                if decision_total
                else 0.0,
                "formal_memory_total": formal_total,
                "automatic_memory_count": automatic_count,
                "automatic_memory_rate": round(automatic_count / formal_total, 4)
                if formal_total
                else 0.0,
                "duplicate_extra_count": duplicate_extra_count,
                "duplicate_rate": round(duplicate_extra_count / len(hashes), 4)
                if hashes
                else 0.0,
                "decision_state_distribution": state_distribution,
                "memory_type_distribution": type_distribution,
                "memory_status_distribution": status_distribution,
            },
            "supporting": {
                "decision_hashes_sample": decision_hashes[:50],
            },
            "privacy_notes": [
                "All values are aggregate counts; no raw user content is included.",
                "Decision identifiers are opaque 12-character SHA-256 prefixes capped at 50 samples.",
            ],
        }
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", default=SOLO_USER_ID)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = json.dumps(
        aggregate_extraction_quality(user_id=args.user), ensure_ascii=False, indent=2
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
        print(f"Wrote autonomous-memory quality report to {args.output}")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
