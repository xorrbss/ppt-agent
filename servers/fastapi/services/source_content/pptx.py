"""Deterministic, network-free PPTX object inventory and extraction."""

from __future__ import annotations

import base64
from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
import math
import mimetypes
import posixpath
from pathlib import Path, PurePosixPath
import re
from typing import Any
from zipfile import ZipFile

from lxml import etree

from services.source_content.contracts import (
    SOURCE_LAYOUT_VERSION_V2,
    SourceContentBlock,
    SourceContentCounts,
    SourceContentManifest,
    SourceContentUnit,
    SourceInventoryObject,
    SourceObjectInventorySummary,
)
from services.source_content.digests import digest_bytes, digest_json
from services.source_content.errors import (
    MANIFEST_INVALID,
    UNSUPPORTED_OBJECT,
    SourceContentError,
)
from services.source_content.pptx_document_identity import (
    extract_document_title,
    title_from_file_name,
)
from services.source_content.pptx_text_body import (
    BulletImageResolver,
    extract_text_body,
    placeholder_payload,
)
from services.source_content.pptx_typography import MANIFEST_ID_EXTRACTOR_REVISION, PptxTypographyResolver
from services.source_content.security import (
    REL_NS,
    PackageSecurityReport,
    admit_pptx_package,
    secure_xml,
)


P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
C_NS = "http://schemas.openxmlformats.org/drawingml/2006/chart"
NS = {"p": P_NS, "a": A_NS, "r": R_NS, "c": C_NS}

_SLIDE_RE = re.compile(r"^ppt/slides/slide(\d+)\.xml$")
_NOTES_RE = re.compile(r"^ppt/notesSlides/notesSlide(\d+)\.xml$")
_SUPPORTED_REL_SUFFIXES = {
    "/officeDocument",
    "/presentation",
    "/slide",
    "/slideMaster",
    "/slideLayout",
    "/handoutMaster",
    "/theme",
    "/themeOverride",
    "/image",
    "/chart",
    "/notesSlide",
    "/notesMaster",
    "/comments",
    "/commentAuthors",
    "/hyperlink",
    "/tableStyles",
    "/viewProps",
    "/presProps",
    "/extended-properties",
    "/core-properties",
    "/custom-properties",
    "/thumbnail",
    "/customXml",
    "/customXmlProps",
    "/printerSettings",
    "/revisionInfo",
    "/font",
    "/tags",
    "/audio",
    "/video",
    "/media",
    "/package",
}
_UNSUPPORTED_CONTENT_REL_SUFFIXES = {
    "/diagramData",
    "/diagramLayout",
    "/diagramQuickStyle",
    "/diagramColors",
    "/diagramDrawing",
    "/vmlDrawing",
}


@dataclass(frozen=True, slots=True)
class SealedManifestAsset:
    asset_id: str
    media_type: str
    content_digest: str
    payload: bytes


@dataclass(frozen=True, slots=True)
class ExtractedSourceContent:
    manifest: SourceContentManifest
    assets: tuple[SealedManifestAsset, ...]


def _relationship_source_part(rel_part: str) -> str:
    if rel_part == "_rels/.rels":
        return ""
    prefix, filename = rel_part.rsplit("/_rels/", 1)
    if not filename.endswith(".rels"):
        return ""
    return f"{prefix}/{filename[:-5]}"


def _resolve_target(source_part: str, target: str) -> str:
    target = target.replace("\\", "/")
    if target.startswith("/"):
        resolved = target.lstrip("/")
    else:
        resolved = posixpath.normpath(posixpath.join(posixpath.dirname(source_part), target))
    if resolved.startswith("../") or resolved in {"", ".", ".."}:
        raise SourceContentError(
            MANIFEST_INVALID,
            detail={"reason": "relationship_target_invalid"},
        )
    return str(PurePosixPath(resolved))


def _relationship_map(package: ZipFile, source_part: str) -> dict[str, tuple[str, str]]:
    if "/" in source_part:
        directory, filename = source_part.rsplit("/", 1)
        rel_part = f"{directory}/_rels/{filename}.rels"
    else:
        rel_part = f"_rels/{source_part}.rels"
    if rel_part not in package.namelist():
        return {}
    root = secure_xml(package.read(rel_part), part_name=rel_part)
    result: dict[str, tuple[str, str]] = {}
    for rel in root.findall(f"{{{REL_NS}}}Relationship"):
        rel_id = rel.get("Id")
        target = rel.get("Target")
        if not rel_id or not target:
            raise SourceContentError(
                MANIFEST_INVALID,
                detail={"reason": "relationship_missing_identity", "part": rel_part},
            )
        result[rel_id] = (_resolve_target(source_part, target), rel.get("Type", ""))
    return result


