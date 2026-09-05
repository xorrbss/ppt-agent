"""How a figure fills a box that is taller than the content it was drawn for.

``css_compact`` states how six parts fit the *shortest* box a figure is given.
This file states the mirror image: the figure contract now hands every figure a
container whose bottom edge lands on the deck's bottom content line, so the box
is 430..560px tall, and a figure drawn at its intrinsic height sits in the
middle of it with a white band above and below. Measured before this file
existed, at 1138x500: hierarchy used 47% of its box's height, a process card
was 2.3x the height of the content inside it, a roadmap row 4.7x.

Flat CSS cannot read the box (renderer isolation refuses at-rules, so there are
no container queries) and a font size cannot be a percentage of a height. So
the fill is achieved with the three things that *can* follow a box:

* a part that shares the box — ``flex:1 1 0`` rows, ``height:100%`` cards — is
  given a size class cap so it stops where its content genuinely ends, and the
  root spreads what is left between the parts (``justify-content:space-between``)
  rather than pooling it above and below them;
* the graphic inside a part grows with the part: an icon tile is
  ``align-self:stretch`` (row cards) or ``flex:1 1 auto`` (column cards) with
  ``aspect-ratio:1``, so it stays square while it takes the height the text
  cannot; the same for a checklist's badge and a roadmap's phase track. The
  matrix is the exception -- its quadrant is too narrow to give a tile that
  height without taking it from the phrase, so there the card is capped to
  what it holds instead;
* the text of a card is anchored rather than clustered — ``margin-top:auto``
  before the label and before the figure — so a tall card reads as icon at the
  top, phrase in the middle, number at the bottom instead of a small cluster
  floating in white.

Nothing here hides, truncates or shrinks planned text, and no font size is
declared below the pipeline's 15px floor. The caps are all *upper* bounds: in
the short boxes (1138x386, 800x400) every part is already under its cap, which
is why the compaction those boxes were tuned for still applies unchanged.

Appended last to the library template, after ``css_compact``, and every rule
states its own size class so it outranks the ``.ig.nX`` scale in ``css_base``.
"""

from __future__ import annotations

_SIZES = ("n3", "n4", "n5", "n6")

#: Row archetypes that carry an icon tile at the head of every row.
_ICON_ROWS = ("layers", "funnel", "pyramid", "swimlane")
#: Every archetype whose nodes are rows stacked down the figure.
_ROWS = (*_ICON_ROWS, "roadmap", "checklist")
#: Archetypes whose nodes are cards side by side across the figure.
_COLUMNS = ("process", "icon-list", "kpi")

#: A card's icon takes the height its text does not, and stays square.
_COLUMN_ICON = "flex:1 1 auto;min-height:0;height:auto;width:auto;aspect-ratio:1;max-width:100%"
#: A row's icon is the full height of the row it leads, and stays square -- but
#: never wider than a sixth of the row, because the phrase beside it is what
#: pays for the width. A pyramid's top band is half the figure wide, and at
#: 800x400 a fifth of it was one wrapped line too many.
_ROW_ICON = (
    "align-self:stretch;flex:none;min-height:0;height:auto;width:auto;aspect-ratio:1;max-width:17%"
)


def _rule(roots: tuple[str, ...], part: str, body: str, size: str) -> str:
    return ",".join(f".ig-{root}.{size}{part}" for root in roots) + f"{{{body}}}\n"


def _same(roots: tuple[str, ...], part: str, body: str) -> str:
    """One block per size class, so the rule outranks ``.ig.nX`` in css_base."""
    return "".join(_rule(roots, part, body, size) for size in _SIZES)


def _by_size(roots: tuple[str, ...], part: str, bodies: dict[str, str]) -> str:
    """A different block per size class."""
    return "".join(_rule(roots, part, bodies[size], size) for size in _SIZES)


def _scale(prop: str, n3: str, n4: str, n5: str, n6: str) -> dict[str, str]:
    return {"n3": f"{prop}:{n3}", "n4": f"{prop}:{n4}", "n5": f"{prop}:{n5}", "n6": f"{prop}:{n6}"}


