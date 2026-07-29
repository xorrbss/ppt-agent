from copy import deepcopy
import json
from pathlib import Path

import pytest

from services.template_v2_poc import (
    INTERNAL_TEMPLATE_V2_ID,
    FixtureTemplateV2Repository,
    TemplateV2ContractError,
    adapt_template_v2_to_adaptive,
    adapt_internal_template_v2,
    build_template_v2_export_contract,
    get_template_v2_policy,
    require_template_v2_creation,
    validate_internal_template_v2,
)


def source_fixture():
    fixture_path = (
        Path(__file__).parents[1]
        / "fixtures"
        / "template_v2"
        / "internal-title-body.v2.json"
    )
    return json.loads(fixture_path.read_text(encoding="utf-8"))

def test_creation_and_discovery_are_default_off():
    policy = get_template_v2_policy({})

    assert policy.creation_enabled is False
    assert policy.can_create(INTERNAL_TEMPLATE_V2_ID) is False
    assert policy.can_discover(INTERNAL_TEMPLATE_V2_ID) is False


def test_creation_requires_flag_and_explicit_allowlist():
    policy = get_template_v2_policy(
        {
            "ENABLE_TEMPLATE_V2_POC": "true",
            "TEMPLATE_V2_POC_TEMPLATE_ALLOWLIST": (
                f"{INTERNAL_TEMPLATE_V2_ID}, other"
            ),
        }
    )

    assert policy.can_create(INTERNAL_TEMPLATE_V2_ID) is True
    assert policy.can_create("existing-template") is False
    require_template_v2_creation(INTERNAL_TEMPLATE_V2_ID, policy)

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

    first = adapt_internal_template_v2(source)
    second = adapt_internal_template_v2(source)

    assert source == before
    assert first == second
    assert first["mode"] == "adaptive"
    assert [slide["index"] for slide in first["slides"]] == [0, 1]
    assert first["slides"][0]["content"] == {
        "title": "Quarterly direction",
        "body": "Focus the team on reliable delivery.",
    }
    assert first["slides"][0]["properties"]["template_v2"] == {
        "source_slide_id": "slide-1",
        "source_node_ids": {
            "title": "slide-1-title",
            "body": "slide-1-body",
        },
    }


def test_adapter_rejects_duplicate_source_ids():
    source = source_fixture()
    source["slides"][1]["id"] = "slide-1"

    with pytest.raises(TemplateV2ContractError) as error:
        adapt_internal_template_v2(source)
    assert error.value.code == "template_v2_duplicate_slide_id"


def test_internal_schema_rejects_unknown_node_type_and_fields():
    unknown_node = source_fixture()
    unknown_node["slides"][0]["nodes"][0]["type"] = "html"

    with pytest.raises(TemplateV2ContractError) as error:
        validate_internal_template_v2(unknown_node)
    assert error.value.code == "template_v2_unknown_node_type"

    unknown_field = source_fixture()
    unknown_field["slides"][0]["nodes"][0]["style"] = {"fontSize": 100}

    with pytest.raises(TemplateV2ContractError) as error:
        validate_internal_template_v2(unknown_field)
    assert error.value.code == "template_v2_invalid_node_fields"


def test_internal_schema_rejects_duplicate_node_ids():
    source = source_fixture()
    source["slides"][1]["nodes"][1]["id"] = "slide-1-title"

    with pytest.raises(TemplateV2ContractError) as error:
        validate_internal_template_v2(source)
    assert error.value.code == "template_v2_duplicate_node_id"


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


def enabled_policy():
    return get_template_v2_policy(
        {
            "ENABLE_TEMPLATE_V2_POC": "true",
            "TEMPLATE_V2_POC_TEMPLATE_ALLOWLIST": INTERNAL_TEMPLATE_V2_ID,
        }
    )


def test_fixture_repository_create_save_reopen_duplicate_undo_redo():
    repository = FixtureTemplateV2Repository(enabled_policy())
    source = source_fixture()
    before = deepcopy(source)

    created = repository.create(source)
    assert created == before
    source["title"] = "caller mutation"
    assert repository.reopen("presentation-1") == before

    edited = repository.reopen("presentation-1")
    edited["slides"][0]["nodes"][1]["text"] = "Ship the tested vertical slice."
    repository.save(edited)
    assert (
        repository.reopen("presentation-1")["slides"][0]["nodes"][1]["text"]
        == "Ship the tested vertical slice."
    )

    assert repository.undo("presentation-1") == before
    assert (
        repository.redo("presentation-1")["slides"][0]["nodes"][1]["text"]
        == "Ship the tested vertical slice."
    )

    duplicate = repository.duplicate("presentation-1", "presentation-copy-1")
    assert duplicate["presentation_id"] == "presentation-copy-1"
    assert duplicate["slides"][0]["id"] == "presentation-copy-1:slide-1"
    assert (
        duplicate["slides"][0]["nodes"][0]["id"]
        == "presentation-copy-1:slide-1-title"
    )
    assert repository.reopen("presentation-copy-1") == duplicate
    assert repository.reopen("presentation-1")["presentation_id"] == "presentation-1"


def test_kill_switch_blocks_new_rows_but_preserves_reopen_and_export():
    repository = FixtureTemplateV2Repository(enabled_policy())
    repository.create(source_fixture())
    repository.set_policy(get_template_v2_policy({}))

    reopened = repository.reopen("presentation-1")
    assert repository.policy.can_read(reopened) is True
    assert build_template_v2_export_contract(reopened, "pptx")["export_as"] == "pptx"

    new_source = source_fixture()
    new_source["presentation_id"] = "presentation-2"
    with pytest.raises(TemplateV2ContractError) as error:
        repository.create(new_source)
    assert error.value.code == "template_v2_creation_disabled"


@pytest.mark.parametrize("export_as", ["pptx", "pdf"])
def test_adaptive_export_contract_is_json_safe_and_authored_free(export_as):
    source = source_fixture()
    before = deepcopy(source)

    contract = build_template_v2_export_contract(source, export_as)

    assert source == before
    assert contract["export_as"] == export_as
    assert contract["presentation"]["mode"] == "adaptive"
    assert all("html_content" not in slide for slide in contract["presentation"]["slides"])
    assert json.loads(json.dumps(contract)) == contract


def test_export_contract_rejects_unknown_format():
    with pytest.raises(TemplateV2ContractError) as error:
        build_template_v2_export_contract(source_fixture(), "html")
    assert error.value.code == "template_v2_unsupported_export_format"