def _ordered_slide_parts(
    package: ZipFile, report: PackageSecurityReport
) -> list[str]:
    presentation_part = "ppt/presentation.xml"
    if presentation_part not in report.entries:
        raise SourceContentError(
            MANIFEST_INVALID,
            detail={"reason": "presentation_part_missing"},
        )
    root = secure_xml(package.read(presentation_part), part_name=presentation_part)
    relationships = _relationship_map(package, presentation_part)
    slide_entries = {
        name for name in report.entries if _SLIDE_RE.match(name)
    }
    ordered: list[str] = []
    for slide_id in root.findall(".//p:sldId", namespaces=NS):
        rel_id = slide_id.get(f"{{{R_NS}}}id")
        relation = relationships.get(rel_id or "")
        if (
            relation is None
            or not relation[1].endswith("/slide")
            or relation[0] not in slide_entries
        ):
            raise SourceContentError(
                MANIFEST_INVALID,
                detail={"reason": "slide_relationship_invalid"},
            )
        ordered.append(relation[0])
    if len(ordered) != len(slide_entries) or set(ordered) != slide_entries:
        raise SourceContentError(
            MANIFEST_INVALID,
            detail={"reason": "slide_order_invalid"},
        )
    return ordered


def _part_classification(name: str) -> tuple[str, bool]:
    lowered = name.lower()
    if _SLIDE_RE.match(name) or _NOTES_RE.match(name):
        return "source_unit", True
    if lowered.startswith("ppt/media/"):
        return "content_asset", True
    if lowered.startswith("ppt/charts/") or lowered.startswith("ppt/embeddings/"):
        return "content_data", True
    if lowered.startswith(("ppt/slidemasters/", "ppt/slidelayouts/", "ppt/theme/")):
        return "source_decorative", False
    if lowered.endswith(".rels"):
        return "relationship_container", False
    if lowered in {"[content_types].xml", "ppt/presentation.xml"}:
        return "package_structure", False
    if lowered.startswith(
        (
            "docprops/",
            "ppt/notesmasters/",
            "ppt/handoutmasters/",
            "ppt/presprops.xml",
            "ppt/revisioninfo.xml",
            "ppt/viewprops.xml",
            "ppt/tablestyles.xml",
            "ppt/commentauthors.xml",
            "ppt/comments/",
            "ppt/printersettings/",
            "ppt/tags/",
            "customxml/",
        )
    ):
        return "package_metadata", False
    return "unclassified", False


def _rel_classification(rel_type: str) -> tuple[str, bool, bool]:
    if any(rel_type.endswith(suffix) for suffix in _SUPPORTED_REL_SUFFIXES):
        content = any(
            rel_type.endswith(suffix)
            for suffix in ("/image", "/chart", "/audio", "/video", "/media", "/notesSlide")
        )
        return "supported_relationship", content, True
    if any(rel_type.endswith(suffix) for suffix in _UNSUPPORTED_CONTENT_REL_SUFFIXES):
        return "unsupported_content_relationship", True, False
    return "unclassified", False, False


def _shape_identity(element: etree._Element, fallback: int) -> tuple[str, str]:
    c_nv_pr = element.find(".//p:cNvPr", namespaces=NS)
    if c_nv_pr is None:
        return str(fallback), ""
    return c_nv_pr.get("id", str(fallback)), c_nv_pr.get("name", "")


AffineTransform = tuple[float, float, float, float, float, float]
IDENTITY_TRANSFORM: AffineTransform = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _compose_affine(
    outer: AffineTransform, inner: AffineTransform
) -> AffineTransform:
    oa, ob, oc, od, oe, of = outer
    ia, ib, ic, id_, ie, if_ = inner
    return (
        oa * ia + oc * ib,
        ob * ia + od * ib,
        oa * ic + oc * id_,
        ob * ic + od * id_,
        oa * ie + oc * if_ + oe,
        ob * ie + od * if_ + of,
    )


def _apply_affine(transform: AffineTransform, x: float, y: float) -> tuple[float, float]:
    a, b, c, d, e, f = transform
    return a * x + c * y + e, b * x + d * y + f


def _group_child_transform(element: etree._Element) -> AffineTransform:
    xfrm = element.find("./p:grpSpPr/a:xfrm", namespaces=NS)
    if xfrm is None:
        raise SourceContentError(
            UNSUPPORTED_OBJECT,
            detail={"reason": "invalid_group_coordinate_space"},
        )
    off = xfrm.find("a:off", namespaces=NS)
    ext = xfrm.find("a:ext", namespaces=NS)
    child_off = xfrm.find("a:chOff", namespaces=NS)
    child_ext = xfrm.find("a:chExt", namespaces=NS)
    if ext is None or child_ext is None:
        raise SourceContentError(
            UNSUPPORTED_OBJECT,
            detail={"reason": "invalid_group_coordinate_space"},
        )
    width = float(ext.get("cx", "0"))
    height = float(ext.get("cy", "0"))
    child_width = float(child_ext.get("cx", "0"))
    child_height = float(child_ext.get("cy", "0"))
    if width <= 0 or height <= 0 or child_width <= 0 or child_height <= 0:
        raise SourceContentError(
            UNSUPPORTED_OBJECT,
            detail={"reason": "invalid_group_coordinate_space"},
        )
    x = float(off.get("x", "0")) if off is not None else 0.0
    y = float(off.get("y", "0")) if off is not None else 0.0
    child_x = float(child_off.get("x", "0")) if child_off is not None else 0.0
    child_y = float(child_off.get("y", "0")) if child_off is not None else 0.0
    scale_x = width / child_width
    scale_y = height / child_height
    base: AffineTransform = (
        scale_x,
        0.0,
        0.0,
        scale_y,
        x - child_x * scale_x,
        y - child_y * scale_y,
    )
    center_x = x + width / 2.0
    center_y = y + height / 2.0
    angle = math.radians(float(xfrm.get("rot", "0")) / 60_000.0)
    flip_x = -1.0 if xfrm.get("flipH", "0").lower() in {"1", "true", "on"} else 1.0
    flip_y = -1.0 if xfrm.get("flipV", "0").lower() in {"1", "true", "on"} else 1.0
    cos_angle = math.cos(angle)
    sin_angle = math.sin(angle)
    around_center: AffineTransform = (
        cos_angle * flip_x,
        sin_angle * flip_x,
        -sin_angle * flip_y,
        cos_angle * flip_y,
        center_x - cos_angle * flip_x * center_x + sin_angle * flip_y * center_y,
        center_y - sin_angle * flip_x * center_x - cos_angle * flip_y * center_y,
    )
    return _compose_affine(around_center, base)


