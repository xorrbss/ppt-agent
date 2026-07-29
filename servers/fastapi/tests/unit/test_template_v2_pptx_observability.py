import math
from unittest.mock import Mock

import pytest

from services.template_v2_pptx_observability import (
    build_pptx_analysis_observation,
    log_pptx_analysis_observation,
)


def test_pptx_analysis_observation_has_only_aggregate_fields():
    assert build_pptx_analysis_observation(
        provider="deterministic-ooxml-static",
        status="completed",
        duration_ms=125.5,
        count=3,
    ) == {
        "provider": "deterministic-ooxml-static",
        "status": "completed",
        "duration_ms": 125.5,
        "count": 3,
    }


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        (
            {
                "provider": "private-deck.pptx",
                "status": "completed",
            },
            "provider",
        ),
        (
            {
                "provider": "deterministic-ooxml-static",
                "status": "Jane.Doe@example.com",
            },
            "status",
        ),
        (
            {
                "provider": "deterministic-ooxml-static",
                "status": "completed",
                "duration_ms": math.inf,
            },
            "duration_ms",
        ),
        (
            {
                "provider": "deterministic-ooxml-static",
                "status": "completed",
                "duration_ms": True,
            },
            "duration_ms",
        ),
        (
            {
                "provider": "deterministic-ooxml-static",
                "status": "completed",
                "count": -1,
            },
            "count",
        ),
        (
            {
                "provider": "deterministic-ooxml-static",
                "status": "completed",
                "count": True,
            },
            "count",
        ),
    ],
)
def test_pptx_analysis_observation_rejects_unbounded_or_unstructured_values(
    kwargs, message
):
    with pytest.raises(ValueError, match=message):
        build_pptx_analysis_observation(**kwargs)


def test_pptx_analysis_observation_has_no_arbitrary_metadata_entry_point():
    with pytest.raises(TypeError, match="unexpected keyword argument"):
        build_pptx_analysis_observation(
            provider="deterministic-ooxml-static",
            status="completed",
            filename="private-deck.pptx",
        )


def test_pptx_analysis_logger_emits_only_validated_four_field_payload():
    logger = Mock()

    event = log_pptx_analysis_observation(
        provider="deterministic-ooxml-static",
        status="completed",
        duration_ms=125.5,
        count=3,
        logger=logger,
    )

    assert event == {
        "provider": "deterministic-ooxml-static",
        "status": "completed",
        "duration_ms": 125.5,
        "count": 3,
    }
    logger.info.assert_called_once_with(
        "template_v2_pptx_analysis %s",
        (
            '{"count":3,"duration_ms":125.5,'
            '"provider":"deterministic-ooxml-static","status":"completed"}'
        ),
    )
