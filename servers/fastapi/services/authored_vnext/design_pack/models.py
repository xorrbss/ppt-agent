"""Versioned contracts for source-native authored design packages."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


Digest = str


def _camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in tail)


def canonical_digest(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


class PackModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_camel,
        populate_by_name=True,
        extra="forbid",
        frozen=True,
    )


class Rect(PackModel):
    x: float = Field(ge=0)
    y: float = Field(ge=0)
    width: float = Field(gt=0)
    height: float = Field(gt=0)


class Canvas(PackModel):
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    unit: Literal["emu", "px"]


class TextStyle(PackModel):
    latin_font: str | None = Field(default=None, max_length=200)
    east_asian_font: str | None = Field(default=None, max_length=200)
    font_size: float | None = Field(default=None, gt=0, le=1000)
    font_weight: int | None = Field(default=None, ge=100, le=1000)
    color: str | None = Field(default=None, pattern=r"^(#[0-9A-Fa-f]{6}|scheme:[A-Za-z0-9_-]+)$")
    alignment: Literal["left", "center", "right", "justify", "unknown"] = "unknown"


class Spacing(PackModel):
    inset_left: float = Field(default=0, ge=0)
    inset_top: float = Field(default=0, ge=0)
    inset_right: float = Field(default=0, ge=0)
    inset_bottom: float = Field(default=0, ge=0)
    paragraph_before: float | None = Field(default=None, ge=0)
    paragraph_after: float | None = Field(default=None, ge=0)
    line_spacing: float | None = Field(default=None, gt=0)


class Paint(PackModel):
    fill: str | None = None
    line: str | None = None
    line_width: float | None = Field(default=None, ge=0)


class SlotConstraint(PackModel):
    constraint_id: str = Field(min_length=1, max_length=240)
    kind: Literal[
        "inside-canvas",
        "minimum-font",
        "maximum-lines",
        "maximum-characters",
        "aspect-ratio",
        "no-overlap",
        "source-geometry",
    ]
    value: float | int | str | bool
    hard: bool = True


EditableKind = Literal["text", "image", "table", "chart"]


class CapacityEnvelope(PackModel):
    """Measured or source-derived hard limits for one executable slot."""

    measurement_basis: Literal["source-geometry", "chromium-observed"]
    content_area: float = Field(gt=0)
    max_characters: int | None = Field(default=None, ge=1, le=100000)
    max_lines: int | None = Field(default=None, ge=1, le=10000)
    max_table_rows: int | None = Field(default=None, ge=1, le=10000)
    max_table_columns: int | None = Field(default=None, ge=1, le=1000)
    max_numeric_items: int | None = Field(default=None, ge=1, le=10000)
    aspect_ratio_min: float | None = Field(default=None, gt=0, le=1000)
    aspect_ratio_max: float | None = Field(default=None, gt=0, le=1000)

    @model_validator(mode="after")
    def _ordered_aspect_ratio(self) -> "CapacityEnvelope":
        if (
            self.aspect_ratio_min is not None
            and self.aspect_ratio_max is not None
            and self.aspect_ratio_min > self.aspect_ratio_max
        ):
            raise ValueError("design_pack_capacity_aspect_ratio_invalid")
        return self


class PatchHandle(PackModel):
    """Exact source-native target; selectors alone are not patch authority."""

    kind: Literal["pptx-shape", "authored-qid", "scene-element"]
    source_part: str = Field(min_length=1, max_length=500)
    element_id: str = Field(min_length=1, max_length=240)
    selector: str = Field(min_length=1, max_length=500)
    source_digest: Digest = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class EditableSlot(PackModel):
    slot_id: str = Field(min_length=1, max_length=240)
    semantic_role: str = Field(min_length=1, max_length=120)
    selector: str = Field(min_length=1, max_length=500)
    bounds: Rect
    accepted_kinds: tuple[EditableKind, ...] = Field(min_length=1, max_length=4)
    source_shape_id: str | None = Field(default=None, max_length=120)
    source_xml_digest: Digest | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$"
    )
    text_style: TextStyle | None = None
    spacing: Spacing | None = None
    paint: Paint | None = None
    capacity_units: int = Field(default=1, ge=1, le=10000)
    density: Literal["low", "medium", "high"] = "medium"
    constraints: tuple[SlotConstraint, ...] = Field(default=(), max_length=32)
    required: bool = False
    capacity_envelope: CapacityEnvelope | None = None
    patch_handle: PatchHandle | None = None
    container_id: str | None = Field(default=None, min_length=1, max_length=500)
    hide_policy: Literal["never", "when-optional-empty"] = "never"
    fallback_policy: Literal["reject-frame", "fidelity-only"] = "reject-frame"
    extraction_confidence: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def _executable_handle_consistent(self) -> "EditableSlot":
        if self.required and self.hide_policy != "never":
            raise ValueError("design_pack_required_slot_hide_policy_invalid")
        if self.patch_handle is not None:
            if self.patch_handle.selector != self.selector:
                raise ValueError("design_pack_patch_selector_mismatch")
            if (
                self.source_xml_digest is not None
                and self.patch_handle.source_digest != self.source_xml_digest
            ):
                raise ValueError("design_pack_patch_digest_mismatch")
        return self


class NativeElement(PackModel):
    element_id: str = Field(min_length=1, max_length=240)
    kind: Literal[
        "text",
        "image",
        "table",
        "chart",
        "shape",
        "group",
        "connector",
        "background",
        "unsupported",
    ]
    selector: str = Field(min_length=1, max_length=500)
    bounds: Rect | None = None
    source_part: str = Field(min_length=1, max_length=500)
    source_xml_digest: Digest = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    text_style: TextStyle | None = None
    spacing: Spacing | None = None
    paint: Paint | None = None
    editable: bool = False
    protected: bool = True
    fallback: Literal["native", "raster", "omit", "reject"] = "native"

    @model_validator(mode="after")
    def _protected_by_default(self) -> "NativeElement":
        if self.editable or not self.protected:
            raise ValueError("design_pack_native_element_must_be_protected")
        return self


class UnsupportedElement(PackModel):
    selector: str = Field(min_length=1, max_length=500)
    reason: str = Field(min_length=1, max_length=500)
    fallback: Literal["raster", "omit", "reject"]


class RepresentativeRenderRef(PackModel):
    role: str = Field(min_length=1, max_length=120)
    asset_path: str = Field(min_length=1, max_length=500)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    media_type: Literal["image/svg+xml", "image/png", "image/jpeg", "image/webp"]
    evidence_level: Literal["structural", "source-render"] = "structural"


class DesignFrame(PackModel):
    frame_id: str = Field(min_length=1, max_length=240)
    source_index: int = Field(ge=0)
    role: Literal[
        "cover",
        "section",
        "content",
        "data",
        "timeline",
        "architecture",
        "process",
        "closing",
    ]
    frame_type: Literal["pptx-native", "html", "scene"]
    source_part: str = Field(min_length=1, max_length=500)
    layout_part: str | None = Field(default=None, max_length=500)
    master_part: str | None = Field(default=None, max_length=500)
    source_digest: Digest = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    background: Paint = Paint()
    density: Literal["low", "medium", "high"]
    slots: tuple[EditableSlot, ...] = Field(default=(), max_length=128)
    chrome: tuple[NativeElement, ...] = Field(default=(), max_length=4096)
    unsupported: tuple[UnsupportedElement, ...] = Field(default=(), max_length=4096)
    constraints: tuple[SlotConstraint, ...] = Field(default=(), max_length=128)
    representative_render_ref: str | None = Field(default=None, max_length=500)
    editability: Literal["source-native", "fidelity-only"] = "source-native"


class DesignTokenSummary(PackModel):
    typography: tuple[str, ...] = Field(default=(), max_length=256)
    colors: tuple[str, ...] = Field(default=(), max_length=256)
    line_styles: tuple[str, ...] = Field(default=(), max_length=256)
    spacing_values: tuple[float, ...] = Field(default=(), max_length=512)


class FallbackPolicy(PackModel):
    unsupported_shape: Literal["raster", "omit", "reject"] = "raster"
    unsupported_active_content: Literal["reject"] = "reject"
    missing_media: Literal["reject"] = "reject"
    render_reference_required_for_release: bool = True


class DesignPackStructure(PackModel):
    schema_version: Literal[
        "source-native-design-structure/v1",
        "source-native-design-structure/v2",
    ] = (
        "source-native-design-structure/v2"
    )
    source_kind: Literal["pptx", "authored"]
    source_digest: Digest = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    canvas: Canvas
    frames: tuple[DesignFrame, ...] = Field(min_length=1, max_length=1000)
    tokens: DesignTokenSummary
    fallback_policy: FallbackPolicy
    representative_renders: tuple[RepresentativeRenderRef, ...] = Field(
        default=(), max_length=1000
    )
    structure_digest: Digest = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _sealed_and_ordered(self) -> "DesignPackStructure":
        indexes = [frame.source_index for frame in self.frames]
        if indexes != list(range(len(indexes))):
            raise ValueError("design_pack_frame_order_invalid")
        frame_ids = [frame.frame_id for frame in self.frames]
        if len(frame_ids) != len(set(frame_ids)):
            raise ValueError("design_pack_frame_id_duplicate")
        render_paths = {item.asset_path for item in self.representative_renders}
        if any(
            frame.representative_render_ref not in render_paths
            for frame in self.frames
            if frame.representative_render_ref is not None
        ):
            raise ValueError("design_pack_render_reference_invalid")
        if self.schema_version == "source-native-design-structure/v2":
            slot_ids: list[str] = []
            patch_targets: list[tuple[str, str]] = []
            for frame in self.frames:
                if frame.editability == "source-native" and not frame.slots:
                    raise ValueError("design_pack_source_native_slots_missing")
                if frame.editability == "fidelity-only" and frame.slots:
                    raise ValueError("design_pack_fidelity_only_slots_present")
                for element in frame.chrome:
                    if element.editable or not element.protected:
                        raise ValueError("design_pack_native_element_must_be_protected")
                for slot in frame.slots:
                    if (
                        slot.patch_handle is None
                        or slot.capacity_envelope is None
                        or slot.container_id is None
                        or slot.extraction_confidence is None
                        or slot.extraction_confidence < 0.95
                    ):
                        raise ValueError("design_pack_slot_not_executable")
                    slot_ids.append(slot.slot_id)
                    patch_targets.append(
                        (slot.patch_handle.source_part, slot.patch_handle.element_id)
                    )
            if len(slot_ids) != len(set(slot_ids)):
                raise ValueError("design_pack_slot_id_duplicate")
            if len(patch_targets) != len(set(patch_targets)):
                raise ValueError("design_pack_patch_target_duplicate")
            payload = self.model_dump(
                mode="json", by_alias=True, exclude={"structure_digest"}
            )
        else:
            # v1 packages predate executable-slot fields.  Preserve their original
            # digest surface while allowing them to be read as legacy/fidelity data.
            payload = self.model_dump(
                mode="json",
                by_alias=True,
                exclude={"structure_digest"},
                exclude_unset=True,
            )
        if self.structure_digest != canonical_digest(payload):
            raise ValueError("design_pack_structure_digest_mismatch")
        return self


class PackageFile(PackModel):
    path: str = Field(min_length=1, max_length=500)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    media_type: str = Field(min_length=1, max_length=200)
    role: Literal["structure", "source", "representative-render"]


class DesignPackManifest(PackModel):
    schema_version: Literal["source-native-design-pack/v1"] = (
        "source-native-design-pack/v1"
    )
    pack_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,119}$")
    package_version: str = Field(pattern=r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
    source_kind: Literal["pptx", "authored"]
    source_filename: str = Field(min_length=1, max_length=255)
    source_digest: Digest = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    structure_path: Literal["structure.json"] = "structure.json"
    structure_digest: Digest = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    inventory: tuple[PackageFile, ...] = Field(min_length=2, max_length=5000)
    inventory_digest: Digest = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    package_digest: Digest = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _sealed(self) -> "DesignPackManifest":
        paths = [item.path for item in self.inventory]
        if len(paths) != len(set(paths)) or "structure.json" not in paths:
            raise ValueError("design_pack_inventory_invalid")
        inventory_payload = [
            item.model_dump(mode="json", by_alias=True) for item in self.inventory
        ]
        if self.inventory_digest != canonical_digest(inventory_payload):
            raise ValueError("design_pack_inventory_digest_mismatch")
        payload = self.model_dump(mode="json", by_alias=True, exclude={"package_digest"})
        if self.package_digest != canonical_digest(payload):
            raise ValueError("design_pack_package_digest_mismatch")
        return self


def seal_structure(**values: Any) -> DesignPackStructure:
    provisional = DesignPackStructure.model_construct(
        **values, structure_digest="sha256:" + "0" * 64
    )
    payload = provisional.model_dump(
        mode="json", by_alias=True, exclude={"structure_digest"}
    )
    return DesignPackStructure(**values, structure_digest=canonical_digest(payload))


def seal_manifest(**values: Any) -> DesignPackManifest:
    inventory = tuple(values["inventory"])
    inventory_payload = [
        item.model_dump(mode="json", by_alias=True)
        if isinstance(item, PackageFile)
        else item
        for item in inventory
    ]
    values = {
        **values,
        "inventory": inventory,
        "inventory_digest": canonical_digest(inventory_payload),
    }
    provisional = DesignPackManifest.model_construct(
        **values, package_digest="sha256:" + "0" * 64
    )
    payload = provisional.model_dump(
        mode="json", by_alias=True, exclude={"package_digest"}
    )
    return DesignPackManifest(**values, package_digest=canonical_digest(payload))


__all__ = [
    "CapacityEnvelope",
    "Canvas",
    "DesignFrame",
    "DesignPackManifest",
    "DesignPackStructure",
    "DesignTokenSummary",
    "EditableSlot",
    "FallbackPolicy",
    "NativeElement",
    "PackageFile",
    "Paint",
    "PatchHandle",
    "Rect",
    "RepresentativeRenderRef",
    "SlotConstraint",
    "Spacing",
    "TextStyle",
    "UnsupportedElement",
    "canonical_digest",
    "seal_manifest",
    "seal_structure",
]
