"""Deterministically materialize an ID-only placement plan from a manifest.

No caller- or model-supplied content is accepted here.  Every rendered string,
cell, chart value and image reference is re-read from the immutable manifest.
"""

from __future__ import annotations

import base64
import math
import re
from typing import Any, Literal, Mapping, Sequence

from pydantic import Field, model_validator

from models.authored_vnext.deck.scene import (
    DeckSceneIR,
    SceneAssetManifestEntry,
    SceneChartElement,
    SceneDataManifestEntry,
    SceneEditability,
    SceneImageElement,
    SceneShapeElement,
    SceneTableElement,
    SceneTextElement,
    SceneTextRun,
    SceneTextStyle,
    seal_deck_scene,
    seal_slide_scene,
    scene_digest,
)
from models.authored_vnext.master.base import Digest, StrictCamelModel
from models.authored_vnext.master.contract import Rect
from services.authored_vnext.generation.scene_compiler import (
    default_scene_capabilities,
)
from services.source_content.allocator import (
    SourceContentPlacementPlan,
    SourceRange,
    WritableLayout,
    canonical_digest,
)


MATERIALIZED_SCENE_VERSION = "source-content-materialized-scene/v1"


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


def _stable_suffix(value: str, *, length: int = 20) -> str:
    return canonical_digest(value).removeprefix("sha256:")[:length]


class SourceContentStyleTokens(StrictCamelModel):
    background: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    surface: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    text_primary: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    text_secondary: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    accent: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    heading_font: str = Field(min_length=1, max_length=200)
    body_font: str = Field(min_length=1, max_length=200)


class SourceContentSceneBinding(StrictCamelModel):
    source_block_id: str
    source_unit_id: str
    source_range: SourceRange
    content_digest: Digest
    slide_id: str
    element_id: str
    data_id: str | None = None
    asset_id: str | None = None
    source_order: int = Field(ge=0)
    derived_duplicate: Literal[False] = False


class SourceContentCompositionApplication(StrictCamelModel):
    style_token_digest: Digest
    style_token_digest_applied: bool
    master_geometry_digest: Digest | None = None
    master_geometry_digest_applied: bool


class SourceContentSceneValidation(StrictCamelModel):
    overflow_count: int = Field(ge=0)
    clipping_count: int = Field(ge=0)
    canvas_bounds: Rect


class MaterializedSourceContentScene(StrictCamelModel):
    contract_version: Literal["source-content-materialized-scene/v1"] = (
        MATERIALIZED_SCENE_VERSION
    )
    manifest_digest: Digest
    plan_digest: Digest
    scene: DeckSceneIR
    source_bindings: tuple[SourceContentSceneBinding, ...] = Field(min_length=1)
    composition_application: SourceContentCompositionApplication
    scene_validation: SourceContentSceneValidation
    materialization_digest: Digest

    @model_validator(mode="after")
    def _sealed(self) -> "MaterializedSourceContentScene":
        payload = self.model_dump(
            mode="json", by_alias=True, exclude={"materialization_digest"}
        )
        if self.materialization_digest != canonical_digest(payload):
            raise ValueError("source_content_materialization_digest_mismatch")
        element_ids = {
            (slide.slide_id, element.element_id)
            for slide in self.scene.slides
            for element in slide.elements
            if element.semantic_role != "canvasBackground"
        }
        binding_ids = {(item.slide_id, item.element_id) for item in self.source_bindings}
        if element_ids != binding_ids:
            raise ValueError("source_content_scene_binding_mismatch")
        if self.scene_validation.overflow_count or self.scene_validation.clipping_count:
            raise ValueError("source_content_overflow")
        return self


