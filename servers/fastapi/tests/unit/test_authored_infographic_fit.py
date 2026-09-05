"""The library rules that make a figure fit the box it was given.

The defect these pin: a six-node ``roadmap`` was handed a 1138x386 box and
drew 548px of rows in it. ``.ig-roadmap`` stacks its rows in a column flex and
a flex item's default ``min-height:auto`` refuses to shrink below its content,
so the rows simply kept their intrinsic height, row six landed at y=667..744,
its phrase was clipped by the canvas and the whole deck was terminated by the
gate (``browser_text_not_visible``, ``safety:off_canvas``).

Fitting is achieved by three things, and all three are checked here without a
browser:

* every ``.ig-node`` is shrinkable (``min-height:0``), including the row
  archetypes that state their own row height;
* the ``n5``/``n6`` size classes state a compaction — padding, gaps,
  connector heights, icon sizes, line-height — that lets five or six parts
  share the shortest box a figure is given;
* no declared ``font-size`` is under 15px, because the pipeline clamps the
  whole document to that floor before the first render, so a smaller value is
  a fit that no browser would honour.

What is deliberately NOT a way to fit: hiding or truncating planned text. No
label, phrase or figure slot may carry ``overflow:hidden`` or ``display:none``.
The rendered proof is ``scripts/authored_infographic_fit_sweep.py``.
"""

import re
from types import SimpleNamespace

import pytest

from utils.authored_infographic import library_css

#: Archetypes whose nodes are rows stacked down the figure -- the shape that
#: overflowed, and the shape whose rows must be able to shrink.
STACKED_ROOTS = ("roadmap", "swimlane", "layers", "checklist", "funnel", "pyramid")
#: Slots that carry planned text and may therefore never be clipped away.
TEXT_SLOTS = (".ig-label", ".ig-desc", ".ig-num", ".ig-unit", ".ig-hub-title", ".ig-hub-sub")

_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")


def _resolved(css: str) -> dict[str, dict[str, str]]:
    """Declarations per literal selector, later rules winning, as authored."""
    out: dict[str, dict[str, str]] = {}
    for selectors, body in _RULE.findall(css):
        declarations = {}
        for piece in body.split(";"):
            name, _, value = piece.partition(":")
            if value:
                declarations[name.strip()] = value.strip()
        for selector in " ".join(selectors.split()).split(","):
            out.setdefault(selector.strip(), {}).update(declarations)
    return out


@pytest.fixture(scope="module")
def rules() -> dict[str, dict[str, str]]:
    brand = SimpleNamespace(topic="스마트팩토리", primary="#1E5EFF", fonts="Pretendard")
    return _resolved(library_css(brand))


def test_every_node_can_shrink_below_its_content(rules):
    assert rules[".ig-node"]["min-height"] == "0"
    assert rules[".ig-node"]["min-width"] == "0"


@pytest.mark.parametrize("root", STACKED_ROOTS)
@pytest.mark.parametrize("size", ["n5", "n6"])
def test_a_stacked_row_shrinks_at_five_and_six_nodes(rules, root, size):
    """The row archetypes restate their own row height, so they restate 0 too."""
    assert rules[f".ig-{root}.{size} .ig-node"]["min-height"] == "0"


@pytest.mark.parametrize("root", STACKED_ROOTS)
def test_a_stacked_root_gives_its_vertical_padding_back(rules, root):
    for size in ("n5", "n6"):
        declarations = rules[f".ig-{root}.{size}"]
        assert declarations["padding-top"] == "0"
        assert declarations["padding-bottom"] == "0"
    assert rules[f".ig-{root}.n5"]["gap"] == "8px"
    assert rules[f".ig-{root}.n6"]["gap"] == "6px"


