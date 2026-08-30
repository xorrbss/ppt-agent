"""Canonical capability contract shared by authored generation boundaries.

The registry is deliberately data-only: every consumer receives the same
versioned digest, while its own adapter remains responsible for deciding
whether an element is native, rasterized, or rejected.  This keeps a missing
implementation from becoming an implicit Fidelity downgrade.
"""

from __future__ import annotations

from hashlib import sha256
import json
from types import MappingProxyType
from typing import Any, Literal, Mapping


CAPABILITY_REGISTRY_SCHEMA_VERSION = "authored-capability-registry/v1"
EDITABLE_HYBRID_PROFILE_ID = "authored-editable-hybrid-ko-en-basic-v1"

_SCENE_CAPABILITY_POLICIES: tuple[dict[str, str], ...] = (
    {"kind": "text", "preview": "supported", "workbenchEdit": "supported", "nativePptxExport": "supported"},
    {"kind": "image", "preview": "supported", "workbenchEdit": "supported", "nativePptxExport": "supported"},
    {"kind": "shape", "preview": "supported", "workbenchEdit": "supported", "nativePptxExport": "supported"},
    {"kind": "connector", "preview": "partial", "workbenchEdit": "partial", "nativePptxExport": "unsupported", "reason": "connector routing is preserved but native connector export is not implemented"},
    {"kind": "table", "preview": "supported", "workbenchEdit": "supported", "nativePptxExport": "supported"},
    {"kind": "chart", "preview": "supported", "workbenchEdit": "supported", "nativePptxExport": "supported"},
    {"kind": "group", "preview": "supported", "workbenchEdit": "readOnly", "nativePptxExport": "unsupported", "reason": "native group export is not implemented; child identities remain preserved"},
)

HYBRID_NATIVE_CAPABILITY_BY_KIND: Mapping[str, str] = MappingProxyType(
    {
        "text": "editable.text.ko-en.v1",
        "shape": "editable.shape.solid-basic.v1",
        "image": "editable.image.rect-crop.v1",
        "table": "editable.table.basic.v1",
        "chart": "editable.chart.line-pie.v1",
    }
)
HYBRID_NATIVE_CAPABILITY_IDS: frozenset[str] = frozenset(
    {
        *HYBRID_NATIVE_CAPABILITY_BY_KIND.values(),
        "editable.text.advanced.v1",
        "editable.shape.advanced.v1",
        "editable.table.advanced.v1",
    }
)
HYBRID_RASTER_ONLY_CAPABILITY_IDS: tuple[str, ...] = (
    "editable.image.advanced-crop-mask.v1",
    "editable.text.ja-zh.v1",
)
ADMISSION_FEATURES_BY_KIND: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "text": frozenset({"plain-text", "mixed-runs", "explicit-breaks"}),
        "image": frozenset({"crop", "object-fit", "object-position"}),
        "shape": frozenset({"solid-fill", "solid-stroke", "radius"}),
        "chart": frozenset(
            {"chart", "native-chart", "chart-data", "chart.axes", "chart.legend", "chart.fonts"}
        ),
    }
)
AUTHORED_FONT_FLOOR_PX = 12.0
AUTHORED_FONT_FLOOR_PT = 9.0
NATIVE_CAPABILITY_IDS_V1: tuple[str, ...] = tuple(
    sorted(
        {
            "css.background-plane.v1",
            "css.clip-path.v1",
            "css.filter-blend.v1",
            "css.gradient.v1",
            "css.grid-flex.v1",
            "css.paint-closure.v1",
            "css.shadow.v1",
            "editable.chart.raster.v1",
            "editable.image.advanced-crop-mask.v1",
            "editable.raster-island-integrity.v1",
            "editable.shape.advanced.v1",
            "editable.table.advanced.v1",
            "editable.text.advanced.v1",
            "editable.text.ja-zh.v1",
            *HYBRID_NATIVE_CAPABILITY_IDS,
        }
    )
)
EDITABLE_MVP_CAPABILITY_IDS_V1: frozenset[str] = frozenset(
    {
        "editable.image.rect-crop.v1",
        "editable.shape.solid-basic.v1",
        "editable.table.basic.v1",
        "editable.text.ko-en.v1",
    }
)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")


_REGISTRY_PAYLOAD = {
    "schemaVersion": CAPABILITY_REGISTRY_SCHEMA_VERSION,
    "hybridProfileId": EDITABLE_HYBRID_PROFILE_ID,
    "sceneCapabilities": _SCENE_CAPABILITY_POLICIES,
    "nativeCapabilityIds": list(NATIVE_CAPABILITY_IDS_V1),
    "hybridNativeCapabilityByKind": dict(HYBRID_NATIVE_CAPABILITY_BY_KIND),
    "hybridNativeCapabilityIds": sorted(HYBRID_NATIVE_CAPABILITY_IDS),
    "hybridRasterOnlyCapabilityIds": list(HYBRID_RASTER_ONLY_CAPABILITY_IDS),
    "admissionFeaturesByKind": {
        kind: sorted(features) for kind, features in sorted(ADMISSION_FEATURES_BY_KIND.items())
    },
    "authoredFontFloor": {"px": AUTHORED_FONT_FLOOR_PX, "pt": AUTHORED_FONT_FLOOR_PT},
}
CAPABILITY_REGISTRY_DIGEST = "sha256:" + sha256(_canonical_json(_REGISTRY_PAYLOAD)).hexdigest()


