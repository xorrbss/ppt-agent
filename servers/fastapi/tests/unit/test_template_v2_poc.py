from copy import deepcopy

import pytest

from services.template_v2_poc import (
    TEMPLATE_V2_FORMAT,
    TemplateV2ContractError,
    adapt_template_v2_to_adaptive,
    get_template_v2_policy,
    require_template_v2_creation,
)


def source_fixture():
    return {
        "version": TEMPLATE_V2_FORMAT,
        "template_id": "poc-internal-1",
        "slides": [
            {"id": "slide-1", "payload": {"title": "Alpha"}},
            {"id": "slide-2", "payload": {"title": "Beta"}},
        ],
    }


def adaptive_adapter(source):
    return {
        "mode": "adaptive",
        "slides": [
            {
                "layout_group": source["template_id"],
                "layout": "title",
                "index": index,
                "content": deepcopy(slide["payload"]),
            }
            for index, slide in enumerate(source["slides"])
        ],
    }


def test_creation_and_discovery_are_default_off():
    policy = get_template_v2_policy({})

    assert policy.creation_enabled is False
    assert policy.can_create("poc-internal-1") is False
    assert policy.can_discover("poc-internal-1") is False


def test_creation_requires_flag_and_explicit_allowlist():
    policy = get_template_v2_policy(
        {
            "ENABLE_TEMPLATE_V2_POC": "true",
            "TEMPLATE_V2_POC_TEMPLATE_ALLOWLIST": "poc-internal-1, other",
        }
    )

    assert policy.can_create("poc-internal-1") is True
    assert policy.can_create("existing-template") is False
    require_template_v2_creation("poc-internal-1", policy)

    with pytest.raises(TemplateV2ContractError) as error:
        require_template_v2_creation("existing-template", policy)
    assert error.value.code == "template_v2_template_not_allowed"


def test_kill_switch_keeps_marked_rows_readable():
    policy = get_template_v2_policy({})

    assert policy.can_read(source_fixture()) is True
    assert policy.can_read({"version": "v1"}) is False


def test_adapter_is_adaptive_only_deterministic_and_non_mutating():
    source = source_fixture()
    before = deepcopy(source)

    first = adapt_template_v2_to_adaptive(source, adaptive_adapter)
    second = adapt_template_v2_to_adaptive(source, adaptive_adapter)

    assert source == before
    assert first == second
    assert first["mode"] == "adaptive"
    assert [slide["index"] for slide in first["slides"]] == [0, 1]


def test_adapter_rejects_duplicate_source_ids():
    source = source_fixture()
    source["slides"][1]["id"] = "slide-1"

    with pytest.raises(TemplateV2ContractError) as error:
        adapt_template_v2_to_adaptive(source, adaptive_adapter)
    assert error.value.code == "template_v2_duplicate_slide_id"


@pytest.mark.parametrize(
    "output",
    [
        {"mode": "authored", "slides": []},
        {
            "mode": "adaptive",
            "slides": [
                {
                    "layout_group": "poc",
                    "layout": "title",
                    "index": 0,
                    "content": {},
                    "html_content": "<h1>forbidden</h1>",
                },
                {
                    "layout_group": "poc",
                    "layout": "title",
                    "index": 1,
                    "content": {},
                },
            ],
        },
    ],
)
def test_phase_one_cannot_select_authored_output(output):
    with pytest.raises(TemplateV2ContractError) as error:
        adapt_template_v2_to_adaptive(source_fixture(), lambda _source: output)
    assert error.value.code == "template_v2_authored_output_forbidden"
