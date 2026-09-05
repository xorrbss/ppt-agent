"""How a figure fits the box the authoring model gave it.

The library states one figure at one size class; the box it lands in is the
model's decision, and the observed boxes run from about 758x373 to 1184x526.
Nothing in CSS can measure that box, so fitting is achieved the only way a
stylesheet can: every stacked part is a shrinkable flex item
(``min-height:0``), and the ``n5``/``n6`` size classes state a compaction that
makes five or six parts fit the *shortest* box a figure is given rather than
the tallest.

Two rules govern what may be compacted:

* No planned text is ever hidden or truncated. Compaction spends padding,
  gaps, connector heights, icon sizes and line-height — never a label, a
  phrase or a figure, and never ``overflow:hidden`` on text.
* No declared font-size drops below 15px, because the pipeline clamps the
  document to that floor before the first render (see ``css_base``).

Compaction has a mirror image, and it is the defect this file used to leave
standing. A column archetype's card is ``flex:1 1 0`` under ``align-items:
stretch``, so its height IS the box height; its parts then sit centred inside
it at the size class's type scale. In the 386px boxes the library was tuned
for that is dense. In the 440-560px boxes the figure contract now asks for, a
six-node process card is 375px tall around 130px of content: two thirds of
every card is empty white (measured on bench slide 18306a40 s08). Growing the
type with the box is not something flat CSS can do -- renderer isolation
refuses at-rules, so there are no container queries -- so the surplus is
handled the other way round: the cards are capped at a height their content
genuinely fills, the row is centred in the box, and the type scale of those
archetypes rises to what a capped card holds. Below the cap nothing changes,
which is why the short boxes still fit.

This block is appended last to the library template, so it wins the ties its
selectors do not already outrank.
"""

from __future__ import annotations

#: Archetypes that stack their nodes as rows down the figure. These are the
#: ones that overflowed: six rows of intrinsic height need about 550px, and a
#: figure box is routinely under 400px tall.
_STACKED = ("roadmap", "swimlane", "layers", "checklist", "funnel", "pyramid")


def _stacked(size: str, suffix: str = "") -> str:
    """The ``suffix`` selector under every stacked-row root at ``size``."""
    return ",".join(f".ig-{name}.{size}{suffix}" for name in _STACKED)


#: Row archetypes: the root gives its vertical padding back, the rows shrink,
#: and the space a row keeps is spent on the text rather than on its inset.
#:
#: A roadmap's phase track and a checklist's badge are not compacted here any
#: more: ``css_fill`` makes both the height of the row they sit in, so they
#: shrink with it in a short box and grow with it in a tall one.
_ROWS_CSS = f"""\
{_stacked("n5")},{_stacked("n6")}{{padding-top:0;padding-bottom:0}}
{_stacked("n5")}{{gap:8px}}
{_stacked("n6")}{{gap:6px}}
{_stacked("n5", " .ig-node")}{{min-height:0;padding:10px 18px;gap:16px}}
{_stacked("n6", " .ig-node")}{{min-height:0;padding:7px 16px;gap:14px}}
{_stacked("n5", " .ig-text")},{_stacked("n6", " .ig-text")}{{gap:1px}}
.ig-roadmap.n5 .ig-head,.ig-roadmap.n6 .ig-head{{width:300px;gap:1px}}
.ig-roadmap.n6 .ig-tail{{width:96px}}
.ig-roadmap.n3 .ig-num,.ig-roadmap.n4 .ig-num{{font-size:40px}}
.ig-swimlane.n5 .ig-step{{height:34px;line-height:32px}}
.ig-swimlane.n6 .ig-step{{height:28px;line-height:26px}}
.ig-swimlane.n5 .ig-steps,.ig-swimlane.n6 .ig-steps{{gap:6px}}
.ig-swimlane.n6 .ig-head{{width:180px}}
.ig-funnel.n5 .ig-num,.ig-funnel.n6 .ig-num,.ig-pyramid.n5 .ig-num,.ig-pyramid.n6 .ig-num{{font-size:26px}}
"""

#: Column archetypes: the cards already share the width, so compaction is the
#: gutter between them and the connector that eats into it.
_COLUMNS_CSS = """\
.ig-kpi.n5,.ig-icon-list.n5{gap:14px}
.ig-kpi.n6,.ig-icon-list.n6{gap:10px}
.ig-kpi.n6 .ig-node,.ig-icon-list.n6 .ig-node{padding:16px 12px}
.ig-process.n6 .ig-arrow{width:24px}
.ig-process.n5 .ig-node,.ig-process.n6 .ig-node{max-width:220px}
.ig-milestones.n5 .ig-track,.ig-milestones.n6 .ig-track{gap:10px}
.ig-milestones.n6 .ig-node{padding:48px 10px 14px;gap:6px}
"""

