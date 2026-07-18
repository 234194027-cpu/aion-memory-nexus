import pytest


def test_second_brain_eval_reports_only_labelled_capability_slices():
    from src.cognition.services.second_brain_eval import (
        SecondBrainEvalObservation,
        compute_second_brain_eval_metrics,
    )

    metrics = compute_second_brain_eval_metrics([
        SecondBrainEvalObservation(
            observation_id="opaque-1",
            temporal_correct=True,
            correction_correct=True,
            relationship_correct=True,
            open_loop_correct=True,
            citation_correct=True,
            reflection_support_complete=True,
            reminder_sent=True,
            reminder_useful=True,
            reminder_closed=False,
            reminder_repeated_within_7d=False,
        ),
        SecondBrainEvalObservation(
            observation_id="opaque-2",
            temporal_correct=False,
            correction_correct=True,
            reminder_sent=True,
            reminder_useful=False,
            reminder_closed=True,
            reminder_repeated_within_7d=True,
        ),
    ])

    assert metrics.to_dict() == {
        "observation_count": 2,
        "temporal_accuracy": 0.5,
        "correction_accuracy": 1.0,
        "relationship_accuracy": 1.0,
        "open_loop_accuracy": 1.0,
        "citation_accuracy": 1.0,
        "reflection_support_coverage": 1.0,
        "reminder_usefulness": 0.5,
        "reminder_close_rate": 0.5,
        "reminder_seven_day_repeat_rate": 0.5,
    }


def test_second_brain_eval_rejects_content_fields_and_unlabelled_metrics_stay_null():
    from src.cognition.services.second_brain_eval import (
        SecondBrainEvalObservation,
        compute_second_brain_eval_metrics,
    )

    with pytest.raises(ValueError, match="unsupported fields"):
        SecondBrainEvalObservation.from_mapping({"observation_id": "opaque-private", "raw_message": "do not store"})

    metrics = compute_second_brain_eval_metrics([SecondBrainEvalObservation(observation_id="opaque-empty")])
    assert metrics.to_dict()["temporal_accuracy"] is None
    assert metrics.to_dict()["reminder_usefulness"] is None
