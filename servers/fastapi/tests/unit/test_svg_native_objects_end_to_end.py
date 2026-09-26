"""Native tables/charts for svg decks through the whole offline chain.

A hand-written model response (2 slides: a 4x3 Korean/number table and a
column chart, each declared with ``data-native-*`` JSON) goes through the same
functions the product runs, in order: author-side extraction, the SVG
contract, ``finalize_svg`` (page number), declaration binding, publish
(``svg_slide_content``), the operation's bundle entry, and the isolated
worker's ``execute_svg_export``. The PPTX is read back with python-pptx.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from io import BytesIO
from types import SimpleNamespace
from xml.etree import ElementTree as ET

from pptx import Presentation

from services.authored_canvas import canvas_metadata_for_aspect_ratio
from services.generation_core.drivers.svg.export_native_wire import BUNDLE_KEY, bundle_entry
from services.generation_core.drivers.svg.native_declarations import (
    bind_native_declarations,
    extract_native_declarations,
)
from services.generation_core.drivers.svg.publish import svg_slide_content
from services.svg_contract import SVG_CONTRACT_VERSION, finalize_svg, validate_svg
from tests.unit.test_svg_vector_pdf_export import MemoryStore
from workers.public_svg_pptx_readback import iter_shapes
from workers.public_svg_export_worker import (
    CONVERTER_VERSION,
    SVG_EXPORT_SCHEMA,
    converter_digest,
    execute_svg_export,
)

ROWS = [["구분", "2024년", "2025년"], ["매출", "1,200", "1,450"],
        ["영업이익", "180", "236"], ["순이익", "95", "141"]]
X, Y, COL, ROW = 120, 160, 200, 60
CATEGORIES, VALUES = ["1분기", "2분기", "3분기"], [120, 95, 140]


def _table_group(rows) -> str:
    declaration = json.dumps({"rows": rows, "headerRows": 1}, ensure_ascii=False)
    width, height = COL * len(rows[0]), ROW * len(rows)
    parts = [f"<g id=\"native-table-sales\" data-native-table='{declaration}'>",
             f'<rect x="{X}" y="{Y}" width="{width}" height="{height}" fill="#FFFFFF" stroke="#94A3B8"/>',
             f'<rect x="{X}" y="{Y}" width="{width}" height="{ROW}" fill="#1E3A8A"/>']
    parts += [f'<line x1="{X + COL * c}" y1="{Y}" x2="{X + COL * c}" y2="{Y + height}" stroke="#94A3B8"/>'
              for c in range(1, len(rows[0]))]
    parts += [f'<line x1="{X}" y1="{Y + ROW * r}" x2="{X + width}" y2="{Y + ROW * r}" stroke="#94A3B8"/>'
              for r in range(1, len(rows))]
    for r, row in enumerate(rows):
        color = "#FFFFFF" if r == 0 else "#111827"
        for c, text in enumerate(row):
            parts.append(f'<text x="{X + COL * c + 16}" y="{Y + ROW * r + 38}" font-family="Noto Sans KR" '
                         f'font-size="20" fill="{color}">{text}</text>')
    return "".join(parts) + "</g>"


def _chart_group() -> str:
    declaration = json.dumps({"chartType": "column", "categories": CATEGORIES,
                              "series": [{"name": "매출", "values": VALUES}]}, ensure_ascii=False)
    parts = [f"<g id=\"native-chart-sales\" data-native-chart='{declaration}'>"]
    for i, (label, value) in enumerate(zip(CATEGORIES, VALUES, strict=True)):
        x = 200 + i * 220
        parts.append(f'<rect x="{x}" y="{560 - value * 2}" width="120" height="{value * 2}" fill="#2563EB"/>')
        parts.append(f'<text x="{x + 30}" y="600" font-family="Noto Sans KR" font-size="20" '
                     f'fill="#111827">{label}</text>')
    parts.append('<text x="900" y="200" font-family="Noto Sans KR" font-size="20" fill="#111827">매출</text>')
    return "".join(parts) + "</g>"


def _response(body: str) -> str:
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">'
            '<rect x="0" y="0" width="1280" height="720" fill="#FFFFFF"/>'
            '<text x="120" y="100" font-family="Noto Sans KR" font-size="36" fill="#111827">실적 요약</text>'
            f"{body}</svg>")


def _published_content(raw: str, index: int) -> tuple[str, dict]:
    root = ET.fromstring(raw)
    declarations = extract_native_declarations(root)
    authored = ET.tostring(root, encoding="unicode")
    validate_svg(authored)
    payload = {"svg_content": authored, "quality": {"passed": True}}
    bind_native_declarations(payload, authored, declarations)
    payload["svg_content"] = finalize_svg(authored, index=index, n=2)
    return payload["svg_content"], svg_slide_content(payload, role="content", source="", pt_per_px=0.75)


def _export(tmp_path, svgs, contents):
    store, operation_id = MemoryStore(tmp_path), uuid.uuid4()
    canvas = canvas_metadata_for_aspect_ratio("16:9")
    slides = [{"slideIndex": i, "notes": "", "artifactRef": store.put(
        svg.encode(), operation_id=operation_id, kind="slide-svg", media_type="image/svg+xml").to_wire()}
        for i, svg in enumerate(svgs)]
    bundle = {"schemaVersion": SVG_EXPORT_SCHEMA, "contractVersion": SVG_CONTRACT_VERSION,
              "converterVersion": CONVERTER_VERSION, "converterDigest": converter_digest(),
              "canonicalExportIdentity": hashlib.sha256(b"identity").hexdigest(), "exportFormat": "pptx",
              "canvas": canvas.model_dump(mode="json", by_alias=True), "fontEmbedding": False,
              "slides": slides, BUNDLE_KEY: [bundle_entry(content) for content in contents]}
    ref = store.put(json.dumps(bundle).encode(), operation_id=operation_id, kind="export-bundle",
                    media_type="application/json")
    job = SimpleNamespace(input_artifact=ref, operation_id=operation_id, deadline_epoch_seconds=time.time() + 300)
    output = asyncio.run(execute_svg_export(job, store))
    return Presentation(BytesIO(store.read(output.artifact))), output.evidence


def _frames(slide):
    return list(iter_shapes(slide.shapes))


def _texts(slide) -> list[str]:
    return [shape.text_frame.text for shape in iter_shapes(slide.shapes) if shape.has_text_frame]


def test_declared_table_and_chart_export_as_native_objects(tmp_path):
    svg1, content1 = _published_content(_response(_table_group(ROWS)), 0)
    svg2, content2 = _published_content(_response(_chart_group()), 1)
    assert "data-native" not in svg1 + svg2
    assert content1["svgReceipt"]["nativeObjects"] == {"declared": 1, "accepted": 1, "dropped": []}
    assert content2["svgReceipt"]["nativeObjects"] == {"declared": 1, "accepted": 1, "dropped": []}

    deck, evidence = _export(tmp_path, [svg1, svg2], [content1, content2])

    tables = [shape for shape in _frames(deck.slides[0]) if shape.has_table]
    assert len(tables) == 1
    table = tables[0].table
    assert [[cell.text for cell in row.cells] for row in table.rows] == ROWS
    drawn = _texts(deck.slides[0])
    assert "매출" not in drawn and "실적 요약" in drawn  # cells live only in the table
    charts = [shape for shape in _frames(deck.slides[1]) if shape.has_chart]
    assert len(charts) == 1
    plot = charts[0].chart.plots[0]
    assert list(plot.categories) == CATEGORIES
    assert [list(series.values) for series in plot.series] == [[float(v) for v in VALUES]]
    readback = evidence["structuralReadback"]["nativeObjects"]
    assert readback == {"offered": 2, "native": 2, "fellBack": 0, "reasons": []}


def test_mismatched_declaration_keeps_the_drawn_grid_and_records_why(tmp_path):
    wrong = [row[:] for row in ROWS]
    wrong[1][1] = "1,300"  # JSON disagrees with the drawn "1,200"
    raw = _response(_table_group(ROWS).replace(
        json.dumps({"rows": ROWS, "headerRows": 1}, ensure_ascii=False),
        json.dumps({"rows": wrong, "headerRows": 1}, ensure_ascii=False)))
    svg, content = _published_content(raw, 0)
    summary = content["svgReceipt"]["nativeObjects"]
    assert summary["accepted"] == 0 and summary["dropped"][0]["id"] == "native-table-sales"
    assert "nativeObjects" not in content

    deck, evidence = _export(tmp_path, [svg], [content])

    assert not any(shape.has_table for shape in _frames(deck.slides[0]))
    assert "1,200" in _texts(deck.slides[0])  # the drawn grid is exported as editable text
    assert evidence["structuralReadback"]["nativeObjects"]["offered"] == 0


def test_a_normal_weight_header_stays_normal_weight(tmp_path):
    svg, content = _published_content(_response(_table_group(ROWS)), 0)
    deck, _ = _export(tmp_path, [svg], [content])
    table = next(shape for shape in _frames(deck.slides[0]) if shape.has_table).table
    runs = [run for cell in table.rows[0].cells for p in cell.text_frame.paragraphs for run in p.runs]
    assert runs and all(run.font.bold is not True for run in runs)
