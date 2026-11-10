"""Focused acceptance for the canonical authored-vNext design compiler."""

from __future__ import annotations

import re


from models.authored_vnext.deck.scene import (
    DeckSceneIR,
    SceneEditability,
    SceneOwnerDecision,
    SceneShapeElement,
    SceneTextElement,
    SceneTextStyle,
    SlideHybridExportPlan,
    SlideSceneIR,
    scene_digest,
    seal_slide_scene,
)
from models.authored_vnext.master.contract import DeckMasterContract, Rect
from services.authored_vnext.generation.scene_compiler import (
    default_scene_capabilities,
)
from services.authored_vnext.master.digests import seal_master_contract
from tests.support.authored_vnext_master_fixture import master_contract_payload


SOURCE_DIGEST = "sha256:" + "7" * 64
_VISIBLE_DELIMITER = re.compile(r"(?:\s+·\s+|\s*[|→➜▶▸]+\s*)")


def test_slide_scene_digest_keeps_legacy_optional_fields_absent_and_seals_final_metrics() -> None:
    element = SceneTextElement(
        kind="text",
        element_id="element:legacy-title",
        z_order=1,
        bounds=Rect(x=10, y=12, width=300, height=60),
        semantic_role="title",
        editability=SceneEditability(text=True, geometry=True, style=True),
        text="기존 저장 Scene",
        style=SceneTextStyle(
            font_family="Noto Sans KR",
            font_size=24,
            font_weight=700,
            color="#123456",
            text_align="left",
        ),
    )
    legacy_payload = {
        "schemaVersion": "slide-scene-ir/v1",
        "slideId": "slide:legacy-optional-fields",
        "revision": 1,
        "elements": [element.model_dump(mode="json", by_alias=True)],
    }
    legacy = SlideSceneIR.model_validate(
        {**legacy_payload, "slideDigest": scene_digest(legacy_payload)}
    )
    legacy_wire = legacy.model_dump(mode="json", by_alias=True)
    assert "hybridExport" not in legacy_wire
    assert "lineHeight" not in legacy_wire["elements"][0]["style"]

    measured_element = element.model_copy(
        update={
            "style": element.style.model_copy(
                update={"line_height": 1.2, "margin_left_pt": 4.5}
            )
        }
    )
    sealed = seal_slide_scene(
        slide_id="slide:final-metrics",
        revision=1,
        elements=[measured_element],
        hybrid_export=SlideHybridExportPlan(
            owner_decisions=[
                SceneOwnerDecision(
                    owner_id=measured_element.element_id,
                    decision="nativeEditable",
                    capability_id="editable.text.ko-en.v1",
                )
            ]
        ),
    )
    sealed_payload = sealed.model_dump(
        mode="json", by_alias=True, exclude={"slide_digest"}
    )
    assert sealed_payload["elements"][0]["style"]["lineHeight"] == 1.2
    assert sealed_payload["elements"][0]["style"]["marginLeftPt"] == 4.5
    assert "hybridExport" in sealed_payload
    assert sealed.slide_digest == scene_digest(sealed_payload)


def test_explicit_null_hybrid_fields_keep_intermediate_persisted_digests_valid() -> None:
    element = SceneShapeElement(
        kind="shape",
        element_id="element:intermediate-shape",
        z_order=1,
        bounds=Rect(x=10, y=10, width=120, height=80),
        semantic_role="body",
        editability=SceneEditability(geometry=True, style=True),
        shape="rect",
        fill="#2463EB",
    )
    slide_payload = {
        "schemaVersion": "slide-scene-ir/v1",
        "slideId": "slide:intermediate-null",
        "revision": 1,
        "elements": [element.model_dump(mode="json", by_alias=True)],
        "hybridExport": None,
    }
    persisted_slide = {
        **slide_payload,
        "slideDigest": scene_digest(slide_payload),
    }
    slide = SlideSceneIR.model_validate(persisted_slide)
    assert "hybrid_export" in slide.model_fields_set
    assert "hybridExport" not in slide.model_dump(mode="json", by_alias=True)

    capabilities = list(default_scene_capabilities())
    deck_payload = {
        "schemaVersion": "deck-scene-ir/v1",
        "deckId": "deck:intermediate-null",
        "revision": 1,
        "compositionDigest": f"sha256:{'1' * 64}",
        "sourceDigest": f"sha256:{'2' * 64}",
        "hybridProfileId": None,
        "slides": [persisted_slide],
        "assetManifest": [],
        "dataManifest": [],
        "capabilities": [
            item.model_dump(mode="json", by_alias=True) for item in capabilities
        ],
    }
    scene = DeckSceneIR.model_validate(
        {**deck_payload, "sceneDigest": scene_digest(deck_payload)}
    )
    assert "hybrid_profile_id" in scene.model_fields_set
    current_wire = scene.model_dump(mode="json", by_alias=True)
    assert "hybridProfileId" not in current_wire
    assert "hybridExport" not in current_wire["slides"][0]


def _semantic_copy(value: str) -> str:
    return " ".join(_VISIBLE_DELIMITER.sub(" ", value).split())


def _contract(*, body_grammar: bool = False) -> DeckMasterContract:
    payload = master_contract_payload()
    if body_grammar:
        payload["semanticElements"] = [
            {
                "id": "element:authored-body-grammar-v2",
                "type": "text",
                "scope": {"kind": "common"},
                "bounds": {"x": 0, "y": 0, "width": 1, "height": 1},
                "rotation": 0,
                "zIndex": -100000,
                "visibility": "hidden",
                "editableByUser": False,
                "editableByAI": False,
                "locked": True,
                "style": {},
                "content": {
                    "role": "authored-body-grammar:osb-smart-banking:v2"
                },
            }
        ]
    return seal_master_contract(DeckMasterContract.model_validate(payload))


def _company_variant_contract() -> DeckMasterContract:
    payload = master_contract_payload()
    payload["masterId"] = "master:company-variant"
    payload["canvas"]["safeArea"] = {
        "x": 120,
        "y": 40,
        "width": 1030,
        "height": 620,
    }
    payload["tokens"]["fonts"] = {
        "latin": "Arial",
        "eastAsian": "Arial",
        "complexScript": "Arial",
        "fallbacks": ["Arial"],
    }
    payload["tokens"]["colors"] = {
        "background": "#101827",
        "surface": "#182337",
        "textPrimary": "#F4F7FB",
        "textSecondary": "#D5DDEA",
        "accent": "#F4C95D",
        "divider": "#39465B",
        "warning": "#F28C8C",
    }
    payload["fontManifest"].update(
        {
            "primary": "Arial",
            "fallbacks": ["Arial"],
            "windowsFallback": "Arial",
            "themeScripts": {
                "latin": "Arial",
                "hang": "Arial",
                "jpan": "Arial",
                "hans": "Arial",
                "hant": "Arial",
            },
            "files": [],
        }
    )
    payload["furniture"]["header"]["text"] = "COMPANY B"
    payload["furniture"]["header"]["fill"] = "#F4C95D"
    payload["furniture"]["footer"]["text"] = "CONFIDENTIAL"
    for layout in payload["layouts"].values():
        for slot in layout["slots"]:
            slot["bounds"]["x"] += 18
            slot["bounds"]["width"] -= 36
    return seal_master_contract(DeckMasterContract.model_validate(payload))


