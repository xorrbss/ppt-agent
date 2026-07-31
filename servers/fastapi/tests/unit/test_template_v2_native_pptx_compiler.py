from __future__ import annotations

import hashlib
from io import BytesIO
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
from pptx import Presentation

from templates.v2.models.layouts import SlideLayout
from templates.v2.pptx.assembler import assemble_template_v2_draft
from templates.v2.pptx.native_compiler import (
    TEMPLATE_V2_NATIVE_PPTX_COMPILER_NAME,
    TEMPLATE_V2_NATIVE_PPTX_COMPILER_VERSION,
    TEMPLATE_V2_NATIVE_PPTX_SCHEMA_VERSION,
    TemplateV2NativePptxCompileError,
    compile_template_v2_pptx,
    inspect_native_pptx_structure,
)
from templates.v2.pptx.ooxml_parser import parse_presentation_candidates
from templates.v2.pptx.package_reader import PptxPackageReader

FIXTURE = (
    Path(__file__).parents[1]
    / "fixtures"
    / "template_v2"
    / "native_pptx_golden_v1.json"
)


def _golden_layout() -> SlideLayout:
    return SlideLayout.model_validate(
        {
            "id": "native-golden",
            "description": "Deterministic native compiler golden layout",
            "components": [
                {
                    "id": "hero",
                    "description": "Editable hero and decorative background",
                    "position": {"x": 0, "y": 0},
                    "elements": [
                        {
                            "type": "container",
                            "position": {"x": 0, "y": 0},
                            "size": {"width": 1280, "height": 720},
                            "fill": {"color": "#F4F7FB"},
                            "stroke": {"color": "#D8E1EE", "width": 1},
                        },
                        {
                            "type": "text",
                            "position": {"x": 96, "y": 88},
                            "size": {"width": 1088, "height": 90},
                            "runs": [{"text": "Original title"}],
                            "font": {
                                "size": 32,
                                "family": "Arial",
                                "color": "#123456",
                                "bold": True,
                            },
                            "alignment": {
                                "horizontal": "left",
                                "vertical": "middle",
                            },
                            "decorative": False,
                            "name": "title",
                            "min_length": 1,
                            "max_length": 80,
                        },
                        {
                            "type": "text",
                            "position": {"x": 96, "y": 194},
                            "size": {"width": 1088, "height": 55},
                            "runs": [{"text": "Native OOXML · editable text"}],
                            "font": {
                                "size": 18,
                                "family": "Arial",
                                "color": "#52657A",
                            },
                            "decorative": True,
                            "name": "eyebrow",
                            "min_length": 1,
                            "max_length": 80,
                        },
                        {
                            "type": "group",
                            "name": "details",
                            "position": {"x": 96, "y": 300},
                            "size": {"width": 1088, "height": 240},
                            "children": [
                                {
                                    "type": "text-list",
                                    "position": {"x": 0, "y": 0},
                                    "size": {"width": 1088, "height": 240},
                                    "font": {
                                        "size": 22,
                                        "family": "Arial",
                                        "color": "#123456",
                                    },
                                    "marker": "none",
                                    "items": [[{"text": "Original item"}]],
                                    "decorative": False,
                                    "name": "items",
                                    "min_items": 1,
                                    "max_items": 3,
                                    "min_item_length": 1,
                                    "max_item_length": 80,
                                }
                            ],
                        },
                    ],
                }
            ],
        }
    )


def _golden_content() -> dict:
    return {
        "hero": {
            "title": "Quarterly direction",
            "details": {"items": ["Plan", "Build", "Measure"]},
        }
    }


def _merged_table_layout() -> SlideLayout:
    covered = {
        "runs": [{"text": "preserved while covered"}],
        "covered_by": {"row": 0, "column": 0},
    }
    return SlideLayout.model_validate(
        {
            "id": "native-merged-table",
            "description": "Native editable table with a merged region",
            "components": [
                {
                    "id": "table-panel",
                    "description": "Merged table",
                    "position": {"x": 0, "y": 0},
                    "elements": [
                        {
                            "type": "table",
                            "position": {"x": 100, "y": 100},
                            "size": {"width": 900, "height": 300},
                            "columns": [
                                {
                                    "runs": [{"text": "Merged"}],
                                    "rowspan": 2,
                                    "colspan": 2,
                                    "color": {"color": "#DDEEFF"},
                                    "alignment": "center",
                                },
                                covered,
                                {"runs": [{"text": "H3"}]},
                            ],
                            "rows": [
                                [
                                    covered,
                                    covered,
                                    {"runs": [{"text": "V3"}]},
                                ]
                            ],
                            "decorative": True,
                            "name": "summary",
                            "min_columns": 3,
                            "max_columns": 3,
                            "min_rows": 1,
                            "max_rows": 1,
                        }
                    ],
                }
            ],
        }
    )


def test_native_compiler_is_byte_deterministic_and_matches_golden():
    first = compile_template_v2_pptx([(_golden_layout(), _golden_content())])
    second = compile_template_v2_pptx([(_golden_layout(), _golden_content())])

    assert first.pptx == second.pptx
    assert first.package_sha256 == second.package_sha256
    assert first.structural_sha256 == second.structural_sha256
    assert first.manifest == json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert first.manifest["schema_version"] == TEMPLATE_V2_NATIVE_PPTX_SCHEMA_VERSION
    assert first.manifest["compiler"] == {
        "name": TEMPLATE_V2_NATIVE_PPTX_COMPILER_NAME,
        "version": TEMPLATE_V2_NATIVE_PPTX_COMPILER_VERSION,
    }

    with ZipFile(BytesIO(first.pptx)) as package:
        assert package.namelist() == sorted(package.namelist())
        assert {entry.date_time for entry in package.infolist()} == {
            (1980, 1, 1, 0, 0, 0)
        }