def _geometry(
    element: etree._Element,
    *,
    parent_transform: AffineTransform = IDENTITY_TRANSFORM,
    inherited_shapes: tuple[tuple[str, etree._Element], ...] = (),
    local_source: str = "slide",
    source_block_id: str | None = None,
) -> dict[str, Any]:
    def transform_of(shape: etree._Element) -> etree._Element | None:
        transform = shape.find("./p:spPr/a:xfrm", namespaces=NS)
        return transform if transform is not None else shape.find("./p:xfrm", namespaces=NS)

    candidates = [(local_source, element), *inherited_shapes]
    transforms = [
        (source, transform)
        for source, shape in candidates
        if (transform := transform_of(shape)) is not None
    ]
    if not transforms:
        return {}

    def component(node_name: str, attribute: str) -> tuple[float, str] | None:
        for source, transform in transforms:
            node = transform.find(f"a:{node_name}", namespaces=NS)
            if node is None or node.get(attribute) is None:
                continue
            try:
                return float(node.get(attribute, "0")), source
            except ValueError:
                return None
        return None

    resolved = {
        "x": component("off", "x"),
        "y": component("off", "y"),
        "cx": component("ext", "cx"),
        "cy": component("ext", "cy"),
    }
    if any(value is None for value in resolved.values()):
        return {}
    x, source_x = resolved["x"]  # type: ignore[misc]
    y, source_y = resolved["y"]  # type: ignore[misc]
    width, source_width = resolved["cx"]  # type: ignore[misc]
    height, source_height = resolved["cy"]  # type: ignore[misc]
    corners = (
        _apply_affine(parent_transform, x, y),
        _apply_affine(parent_transform, x + width, y),
        _apply_affine(parent_transform, x, y + height),
        _apply_affine(parent_transform, x + width, y + height),
    )
    left = min(point[0] for point in corners)
    top = min(point[1] for point in corners)
    right = max(point[0] for point in corners)
    bottom = max(point[1] for point in corners)
    sources = {
        "x": source_x,
        "y": source_y,
        "cx": source_width,
        "cy": source_height,
    }
    geometry: dict[str, Any] = {
        "x": round(left),
        "y": round(top),
        "cx": round(right - left),
        "cy": round(bottom - top),
        "source": next(iter(set(sources.values()))) if len(set(sources.values())) == 1 else "mixed",
        "sources": sources,
    }
    if source_block_id is not None:
        geometry["sourceBlockId"] = source_block_id
    for attribute, key, divisor in (
        ("rot", "rotationDegrees", 60_000.0),
        ("flipH", "flipH", None),
        ("flipV", "flipV", None),
    ):
        for source, transform in transforms:
            value = transform.get(attribute)
            if value is None:
                continue
            if divisor is not None:
                try:
                    geometry[key] = float(value) / divisor
                except ValueError:
                    pass
            else:
                geometry[key] = value.lower() in {"1", "true", "on"}
            geometry["sources"][key] = source
            break
    return geometry


def _image_crop(element: etree._Element) -> dict[str, float]:
    source_rect = element.find(".//p:blipFill/a:srcRect", namespaces=NS)

    def fraction(attribute: str) -> float:
        if source_rect is None:
            return 0.0
        try:
            return int(source_rect.get(attribute, "0")) / 100_000.0
        except ValueError:
            return 0.0

    return {
        "left": fraction("l"),
        "top": fraction("t"),
        "right": fraction("r"),
        "bottom": fraction("b"),
    }