#: The two-deck archetypes: a root plate over a row of children, with the
#: stems between them. Both decks are always present whatever the node count,
#: so their vertical cost is compacted at every size, not only at n5/n6.
#:
#: A hierarchy child card is a flex item of its own ``.ig-child`` column, and
#: that column's height comes from its content — so ``min-height:0`` there
#: does not make the card share a box, it makes the card collapse to nothing
#: while its text stays put. The card keeps its automatic minimum; the fit
#: comes from the compaction below.
_HIERARCHY_CSS = """\
.ig-hierarchy .ig-child .ig-node{min-height:auto}
.ig-hierarchy .ig-stem{height:10px}
.ig-hierarchy .ig-root,.ig-hierarchy .ig-children .ig-node{padding:8px 14px;gap:3px}
.ig-hierarchy .ig-ic{width:30px;height:30px;border-radius:10px}
.ig-hierarchy .ig-ic svg,.ig-hierarchy .ig-ic img{width:18px;height:18px}
.ig-hierarchy.n3 .ig-num,.ig-hierarchy.n4 .ig-num{font-size:26px}
.ig-hierarchy.n3 .ig-label,.ig-hierarchy.n4 .ig-label{font-size:20px}
.ig-hierarchy.n3 .ig-desc{font-size:15px}
.ig-hierarchy.n5 .ig-children,.ig-hierarchy.n6 .ig-children{gap:12px}
.ig-hierarchy.n5 .ig-children .ig-node,.ig-hierarchy.n6 .ig-children .ig-node,
  .ig-hierarchy.n5 .ig-root,.ig-hierarchy.n6 .ig-root{padding:7px 8px;gap:2px}
.ig-hierarchy.n5 .ig-ic,.ig-hierarchy.n6 .ig-ic{width:24px;height:24px;border-radius:8px}
.ig-hierarchy.n5 .ig-ic svg,.ig-hierarchy.n6 .ig-ic svg,.ig-hierarchy.n5 .ig-ic img,
  .ig-hierarchy.n6 .ig-ic img{width:14px;height:14px}
.ig-hierarchy.n5 .ig-num,.ig-hierarchy.n6 .ig-num{font-size:24px}
.ig-branch .ig-root{padding:16px 20px}
.ig-branch.n4 .ig-outcomes,.ig-branch.n5 .ig-outcomes,.ig-branch.n6 .ig-outcomes{gap:10px}
.ig-branch.n4 .ig-outcomes .ig-node,.ig-branch.n5 .ig-outcomes .ig-node{padding:9px 16px;gap:14px}
.ig-branch.n6 .ig-outcomes .ig-node{padding:6px 14px;gap:12px}
"""

#: Data archetypes: the quadrant inset and the fact-row inset are what a fifth
#: and sixth node have to come out of.
_DATA_CSS = """\
.ig-matrix.n5 .ig-q,.ig-matrix.n6 .ig-q{min-height:0;padding:26px 12px 8px;gap:6px}
.ig-matrix.n5 .ig-node,.ig-matrix.n6 .ig-node{padding:6px 10px;gap:10px}
.ig-matrix.n5 .ig-ic,.ig-matrix.n6 .ig-ic{width:30px;height:30px}
.ig-gauge.n5 .ig-facts,.ig-gauge.n6 .ig-facts{gap:8px}
.ig-gauge.n5 .ig-facts .ig-node{padding:8px 14px;gap:12px}
.ig-gauge.n6 .ig-facts .ig-node{padding:6px 12px;gap:10px}
.ig-gauge.n5 .ig-facts .ig-num,.ig-gauge.n6 .ig-facts .ig-num{font-size:24px}
"""

#: Orbit archetypes: two rows of station plates and a hub between them have to
#: share the height, so at five and six stations the plates get wider (one
#: line of phrase instead of two) and the hub gives the middle band back.
_ORBIT_CSS = """\
.ig-cycle.n5 .ig-node,.ig-cycle.n6 .ig-node,.ig-hub-spoke.n5 .ig-node,.ig-hub-spoke.n6 .ig-node{
  width:220px;margin-left:-110px;padding:8px 12px;gap:3px}
.ig-cycle.n5 .ig-node.is-key,.ig-cycle.n6 .ig-node.is-key{width:232px;margin-left:-116px}
.ig-cycle .ig-hub{width:260px;margin-left:-130px}
.ig-cycle.n5 .ig-hub,.ig-cycle.n6 .ig-hub{height:48px;margin-top:-24px}
.ig-hub-spoke.n5 .ig-hub,.ig-hub-spoke.n6 .ig-hub{height:84px;margin-top:-42px}
.ig-cycle.n5 .ig-hub-title,.ig-cycle.n6 .ig-hub-title,.ig-hub-spoke.n5 .ig-hub-title,
  .ig-hub-spoke.n6 .ig-hub-title{font-size:18px}
"""

