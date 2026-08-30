from __future__ import annotations

import json

import pytest

from models.authored_vnext.deck import (
    SceneEditability,
    SceneOwnerDecision,
    SceneTextElement,
    SlideHybridExportPlan,
    seal_deck_scene,
    seal_slide_scene,
)
from models.authored_vnext.master.contract import Rect
from services.authored_vnext.capability_registry import (
    CAPABILITY_REGISTRY_DIGEST,
    EDITABLE_HYBRID_PROFILE_ID,
    scene_capabilities,
)
from services.authored_vnext.ooxml.native_scene import (
    NativeSceneError,
    _inject_custom_properties,
    _read_custom_properties,
    materialize_scene_pptx,
    readback_scene_pptx,
)
from services.authored_vnext.integration.export_service import (
    ExportServiceError,
    _require_explicit_hybrid_fallback_policy,
)


def test_ooxml_registry_binding_is_mode_independent_and_tamper_closed() -> None:
    receipts: dict[str, tuple[bytes, dict[str, object]]] = {}
    for mode in ("unprofiled", "hybrid"):
        element = SceneTextElement(
            kind="text",
            element_id="title.main",
            z_order=1,
            bounds=Rect(x=40, y=40, width=600, height=80),
            semantic_role="title",
            editability=SceneEditability(text=True, geometry=True, style=True),
            text="Registry-bound OOXML",
        )
        hybrid_plan = (
            SlideHybridExportPlan(
                owner_decisions=[
                    SceneOwnerDecision(
                        owner_id=element.element_id,
                        decision="nativeEditable",
                        capability_id="editable.text.ko-en.v1",
                    )
                ]
            )
            if mode == "hybrid"
            else None
        )
        slide = seal_slide_scene(
            slide_id=f"slide:{mode}",
            revision=1,
            elements=[element],
            hybrid_export=hybrid_plan,
        )
        scene = seal_deck_scene(
            deck_id=f"deck:{mode}",
            revision=1,
            composition_digest="sha256:" + "1" * 64,
            source_digest="sha256:" + "2" * 64,
            hybrid_profile_id=(
                EDITABLE_HYBRID_PROFILE_ID if mode == "hybrid" else None
            ),
            slides=[slide],
            capabilities=scene_capabilities(),
        )
        content, receipt = materialize_scene_pptx(
            scene.model_dump(mode="json", by_alias=True),
            [],
            {
                "slideSizePx": {"width": 960, "height": 540},
                "tokens": {"color.background": "#F7F9FC"},
                "furniture": [],
            },
        )
        capabilities = receipt["capabilities"]
        assert isinstance(capabilities, dict)
        assert capabilities["registryDigest"] == CAPABILITY_REGISTRY_DIGEST
        assert capabilities["registryConsumer"] == "ooxml"
        assert capabilities["readbackRegistry"] == {
            "registrySchemaVersion": "authored-capability-registry/v1",
            "registryDigest": CAPABILITY_REGISTRY_DIGEST,
            "registryConsumer": "readback",
        }
        assert receipt["hybridOwnership"]["profileId"] == (
            EDITABLE_HYBRID_PROFILE_ID if mode == "hybrid" else None
        )
        receipts[mode] = content, receipt

    hybrid_content, _ = receipts["hybrid"]
    properties = _read_custom_properties(hybrid_content)
    capabilities = json.loads(properties["AuthoredNativeCapabilities"])
    capabilities["registryDigest"] = "sha256:" + "0" * 64
    properties["AuthoredNativeCapabilities"] = json.dumps(
        capabilities, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    )
    tampered = _inject_custom_properties(hybrid_content, properties)
    with pytest.raises(
        NativeSceneError, match="authored_vnext_capability_registry_mismatch"
    ):
        readback_scene_pptx(tampered)


def test_hybrid_export_requires_an_explicit_non_silent_fallback_policy() -> None:
    element = SceneTextElement(
        kind="text",
        element_id="title.main",
        z_order=1,
        bounds=Rect(x=40, y=40, width=600, height=80),
        semantic_role="title",
        editability=SceneEditability(text=True, geometry=True, style=True),
        text="Explicit fallback",
    )
    scene = seal_deck_scene(
        deck_id="deck:hybrid-policy",
        revision=1,
        composition_digest="sha256:" + "1" * 64,
        source_digest="sha256:" + "2" * 64,
        hybrid_profile_id=EDITABLE_HYBRID_PROFILE_ID,
        slides=[
            seal_slide_scene(
                slide_id="slide:hybrid-policy",
                revision=1,
                elements=[element],
                hybrid_export=SlideHybridExportPlan(
                    owner_decisions=[
                        SceneOwnerDecision(
                            owner_id=element.element_id,
                            decision="nativeEditable",
                            capability_id="editable.text.ko-en.v1",
                        )
                    ]
                ),
            )
        ],
        capabilities=scene_capabilities(),
    ).model_dump(mode="json", by_alias=True)

    with pytest.raises(ExportServiceError, match="fallback_policy_required"):
        _require_explicit_hybrid_fallback_policy(scene, {})
    with pytest.raises(ExportServiceError, match="fallback_policy_unsupported"):
        _require_explicit_hybrid_fallback_policy(
            scene, {"fallbackPolicy": "whole-deck-fidelity"}
        )
    _require_explicit_hybrid_fallback_policy(scene, {"fallbackPolicy": "reject"})
