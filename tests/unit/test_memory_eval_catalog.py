import pytest

from src.memory.services.memory_eval import (
    MEMORY_EVAL_CASES,
    MemoryEvalObservation,
    compute_memory_eval_metrics,
    memory_eval_nodeids,
)


def test_memory_eval_catalog_has_the_required_deterministic_coverage() -> None:
    assert [case.case_id for case in MEMORY_EVAL_CASES] == [f"ME-{index:02d}" for index in range(1, 17)]
    assert len(memory_eval_nodeids()) >= 12
    assert all(case.pytest_nodeids for case in MEMORY_EVAL_CASES)


def test_memory_eval_metrics_calculates_only_from_anonymous_human_labels() -> None:
    observations = [
        MemoryEvalObservation(
            observation_id="eval-1",
            expected_memory_ids=("m-1",),
            retrieved_memory_ids=("m-1", "m-2"),
            expected_source_ids=("s-1",),
            cited_source_ids=("s-1",),
            should_abstain=False,
            did_abstain=False,
            expected_temporal_label="current",
            actual_temporal_label="current",
        ),
        MemoryEvalObservation(
            observation_id="eval-2",
            expected_memory_ids=("m-3", "m-4"),
            retrieved_memory_ids=("m-5", "m-4", "m-3"),
            expected_source_ids=("s-2",),
            cited_source_ids=(),
            should_abstain=True,
            did_abstain=True,
            expected_temporal_label="historical",
            actual_temporal_label="current",
        ),
    ]

    metrics = compute_memory_eval_metrics(observations, ks=(1, 3))

    assert metrics.observation_count == 2
    assert metrics.recall_at_k == {1: 0.5, 3: 1.0}
    assert metrics.mrr == 0.75
    assert metrics.citation_accuracy == 0.5
    assert metrics.source_coverage == 0.5
    assert metrics.abstention_accuracy == 1.0
    assert metrics.temporal_accuracy == 0.5


def test_memory_eval_metrics_leave_unlabelled_categories_unreported() -> None:
    metrics = compute_memory_eval_metrics([
        MemoryEvalObservation(
            observation_id="eval-unlabelled",
            expected_memory_ids=(),
            retrieved_memory_ids=(),
        )
    ])

    assert metrics.recall_at_k == {}
    assert metrics.mrr is None
    assert metrics.citation_accuracy is None
    assert metrics.source_coverage is None
    assert metrics.abstention_accuracy is None
    assert metrics.temporal_accuracy is None


def test_memory_eval_observation_rejects_unapproved_content_fields() -> None:
    with pytest.raises(ValueError, match="unsupported fields"):
        MemoryEvalObservation.from_mapping({
            "observation_id": "eval-private-input",
            "expected_memory_ids": ["memory-opaque-id"],
            "query": "this must not be accepted",
        })