#: The n6 compaction, pinned. These are the numbers the rendered sweep was
#: driven to zero with; changing one is changing what fits. The two ``auto``
#: entries are parts ``css_fill`` sizes from the row they sit in instead of
#: from the size class -- they shrink in a short box exactly as a fixed value
#: did, and grow in a tall one, which a fixed value could not.
N6_RULES = [
    (".ig.n6 .ig-node", "padding", "10px 12px"),
    (".ig.n6 .ig-node", "gap", "4px"),
    (".ig.n6 .ig-label", "font-size", "18px"),
    (".ig.n6 .ig-label", "line-height", "1.15"),
    (".ig.n6 .ig-desc", "font-size", "15px"),
    (".ig.n6 .ig-desc", "line-height", "1.2"),
    (".ig.n6 .ig-num", "font-size", "32px"),
    (".ig.n6 .ig-ic", "width", "34px"),
    (".ig-roadmap.n6 .ig-node", "padding", "7px 16px"),
    (".ig-roadmap.n6 .ig-head", "width", "300px"),
    (".ig-roadmap.n6 .ig-track", "height", "auto"),
    (".ig-swimlane.n6 .ig-step", "height", "28px"),
    (".ig-checklist.n6 .ig-check", "width", "auto"),
    (".ig-matrix.n6 .ig-q", "min-height", "0"),
    (".ig-gauge.n6 .ig-facts .ig-node", "padding", "6px 12px"),
    (".ig-cycle.n6 .ig-node", "width", "220px"),
    (".ig-branch.n6 .ig-outcomes .ig-node", "padding", "6px 14px"),
]


@pytest.mark.parametrize(("selector", "prop", "value"), N6_RULES)
def test_the_six_node_compaction_is_what_it_was_measured_at(rules, selector, prop, value):
    assert rules[selector][prop] == value


def test_a_branch_row_cannot_grow_past_the_figure(rules):
    """The grid row is the figure's height, not its content's.

    ``.ig-branch`` is a grid with no explicit row, so the implicit row was
    sized by the tallest column and the outcomes ran past the box below it.
    """
    assert rules[".ig-branch"]["grid-template-rows"] == "minmax(0,1fr)"


def test_a_hierarchy_child_card_keeps_its_automatic_minimum(rules):
    """``min-height:0`` collapses it instead of making it share a box.

    A hierarchy card is a flex item of its own ``.ig-child`` column, and that
    column is sized by its content, so a shrinkable card collapses to nothing
    while its text stays where it was. Hierarchy fits by compaction instead.
    """
    assert rules[".ig-hierarchy .ig-child .ig-node"]["min-height"] == "auto"


#: Archetypes that place their parts absolutely used to state a fixed pixel
#: floor taller than the box a real slide reserves, which is how the parts
#: left the canvas. The floor that remains is below every observed box.
SHORTEST_OBSERVED_FIGURE_BOX = 373


@pytest.mark.parametrize(
    "selector", [".ig-cycle", ".ig-hub-spoke", ".ig-venn", ".ig-scene"]
)
def test_an_absolutely_placed_figure_does_not_outgrow_a_short_box(rules, selector):
    floor = rules[selector]["min-height"]
    assert floor.endswith("px")
    assert float(floor[:-2]) < SHORTEST_OBSERVED_FIGURE_BOX


def test_no_declared_font_size_is_below_the_pipeline_floor(rules):
    sizes = {
        (selector, value)
        for selector, declarations in rules.items()
        for prop, value in declarations.items()
        if prop == "font-size" and value.endswith("px")
    }
    too_small = {(s, v) for s, v in sizes if float(v[:-2]) < 15}
    assert not too_small, f"font sizes the 15px pipeline floor would rewrite: {sorted(too_small)}"


@pytest.mark.parametrize("slot", TEXT_SLOTS)
def test_planned_text_is_never_clipped_or_hidden_to_make_it_fit(rules, slot):
    for selector, declarations in rules.items():
        if not selector.endswith(slot):
            continue
        assert declarations.get("overflow") in (None, "visible"), selector
        assert declarations.get("display") != "none", selector
        assert declarations.get("text-overflow") is None, selector


#: The mirror image of the compaction above: the box the figure contract now
#: asks for is 430..560px tall, and a figure drawn at its intrinsic height sat
#: in the middle of it. ``css_fill`` is what makes it use the box, and these
#: are the rules a rendered sweep was driven to zero flags with.

#: Every archetype whose nodes are rows stacked down the figure.
FILL_ROOTS = ("layers", "funnel", "pyramid", "swimlane", "roadmap", "checklist")
#: Row archetypes that lead each row with an icon tile.
ICON_ROW_ROOTS = ("layers", "funnel", "pyramid", "swimlane")
#: Archetypes whose nodes are cards side by side.
COLUMN_ROOTS = ("process", "icon-list", "kpi")


