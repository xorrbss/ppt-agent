from copy import deepcopy

import pytest

from services.template_v2_poc import (
    TEMPLATE_V2_FORMAT,
    TemplateV2ContractError,
    get_template_v2_policy,
)
from services.template_v2_rollout import TemplateV2RolloutService


def _marked_row():
    return {
        "version": TEMPLATE_V2_FORMAT,
        "template_id": "internal-template",
        "slides": [
            {
                "id": "slide-1",
                "payload": {
                    "title": "CONFIDENTIAL_SLIDE_TEXT",
                    "html": "<h1>SECRET_AUTHORED_HTML</h1>",
                },
            }
        ],
        "prompt": "SECRET_PRESENTATION_PROMPT",
    }


def test_flag_off_blocks_discovery_and_creation():
    events = []
    service = TemplateV2RolloutService(
        get_template_v2_policy({}),
        observation_sink=events.append,
    )

    assert service.filter_discoverable(["internal-template", "other-template"]) == ()
    with pytest.raises(TemplateV2ContractError) as error:
        service.require_creation("internal-template")

    assert error.value.code == "template_v2_creation_disabled"
    assert [event["operation"] for event in events] == [
        "discover",
        "discover",
        "create",
    ]
    assert all(event["outcome"] == "blocked" for event in events)


def test_enabled_discovery_and_creation_still_require_allowlist():
    events = []
    service = TemplateV2RolloutService(
        get_template_v2_policy(
            {
                "ENABLE_TEMPLATE_V2_POC": "true",
                "TEMPLATE_V2_POC_TEMPLATE_ALLOWLIST": "internal-template",
            }
        ),
        observation_sink=events.append,
    )

    assert service.filter_discoverable(
        ["existing-template", "internal-template"]
    ) == ("internal-template",)
    service.require_creation("internal-template")

    with pytest.raises(TemplateV2ContractError) as error:
        service.require_creation("existing-template")
    assert error.value.code == "template_v2_template_not_allowed"
    assert [event["outcome"] for event in events] == [
        "blocked",
        "allowed",
        "allowed",
        "blocked",
    ]


def test_kill_switch_preserves_existing_marked_row_read_and_export():
    events = []
    row = _marked_row()
    before = deepcopy(row)
    service = TemplateV2RolloutService(
        get_template_v2_policy({}),
        observation_sink=events.append,
    )

    service.require_existing_read(row, "internal-template")
    service.require_existing_export(row, "internal-template", "pptx")
    service.require_existing_export(row, "internal-template", "pdf")

    assert row == before
    assert [event["operation"] for event in events] == ["read", "export", "export"]
    assert all(event["outcome"] == "allowed" for event in events)
    assert all(event["code"] == "existing_marked_row" for event in events)
    assert all(event["creation_enabled"] is False for event in events)


def test_existing_access_requires_explicit_v2_marker():
    events = []
    service = TemplateV2RolloutService(
        get_template_v2_policy({}),
        observation_sink=events.append,
    )

    with pytest.raises(TemplateV2ContractError) as error:
        service.require_existing_export(
            {"version": "v1", "content": "must not be logged"},
            "internal-template",
            "pptx",
        )

    assert error.value.code == "template_v2_marker_required"
    assert events[0]["outcome"] == "blocked"
    assert events[0]["code"] == "template_v2_marker_required"


def test_observation_schema_cannot_capture_presentation_content_or_raw_template_id():
    events = []
    row = _marked_row()
    service = TemplateV2RolloutService(
        get_template_v2_policy({}),
        observation_sink=events.append,
    )

    service.require_existing_read(row, row["template_id"])
    service.record_outcome(
        operation="export",
        outcome="fallback",
        template_id=row["template_id"],
        code="unsupported_element",
        export_type="pptx",
    )

    assert events[0]["template_id_hash"] == events[1]["template_id_hash"]
    assert set(events[0]) == {
        "schema_version",
        "event",
        "operation",
        "outcome",
        "format_marker",
        "template_id_hash",
        "creation_enabled",
        "code",
    }
    serialized = repr(events)
    for forbidden in (
        "internal-template",
        "CONFIDENTIAL_SLIDE_TEXT",
        "SECRET_AUTHORED_HTML",
        "SECRET_PRESENTATION_PROMPT",
    ):
        assert forbidden not in serialized


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        (
            {
                "operation": "export",
                "outcome": "failure",
                "template_id": "internal-template",
                "code": "confidential_slide_text",
            },
            "safe allowlist",
        ),
        (
            {
                "operation": "export",
                "outcome": "success",
                "template_id": "internal-template",
                "export_type": "html",
            },
            "export type",
        ),
    ],
)
def test_observation_rejects_unstructured_values(kwargs, message):
    service = TemplateV2RolloutService(
        get_template_v2_policy({}),
        observation_sink=lambda _event: None,
    )

    with pytest.raises(ValueError, match=message):
        service.record_outcome(**kwargs)
