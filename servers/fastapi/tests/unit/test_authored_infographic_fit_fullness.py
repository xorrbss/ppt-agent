"""The fullness reading of the offline fit sweep, without a browser.

``scripts/authored_infographic_fit_fullness.py`` decides whether a rendered
figure uses the box it was given: whether a card is a hollow box (``card_slack``)
and whether the figure's content spans the box at all (``box_use``). Both are
arithmetic over one capture's element list, so a wrong reading is caught here
rather than after a few hundred renders have been spent believing it.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import authored_infographic_fit_fullness as fullness  # noqa: E402


def element(qid: str, y: float, height: float, **extra) -> dict:
    """One measured element, 100px wide at x=0 unless told otherwise."""
    item = {
        "qid": qid,
        "tag": extra.pop("tag", "div"),
        "bbox": {"x": extra.pop("x", 0.0), "y": y, "width": extra.pop("width", 100.0), "height": height},
    }
    item.update(extra)
    return item


def test_text_a_node_owns_is_content():
    assert fullness.is_content(element("ig.s4.n1.label", 10, 20, owned_text="치명 결함"))


def test_whitespace_is_not_text():
    assert not fullness.is_content(element("ig.s4.n1.label", 10, 20, owned_text="  \n "))


def test_the_plate_behind_a_card_is_not_content():
    """``.ig-node-bg`` is the card's own height, so counting it would let every
    empty card prove itself full."""
    assert not fullness.is_content(element("ig.s4.n1.bg", 0, 300))


def test_the_figure_root_is_not_content():
    assert not fullness.is_content(element("ig.s4.root", 0, 500))


def test_an_image_is_content_without_owning_text():
    assert fullness.is_content(element("ig.s4.art.img", 0, 200, tag="img"))


def test_a_native_table_is_content():
    assert fullness.is_content(element("ig.s4.table", 0, 400, tag="table"))


def test_a_zero_sized_element_is_not_content():
    assert not fullness.is_content(element("ig.s4.n1.label", 10, 0, owned_text="x"))


def test_a_cards_own_graphic_slot_is_content():
    """A roadmap row's phase track and a checklist badge are what the row
    draws; a card holding one is not empty even with a single line of text."""
    assert fullness.is_content(element("ig.s4.n1.track", 10, 60))
    assert fullness.is_content(element("ig.s4.n1.check", 10, 40))
    assert fullness.is_content(element("ig.s4.n1.ic", 10, 40))


def test_a_figure_level_wrapper_of_the_same_name_is_not():
    """``ig.s4.track`` is the box the whole timeline is laid out in. Counting
    it would make every timeline report a full box whatever it holds."""
    assert not fullness.is_content(element("ig.s4.track", 0, 500))


def test_overlapping_boxes_are_counted_once():
    boxes = [(0.0, 0.0, 10.0, 10.0), (5.0, 0.0, 10.0, 10.0)]
    assert fullness.union_area(boxes) == 150.0
    assert fullness.union_area([]) == 0.0


def _capture(card_height: float, text_top: float, text_height: float) -> list[dict]:
    return [
        element("ig.s4.root", 0, 500),
        element("ig.s4.n1", 0, card_height),
        element("ig.s4.n1.bg", 0, card_height),
        element("ig.s4.n1.label", text_top, text_height, owned_text="치명 결함"),
    ]


def test_a_card_that_hugs_its_content_has_no_slack():
    full = fullness.measure(_capture(60, 10, 40), "ig.s4.", 500, 500 * 100)
    assert full.card_slack == 1.5
    assert full.flags == ("slack", "box_use")


def test_a_card_filled_by_its_content_passes_both_bars():
    # 460 of content in a 500px box, inside a card 480 tall: 1.04 and 0.92.
    full = fullness.measure(_capture(480, 10, 460), "ig.s4.", 500, 500 * 1000)
    assert full.card_slack < fullness.CARD_SLACK_LIMIT
    assert full.box_use > fullness.BOX_USE_FLOOR
    assert full.flags == ()
    assert not full.failed


def test_box_use_spans_the_topmost_and_bottom_most_content():
    elements = [
        element("ig.s4.n1", 0, 100),
        element("ig.s4.n1.label", 20, 30, owned_text="a"),
        element("ig.s4.n2", 400, 100),
        element("ig.s4.n2.label", 420, 30, owned_text="b"),
    ]
    full = fullness.measure(elements, "ig.s4.", 500, 500 * 100)
    assert full.box_use == round((450 - 20) / 500, 3)


def test_only_this_figures_elements_are_read():
    elements = [
        element("ig.s4.n1", 0, 100),
        element("ig.s4.n1.label", 20, 30, owned_text="a"),
        element("s4.hero.claim", 0, 500, owned_text="a whole slide"),
    ]
    assert fullness.measure(elements, "ig.s4.", 500, 50000).box_use == 0.06


def test_a_card_with_no_content_of_its_own_is_not_a_division_by_zero():
    elements = [element("ig.s4.n1", 0, 100), element("ig.s4.n1.bg", 0, 100)]
    assert fullness.measure(elements, "ig.s4.", 500, 50000).card_slack == 0.0


def test_ink_fill_is_the_content_area_over_the_box():
    elements = [
        element("ig.s4.n1", 0, 100),
        element("ig.s4.n1.label", 0, 50, width=100, owned_text="a"),
    ]
    assert fullness.measure(elements, "ig.s4.", 500, 1000).ink_fill == 5.0


def test_the_worst_reading_of_each_archetype_and_box_is_reported():
    rows = [
        {"archetype": "funnel", "nodes": 3, "box": "1138x500", "card_slack": 1.1,
         "slack_qid": "ig.s4.n1", "box_use": 0.9, "ink_fill": 0.3, "full": True},
        {"archetype": "funnel", "nodes": 3, "box": "1138x500", "card_slack": 2.4,
         "slack_qid": "ig.s4.n2", "box_use": 0.6, "ink_fill": 0.2, "full": False},
        {"archetype": "funnel", "nodes": 4, "box": "1138x500", "card_slack": 1.0,
         "slack_qid": "", "box_use": 0.95, "ink_fill": 0.4, "full": True},
    ]
    worst = fullness.worst_per_archetype(rows)
    assert [row["nodes"] for row in worst] == [3, 4]
    assert worst[0] == {
        "archetype": "funnel", "nodes": 3, "box": "1138x500", "card_slack": 2.4,
        "slack_qid": "ig.s4.n2", "box_use": 0.6, "ink_fill": 0.2, "flagged": 1,
    }
