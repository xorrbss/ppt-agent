"""Fail-closed staged Scene coverage for source-content restyling.

The verifier deliberately consumes the materializer envelope instead of trying
to infer provenance from visible text.  Source bindings are the bridge between
the immutable manifest, the ID-only placement plan, and canonical Scene IR.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from services.authored_vnext.master.digests import canonical_digest


class SourceContentCoverageError(ValueError):
    """A publication-blocking source-content coverage failure."""

    def __init__(self, code: str, *, detail: str, block_ids: tuple[str, ...] = ()) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail
        self.block_ids = block_ids


@dataclass(frozen=True)
class SceneCoverageResult:
    manifest_digest: str
    plan_digest: str
    scene_digest: str
    discovered_object_coverage: str
    text_block_coverage: str
    table_cell_coverage: str
    chart_value_coverage: str
    image_occurrence_coverage: str
    unclassified_object_count: int
    unsupported_content_object_count: int
    order_violations: int
    fragment_gap_count: int
    fragment_overlap_count: int
    content_digest_mismatches: int
    render_clipping_count: int
    style_token_digest_matched: bool
    master_geometry_digest_matched: bool
    unplaced_block_ids: tuple[str, ...]


def _mapping(value: object, *, label: str) -> Mapping[str, Any]:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json", by_alias=True)  # type: ignore[union-attr]
    if not isinstance(value, Mapping):
        raise SourceContentCoverageError(
            "source_content_coverage_failed", detail=f"{label} must be an object"
        )
    return value


def _value(value: Mapping[str, Any], camel: str, snake: str | None = None, default: Any = None) -> Any:
    if camel in value:
        return value[camel]
    if snake and snake in value:
        return value[snake]
    return default


def _units(manifest: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = _value(manifest, "units", default=[])
    if not isinstance(raw, list):
        raise SourceContentCoverageError(
            "source_content_coverage_failed", detail="manifest units must be a list"
        )
    return [_mapping(item, label="manifest unit") for item in raw]


def _blocks(manifest: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    result: list[Mapping[str, Any]] = []
    for unit in _units(manifest):
        raw = _value(unit, "blocks", default=[])
        if not isinstance(raw, list):
            raise SourceContentCoverageError(
                "source_content_coverage_failed", detail="manifest blocks must be a list"
            )
        result.extend(_mapping(item, label="manifest block") for item in raw)
    ids = [str(_value(item, "sourceBlockId", "source_block_id", "")) for item in result]
    if not ids or any(not item for item in ids) or len(ids) != len(set(ids)):
        raise SourceContentCoverageError(
            "source_content_coverage_failed", detail="manifest block IDs must be non-empty and unique"
        )
    return result


def _placements(plan: Mapping[str, Any]) -> list[tuple[int, int, Mapping[str, Any]]]:
    slides = _value(plan, "slides", default=[])
    if not isinstance(slides, list):
        raise SourceContentCoverageError(
            "source_content_coverage_failed", detail="placement plan slides must be a list"
        )
    result: list[tuple[int, int, Mapping[str, Any]]] = []
    for slide_position, raw_slide in enumerate(slides):
        slide = _mapping(raw_slide, label="placement slide")
        output_index = int(_value(slide, "outputIndex", "output_index", slide_position))
        raw_placements = _value(slide, "placements", default=[])
        if not isinstance(raw_placements, list):
            raise SourceContentCoverageError(
                "source_content_coverage_failed", detail="slide placements must be a list"
            )
        for placement_index, raw_placement in enumerate(raw_placements):
            placement = _mapping(raw_placement, label="placement")
            if _value(placement, "derivedDuplicate", "derived_duplicate", False) is not True:
                result.append((output_index, placement_index, placement))
    return result


def _source_range(value: Mapping[str, Any]) -> tuple[str, int, int] | None:
    raw = _value(value, "sourceRange", "source_range")
    if raw is None:
        return None
    item = _mapping(raw, label="source range")
    kind = str(_value(item, "kind", default=""))
    start = _value(item, "start", default=None)
    end = _value(item, "endExclusive", "end_exclusive", None)
    if (
        not kind
        or isinstance(start, bool)
        or not isinstance(start, int)
        or isinstance(end, bool)
        or not isinstance(end, int)
        or start < 0
        or end <= start
    ):
        raise SourceContentCoverageError(
            "source_content_coverage_failed", detail="source range is invalid"
        )
    return kind, start, end


def _fragment_extent(block: Mapping[str, Any]) -> int | None:
    kind = str(_value(block, "kind", default=""))
    payload = _mapping(_value(block, "payload", default={}), label="block payload")
    if kind == "table":
        cells = _value(payload, "cells")
        return len(cells) if isinstance(cells, list) and cells else None
    if kind not in {"text", "source_note"}:
        return None
    runs = _value(payload, "runs")
    if isinstance(runs, list) and runs:
        return len(runs)
    text = _value(payload, "text")
    return len(text) if isinstance(text, str) and text else None


def _expected_fragment_text(block: Mapping[str, Any], source_range: tuple[str, int, int] | None) -> str | None:
    payload = _mapping(_value(block, "payload", default={}), label="block payload")
    text = _value(payload, "text")
    if not isinstance(text, str):
        return None
    if source_range is None:
        return text
    kind, start, end = source_range
    runs = _value(payload, "runs")
    if kind == "text-run-range" and isinstance(runs, list):
        values: list[str] = []
        for run in runs[start:end]:
            if isinstance(run, str):
                values.append(run)
            elif isinstance(run, Mapping) and isinstance(run.get("text"), str):
                values.append(str(run["text"]))
            else:
                raise SourceContentCoverageError(
                    "source_content_coverage_failed", detail="manifest text run is invalid"
                )
        return "".join(values)
    if kind == "text-character-range":
        return text[start:end]
    raise SourceContentCoverageError(
        "source_content_coverage_failed", detail="text fragment range kind is unsupported"
    )


def _scene_maps(scene: Mapping[str, Any]) -> tuple[dict[str, Mapping[str, Any]], dict[str, Mapping[str, Any]], dict[str, Mapping[str, Any]]]:
    elements: dict[str, Mapping[str, Any]] = {}
    for raw_slide in _value(scene, "slides", default=[]):
        slide = _mapping(raw_slide, label="scene slide")
        for raw_element in _value(slide, "elements", default=[]):
            element = _mapping(raw_element, label="scene element")
            element_id = str(_value(element, "elementId", "element_id", ""))
            if not element_id or element_id in elements:
                raise SourceContentCoverageError(
                    "source_content_coverage_failed", detail="scene element identity is invalid"
                )
            elements[element_id] = element
    data = {
        str(_value(item, "dataId", "data_id")): item
        for item in (
            _mapping(value, label="scene data manifest entry")
            for value in _value(scene, "dataManifest", "data_manifest", [])
        )
    }
    assets = {
        str(_value(item, "assetId", "asset_id")): item
        for item in (
            _mapping(value, label="scene asset manifest entry")
            for value in _value(scene, "assetManifest", "asset_manifest", [])
        )
    }
    return elements, data, assets


def _payload_count(block: Mapping[str, Any], kind: str) -> int:
    payload = _mapping(_value(block, "payload", default={}), label="block payload")
    if kind == "table":
        cells = _value(payload, "cells", default=[])
        return sum(len(row) for row in cells if isinstance(row, list)) if isinstance(cells, list) else 0
    if kind == "chart":
        series = _value(payload, "series", default=[])
        if not isinstance(series, list):
            return 0
        return sum(
            len(item.get("values", []))
            for item in series
            if isinstance(item, Mapping) and isinstance(item.get("values"), list)
        )
    return 1


def _expected_table_payload(
    payload: Mapping[str, Any], source_range: tuple[str, int, int]
) -> dict[str, Any]:
    _kind, start, end = source_range
    cells = _value(payload, "cells", default=[])
    result: dict[str, Any] = {"cells": cells[start:end]}
    for camel, snake in (
        ("columnWidthsPt", "column_widths_pt"),
        ("cellStyles", "cell_styles"),
        ("rowHeightsPt", "row_heights_pt"),
    ):
        values = _value(payload, camel, snake)
        if values is None:
            continue
        result[camel] = (
            values[start:end]
            if camel in {"cellStyles", "rowHeightsPt"} and isinstance(values, list)
            else values
        )
    merged = _value(payload, "mergedCells", "merged_cells")
    adjusted: list[dict[str, int]] = []
    if isinstance(merged, list):
        for raw in merged:
            item = _mapping(raw, label="merged table cell")
            row = int(_value(item, "row", default=0))
            row_span = int(_value(item, "rowSpan", "row_span", 0))
            if row + row_span <= start or row >= end:
                continue
            if row < start or row + row_span > end:
                raise SourceContentCoverageError(
                    "source_content_coverage_failed",
                    detail="table fragment cuts through a merged cell",
                )
            adjusted.append(
                {
                    "row": row - start,
                    "column": int(_value(item, "column", default=0)),
                    "rowSpan": row_span,
                    "columnSpan": int(_value(item, "columnSpan", "column_span", 0)),
                }
            )
    if adjusted:
        result["mergedCells"] = adjusted
    return result


def _composition_expected(plan: Mapping[str, Any]) -> tuple[str | None, str | None]:
    pin = _mapping(_value(plan, "compositionPin", "composition_pin", {}), label="composition pin")
    return (
        _value(pin, "authoredStyleDigest", "authored_style_digest"),
        _value(pin, "masterGeometryDigest", "master_geometry_digest"),
    )


def verify_scene_coverage(
    *,
    manifest: object,
    placement_plan: object,
    materialized_scene: object,
) -> SceneCoverageResult:
    """Verify inventory, placement partitions, exact payloads, and composition use."""

    manifest_map = _mapping(manifest, label="manifest")
    plan_map = _mapping(placement_plan, label="placement plan")
    envelope = _mapping(materialized_scene, label="materialized Scene")
    scene = _mapping(_value(envelope, "scene"), label="canonical Scene")
    inventory = _mapping(
        _value(manifest_map, "objectInventory", "object_inventory", {}),
        label="object inventory",
    )
    discovered = int(_value(inventory, "discoveredObjectCount", "discovered_object_count", -1))
    classified = int(_value(inventory, "classifiedObjectCount", "classified_object_count", -2))
    unclassified = tuple(_value(inventory, "unclassifiedObjectIds", "unclassified_object_ids", []) or [])
    unsupported = tuple(
        _value(inventory, "unsupportedContentObjectIds", "unsupported_content_object_ids", []) or []
    )
    if discovered < 0 or discovered != classified or unclassified or unsupported:
        raise SourceContentCoverageError(
            "source_content_coverage_failed",
            detail="source object inventory is not fully classified and supported",
            block_ids=tuple(str(item) for item in (*unclassified, *unsupported)),
        )

    blocks = _blocks(manifest_map)
    block_by_id = {
        str(_value(item, "sourceBlockId", "source_block_id")): item for item in blocks
    }
    placements = _placements(plan_map)
    by_block: dict[str, list[tuple[int, int, Mapping[str, Any]]]] = {}
    for output_index, placement_index, placement in placements:
        block_id = str(_value(placement, "sourceBlockId", "source_block_id", ""))
        if block_id not in block_by_id:
            raise SourceContentCoverageError(
                "source_content_coverage_failed", detail="placement references an unknown source block"
            )
        by_block.setdefault(block_id, []).append((output_index, placement_index, placement))

    missing = tuple(sorted(set(block_by_id) - set(by_block)))
    gaps = 0
    overlaps = 0
    for block_id, block in block_by_id.items():
        items = by_block.get(block_id, [])
        ranges = [_source_range(item[2]) for item in items]
        if any(item is not None for item in ranges):
            if any(item is None for item in ranges):
                gaps += 1
                continue
            if all(item is not None and item[0] == "atomic" for item in ranges):
                if len(ranges) != 1 or ranges[0] != ("atomic", 0, 1):
                    overlaps += 1
                continue
            extent = _fragment_extent(block)
            ordered = sorted((item[1], item[2]) for item in ranges if item is not None)
            cursor = 0
            for start, end in ordered:
                if start > cursor:
                    gaps += 1
                elif start < cursor:
                    overlaps += 1
                cursor = max(cursor, end)
            if extent is None or cursor < extent:
                gaps += 1
            elif cursor > extent:
                overlaps += 1
        elif len(items) != 1:
            overlaps += max(1, len(items) - 1)
    if missing or gaps or overlaps:
        raise SourceContentCoverageError(
            "source_content_coverage_failed",
            detail="source block placement has omissions, gaps, or overlaps",
            block_ids=missing,
        )

    ordered_block_ids = [
        str(_value(item, "sourceBlockId", "source_block_id")) for item in blocks
    ]
    output_order = [
        str(_value(item[2], "sourceBlockId", "source_block_id"))
        for item in sorted(placements, key=lambda value: (value[0], value[1]))
    ]
    first_positions = {block_id: output_order.index(block_id) for block_id in set(output_order)}
    order_violations = sum(
        first_positions[left] > first_positions[right]
        for left, right in zip(ordered_block_ids, ordered_block_ids[1:])
    )
    if order_violations:
        raise SourceContentCoverageError(
            "source_content_coverage_failed", detail="source block order changed"
        )

    bindings_raw = _value(envelope, "sourceBindings", "source_bindings", [])
    if not isinstance(bindings_raw, list):
        raise SourceContentCoverageError(
            "source_content_coverage_failed", detail="Scene sourceBindings must be a list"
        )
    bindings = [_mapping(item, label="Scene source binding") for item in bindings_raw]
    binding_keys = Counter(
        (
            str(_value(item, "sourceBlockId", "source_block_id", "")),
            canonical_digest(_value(item, "sourceRange", "source_range")),
        )
        for item in bindings
    )
    placement_keys = Counter(
        (
            str(_value(item[2], "sourceBlockId", "source_block_id", "")),
            canonical_digest(_value(item[2], "sourceRange", "source_range")),
        )
        for item in placements
    )
    if binding_keys != placement_keys:
        raise SourceContentCoverageError(
            "source_content_coverage_failed", detail="Scene bindings do not exactly match placement plan"
        )

    elements, data_entries, asset_entries = _scene_maps(scene)
    kind_counts: Counter[str] = Counter()
    count_values: Counter[str] = Counter()
    for binding in bindings:
        block_id = str(_value(binding, "sourceBlockId", "source_block_id"))
        block = block_by_id[block_id]
        kind = str(_value(block, "kind", default=""))
        expected_digest = str(_value(block, "contentDigest", "content_digest", ""))
        if _value(binding, "contentDigest", "content_digest") != expected_digest:
            raise SourceContentCoverageError(
                "source_content_coverage_failed",
                detail="Scene binding content digest differs from immutable manifest",
                block_ids=(block_id,),
            )
        element_id = str(_value(binding, "elementId", "element_id", ""))
        element = elements.get(element_id)
        rendered_as_fallback = _value(element, "kind") == "image" and kind != "image"
        scene_kind = "image" if rendered_as_fallback else ("text" if kind == "source_note" else kind)
        if element is None or _value(element, "kind") != scene_kind:
            raise SourceContentCoverageError(
                "source_content_coverage_failed",
                detail="Scene binding element is missing or has the wrong kind",
                block_ids=(block_id,),
            )
        if rendered_as_fallback:
            asset_id = str(_value(binding, "assetId", "asset_id", ""))
            entry = asset_entries.get(asset_id)
            fallback_asset_id = _value(block, "fallbackAssetId", "fallback_asset_id")
            if entry is None or not fallback_asset_id or asset_id != fallback_asset_id:
                raise SourceContentCoverageError(
                    "source_content_coverage_failed",
                    detail="Scene raster fallback is not bound to the sealed block asset",
                    block_ids=(block_id,),
                )
        elif kind in {"text", "source_note"}:
            expected_text = _expected_fragment_text(block, _source_range(binding))
            if expected_text is None or _value(element, "text") != expected_text:
                raise SourceContentCoverageError(
                    "source_content_coverage_failed",
                    detail="Scene text differs from immutable manifest",
                    block_ids=(block_id,),
                )
        elif kind in {"table", "chart"}:
            data_id = str(_value(binding, "dataId", "data_id", ""))
            entry = data_entries.get(data_id)
            payload = _mapping(_value(block, "payload", default={}), label="block payload")
            source_range = _source_range(binding)
            expected_payload: object = (
                {
                    key: value
                    for key in ("categories", "series", "style")
                    if (value := _value(payload, key)) is not None
                }
                if kind == "chart"
                else payload
            )
            if kind == "table" and source_range is not None and source_range[0] == "table-row-range":
                expected_payload = _expected_table_payload(payload, source_range)
            if entry is None or _value(entry, "payload") != expected_payload:
                raise SourceContentCoverageError(
                    "source_content_coverage_failed",
                    detail=f"Scene {kind} data differs from immutable manifest",
                    block_ids=(block_id,),
                )
        elif kind == "image":
            asset_id = str(_value(binding, "assetId", "asset_id", ""))
            entry = asset_entries.get(asset_id)
            payload = _mapping(_value(block, "payload", default={}), label="image payload")
            expected_asset_digest = _value(
                payload,
                "assetDigest",
                "asset_digest",
                _value(payload, "contentDigest", "content_digest", expected_digest),
            )
            if (
                entry is None
                or _value(entry, "assetDigest", "asset_digest") != expected_asset_digest
            ):
                raise SourceContentCoverageError(
                    "source_content_coverage_failed",
                    detail="Scene image occurrence differs from immutable manifest",
                    block_ids=(block_id,),
                )
        elif kind == "shape":
            payload = _mapping(
                _value(block, "payload", default={}), label="shape payload"
            )
            expected_shape = _value(
                payload, "preset", default=_value(payload, "shape")
            )
            if (
                expected_shape
                not in {
                    "rect",
                    "roundRect",
                    "ellipse",
                    "triangle",
                    "diamond",
                    "chevron",
                    "line",
                }
                or _value(element, "shape") != expected_shape
            ):
                raise SourceContentCoverageError(
                    "source_content_coverage_failed",
                    detail="Scene shape preset differs from immutable manifest",
                    block_ids=(block_id,),
                )
        else:
            raise SourceContentCoverageError(
                "source_content_coverage_failed",
                detail="manifest block kind is unsupported by Scene coverage",
                block_ids=(block_id,),
            )
        kind_counts[kind] += 1
        count_values[kind] += _payload_count(block, kind)

    validation = _mapping(
        _value(envelope, "sceneValidation", "scene_validation", {}),
        label="Scene validation evidence",
    )
    clipping = int(_value(validation, "clippingCount", "clipping_count", -1))
    overflow = int(_value(validation, "overflowCount", "overflow_count", -1))
    if clipping != 0 or overflow != 0:
        raise SourceContentCoverageError(
            "source_content_overflow",
            detail="staged Scene requires explicit zero clipping and overflow evidence",
        )

    composition = _mapping(
        _value(envelope, "compositionApplication", "composition_application", {}),
        label="composition application",
    )
    expected_style, expected_master = _composition_expected(plan_map)
    actual_style = _value(composition, "styleTokenDigest", "style_token_digest")
    actual_master = _value(composition, "masterGeometryDigest", "master_geometry_digest")
    style_match = (
        _value(composition, "styleTokenDigestApplied", "style_token_digest_applied") is True
        and isinstance(expected_style, str)
        and actual_style == expected_style
    )
    master_applied = _value(
        composition, "masterGeometryDigestApplied", "master_geometry_digest_applied"
    )
    master_match = (
        master_applied is True
        and ((expected_master is None and actual_master is None) or actual_master == expected_master)
    )
    if not style_match or not master_match:
        raise SourceContentCoverageError(
            "source_content_composition_not_applied",
            detail="selected style tokens or master geometry were not applied to Scene",
        )

    manifest_digest = str(_value(manifest_map, "manifestDigest", "manifest_digest", ""))
    plan_digest = str(_value(plan_map, "planDigest", "plan_digest", ""))
    scene_digest_value = str(_value(scene, "sceneDigest", "scene_digest", ""))
    counts = _mapping(_value(manifest_map, "counts", default={}), label="manifest counts")
    expected_text = int(
        _value(
            counts,
            "textBlocks",
            "text_blocks",
            sum(
                1
                for block in blocks
                if _value(block, "kind") in {"text", "source_note"}
            ),
        )
    )
    expected_table = int(_value(counts, "tableCells", "table_cells", sum(_payload_count(b, "table") for b in blocks if _value(b, "kind") == "table")))
    expected_chart = int(_value(counts, "chartValues", "chart_values", sum(_payload_count(b, "chart") for b in blocks if _value(b, "kind") == "chart")))
    expected_images = int(_value(counts, "imageOccurrences", "image_occurrences", sum(1 for b in blocks if _value(b, "kind") == "image")))
    actual_text = len(
        {
            str(_value(block, "sourceBlockId", "source_block_id"))
            for block in blocks
            if _value(block, "kind") in {"text", "source_note"}
            and str(_value(block, "sourceBlockId", "source_block_id")) in by_block
        }
    )
    actual_table = sum(_payload_count(b, "table") for b in blocks if _value(b, "kind") == "table" and str(_value(b, "sourceBlockId", "source_block_id")) in by_block)
    actual_chart = sum(_payload_count(b, "chart") for b in blocks if _value(b, "kind") == "chart" and str(_value(b, "sourceBlockId", "source_block_id")) in by_block)
    actual_images = sum(kind_counts["image"] for _ in [0])
    if (actual_text, actual_table, actual_chart, actual_images) != (
        expected_text,
        expected_table,
        expected_chart,
        expected_images,
    ):
        raise SourceContentCoverageError(
            "source_content_coverage_failed", detail="manifest count coverage is incomplete"
        )
    return SceneCoverageResult(
        manifest_digest=manifest_digest,
        plan_digest=plan_digest,
        scene_digest=scene_digest_value,
        discovered_object_coverage=f"{discovered}/{discovered}",
        text_block_coverage=f"{actual_text}/{expected_text}",
        table_cell_coverage=f"{actual_table}/{expected_table}",
        chart_value_coverage=f"{actual_chart}/{expected_chart}",
        image_occurrence_coverage=f"{actual_images}/{expected_images}",
        unclassified_object_count=0,
        unsupported_content_object_count=0,
        order_violations=0,
        fragment_gap_count=0,
        fragment_overlap_count=0,
        content_digest_mismatches=0,
        render_clipping_count=0,
        style_token_digest_matched=True,
        master_geometry_digest_matched=True,
        unplaced_block_ids=(),
    )


__all__ = [
    "SceneCoverageResult",
    "SourceContentCoverageError",
    "verify_scene_coverage",
]