def _inset(n3: str, n4: str, n5: str, n6: str) -> dict[str, str]:
    """Vertical padding per size class, leaving the horizontal one alone."""
    return {
        size: f"padding-top:{value};padding-bottom:{value}"
        for size, value in zip(_SIZES, (n3, n4, n5, n6), strict=True)
    }


#: How tall one stacked row may become. Roughly the tallest box (560px) divided
#: by the node count, so at five and six nodes the rows still fill it, and low
#: enough that the icon stretched inside the row is never a hollow tile: the
#: slack of a filled row is cap / (cap - 2 x its padding), about 1.2 at every
#: size class.
_ROW_CAP = _scale("max-height", "156px", "134px", "112px", "94px")
_ROW_ICON_CAP = _scale("max-height", "128px", "110px", "92px", "78px")
#: The two row archetypes with no icon fill their height with a badge or a
#: track instead, and those may not grow as far before they stop reading as
#: what they are, so their rows stop earlier too.
_TIGHT_ROW_CAP = _scale("max-height", "116px", "104px", "94px", "84px")
#: How far a tile may grow. A square tile runs into the *width* of the card it
#: leads before it runs into the height, and the narrowest box a real slide was
#: seen giving a figure is 800px wide: a six-card column there is 125px wide, a
#: matrix quadrant 350px, a branch outcome 360px, a gauge fact column 390px. A
#: tile wider than that squeezes the phrase beside it into one glyph per line,
#: which is how a matrix left its box at 800x400 before these caps existed.
#: Each row cap below is about 1.25x its tile cap, so a row whose tile has
#: stopped growing still reads as filled rather than as slack.
_COLUMN_ICON_CAP = _scale("max-height", "196px", "156px", "124px", "96px")
#: A hierarchy child card is capped instead, so the surplus of a tall box goes
#: to the stem that connects it -- an org chart reads as a longer connector,
#: not as a taller empty card -- and its icon only fills what is left inside.
_CHILD_CAP = _scale("max-height", "250px", "240px", "220px", "196px")
_CHILD_ICON_CAP = _scale("max-height", "128px", "120px", "104px", "88px")
#: The root plate takes its share of a tall box too, so the surplus is not all
#: spent on the stems: the two decks grow together and the connector stays a
#: connector. The plate is 320px wide, which is what caps the tile inside it.
_ROOT_CAP = _scale("max-height", "240px", "230px", "220px", "200px")
_ROOT_ICON_CAP = _scale("max-height", "104px", "96px", "84px", "72px")
#: The glyph inside a tile that is no longer a fixed size.
_GLYPH = "width:46%;height:46%;max-width:88px;max-height:88px"

#: A row's own inset is what its graphic cannot fill, so it is what decides
#: whether a filled row still reads as slack: the reading is
#: height / (height - inset), and 1.3 is the bar, so the inset stays under
#: 23% of the shortest row each size class produces (a five-row figure in a
#: 430px box is 80px per row).
_ROW_INSET = _inset("10px", "10px", "8px", "6px")

#: Stacked rows: share the box, stop at the cap, spread the rest, and let the
#: icon take the height the two lines of text cannot.
_ROW_FILL_CSS = (
    _same(_ROWS, "", "justify-content:space-between")
    + _by_size(_ROWS, " .ig-node", _ROW_INSET)
    + _by_size(_ICON_ROWS, " .ig-node", _ROW_CAP)
    + _by_size(("roadmap", "checklist"), " .ig-node", _TIGHT_ROW_CAP)
    # These two rows are the shortest the library draws (a track and a badge
    # are not as tall as an icon tile), so they give a little more of their
    # inset back: at four rows in a 386px box, 10px reads as 1.32 and 8px
    # as 1.23.
    + _by_size(("roadmap", "checklist"), " .ig-node", _inset("8px", "8px", "8px", "6px"))
    + _by_size(_ICON_ROWS, " .ig-node > .ig-ic", _ROW_ICON_CAP)
    + _same(_ICON_ROWS, " .ig-node > .ig-ic", _ROW_ICON)
    + _same(_ICON_ROWS, " .ig-ic svg", _GLYPH)
    + _same(_ICON_ROWS, " .ig-ic img", _GLYPH)
    # A checklist row has a badge where the others have an icon: it grows the
    # same way, and its radius stays one pixel value the browser clamps to a
    # circle at every size it reaches.
    + _by_size(("checklist",), " .ig-check", _scale("max-height", "96px", "84px", "78px", "72px"))
    + _same(("checklist",), " .ig-check", _ROW_ICON + ";border-radius:60px")
    + _same(("checklist",), " .ig-check-mark", "left:31%;top:16%;width:26%;height:52%")
    # A roadmap row's graphic is its phase track, so that is what grows.
    + _same(("roadmap",), " .ig-track", "align-self:stretch;height:auto;min-height:0")
    + _same(("roadmap",), " .ig-fill", "top:14%;bottom:14%;height:auto")
)

