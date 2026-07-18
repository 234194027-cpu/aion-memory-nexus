"""Create a content-free offline learning review artifact from audit feedback."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.execution.runtime.learning_review import (  # noqa: E402
    build_learning_review_from_audit_logs,
    validate_learning_release,
)
from src.shared.db.database import async_session  # noqa: E402
from src.shared.security.dependencies import SOLO_USER_ID  # noqa: E402


def _load_metrics(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


async def _build(args: argparse.Namespace) -> dict:
    async with async_session() as db:
        review = await build_learning_review_from_audit_logs(
            db,
            user_id=args.user_id,
            baseline_metrics=_load_metrics(args.baseline_metrics),
            candidate_metrics=_load_metrics(args.candidate_metrics),
        )
    if args.decision:
        review["release_decision"] = validate_learning_release(
            review, reviewer=args.reviewer or "", decision=args.decision
        )
    return review


def main() -> int:
    parser = argparse.ArgumentParser(description="Build an offline-only V2 learning review artifact.")
    parser.add_argument("--baseline-metrics", type=Path, required=True, help="JSON object with baseline metric values")
    parser.add_argument("--candidate-metrics", type=Path, required=True, help="JSON object with candidate metric values")
    parser.add_argument("--user-id", default=SOLO_USER_ID, help="scope for local aggregation; never emitted")
    parser.add_argument("--decision", choices=("approved", "rejected"), help="explicit human release decision")
    parser.add_argument("--reviewer", help="named human reviewer; required for approval")
    parser.add_argument("--output", type=Path, help="write the review JSON locally instead of stdout")
    args = parser.parse_args()
    try:
        review = asyncio.run(_build(args))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"learning review input error: {exc}", file=sys.stderr)
        return 2
    rendered = json.dumps(review, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
