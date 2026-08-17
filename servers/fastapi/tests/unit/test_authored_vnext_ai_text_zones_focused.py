"""Focused round-trip and deck-scoped resolution tests for AI master text zones."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from pathlib import Path

import pytest

from models.authored_vnext.master.contract import DeckMasterContract
from services.authored_vnext.generation.ai_text_zones import (
    AITextZoneContext,
    AITextZoneResolutionRequest,
    build_ai_edit_zone_slide_contexts,
    materialize_ai_text_zone_contract,
    resolve_deck_ai_text_zones,
    validate_ai_edit_zone_slide_contexts,
)
from services.authored_vnext.generation.effective_style import (
    build_ai_edit_zone_template_design_context,
)
from tests.support.authored_vnext_master_fixture import master_contract_payload
from utils.llm_calls.author_deck import _constrain_structured_zone_html


MASTER_DIGEST = "sha256:" + ("a" * 64)
REPOSITORY_ROOT = Path(__file__).resolve().parents[4]


class _CandidateRepository:
    def __init__(self) -> None:
        self.candidates: dict[str, dict[str, object]] = {}

    def get_candidate(self, digest: str) -> dict[str, object] | None:
        return deepcopy(self.candidates.get(digest))

    def save_candidate(self, digest: str, payload: dict[str, object]) -> None:
        self.candidates[digest] = deepcopy(payload)


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


def test_deck_resolution_is_context_fenced_cached_and_materializes_empty_rules() -> None:
    async def scenario() -> None:
        repository = _CandidateRepository()
        contract = _contract()
        resolver_calls: list[AITextZoneResolutionRequest] = []

        async def evidence_resolver(
            request: AITextZoneResolutionRequest,
        ) -> dict[str, str]:
            resolver_calls.append(request)
            evidence = request.context.materials[0] if request.context.materials else ""
            return {zone.element_id: evidence for zone in request.zones}

        context_a = AITextZoneContext(materials=("Document A evidence",))
        context_b = AITextZoneContext(materials=("Document B evidence",))
        resolved_a = await resolve_deck_ai_text_zones(
            repository=repository,
            deck_id="deck:shared-slides",
            master_digest=MASTER_DIGEST,
            contract=contract,
            context=context_a,
            resolver=evidence_resolver,
        )
        # A second slide in the same deck consumes the already pinned sidecar.
        resolved_a_second_slide = await resolve_deck_ai_text_zones(
            repository=repository,
            deck_id="deck:shared-slides",
            master_digest=MASTER_DIGEST,
            contract=contract,
            context=context_a,
            resolver=evidence_resolver,
        )
        resolved_b = await resolve_deck_ai_text_zones(
            repository=repository,
            deck_id="deck:shared-slides",
            master_digest=MASTER_DIGEST,
            contract=contract,
            context=context_b,
            resolver=evidence_resolver,
        )
        resolved_other_deck = await resolve_deck_ai_text_zones(
            repository=repository,
            deck_id="deck:isolated",
            master_digest=MASTER_DIGEST,
            contract=contract,
            context=context_a,
            resolver=evidence_resolver,
        )

        assert len(resolver_calls) == 3
        assert resolved_a_second_slide == resolved_a
        assert {value.value for value in resolved_a.values} == {"Document A evidence"}
        assert {value.value for value in resolved_b.values} == {"Document B evidence"}
        assert resolved_other_deck.deck_id == "deck:isolated"
        assert resolved_a.context_digest != resolved_b.context_digest
        assert resolved_a.digest != resolved_b.digest
        assert resolved_a.digest != resolved_other_deck.digest

        async def missing_evidence(
            request: AITextZoneResolutionRequest,
        ) -> dict[str, str]:
            return {zone.element_id: "" for zone in request.zones}

        empty = await resolve_deck_ai_text_zones(
            repository=repository,
            deck_id="deck:missing-evidence",
            master_digest=MASTER_DIGEST,
            contract=contract,
            context=AITextZoneContext(),
            resolver=missing_evidence,
        )
        values = {value.element_id: value for value in empty.values}
        assert values["element:ai-hidden"].value == ""
        assert values["element:ai-hidden"].hidden is True
        assert values["element:ai-blank"].value == ""
        assert values["element:ai-blank"].hidden is False
        assert values["element:ai-fallback"].value == "Approved fallback"
        assert values["element:ai-fallback"].source == "fallback"

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

    asyncio.run(scenario())


def test_deterministic_resolution_can_bypass_repository_cache() -> None:
    async def scenario() -> None:
        repository = _CandidateRepository()

        async def deterministic_resolver(
            request: AITextZoneResolutionRequest,
        ) -> dict[str, str]:
            return {zone.element_id: "" for zone in request.zones}

        resolved = await resolve_deck_ai_text_zones(
            repository=repository,
            deck_id="deck:deterministic-replay",
            master_digest=MASTER_DIGEST,
            contract=_contract(),
            context=AITextZoneContext(materials=("Saved manuscript",)),
            resolver=deterministic_resolver,
            use_repository_cache=False,
        )

        assert repository.candidates == {}
        assert resolved.deck_id == "deck:deterministic-replay"
        assert next(
            value for value in resolved.values
            if value.element_id == "element:ai-fallback"
        ).value == "Approved fallback"

    asyncio.run(scenario())


def test_ai_edit_zone_builds_slide_scoped_semantic_plan_from_template_tokens() -> None:
    payload = master_contract_payload()
    payload["aiEditZones"] = [
        {
            "id": "ai-zone:requirements",
            "scope": {"kind": "common"},
            "bounds": {"x": 96, "y": 168, "width": 1088, "height": 432},
            "instruction": "핵심 요구사항을 3개의 번호 카드로 구성",
            "contentType": "auto",
            "pattern": "auto",
            "sourcePolicy": "documentOnly",
            "emptyBehavior": "fallback",
            "fallbackText": "문서 근거가 없습니다.",
            "maxChars": 240,
            "maxItems": 3,
            "density": "balanced",
            "textAlign": "right",
            "stylePolicy": "inheritTemplate",
        }
    ]
    contract = DeckMasterContract.model_validate(payload)
    design_context = build_ai_edit_zone_template_design_context(contract)

    contexts = build_ai_edit_zone_slide_contexts(
        contract=contract,
        slide_objectives=("요구사항 요약", "이행 방안"),
        layout_ids=(contract.layouts.standard.id, contract.layouts.standard.id),
        context=AITextZoneContext(
            prompt="제안서를 작성해 주세요.",
            instructions="문서에 없는 내용은 만들지 마세요.",
            materials=("첫 번째 슬라이드 근거", "두 번째 슬라이드 근거"),
        ),
        template_design_context=design_context,
    )

    validated = validate_ai_edit_zone_slide_contexts(contexts)
    first = contexts[0]
    first_zone = first["zones"][0]
    assert first["schemaVersion"] == "authored-vnext-ai-edit-zone-context/v1"
    assert first["slideObjective"] == "요구사항 요약"
    assert first["sourceContext"]["materials"] == ["첫 번째 슬라이드 근거"]
    assert first_zone["zoneId"] == "ai-zone:requirements"
    assert first_zone["pattern"] == "numberedCards"
    assert first_zone["textAlign"] == "right"
    assert first_zone["generationState"] == "generate"
    assert first_zone["sourceRefs"] == ["sourceContext.materials.0"]
    assert first_zone["blocks"][0]["kind"] == "cards"
    assert first_zone["blocks"][0]["maxItems"] == 3
    assert all(
        not style_ref.startswith("#")
        for style_ref in first_zone["blocks"][0]["styleRefs"]
    )
    assert first["templateDesignContext"]["tokens"]["colors"] == (
        contract.tokens.colors.model_dump(mode="json", by_alias=True)
    )
    assert len(validated) == 2


def test_document_only_ai_edit_zone_uses_declared_empty_behavior_without_evidence() -> None:
    payload = master_contract_payload()
    payload["aiEditZones"] = [
        {
            "id": "ai-zone:document-only",
            "scope": {"kind": "common"},
            "bounds": {"x": 96, "y": 168, "width": 1088, "height": 432},
            "instruction": "문서의 수치를 차트로 구성",
            "contentType": "chart",
            "sourcePolicy": "documentOnly",
            "emptyBehavior": "fallback",
            "fallbackText": "확인 가능한 문서 수치가 없습니다.",
            "stylePolicy": "inheritTemplate",
        }
    ]
    contract = DeckMasterContract.model_validate(payload)

    contexts = build_ai_edit_zone_slide_contexts(
        contract=contract,
        slide_objectives=("수치 요약",),
        layout_ids=(contract.layouts.standard.id,),
        context=AITextZoneContext(prompt="시장 현황을 정리해 주세요."),
        template_design_context=build_ai_edit_zone_template_design_context(contract),
    )

    zone = contexts[0]["zones"][0]
    assert zone["sourceRefs"] == []
    assert zone["generationState"] == "fallback"
    assert zone["fallbackText"] == "확인 가능한 문서 수치가 없습니다."
    assert zone["overflowPolicy"] == "reject"
    assert zone["stylePolicy"] == "inheritTemplate"


def test_structured_zone_html_is_token_fenced_and_pinned_to_bounds() -> None:
    payload = master_contract_payload()
    payload["aiEditZones"] = [
        {
            "id": "ai-zone:requirements",
            "scope": {"kind": "common"},
            "bounds": {"x": 96, "y": 168, "width": 1088, "height": 432},
            "instruction": "핵심 요구사항을 3개의 번호 카드로 구성",
            "pattern": "numberedCards",
            "sourcePolicy": "promptOnly",
            "emptyBehavior": "blank",
            "maxChars": 240,
            "maxItems": 3,
            "density": "balanced",
            "textAlign": "right",
            "stylePolicy": "inheritTemplate",
        }
    ]
    contract = DeckMasterContract.model_validate(payload)
    context = build_ai_edit_zone_slide_contexts(
        contract=contract,
        slide_objectives=("요구사항 요약",),
        layout_ids=(contract.layouts.standard.id,),
        context=AITextZoneContext(prompt="제안서 핵심 요구사항"),
        template_design_context=build_ai_edit_zone_template_design_context(contract),
    )[0]
    colors = context["templateDesignContext"]["tokens"]["colors"]
    font = context["templateDesignContext"]["allowedFonts"][0]
    block_id = context["zones"][0]["blocks"][0]["blockId"]
    html = (
        "<!doctype html><html><head><style>"
        f"body{{background:{colors['background']};font-family:'{font}'}}"
        f".card{{background:{colors['surface']};color:{colors['textPrimary']}}}"
        "</style></head><body>"
        '<section data-ai-zone-id="ai-zone:requirements">'
        f'<div data-ai-block-id="{block_id}">'
        '<article class="card" data-ai-item>요구사항 1</article>'
        '<article class="card" data-ai-item>요구사항 2</article>'
        '<article class="card" data-ai-item>요구사항 3</article>'
        "</div></section></body></html>"
    )

    constrained = _constrain_structured_zone_html(html, context)

    assert 'data-ai-zone-constraints="v1"' in constrained
    assert "left:96px!important" in constrained
    assert "width:1088px!important" in constrained
    assert "overflow:hidden!important" in constrained
    assert "text-align:right!important" in constrained

    with pytest.raises(ValueError, match="invented colors"):
        _constrain_structured_zone_html(
            html.replace(colors["surface"], "#010203"),
            context,
        )
    with pytest.raises(ValueError, match="exceeds maxItems"):
        _constrain_structured_zone_html(
            html.replace(
                "</div></section>",
                '<article class="card" data-ai-item>요구사항 4</article></div></section>',
            ),
            context,
        )