def _image_color_transforms(element: etree._Element) -> list[dict[str, Any]]:
    """Supported occurrence-local ``a:blip`` effects in source order."""

    def unsupported(reason: str) -> SourceContentError:
        return SourceContentError(
            UNSUPPORTED_OBJECT,
            detail={"reason": "unsupported_image_color_transform", "detail": reason},
        )

    def literal_color(container: etree._Element | None) -> tuple[str, float]:
        if container is None or len(container) != 1:
            raise unsupported("color_container_invalid")
        color = container[0]
        if etree.QName(color).localname != "srgbClr":
            raise unsupported("color_model_not_srgb")
        value = (color.get("val") or "").strip()
        if not re.fullmatch(r"[0-9A-Fa-f]{6}", value):
            raise unsupported("srgb_value_invalid")
        alpha = 1.0
        for transform in color:
            if etree.QName(transform).localname != "alpha":
                raise unsupported("nested_color_transform_unsupported")
            try:
                raw_alpha = int(transform.get("val", ""))
            except ValueError as exc:
                raise unsupported("alpha_value_invalid") from exc
            if not 0 <= raw_alpha <= 100_000:
                raise unsupported("alpha_value_out_of_range")
            alpha = raw_alpha / 100_000.0
        return f"#{value.upper()}", alpha

    def fixed_percentage(effect: etree._Element, attribute: str) -> float:
        raw = effect.get(attribute, "0").strip()
        try:
            value = float(raw[:-1]) / 100.0 if raw.endswith("%") else int(raw) / 100_000.0
        except ValueError as exc:
            raise unsupported(f"{attribute}_value_invalid") from exc
        if not -1.0 <= value <= 1.0:
            raise unsupported(f"{attribute}_value_out_of_range")
        return value

    blip = element.find(".//a:blip", namespaces=NS)
    if blip is None:
        return []
    result: list[dict[str, Any]] = []
    for effect in blip:
        effect_name = etree.QName(effect).localname
        if effect_name == "extLst":
            continue
        if effect_name == "clrChange":
            raw_use_alpha = effect.get("useA")
            if raw_use_alpha is None or raw_use_alpha in {"1", "true"}:
                use_alpha = True
            elif raw_use_alpha in {"0", "false"}:
                use_alpha = False
            else:
                raise unsupported("use_alpha_invalid")
            from_color, from_alpha = literal_color(
                effect.find("a:clrFrom", namespaces=NS)
            )
            to_color, to_alpha = literal_color(effect.find("a:clrTo", namespaces=NS))
            result.append(
                {
                    "kind": "colorChange",
                    "fromColor": from_color,
                    "fromAlpha": from_alpha,
                    "toColor": to_color,
                    "toAlpha": to_alpha,
                    "useAlpha": use_alpha,
                }
            )
            continue
        if effect_name == "lum":
            if len(effect):
                raise unsupported("luminance_children_unsupported")
            result.append(
                {
                    "kind": "luminance",
                    "brightness": fixed_percentage(effect, "bright"),
                    "contrast": fixed_percentage(effect, "contrast"),
                }
            )
            continue
        raise unsupported(f"effect_{effect_name}_unsupported")
    return result


def _block(
    *,
    block_id: str,
    kind: str,
    role: str,
    order: int,
    payload: dict[str, Any],
    native_candidate: bool = True,
) -> SourceContentBlock:
    projection = None
    if kind in {"text", "source_note"}:
        projection = {"unicodeForm": "NFC", "lineEnding": "LF"}
    return SourceContentBlock(
        source_block_id=block_id,
        kind=kind,
        semantic_role=role,
        order=order,
        payload=payload,
        comparison_projection=projection,
        content_digest=digest_json(payload),
        native_candidate=native_candidate,
    )


def _extract_table(
    element: etree._Element,
    *,
    source_block_id: str,
    bullet_image_resolver: BulletImageResolver,
) -> tuple[dict[str, Any], int, int]:
    rows: list[list[dict[str, Any]]] = []
    merged_cells: list[dict[str, int]] = []
    count = 0
    picture_bullet_count = 0
    column_widths = [
        int(column.get("w", "0"))
        for column in element.findall(".//a:tbl/a:tblGrid/a:gridCol", namespaces=NS)
    ]
    row_heights: list[int] = []
    for row_index, row in enumerate(element.findall(".//a:tbl/a:tr", namespaces=NS)):
        row_heights.append(int(row.get("h", "0")))
        values: list[dict[str, Any]] = []
        for column_index, cell in enumerate(row.findall("a:tc", namespaces=NS)):
            body = extract_text_body(
                cell, bullet_image_resolver=bullet_image_resolver
            )
            picture_bullet_count += body.picture_bullet_count
            grid_span = int(cell.get("gridSpan", "1"))
            row_span = int(cell.get("rowSpan", "1"))
            h_merge = cell.get("hMerge") in {"1", "true"}
            v_merge = cell.get("vMerge") in {"1", "true"}
            values.append(
                {
                    "row": row_index,
                    "column": column_index,
                    "sourceCellId": f"{source_block_id}:row:{row_index}:column:{column_index}",
                    "text": body.text,
                    "paragraphs": body.paragraphs,
                    "runs": body.runs,
                    "runStyles": body.run_styles,
                    "textBody": body.text_body,
                    "gridSpan": grid_span,
                    "rowSpan": row_span,
                    "hMerge": h_merge,
                    "vMerge": v_merge,
                }
            )
            if (grid_span > 1 or row_span > 1) and not h_merge and not v_merge:
                merged_cells.append(
                    {
                        "row": row_index,
                        "column": column_index,
                        "rowSpan": row_span,
                        "columnSpan": grid_span,
                    }
                )
            count += 1
        rows.append(values)
    for merged in merged_cells:
        origin_row = merged["row"]
        origin_column = merged["column"]
        for row_index in range(origin_row, origin_row + merged["rowSpan"]):
            for column_index in range(origin_column, origin_column + merged["columnSpan"]):
                if row_index >= len(rows) or column_index >= len(rows[row_index]):
                    continue
                rows[row_index][column_index]["mergeOrigin"] = {
                    "row": origin_row,
                    "column": origin_column,
                }
                rows[row_index][column_index]["mergeCovered"] = (
                    row_index != origin_row or column_index != origin_column
                )
    return {
        "rows": rows,
        "cells": [[cell["text"] for cell in row] for row in rows],
        "mergedCells": merged_cells,
        "columnWidthsEmu": column_widths,
        "rowHeightsEmu": row_heights,
    }, count, picture_bullet_count


