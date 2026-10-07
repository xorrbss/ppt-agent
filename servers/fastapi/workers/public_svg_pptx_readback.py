"""Structural readback of a produced svg-export PPTX (python-pptx, worker side)."""
from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import Counter

from pptx.enum.shapes import MSO_SHAPE_TYPE


def iter_shapes(shapes):
    for shape in shapes:
        yield shape
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            yield from iter_shapes(shape.shapes)


def _readback_text(value):
    value = unicodedata.normalize("NFC", value or "")
    return re.sub(r"\s+", " ", value).strip()


def _binding_digest(value):
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _source_native_tables(shapes, receipt, deck):
    """Bind native table identity, grid and merged cells to immutable source cells."""
    declarations = receipt.get("native_tables") or []
    actual = [shape for shape in shapes if shape.has_table]
    if not declarations:
        if actual:
            raise ValueError("svg_source_pptx_table_binding_mismatch")
        return set(), []
    by_name = {shape.name: shape for shape in actual}
    if len(by_name) != len(actual) or len(actual) != len(declarations):
        raise ValueError("svg_source_pptx_table_binding_mismatch")
    consumed, evidence = set(), []
    for declaration in declarations:
        block_id, element_id = declaration["block_id"], declaration["element_id"]
        if block_id in consumed or element_id not in by_name:
            raise ValueError("svg_source_pptx_table_binding_mismatch")
        shape = by_name.pop(element_id)
        frame = declaration["frame"]
        scale_x = deck.slide_width / receipt["canvas"]["width"]
        scale_y = deck.slide_height / receipt["canvas"]["height"]
        if any(abs(actual_value - frame[key] * scale) > max(1, scale * .5)
               for actual_value, key, scale in ((shape.left, "x", scale_x), (shape.top, "y", scale_y),
                   (shape.width, "width", scale_x), (shape.height, "height", scale_y))):
            raise ValueError("svg_source_pptx_table_geometry_mismatch")
        table = shape.table
        entries = [b for b in receipt["bindings"]
                   if b["kind"] == "table_cell" and b.get("block_id") == block_id]
        cells = {}
        for binding in entries:
            cells.setdefault(binding["source_id"], []).append(binding)
        if not cells:
            raise ValueError("svg_source_pptx_table_binding_mismatch")
        coverage, cell_evidence = set(), []
        for source_id, lines in cells.items():
            lines.sort(key=lambda b: b["start"])
            first = lines[0]
            r, c, rs, cs = (first[k] for k in ("row", "column", "row_span", "column_span"))
            if min(r, c) < 0 or min(rs, cs) < 1 or r + rs > len(table.rows) or c + cs > len(table.columns):
                raise ValueError("svg_source_pptx_table_structure_mismatch")
            cursor, fragments = 0, []
            for line in lines:
                if (any(line[k] != first[k] for k in ("row", "column", "row_span", "column_span"))
                        or line["start"] != cursor or line["end"] != cursor + len(line["text"])):
                    raise ValueError("svg_source_pptx_table_binding_mismatch")
                fragments.append(line["text"])
                cursor = line["end"]
            value = "".join(fragments)
            cell = table.cell(r, c)
            if (cell.is_spanned or cell.span_height != rs or cell.span_width != cs
                    or _readback_text(cell.text) != _readback_text(value)):
                raise ValueError("svg_source_pptx_table_cell_mismatch")
            for row in range(r, r + rs):
                for column in range(c, c + cs):
                    key = (row, column)
                    covered = table.cell(row, column)
                    if key in coverage or (key != (r, c) and (not covered.is_spanned or covered.text)):
                        raise ValueError("svg_source_pptx_table_structure_mismatch")
                    coverage.add(key)
            cell_evidence.append({"sourceId": source_id, "row": r, "column": c,
                "rowSpan": rs, "columnSpan": cs, "valueDigest": _binding_digest(value)})
        if len(coverage) != len(table.rows) * len(table.columns):
            raise ValueError("svg_source_pptx_table_structure_mismatch")
        consumed.add(block_id)
        evidence.append({"sourceBlockId": block_id, "elementId": element_id,
            "rows": len(table.rows), "columns": len(table.columns), "cells": cell_evidence})
    if by_name:
        raise ValueError("svg_source_pptx_table_binding_mismatch")
    return consumed, evidence