def _slice_text(payload: Mapping[str, Any], source_range: SourceRange) -> tuple[str, list[Any]]:
    raw_runs = _field(payload, "runs")
    if isinstance(raw_runs, list) and raw_runs:
        selected = raw_runs[source_range.start : source_range.end_exclusive]
        if len(selected) != source_range.end_exclusive - source_range.start:
            raise ValueError("source_content_fragment_gap")
        text = "".join(
            str(item.get("text", "")) if isinstance(item, Mapping) else str(item)
            for item in selected
        )
        return text, selected
    if source_range.start != 0 or source_range.end_exclusive != 1:
        raise ValueError("source_content_fragment_range_invalid")
    text = _field(payload, "text")
    if not isinstance(text, str):
        raise ValueError("source_content_manifest_invalid")
    return text, [text]


def _slice_table(payload: Mapping[str, Any], source_range: SourceRange) -> dict[str, Any]:
    cells = _field(payload, "cells")
    if not isinstance(cells, list) or not cells:
        raise ValueError("source_content_manifest_invalid")
    selected = cells[source_range.start : source_range.end_exclusive]
    if len(selected) != source_range.end_exclusive - source_range.start:
        raise ValueError("source_content_fragment_gap")
    result: dict[str, Any] = {"cells": selected}
    row_heights = _field(payload, "rowHeightsPt", "row_heights_pt")
    if isinstance(row_heights, list):
        result["rowHeightsPt"] = row_heights[
            source_range.start : source_range.end_exclusive
        ]
    for camel, snake in (
        ("columnWidthsPt", "column_widths_pt"),
        ("cellStyles", "cell_styles"),
    ):
        value = _field(payload, camel, snake)
        if value is not None:
            result[camel] = (
                value[source_range.start : source_range.end_exclusive]
                if camel == "cellStyles" and isinstance(value, list)
                else value
            )
    merged = _field(payload, "mergedCells", "merged_cells")
    if isinstance(merged, list):
        adjusted: list[dict[str, Any]] = []
        for raw in merged:
            item = _mapping(raw)
            row = int(
                _field(item, "row")
                if _field(item, "row") is not None
                else _field(item, "startRow", "start_row") or 0
            )
            row_span = int(_field(item, "rowSpan", "row_span") or 0)
            end = row + row_span
            if end <= source_range.start or row >= source_range.end_exclusive:
                continue
            if row < source_range.start or end > source_range.end_exclusive:
                raise ValueError("source_content_unplaceable")
            adjusted.append(
                {
                    "row": row - source_range.start,
                    "column": int(
                        _field(item, "column")
                        if _field(item, "column") is not None
                        else _field(item, "startColumn", "start_column") or 0
                    ),
                    "rowSpan": row_span,
                    "columnSpan": int(_field(item, "columnSpan", "column_span") or 0),
                }
            )
        if adjusted:
            result["mergedCells"] = adjusted
    return result


def _asset_entry(
    block: Mapping[str, Any],
    payload: Mapping[str, Any],
    sealed_assets: Mapping[str, Mapping[str, Any]],
) -> SceneAssetManifestEntry:
    asset = _field(payload, "asset")
    source = _mapping(asset) if asset is not None else payload
    asset_id = _field(source, "assetId", "asset_id") or _field(
        block, "fallbackAssetId", "fallback_asset_id"
    )
    digest = _field(source, "assetDigest", "asset_digest") or _field(source, "digest")
    media_type = _field(source, "mediaType", "media_type")
    storage = _field(source, "storageReference", "storage_reference")
    size = _field(source, "sizeBytes", "size_bytes")
    if not isinstance(asset_id, str):
        raise ValueError("source_content_manifest_invalid")
    if re.fullmatch(r"asset:[a-z0-9][a-z0-9._-]*", asset_id) is None:
        sealed = sealed_assets.get(asset_id)
        if sealed is None:
            raise ValueError("source_content_manifest_invalid")
        content = sealed.get("content")
        digest = sealed.get("digest")
        media_type = sealed.get("media_type")
        if not isinstance(content, bytes) or not isinstance(media_type, str):
            raise ValueError("source_content_manifest_invalid")
        asset_id = f"asset:source_{_stable_suffix(str(asset_id), length=32)}"
        storage = (
            f"data:{media_type};base64,"
            f"{base64.b64encode(content).decode('ascii')}"
        )
        size = len(content)
    try:
        return SceneAssetManifestEntry(
            asset_id=asset_id,
            asset_digest=digest,
            media_type=media_type,
            storage_reference=storage,
            size_bytes=size,
        )
    except Exception as error:
        raise ValueError("source_content_manifest_invalid") from error