def _chart_values(element: etree._Element | None) -> list[str]:
    if element is None:
        return []
    points = element.findall(".//c:pt", namespaces=NS)
    if points:
        return [
            (point.find("c:v", namespaces=NS).text or "")
            for point in sorted(points, key=lambda value: int(value.get("idx", "0")))
            if point.find("c:v", namespaces=NS) is not None
        ]
    return [value.text or "" for value in element.findall(".//c:v", namespaces=NS)]


def _numeric_chart_values(values: list[str], *, chart_part: str) -> list[int | float]:
    result: list[int | float] = []
    for value in values:
        try:
            number = float(value)
        except ValueError as error:
            raise SourceContentError(
                UNSUPPORTED_OBJECT,
                detail={"reason": "chart_value_not_numeric", "part": chart_part},
            ) from error
        result.append(int(number) if number.is_integer() else number)
    return result


def _extract_chart(package: ZipFile, chart_part: str) -> tuple[dict[str, Any], int]:
    if chart_part not in package.namelist():
        raise SourceContentError(
            MANIFEST_INVALID,
            detail={"reason": "chart_part_missing", "part": chart_part},
        )
    root = secure_xml(package.read(chart_part), part_name=chart_part)
    values = [value.text or "" for value in root.findall(".//c:v", namespaces=NS)]
    formulas = [value.text or "" for value in root.findall(".//c:f", namespaces=NS)]
    plot_area = root.find(".//c:plotArea", namespaces=NS)
    chart_element = None
    if plot_area is not None:
        chart_element = next(
            (
                child
                for child in plot_area
                if etree.QName(child).localname.endswith("Chart")
            ),
            None,
        )
    if chart_element is None:
        raise SourceContentError(
            UNSUPPORTED_OBJECT,
            detail={"reason": "chart_type_unsupported", "part": chart_part},
        )
    ooxml_chart_type = etree.QName(chart_element).localname
    if ooxml_chart_type == "barChart":
        bar_direction = chart_element.find("c:barDir", namespaces=NS)
        chart_type = (
            "bar"
            if bar_direction is not None and bar_direction.get("val") == "bar"
            else "column"
        )
    else:
        chart_type = {
            "lineChart": "line",
            "areaChart": "area",
            "pieChart": "pie",
            "doughnutChart": "donut",
        }.get(ooxml_chart_type)
    if chart_type is None:
        raise SourceContentError(
            UNSUPPORTED_OBJECT,
            detail={
                "reason": "chart_type_unsupported",
                "part": chart_part,
                "chartType": ooxml_chart_type,
            },
        )
    categories: list[str] = []
    series: list[dict[str, Any]] = []
    chart_value_count = 0
    for series_index, series_element in enumerate(
        chart_element.findall("c:ser", namespaces=NS)
    ):
        name_values = _chart_values(series_element.find("c:tx", namespaces=NS))
        category_values = _chart_values(series_element.find("c:cat", namespaces=NS))
        if not category_values:
            category_values = _chart_values(series_element.find("c:xVal", namespaces=NS))
        raw_values = _chart_values(series_element.find("c:val", namespaces=NS))
        if not raw_values:
            raw_values = _chart_values(series_element.find("c:yVal", namespaces=NS))
        numeric_values = _numeric_chart_values(raw_values, chart_part=chart_part)
        if not numeric_values:
            raise SourceContentError(
                UNSUPPORTED_OBJECT,
                detail={"reason": "chart_series_values_missing", "part": chart_part},
            )
        if not categories:
            categories = category_values
        elif category_values and category_values != categories:
            raise SourceContentError(
                UNSUPPORTED_OBJECT,
                detail={"reason": "chart_category_mismatch", "part": chart_part},
            )
        series.append(
            {
                "name": name_values[0] if name_values else f"Series {series_index + 1}",
                "values": numeric_values,
            }
        )
        chart_value_count += len(numeric_values)
    if not series:
        raise SourceContentError(
            UNSUPPORTED_OBJECT,
            detail={"reason": "chart_series_missing", "part": chart_part},
        )
    return {
        "values": values,
        "formulas": formulas,
        "sourcePart": chart_part,
        "categories": categories,
        "series": series,
        "chartType": chart_type,
    }, chart_value_count


def _semantic_role(element: etree._Element, *, default: str) -> str:
    placeholder = element.find(".//p:ph", namespaces=NS)
    placeholder_type = placeholder.get("type") if placeholder is not None else None
    return {
        "title": "title",
        "ctrTitle": "title",
        "subTitle": "subtitle",
        "body": "body",
        "ftr": "source_note",
    }.get(placeholder_type or "", default)


