"""The slot contract shown to the authoring model for a figure slide.

The prose is deliberately about what the model may still decide — where the
figure sits, which icon each node carries, the few free text slots of a given
archetype — because everything else was decided by the plan and is handed
over as finished markup by ``skeleton.render_skeleton``.

The one thing the model must NOT be left to judge is how tall the figure's
container is. Measured on 203 real figure slides, the box the model chose
clustered just above the old ``at least 700x380px`` floor (median 1102x442 on
a canvas whose content line is 648px or 688px down), and the surplus became a
blank band at the foot of the page: the figure's own lowest text ended at
y=612 on the median slide. Nothing else in the prompt states a LOWER bound
down the page — the design system only states the upper one ("must end at or
above y=648px") — so the floor was read as the target. The contract therefore
states the bottom line in pixels and the arithmetic that reaches it.

That rule assumes the archetype CAN honestly reach the line. A few cannot: a
KPI row, a gauge dial, a short table, a short list or a short timeline have an
intrinsic content height well under 430px at 3-6 nodes, and telling the model
to reach the line anyway just moves the blank band inside the figure instead
of removing it. ``LOW_HEIGHT_MAX_PX`` names those archetypes and their honest
ceiling; ``_box_rule`` gives them a different instruction — hold the figure at
its ceiling and fill the rest of the content area with a text column beside
it, or centre the figure when there is nothing more to say.
"""

from __future__ import annotations

from models.authored_infographic_plan import AuthoredSlideInfographic

from utils.authored_infographic.icons import icon_reference_lines
from utils.authored_infographic.skeleton import render_skeleton

_ARCHETYPE_NOTES: dict[str, str] = {
    "process": "The arrows between the nodes are the empty .ig-arrow blocks; keep them in place.",
    "cycle": (
        "The ring and the .ig-ring-head arrowheads between the stations are drawn by the "
        "library; keep every one of them. The .ig-hub in the middle carries the takeaway. You may "
        "add one <div class=\"ig-hub-sub\"> line (<= 30 characters) after .ig-hub-title saying "
        "why the loop matters."
    ),
    "hub_spoke": "The first planned node is the hub; the spokes are the empty .ig-spoke elements.",
    "hierarchy": "The first planned node is the root; every other node is a child under the bar.",
    "layers": "Layers stack top to bottom in the planned order; the last layer is the foundation.",
    "funnel": "Bands narrow downward in the planned order; keep the f1..fN classes as given.",
    "pyramid": "Bands widen downward in the planned order; keep the f1..fN classes as given.",
    "timeline": ".ig-when is the date or period of each event; write it from CONTENT when the plan gave none.",
    "roadmap": (
        "Each phase has a bar: choose its offset class o0/o20/o40/o60/o80 and width class "
        "w20/w40/w60/w80/w100 from the durations in CONTENT so the bars read as a schedule "
        "(offset + width at most 100; the library clips anything longer)."
    ),
    "swimlane": (
        "Each lane's .ig-step chips are its steps; rewrite them from CONTENT (<= 8 characters each, "
        "at most 5 per lane) and keep one empty .ig-step-arrow between chips."
    ),
    "branch": "The first planned node is the decision; the others are its outcomes on the right.",
    "comparison": (
        "This is a real <table>: the header cells are the options. Add two to four criteria rows "
        "<tr data-qid-ignore=\"\"><th scope=\"row\" data-qid-ignore=\"\">criterion</th>"
        "<td data-qid-ignore=\"\">...</td>...</tr> to <tbody> from CONTENT, one <td> per option, "
        "and replace the CRITERION placeholder with the criterion the figures belong to. "
        "Mark the decisive cell with class is-key when CONTENT supports it. Every row and cell you "
        "add carries data-qid-ignore and no data-qid, and no cell holds an icon or an image."
    ),
    "before_after": (
        "This is a real <table>. Write the header words (ITEM, BEFORE, AFTER and, when present, "
        "DELTA) in the deck language as CONTENT names them; the row cells are already filled "
        "from the plan's before/after values. Keep every data-qid-ignore, and never put an icon "
        "or an image in a cell."
    ),
    "comparison_values": (
        "The BEFORE / AFTER row headers are placeholders: write them in the deck language as "
        "CONTENT names the two moments or measures."
    ),
    "kpi": "Each tile is one stated figure with its unit; never invent or round a number.",
    "gauge": (
        "Pick the dial class v10..v100 on .ig-dial-ring that matches the first node's share "
        "(v70 for 72%). The other nodes are the facts beside the dial."
    ),
    "icon_list": "Give every node its own icon; the labels stay exactly as planned.",
    "checklist": "Set each node to is-done or is-open as CONTENT states; the check mark is drawn by the library.",
    "matrix": (
        "Replace AXIS Y / AXIS X / LOW with the two axis names and their low end from CONTENT, "
        "replace each QUADRANT tag with a two- to four-word name, and move each .ig-node into the "
        ".ig-q quadrant CONTENT places it in (q-tr is high/high)."
    ),
    "venn": "The circles are drawn by the library; the .ig-core or the fourth node names the overlap.",
    "scene": (
        "Write the img data-illust-prompt exactly as the ILLUSTRATION SLOT rules require (an English "
        "SCENE of the real object CONTENT names, labels verbatim from CONTENT), keep its px "
        "width/height, and place each .ig-co callout and its .ig-pin around the object by editing "
        "only their left/top values. Keep those as PERCENTAGES of the figure, as given: the figure "
        "box is yours to choose and a pixel offset that fits a wide box runs off a narrow one."
    ),
}


