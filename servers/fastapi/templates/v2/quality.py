"""Deterministic quality inspection and explicit fix previews for Template V2.

This module deliberately has no provider, network, or persistence dependency.
It validates through the strict Template V2 fork while retaining the lossless
wire value as the source of truth.  Inspection never mutates a document, and
the small set of safe fixes can only be applied from a digest-bound preview
under an explicit revision compare-and-swap.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from typing import Any, Iterable, Mapping, Sequence

from .wire_codec import decode_wire_layouts


MIN_READABLE_FONT_SIZE = 9.0
MAX_DENSE_LAYOUT_LEAVES = 24
MAX_READABLE_TABLE_COLUMNS = 8
MIN_TEXT_CONTRAST = 4.5


class TemplateV2QualityError(ValueError):
    """Stable fail-closed contract error suitable for API translation."""


@dataclass(frozen=True, slots=True)
class QualityFinding:
    reason_code: str
    severity: str
    element_path: tuple[str | int, ...]
    details: tuple[tuple[str, str | int | float | bool], ...] = ()


@dataclass(frozen=True, slots=True)
class QualityInspection:
    source_digest: str
    findings: tuple[QualityFinding, ...]


@dataclass(frozen=True, slots=True)
class QualityPatch:
    reason_code: str
    path: tuple[str | int, ...]
    before: Any
    after: Any


@dataclass(frozen=True, slots=True)
class QualityFixPreview:
    preview_id: str
    source_digest: str
    patches: tuple[QualityPatch, ...]


@dataclass(frozen=True, slots=True)
class QualityApplyResult:
    layouts: dict[str, Any]
    revision: int
    preview_id: str


def _canonical_digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _details(**values: str | int | float | bool) -> tuple[
    tuple[str, str | int | float | bool], ...
]:
    return tuple(sorted(values.items()))


def _walk_elements(
    elements: Sequence[Mapping[str, Any]],
    path: tuple[str | int, ...],
) -> Iterable[tuple[tuple[str | int, ...], Mapping[str, Any]]]:
    for index, element in enumerate(elements):
        element_path = (*path, index)
        yield element_path, element
        element_type = element.get("type")
        if element_type in {"flex", "grid", "group"}:
            children = element.get("children")
            if isinstance(children, list):
                yield from _walk_elements(
                    children,
                    (*element_path, "children"),
                )
        elif element_type == "container":
            child = element.get("child")
            if isinstance(child, Mapping):
                yield from _walk_elements(
                    [child],
                    (*element_path, "child"),
                )


def _hex_rgb(value: object) -> tuple[int, int, int] | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if len(normalized) == 4 and normalized.startswith("#"):
        normalized = "#" + "".join(character * 2 for character in normalized[1:])
    if len(normalized) != 7 or not normalized.startswith("#"):
        return None
    try:
        return tuple(
            int(normalized[offset : offset + 2], 16)
            for offset in (1, 3, 5)
        )
    except ValueError:
        return None


def _relative_luminance(rgb: tuple[int, int, int]) -> float:
    channels = []
    for channel in rgb:
        value = channel / 255
        channels.append(
            value / 12.92
            if value <= 0.04045
            else ((value + 0.055) / 1.055) ** 2.4
        )
    red, green, blue = channels
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _contrast_ratio(foreground: object, background: object) -> float | None:
    foreground_rgb = _hex_rgb(foreground)
    background_rgb = _hex_rgb(background)
    if foreground_rgb is None or background_rgb is None:
        return None
    lighter, darker = sorted(
        (
            _relative_luminance(foreground_rgb),
            _relative_luminance(background_rgb),
        ),
        reverse=True,
    )
    return (lighter + 0.05) / (darker + 0.05)


def _text_runs(element: Mapping[str, Any]) -> Iterable[
    tuple[tuple[str | int, ...], Mapping[str, Any]]
]:
    element_font = element.get("font")
    if isinstance(element_font, Mapping):
        yield ("font",), element_font
    runs = element.get("runs")
    if isinstance(runs, list):
        for index, run in enumerate(runs):
            if isinstance(run, Mapping) and isinstance(run.get("font"), Mapping):
                yield ("runs", index, "font"), run["font"]
    items = element.get("items")
    if isinstance(items, list):
        for item_index, item in enumerate(items):
            if not isinstance(item, list):
                continue
            for run_index, run in enumerate(item):
                if isinstance(run, Mapping) and isinstance(
                    run.get("font"), Mapping
                ):
                    yield (
                        "items",
                        item_index,
                        run_index,
                        "font",
                    ), run["font"]
    for collection_name in ("columns", "rows"):
        collection = element.get(collection_name)
        if not isinstance(collection, list):
            continue
        cell_rows = [collection] if collection_name == "columns" else collection
        for row_index, row in enumerate(cell_rows):
            if not isinstance(row, list):
                continue
            for cell_index, cell in enumerate(row):
                if not isinstance(cell, Mapping):
                    continue
                cell_prefix: tuple[str | int, ...]
                if collection_name == "columns":
                    cell_prefix = ("columns", cell_index)
                else:
                    cell_prefix = ("rows", row_index, cell_index)
                cell_font = cell.get("font")
                if isinstance(cell_font, Mapping):
                    yield (*cell_prefix, "font"), cell_font
                cell_runs = cell.get("runs")
                if not isinstance(cell_runs, list):
                    continue
                for run_index, run in enumerate(cell_runs):
                    if isinstance(run, Mapping) and isinstance(
                        run.get("font"), Mapping
                    ):
                        yield (
                            *cell_prefix,
                            "runs",
                            run_index,
                            "font",
                        ), run["font"]


def _text_content(element: Mapping[str, Any]) -> str:
    runs = element.get("runs")
    if isinstance(runs, list):
        return "".join(
            str(run.get("text") or "")
            for run in runs
            if isinstance(run, Mapping)
        )
    items = element.get("items")
    if isinstance(items, list):
        return "\n".join(
            "".join(
                str(run.get("text") or "")
                for run in item
                if isinstance(run, Mapping)
            )
            for item in items
            if isinstance(item, list)
        )
    return ""


def _inspect_element(
    element: Mapping[str, Any],
    path: tuple[str | int, ...],
) -> list[QualityFinding]:
    findings: list[QualityFinding] = []
    element_type = element.get("type")

    if element_type in {"text", "text-list", "table"}:
        for font_path, font in _text_runs(element):
            size = font.get("size")
            if (
                isinstance(size, (int, float))
                and not isinstance(size, bool)
                and size < MIN_READABLE_FONT_SIZE
            ):
                findings.append(
                    QualityFinding(
                        reason_code="TEXT_BELOW_9PT",
                        severity="warning",
                        element_path=(*path, *font_path, "size"),
                        details=_details(
                            actual=round(float(size), 3),
                            minimum=MIN_READABLE_FONT_SIZE,
                        ),
                    )
                )

    if element_type == "text":
        text = _text_content(element)
        max_length = element.get("max_length")
        explicit_overflow = element.get("overflow") is True
        text_fit = element.get("text_fit")
        if isinstance(text_fit, Mapping):
            explicit_overflow = (
                explicit_overflow or text_fit.get("overflow") is True
            )
        if explicit_overflow or (
            isinstance(max_length, int) and len(text) > max_length
        ):
            findings.append(
                QualityFinding(
                    reason_code="TEXT_OVERFLOW",
                    severity="error",
                    element_path=path,
                    details=_details(
                        actual_length=len(text),
                        maximum=max_length if isinstance(max_length, int) else -1,
                    ),
                )
            )

        background = element.get("background_color")
        fill = element.get("fill")
        if background is None and isinstance(fill, Mapping):
            background = fill.get("color")
        for font_path, font in _text_runs(element):
            foreground = font.get("color")
            ratio = _contrast_ratio(foreground, background)
            if ratio is not None and ratio < MIN_TEXT_CONTRAST:
                findings.append(
                    QualityFinding(
                        reason_code="TEXT_LOW_CONTRAST",
                        severity="warning",
                        element_path=(*path, *font_path, "color"),
                        details=_details(
                            background=str(background),
                            ratio=round(ratio, 3),
                            required=MIN_TEXT_CONTRAST,
                        ),
                    )
                )

    if element_type == "chart":
        series = element.get("series")
        if isinstance(series, list) and len(series) > 1 and (
            element.get("legend") is not True
        ):
            findings.append(
                QualityFinding(
                    reason_code="CHART_LEGEND_MISSING",
                    severity="warning",
                    element_path=path,
                    details=_details(series_count=len(series)),
                )
            )
        has_values = isinstance(series, list) and any(
            isinstance(item, Mapping) and bool(item.get("values"))
            for item in series
        )
        if has_values and not str(element.get("y_axis_title") or "").strip():
            findings.append(
                QualityFinding(
                    reason_code="CHART_UNIT_UNSPECIFIED",
                    severity="info",
                    element_path=path,
                )
            )

    if element_type == "table":
        columns = element.get("columns")
        if (
            isinstance(columns, list)
            and len(columns) > MAX_READABLE_TABLE_COLUMNS
        ):
            findings.append(
                QualityFinding(
                    reason_code="TABLE_TOO_MANY_COLUMNS",
                    severity="warning",
                    element_path=path,
                    details=_details(
                        actual=len(columns),
                        recommended_maximum=MAX_READABLE_TABLE_COLUMNS,
                    ),
                )
            )

    compatibility = element.get("compatibility")
    unsupported_reason = element.get("unsupported_reason")
    if isinstance(compatibility, Mapping):
        unsupported_reason = (
            unsupported_reason or compatibility.get("unsupported_reason")
        )
    if unsupported_reason:
        findings.append(
            QualityFinding(
                reason_code="ELEMENT_UNSUPPORTED",
                severity="error",
                element_path=path,
                details=_details(reason=str(unsupported_reason)),
            )
        )

    raster_only = element.get("raster_only") is True
    if isinstance(compatibility, Mapping):
        raster_only = raster_only or compatibility.get("render_mode") in {
            "raster",
            "raster-only",
        }
    if raster_only:
        findings.append(
            QualityFinding(
                reason_code="ELEMENT_RASTER_ONLY",
                severity="warning",
                element_path=path,
            )
        )

    return findings


def inspect_template_v2_quality(
    layouts: Mapping[str, Any],
) -> QualityInspection:
    """Return stable findings without changing or projecting the input."""

    wire = decode_wire_layouts(layouts)
    wire.validate_strict()
    source = wire.to_wire_value()
    findings: list[QualityFinding] = []

    for layout_index, layout in enumerate(source["layouts"]):
        leaf_count = 0
        components = layout.get("components")
        if not isinstance(components, list):
            continue
        for component_index, component in enumerate(components):
            elements = component.get("elements")
            if not isinstance(elements, list):
                continue
            root_path = (
                "layouts",
                layout_index,
                "components",
                component_index,
                "elements",
            )
            for path, element in _walk_elements(elements, root_path):
                if element.get("type") not in {
                    "container",
                    "flex",
                    "grid",
                    "group",
                }:
                    leaf_count += 1
                findings.extend(_inspect_element(element, path))
        if leaf_count > MAX_DENSE_LAYOUT_LEAVES:
            findings.append(
                QualityFinding(
                    reason_code="SLIDE_OVERDENSE",
                    severity="warning",
                    element_path=("layouts", layout_index),
                    details=_details(
                        actual=leaf_count,
                        recommended_maximum=MAX_DENSE_LAYOUT_LEAVES,
                    ),
                )
            )

    findings.sort(
        key=lambda finding: (
            tuple(str(part) for part in finding.element_path),
            finding.reason_code,
        )
    )
    return QualityInspection(
        source_digest=_canonical_digest(source),
        findings=tuple(findings),
    )


def _read_path(source: Mapping[str, Any], path: Sequence[str | int]) -> Any:
    value: Any = source
    for part in path:
        if isinstance(part, int):
            if not isinstance(value, list) or part >= len(value):
                raise TemplateV2QualityError("template_v2_quality_preview_invalid")
            value = value[part]
        else:
            if not isinstance(value, Mapping) or part not in value:
                raise TemplateV2QualityError("template_v2_quality_preview_invalid")
            value = value[part]
    return value


def _high_contrast_color(background: object) -> str | None:
    black_ratio = _contrast_ratio("#000000", background)
    white_ratio = _contrast_ratio("#FFFFFF", background)
    if black_ratio is None or white_ratio is None:
        return None
    return "#000000" if black_ratio >= white_ratio else "#FFFFFF"


def preview_template_v2_quality_fixes(
    layouts: Mapping[str, Any],
    inspection: QualityInspection,
) -> QualityFixPreview:
    """Build a non-mutating, digest-bound preview of safe deterministic fixes."""

    wire = decode_wire_layouts(layouts)
    wire.validate_strict()
    source = wire.to_wire_value()
    if _canonical_digest(source) != inspection.source_digest:
        raise TemplateV2QualityError("template_v2_quality_inspection_stale")

    patches: list[QualityPatch] = []
    for finding in inspection.findings:
        if finding.reason_code == "TEXT_BELOW_9PT":
            before = _read_path(source, finding.element_path)
            patches.append(
                QualityPatch(
                    reason_code=finding.reason_code,
                    path=finding.element_path,
                    before=before,
                    after=MIN_READABLE_FONT_SIZE,
                )
            )
        elif finding.reason_code == "CHART_LEGEND_MISSING":
            path = (*finding.element_path, "legend")
            element = _read_path(source, finding.element_path)
            if not isinstance(element, Mapping):
                raise TemplateV2QualityError(
                    "template_v2_quality_preview_invalid"
                )
            patches.append(
                QualityPatch(
                    reason_code=finding.reason_code,
                    path=path,
                    before=element.get("legend"),
                    after=True,
                )
            )
        elif finding.reason_code == "TEXT_LOW_CONTRAST":
            before = _read_path(source, finding.element_path)
            background = dict(finding.details).get("background")
            after = _high_contrast_color(background)
            if after is not None and after != before:
                patches.append(
                    QualityPatch(
                        reason_code=finding.reason_code,
                        path=finding.element_path,
                        before=before,
                        after=after,
                    )
                )

    patch_payload = [
        {
            "reason_code": patch.reason_code,
            "path": list(patch.path),
            "before": patch.before,
            "after": patch.after,
        }
        for patch in patches
    ]
    preview_id = hashlib.sha256(
        json.dumps(
            {
                "source_digest": inspection.source_digest,
                "patches": patch_payload,
            },
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return QualityFixPreview(
        preview_id=preview_id,
        source_digest=inspection.source_digest,
        patches=tuple(patches),
    )


def _write_path(
    source: dict[str, Any],
    path: Sequence[str | int],
    *,
    before: Any,
    after: Any,
) -> None:
    if not path:
        raise TemplateV2QualityError("template_v2_quality_preview_invalid")
    parent: Any = source
    for part in path[:-1]:
        if isinstance(part, int):
            if not isinstance(parent, list) or part >= len(parent):
                raise TemplateV2QualityError(
                    "template_v2_quality_preview_invalid"
                )
            parent = parent[part]
        else:
            if not isinstance(parent, Mapping) or part not in parent:
                raise TemplateV2QualityError(
                    "template_v2_quality_preview_invalid"
                )
            parent = parent[part]
    field = path[-1]
    if isinstance(field, int):
        if not isinstance(parent, list) or field >= len(parent):
            raise TemplateV2QualityError("template_v2_quality_preview_invalid")
        current = parent[field]
        if current != before:
            raise TemplateV2QualityError("template_v2_quality_preview_tampered")
        parent[field] = deepcopy(after)
        return
    if not isinstance(parent, dict):
        raise TemplateV2QualityError("template_v2_quality_preview_invalid")
    current = parent.get(field)
    if current != before:
        raise TemplateV2QualityError("template_v2_quality_preview_tampered")
    parent[field] = deepcopy(after)


def apply_template_v2_quality_preview(
    layouts: Mapping[str, Any],
    preview: QualityFixPreview,
    *,
    expected_revision: int,
    current_revision: int,
) -> QualityApplyResult:
    """Apply only the reviewed patch list under a revision and digest CAS."""

    if (
        isinstance(current_revision, bool)
        or not isinstance(current_revision, int)
        or current_revision < 1
    ):
        raise TemplateV2QualityError("template_v2_quality_revision_invalid")
    if expected_revision != current_revision:
        raise TemplateV2QualityError("template_v2_quality_stale_revision")
    if not preview.patches:
        raise TemplateV2QualityError("template_v2_quality_no_safe_fixes")

    wire = decode_wire_layouts(layouts)
    wire.validate_strict()
    source = wire.to_wire_value()
    if _canonical_digest(source) != preview.source_digest:
        raise TemplateV2QualityError("template_v2_quality_preview_stale")

    result = deepcopy(source)
    for patch in preview.patches:
        _write_path(
            result,
            patch.path,
            before=patch.before,
            after=patch.after,
        )

    # Re-enter strict validation after applying the bounded patch set.  The
    # returned value remains the lossless JSON clone, including all extensions.
    decode_wire_layouts(result).validate_strict()
    return QualityApplyResult(
        layouts=result,
        revision=current_revision + 1,
        preview_id=preview.preview_id,
    )


__all__ = [
    "QualityApplyResult",
    "QualityFinding",
    "QualityFixPreview",
    "QualityInspection",
    "QualityPatch",
    "TemplateV2QualityError",
    "apply_template_v2_quality_preview",
    "inspect_template_v2_quality",
    "preview_template_v2_quality_fixes",
]
