"""Table and chart semantics from source PPTX objects."""

from __future__ import annotations

import math
from typing import Any
from zipfile import ZipFile

from lxml import etree

from services.source_content.errors import MANIFEST_INVALID, SourceContentError, UNSUPPORTED_OBJECT
from services.source_content.pptx_extraction_constants import NS
from services.source_content.pptx_text_body import BulletImageResolver, extract_text_body
from services.source_content.security import secure_xml


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
        count = element.find(".//c:ptCount", namespaces=NS)
        try:
            indexes = sorted(int(point.get("idx", "-1")) for point in points)
            declared_count = int(count.get("val", "-1")) if count is not None else len(points)
        except ValueError as error:
            raise SourceContentError(UNSUPPORTED_OBJECT, detail={"reason": "chart_cache_incomplete"}) from error
        if (indexes != list(range(len(points))) or
                declared_count != len(points) or
                any(point.find("c:v", namespaces=NS) is None for point in points)):
            raise SourceContentError(UNSUPPORTED_OBJECT, detail={"reason": "chart_cache_incomplete"})
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
            if not math.isfinite(number):
                raise ValueError("chart_value_nonfinite")
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
    source_series = chart_element.findall("c:ser", namespaces=NS)
    try:
        source_series.sort(key=lambda element: int(element.find("c:order", namespaces=NS).get("val")))
    except (AttributeError, ValueError, TypeError) as error:
        raise SourceContentError(UNSUPPORTED_OBJECT, detail={"reason": "chart_series_order_invalid"}) from error
    for series_index, series_element in enumerate(source_series):
        name_values = _chart_values(series_element.find("c:tx", namespaces=NS))
        category_values = _chart_values(series_element.find("c:cat", namespaces=NS))
        if not category_values:
            category_values = _chart_values(series_element.find("c:xVal", namespaces=NS))
        raw_values = _chart_values(series_element.find("c:val", namespaces=NS))
        if not raw_values:
            raw_values = _chart_values(series_element.find("c:yVal", namespaces=NS))
        numeric_values = _numeric_chart_values(raw_values, chart_part=chart_part)
        if not category_values or len(category_values) != len(numeric_values):
            raise SourceContentError(UNSUPPORTED_OBJECT,
                detail={"reason": "chart_category_mismatch", "part": chart_part})
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
    from services.source_content.pptx_chart_provenance import native_chart_provenance
    original_series = [{**item, "categories": categories} for item in series]
    return {
        "values": values,
        "formulas": formulas,
        "sourcePart": chart_part,
        "categories": categories,
        "series": series,
        "chartType": chart_type,
        "nativeSource": native_chart_provenance(package, chart_part, root, chart_element, original_series),
    }, chart_value_count
