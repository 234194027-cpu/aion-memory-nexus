"""Run the deterministic MemoryEval baseline without real personal data or LLM calls."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.memory.services.memory_eval import (
    MEMORY_EVAL_CASES,
    MemoryEvalObservation,
    compute_memory_eval_metrics,
    memory_eval_nodeids,
)
from src.cognition.services.second_brain_eval import (  # noqa: E402
    SecondBrainEvalObservation,
    compute_second_brain_eval_metrics,
)
from src.execution.runtime.quality_eval import (  # noqa: E402
    ConversationQualityObservation,
    compute_quality_metrics,
)


CONVERSATION_EVAL_PATH = PROJECT_ROOT / "docs" / "eval" / "conversation-eval.jsonl"
RAW_EVENT_EVAL_PATH = PROJECT_ROOT / "docs" / "eval" / "raw-event-eval.jsonl"

CONVERSATION_SCENARIO_TYPES = (
    "historical_fact_qa",
    "decision_reason_qa",
    "insufficient_info_followup",
    "activity_vs_chat",
    "user_correction",
    "sensitive_unauthorized_request",
    "tool_failure_timeout",
    "long_session_compressed",
    "no_memory_abstain",
    "identity_and_rename",
    "natural_answer",
    "topic_switch",
    "explicit_record",
    "deadline_plan",
    "next_day_continuation",
    "working_handoff",
    "proactive_cooldown",
)

RAW_EVENT_KINDS = (
    "single_fact",
    "multi_fact",
    "noise",
    "synonym_duplicate",
    "preference_change",
    "explicit_correction",
    "temporal_validity",
    "source_credibility_diff",
    "low_confidence_inference",
    "retry_idempotent",
)

CONVERSATION_REQUIRED_FIELDS = (
    "sample_id",
    "scenario_type",
    "user_message",
    "context_summary",
    "expected_behavior",
)

RAW_EVENT_REQUIRED_FIELDS = (
    "sample_id",
    "event_kind",
    "anonymized_content",
    "event_metadata",
    "expected_candidates",
)


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(f"evaluation set not found: {path}")
    items: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            stripped = raw_line.strip()
            if not stripped:
                continue
            try:
                item = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number} invalid JSON: {exc}") from exc
            if not isinstance(item, dict):
                raise ValueError(f"{path}:{line_number} each line must be a JSON object")
            items.append(item)
    return items


def _validate_conversation_eval(items: list[dict]) -> tuple[int, int]:
    seen_ids: set[str] = set()
    scenario_types: set[str] = set()
    for index, item in enumerate(items, start=1):
        for field in CONVERSATION_REQUIRED_FIELDS:
            if field not in item:
                raise ValueError(f"conversation sample #{index} missing required field: {field}")
        sample_id = item["sample_id"]
        if not isinstance(sample_id, str) or not sample_id:
            raise ValueError(f"conversation sample #{index} sample_id must be a non-empty string")
        if sample_id in seen_ids:
            raise ValueError(f"conversation sample #{index} duplicate sample_id: {sample_id}")
        seen_ids.add(sample_id)

        scenario_type = item["scenario_type"]
        if not isinstance(scenario_type, str) or scenario_type not in CONVERSATION_SCENARIO_TYPES:
            raise ValueError(
                f"conversation sample {sample_id} scenario_type must be one of "
                f"{CONVERSATION_SCENARIO_TYPES}"
            )
        scenario_types.add(scenario_type)

        if not isinstance(item["user_message"], str) or not item["user_message"].strip():
            raise ValueError(f"conversation sample {sample_id} user_message must be a non-empty string")
        if not isinstance(item["context_summary"], str) or not item["context_summary"].strip():
            raise ValueError(f"conversation sample {sample_id} context_summary must be a non-empty string")
        behaviors = item["expected_behavior"]
        if not isinstance(behaviors, list) or not behaviors:
            raise ValueError(f"conversation sample {sample_id} expected_behavior must be a non-empty array")
        if not all(isinstance(entry, str) and entry for entry in behaviors):
            raise ValueError(f"conversation sample {sample_id} expected_behavior entries must be non-empty strings")

    return len(items), len(scenario_types)


def _validate_raw_event_eval(items: list[dict]) -> tuple[int, int]:
    seen_ids: set[str] = set()
    event_kinds: set[str] = set()
    for index, item in enumerate(items, start=1):
        for field in RAW_EVENT_REQUIRED_FIELDS:
            if field not in item:
                raise ValueError(f"raw event sample #{index} missing required field: {field}")
        sample_id = item["sample_id"]
        if not isinstance(sample_id, str) or not sample_id:
            raise ValueError(f"raw event sample #{index} sample_id must be a non-empty string")
        if sample_id in seen_ids:
            raise ValueError(f"raw event sample #{index} duplicate sample_id: {sample_id}")
        seen_ids.add(sample_id)

        event_kind = item["event_kind"]
        if not isinstance(event_kind, str) or event_kind not in RAW_EVENT_KINDS:
            raise ValueError(
                f"raw event sample {sample_id} event_kind must be one of "
                f"{RAW_EVENT_KINDS}"
            )
        event_kinds.add(event_kind)

        if not isinstance(item["anonymized_content"], str) or not item["anonymized_content"].strip():
            raise ValueError(f"raw event sample {sample_id} anonymized_content must be a non-empty string")
        if not isinstance(item["event_metadata"], dict):
            raise ValueError(f"raw event sample {sample_id} event_metadata must be a JSON object")
        candidates = item["expected_candidates"]
        if not isinstance(candidates, dict):
            raise ValueError(f"raw event sample {sample_id} expected_candidates must be a JSON object")

    return len(items), len(event_kinds)


def _print_conversation_eval_summary() -> int:
    items = _load_jsonl(CONVERSATION_EVAL_PATH)
    sample_count, scenario_count = _validate_conversation_eval(items)
    print(f"{sample_count} samples, {scenario_count} scenario types, schema valid")
    return 0


def _print_raw_event_eval_summary() -> int:
    items = _load_jsonl(RAW_EVENT_EVAL_PATH)
    sample_count, kind_count = _validate_raw_event_eval(items)
    print(f"{sample_count} samples, {kind_count} event kinds, schema valid")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="print cases or eval summary without executing pytest")
    parser.add_argument(
        "--metrics-file",
        type=Path,
        help="calculate metrics from an anonymous, human-labelled JSON observation array",
    )
    parser.add_argument(
        "--v251-quality-metrics-file",
        type=Path,
        help="calculate content-free V2.5.1 conversation and memory quality metrics",
    )
    parser.add_argument(
        "--second-brain-metrics-file",
        type=Path,
        help="calculate anonymous V2 time/correction/relationship/open-loop/reflection/reminder slices",
    )
    parser.add_argument(
        "--conversation-eval",
        action="store_true",
        help="validate the anonymous conversation evaluation set (no LLM call)",
    )
    parser.add_argument(
        "--raw-event-eval",
        action="store_true",
        help="validate the anonymous RawEvent evaluation set (no LLM call)",
    )
    args = parser.parse_args()

    if args.conversation_eval:
        return _print_conversation_eval_summary()

    if args.raw_event_eval:
        return _print_raw_event_eval_summary()

    if args.list:
        for case in MEMORY_EVAL_CASES:
            print(f"{case.case_id}\t{case.capability}\t{','.join(case.pytest_nodeids)}")
        return 0

    if args.metrics_file:
        try:
            raw_observations = json.loads(args.metrics_file.read_text(encoding="utf-8"))
            if not isinstance(raw_observations, list):
                raise ValueError("metrics file must contain a JSON array")
            observations = [MemoryEvalObservation.from_mapping(item) for item in raw_observations if isinstance(item, dict)]
            if len(observations) != len(raw_observations):
                raise ValueError("metrics file items must be JSON objects")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"MemoryEval metrics input error: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(compute_memory_eval_metrics(observations).to_dict(), ensure_ascii=False, indent=2))
        return 0

    if args.second_brain_metrics_file:
        try:
            raw_observations = json.loads(args.second_brain_metrics_file.read_text(encoding="utf-8"))
            if not isinstance(raw_observations, list):
                raise ValueError("second-brain metrics file must contain a JSON array")
            observations = [SecondBrainEvalObservation.from_mapping(item) for item in raw_observations if isinstance(item, dict)]
            if len(observations) != len(raw_observations):
                raise ValueError("second-brain metric items must be JSON objects")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"SecondBrainEval metrics input error: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(compute_second_brain_eval_metrics(observations).to_dict(), ensure_ascii=False, indent=2))
        return 0

    if args.v251_quality_metrics_file:
        try:
            raw_observations = json.loads(args.v251_quality_metrics_file.read_text(encoding="utf-8"))
            if not isinstance(raw_observations, list):
                raise ValueError("V2.5.1 quality metrics file must contain a JSON array")
            observations = [
                ConversationQualityObservation.from_mapping(item)
                for item in raw_observations
                if isinstance(item, dict)
            ]
            if len(observations) != len(raw_observations):
                raise ValueError("V2.5.1 quality metric items must be JSON objects")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"V2.5.1 quality metrics input error: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(compute_quality_metrics(observations), ensure_ascii=False, indent=2))
        return 0

    command = [sys.executable, "-X", "utf8", "-m", "pytest", *memory_eval_nodeids(), "-q", "-p", "no:faulthandler"]
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
