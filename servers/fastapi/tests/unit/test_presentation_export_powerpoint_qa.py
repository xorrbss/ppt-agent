from services.presentation_export_powerpoint_qa import (
    _contains_presenton_qid,
    _powerpoint_main_hwnd,
    _snapshot_comparison,
)


class _Application:
    Caption = ""


class _WindowApi:
    def __init__(self, application):
        self.application = application

    def EnumWindows(self, callback, context):
        callback(101, context)
        callback(202, context)

    @staticmethod
    def GetClassName(hwnd):
        return "PPTFrameClass" if hwnd == 101 else "OtherWindow"

    def GetWindowText(self, hwnd):
        return self.application.Caption if hwnd == 101 else ""


def test_powerpoint_qid_owner_and_interactive_hwnd_contracts():
    assert _contains_presenton_qid("Presenton hybrid text q-title", "q-title")
    assert _contains_presenton_qid(
        "Presenton hybrid shape q-badge-container", "q-badge"
    )
    assert _contains_presenton_qid(
        "Presenton hybrid shape q-root top border", "q-root"
    )
    assert not _contains_presenton_qid(
        "Presenton raster visual fragment 5", "q-badge"
    )
    application = _Application()
    assert _powerpoint_main_hwnd(application, _WindowApi(application)) == 101


def _shape(**overrides):
    value = {
        "shapeIndex": 1,
        "name": "Title 1",
        "type": 17,
        "left": 72.0,
        "top": 48.0,
        "width": 640.0,
        "height": 48.0,
        "hasText": True,
        "hasTable": False,
        "hasChart": False,
        "text": "첫 줄\n둘째 줄",
        "lineBreakCount": 1,
        "latinTypeface": "Malgun Gothic",
        "eastAsianTypeface": "Malgun Gothic",
        "table": None,
        "chart": None,
        "crop": None,
    }
    value.update(overrides)
    return value


def test_snapshot_comparison_preserves_geometry_line_breaks_and_typefaces():
    first = [[_shape()]]
    assert _snapshot_comparison(first, [[_shape()]]) == {
        "shape_count_changes": 0,
        "object_identity_changes": 0,
        "geometry_changes_gt_5pt": 0,
        "geometry_changes_gt_10pt": 0,
        "text_changes": 0,
        "line_break_changes": 0,
        "typeface_changes": 0,
        "table_content_changes": 0,
        "chart_content_changes": 0,
        "crop_changes": 0,
        "readback_match": True,
    }
    changed = _snapshot_comparison(
        first,
        [[_shape(left=83.0, text="첫 줄", lineBreakCount=0, eastAsianTypeface="Batang")]],
    )
    assert changed["geometry_changes_gt_5pt"] == 1
    assert changed["geometry_changes_gt_10pt"] == 1
    assert changed["text_changes"] == 1
    assert changed["line_break_changes"] == 1
    assert changed["typeface_changes"] == 1
    assert changed["readback_match"] is False


def test_snapshot_comparison_detects_native_object_content_and_crop_changes():
    table = {"rows": 1, "columns": 1, "cells": [[{"text": "A"}]]}
    chart = {"chartType": 51, "series": [{"name": "S", "values": [1]}]}
    crop = {"leftPt": 4.0, "topPt": 0.0, "rightPt": 0.0, "bottomPt": 0.0}
    first = [[_shape(hasTable=True, table=table, chart=chart, crop=crop)]]
    changed = _snapshot_comparison(
        first,
        [[
            _shape(
                name="renamed",
                hasTable=True,
                table={**table, "cells": [[{"text": "B"}]]},
                chart={**chart, "series": [{"name": "S", "values": [2]}]},
                crop={**crop, "leftPt": 5.0},
            )
        ]],
    )
    assert changed["object_identity_changes"] == 1
    assert changed["table_content_changes"] == 1
    assert changed["chart_content_changes"] == 1
    assert changed["crop_changes"] == 1
    assert changed["readback_match"] is False