def _extract_unit(
    package: ZipFile,
    *,
    part_name: str,
    source_unit_id: str,
    source_index: int,
    object_type: str,
    media_assets: dict[str, SealedManifestAsset],
    image_occurrences: Counter[str],
    typography: PptxTypographyResolver,
) -> tuple[SourceContentUnit, list[SourceInventoryObject], dict[str, int]]:
    root = secure_xml(package.read(part_name), part_name=part_name)
    rels = _relationship_map(package, part_name)
    shape_tree = root.find(".//p:spTree", namespaces=NS)
    if shape_tree is None:
        raise SourceContentError(
            MANIFEST_INVALID,
            detail={"reason": "shape_tree_missing", "sourceUnitId": source_unit_id},
        )
    blocks: list[SourceContentBlock] = []
    objects: list[SourceInventoryObject] = []
    stats = {
        "table_cells": 0,
        "chart_values": 0,
        "tables": 0,
        "charts": 0,
        "picture_bullets": 0,
    }
    order = 0
    source_shape_index = 0

    def bullet_image_resource(rel_id: str) -> dict[str, str]:
        target = rels.get(rel_id)
        if (
            target is None
            or not target[1].endswith("/image")
            or target[0] not in package.namelist()
        ):
            raise SourceContentError(
                MANIFEST_INVALID,
                detail={
                    "reason": "picture_bullet_relationship_invalid",
                    "part": part_name,
                    "relationshipId": rel_id,
                },
            )
        media_part = target[0]
        payload = package.read(media_part)
        media_type = mimetypes.guess_type(media_part)[0] or "application/octet-stream"
        return {
            "bulletImageDataUri": (
                f"data:{media_type};base64,{base64.b64encode(payload).decode('ascii')}"
            ),
            "bulletImageDigest": digest_bytes(payload),
        }

    def visit_shapes(
        container: etree._Element,
        parent_transform: AffineTransform,
    ) -> None:
        nonlocal order, source_shape_index
        for ordinal, element in enumerate(container):
            local = etree.QName(element).localname
            if local in {"nvGrpSpPr", "grpSpPr", "extLst"}:
                continue
            shape_id, shape_name = _shape_identity(element, ordinal)
            current_shape_index = source_shape_index
            source_shape_index += 1
            object_id = f"{source_unit_id}:object:{shape_id}"
            content_bearing = True
            supported = True
            classification = "supported_slide_object"
            new_block: SourceContentBlock | None = None
            group_transform: AffineTransform | None = None

            def source_payload() -> dict[str, Any]:
                return {
                    "shapeId": shape_id,
                    "sourceSlideIndex": source_index,
                    "sourceShapeIndex": current_shape_index,
                }

            def geometry_for(block_id: str) -> dict[str, Any]:
                return _geometry(
                    element,
                    parent_transform=parent_transform,
                    inherited_shapes=typography.placeholder_shape_chain(
                        element, part_name=part_name
                    ),
                    local_source="slide" if object_type == "slide_object" else "notes",
                    source_block_id=block_id,
                )

            if local == "sp":
                body = extract_text_body(
                    element,
                    context=typography.shape_context(element, part_name=part_name),
                    bullet_image_resolver=bullet_image_resource,
                )
                stats["picture_bullets"] += body.picture_bullet_count
                if body.text:
                    kind = "source_note" if object_type == "notes_object" else "text"
                    role = _semantic_role(element, default="body")
                    block_id = f"{source_unit_id}:{kind}:{shape_id}"
                    new_block = _block(
                        block_id=block_id,
                        kind=kind,
                        role=role,
                        order=order,
                        payload={
                            "text": body.text,
                            "paragraphs": body.paragraphs,
                            "runs": body.runs,
                            **source_payload(),
                            "shapeName": shape_name,
                            "geometry": geometry_for(block_id),
                            "runStyles": body.run_styles,
                            "dominantSizePt": body.dominant_size_pt,
                            "textBody": body.text_body,
                            **placeholder_payload(element),
                        },
                    )
                elif element.find(".//p:ph", namespaces=NS) is not None:
                    content_bearing = False
                    classification = "empty_placeholder"
                else:
                    preset = element.find(".//a:prstGeom", namespaces=NS)
                    block_id = f"{source_unit_id}:shape:{shape_id}"
                    new_block = _block(
                        block_id=block_id,
                        kind="shape",
                        role="shape",
                        order=order,
                        payload={
                            **source_payload(),
                            "shapeName": shape_name,
                            "preset": preset.get("prst") if preset is not None else None,
                            "geometry": geometry_for(block_id),
                        },
                    )
            elif local == "cxnSp":
                block_id = f"{source_unit_id}:shape:{shape_id}"
                new_block = _block(
                    block_id=block_id,
                    kind="shape",
                    role="connector",
                    order=order,
                    payload={
                        **source_payload(),
                        "shapeName": shape_name,
                        "geometry": geometry_for(block_id),
                    },
                )
            elif local == "pic":
                blip = element.find(".//a:blip", namespaces=NS)
                rel_id = blip.get(f"{{{R_NS}}}embed") if blip is not None else None
                target = rels.get(rel_id or "")
                if target is None or target[0] not in package.namelist():
                    supported = False
                    classification = "unclassified"
                else:
                    media_part = target[0]
                    payload = package.read(media_part)
                    media_digest = digest_bytes(payload)
                    asset_id = f"scma_{media_digest.removeprefix('sha256:')}"
                    media_assets.setdefault(
                        asset_id,
                        SealedManifestAsset(
                            asset_id=asset_id,
                            media_type=mimetypes.guess_type(media_part)[0] or "application/octet-stream",
                            content_digest=media_digest,
                            payload=payload,
                        ),
                    )
                    image_occurrences[asset_id] += 1
                    block_id = f"{source_unit_id}:image:{shape_id}"
                    new_block = _block(
                        block_id=block_id,
                        kind="image",
                        role="image",
                        order=order,
                        payload={
                            **source_payload(),
                            "assetId": asset_id,
                            "contentDigest": media_digest,
                            "sizeBytes": len(payload),
                            "occurrence": image_occurrences[asset_id],
                            "geometry": geometry_for(block_id),
                            "crop": _image_crop(element),
                            "colorTransforms": _image_color_transforms(element),
                            "altText": shape_name,
                            **placeholder_payload(element),
                        },
                    )
            elif local == "graphicFrame":
                graphic_data = element.find(".//a:graphicData", namespaces=NS)
                uri = graphic_data.get("uri", "") if graphic_data is not None else ""
                if uri.endswith("/table") and element.find(".//a:tbl", namespaces=NS) is not None:
                    block_id = f"{source_unit_id}:table:{shape_id}"
                    payload, cell_count, picture_bullet_count = _extract_table(
                        element,
                        source_block_id=block_id,
                        bullet_image_resolver=bullet_image_resource,
                    )
                    payload.update(source_payload())
                    payload["geometry"] = geometry_for(block_id)
                    stats["table_cells"] += cell_count
                    stats["picture_bullets"] += picture_bullet_count
                    stats["tables"] += 1
                    new_block = _block(
                        block_id=block_id,
                        kind="table",
                        role="table",
                        order=order,
                        payload=payload,
                    )
                elif uri.endswith("/chart"):
                    chart = element.find(".//c:chart", namespaces=NS)
                    rel_id = chart.get(f"{{{R_NS}}}id") if chart is not None else None
                    target = rels.get(rel_id or "")
                    if target is None:
                        supported = False
                        classification = "unclassified"
                    else:
                        payload, value_count = _extract_chart(package, target[0])
                        block_id = f"{source_unit_id}:chart:{shape_id}"
                        payload.update(source_payload())
                        payload["geometry"] = geometry_for(block_id)
                        stats["chart_values"] += value_count
                        stats["charts"] += 1
                        new_block = _block(
                            block_id=block_id,
                            kind="chart",
                            role="chart",
                            order=order,
                            payload=payload,
                        )
                else:
                    supported = False
                    classification = "unsupported_content_object"
            elif local == "grpSp":
                content_bearing = False
                classification = "supported_group"
                group_transform = _compose_affine(
                    parent_transform, _group_child_transform(element)
                )
            else:
                supported = False
                classification = "unclassified"

            objects.append(
                SourceInventoryObject(
                    object_id=object_id,
                    object_type=object_type,
                    classification=classification,
                    source_unit_id=source_unit_id,
                    content_bearing=content_bearing,
                    supported=supported,
                )
            )
            if new_block is not None:
                blocks.append(new_block)
                order += 1
            if group_transform is not None:
                visit_shapes(element, group_transform)

    visit_shapes(shape_tree, IDENTITY_TRANSFORM)
    return (
        SourceContentUnit(
            source_unit_id=source_unit_id,
            source_index=source_index,
            blocks=tuple(blocks),
        ),
        objects,
        stats,
    )


