from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from templates.v2.wire_codec import (
    ELEMENT_DISCRIMINATORS,
    UPSTREAM_TEMPLATE_V2_SHA,
    TemplateV2WireCodecError,
    decode_wire_element,
    decode_wire_layouts,
    dump_storage_element,
    dump_storage_layouts,
    load_storage_element,
    load_storage_layouts,
)


FIXTURE_PATH = (
    Path(__file__).parents[1]
    / "fixtures"
    / "template_v2"
    / "upstream-elements-57b194b.json"
)


@pytest.fixture(scope="module")
def upstream_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_fixture_is_pinned_to_the_reviewed_upstream_contract(upstream_fixture):
    assert upstream_fixture["upstream_sha"] == UPSTREAM_TEMPLATE_V2_SHA
    assert {
        element["type"] for element in upstream_fixture["elements"]
    } == ELEMENT_DISCRIMINATORS
    assert len(upstream_fixture["elements"]) == 11


@pytest.mark.parametrize("element_index", range(11))
def test_all_upstream_discriminators_round_trip_without_field_loss(
    upstream_fixture,
    element_index,
):
    source = upstream_fixture["elements"][element_index]

    wire = decode_wire_element(
        json.dumps(source, ensure_ascii=False).encode("utf-8")
    )
    stored = dump_storage_element(wire)
    loaded = load_storage_element(stored)

    assert wire.discriminator == source["type"]
    assert stored == source
    assert loaded.to_wire_value() == source


def test_unknown_fields_are_preserved_recursively_and_copies_are_isolated(
    upstream_fixture,
):
    source = deepcopy(upstream_fixture["elements"][0])
    wire = decode_wire_element(source)

    source["upstream_element_extension"] = "mutated-after-decode"
    source["runs"][0]["upstream_run_extension"]["locale"] = "en-US"
    first_dump = dump_storage_element(wire)
    first_dump["runs"][0]["upstream_run_extension"]["locale"] = "fr-FR"

    preserved = dump_storage_element(wire)
    assert preserved["upstream_element_extension"] == "text-vNext"
    assert preserved["runs"][0]["upstream_run_extension"] == {
        "locale": "ko-KR"
    }


def test_wire_acceptance_does_not_apply_editor_export_coercion():
    source = {
        "type": "chart",
        "position": {"x": "12.5", "y": 20},
        "chart_type": "bar",
        "categories": ["Q1"],
        "series": [{"name": "Revenue", "values": [10]}],
        "data_labels": True,
        "decorative": False,
        "name": "revenue",
    }

    wire = decode_wire_element(source)
    strict = wire.validate_strict()

    assert wire.to_storage_value()["position"]["x"] == "12.5"
    assert wire.to_storage_value()["data_labels"] is True
    assert strict.position is not None
    assert strict.position.x == 12.5
    assert strict.data_labels is not None
    assert strict.data_labels.value == "top"


@pytest.mark.parametrize("difference_index", range(3))
def test_upstream_valid_but_locally_stricter_values_remain_storable(
    upstream_fixture,
    difference_index,
):
    source = upstream_fixture["upstream_valid_local_strict_differences"][
        difference_index
    ]

    wire = decode_wire_element(source)

    assert wire.to_storage_value() == source
    with pytest.raises(ValidationError):
        wire.validate_strict()


def test_unknown_fields_are_rejected_only_at_the_explicit_strict_boundary(
    upstream_fixture,
):
    wire = decode_wire_element(upstream_fixture["elements"][6])

    assert (
        wire.to_storage_value()["upstream_element_extension"]
        == "chart-vNext"
    )
    with pytest.raises(ValidationError, match="upstream_element_extension"):
        wire.validate_strict()


def test_layout_envelope_validates_known_schema_without_projecting_extensions():
    source = {
        "layouts": [
            {
                "id": "title-slide",
                "description": "Native editable title slide",
                "components": [
                    {
                        "id": "hero",
                        "description": "Editable hero title component",
                        "position": {
                            "x": 0,
                            "y": 0,
                            "upstream_position_extension": "position-vNext",
                        },
                        "elements": [
                            {
                                "type": "text",
                                "runs": [
                                    {
                                        "text": "Title",
                                        "upstream_run_extension": {
                                            "locale": "ko-KR"
                                        },
                                    }
                                ],
                                "decorative": False,
                                "name": "title",
                                "min_length": 1,
                                "max_length": 80,
                                "upstream_element_extension": "text-vNext",
                            }
                        ],
                        "upstream_component_extension": "component-vNext",
                    }
                ],
                "upstream_layout_extension": "layout-vNext",
            }
        ],
        "upstream_envelope_extension": {"version": 3},
    }

    wire = decode_wire_layouts(source)
    wire.validate_strict()
    stored = dump_storage_layouts(wire)
    loaded = load_storage_layouts(stored)

    assert stored == source
    assert loaded.to_wire_value() == source


def test_layout_envelope_strict_validation_rejects_unknown_discriminator():
    source = {
        "layouts": [
            {
                "id": "future-slide",
                "description": "Layout with unsupported future element",
                "components": [
                    {
                        "id": "future",
                        "description": "Unsupported future element component",
                        "position": {"x": 0, "y": 0},
                        "elements": [{"type": "future-element"}],
                    }
                ],
            }
        ]
    }

    wire = decode_wire_layouts(source)

    with pytest.raises(ValidationError, match="union_tag_invalid"):
        wire.validate_strict()


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ({"name": "missing"}, "discriminator_required"),
        ({"type": "future-element"}, "unknown_discriminator"),
        ({"type": "text", "value": float("nan")}, "non_finite_number"),
        ({"type": "text", 7: "non-string"}, "non_string_key"),
        ({"type": "text", "value": ("tuple",)}, "non_json_value"),
    ],
)
def test_codec_rejects_non_json_or_unknown_element_boundaries(value, message):
    with pytest.raises(TemplateV2WireCodecError, match=message):
        decode_wire_element(value)