def test_native_pptx_keeps_text_editable_and_geometry_at_96_dpi():
    compilation = compile_template_v2_pptx(
        [(_golden_layout(), _golden_content())]
    )
    presentation = Presentation(BytesIO(compilation.pptx))
    slide = presentation.slides[0]

    assert presentation.core_properties.subject == (
        TEMPLATE_V2_NATIVE_PPTX_SCHEMA_VERSION
    )
    assert presentation.core_properties.author == (
        TEMPLATE_V2_NATIVE_PPTX_COMPILER_NAME
    )
    assert presentation.core_properties.comments == (
        f"{TEMPLATE_V2_NATIVE_PPTX_COMPILER_NAME}/"
        f"{TEMPLATE_V2_NATIVE_PPTX_COMPILER_VERSION}"
    )
    assert (presentation.slide_width, presentation.slide_height) == (
        12_192_000,
        6_858_000,
    )
    assert [shape.name for shape in slide.shapes] == [
        "hero/container-0",
        "hero/title",
        "hero/eyebrow",
        "hero/details/items",
    ]
    assert [shape.text for shape in slide.shapes if shape.has_text_frame] == [
        "",
        "Quarterly direction",
        "Native OOXML · editable text",
        "Plan\nBuild\nMeasure",
    ]
    title = slide.shapes[1]
    assert (title.left, title.top, title.width, title.height) == (
        914_400,
        838_200,
        10_363_200,
        857_250,
    )
    assert title.text_frame.paragraphs[0].runs[0].font.name == "Arial"
    assert title.text_frame.paragraphs[0].runs[0].font.size.pt == 32

    assert inspect_native_pptx_structure(
        compilation.pptx,
        layout_ids=["native-golden"],
    ) == compilation.manifest


def test_native_pptx_compiles_merged_table_as_editable_ooxml(tmp_path: Path):
    compilation = compile_template_v2_pptx([(_merged_table_layout(), {})])
    presentation = Presentation(BytesIO(compilation.pptx))
    shape = presentation.slides[0].shapes[0]

    assert shape.has_table
    assert shape.name == "table-panel/summary"
    assert len(shape.table.rows) == 2
    assert len(shape.table.columns) == 3
    anchor = shape.table.cell(0, 0)
    assert anchor.is_merge_origin
    assert (anchor.span_width, anchor.span_height) == (2, 2)
    assert anchor.text == "Merged"
    assert shape.table.cell(0, 1).is_spanned
    assert shape.table.cell(1, 0).is_spanned
    assert shape.table.cell(1, 1).is_spanned
    assert shape.table.cell(0, 1).text == "preserved while covered"
    assert shape.table.cell(1, 0).text == "preserved while covered"
    assert shape.table.cell(1, 1).text == "preserved while covered"
    assert shape.table.cell(0, 2).text == "H3"
    assert shape.table.cell(1, 2).text == "V3"

    manifest_table = compilation.manifest["slides"][0]["shapes"][0]["table"]
    assert manifest_table["row_count"] == 2
    assert manifest_table["column_count"] == 3
    assert manifest_table["rows"][0][0] == {
        "text": "Merged",
        "is_merge_origin": True,
        "is_spanned": False,
        "span_width": 2,
        "span_height": 2,
    }
    assert inspect_native_pptx_structure(
        compilation.pptx,
        layout_ids=["native-merged-table"],
    ) == compilation.manifest

    source = tmp_path / "native-merged-table.pptx"
    source.write_bytes(compilation.pptx)
    candidates = parse_presentation_candidates(
        PptxPackageReader(source),
        source_sha256=hashlib.sha256(compilation.pptx).hexdigest(),
    )
    draft = assemble_template_v2_draft(candidates)
    imported = draft.raw_layouts.layouts[0].elements[0]

    assert imported.columns[0].rowspan == 2
    assert imported.columns[0].colspan == 2
    assert [
        "".join(run.text for run in imported.columns[1].runs),
        "".join(run.text for run in imported.rows[0][0].runs),
        "".join(run.text for run in imported.rows[0][1].runs),
    ] == ["preserved while covered"] * 3
    for cell in (
        imported.columns[1],
        imported.rows[0][0],
        imported.rows[0][1],
    ):
        assert cell.covered_by.model_dump(mode="json") == {
            "row": 0,
            "column": 0,
        }


def test_native_compiler_fails_closed_for_unsupported_element():
    layout = SlideLayout.model_validate(
        {
            "id": "chart",
            "description": "Unsupported chart compiler contract",
            "components": [
                {
                    "id": "chart-panel",
                    "description": "Editable chart component content",
                    "position": {"x": 0, "y": 0},
                    "elements": [
                        {
                            "type": "chart",
                            "position": {"x": 80, "y": 80},
                            "size": {"width": 640, "height": 360},
                            "chart_type": "bar",
                            "categories": ["A"],
                            "series": [{"name": "Series", "values": [1]}],
                            "decorative": False,
                            "name": "chart",
                        }
                    ],
                }
            ],
        }
    )
    content = {
        "chart-panel": {
            "chart": {
                "chart_type": "bar",
                "categories": ["A"],
                "series": [{"name": "Series", "values": [1]}],
            }
        }
    }

    with pytest.raises(
        TemplateV2NativePptxCompileError,
        match="template_v2_native_pptx_element_unsupported",
    ) as error:
        compile_template_v2_pptx([(layout, content)])

    assert error.value.path.endswith(".type=chart")