#: The lowest y a figure container may stop at, per page-number mode. These are
#: the lines ``author_slide_rules`` already states to the model: a numbered deck
#: reserves a 72px footer (content ends at or above y=648) and an unnumbered one
#: keeps a 32px bottom margin (y=688). ``companyStandard`` numbering is composed
#: into the same 72px band after rendering, so it shares the numbered line.
CONTENT_BOTTOM_PX_BY_PAGE_NUMBER_MODE: dict[str, int] = {
    "template": 648,
    "companyStandard": 648,
    "none": 688,
}

#: What the contract falls back to when a caller states no mode: the higher of
#: the two lines, so a figure sized against it can never cross the other.
DEFAULT_CONTENT_BOTTOM_PX = 648


def content_bottom_px(page_number_mode: str | None) -> int:
    """The bottom content line a figure on this deck has to reach."""
    return CONTENT_BOTTOM_PX_BY_PAGE_NUMBER_MODE.get(
        page_number_mode or "", DEFAULT_CONTENT_BOTTOM_PX
    )


#: Archetypes whose figure has an honest ceiling well under the 430px floor
#: that applies to every other archetype, so "reach the bottom line" would
#: either inflate the figure with blank interior padding or leave the exact
#: blank band the floor exists to remove. Each value is the tallest the
#: archetype's own CSS packs its content to at 3-6 nodes — computed from each
#: node's stack of padding + inter-child gap + font line-heights, not guessed:
#:
#: * ``kpi`` — ``.ig-kpi .ig-node`` (css_data.py KPI_CSS) is a column of
#:   icon(46) + label + optional desc + the ``.ig-figure`` number line, padded
#:   26px top/bottom with 10px gaps; the tallest combination lands at the n3/n4
#:   node count (52-56px numerals before the n5/n6 shrink rules apply), ~235px
#:   of content plus padding.
#: * ``gauge`` — the dial itself is a fixed ``320x320px`` circle
#:   (``.ig-gauge .ig-dial``, css_data.py GAUGE_CSS); it is centred by
#:   ``margin:auto`` rather than stretched, so 320px is the ceiling regardless
#:   of node count.
#: * ``before_after`` / ``comparison`` — real ``<table>`` rows (css_data.py
#:   TABLE_CSS): at the archetype note's own 3-row example, one header row
#:   plus 3 body rows at the n3 padding (20px/22px) and font (20px, line-height
#:   1.4) sum to roughly 4 x 70px.
#: * ``icon_list`` — ``.ig-icon-list .ig-node`` (css_list.py ICON_LIST_CSS) is
#:   icon(64) + label + optional desc, padded 26px with 12px gaps; ~195px at
#:   the n3 node count.
#: * ``checklist`` — ``.ig-checklist .ig-node`` (css_list.py CHECKLIST_CSS) is
#:   a row of 3 stacked items, each padded 12px with a check/icon/text cluster
#:   and a 10px gap between items; ~260px for 3 rows.
#: * ``timeline`` — ``.ig-milestones .ig-node`` (css_flow.py) anchors its dot
#:   near the rail (``top:62px``) and its date/label cluster below it; packed
#:   without the auto-margin stretch that pushes them apart, ~190px.
LOW_HEIGHT_MAX_PX: dict[str, int] = {
    "kpi": 240,
    "gauge": 320,
    "before_after": 280,
    "comparison": 280,
    "icon_list": 200,
    "checklist": 260,
    "timeline": 190,
}


