from __future__ import annotations

import pytest

from services.authored_vnext.capability_registry import (
    AUTHORED_FONT_FLOOR_PT,
    AUTHORED_FONT_FLOOR_PX,
    CAPABILITY_REGISTRY_DIGEST,
    EDITABLE_HYBRID_PROFILE_ID,
    HYBRID_NATIVE_CAPABILITY_IDS,
    admission_features_for_kind,
    capability_metadata,
    hybrid_ooxml_capabilities,
    is_hybrid_native_capability,
    prompt_capability_clause,
    require_capability_metadata,
    scene_capabilities,
)
from services.authored_vnext.generation.scene_compiler import default_scene_capabilities
from services.authored_vnext.semantic.admission import SerializerRegistryV1
from utils.authored_font_policy import (
    MIN_AUTHORED_FONT_SIZE_PT,
    MIN_AUTHORED_FONT_SIZE_PX,
)
from utils.llm_calls.author_slide_prompt import build_design_system


def test_scene_and_ooxml_use_the_same_capability_registry_binding() -> None:
    scene = default_scene_capabilities()
    assert scene == scene_capabilities()
    assert {item.kind for item in scene} == {
        "text", "image", "shape", "connector", "table", "chart", "group"
    }
    assert (
        next(item for item in scene if item.kind == "connector").native_pptx_export
        == "unsupported"
    )

    ooxml = hybrid_ooxml_capabilities()
    assert ooxml["profileId"] == EDITABLE_HYBRID_PROFILE_ID
    assert ooxml["registryDigest"] == CAPABILITY_REGISTRY_DIGEST
    assert set(ooxml["nativeCapabilityVersions"]) == HYBRID_NATIVE_CAPABILITY_IDS
    assert capability_metadata(consumer="readback")["registryDigest"] == CAPABILITY_REGISTRY_DIGEST
    assert is_hybrid_native_capability("editable.text.ko-en.v1")
    assert not is_hybrid_native_capability("editable.text.ja-zh.v1")


def test_prompt_validator_and_font_adapters_share_registry_policy() -> None:
    registry = SerializerRegistryV1()
    assert registry.text_features == admission_features_for_kind("text")
    assert registry.image_features == admission_features_for_kind("image")
    assert MIN_AUTHORED_FONT_SIZE_PX == AUTHORED_FONT_FLOOR_PX
    assert MIN_AUTHORED_FONT_SIZE_PT == AUTHORED_FONT_FLOOR_PT
    assert CAPABILITY_REGISTRY_DIGEST in prompt_capability_clause()
    assert capability_metadata(consumer="chromium")["registryDigest"] == CAPABILITY_REGISTRY_DIGEST

    class Brand:
        primary = None
        fonts = None
        language = None

    prompt = build_design_system(
        Brand(),
        None,
        default_style_brief="{primary} {font_stack}",
        design_system_rules="{style_brief}",
        illustration_slot_rules="",
        normalize_reference_mode=lambda value: value or "style",
        exact_reconstruction="exact",
        style_reference="style",
        page_number_system="",
        type_scale="",
    )
    assert CAPABILITY_REGISTRY_DIGEST in prompt


def test_registry_metadata_and_validator_feature_drift_fail_closed() -> None:
    chromium = capability_metadata(consumer="chromium")
    require_capability_metadata(chromium, consumer="chromium")

    stale = dict(chromium)
    stale["registryDigest"] = "sha256:" + "0" * 64
    with pytest.raises(ValueError, match="digest_mismatch"):
        require_capability_metadata(stale, consumer="chromium")
    with pytest.raises(ValueError, match="consumer_mismatch"):
        require_capability_metadata(chromium, consumer="readback")
    with pytest.raises(ValueError, match="validator_mismatch"):
        SerializerRegistryV1(text_features=frozenset({"unexpected"}))