def _inventory_package(
    package: ZipFile,
    report: PackageSecurityReport,
) -> list[SourceInventoryObject]:
    objects: list[SourceInventoryObject] = []
    names = set(report.entries)
    for name in report.entries:
        classification, content_bearing = _part_classification(name)
        objects.append(
            SourceInventoryObject(
                object_id=f"part:{name}",
                object_type="package_part",
                classification=classification,
                content_bearing=content_bearing,
                supported=classification != "unclassified",
                target_part=name,
            )
        )
    for rel_part in report.relationship_parts:
        root = secure_xml(package.read(rel_part), part_name=rel_part)
        source_part = _relationship_source_part(rel_part)
        for rel in sorted(
            root.findall(f"{{{REL_NS}}}Relationship"),
            key=lambda item: item.get("Id", ""),
        ):
            rel_id = rel.get("Id", "missing")
            rel_type = rel.get("Type", "")
            target = rel.get("Target", "")
            classification, content_bearing, supported = _rel_classification(rel_type)
            target_part = _resolve_target(source_part, target)
            if target_part not in names and not rel.get("TargetMode", "").lower() == "external":
                classification = "unclassified"
                supported = False
            objects.append(
                SourceInventoryObject(
                    object_id=f"rel:{rel_part}:{rel_id}",
                    object_type="relationship",
                    classification=classification,
                    content_bearing=content_bearing,
                    supported=supported,
                    relationship_type=rel_type,
                    target_part=target_part,
                )
            )
    return objects