#: Column cards: the card is the box's height, the icon grows into what the
#: text leaves, and the parts anchor top/middle/bottom instead of clustering.
_COLUMN_FILL_CSS = (
    _same(_COLUMNS, "", "align-items:stretch")
    + _same(_COLUMNS, " .ig-node", "max-height:none")
    + _by_size(_COLUMNS, " .ig-ic", _COLUMN_ICON_CAP)
    + _same(_COLUMNS, " .ig-ic", _COLUMN_ICON)
    + _same(_COLUMNS, " .ig-ic svg", _GLYPH)
    + _same(_COLUMNS, " .ig-ic img", _GLYPH)
    + _same(_COLUMNS, " .ig-label", "margin-top:auto")
    + _same(_COLUMNS, " .ig-figure", "margin-top:auto")
)

#: The hierarchy's two decks: the children row takes the height the root plate
#: does not, each child card is the height of that row, and the card fills the
#: same way a column card does.
_HIERARCHY_FILL_CSS = (
    _by_size(("hierarchy",), " .ig-root", _ROOT_CAP)
    + _same(("hierarchy",), " .ig-root", "flex:1 1 auto;min-height:0")
    + _by_size(("hierarchy",), " .ig-root .ig-ic", _ROOT_ICON_CAP)
    + _same(("hierarchy",), " .ig-root .ig-ic", _COLUMN_ICON)
    + _same(("hierarchy",), " .ig-root .ig-ic svg", _GLYPH)
    + _same(("hierarchy",), " .ig-root .ig-ic img", _GLYPH)
    + _same(("hierarchy",), " .ig-root .ig-label", "margin-top:auto")
    + _same(("hierarchy",), " .ig-root .ig-figure", "margin-top:auto")
    + _same(("hierarchy",), " .ig-children", "flex:1 1 0;min-height:0")
    + _same(("hierarchy",), " .ig-child", "justify-content:flex-end")
    + _by_size(("hierarchy",), " .ig-children .ig-node", _CHILD_CAP)
    + _same(("hierarchy",), " .ig-children .ig-node", "height:100%")
    + _by_size(("hierarchy",), " .ig-children .ig-ic", _CHILD_ICON_CAP)
    + _same(("hierarchy",), " .ig-children .ig-ic", _COLUMN_ICON)
    + _same(("hierarchy",), " .ig-children .ig-ic svg", _GLYPH)
    + _same(("hierarchy",), " .ig-children .ig-ic img", _GLYPH)
    + _same(("hierarchy",), " .ig-children .ig-label", "margin-top:auto")
    + _same(("hierarchy",), " .ig-children .ig-figure", "margin-top:auto")
    # The stem is what a taller box lengthens: it takes the height between the
    # bar and the capped card, so the two decks stay connected.
    + _same(("hierarchy",), " .ig-children .ig-stem", "flex:1 1 auto;height:auto;min-height:12px")
)

#: A milestone station cannot move: its dot has to meet the rail, which is
#: drawn at a fixed offset in the figure's own box. What can move is the text
#: under it, so the date stays with the dot and the phrase anchors to the
#: bottom of the card rather than floating just below the rail.
_MILESTONES_FILL_CSS = (
    _same(("milestones",), " .ig-when", "margin-top:0")
    + _same(("milestones",), " .ig-label", "margin-top:auto")
    + _same(("milestones",), " .ig-node > :last-child", "margin-bottom:0")
)

