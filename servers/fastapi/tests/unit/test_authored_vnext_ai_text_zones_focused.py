"""Focused round-trip and materialization tests for AI master text zones."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from models.authored_vnext.master.contract import DeckMasterContract
from services.authored_vnext.generation.ai_text_zones import (
    ResolvedAITextZoneSet,
    _digest,
    discover_ai_text_zones,
    materialize_ai_text_zone_contract,
)
from tests.support.authored_vnext_master_fixture import master_contract_payload


MASTER_DIGEST = "sha256:" + ("a" * 64)
REPOSITORY_ROOT = Path(__file__).resolve().parents[4]


def test_checked_in_ai_edit_zone_schema_matches_authoritative_model() -> None:
    checked_in = json.loads(
        (
            REPOSITORY_ROOT
            / "contracts"
            / "authored_vnext"
            / "master"
            / "v1"
            / "deckmaster-contract.schema.json"
        ).read_text("utf-8")
    )
    authoritative = DeckMasterContract.model_json_schema(
        by_alias=True,
        mode="validation",
    )
    assert checked_in["$defs"]["AIEditZone"] == authoritative["$defs"]["AIEditZone"]


def _ai_element(
    element_id: str,
    *,
    x: float,
    empty_behavior: str,
    fallback_text: str | None = None,
) -> dict[str, object]:
    example_text = f"Example for {element_id}"
    return {
        "id": element_id,
        "type": "text",
        "scope": {"kind": "common"},
        "bounds": {"x": x, "y": 72, "width": 280, "height": 92},
        "rotation": 0,
        "zIndex": 10,
        "visibility": "always",
        "editableByUser": True,
        "editableByAI": True,
        "locked": False,
        "style": {
            "fontFamily": "Malgun Gothic",
            "fontSize": 28,
            "fontWeight": 600,
            "color": "#0F172A",
            "textAlign": "left",
            "verticalAlign": "middle",
        },
        "content": {
            "role": "ai-text-zone",
            # The editor intentionally mirrors exampleText into text on insertion.
            "text": example_text,
            "aiBinding": {
                "instruction": f"Resolve {element_id} from document evidence.",
                "exampleText": example_text,
                "emptyBehavior": empty_behavior,
                "fallbackText": fallback_text,
                "maxChars": 80,
            },
        },
    }


def _contract() -> DeckMasterContract:
    payload = master_contract_payload()
    payload["semanticElements"] = [
        _ai_element("element:ai-hidden", x=72, empty_behavior="hidden"),
        _ai_element("element:ai-blank", x=400, empty_behavior="blank"),
        _ai_element(
            "element:ai-fallback",
            x=728,
            empty_behavior="fallback",
            fallback_text="Approved fallback",
        ),
    ]
    return DeckMasterContract.model_validate(payload)


def test_ai_binding_round_trips_with_editor_example_text_mirrored_to_content() -> None:
    authored = _contract()

    reentered = DeckMasterContract.model_validate(
        authored.model_dump(mode="json", by_alias=True)
    )

    for element in reentered.semantic_elements:
        assert element.content.ai_binding is not None
        assert element.content.text == element.content.ai_binding.example_text


def test_materialized_contract_applies_resolved_empty_rules_without_moving_geometry() -> None:
    contract = _contract()
    zones = discover_ai_text_zones(contract)
    core = {
        "schema_version": "authored-vnext-ai-text-zones/v1",
        "deck_id": "deck:missing-evidence",
        "master_digest": MASTER_DIGEST,
        "context_digest": _digest({}),
        "bindings_digest": _digest([zone.model_dump(mode="json") for zone in zones]),
        "override_digest": _digest({}),
        "values": [
            {"element_id": "element:ai-blank", "value": "", "source": "empty", "hidden": False},
            {
                "element_id": "element:ai-fallback",
                "value": "Approved fallback",
                "source": "fallback",
                "hidden": False,
            },
            {"element_id": "element:ai-hidden", "value": "", "source": "empty", "hidden": True},
        ],
    }
    empty = ResolvedAITextZoneSet.model_validate({**core, "digest": _digest(core)})

    before = {
        element.id: element.model_dump(mode="json")
        for element in contract.semantic_elements
    }
    materialized = materialize_ai_text_zone_contract(contract, empty)
    materialized_by_id = {
        element.id: element for element in materialized.semantic_elements
    }
    assert materialized_by_id["element:ai-hidden"].visibility == "hidden"
    assert materialized_by_id["element:ai-blank"].visibility == "always"
    assert materialized_by_id["element:ai-fallback"].content.text == (
        "Approved fallback"
    )
    for element in materialized.semantic_elements:
        original = before[element.id]
        after = element.model_dump(mode="json")
        original_content = original.pop("content")
        after_content = after.pop("content")
        original_content.pop("text")
        after_content.pop("text")
        original.pop("visibility")
        after.pop("visibility")
        assert after == original
        assert after_content == original_content

    tampered = empty.model_copy(update={"deck_id": "deck:other"})
    with pytest.raises(ValueError, match="digest mismatch"):
        materialize_ai_text_zone_contract(contract, tampered)
