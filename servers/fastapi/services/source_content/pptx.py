"""Deterministic, network-free PPTX object inventory and extraction."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
import mimetypes
import posixpath
from pathlib import Path, PurePosixPath
import re
from typing import Any
from zipfile import ZipFile

from lxml import etree

from services.source_content.contracts import (
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


def _paragraph_payload(element: etree._Element) -> tuple[list[dict[str, Any]], str]:
    paragraphs: list[dict[str, Any]] = []
    flat: list[str] = []
    for paragraph in element.findall(".//a:p", namespaces=NS):
        runs: list[str] = []
        for child in paragraph:
            if child.tag in {f"{{{A_NS}}}r", f"{{{A_NS}}}fld"}:
                text = child.find("a:t", namespaces=NS)
                runs.append(text.text if text is not None and text.text is not None else "")
            elif child.tag == f"{{{A_NS}}}br":
                runs.append("\n")
        paragraph_text = "".join(runs)
        paragraphs.append({"runs": runs, "text": paragraph_text})
        flat.append(paragraph_text)
    return paragraphs, "\n".join(flat)


def _flat_runs(paragraphs: list[dict[str, Any]]) -> list[str]:
    runs: list[str] = []
    for index, paragraph in enumerate(paragraphs):
        runs.extend(paragraph["runs"])
        if index + 1 < len(paragraphs):
            runs.append("\n")
    return runs


def _geometry(element: etree._Element) -> dict[str, int]:
    xfrm = element.find(".//a:xfrm", namespaces=NS)
    if xfrm is None:
        xfrm = element.find(".//p:xfrm", namespaces=NS)
    if xfrm is None:
        return {}
    off = xfrm.find("a:off", namespaces=NS)
    ext = xfrm.find("a:ext", namespaces=NS)
    result: dict[str, int] = {}
    if off is not None:
        result.update(x=int(off.get("x", "0")), y=int(off.get("y", "0")))
    if ext is not None:
        result.update(cx=int(ext.get("cx", "0")), cy=int(ext.get("cy", "0")))
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


def _extract_table(element: etree._Element) -> tuple[dict[str, Any], int]:
    rows: list[list[dict[str, Any]]] = []
    merged_cells: list[dict[str, int]] = []
    count = 0
    for row_index, row in enumerate(element.findall(".//a:tbl/a:tr", namespaces=NS)):
        values: list[dict[str, Any]] = []
        for column_index, cell in enumerate(row.findall("a:tc", namespaces=NS)):
            paragraphs, text = _paragraph_payload(cell)
            grid_span = int(cell.get("gridSpan", "1"))
            row_span = int(cell.get("rowSpan", "1"))
            h_merge = cell.get("hMerge") in {"1", "true"}
            v_merge = cell.get("vMerge") in {"1", "true"}
            values.append(
                {
                    "row": row_index,
                    "column": column_index,
                    "text": text,
                    "paragraphs": paragraphs,
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
    return {
        "rows": rows,
        "cells": [[cell["text"] for cell in row] for row in rows],
        "mergedCells": merged_cells,
        "geometry": _geometry(element),
    }, count


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
    stats = {"table_cells": 0, "chart_values": 0, "tables": 0, "charts": 0}
    order = 0
    for ordinal, element in enumerate(shape_tree):
        local = etree.QName(element).localname
        if local in {"nvGrpSpPr", "grpSpPr", "extLst"}:
            continue
        shape_id, shape_name = _shape_identity(element, ordinal)
        object_id = f"{source_unit_id}:object:{shape_id}"
        content_bearing = True
        supported = True
        classification = "supported_slide_object"
        new_block: SourceContentBlock | None = None

        if local == "sp":
            paragraphs, text = _paragraph_payload(element)
            if text:
                kind = "source_note" if object_type == "notes_object" else "text"
                role = _semantic_role(element, default="body")
                new_block = _block(
                    block_id=f"{source_unit_id}:{kind}:{shape_id}",
                    kind=kind,
                    role=role,
                    order=order,
                    payload={
                        "text": text,
                        "paragraphs": paragraphs,
                        "runs": _flat_runs(paragraphs),
                        "shapeName": shape_name,
                        "geometry": _geometry(element),
                    },
                )
            elif element.find(".//p:ph", namespaces=NS) is not None:
                content_bearing = False
                classification = "empty_placeholder"
            else:
                preset = element.find(".//a:prstGeom", namespaces=NS)
                new_block = _block(
                    block_id=f"{source_unit_id}:shape:{shape_id}",
                    kind="shape",
                    role="shape",
                    order=order,
                    payload={
                        "shapeName": shape_name,
                        "preset": preset.get("prst") if preset is not None else None,
                        "geometry": _geometry(element),
                    },
                )
        elif local == "cxnSp":
            new_block = _block(
                block_id=f"{source_unit_id}:shape:{shape_id}",
                kind="shape",
                role="connector",
                order=order,
                payload={"shapeName": shape_name, "geometry": _geometry(element)},
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
                new_block = _block(
                    block_id=f"{source_unit_id}:image:{shape_id}",
                    kind="image",
                    role="image",
                    order=order,
                    payload={
                        "assetId": asset_id,
                        "contentDigest": media_digest,
                        "sizeBytes": len(payload),
                        "occurrence": image_occurrences[asset_id],
                        "geometry": _geometry(element),
                        "altText": shape_name,
                    },
                )
        elif local == "graphicFrame":
            graphic_data = element.find(".//a:graphicData", namespaces=NS)
            uri = graphic_data.get("uri", "") if graphic_data is not None else ""
            if uri.endswith("/table") and element.find(".//a:tbl", namespaces=NS) is not None:
                payload, cell_count = _extract_table(element)
                stats["table_cells"] += cell_count
                stats["tables"] += 1
                new_block = _block(
                    block_id=f"{source_unit_id}:table:{shape_id}",
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
                    payload["geometry"] = _geometry(element)
                    stats["chart_values"] += value_count
                    stats["charts"] += 1
                    new_block = _block(
                        block_id=f"{source_unit_id}:chart:{shape_id}",
                        kind="chart",
                        role="chart",
                        order=order,
                        payload=payload,
                    )
            else:
                supported = False
                classification = "unsupported_content_object"
        elif local == "grpSp":
            supported = False
            classification = "unsupported_content_object"
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
) -> ExtractedSourceContent:
    report = admit_pptx_package(path)
    source_bytes = path.read_bytes()
    source_digest = digest_bytes(source_bytes)
    seed = f"{owner_id}\n{reference_snapshot_id}\n{source_digest}".encode("utf-8")
    manifest_id = f"scm_{sha256(seed).hexdigest()}"

    with ZipFile(path, "r") as package:
        inventory_objects = _inventory_package(package, report)
        media_assets: dict[str, SealedManifestAsset] = {}
        image_occurrences: Counter[str] = Counter()
        units: list[SourceContentUnit] = []
        totals = {"table_cells": 0, "chart_values": 0, "tables": 0, "charts": 0}
        slide_parts = sorted(
            (name for name in report.entries if _SLIDE_RE.match(name)),
            key=lambda name: int(_SLIDE_RE.match(name).group(1)),  # type: ignore[union-attr]
        )
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
        raster_fallback_blocks=sum(not block.native_candidate for block in blocks),
    )
    manifest_without_digest = {
        "contractVersion": "source-content-manifest/v1",
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
    manifest = SourceContentManifest(
        **manifest_without_digest,
        owner_id=owner_id,
        manifest_digest=digest_json(manifest_without_digest),
    )
    return ExtractedSourceContent(
        manifest=manifest,
        assets=tuple(media_assets[key] for key in sorted(media_assets)),
    )