#: The type scale of the column archetypes, given a box taller than the content
#: they were drawn for.
#:
#: ``height:100%`` keeps every card of a row exactly as tall as every other one
#: (the property ``align-items:stretch`` used to provide). Capping that height
#: was the first answer to a card taller than its content, and it was the wrong
#: one: a capped row still pooled the surplus above and below itself, and at
#: density ``low`` -- an icon and a label, nothing else -- the card was 2.3x the
#: height of what it held whatever the cap. ``css_fill`` replaces the caps: the
#: card takes the box, its icon grows into what the text leaves, and the parts
#: anchor to the top, the middle and the bottom.
#:
#: The type scale stays here, and rises: these archetypes are wide, so their
#: limit was never horizontal, and at the old scale a card read as a small label
#: floating in a large white box. Every value stays above the 15px floor and is
#: measured by the fit sweep at 1138x386 and 800x400 -- the shortest and
#: narrowest boxes real slides were seen giving a figure -- as well as at the
#: tall ones the contract now asks for.
#:
#: Every selector states its own ``nX`` because ``css_base`` sizes type through
#: ``.ig.n3``/``.ig.n5``/``.ig.n6`` (three classes): a two-class
#: ``.ig-process .ig-label`` would lose to it however late it is appended.
_COLUMN_FILL = ("process", "icon-list", "kpi")

#: The timeline's stations are cards too, and the emptiest ones measured (ink
#: fill 0.099, the lowest of any archetype). A station's dot has to meet the
#: rail, which is drawn at a fixed offset in the figure's own box, so the card
#: cannot move; it takes the type half of the fix here and the anchoring half
#: in ``css_fill``.
_MILESTONES = ("milestones",)


def _cards(
    prop: str,
    sizes: dict[str, str],
    *,
    part: str = "",
    roots: tuple[str, ...] = _COLUMN_FILL,
) -> str:
    """``prop`` for each root's ``part``, one rule per size class."""
    lines = []
    for size, value in sizes.items():
        selector = ",".join(f".ig-{name}.{size}{part}" for name in roots)
        lines.append(f"{selector}{{{prop}:{value}}}")
    return "\n".join(lines) + "\n"


_SIZES = ("n3", "n4", "n5", "n6")


def _all(value: str, *, n5: str | None = None, n6: str | None = None) -> dict[str, str]:
    """One value for n3/n4, optionally tightened at n5 and n6."""
    return dict(zip(_SIZES, (value, value, n5 or value, n6 or n5 or value), strict=True))


#: The cap is roughly the content height of the fullest card at that size class,
#: so a card at the cap is full rather than merely shorter than the box.
_COLUMN_FILL_CSS = (
    """\
.ig-process .ig-node,.ig-icon-list .ig-node,.ig-kpi .ig-node{height:100%}
"""
    + _cards("padding", _all("26px 18px", n5="21px 14px", n6="18px 12px"), part=" .ig-node")
    + _cards("gap", _all("12px", n5="10px", n6="8px"), part=" .ig-node")
    + _cards("font-size", _all("28px", n5="24px", n6="22px"), part=" .ig-label")
    + _cards("font-size", _all("19px", n5="18px", n6="17px"), part=" .ig-desc")
    + _cards("font-size", _all("52px", n5="46px", n6="40px"), part=" .ig-num")
    # The tile itself is sized in ``css_fill``: it starts at the base size and
    # grows into whatever height the text of a card leaves, so a fixed size
    # here would be a rule no card ever uses.
    # A KPI tile is one big number, so it keeps the larger numeral the archetype
    # was designed around; only the emphasis tile still outgrows it.
    + ".ig-kpi.n3 .ig-num,.ig-kpi.n4 .ig-num{font-size:60px}\n"
    ".ig-kpi.n5 .ig-num{font-size:52px}\n"
    ".ig-kpi.n6 .ig-num{font-size:46px}\n"
    ".ig-kpi .ig-node.is-key .ig-num{font-size:68px}\n"
    ".ig-kpi.n5 .ig-node.is-key .ig-num{font-size:58px}\n"
    ".ig-kpi.n6 .ig-node.is-key .ig-num{font-size:50px}\n"
    + _cards("font-size", _all("26px", n5="23px", n6="21px"),
             part=" .ig-label", roots=_MILESTONES)
    + _cards("font-size", _all("18px", n5="17px", n6="16px"),
             part=" .ig-desc", roots=_MILESTONES)
    + _cards("font-size", _all("18px", n5="17px", n6="16px"),
             part=" .ig-when", roots=_MILESTONES)
)

COMPACT_CSS = (
    _ROWS_CSS + _COLUMNS_CSS + _HIERARCHY_CSS + _DATA_CSS + _ORBIT_CSS + _COLUMN_FILL_CSS
)

__all__ = ["COMPACT_CSS"]
