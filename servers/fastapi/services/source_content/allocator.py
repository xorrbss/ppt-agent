"""Deterministic, ID-only allocation for source-content restyling.

The allocator is deliberately ignorant of source strings and values.  It only
sees immutable block identities, kinds, ordering metadata and bounded lengths.
All payload materialization happens later from the admitted manifest.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any, Literal, Mapping, Sequence

from pydantic import Field, model_validator

from models.authored_vnext.master.base import Digest, StrictCamelModel
from models.authored_vnext.master.contract import Rect
from services.source_content.contracts import (
    RESTYLE_CONTENT_KINDS,
    SourceContentManifestPin,
)


PLAN_VERSION = "source-content-placement-plan/v1"
MERGE_POLICY_VERSION = "master-slots-authored-body/v3"


def canonical_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dump(mode="json", by_alias=True)
    raise ValueError("source_content_manifest_invalid")


def _field(value: Mapping[str, Any], camel: str, snake: str | None = None) -> Any:
    if camel in value:
        return value[camel]
    return value.get(snake or camel)


class SourceRange(StrictCamelModel):
    kind: Literal["atomic", "text-run-range", "table-row-range"]
    start: int = Field(ge=0)
    end_exclusive: int = Field(gt=0)

    @model_validator(mode="after")
    def _ordered(self) -> "SourceRange":
        if self.end_exclusive <= self.start:
            raise ValueError("source_content_fragment_range_invalid")
        if self.kind == "atomic" and (self.start != 0 or self.end_exclusive != 1):
            raise ValueError("source_content_atomic_range_invalid")
        return self


class WritableSlot(StrictCamelModel):
    slot_id: str = Field(min_length=1, max_length=200)
    bounds: Rect
    accepted_kinds: tuple[
        Literal["text", "table", "chart", "image", "shape", "source_note"],
        ...,
    ] = (
        "text",
        "table",
        "chart",
        "image",
    )
    semantic_roles: tuple[str, ...] = ()
    capacity_units: int = Field(default=1, ge=1, le=1000)


class WritableLayout(StrictCamelModel):
    layout_role: str = Field(min_length=1, max_length=100)
    layout_id: str = Field(min_length=1, max_length=200)
    slots: tuple[WritableSlot, ...] = Field(min_length=1, max_length=64)


class PlacementCompositionPin(StrictCamelModel):
    authored_style_id: str = Field(min_length=1, max_length=200)
    authored_style_digest: Digest
    authored_style_pin_digest: Digest
    company_standard_master_id: str | None = Field(default=None, max_length=200)
    company_standard_revision: int | None = Field(default=None, ge=1)
    master_digest: Digest | None = None
    master_geometry_digest: Digest
    merge_policy_version: Literal["master-slots-authored-body/v3"] = (
        MERGE_POLICY_VERSION
    )


class SourceContentPlacement(StrictCamelModel):
    source_block_id: str = Field(min_length=1, max_length=300)
    source_unit_id: str = Field(min_length=1, max_length=300)
    source_order: int = Field(ge=0)
    slot_id: str = Field(min_length=1, max_length=200)
    render_mode: Literal["native", "rasterFallback"]
    source_range: SourceRange
    derived_duplicate: Literal[False] = False


class SourceContentPlacementSlide(StrictCamelModel):
    output_index: int = Field(ge=0)
    source_unit_id: str = Field(min_length=1, max_length=300)
    source_part: int = Field(ge=1)
    layout_role: str = Field(min_length=1, max_length=100)
    layout_id: str = Field(min_length=1, max_length=200)
    # A source page is locked to one output slide.  A template may therefore
    # need more placements than its initial slot count; the materializer
    # deterministically re-composes repeated slots within the same page.
    placements: tuple[SourceContentPlacement, ...] = Field(max_length=4096)


class SourceContentPlacementPlan(StrictCamelModel):
    contract_version: Literal["source-content-placement-plan/v1"] = PLAN_VERSION
    manifest_pin: SourceContentManifestPin
    composition_pin: PlacementCompositionPin
    # Do not impose an arbitrary 500-slide ceiling: cardinality is governed by
    # the admitted source manifest (one output slide per source unit).
    slides: tuple[SourceContentPlacementSlide, ...] = Field(min_length=1)
    plan_digest: Digest

    @model_validator(mode="after")
    def _sealed_partition_and_order(self) -> "SourceContentPlacementPlan":
        indexes = [slide.output_index for slide in self.slides]
        if indexes != list(range(len(indexes))):
            raise ValueError("source_content_output_order_invalid")
        placements = [item for slide in self.slides for item in slide.placements]
        slide_units = [slide.source_unit_id for slide in self.slides]
        if len(set(slide_units)) != len(slide_units):
            raise ValueError("source_content_source_unit_duplicate")
        if any(
            placement.source_unit_id != slide.source_unit_id
            for slide in self.slides
            for placement in slide.placements
        ):
            raise ValueError("source_content_plan_manifest_mismatch")
        if [item.source_order for item in placements] != sorted(
            item.source_order for item in placements
        ):
            raise ValueError("source_content_source_order_violation")
        fragments: dict[str, list[SourceRange]] = defaultdict(list)
        for item in placements:
            fragments[item.source_block_id].append(item.source_range)
        for ranges in fragments.values():
            ordered = sorted(ranges, key=lambda item: item.start)
            if ordered[0].start != 0:
                raise ValueError("source_content_fragment_gap")
            for previous, current in zip(ordered, ordered[1:]):
                if current.start < previous.end_exclusive:
                    raise ValueError("source_content_fragment_overlap")
                if current.start > previous.end_exclusive:
                    raise ValueError("source_content_fragment_gap")
            if any(item.kind == "atomic" for item in ordered) and len(ordered) != 1:
                raise ValueError("source_content_atomic_placement_duplicate")
        payload = self.model_dump(mode="json", by_alias=True, exclude={"plan_digest"})
        if self.plan_digest != canonical_digest(payload):
            raise ValueError("source_content_plan_digest_mismatch")
        return self


def _block_units(block: Mapping[str, Any]) -> tuple[str, int]:
    kind = str(_field(block, "kind") or "")
    payload = _mapping(_field(block, "payload") or {})
    if kind in {"text", "source_note"}:
        runs = _field(payload, "runs")
        return "text-run-range", max(1, len(runs) if isinstance(runs, list) else 1)
    if kind == "table":
        cells = _field(payload, "cells")
        return "table-row-range", max(1, len(cells) if isinstance(cells, list) else 1)
    return "atomic", 1


def _render_mode(block: Mapping[str, Any]) -> Literal["native", "rasterFallback"]:
    native = _field(block, "nativeCandidate", "native_candidate")
    fallback = _field(block, "fallbackAssetId", "fallback_asset_id")
    if native is False:
        if not fallback:
            raise ValueError("source_content_unsupported_object")
        return "rasterFallback"
    return "native"


class ContentAllocator:
    """Allocate every manifest block into a deterministic ordered plan."""

    def allocate(
        self,
        *,
        manifest: Any,
        manifest_pin: SourceContentManifestPin,
        composition_pin: PlacementCompositionPin,
        layouts: Sequence[WritableLayout],
        outline: Any | None = None,
    ) -> SourceContentPlacementPlan:
        manifest_value = _mapping(manifest)
        manifest_digest = _field(manifest_value, "manifestDigest", "manifest_digest")
        manifest_id = _field(manifest_value, "manifestId", "manifest_id")
        if manifest_digest != manifest_pin.digest or manifest_id != manifest_pin.manifest_id:
            raise ValueError("source_content_manifest_invalid")
        units = _field(manifest_value, "units")
        if not isinstance(units, list) or not units:
            raise ValueError("source_content_manifest_invalid")
        if not layouts:
            raise ValueError("source_content_unplaceable")

        ordered_units = sorted(
            (_mapping(unit) for unit in units),
            key=lambda unit: (
                int(_field(unit, "sourceIndex", "source_index") or 0),
                str(_field(unit, "sourceUnitId", "source_unit_id") or ""),
            ),
        )
        if outline is not None:
            outline_value = _mapping(outline)
            outline_units = _field(outline_value, "slides")
            if not isinstance(outline_units, list):
                raise ValueError("source_content_outline_invalid")
            outline_ids = [
                str(_field(_mapping(item), "sourceUnitId", "source_unit_id") or "")
                for item in outline_units
            ]
            manifest_ids = [
                str(_field(unit, "sourceUnitId", "source_unit_id") or "")
                for unit in ordered_units
            ]
            if outline_ids != manifest_ids:
                raise ValueError("source_content_source_unit_cardinality_mismatch")
            for unit, outline_slide in zip(ordered_units, outline_units):
                block_ids = [
                    str(_field(_mapping(block), "sourceBlockId", "source_block_id") or "")
                    for block in sorted(
                        (_mapping(block) for block in (_field(unit, "blocks") or [])),
                        key=lambda block: (
                            int(_field(block, "order") or 0),
                            str(_field(block, "sourceBlockId", "source_block_id") or ""),
                        ),
                    )
                    if str(_field(block, "kind") or "") in RESTYLE_CONTENT_KINDS
                ]
                reading_order = [
                    str(item)
                    for item in (_field(_mapping(outline_slide), "readingOrder", "reading_order") or [])
                ]
                if reading_order != block_ids:
                    raise ValueError("source_content_outline_invalid")
        slides: list[SourceContentPlacementSlide] = []
        seen_block_ids: set[str] = set()
        source_order = 0
        output_index = 0

        for unit in ordered_units:
            source_unit_id = str(_field(unit, "sourceUnitId", "source_unit_id") or "")
            blocks = _field(unit, "blocks")
            if not source_unit_id or not isinstance(blocks, list):
                raise ValueError("source_content_manifest_invalid")
            ordered_blocks = sorted(
                (_mapping(block) for block in blocks),
                key=lambda block: (
                    int(_field(block, "order") or 0),
                    str(_field(block, "sourceBlockId", "source_block_id") or ""),
                ),
            )
            layout = layouts[0]
            current: list[SourceContentPlacement] = []
            used_slots: set[str] = set()
            source_part = 1

            for block in ordered_blocks:
                block_id = str(_field(block, "sourceBlockId", "source_block_id") or "")
                kind = str(_field(block, "kind") or "")
                if kind not in RESTYLE_CONTENT_KINDS:
                    # Shapes and speaker notes are template-owned design or
                    # metadata, not source substance to place in the output.
                    continue
                semantic_role = str(
                    _field(block, "semanticRole", "semantic_role") or "body"
                )
                if not block_id or block_id in seen_block_ids:
                    raise ValueError("source_content_manifest_invalid")
                seen_block_ids.add(block_id)
                range_kind, unit_count = _block_units(block)
                start = 0
                while start < unit_count:
                    candidates = [
                        slot
                        for slot in layout.slots
                        if slot.slot_id not in used_slots
                        and kind in slot.accepted_kinds
                        and (not slot.semantic_roles or semantic_role in slot.semantic_roles)
                    ]
                    if not candidates:
                        # Page-locked composition: once every compatible slot
                        # has been used, reuse the compatible slot on this same
                        # source page.  Materialization performs deterministic
                        # in-page re-composition for repeated slots.
                        candidates = [
                            slot
                            for slot in layout.slots
                            if kind in slot.accepted_kinds
                            and (not slot.semantic_roles or semantic_role in slot.semantic_roles)
                        ]
                    if not candidates:
                        raise ValueError("source_content_template_overflow")
                    slot = candidates[0]
                    end = min(unit_count, start + slot.capacity_units)
                    current.append(
                        SourceContentPlacement(
                            source_block_id=block_id,
                            source_unit_id=source_unit_id,
                            source_order=source_order,
                            slot_id=slot.slot_id,
                            render_mode=_render_mode(block),
                            source_range=SourceRange(
                                kind=range_kind,
                                start=start,
                                end_exclusive=end,
                            ),
                        )
                    )
                    used_slots.add(slot.slot_id)
                    source_order += 1
                    start = end

            if len(current) > 4096:
                raise ValueError("source_content_template_overflow")
            # Exactly one output slide for every source unit, including an
            # empty source page.  Empty pages are preserved rather than
            # silently omitted.
            slides.append(
                SourceContentPlacementSlide(
                    output_index=output_index,
                    source_unit_id=source_unit_id,
                    source_part=source_part,
                    layout_role=layout.layout_role,
                    layout_id=layout.layout_id,
                    placements=tuple(current),
                )
            )
            output_index += 1

        if not slides:
            raise ValueError("source_content_unplaceable")
        payload = {
            "contractVersion": PLAN_VERSION,
            "manifestPin": manifest_pin.model_dump(mode="json", by_alias=True),
            "compositionPin": composition_pin.model_dump(mode="json", by_alias=True),
            "slides": [slide.model_dump(mode="json", by_alias=True) for slide in slides],
        }
        return SourceContentPlacementPlan(
            manifest_pin=manifest_pin,
            composition_pin=composition_pin,
            slides=tuple(slides),
            plan_digest=canonical_digest(payload),
        )


class PageLockedTemplateCompositionPlanner(ContentAllocator):
    """Named planner façade for the page-locked source-content contract.

    Keeping the allocator implementation as the single deterministic path
    avoids a second planner that could accidentally reintroduce slide
    expansion.  ``plan`` is provided as the terminology used by the design
    contract; ``allocate`` remains backwards compatible.
    """

    def plan(self, **kwargs: Any) -> SourceContentPlacementPlan:
        return self.allocate(**kwargs)


__all__ = [
    "ContentAllocator",
    "PageLockedTemplateCompositionPlanner",
    "MERGE_POLICY_VERSION",
    "PlacementCompositionPin",
    "SourceContentManifestPin",
    "SourceContentPlacementPlan",
    "SourceRange",
    "WritableLayout",
    "WritableSlot",
    "canonical_digest",
]