@pytest.mark.parametrize("root", FILL_ROOTS)
@pytest.mark.parametrize("size", ["n3", "n4", "n5", "n6"])
def test_a_stacked_root_spreads_what_a_tall_box_leaves_over(rules, root, size):
    """Capped rows plus ``space-between``: the surplus goes between the rows,
    not into a white band above and below them."""
    assert rules[f".ig-{root}.{size}"]["justify-content"] == "space-between"
    assert rules[f".ig-{root}.{size} .ig-node"]["max-height"].endswith("px")


#: How tall a row may become before it stops being filled by what it holds.
ROW_CAPS = [
    (".ig-layers.n3 .ig-node", "156px"),
    (".ig-funnel.n4 .ig-node", "134px"),
    (".ig-pyramid.n5 .ig-node", "112px"),
    (".ig-swimlane.n6 .ig-node", "94px"),
    (".ig-roadmap.n3 .ig-node", "116px"),
    (".ig-checklist.n6 .ig-node", "84px"),
]


@pytest.mark.parametrize(("selector", "cap"), ROW_CAPS)
def test_a_row_stops_where_its_content_ends(rules, selector, cap):
    assert rules[selector]["max-height"] == cap


@pytest.mark.parametrize("root", ICON_ROW_ROOTS)
def test_a_rows_icon_is_the_height_of_the_row_and_stays_square(rules, root):
    """The one part of a row that CAN follow the box: a font size cannot be a
    percentage of a height, a tile can."""
    for size in ("n3", "n4", "n5", "n6"):
        declarations = rules[f".ig-{root}.{size} .ig-node > .ig-ic"]
        assert declarations["align-self"] == "stretch"
        assert declarations["aspect-ratio"] == "1"
        assert declarations["width"] == "auto"
        assert declarations["height"] == "auto"


#: A filled row's slack is height / (height - inset), so the inset is what
#: decides whether a row that IS full still reads as one. These are the values
#: the sweep was driven to zero flags with.
ROW_INSETS = [("n3", "10px"), ("n4", "10px"), ("n5", "8px"), ("n6", "6px")]


@pytest.mark.parametrize(("size", "inset"), ROW_INSETS)
def test_a_rows_inset_is_small_enough_that_a_filled_row_reads_as_filled(rules, size, inset):
    declarations = rules[f".ig-layers.{size} .ig-node"]
    assert declarations["padding-top"] == inset
    assert declarations["padding-bottom"] == inset


def test_a_tile_never_takes_the_width_the_phrase_beside_it_needs(rules):
    """The guard that keeps a growing tile from wrapping a Korean phrase to one
    glyph per line in the narrowest box a real slide gives a figure (800px)."""
    assert rules[".ig-layers.n3 .ig-node > .ig-ic"]["max-width"] == "17%"


def test_a_checklist_badge_and_a_roadmap_track_grow_the_same_way(rules):
    """Those two rows carry no icon; what fills them is their own graphic."""
    check = rules[".ig-checklist.n3 .ig-check"]
    assert (check["align-self"], check["aspect-ratio"]) == ("stretch", "1")
    # One uniform pixel value the browser clamps to a circle at any size it
    # reaches, so the badge is round whatever height the row gives it.
    assert check["border-radius"] == "60px"
    assert rules[".ig-roadmap.n3 .ig-track"]["align-self"] == "stretch"
    assert rules[".ig-roadmap.n3 .ig-fill"]["top"] == "14%"


@pytest.mark.parametrize("root", COLUMN_ROOTS)
@pytest.mark.parametrize("size", ["n3", "n4", "n5", "n6"])
def test_a_column_card_takes_the_box_and_anchors_what_it_holds(rules, root, size):
    """The first answer to a card taller than its content was a cap, and a
    capped row still pooled the surplus around itself. The card takes the box;
    the icon grows into it and the text anchors to the bottom of it."""
    assert rules[f".ig-{root}.{size} .ig-node"]["max-height"] == "none"
    icon = rules[f".ig-{root}.{size} .ig-ic"]
    assert icon["flex"] == "1 1 auto"
    assert icon["aspect-ratio"] == "1"
    assert icon["max-height"].endswith("px")
    assert rules[f".ig-{root}.{size} .ig-label"]["margin-top"] == "auto"
    assert rules[f".ig-{root}.{size} .ig-figure"]["margin-top"] == "auto"


#: A square tile runs into the card's width first: a six-card row is about
#: 175px wide, a three-card process row 280px.
COLUMN_ICON_CAPS = [("n3", "196px"), ("n4", "156px"), ("n5", "124px"), ("n6", "96px")]


