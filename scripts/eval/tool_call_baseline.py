"""Tool call baseline collector.

对只读工具（read_memory、manage_task list）跑 N 次，采集 latency_ms
和 failure_rate。

脚本不接入运行时，仅做基线快照。输出脱敏 JSON，仅含聚合数字。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.shared.config import settings
from src.shared.db.database import async_session
from src.shared.security.dependencies import SOLO_USER_ID


def _opaque_hash(value: str) -> str:
    if not value:
        return ""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


# 虚构的代表性查询，不引用任何真实用户数据
REPRESENTATIVE_QUERIES = (
    "我今天有什么待办？",
    "最近有没有关于工作的记录？",
    "我之前的偏好是什么？",
    "请列出我的项目列表。",
    "最近一次会话讨论了什么？",
)


async def _measure_read_memory(
    *,
    db_session,
    user_id: str,
    question: str,
) -> dict:
    """调用 RetrievalEngine.reconstruct_context 并采集 latency。"""
    from src.memory.services.retrieval_engine import RetrievalEngine

    engine = RetrievalEngine(db_session)
    start = time.perf_counter()
    error: str | None = None
    try:
        result = await engine.reconstruct_context(
            user_id=user_id,
            question=question,
            recall_level="work_context",
        )
        # 简单提取结果大小用于诊断
        if isinstance(result, dict):
            _ = len(result.get("memories") or [])
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    latency_ms = (time.perf_counter() - start) * 1000.0
    return {
        "latency_ms": round(latency_ms, 2),
        "error": error,
    }


async def _measure_manage_task_list(
    *,
    db_session,
    user_id: str,
) -> dict:
    """调用 TaskSystem.list_tasks 并采集 latency（只读，不创建任务）。"""
    from src.execution.services.task_system import TaskSystem

    task_system = TaskSystem(db_session)
    start = time.perf_counter()
    error: str | None = None
    try:
        tasks = await task_system.list_tasks(user_id, status=None)
        _ = len(tasks)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    latency_ms = (time.perf_counter() - start) * 1000.0
    return {
        "latency_ms": round(latency_ms, 2),
        "error": error,
    }


async def collect_tool_baseline(*, samples: int, user_id: str) -> dict:
    tool_calls: list[dict] = []
    success_count = 0
    failure_count = 0
    read_memory_latencies: list[float] = []
    manage_task_latencies: list[float] = []

    queries_to_run = []
    for index in range(samples):
        query = REPRESENTATIVE_QUERIES[index % len(REPRESENTATIVE_QUERIES)]
        queries_to_run.append((index + 1, query))

    async with async_session() as db_session:
        for index, query in queries_to_run:
            # read_memory
            rm_result = await _measure_read_memory(
                db_session=db_session,
                user_id=user_id,
                question=query,
            )
            if rm_result["error"] is None:
                success_count += 1
                read_memory_latencies.append(rm_result["latency_ms"])
            else:
                failure_count += 1
            tool_calls.append({
                "sample_index": index,
                "tool_name": "read_memory",
                "query_opaque": _opaque_hash(query),
                "latency_ms": rm_result["latency_ms"],
                "error": rm_result["error"],
            })

            # manage_task list (read-only)
            mt_result = await _measure_manage_task_list(
                db_session=db_session,
                user_id=user_id,
            )
            if mt_result["error"] is None:
                success_count += 1
                manage_task_latencies.append(mt_result["latency_ms"])
            else:
                failure_count += 1
            tool_calls.append({
                "sample_index": index,
                "tool_name": "manage_task_list",
                "query_opaque": _opaque_hash(query),
                "latency_ms": mt_result["latency_ms"],
                "error": mt_result["error"],
            })

    def _stats(latencies: list[float]) -> dict:
        if not latencies:
            return {"avg": 0.0, "p50": 0.0, "p95": 0.0, "min": 0.0, "max": 0.0}
        sorted_l = sorted(latencies)
        p50 = sorted_l[len(sorted_l) // 2]
        p95_idx = int(len(sorted_l) * 0.95)
        p95 = sorted_l[min(p95_idx, len(sorted_l) - 1)]
        return {
            "avg": round(sum(latencies) / len(latencies), 2),
            "p50": round(p50, 2),
            "p95": round(p95, 2),
            "min": round(min(latencies), 2),
            "max": round(max(latencies), 2),
        }

    total_calls = len(tool_calls)
    failure_rate = round(failure_count / total_calls, 4) if total_calls else 0.0

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "user_id_opaque": _opaque_hash(user_id),
        "samples_requested": samples,
        "total_calls": total_calls,
        "metrics": {
            "success_count": success_count,
            "failure_count": failure_count,
            "failure_rate": failure_rate,
            "read_memory_latency_ms": _stats(read_memory_latencies),
            "manage_task_list_latency_ms": _stats(manage_task_latencies),
        },
        "calls": tool_calls,
        "privacy_notes": [
            "All query content is hashed; no raw user query text is included in the output.",
            "Only read-only tool calls (read_memory, manage_task list) are measured.",
            "No tasks are created or modified during baseline collection.",
        ],
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--samples",
        type=int,
        default=10,
        help="number of tool calls per tool (default: 10)",
    )
    parser.add_argument(
        "--user",
        default=SOLO_USER_ID,
        help="user_id to use (defaults to solo-user)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="output JSON file path; if omitted, prints to stdout",
    )
    args = parser.parse_args()

    report = asyncio.run(collect_tool_baseline(samples=args.samples, user_id=args.user))
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
        print(f"Wrote tool call baseline to {args.output}")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
