from __future__ import annotations

import pytest

from models.authored_vnext.capability_registry import (
    CAPABILITY_REGISTRY_DIGEST,
    EDITABLE_HYBRID_PROFILE_ID,
    HYBRID_NATIVE_CAPABILITY_IDS,
    capability_metadata,
    hybrid_ooxml_capabilities,
    is_hybrid_native_capability,
    require_capability_metadata,
    scene_capabilities,
)
from services.authored_vnext.generation.scene_compiler import default_scene_capabilities


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


def test_registry_metadata_and_validator_feature_drift_fail_closed() -> None:
    chromium = capability_metadata(consumer="chromium")
    require_capability_metadata(chromium, consumer="chromium")

    stale = dict(chromium)
    stale["registryDigest"] = "sha256:" + "0" * 64
    with pytest.raises(ValueError, match="digest_mismatch"):
        require_capability_metadata(stale, consumer="chromium")
    with pytest.raises(ValueError, match="consumer_mismatch"):
        require_capability_metadata(chromium, consumer="readback")
