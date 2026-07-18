"""LLM call baseline collector.

关闭 `_LLM_CACHE` 后跑 N 次代表性 prompt（来自 `docs/eval/conversation-eval.jsonl`），
采集：
- prompt_tokens、completion_tokens（从 provider usage 字段）
- latency_ms（每次调用耗时）
- model_name、provider_type
- cost_estimate（基于价格表）

脚本不接入运行时，仅做基线快照。输出脱敏 JSON，仅含聚合数字与 opaque prompt hash。
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
from src.shared.security.outbound_url import assert_safe_llm_endpoint


CONVERSATION_EVAL_PATH = PROJECT_ROOT / "docs" / "eval" / "conversation-eval.jsonl"

# DeepSeek 官方定价（人民币元/1K tokens，仅供参考；实际以 provider 公告为准）
# 来源：DeepSeek 官方定价页面（公开）
DEFAULT_PRICE_TABLE_RMB_PER_1K = {
    "deepseek-chat": {"input": 0.001, "output": 0.002},
    "deepseek-coder": {"input": 0.001, "output": 0.002},
    "default": {"input": 0.001, "output": 0.002},
}


def _opaque_hash(value: str) -> str:
    if not value:
        return ""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _load_conversation_samples(limit: int) -> list[dict]:
    if not CONVERSATION_EVAL_PATH.exists():
        raise FileNotFoundError(f"conversation eval not found: {CONVERSATION_EVAL_PATH}")
    samples: list[dict] = []
    with CONVERSATION_EVAL_PATH.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                item = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict) and item.get("user_message"):
                samples.append(item)
            if len(samples) >= limit:
                break
    return samples


async def _call_deepseek_once(
    *,
    prompt: str,
    api_key: str,
    api_url: str,
    model_name: str,
    timeout: float = 60.0,
) -> dict:
    """直接通过 httpx 调用 DeepSeek API 并返回完整响应（含 usage）。

    绕过 src.shared.llm.providers.DeepSeekProvider.generate() 以保留 usage
    字段；同时不使用 _LLM_CACHE。
    """
    import httpx

    endpoint = f"{api_url.rstrip('/')}/chat/completions"
    await assert_safe_llm_endpoint(endpoint, "openai")
    start = time.perf_counter()
    latency_ms: float = 0.0
    error: str | None = None
    completion_text: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            response = await client.post(
                endpoint,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model_name,
                    "messages": [
                        {"role": "system", "content": "You are a helpful assistant."},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.3,
                    "max_tokens": 256,
                },
            )
            latency_ms = (time.perf_counter() - start) * 1000.0
            response.raise_for_status()
            data = response.json()
            completion_text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            usage = data.get("usage", {}) or {}
            prompt_tokens = int(usage.get("prompt_tokens") or 0)
            completion_tokens = int(usage.get("completion_tokens") or 0)
            total_tokens = int(usage.get("total_tokens") or 0)
        except Exception as exc:
            if latency_ms == 0.0:
                latency_ms = (time.perf_counter() - start) * 1000.0
            error = f"{type(exc).__name__}: {exc}"
            completion_text = ""

    return {
        "latency_ms": round(latency_ms, 2),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "completion_chars": len(completion_text),
        "error": error,
    }


def _estimate_cost_rmb(
    *,
    model_name: str,
    prompt_tokens: int,
    completion_tokens: int,
    price_table: dict,
) -> float:
    key = model_name if model_name in price_table else "default"
    prices = price_table.get(key, price_table["default"])
    return round(
        (prompt_tokens / 1000.0) * prices["input"]
        + (completion_tokens / 1000.0) * prices["output"],
        6,
    )


async def collect_llm_baseline(
    *,
    samples: int,
    model_name: str,
    provider_type: str,
    api_key: str,
    api_url: str,
    price_table: dict,
) -> dict:
    conversation_samples = _load_conversation_samples(samples)
    if not conversation_samples:
        raise RuntimeError("no conversation samples loaded; cannot run LLM baseline")

    call_results: list[dict] = []
    success_count = 0
    failure_count = 0
    total_prompt_tokens = 0
    total_completion_tokens = 0
    total_cost_rmb = 0.0
    latencies: list[float] = []

    for index, item in enumerate(conversation_samples, start=1):
        prompt = item["user_message"]
        sample_id = item.get("sample_id", f"sample-{index}")
        result = await _call_deepseek_once(
            prompt=prompt,
            api_key=api_key,
            api_url=api_url,
            model_name=model_name,
        )
        cost = _estimate_cost_rmb(
            model_name=model_name,
            prompt_tokens=result["prompt_tokens"],
            completion_tokens=result["completion_tokens"],
            price_table=price_table,
        )
        if result["error"] is None:
            success_count += 1
            total_prompt_tokens += result["prompt_tokens"]
            total_completion_tokens += result["completion_tokens"]
            total_cost_rmb += cost
            latencies.append(result["latency_ms"])
        else:
            failure_count += 1
        call_results.append({
            "sample_index": index,
            "sample_id_opaque": _opaque_hash(sample_id),
            "scenario_type": item.get("scenario_type"),
            "latency_ms": result["latency_ms"],
            "prompt_tokens": result["prompt_tokens"],
            "completion_tokens": result["completion_tokens"],
            "total_tokens": result["total_tokens"],
            "completion_chars": result["completion_chars"],
            "cost_rmb": cost,
            "error": result["error"],
        })

    latencies_sorted = sorted(latencies)
    p50 = latencies_sorted[len(latencies_sorted) // 2] if latencies_sorted else 0.0
    p95_index = int(len(latencies_sorted) * 0.95)
    p95 = latencies_sorted[min(p95_index, len(latencies_sorted) - 1)] if latencies_sorted else 0.0
    avg_latency = sum(latencies) / len(latencies) if latencies else 0.0

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provider_type": provider_type,
        "model_name": model_name,
        "api_url_opaque": _opaque_hash(api_url),
        "samples_requested": samples,
        "samples_executed": len(call_results),
        "metrics": {
            "success_count": success_count,
            "failure_count": failure_count,
            "total_prompt_tokens": total_prompt_tokens,
            "total_completion_tokens": total_completion_tokens,
            "total_cost_rmb": round(total_cost_rmb, 6),
            "avg_latency_ms": round(avg_latency, 2),
            "p50_latency_ms": round(p50, 2),
            "p95_latency_ms": round(p95, 2),
        },
        "calls": call_results,
        "price_table_rmb_per_1k_tokens": price_table,
        "privacy_notes": [
            "All prompt content is hashed; no raw user_message text is included in the output.",
            "api_url_opaque is a 12-character SHA-256 prefix; the actual API endpoint is not logged.",
            "API keys are never included in the output.",
        ],
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--samples",
        type=int,
        default=10,
        help="number of prompts to run (default: 10)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="output JSON file path; if omitted, prints to stdout",
    )
    parser.add_argument(
        "--model",
        default="deepseek-chat",
        help="model name (default: deepseek-chat)",
    )
    parser.add_argument(
        "--provider",
        default="deepseek",
        help="provider type label (default: deepseek)",
    )
    args = parser.parse_args()

    api_key = settings.DEEPSEEK_API_KEY
    api_url = settings.DEEPSEEK_API_URL
    if not api_key or api_key.startswith("your-"):
        print(
            "DEEPSEEK_API_KEY is not configured; cannot run real LLM baseline. "
            "Set it in .env before running this script.",
            file=sys.stderr,
        )
        return 2

    report = asyncio.run(
        collect_llm_baseline(
            samples=args.samples,
            model_name=args.model,
            provider_type=args.provider,
            api_key=api_key,
            api_url=api_url,
            price_table=DEFAULT_PRICE_TABLE_RMB_PER_1K,
        )
    )
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
        print(f"Wrote LLM baseline to {args.output}")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