def scene_capabilities() -> list[Any]:
    """Return canonical Scene declarations without importing Scene at module load."""

    from models.authored_vnext.deck.scene import SceneCapability

    return [SceneCapability.model_validate(item) for item in _SCENE_CAPABILITY_POLICIES]


def hybrid_capability_for_kind(kind: str) -> str | None:
    return HYBRID_NATIVE_CAPABILITY_BY_KIND.get(kind)


def is_hybrid_native_capability(capability_id: str) -> bool:
    return capability_id in HYBRID_NATIVE_CAPABILITY_IDS


def admission_features_for_kind(kind: str) -> frozenset[str] | None:
    return ADMISSION_FEATURES_BY_KIND.get(kind)


def prompt_capability_clause() -> str:
    """Tell the author that native editability is an explicit later decision."""

    return (
        "\nCapability contract: author the visual source only; do not claim editable "
        "Hybrid output. Native promotion is explicitly admitted later, and unsupported "
        "features are rasterized or rejected rather than silently downgraded. "
        f"Registry digest: {CAPABILITY_REGISTRY_DIGEST}."
    )


def capability_metadata(
    *,
    consumer: Literal[
        "prompt", "validator", "font", "renderer", "chromium", "scene", "ooxml", "readback", "source-readback"
    ],
) -> dict[str, Any]:
    """Return the sealed registry binding carried by Scene/OOXML/readback."""

    return {
        "registrySchemaVersion": CAPABILITY_REGISTRY_SCHEMA_VERSION,
        "registryDigest": CAPABILITY_REGISTRY_DIGEST,
        "registryConsumer": consumer,
    }


class CapabilityRegistryMismatchError(ValueError):
    """A consumer supplied metadata from another registry revision."""


def require_capability_metadata(
    value: object,
    *,
    consumer: Literal[
        "prompt", "validator", "font", "renderer", "chromium", "scene", "ooxml", "readback", "source-readback"
    ],
) -> None:
    """Reject absent, stale, or mislabelled capability bindings at a boundary.

    Consumers that carry registry metadata must call this before treating a
    result as evidence.  A missing binding is deliberately not equivalent to
    ``not_required``: it is an integrity failure.
    """

    if not isinstance(value, Mapping):
        raise CapabilityRegistryMismatchError("authored_capability_registry_metadata_missing")
    if value.get("registrySchemaVersion") != CAPABILITY_REGISTRY_SCHEMA_VERSION:
        raise CapabilityRegistryMismatchError("authored_capability_registry_schema_mismatch")
    if value.get("registryDigest") != CAPABILITY_REGISTRY_DIGEST:
        raise CapabilityRegistryMismatchError("authored_capability_registry_digest_mismatch")
    if value.get("registryConsumer") != consumer:
        raise CapabilityRegistryMismatchError("authored_capability_registry_consumer_mismatch")


def hybrid_ooxml_capabilities(*, consumer: Literal["ooxml", "readback"] = "ooxml") -> dict[str, Any]:
    """OOXML/readback capability projection; Hybrid is never implied by this call."""

    return {
        "profileId": EDITABLE_HYBRID_PROFILE_ID,
        "nativeEditable": sorted(HYBRID_NATIVE_CAPABILITY_BY_KIND),
        "nativeCapabilityVersions": sorted(HYBRID_NATIVE_CAPABILITY_IDS),
        "rasterOnlyCapabilityVersions": list(HYBRID_RASTER_ONLY_CAPABILITY_IDS),
        **capability_metadata(consumer=consumer),
    }


__all__ = [
    "CAPABILITY_REGISTRY_DIGEST",
    "CAPABILITY_REGISTRY_SCHEMA_VERSION",
    "CapabilityRegistryMismatchError",
    "ADMISSION_FEATURES_BY_KIND",
    "AUTHORED_FONT_FLOOR_PT",
    "AUTHORED_FONT_FLOOR_PX",
    "EDITABLE_HYBRID_PROFILE_ID",
    "EDITABLE_MVP_CAPABILITY_IDS_V1",
    "HYBRID_NATIVE_CAPABILITY_BY_KIND",
    "HYBRID_NATIVE_CAPABILITY_IDS",
    "HYBRID_RASTER_ONLY_CAPABILITY_IDS",
    "NATIVE_CAPABILITY_IDS_V1",
    "admission_features_for_kind",
    "capability_metadata",
    "hybrid_capability_for_kind",
    "hybrid_ooxml_capabilities",
    "is_hybrid_native_capability",
    "prompt_capability_clause",
    "require_capability_metadata",
    "scene_capabilities",
]