def extract_pptx_source_content(
    path: Path,
    *,
    owner_id: str,
    reference_snapshot_id: str,
    original_file_name: str | None = None,
) -> ExtractedSourceContent:
    report = admit_pptx_package(path)
    source_bytes = path.read_bytes()
    source_digest = digest_bytes(source_bytes)
    # Revision is part of the seed so an extractor upgrade seals a new manifest_id.
    seed = f"{MANIFEST_ID_EXTRACTOR_REVISION}\n{owner_id}\n{reference_snapshot_id}\n{source_digest}"
    manifest_id = f"scm_{sha256(seed.encode('utf-8')).hexdigest()}"

    with ZipFile(path, "r") as package:
        typography = PptxTypographyResolver(package, relationship_map=_relationship_map)
        slide_size = typography.slide_size()
        document_title = extract_document_title(package, report)
        source_file_title = title_from_file_name(original_file_name)
        inventory_objects = _inventory_package(package, report)
        media_assets: dict[str, SealedManifestAsset] = {}
        image_occurrences: Counter[str] = Counter()
        units: list[SourceContentUnit] = []
        totals = {
            "table_cells": 0,
            "chart_values": 0,
            "tables": 0,
            "charts": 0,
            "picture_bullets": 0,
        }
        slide_parts = _ordered_slide_parts(package, report)
        for index, part_name in enumerate(slide_parts):
            source_unit_id = f"source-slide:{index + 1}"
            unit, objects, stats = _extract_unit(
                package,
                part_name=part_name,
                source_unit_id=source_unit_id,
                source_index=index,
                object_type="slide_object",
                media_assets=media_assets,
                image_occurrences=image_occurrences,
                typography=typography,
            )
            units.append(unit)
            inventory_objects.extend(objects)
            for key in totals:
                totals[key] += stats[key]

            notes_part = None
            for target, rel_type in _relationship_map(package, part_name).values():
                if rel_type.endswith("/notesSlide"):
                    notes_part = target
                    break
            if notes_part is not None and notes_part in package.namelist():
                notes_unit, notes_objects, _ = _extract_unit(
                    package,
                    part_name=notes_part,
                    source_unit_id=source_unit_id,
                    source_index=index,
                    object_type="notes_object",
                    media_assets=media_assets,
                    image_occurrences=image_occurrences,
                    typography=typography,
                )
                if notes_unit.blocks:
                    units[-1] = SourceContentUnit(
                        source_unit_id=source_unit_id,
                        source_index=index,
                        blocks=tuple([*unit.blocks, *notes_unit.blocks]),
                    )
                inventory_objects.extend(notes_objects)

    unsupported = tuple(
        item.object_id
        for item in inventory_objects
        if item.content_bearing and not item.supported and item.classification.startswith("unsupported")
    )
    unclassified = tuple(
        item.object_id for item in inventory_objects if item.classification == "unclassified"
    )
    classified_count = sum(item.classification != "unclassified" for item in inventory_objects)
    inventory = SourceObjectInventorySummary(
        discovered_object_count=len(inventory_objects),
        classified_object_count=classified_count,
        content_bearing_object_count=sum(item.content_bearing for item in inventory_objects),
        unsupported_content_object_ids=unsupported,
        unclassified_object_ids=unclassified,
        external_relationship_count=report.external_relationship_count,
    )
    if unclassified:
        raise SourceContentError(
            MANIFEST_INVALID,
            detail={"unclassifiedObjectIds": list(unclassified[:50])},
        )
    if unsupported:
        raise SourceContentError(
            UNSUPPORTED_OBJECT,
            detail={"unsupportedContentObjectIds": list(unsupported[:50])},
        )

    blocks = [block for unit in units for block in unit.blocks]
    counts = SourceContentCounts(
        source_units=len(units),
        # Speaker notes are metadata, not visible source substance to carry
        # into a template-restyled slide.
        text_blocks=sum(block.kind == "text" for block in blocks),
        table_cells=totals["table_cells"],
        chart_values=totals["chart_values"],
        unique_images=len(media_assets),
        image_occurrences=sum(image_occurrences.values()),
        picture_bullet_count=totals["picture_bullets"],
        raster_fallback_blocks=sum(not block.native_candidate for block in blocks),
    )
    manifest_without_digest = {
        "contractVersion": "source-content-manifest/v1",
        "sourceLayoutVersion": SOURCE_LAYOUT_VERSION_V2,
        "manifestId": manifest_id,
        "referenceSnapshotId": reference_snapshot_id,
        "sourceType": "pptx",
        "sourceDigest": source_digest,
        "objectInventory": inventory.model_dump(mode="json", by_alias=True),
        "inventoryObjects": [
            item.model_dump(mode="json", by_alias=True) for item in inventory_objects
        ],
        "units": [unit.model_dump(mode="json", by_alias=True) for unit in units],
        "counts": counts.model_dump(mode="json", by_alias=True),
    }
    if slide_size is not None:
        manifest_without_digest["slideSize"] = slide_size
    if document_title is not None:
        manifest_without_digest["documentTitle"] = document_title
    if source_file_title is not None:
        manifest_without_digest["sourceFileTitle"] = source_file_title
    manifest = SourceContentManifest(
        **manifest_without_digest,
        owner_id=owner_id,
        manifest_digest=digest_json(manifest_without_digest),
    )
    return ExtractedSourceContent(
        manifest=manifest,
        assets=tuple(media_assets[key] for key in sorted(media_assets)),
    )
