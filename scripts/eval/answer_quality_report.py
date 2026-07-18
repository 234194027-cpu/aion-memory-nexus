"""Read-only aggregation for answer quality baseline.

聚合 AdvisorSession 表（白皮书 11.1 节）：
- 按 advisor_mode 分布（recall/decision/review/planning/reflection）
- cited_memory_ids 为空率（= 无证据回答率上界）
- confidence 分布（高/中/低三档）
- 追问率、澄清率、无证据拒答率（基于 mode 和 cited 字段推导）

输出仅含聚合数字与 opaque session_id hash，禁止包含真实 user_id、
真实 question/answer 文本、API Key、Token 或完整敏感记忆。
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


ADVISOR_MODES = ("recall", "decision", "review", "planning", "reflection")
CONFIDENCE_BANDS = (
    ("high", 0.7, 1.01),
    ("medium", 0.4, 0.7),
    ("low", 0.0, 0.4),
)


def _resolve_db_path() -> Path:
    """从 settings.POSTGRES_URL 解析 SQLite 文件路径；PostgreSQL 部署时
    fallback 到本地 life_memory.db。本脚本只读 SQLite，不连接 PostgreSQL。"""
    url = settings.POSTGRES_URL
    if url.startswith("sqlite"):
        parsed = urlparse(url)
        raw_path = parsed.path
        if raw_path.startswith("/"):
            raw_path = raw_path.lstrip("/")
        db_path = Path(raw_path)
        if not db_path.is_absolute():
            db_path = PROJECT_ROOT / db_path
    else:
        db_path = PROJECT_ROOT / "life_memory.db"
    if not db_path.exists():
        raise FileNotFoundError(
            f"SQLite database not found at {db_path}; this script only reads "
            f"local SQLite copies and does not connect to PostgreSQL."
        )
    return db_path


def _opaque_hash(value: str) -> str:
    if not value:
        return ""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _safe_json_loads(value):
    if value is None:
        return None
    if isinstance(value, (list, dict)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
    return None


def _confidence_band(value: float) -> str:
    if value is None:
        return "unknown"
    for label, lo, hi in CONFIDENCE_BANDS:
        if lo <= value < hi:
            return label
    return "unknown"


def _is_cited_empty(cited_raw) -> bool:
    """cited_memory_ids 为空：None / 空字符串 / 空 list。"""
    if cited_raw is None:
        return True
    parsed = _safe_json_loads(cited_raw)
    if parsed is None:
        # 不是 JSON，按字符串处理
        return not str(cited_raw).strip()
    if isinstance(parsed, list):
        return len(parsed) == 0
    return False


def aggregate_answer_quality(*, user_id: str) -> dict:
    db_path = _resolve_db_path()
    conn = sqlite3.connect(str(db_path))
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, user_id, advisor_mode, cited_memory_ids, confidence, created_at
            FROM advisor_sessions
            WHERE user_id = ?
            """,
            (user_id,),
        )
        rows = cursor.fetchall()

        mode_distribution = {mode: 0 for mode in ADVISOR_MODES}
        mode_distribution["unknown"] = 0
        confidence_distribution = {"high": 0, "medium": 0, "low": 0, "unknown": 0}
        cited_empty_count = 0
        total = len(rows)
        session_hashes: list[str] = []

        for row in rows:
            session_id, _uid, mode, cited_raw, confidence, _created_at = row
            session_hashes.append(_opaque_hash(session_id))
            mode_str = str(mode or "").lower()
            if mode_str in mode_distribution:
                mode_distribution[mode_str] += 1
            else:
                mode_distribution["unknown"] += 1
            try:
                confidence_value = float(confidence) if confidence is not None else None
            except (TypeError, ValueError):
                confidence_value = None
            confidence_distribution[_confidence_band(confidence_value)] += 1
            if _is_cited_empty(cited_raw):
                cited_empty_count += 1

        # 追问率：基于 mode 推导（recall 模式作为追问近似，因为它检索已有记忆）
        # 澄清率：review 模式作为澄清/反思近似
        # 无证据拒答率：cited 为空 + advisor_mode 不是 planning 的比例
        recall_count = mode_distribution.get("recall", 0)
        review_count = mode_distribution.get("review", 0)
        planning_count = mode_distribution.get("planning", 0)
        no_evidence_count = max(cited_empty_count - planning_count, 0)

        followup_rate = round(recall_count / total, 4) if total else 0.0
        clarification_rate = round(review_count / total, 4) if total else 0.0
        no_evidence_abstain_rate = round(no_evidence_count / total, 4) if total else 0.0
        cited_empty_rate = round(cited_empty_count / total, 4) if total else 0.0

        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "user_id_opaque": _opaque_hash(user_id),
            "metrics": {
                "total_sessions": total,
                "mode_distribution": mode_distribution,
                "confidence_distribution": confidence_distribution,
                "cited_empty_rate": cited_empty_rate,
                "cited_empty_count": cited_empty_count,
                "followup_rate": followup_rate,
                "clarification_rate": clarification_rate,
                "no_evidence_abstain_rate": no_evidence_abstain_rate,
            },
            "supporting": {
                "session_count": total,
                "session_hashes_sample": session_hashes[:50],
            },
            "derivation_notes": [
                "followup_rate: derived from advisor_mode==recall count / total_sessions",
                "clarification_rate: derived from advisor_mode==review count / total_sessions",
                "no_evidence_abstain_rate: derived from (cited_empty - planning_count) / total_sessions",
                "cited_empty_rate: fraction of sessions with cited_memory_ids being None or empty list",
                "confidence bands: high >=0.7, medium [0.4, 0.7), low [0.0, 0.4)",
            ],
            "privacy_notes": [
                "All values are aggregated counts only; no raw question/answer text is included.",
                "session_hashes are 12-character SHA-256 prefixes (opaque IDs) capped at 50 samples.",
            ],
        }
        return report
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--user",
        default=SOLO_USER_ID,
        help="user_id to aggregate (defaults to solo-user)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="output JSON file path; if omitted, prints to stdout",
    )
    args = parser.parse_args()

    report = aggregate_answer_quality(user_id=args.user)
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
        print(f"Wrote baseline report to {args.output}")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