def _box_rule(slide: AuthoredSlideInfographic, bottom: int) -> str:
    """How tall the figure's container is, as arithmetic rather than a floor."""
    ceiling = LOW_HEIGHT_MAX_PX.get(slide.archetype)
    if ceiling is not None:
        return (
            f"This archetype has an intrinsic height ceiling: give its container an "
            f"explicit width and a height of exactly {ceiling}px — the honest maximum "
            f"a {slide.archetype} figure reaches at this node count without padding "
            "itself with blank interior space. Do NOT stretch it toward "
            f"y={bottom}px or down to the 430px floor that applies to other "
            "archetypes: an inflated figure is the same defect as a blank band, just "
            "moved inside the box instead of under it. Spend the rest of the content "
            f"area, down to y={bottom}px, one of two ways: (a) place a text column of "
            "its own BESIDE the figure so the two together — the figure in the wider "
            "column — fill the full content width and reach the bottom line; or (b), "
            "when this slide has nothing more to say, centre the figure vertically in "
            "the content area with the takeaway above it, leaving the remaining space "
            "as plain margin — never as an invented caption or a spacer manufactured "
            "to fill it. The figure fills the box you give it: put no width, height, "
            "margin or position on the figure's own root element — the library sizes "
            "the root to 100% of its container, so a margin on the root pushes the "
            "figure past the canvas edge.\n"
        )
    reach = (
        "the whole content area under the header"
        if slide.weight == "hero"
        else "everything the header, the takeaway and any side text leave"
    )
    return (
        "Give its container an explicit width and height in px, computed for THIS page "
        "rather than chosen from habit: the width is your full content width, and the "
        f"height is {bottom} minus the container's own top. The container takes {reach}, "
        f"and its bottom edge lands ON y={bottom}px — this deck's bottom content line — "
        "not somewhere above it. A container that stops short of that line leaves a blank "
        "band across the foot of the slide, and that band is a defect rather than "
        "restraint: it is the most common failure on figure pages. Measured on real "
        "slides, the median figure box stopped 76px above the line and the figure's own "
        "lowest text 108px above it. Never make the box shorter than 430px, and never "
        "fill a shortfall with an empty spacer, an invented caption, or a taller gap "
        "above the figure. The figure fills that box: size and place the container you "
        "wrap it in, and put no width, height, margin or position on the figure's own "
        "root element — the library sizes the root to 100% of its container, so a margin "
        "on the root pushes the figure past the canvas edge.\n"
    )


def slot_contract(
    slide: AuthoredSlideInfographic, *, page_number_mode: str | None = None
) -> str:
    """The figure contract for one planned figure slide."""
    skeleton = render_skeleton(slide)
    note = _ARCHETYPE_NOTES[slide.archetype]
    if slide.archetype == "comparison" and any(n.before or n.after for n in slide.nodes):
        note += " " + _ARCHETYPE_NOTES["comparison_values"]
    return (
        "FIGURE CONTRACT (the figure below is finished markup; reproduce it, do not redesign it):\n"
        "1. Paste the following figure into the slide body EXACTLY as given — every element, class, "
        "data-qid, data-native, data-qid-ignore and text — at the place your layout reserves for it. "
        + _box_rule(slide, content_bottom_px(page_number_mode))
        +
        "2. The figure's stylesheet is injected by the server. Do NOT write CSS for any .ig* class, "
        "do NOT add a class the library does not define, and do NOT put a style attribute or a "
        "<style> element inside the figure (the only exception: left/top on .ig-co / .ig-pin, "
        "kept in the percentages the skeleton gives, and the px size of a scene img).\n"
        "3. You MAY: swap any icon inside an .ig-ic for another from the ICON MENU; rewrite the "
        "UPPERCASE placeholder words in the deck language; and do what the ARCHETYPE NOTE allows. "
        "Every other text is verbatim from the plan — never reword, translate, add or drop a label.\n"
        "4. Everything outside the figure (header, eyebrow, page number, any supporting text) is "
        "yours to author under the design system as usual, but no other diagram, card grid or "
        "chart may compete with the figure.\n"
        f"ARCHETYPE NOTE ({slide.archetype}): {note}\n"
        "FIGURE MARKUP:\n"
        f"{skeleton}\n"
        "ICON MENU (paste one <svg> verbatim inside an .ig-ic; no fill/stroke/class/style attributes):\n"
        f"{icon_reference_lines()}"
    )


__all__ = [
    "CONTENT_BOTTOM_PX_BY_PAGE_NUMBER_MODE",
    "DEFAULT_CONTENT_BOTTOM_PX",
    "LOW_HEIGHT_MAX_PX",
    "content_bottom_px",
    "slot_contract",
]
