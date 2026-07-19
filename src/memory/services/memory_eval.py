"""Deterministic MemoryEval catalogue for the project's memory invariants.

The catalogue deliberately points to isolated fixtures and existing targeted
tests.  It does not contain user memories and it never calls a real model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping


MEMORY_EVAL_OBSERVATION_FIELDS = frozenset({
    "observation_id",
    "expected_memory_ids",
    "retrieved_memory_ids",
    "expected_source_ids",
    "cited_source_ids",
    "should_abstain",
    "did_abstain",
    "expected_temporal_label",
    "actual_temporal_label",
})


@dataclass(frozen=True)
class MemoryEvalCase:
    case_id: str
    capability: str
    pytest_nodeids: tuple[str, ...]


@dataclass(frozen=True)
class MemoryEvalObservation:
    """One human-labelled, content-free evaluation result.

    IDs must be opaque test or consented-trial identifiers.  This object
    deliberately has no query, memory content, answer text, or user ID field.
    ``None`` means a dimension was not labelled and must not be scored.
    """

    observation_id: str
    expected_memory_ids: tuple[str, ...] = ()
    retrieved_memory_ids: tuple[str, ...] = ()
    expected_source_ids: tuple[str, ...] | None = None
    cited_source_ids: tuple[str, ...] = ()
    should_abstain: bool | None = None
    did_abstain: bool | None = None
    expected_temporal_label: str | None = None
    actual_temporal_label: str | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "MemoryEvalObservation":
        """Load a JSON object while rejecting missing or malformed identifiers."""
        unsupported_fields = sorted(set(value).difference(MEMORY_EVAL_OBSERVATION_FIELDS))
        if unsupported_fields:
            raise ValueError(f"unsupported fields: {', '.join(unsupported_fields)}")

        observation_id = value.get("observation_id")
        if not isinstance(observation_id, str) or not observation_id.strip():
            raise ValueError("observation_id must be a non-empty string")

        def ids(name: str, *, allow_none: bool = False) -> tuple[str, ...] | None:
            raw = value.get(name)
            if raw is None and allow_none:
                return None
            if raw is None:
                return ()
            if not isinstance(raw, list) or not all(isinstance(item, str) and item for item in raw):
                raise ValueError(f"{name} must be an array of non-empty strings")
            return tuple(raw)

        def optional_bool(name: str) -> bool | None:
            raw = value.get(name)
            if raw is None:
                return None
            if not isinstance(raw, bool):
                raise ValueError(f"{name} must be true, false, or omitted")
            return raw

        def optional_label(name: str) -> str | None:
            raw = value.get(name)
            if raw is None:
                return None
            if not isinstance(raw, str) or not raw.strip():
                raise ValueError(f"{name} must be a non-empty string or omitted")
            return raw

        return cls(
            observation_id=observation_id,
            expected_memory_ids=ids("expected_memory_ids") or (),
            retrieved_memory_ids=ids("retrieved_memory_ids") or (),
            expected_source_ids=ids("expected_source_ids", allow_none=True),
            cited_source_ids=ids("cited_source_ids") or (),
            should_abstain=optional_bool("should_abstain"),
            did_abstain=optional_bool("did_abstain"),
            expected_temporal_label=optional_label("expected_temporal_label"),
            actual_temporal_label=optional_label("actual_temporal_label"),
        )


@dataclass(frozen=True)
class MemoryEvalMetrics:
    observation_count: int
    retrieval_case_count: int
    recall_at_k: dict[int, float]
    mrr: float | None
    citation_accuracy: float | None
    source_coverage: float | None
    abstention_accuracy: float | None
    temporal_accuracy: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "observation_count": self.observation_count,
            "retrieval_case_count": self.retrieval_case_count,
            "recall_at_k": {str(key): value for key, value in self.recall_at_k.items()},
            "mrr": self.mrr,
            "citation_accuracy": self.citation_accuracy,
            "source_coverage": self.source_coverage,
            "abstention_accuracy": self.abstention_accuracy,
            "temporal_accuracy": self.temporal_accuracy,
        }


MEMORY_EVAL_CASES: tuple[MemoryEvalCase, ...] = (
    MemoryEvalCase("ME-01", "single_fact_recall", ("tests/integration/test_gen3_closed_loop.py::test_loop1_memory_recall",)),
    MemoryEvalCase("ME-02", "multi_session_combination", ("tests/unit/test_agent_runtime.py::test_conversation_ledger_is_durable_bounded_in_context_and_resettable",)),
    MemoryEvalCase("ME-03", "current_vs_historical_view", ("tests/unit/test_llm_governance_evaluation.py::test_temporal_and_epistemic_context_is_visible_to_retrieval_prompt",)),
    MemoryEvalCase("ME-04", "knowledge_update", ("tests/integration/test_cip_closed_loop.py::test_preclassified_user_evidence_traverses_case_decision_and_formal_memory",)),
    MemoryEvalCase("ME-05", "temporal_expiry", ("tests/unit/test_memory_governance_policy.py::test_expired_active_memory_is_not_retrievable",)),
    MemoryEvalCase("ME-06", "epistemic_separation", ("tests/unit/test_llm_governance_evaluation.py::test_proposition_provenance_keeps_user_assertion_separate_from_model_inference",)),
    MemoryEvalCase("ME-07", "maintenance_rollback", ("tests/unit/test_memory_operations_v251.py::test_merge_rollback_restores_both_memories_and_removes_derived_links",)),
    MemoryEvalCase("ME-08", "duplicate_commit_guard", ("tests/unit/test_memory_operations_v251.py::test_dedup_uses_bounded_neighbors_with_ten_thousand_memories",)),
    MemoryEvalCase("ME-09", "abstention_without_evidence", ("tests/unit/test_memory_governance_policy.py::test_no_textual_match_returns_no_importance_ranked_memories",)),
    MemoryEvalCase("ME-10", "citation_accuracy", ("tests/integration/test_security_regressions.py::test_memory_ask_discards_fabricated_citations_and_uses_real_source_type",)),
    MemoryEvalCase("ME-11", "no_source_answer", ("tests/integration/test_security_regressions.py::test_memory_ask_discards_fabricated_citations_and_uses_real_source_type",)),
    MemoryEvalCase("ME-12", "sensitive_memory_isolation", ("tests/unit/test_llm_governance_evaluation.py::test_task_scope_excludes_private_and_sensitive_memories",)),
    MemoryEvalCase("ME-13", "cross_user_isolation", ("tests/integration/test_data_portability.py::test_account_export_is_user_scoped_and_deletion_cleans_derivatives",)),
    MemoryEvalCase("ME-14", "deleted_memory_not_recalled", ("tests/integration/test_security_regressions.py::test_delete_memory_removes_content_sources_and_embeddings",)),
    MemoryEvalCase("ME-15", "correction_supersedes_current", ("tests/unit/test_working_memory_case.py::test_same_proposition_creates_a_superseding_formal_memory_revision",)),
    MemoryEvalCase("ME-16", "prompt_injection_resistance", ("tests/unit/test_llm_governance_evaluation.py::test_untrusted_raw_event_cannot_replace_extraction_instructions",)),
)


def memory_eval_nodeids() -> tuple[str, ...]:
    """Return ordered, de-duplicated deterministic test node IDs."""
    return tuple(dict.fromkeys(nodeid for case in MEMORY_EVAL_CASES for nodeid in case.pytest_nodeids))


def compute_memory_eval_metrics(
    observations: Iterable[MemoryEvalObservation],
    *,
    ks: tuple[int, ...] = (1, 3, 5),
) -> MemoryEvalMetrics:
    """Calculate transparent metrics from human-labelled opaque identifiers."""
    normalized_ks = tuple(sorted(set(ks)))
    if not normalized_ks or normalized_ks[0] <= 0:
        raise ValueError("ks must contain positive integers")

    items = tuple(observations)
    retrieval_items = tuple(item for item in items if item.expected_memory_ids)
    recall_at_k: dict[int, float] = {}
    if retrieval_items:
        for k in normalized_ks:
            recalls = [
                len(set(item.expected_memory_ids).intersection(item.retrieved_memory_ids[:k]))
                / len(set(item.expected_memory_ids))
                for item in retrieval_items
            ]
            recall_at_k[k] = sum(recalls) / len(recalls)

    reciprocal_ranks: list[float] = []
    for item in retrieval_items:
        expected = set(item.expected_memory_ids)
        rank = next((index for index, memory_id in enumerate(item.retrieved_memory_ids, start=1) if memory_id in expected), None)
        reciprocal_ranks.append(0.0 if rank is None else 1.0 / rank)

    citation_items = tuple(item for item in items if item.expected_source_ids is not None)
    source_expected = [source_id for item in citation_items for source_id in item.expected_source_ids or ()]
    source_hits = [
        source_id
        for item in citation_items
        for source_id in item.expected_source_ids or ()
        if source_id in item.cited_source_ids
    ]
    abstention_items = tuple(item for item in items if item.should_abstain is not None and item.did_abstain is not None)
    temporal_items = tuple(
        item
        for item in items
        if item.expected_temporal_label is not None and item.actual_temporal_label is not None
    )

    return MemoryEvalMetrics(
        observation_count=len(items),
        retrieval_case_count=len(retrieval_items),
        recall_at_k=recall_at_k,
        mrr=sum(reciprocal_ranks) / len(reciprocal_ranks) if reciprocal_ranks else None,
        citation_accuracy=(
            sum(set(item.expected_source_ids or ()) == set(item.cited_source_ids) for item in citation_items) / len(citation_items)
            if citation_items
            else None
        ),
        source_coverage=sum(1 for _ in source_hits) / len(source_expected) if source_expected else None,
        abstention_accuracy=(
            sum(item.should_abstain == item.did_abstain for item in abstention_items) / len(abstention_items)
            if abstention_items
            else None
        ),
        temporal_accuracy=(
            sum(item.expected_temporal_label == item.actual_temporal_label for item in temporal_items) / len(temporal_items)
            if temporal_items
            else None
        ),
    )
