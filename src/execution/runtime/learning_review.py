"""Offline-only review artifacts for controlled Agent learning releases.

This module deliberately produces review evidence and a release decision only.
It never changes prompts, Skills, tool permissions, governance thresholds, or
memory records at runtime.
"""
from __future__ import annotations

from hashlib import sha256
import json
from math import isfinite
from typing import Mapping

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.execution.models.audit_log import AuditLog
from src.execution.models.memory_work import MemoryWorkCase, MemoryWorkDecision
from src.memory.models.committed_memory import CommittedMemory

from .prompt_registry import list_prompts
from .skills import list_skills


_FEEDBACK_STATUSES = ("accepted", "corrected", "ignored", "closed")


def _metric_map(values: Mapping[str, object] | None) -> dict[str, float]:
    output: dict[str, float] = {}
    for key, value in (values or {}).items():
        if not isinstance(key, str) or not isinstance(value, (int, float)):
            continue
        numeric = float(value)
        if isfinite(numeric):
            output[key] = numeric
    return output


def build_learning_review(
    *,
    feedback_counts: Mapping[str, object] | None,
    baseline_metrics: Mapping[str, object] | None,
    candidate_metrics: Mapping[str, object] | None,
) -> dict:
    """Build a content-free, reproducible artifact for an offline human review."""
    normalized_feedback: dict[str, int] = {}
    for status in _FEEDBACK_STATUSES:
        value = (feedback_counts or {}).get(status)
        normalized_feedback[status] = (
            max(0, int(value)) if isinstance(value, (int, float)) else 0
        )
    baseline = _metric_map(baseline_metrics)
    candidate = _metric_map(candidate_metrics)
    registry_versions = {
        "prompts": {item.prompt_id: item.version for item in list_prompts().values()},
        "skills": {item.skill_id: item.version for item in list_skills().values()},
    }
    fingerprint_payload = {
        "feedback_counts": normalized_feedback,
        "baseline_metrics": baseline,
        "candidate_metrics": candidate,
        "registry_versions": registry_versions,
    }
    review_id = sha256(
        json.dumps(fingerprint_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:24]
    return {
        "schema_version": "learning-review-v1",
        "review_id": review_id,
        "change_mode": "offline_review_only",
        "contains_user_content": False,
        "feedback_counts": normalized_feedback,
        "baseline_metrics": baseline,
        "candidate_metrics": candidate,
        "registry_versions": registry_versions,
        "approval_requirements": [
            "named_human_reviewer",
            "candidate_metrics_not_worse_than_baseline",
            "manual_code_review_required",
            "documented_rollback_target",
        ],
        "rollback_target": registry_versions,
    }


async def build_learning_review_from_audit_logs(
    db: AsyncSession,
    *,
    user_id: str,
    baseline_metrics: Mapping[str, object] | None,
    candidate_metrics: Mapping[str, object] | None,
) -> dict:
    """Aggregate only feedback labels from durable audit records for offline review."""
    rows = list((await db.execute(
        select(AuditLog.detail).where(
            AuditLog.user_id == user_id,
            AuditLog.action == "insight_feedback",
        )
    )).scalars())
    feedback_counts = {status: 0 for status in _FEEDBACK_STATUSES}
    for detail in rows:
        try:
            payload = json.loads(detail) if isinstance(detail, str) else {}
        except json.JSONDecodeError:
            continue
        status = payload.get("status") if isinstance(payload, dict) else None
        if status in feedback_counts:
            feedback_counts[status] += 1
    return build_learning_review(
        feedback_counts=feedback_counts,
        baseline_metrics=baseline_metrics,
        candidate_metrics=candidate_metrics,
    )


def validate_learning_release(review: Mapping[str, object], *, reviewer: str, decision: str) -> dict:
    """Validate an explicit human decision without applying any runtime mutation."""
    reasons: list[str] = []
    if not reviewer.strip():
        reasons.append("missing_human_reviewer")
    if decision != "approved":
        reasons.append("decision_not_approved")
    baseline_value = review.get("baseline_metrics")
    candidate_value = review.get("candidate_metrics")
    baseline = _metric_map(
        baseline_value if isinstance(baseline_value, Mapping) else None
    )
    candidate = _metric_map(
        candidate_value if isinstance(candidate_value, Mapping) else None
    )
    if not baseline:
        reasons.append("missing_baseline_metrics")
    for metric, baseline_value in baseline.items():
        candidate_value = candidate.get(metric)
        if candidate_value is None:
            reasons.append(f"missing_candidate_metric:{metric}")
        elif candidate_value < baseline_value:
            reasons.append(f"metric_regressed:{metric}")
    approved = not reasons
    return {
        "review_id": review.get("review_id") if isinstance(review, Mapping) else None,
        "approved": approved,
        "reasons": reasons,
        "release_action": "manual_code_review_required" if approved else "keep_current_version",
        "rollback_target": review.get("rollback_target") if isinstance(review, Mapping) else None,
        "runtime_mutation": False,
    }


async def build_working_quality_snapshot(db: AsyncSession, *, user_id: str) -> dict:
    """Return content-free Working-Agent outcome counts for offline review."""
    decision_rows = (
        await db.execute(
            select(MemoryWorkDecision.state, func.count(MemoryWorkDecision.id))
            .where(MemoryWorkDecision.user_id == user_id)
            .group_by(MemoryWorkDecision.state)
        )
    ).all()
    case_rows = (
        await db.execute(
            select(MemoryWorkCase.status, func.count(MemoryWorkCase.id))
            .where(MemoryWorkCase.user_id == user_id)
            .group_by(MemoryWorkCase.status)
        )
    ).all()
    decision_counts = {str(status): int(count) for status, count in decision_rows}
    case_counts = {str(status): int(count) for status, count in case_rows}
    automatic_memory_count = int(
        await db.scalar(
            select(func.count(CommittedMemory.id)).where(
                CommittedMemory.user_id == user_id,
                CommittedMemory.origin_kind == "working_agent",
            )
        )
        or 0
    )
    return {
        "schema_version": "working-quality-v2",
        "contains_user_content": False,
        "decision_counts": decision_counts,
        "case_counts": case_counts,
        "automatic_memory_count": automatic_memory_count,
        "runtime_mutation": False,
    }
