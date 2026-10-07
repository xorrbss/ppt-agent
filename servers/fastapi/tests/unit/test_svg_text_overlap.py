"""Pure decision tests for the svg ``text_overlap`` basis (glyph ink, else line box)."""

from __future__ import annotations

from services.svg_text_overlap import comparison_fragments, text_overlaps


def _rect(x, y, width, height):
    return {"x": x, "y": y, "width": width, "height": height}


# Two stacked lines: the line boxes intersect by 15 px, the ink is 9 px apart.
LINE_A, INK_A = _rect(80, 144, 410, 70), _rect(81, 159, 406, 46)
LINE_B, INK_B = _rect(80, 199, 291, 70), _rect(81, 214, 286, 46)


def _item(qid, line, ink="absent", parent=None):
    item = {"qid": qid, "parent_qid": parent, "text_fragments": line}
    if ink != "absent":
        item["text_ink_fragments"] = ink
    return item


def test_ink_boxes_decide_when_present():
    overlaps, basis = text_overlaps([_item("a", [LINE_A], [INK_A]), _item("b", [LINE_B], [INK_B])])
    assert overlaps == []
    assert basis["version"] == "glyph-ink/v3"
    assert basis["fragments"] == {"ink": 2, "ink_cell": 0, "line_box": 0}
    assert basis["pairs"] == []


def test_missing_ink_keeps_the_line_box_verdict():
    overlaps, basis = text_overlaps([_item("a", [LINE_A]), _item("b", [LINE_B])])
    assert overlaps == [["a", "b"]]
    assert basis["fragments"] == {"ink": 0, "ink_cell": 0, "line_box": 2}
    assert basis["pairs"] == [{"pair": ["a", "b"], "basis": "line_box"}]


def test_cell_fallback_keeps_fragment_ink_geometry_and_reports_its_basis():
    with_cell = {**INK_A, "cellClusters": 1}
    overlaps, basis = text_overlaps([_item("a", [LINE_A], [with_cell]),
                                     _item("b", [LINE_B], [INK_B])])
    assert overlaps == []
    assert comparison_fragments(_item("a", [LINE_A], [with_cell])) == [(INK_A, "ink_cell")]
    assert basis["fragments"] == {"ink": 1, "ink_cell": 1, "line_box": 0}
    colliding = {**_rect(81, 190, 286, 46), "cellClusters": 1}
    overlaps, basis = text_overlaps([_item("a", [LINE_A], [with_cell]),
                                     _item("b", [LINE_B], [colliding])])
    assert overlaps == [["a", "b"]]
    assert basis["pairs"] == [{"pair": ["a", "b"], "basis": "ink_cell"}]


def test_null_ink_entry_falls_back_per_fragment():
    overlaps, basis = text_overlaps([_item("a", [LINE_A], [None]), _item("b", [LINE_B], [None])])
    assert overlaps == [["a", "b"]]
    assert basis["pairs"][0]["basis"] == "line_box"


def test_mixed_basis_compares_ink_against_line_box():
    # A's line box reaches y=214; B's ink starts at 214, so they only touch.
    overlaps, _ = text_overlaps([_item("a", [LINE_A], [None]), _item("b", [LINE_B], [INK_B])])
    assert overlaps == []
    deeper = _rect(81, 200, 286, 46)
    overlaps, basis = text_overlaps([_item("a", [LINE_A], [None]), _item("b", [LINE_B], [deeper])])
    assert overlaps == [["a", "b"]]
    assert basis["pairs"] == [{"pair": ["a", "b"], "basis": "mixed"}]


def test_true_ink_collision_is_still_reported_on_ink():
    colliding = _rect(81, 190, 286, 46)
    overlaps, basis = text_overlaps([_item("a", [LINE_A], [INK_A]), _item("b", [LINE_B], [colliding])])
    assert overlaps == [["a", "b"]]
    assert basis["pairs"] == [{"pair": ["a", "b"], "basis": "ink"}]


def test_ink_array_not_aligned_with_fragments_is_ignored():
    item = _item("a", [LINE_A], [INK_A, INK_A])
    assert comparison_fragments(item) == [(LINE_A, "line_box")]


def test_malformed_ink_entries_fall_back():
    for bad in ({"x": 1}, {"x": 1, "y": 2, "width": 0, "height": 3},
                {"x": float("nan"), "y": 0, "width": 1, "height": 1}, "box", 3):
        assert comparison_fragments(_item("a", [LINE_A], [bad])) == [(LINE_A, "line_box")]


def test_mixed_fragments_within_one_element():
    second_line, second_ink = _rect(80, 300, 100, 70), _rect(81, 315, 98, 46)
    item = _item("a", [LINE_A, second_line], [None, second_ink])
    assert comparison_fragments(item) == [(LINE_A, "line_box"), (second_ink, "ink")]


def test_direct_parent_child_pair_is_never_compared():
    parent = _item("t", [LINE_A])
    child = _item("s", [LINE_A], parent="t")
    overlaps, basis = text_overlaps([parent, child])
    assert overlaps == [] and basis["pairs"] == []


def test_sibling_tspans_are_compared():
    first = _item("s1", [LINE_A], [INK_A], parent="t")
    second = _item("s2", [LINE_A], [INK_A], parent="t")
    assert text_overlaps([first, second])[0] == [["s1", "s2"]]


def test_threshold_is_half_a_pixel_on_both_axes():
    a = _item("a", [_rect(0, 0, 10, 10)], [_rect(0, 0, 10, 10)])
    touching = _item("b", [_rect(0, 9.5, 10, 10)], [_rect(0, 9.5, 10, 10)])
    assert text_overlaps([a, touching])[0] == []
    deeper = _item("b", [_rect(0, 9.4, 10, 10)], [_rect(0, 9.4, 10, 10)])
    assert text_overlaps([a, deeper])[0] == [["a", "b"]]
