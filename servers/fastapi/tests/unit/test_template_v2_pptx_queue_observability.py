from unittest.mock import Mock

import pytest

from services.template_v2_pptx_queue_observability import (
    build_pptx_queue_observation,
    log_pptx_queue_observation,
)


def test_queue_observation_contains_only_bounded_aggregate_fields():
    assert build_pptx_queue_observation(
        operation="recover",
        outcome="completed",
        count=2,
    ) == {
        "operation": "recover",
        "outcome": "completed",
        "count": 2,
    }


def test_recovery_observation_can_include_bounded_latency():
    assert build_pptx_queue_observation(
        operation="recover",
        outcome="completed",
        count=2,
        duration_ms=12.5,
    ) == {
        "operation": "recover",
        "outcome": "completed",
        "count": 2,
        "duration_ms": 12.5,
    }


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"operation": "private-job-id", "outcome": "completed"}, "operation"),
        ({"operation": "dispatch", "outcome": "tenant@example.com"}, "outcome"),
        ({"operation": "dispatch", "outcome": "completed", "count": -1}, "count"),
        ({"operation": "dispatch", "outcome": "completed", "count": True}, "count"),
        (
            {
                "operation": "recover",
                "outcome": "completed",
                "duration_ms": float("inf"),
            },
            "duration_ms",
        ),
        (
            {
                "operation": "recover",
                "outcome": "completed",
                "duration_ms": True,
            },
            "duration_ms",
        ),
    ],
)
def test_queue_observation_rejects_unbounded_values(kwargs, message):
    with pytest.raises(ValueError, match=message):
        build_pptx_queue_observation(**kwargs)


def test_queue_observation_has_no_metadata_entry_point():
    with pytest.raises(TypeError, match="unexpected keyword argument"):
        build_pptx_queue_observation(
            operation="dispatch",
            outcome="completed",
            filename="private-deck.pptx",
        )


def test_queue_observation_logger_emits_validated_payload():
    logger = Mock()

    event = log_pptx_queue_observation(
        operation="dispatch",
        outcome="completed",
        count=3,
        logger=logger,
    )

    assert event == {
        "operation": "dispatch",
        "outcome": "completed",
        "count": 3,
    }
    logger.info.assert_called_once_with(
        "template_v2_pptx_queue %s",
        '{"count":3,"operation":"dispatch","outcome":"completed"}',
    )
