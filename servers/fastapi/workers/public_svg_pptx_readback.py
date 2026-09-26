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


def source_preservation_readback(deck, receipts, expected_image_digests):
    pages = []
    for index, (slide, receipt, image_digests) in enumerate(
        zip(deck.slides, receipts, expected_image_digests, strict=True)
    ):
        shapes = list(iter_shapes(slide.shapes))
        text_bindings = [
            binding for binding in receipt["bindings"]
            if binding["kind"] in {"text", "table_cell"} and binding["text"]
        ]
        expected_text = Counter(_readback_text(binding["text"]) for binding in text_bindings)
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
                "representation": "native-text-and-shape-cells",
                "nativeTableShapeCount": len(table_shapes),
                "nativeTableEditabilityVerified": False,
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
    return {
        "schemaVersion": "svg-source-preservation-export-readback/v1",
        "passed": True,
        "sourceDigest": receipts[0]["source_digest"],
        "manifestDigest": receipts[0]["manifest_digest"],
        "pages": pages,
    }