@pytest.mark.parametrize(("size", "cap"), COLUMN_ICON_CAPS)
def test_a_column_icon_stops_before_it_outgrows_its_card(rules, size, cap):
    assert rules[f".ig-process.{size} .ig-ic"]["max-height"] == cap


#: The type scale of a card that now has a box to fill, unchanged by the fill
#: rules and shared with the archetypes that grew with it.
COLUMN_TYPE = [
    (".ig-process.n3 .ig-label", "font-size", "28px"),
    (".ig-process.n5 .ig-label", "font-size", "24px"),
    (".ig-process.n6 .ig-label", "font-size", "22px"),
    (".ig-icon-list.n3 .ig-desc", "font-size", "19px"),
    (".ig-icon-list.n5 .ig-desc", "font-size", "18px"),
    (".ig-icon-list.n6 .ig-desc", "font-size", "17px"),
    (".ig-kpi.n3 .ig-num", "font-size", "60px"),
    (".ig-process.n3 .ig-num", "font-size", "52px"),
    (".ig-process.n5 .ig-num", "font-size", "46px"),
    (".ig-process.n6 .ig-num", "font-size", "40px"),
]


@pytest.mark.parametrize(("selector", "prop", "value"), COLUMN_TYPE)
def test_the_column_type_scale_is_what_it_was_measured_at(rules, selector, prop, value):
    assert rules[selector][prop] == value


def test_the_two_decks_of_a_hierarchy_share_the_box(rules):
    """The children row takes what the root plate does not, each card is capped
    so the surplus lengthens the stem that connects it, and the plate grows too
    so an org chart does not become one plate over a corridor of white."""
    assert rules[".ig-hierarchy.n3 .ig-children"]["flex"] == "1 1 0"
    assert rules[".ig-hierarchy.n3 .ig-children .ig-node"]["height"] == "100%"
    assert rules[".ig-hierarchy.n3 .ig-children .ig-node"]["max-height"] == "250px"
    assert rules[".ig-hierarchy.n6 .ig-children .ig-node"]["max-height"] == "196px"
    assert rules[".ig-hierarchy.n3 .ig-children .ig-stem"]["flex"] == "1 1 auto"
    assert rules[".ig-hierarchy.n3 .ig-child"]["justify-content"] == "flex-end"
    assert rules[".ig-hierarchy.n3 .ig-root"]["flex"] == "1 1 auto"
    assert rules[".ig-hierarchy.n3 .ig-root"]["max-height"] == "240px"


def test_a_milestone_keeps_its_dot_on_the_rail_and_anchors_its_phrase(rules):
    """The station cannot move -- the rail is drawn at a fixed offset in the
    figure's own box -- so what fills the card is where its text sits."""
    assert rules[".ig-milestones.n3 .ig-when"]["margin-top"] == "0"
    assert rules[".ig-milestones.n3 .ig-label"]["margin-top"] == "auto"
    assert rules[".ig-milestones.n3 .ig-node > :last-child"]["margin-bottom"] == "0"


def test_a_gauge_dial_follows_the_box_instead_of_a_fixed_320px(rules):
    dial = rules[".ig-gauge.n3 .ig-dial"]
    assert dial["height"] == "min(100%,360px)"
    assert dial["aspect-ratio"] == "1"
    assert dial["margin"] == "auto"
    # Its rings are stated as fractions of it, so they follow it.
    assert rules[".ig-gauge.n3 .ig-dial-ring"]["width"] == "100%"
    assert rules[".ig-gauge.n3 .ig-dial-hole"]["width"] == "78.8%"


def test_a_scene_callout_takes_the_height_of_what_it_holds(rules):
    """23% of a 560px box is a 129px plate around two lines of text."""
    assert rules[".ig-scene.n3 .ig-co"]["height"] == "auto"


def test_the_fill_rules_are_appended_after_the_compaction(rules):
    """Both state the same properties on the same parts; a cap that fills a
    tall box has to outrank the compaction that fits a short one."""
    from utils.authored_infographic.css_compact import COMPACT_CSS
    from utils.authored_infographic.css_fill import FILL_CSS
    from utils.authored_infographic.library import LIBRARY_TEMPLATE

    assert LIBRARY_TEMPLATE.index(COMPACT_CSS) < LIBRARY_TEMPLATE.index(FILL_CSS)
    assert LIBRARY_TEMPLATE.endswith(FILL_CSS)