#: A quadrant is a plate the box's height divides in two, and the card in it is
#: the one card the library draws in a narrow column.
_MATRIX_FILL_CSS = (
    _same(("matrix",), " .ig-q", "justify-content:space-between")
    # A quadrant is half the figure wide -- 350px in the narrowest box a real
    # slide gives a figure -- so its card is the one place a tile that grows
    # with the row squeezes the phrase beside it into one glyph per line. The
    # tile keeps a size class here and the card is capped just above what it
    # holds instead. ``min-height:auto`` beats that cap (used height is
    # max(min, min(max, height))), so a card that genuinely needs more keeps
    # its content rather than spilling out of the figure.
    + _by_size(("matrix",), " .ig-q .ig-node", _scale("max-height", "64px", "60px", "56px", "50px"))
    + _same(("matrix",), " .ig-q .ig-node", "flex:1 1 0;min-height:auto")
    + _by_size(("matrix",), " .ig-q .ig-node", _inset("6px", "6px", "5px", "4px"))
    # A quadrant holding one card centres it rather than pinning it to the top:
    # the node is the third child (plate, tag, node) and also the last one.
    + _same(("matrix",), " .ig-q > .ig-node:nth-child(3):last-child", "margin:auto 0")
    + _by_size(("matrix",), " .ig-node > .ig-ic", _scale("width", "52px", "48px", "44px", "40px"))
    + _by_size(("matrix",), " .ig-node > .ig-ic", _scale("height", "52px", "48px", "44px", "40px"))
    # The quadrant card is the narrowest the library draws, and a 44px numeral
    # beside two Korean lines is what pushed the phrase to one word per line.
    + _same(("matrix",), " .ig-num", "font-size:28px")
)

#: The gauge: the dial is a square that follows the box's height instead of a
#: fixed 320px one, its rings are stated as fractions of it, and the fact rows
#: fill like any other stacked row.
_GAUGE_FILL_CSS = (
    _same(
        ("gauge",),
        " .ig-dial",
        "width:auto;height:min(100%,360px);aspect-ratio:1;margin:auto;"
        "display:flex;align-items:center;justify-content:center",
    )
    + _same(("gauge",), " .ig-dial-ring", "left:0;top:0;width:100%;height:100%;border-radius:50%")
    + _same(
        ("gauge",),
        " .ig-dial-hole",
        "left:10.6%;top:10.6%;width:78.8%;height:78.8%;border-radius:50%",
    )
    # The reading in the middle of the dial was a plate stated in percentages
    # of it, which is a box its two lines cannot fill; the dial centres it as a
    # flex item instead, so the plate is the height of what it holds.
    + _same(
        ("gauge",),
        " .ig-dial .ig-node",
        "position:relative;left:auto;top:auto;width:72%;height:auto;padding:0",
    )
    + _same(("gauge",), " .ig-facts", "justify-content:space-between")
    + _by_size(("gauge",), " .ig-facts .ig-node", _scale("max-height", "128px", "118px", "106px", "94px"))
    + _by_size(("gauge",), " .ig-facts .ig-node", _ROW_INSET)
    + _by_size(("gauge",), " .ig-facts .ig-node > .ig-ic", _scale("max-height", "104px", "96px", "88px", "78px"))
    + _same(("gauge",), " .ig-facts .ig-node > .ig-ic", _ROW_ICON)
    + _same(("gauge",), " .ig-facts .ig-ic svg", _GLYPH)
    + _same(("gauge",), " .ig-facts .ig-ic img", _GLYPH)
)

