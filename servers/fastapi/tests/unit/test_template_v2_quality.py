from copy import deepcopy

import pytest

from templates.v2.quality import (
    TemplateV2QualityError,
    apply_template_v2_quality_preview,
    inspect_template_v2_quality,
    preview_template_v2_quality_fixes,
)


def _cell(value: str) -> dict:
    return {"runs": [{"text": value}]}


def _layouts() -> dict:
    columns = [_cell(f"Column {index}") for index in range(9)]
    return {
        "layouts": [
            {
                "id": "quality-slide",
                "description": "A strict quality inspection fixture",
                "vendor_layout_metadata": {"keep": ["exactly", 1]},
                "components": [
                    {
                        "id": "content",
                        "description": "Quality inspection content component",
                        "position": {"x": 0, "y": 0},
                        "vendor_component_metadata": {"keep": True},
                        "elements": [
                            {
                                "type": "text",
                                "name": "title",
                                "decorative": False,
                                "runs": [
                                    {
                                        "text": "This title is deliberately long",
                                        "font": {
                                            "size": 7,
                                            "color": "#777777",
                                            "vendor_font_token": "title-muted",
                                        },
                                    }
                                ],
                                "fill": {"color": "#888888"},
                                "max_length": 10,
                                "min_length": 1,
                                "vendor_element_metadata": {"keep": "yes"},
                            },
                            {
                                "type": "chart",
                                "name": "trend",
                                "decorative": False,
                                "chart_type": "line",
                                "categories": ["Q1", "Q2"],
                                "series": [
                                    {"name": "Actual", "values": [1, 2]},
                                    {"name": "Plan", "values": [2, 3]},
                                ],
                                "legend": False,
                            },
                            {
                                "type": "table",
                                "name": "wide-table",
                                "decorative": False,
                                "columns": columns,
                                "rows": [deepcopy(columns)],
                                "max_columns": 10,
                                "min_columns": 1,
                                "max_rows": 10,
                                "min_rows": 1,
                            },
                            {
                                "type": "image",
                                "name": "legacy-render",
                                "decorative": False,
                                "data": "/app_data/images/legacy.png",
                                "is_icon": False,
                                "raster_only": True,
                                "compatibility": {
                                    "unsupported_reason": "legacy_effect"
                                },
                            },
                        ],
                    }
                ],
            }
        ],
        "vendor_envelope_metadata": {"keep": {"nested": True}},
    }


def test_quality_inspection_is_deterministic_and_never_mutates_source():
    source = _layouts()
    original = deepcopy(source)

    first = inspect_template_v2_quality(source)
    second = inspect_template_v2_quality(source)

    assert first == second
    assert source == original
    assert [finding.reason_code for finding in first.findings] == [
        "TEXT_OVERFLOW",
        "TEXT_LOW_CONTRAST",
        "TEXT_BELOW_9PT",
        "CHART_LEGEND_MISSING",
        "CHART_UNIT_UNSPECIFIED",
        "TABLE_TOO_MANY_COLUMNS",
        "ELEMENT_RASTER_ONLY",
        "ELEMENT_UNSUPPORTED",
    ]


def test_quality_fixes_require_preview_and_revision_cas_and_preserve_unknowns():
    source = _layouts()
    inspection = inspect_template_v2_quality(source)
    preview = preview_template_v2_quality_fixes(source, inspection)

    assert [patch.reason_code for patch in preview.patches] == [
        "TEXT_LOW_CONTRAST",
        "TEXT_BELOW_9PT",
        "CHART_LEGEND_MISSING",
    ]
    assert source == _layouts()

    result = apply_template_v2_quality_preview(
        source,
        preview,
        expected_revision=7,
        current_revision=7,
    )
    title = result.layouts["layouts"][0]["components"][0]["elements"][0]
    chart = result.layouts["layouts"][0]["components"][0]["elements"][1]

    assert result.revision == 8
    assert title["runs"][0]["font"]["size"] == 9
    assert title["runs"][0]["font"]["color"] == "#000000"
    assert chart["legend"] is True
    assert title["vendor_element_metadata"] == {"keep": "yes"}
    assert title["runs"][0]["font"]["vendor_font_token"] == "title-muted"
    assert result.layouts["vendor_envelope_metadata"] == {
        "keep": {"nested": True}
    }

    with pytest.raises(
        TemplateV2QualityError,
        match="template_v2_quality_stale_revision",
    ):
        apply_template_v2_quality_preview(
            source,
            preview,
            expected_revision=6,
            current_revision=7,
        )


def test_quality_preview_fails_closed_when_source_changes_after_inspection():
    source = _layouts()
    inspection = inspect_template_v2_quality(source)
    source["layouts"][0]["vendor_layout_metadata"]["keep"].append("changed")

    with pytest.raises(
        TemplateV2QualityError,
        match="template_v2_quality_inspection_stale",
    ):
        preview_template_v2_quality_fixes(source, inspection)
