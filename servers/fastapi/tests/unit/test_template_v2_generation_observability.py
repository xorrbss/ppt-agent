import math
from unittest.mock import Mock

import pytest

from services.template_v2_generation_observability import (
    build_template_v2_generation_observation,
    log_template_v2_generation_observation,
)


def test_generation_observation_hashes_template_id_and_has_bounded_dimensions():
    event = build_template_v2_generation_observation(
        operation="generate",
        outcome="success",
        template_id="private-template-name",
        template_revision=7,
        duration_ms=125.5,
    )

    assert event == {
        "schema_version": 1,
        "operation": "generate",
        "outcome": "success",
        "request_strategy": "template_v2",
        "generation_strategy": "template-v2",
        "generation_profile": "staged-a-hybrid-v1",
        "template_id_hash": "c395e72ace335f08",
        "template_revision": 7,
        "duration_ms": 125.5,
    }
    assert "private-template-name" not in str(event)


def test_export_failure_observation_uses_stable_code_without_exception_text():
    event = build_template_v2_generation_observation(
        operation="export",
        outcome="failure",
        template_id="canary-template",
        template_revision=3,
        duration_ms=250,
        export_type="pptx",
        code="template_v2_export_failed",
    )

    assert event["operation"] == "export"
    assert event["outcome"] == "failure"
    assert event["export_type"] == "pptx"
    assert event["code"] == "template_v2_export_failed"
    assert "canary-template" not in str(event)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        (
            {
                "operation": "private-operation",
                "outcome": "success",
                "template_id": "a",
                "template_revision": 1,
                "duration_ms": 1,
            },
            "operation",
        ),
        (
            {
                "operation": "generate",
                "outcome": "tenant@example.com",
                "template_id": "a",
                "template_revision": 1,
                "duration_ms": 1,
            },
            "outcome",
        ),
        (
            {
                "operation": "generate",
                "outcome": "success",
                "template_id": "a",
                "template_revision": 0,
                "duration_ms": 1,
            },
            "revision",
        ),
        (
            {
                "operation": "generate",
                "outcome": "success",
                "template_id": "a",
                "template_revision": 1,
                "duration_ms": math.inf,
            },
            "duration_ms",
        ),
        (
            {
                "operation": "export",
                "outcome": "success",
                "template_id": "a",
                "template_revision": 1,
                "duration_ms": 1,
            },
            "export_type",
        ),
        (
            {
                "operation": "generate",
                "outcome": "failure",
                "template_id": "a",
                "template_revision": 1,
                "duration_ms": 1,
                "code": "private-file-name.pptx",
            },
            "code",
        ),
    ],
)
def test_generation_observation_rejects_unbounded_dimensions(kwargs, message):
    with pytest.raises(ValueError, match=message):
        build_template_v2_generation_observation(**kwargs)


def test_generation_observation_has_no_arbitrary_metadata_entry_point():
    with pytest.raises(TypeError, match="unexpected keyword argument"):
        build_template_v2_generation_observation(
            operation="generate",
            outcome="success",
            template_id="a",
            template_revision=1,
            duration_ms=1,
            prompt="private prompt",
        )


def test_generation_logger_emits_validated_json_payload():
    logger = Mock()

    event = log_template_v2_generation_observation(
        operation="export",
        outcome="success",
        template_id="a",
        template_revision=1,
        duration_ms=50,
        export_type="pdf",
        logger=logger,
    )

    assert event["duration_ms"] == 50.0
    logger.info.assert_called_once()
    assert logger.info.call_args.args[0] == "template_v2_generation %s"
    assert '"export_type":"pdf"' in logger.info.call_args.args[1]