#: The branch: the root plate fills the grid row it was centred in, the way a
#: hierarchy root fills its deck, and the outcomes keep their own height.
_BRANCH_FILL_CSS = (
    _by_size(("branch",), " .ig-root", _scale("max-height", "260px", "250px", "230px", "210px"))
    + _same(("branch",), " .ig-root", "height:100%;min-height:0")
    + _by_size(("branch",), " .ig-root .ig-ic", _ROOT_ICON_CAP)
    + _same(("branch",), " .ig-root .ig-ic", _COLUMN_ICON)
    + _same(("branch",), " .ig-root .ig-ic svg", _GLYPH)
    + _same(("branch",), " .ig-root .ig-ic img", _GLYPH)
    + _same(("branch",), " .ig-root .ig-label", "margin-top:auto")
    + _same(("branch",), " .ig-root .ig-figure", "margin-top:auto")
    # An outcome column is half the figure wide and already spans it, and five
    # or six outcomes have to shrink into a 386px box: the rows stay their own
    # height and the inset is what stops them reading as slack. At density
    # ``low`` an outcome is a 46px tile and one line, so the inset has to be
    # small: 10px around a 34px tile reads as 1.44, 4px as 1.24.
    + _same(("branch",), " .ig-outcomes .ig-node", "padding-top:4px;padding-bottom:4px")
)

#: A scene callout is placed in percentages of a box whose height it should not
#: inherit: a 23% plate in a 560px box is a 129px card around two lines, and at
#: density ``low`` around one. It keeps its place and takes the height of what
#: it holds -- a numbered chip when that is one line, a card when it is three --
#: which leaves its own inset as the only slack in it, so that is small too.
_SCENE_FILL_CSS = (
    _same(("scene",), " .ig-co", "height:auto;min-height:0;padding-top:4px;padding-bottom:4px")
    + _same(("scene",), " .ig-co .ig-no", "width:32px;height:32px;border-radius:16px;"
            "line-height:32px;margin-top:-16px")
)

#: A station plate and a venn label are placed by coordinates, so their height
#: is their content's and the only slack in them is their own inset. At density
#: ``low`` a station holds an icon and one label, and 14px of inset around 95px
#: of content already reads as 1.33; 10px reads as 1.21.
_PLATE_INSET_CSS = (
    _by_size(("cycle", "hub-spoke"), " .ig-node", _inset("10px", "10px", "8px", "8px"))
    # A venn label carries no plate of its own unless it is the emphasised one,
    # so its inset is spacing rather than paint, and 6px of it around a single
    # line still reads as a card half empty.
    + _same(("venn",), " .ig-node", "padding-top:3px;padding-bottom:3px")
)

#: The venn's three sets are the one geometry here that cannot follow the box:
#: a circle has to stay round, so its diameter is a length, and the horizontal
#: offset that overlaps it with its neighbours would have to be half of that
#: same length -- a percentage in a horizontal property resolves against the
#: figure's width, not its height, so there is no way to state it. What is left
#: is to make the fixed circle as large as the shortest box still admits: 290px
#: with the lower set at 22% leaves 11px under a 386px box.
_VENN_FILL_CSS = (
    _same(("venn",), " .ig-set", "width:290px;height:290px;border-radius:145px")
    + _same(("venn",), " .ig-set.s1", "left:calc(50% - 243px);top:3%")
    + _same(("venn",), " .ig-set.s2", "left:calc(50% - 47px);top:3%")
    + _same(("venn",), " .ig-set.s3", "left:calc(50% - 145px);top:22%")
    # The labels ride on the circles, and they are what the box-use reading
    # measures: at 8% and 72% they span 69% of a 560px box, just under the bar.
    + _same(("venn",), " .ig-node.v1", "top:5%")
    + _same(("venn",), " .ig-node.v2", "top:5%")
    + _same(("venn",), " .ig-node.v3", "top:75%")
)

#: A hub plate states its own height because half of it is the negative margin
#: that centres it, so it cannot hug its text. Spreading the title and the
#: phrase across it is what a fixed plate can do.
_HUB_FILL_CSS = _same(
    ("hub-spoke",), " .ig-hub", "padding:4px 16px;justify-content:space-between"
)

FILL_CSS = (
    _ROW_FILL_CSS
    + _COLUMN_FILL_CSS
    + _HIERARCHY_FILL_CSS
    + _MILESTONES_FILL_CSS
    + _MATRIX_FILL_CSS
    + _GAUGE_FILL_CSS
    + _BRANCH_FILL_CSS
    + _SCENE_FILL_CSS
    + _PLATE_INSET_CSS
    + _VENN_FILL_CSS
    + _HUB_FILL_CSS
)

__all__ = ["FILL_CSS"]