def source_preservation_readback(deck, receipts, expected_image_digests):
    pages = []
    for index, (slide, receipt, image_digests) in enumerate(
        zip(deck.slides, receipts, expected_image_digests, strict=True)
    ):
        shapes = list(iter_shapes(slide.shapes))
        native_blocks, native_table_evidence = _source_native_tables(shapes, receipt, deck)
        text_bindings = [
            binding for binding in receipt["bindings"]
            if binding["kind"] in {"text", "table_cell"} and binding["text"]
        ]
        expected_text = Counter(_readback_text(binding["text"]) for binding in text_bindings
                                if binding.get("block_id") not in native_blocks)
        furniture = receipt.get("_export_furniture") or {}
        furniture_text = Counter(value for text in furniture.get("texts", []) if (value := _readback_text(text)))
        expected_text.update(furniture_text)
        actual_text = Counter(
            value for shape in shapes if shape.has_text_frame
            if (value := _readback_text(shape.text_frame.text))
        )
        if actual_text != expected_text:
            raise ValueError("svg_source_pptx_text_readback_mismatch")

        image_bindings = [binding for binding in receipt["bindings"] if binding["kind"] == "image"]
        if set(image_digests) != {binding["slot_id"] for binding in image_bindings}:
            raise ValueError("svg_source_pptx_image_expectation_mismatch")
        expected_images = Counter(image_digests[binding["slot_id"]] for binding in image_bindings)
        furniture_images = Counter(furniture.get("image_digests", []))
        expected_images.update(furniture_images)
        actual_images = Counter(
            hashlib.sha256(shape.image.blob).hexdigest()
            for shape in shapes if shape.shape_type == MSO_SHAPE_TYPE.PICTURE
        )
        if actual_images != expected_images:
            raise ValueError("svg_source_pptx_image_readback_mismatch")

        table_bindings = [binding for binding in receipt["bindings"] if binding["kind"] == "table_cell"]
        table_shapes = [shape for shape in shapes if shape.has_table]
        pages.append({
            "sourcePageId": receipt["source_page_id"],
            "sourcePageIndex": index,
            "svgDigest": receipt["svg_digest"],
            "text": {
                "representation": "native-text-shapes",
                "expected": [{"sourceId": binding["source_id"],
                    "valueDigest": _binding_digest(binding["text"])} for binding in text_bindings],
                "matchedBindingCount": len(text_bindings),
                "blankBindingCount": sum(
                    binding["kind"] in {"text", "table_cell"} and not binding["text"]
                    for binding in receipt["bindings"]
                ),
            },
            "tables": {
                "representation": "native-tables" if native_blocks else "native-text-and-shape-cells",
                "nativeTableShapeCount": len(table_shapes),
                "nativeTableEditabilityVerified": bool(native_blocks) and native_blocks == {
                    binding["block_id"] for binding in table_bindings},
                "verifiedNativeTables": native_table_evidence,
                "expectedCells": [{
                    "sourceId": binding["source_id"],
                    "row": binding["row"],
                    "column": binding["column"],
                    "rowSpan": binding["row_span"],
                    "columnSpan": binding["column_span"],
                    "valueDigest": _binding_digest(binding["text"]),
                } for binding in table_bindings],
                "matchedCellValueCount": len(table_bindings),
            },
            "images": {
                "representation": "native-picture-shapes",
                "expectedOccurrences": [{
                    "sourceId": binding["source_id"],
                    "slotId": binding["slot_id"],
                    "sourceAssetDigest": binding["asset_digest"],
                    "exportAssetDigest": "sha256:" + image_digests[binding["slot_id"]],
                } for binding in image_bindings],
                "matchedOccurrenceCount": len(image_bindings),
            },
        })
        if furniture:
            pages[-1]["systemFurniture"] = {
                "digest": furniture["digest"],
                "textShapeCount": sum(furniture_text.values()),
                "imageShapeCount": sum(furniture_images.values()),
                "valuesVerified": True,
            }
    return {
        "schemaVersion": "svg-source-preservation-export-readback/v1",
        "passed": True,
        "sourceDigest": receipts[0]["source_digest"],
        "manifestDigest": receipts[0]["manifest_digest"],
        "pages": pages,
    }