class SourceContentSceneMaterializer:
    """Build the canonical Scene and provenance envelope without generation."""

    def materialize(
        self,
        *,
        manifest: Any,
        plan: SourceContentPlacementPlan,
        layouts: Sequence[WritableLayout],
        style_tokens: SourceContentStyleTokens,
        canvas_bounds: Rect,
        deck_id: str,
        revision: int = 1,
        sealed_assets: Mapping[str, Mapping[str, Any]] | None = None,
        scene_composition_digest: str | None = None,
    ) -> MaterializedSourceContentScene:
        manifest_value = _mapping(manifest)
        if _field(manifest_value, "manifestDigest", "manifest_digest") != plan.manifest_pin.digest:
            raise ValueError("source_content_manifest_invalid")
        blocks: dict[str, tuple[str, Mapping[str, Any]]] = {}
        for raw_unit in _field(manifest_value, "units") or []:
            unit = _mapping(raw_unit)
            unit_id = str(_field(unit, "sourceUnitId", "source_unit_id") or "")
            for raw_block in _field(unit, "blocks") or []:
                block = _mapping(raw_block)
                block_id = str(_field(block, "sourceBlockId", "source_block_id") or "")
                blocks[block_id] = (unit_id, block)
        layout_by_id = {layout.layout_id: layout for layout in layouts}
        style_digest = canonical_digest(style_tokens.model_dump(mode="json", by_alias=True))
        if style_digest != plan.composition_pin.authored_style_digest:
            raise ValueError("source_content_composition_not_applied")

        scene_slides = []
        bindings: list[SourceContentSceneBinding] = []
        assets: dict[str, SceneAssetManifestEntry] = {}
        data_entries: dict[str, SceneDataManifestEntry] = {}
        clipping_count = 0

        for planned_slide in plan.slides:
            layout = layout_by_id.get(planned_slide.layout_id)
            if layout is None:
                raise ValueError("source_content_composition_not_applied")
            slot_by_id = {slot.slot_id: slot for slot in layout.slots}
            slot_use_counts: dict[str, int] = {}
            for placement in planned_slide.placements:
                slot_use_counts[placement.slot_id] = slot_use_counts.get(placement.slot_id, 0) + 1
            # A source page may contain many small text owners while the
            # selected template exposes one body slot. Recompose repeated
            # owners as a deterministic reading-order grid instead of
            # squeezing them into a single vertical pixel strip.
            slot_flow: dict[str, tuple[int, int]] = {}
            for slot_id, total in slot_use_counts.items():
                columns = min(4, total) if total > 1 else 1
                rows = math.ceil(total / columns)
                slot_flow[slot_id] = (columns, rows)
            slot_use_ordinals: dict[str, int] = {}
            slide_id = f"slide:source-{planned_slide.output_index + 1}"
            # The selected template master owns the slide background.  Do not
            # emit a duplicate canvasBackground Scene element: the OOXML
            # materializer deliberately rejects body-level background shapes.
            elements: list[Any] = []
            for placement in planned_slide.placements:
                source = blocks.get(placement.source_block_id)
                slot = slot_by_id.get(placement.slot_id)
                if source is None or slot is None or source[0] != placement.source_unit_id:
                    raise ValueError("source_content_plan_manifest_mismatch")
                block = source[1]
                payload = _mapping(_field(block, "payload") or {})
                kind = str(_field(block, "kind") or "")
                content_digest = str(
                    _field(block, "contentDigest", "content_digest") or ""
                )
                if not content_digest.startswith("sha256:"):
                    raise ValueError("source_content_manifest_invalid")
                element_id = (
                    f"element:source-{planned_slide.output_index + 1}."
                    f"{_stable_suffix(placement.source_block_id + ':' + str(placement.source_range.start))}"
                )
                semantic_role = str(
                    _field(block, "semanticRole", "semantic_role") or "body"
                )
                # Repeated template slots are a same-page recompose, not a new
                # output slide.  Split the slot's rectangle deterministically
                # along its major axis so every occurrence remains visible and
                # independently bound to its source block/range.
                base_bounds = slot.bounds
                total_uses = slot_use_counts.get(placement.slot_id, 1)
                ordinal = slot_use_ordinals.get(placement.slot_id, 0)
                slot_use_ordinals[placement.slot_id] = ordinal + 1
                if total_uses > 1:
                    columns, rows = slot_flow[placement.slot_id]
                    cell_width = base_bounds.width / columns
                    cell_height = base_bounds.height / rows
                    column = ordinal % columns
                    row = ordinal // columns
                    bounds = Rect(
                        x=base_bounds.x + cell_width * column,
                        y=base_bounds.y + cell_height * row,
                        width=(
                            base_bounds.width - cell_width * column
                            if column == columns - 1
                            else cell_width
                        ),
                        height=(
                            base_bounds.height - cell_height * row
                            if row == rows - 1
                            else cell_height
                        ),
                    )
                else:
                    bounds = base_bounds
                data_id: str | None = None
                asset_id: str | None = None
                if (
                    bounds.x < canvas_bounds.x
                    or bounds.y < canvas_bounds.y
                    or bounds.right > canvas_bounds.right
                    or bounds.bottom > canvas_bounds.bottom
                ):
                    clipping_count += 1

                if placement.render_mode == "rasterFallback" or kind == "image":
                    asset = _asset_entry(block, payload, sealed_assets or {})
                    assets.setdefault(asset.asset_id, asset)
                    asset_id = asset.asset_id
                    element = SceneImageElement(
                        kind="image",
                        element_id=element_id,
                        z_order=10 + placement.source_order,
                        bounds=bounds,
                        semantic_role=semantic_role,
                        editability=SceneEditability(geometry=True, style=True, asset=True),
                        asset_id=asset.asset_id,
                        asset_digest=asset.asset_digest,
                        alt_text=str(_field(payload, "altText", "alt_text") or semantic_role),
                        crop="contain",
                    )
                elif kind in {"text", "source_note"}:
                    text, raw_runs = _slice_text(payload, placement.source_range)
                    if not text:
                        raise ValueError("source_content_manifest_invalid")
                    font = (
                        style_tokens.heading_font
                        if semantic_role in {"title", "heading", "section"}
                        else style_tokens.body_font
                    )
                    size = 30.0 if semantic_role in {"title", "heading", "section"} else 18.0
                    # PowerPoint COM auto-fit is not deterministic in readback.
                    # Seal a size based on the estimated wrapped line count so
                    # dense source content remains on the source page without
                    # collapsing every repeated block to the same tiny font.
                    # Scene geometry is expressed in CSS pixels while native
                    # PowerPoint text is measured in points (0.75pt/px on the
                    # 96dpi canvas).  CJK glyphs are approximately one em wide;
                    # using the pixel width directly substantially
                    # under-counts wrapping and produces false 17pt fits in a
                    # 24pt-high box.
                    available_width_pt = bounds.width * 0.75
                    estimated_chars_per_line = max(
                        1, int(available_width_pt / max(size * 0.9, 1.0))
                    )
                    estimated_lines = sum(
                        max(1, math.ceil(len(line) / estimated_chars_per_line))
                        for line in text.split("\n")
                    )
                    # Native PowerPoint stores point sizes on a twips grid and
                    # can read a two-decimal value back one hundredth lower
                    # (for example 2.55 -> 2.54).  A fractional value would
                    # therefore make the sealed Scene style digest differ from
                    # the COM readback.  Integer points are stable across the
                    # native exporter/readback boundary while the 4-column
                    # recompose grid keeps repeated source blocks legible.
                    fitted_size = (
                        max(
                            1.0,
                            math.floor(
                                max(
                                    1.0,
                                    min(
                                        size,
                                        bounds.height
                                        * 0.6
                                        / max(estimated_lines, 1),
                                    ),
                                )
                            ),
                        )
                        if total_uses > 1
                        else size
                    )
                    runs = [
                        SceneTextRun(
                            run_id=f"run:{_stable_suffix(element_id + ':' + str(index))}",
                            text=(
                                str(item.get("text", ""))
                                if isinstance(item, Mapping)
                                else str(item)
                            ),
                            font_family=font,
                            font_size=fitted_size,
                            color=style_tokens.text_primary,
                        )
                        for index, item in enumerate(raw_runs)
                    ]
                    scene_text = "".join(run.text for run in runs)
                    if scene_text != text:
                        raise ValueError("source_content_manifest_invalid")
                    element = SceneTextElement(
                        kind="text",
                        element_id=element_id,
                        z_order=10 + placement.source_order,
                        bounds=bounds,
                        semantic_role=semantic_role,
                        editability=SceneEditability(text=True, geometry=True, style=True),
                        text=scene_text,
                        runs=runs,
                        style=SceneTextStyle(
                            font_family=font,
                            font_size=fitted_size,
                            color=style_tokens.text_primary,
                            # Page-locked recomposition may place many source
                            # text blocks into the same template slot. Let
                            # PowerPoint shrink those repeated boxes to their
                            # sealed bounds instead of overflowing or adding a
                            # new slide.
                            auto_fit="shrink" if total_uses > 1 else "none",
                        ),
                    )
                elif kind == "table":
                    table_payload = _slice_table(payload, placement.source_range)
                    data_id = f"data:source_{_stable_suffix(placement.source_block_id + ':' + str(placement.source_range.start))}"
                    entry = SceneDataManifestEntry(
                        data_id=data_id,
                        data_digest=scene_digest(table_payload),
                        kind="table",
                        payload=table_payload,
                    )
                    data_entries[data_id] = entry
                    element = SceneTableElement(
                        kind="table",
                        element_id=element_id,
                        z_order=10 + placement.source_order,
                        bounds=bounds,
                        semantic_role=semantic_role,
                        editability=SceneEditability(geometry=True, style=True, data=True),
                        data_id=data_id,
                        data_digest=entry.data_digest,
                        rows=len(table_payload["cells"]),
                        columns=len(table_payload["cells"][0]),
                    )
                elif kind == "chart":
                    chart_payload = {
                        key: value
                        for key in ("categories", "series", "style")
                        if (value := _field(payload, key)) is not None
                    }
                    data_id = f"data:source_{_stable_suffix(placement.source_block_id)}"
                    entry = SceneDataManifestEntry(
                        data_id=data_id,
                        data_digest=scene_digest(chart_payload),
                        kind="chart",
                        payload=chart_payload,
                    )
                    data_entries[data_id] = entry
                    raw_chart_type = str(
                        _field(payload, "chartType", "chart_type") or "column"
                    )
                    chart_type = {
                        "barChart": "column",
                        "lineChart": "line",
                        "areaChart": "area",
                        "pieChart": "pie",
                        "doughnutChart": "donut",
                    }.get(raw_chart_type, raw_chart_type)
                    element = SceneChartElement(
                        kind="chart",
                        element_id=element_id,
                        z_order=10 + placement.source_order,
                        bounds=bounds,
                        semantic_role=semantic_role,
                        editability=SceneEditability(geometry=True, style=True, data=True),
                        data_id=data_id,
                        data_digest=entry.data_digest,
                        chart_type=chart_type,
                        accessibility_summary=str(
                            _field(payload, "accessibilitySummary", "accessibility_summary")
                            or semantic_role
                        ),
                    )
                elif kind == "shape":
                    # The PPTX extractor seals the OOXML preset under `preset`.
                    # Accept the short-lived `shape` spelling for compatibility,
                    # but never coerce an absent/unsupported preset to `rect`.
                    # Doing so would preserve only the block ID while changing
                    # the source object's visible semantics.
                    source_preset = _field(payload, "preset")
                    if source_preset is None:
                        source_preset = _field(payload, "shape")
                    supported_presets = {
                        "rect": "rect",
                        "roundRect": "roundRect",
                        "ellipse": "ellipse",
                        "triangle": "triangle",
                        "diamond": "diamond",
                        "chevron": "chevron",
                        "line": "line",
                    }
                    shape = supported_presets.get(str(source_preset))
                    if shape is None:
                        raise ValueError("source_content_unsupported_object")
                    element = SceneShapeElement(
                        kind="shape",
                        element_id=element_id,
                        z_order=10 + placement.source_order,
                        bounds=bounds,
                        semantic_role=semantic_role,
                        editability=SceneEditability(geometry=True, style=True),
                        shape=shape,
                        fill=str(_field(payload, "fill") or style_tokens.surface),
                        stroke=str(_field(payload, "stroke") or style_tokens.accent),
                    )
                else:
                    raise ValueError("source_content_unsupported_object")
                elements.append(element)
                bindings.append(
                    SourceContentSceneBinding(
                        source_block_id=placement.source_block_id,
                        source_unit_id=placement.source_unit_id,
                        source_range=placement.source_range,
                        content_digest=content_digest,
                        slide_id=slide_id,
                        element_id=element_id,
                        data_id=data_id,
                        asset_id=asset_id,
                        source_order=placement.source_order,
                    )
                )
            scene_slides.append(
                seal_slide_scene(
                    slide_id=slide_id,
                    revision=revision,
                    elements=elements,
                )
            )

        composition_digest = scene_composition_digest or canonical_digest(
            plan.composition_pin.model_dump(mode="json", by_alias=True)
        )
        scene = seal_deck_scene(
            deck_id=deck_id,
            revision=revision,
            composition_digest=composition_digest,
            source_digest=plan.manifest_pin.digest,
            slides=scene_slides,
            asset_manifest=list(assets.values()),
            data_manifest=list(data_entries.values()),
            capabilities=default_scene_capabilities(),
        )
        application = SourceContentCompositionApplication(
            style_token_digest=style_digest,
            style_token_digest_applied=(
                style_digest == plan.composition_pin.authored_style_digest
            ),
            master_geometry_digest=canonical_digest(
                [layout.model_dump(mode="json", by_alias=True) for layout in layouts]
            ),
            master_geometry_digest_applied=(
                canonical_digest(
                    [layout.model_dump(mode="json", by_alias=True) for layout in layouts]
                )
                == plan.composition_pin.master_geometry_digest
            ),
        )
        if not (
            application.style_token_digest_applied
            and application.master_geometry_digest_applied
        ):
            raise ValueError("source_content_composition_not_applied")
        validation = SourceContentSceneValidation(
            overflow_count=0,
            clipping_count=clipping_count,
            canvas_bounds=canvas_bounds,
        )
        payload = {
            "contractVersion": MATERIALIZED_SCENE_VERSION,
            "manifestDigest": plan.manifest_pin.digest,
            "planDigest": plan.plan_digest,
            "scene": scene.model_dump(mode="json", by_alias=True),
            "sourceBindings": [
                binding.model_dump(mode="json", by_alias=True) for binding in bindings
            ],
            "compositionApplication": application.model_dump(mode="json", by_alias=True),
            "sceneValidation": validation.model_dump(mode="json", by_alias=True),
        }
        return MaterializedSourceContentScene(
            manifest_digest=plan.manifest_pin.digest,
            plan_digest=plan.plan_digest,
            scene=scene,
            source_bindings=tuple(bindings),
            composition_application=application,
            scene_validation=validation,
            materialization_digest=canonical_digest(payload),
        )


__all__ = [
    "MATERIALIZED_SCENE_VERSION",
    "MaterializedSourceContentScene",
    "SourceContentCompositionApplication",
    "SourceContentSceneBinding",
    "SourceContentSceneMaterializer",
    "SourceContentSceneValidation",
    "SourceContentStyleTokens",
]
