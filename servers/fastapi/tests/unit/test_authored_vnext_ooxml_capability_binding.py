from __future__ import annotations


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
from models.authored_vnext.capability_registry import (
    EDITABLE_HYBRID_PROFILE_ID,
    scene_capabilities,
)
from services.authored_vnext.integration.export_service import (
    ExportServiceError,
    _require_explicit_hybrid_fallback_policy,
)


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
