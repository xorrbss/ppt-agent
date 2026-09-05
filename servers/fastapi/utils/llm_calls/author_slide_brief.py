"""Deck-invariant authoring brief text added by the 2026-09-05 quality
programme (design ``docs/authored-quality-speed-programme-design-20260905.md``
sections G1, G2, G4, G6).

The 2026-09-05 diagnosis measured what the previous brief did and did not say:
it stated a type FLOOR but no ramp, so 80 % of body elements rendered under
18px; it stated a footer safe zone but no fill direction, so pages finished
~120px short of the line; and every universal that made a preset deck look
composed (margins, one accent, the eyebrow spec) lived in the DEFAULT style
brief, which 37 of 38 presets replace. This module holds the text that fixes
those three gaps, plus the two long prompt blocks moved out of
``author_slide_prompt`` to keep that module under the file-size ratchet.

Numbers are never written twice: the bottom content line comes from
``utils.authored_infographic.contracts.content_bottom_px`` (the same source
``author_slide_rules`` and ``authored_geometry_brief`` state it from), and the
type ramp is a table both the TYPE SCALE paragraph and the deck-token block
read, so the ramp and the tokens can never disagree.
"""

from __future__ import annotations

from dataclasses import dataclass

from utils.authored_infographic.contracts import content_bottom_px


#: The floor below which nothing that communicates slide content may compute.
#: One number for the whole prompt: the previous brief carried 13px, 15px and
#: 12px in three different places, and the capability registry's own floor
#: (``AUTHORED_FONT_FLOOR_PX``) is 12.
CONTENT_FONT_FLOOR_PX = 12
#: The same floor in points, at the CSS 96dpi ratio the exporter uses (4px = 3pt).
CONTENT_FONT_FLOOR_PT = CONTENT_FONT_FLOOR_PX * 3 // 4


def composition_intent_block() -> str:
    """G1 — what the page is FOR, stated before any of the fit constraints.

    Every other priority sentence in the brief points away from ambition
    ("prioritise fit over visual ambition"), and with the composition envelope
    off there was no sentence at all asking the page to carry its argument.
    The fill sentence deliberately states a DIRECTION; the geometry brief
    states the same line as a BOUND. Neither repeats the other's job.
    """
    numbered = content_bottom_px("template")
    unnumbered = content_bottom_px("none")
    return f"""\
- COMPOSITION INTENT: the page must be visibly worth its place in the deck.
  - Render the CONTENT's claim and its evidence in full; shorten low-value wording
    before cutting an argument, and cut an argument before shrinking type below the
    scale. Do not pad, and do not summarise the CONTENT into a caption.
  - Make ONE element dominant per slide (a figure, a number, a headline, a diagram);
    everything else is subordinate to it.
  - When CONTENT states three or more comparable numbers sharing a unit, draw them as a
    labelled HTML/CSS bar or column chart with one explicit takeaway; every value and
    unit verbatim, no invented series.
  - When CONTENT is a real row/column structure, render a semantic
    <table data-native="table"> with real <th>/<td>; never a div grid.
  - On a numbered page compose so the content finishes close to y={numbered}px \
(y={unnumbered}px on an
    unnumbered deck); reach the line by adding evidence and structure, never by letting a
    block cross it."""


@dataclass(frozen=True)
class TypeRamp:
    """One deck's type ramp in px, from the deck's verbosity.

    ``display`` is the slide's own title; ``lead`` a section lead; ``claim``
    the sub-heading tier the 2026-09-05 census found missing entirely (one
    ``title`` element per slide and nothing between it and 15px body copy).
    """

    display: int
    lead: int
    claim: int
    body: int
    cell: int
    label: int
    footnote: int


#: Standard is the measured target; a concise deck carries less text so it can
#: afford +2px on every tier, and a text-heavy deck -2px. The page-number
#: element keeps its own 12px furniture size from the PAGE-NUMBER SYSTEM rules.
_STANDARD_RAMP = TypeRamp(
    display=44, lead=26, claim=21, body=18, cell=16, label=15, footnote=13
)


def _shifted(ramp: TypeRamp, delta: int) -> TypeRamp:
    return TypeRamp(
        display=ramp.display + delta * 2,
        lead=ramp.lead + delta,
        claim=ramp.claim + delta,
        body=ramp.body + delta,
        cell=ramp.cell + delta,
        label=ramp.label + delta,
        footnote=max(CONTENT_FONT_FLOOR_PX, ramp.footnote + delta),
    )


_TYPE_RAMPS = {
    "concise": _shifted(_STANDARD_RAMP, 2),
    "standard": _STANDARD_RAMP,
    "text-heavy": _shifted(_STANDARD_RAMP, -2),
}


def type_ramp(verbosity: str | None) -> TypeRamp:
    """The deck's type ramp, defaulting to standard for an unknown verbosity."""
    return _TYPE_RAMPS.get(verbosity or "standard", _STANDARD_RAMP)


def type_scale_text(verbosity: str | None) -> str:
    """G2 — the TYPE SCALE paragraph for one deck, rendered from the ramp."""
    ramp = type_ramp(verbosity)
    return f"""\
- TYPE SCALE — this is a ramp, not a floor. In ordinary authored generation and
  style-reference mode set: slide title {ramp.display - 4}-{ramp.display + 4}px, \
section lead {ramp.lead - 2}-{ramp.lead + 2}px,
  claim or sub-heading {ramp.claim - 1}-{ramp.claim + 1}px, body copy and bullets \
{ramp.body}px OR LARGER (never below {ramp.body}px),
  table cell {ramp.cell}px or larger, label or caption {ramp.label}px, footnote \
{ramp.footnote}px. The label size is for
  eyebrows, axis and legend labels, units and captions ONLY: any sentence, bullet, card
  text or description a reader must actually read is body copy and takes the body size,
  so most of the slide's characters sit at {ramp.body}px or above. A content slide must
  show at least one tier BETWEEN its title and its body copy; a page of title-plus-body
  has no hierarchy to read. Keep no visible content text (headings, body, bullets, tables, chart
  labels, metrics, callouts, captions, footnotes) below {CONTENT_FONT_FLOOR_PX}px \
({CONTENT_FONT_FLOOR_PT}pt equivalent):
  that is a floor, not the size to write at. Express text sizes with `font-size` in px;
  do not use CSS `font` shorthand or relative units to evade the ramp. Spare room on a
  slide goes into a larger type scale and more generous leading, not into one empty band.
  If content does not fit, shorten it or allocate more space instead of shrinking it until
  it becomes unnecessarily hard to read."""


#: Every verbosity's rendered TYPE SCALE paragraph, keyed the way
#: ``author_slide_rules._TYPE_SCALES`` has always been keyed.
TYPE_SCALES = {name: type_scale_text(name) for name in _TYPE_RAMPS}


#: G4 — the SVG contract, four lines instead of the previous 1 560 characters.
#: The exhaustive element/attribute enumeration moved to the validator: it is
#: the component that refuses a document, and its rejection message names the
#: offending element or attribute exactly. Spending ~400 input tokens on every
#: slide of every deck to restate a list the model almost never needs (0 svg
#: elements across 82 fixture slides) bought nothing.
INLINE_SVG_CONTRACT = """\
  - INLINE SVG CONTRACT (only when an essential chart or diagram truly needs SVG):
    - Root attributes: `xmlns="http://www.w3.org/2000/svg"` (exact value, root only) plus
      `viewBox`, `width`, `height`, `preserveAspectRatio`; never `xmlns:xlink`.
    - Elements: only inert presentation shapes, text and paint definitions (g, path, rect,
      circle, ellipse, line, polyline, polygon, text, tspan, defs, linearGradient,
      radialGradient, stop, clipPath, mask).
    - Attributes: only each element's ordinary coordinates plus transform, fill/stroke
      paint, opacity, dash, and text-metric attributes; paint may reference only a local
      fragment id such as `clip-path="url(#clip1)"`.
    - Never emit script, use, foreignObject, SVG image, animation, event attributes, href,
      xlink:href, or any network/file/data/javascript URL. Anything else is refused, and
      the rejection message names it."""


#: G6 — the universals that used to live inside DEFAULT_STYLE_BRIEF, which 37
#: of 38 presets replace wholesale. A preset describes a look; it is not the
#: place a deck learns that it has margins.
SHARED_UNIVERSALS = """\
- SHARED UNIVERSALS (they hold whatever the style brief above says):
  - Margins ~64px on every edge, except an intentional full-bleed colour zone.
  - ONE accent hue per slide: the style's accent carries emphasis and everything else is
    ink, a neutral, or a tint of that accent. A second saturated hue is an error.
  - Eyebrow: UPPERCASE where the language has case, letter-spaced 0.15em, ~13px, in the
    accent, with a short tick or rule beside it.
  - Vary per slide (any of these, never all at once): full-bleed or partial colour-blocked
    zones, asymmetric grids, a left sidebar, oversized numerals, thin rules, and subtle
    tints of the accent."""


# Two kinds of picture can be attached to one authoring call and they carry
# opposite instructions, so the brief names both and says which is which. These
# rules are per-slide rather than part of the design system because a deck whose
# style generates no illustration can still be handed source images.
SOURCE_IMAGE_RULES = (
    "A style reference is art direction and is not reproduced. A source image is "
    "one of the user's own assets and may be placed on this slide.\n"
    "It can earn its place in either of two ways: as evidence for what this "
    "slide claims, or as an example of the tool, artifact or way of working the "
    "slide describes. A screenshot captured on a different project still shows "
    "what that work looks like, which is what a proposal shows it for.\n"
    "Place any relevant candidates with `<img data-source-slot-id=\"SLOT_ID\">` "
    "whose areas are reserved with explicit width/height (or absolute inset) styles, "
    "the way the illustration slot is. Do not add a src attribute, asset id, URL or "
    "data URI: the trusted source-image stage supplies the verified data URL.\n"
    "Leave the slide without a picture when no candidate shows anything it talks "
    "about. These are candidates, not a quota."
)


#: The per-slide half of the header contract's requirement rule. The
#: identifiers are already in this slide's own CONTENT, so they are read from
#: there rather than handed over as a pre-extracted list: recognising them needs
#: the surrounding sentence, and "SHA-256" inside a security paragraph looks
#: identical to a requirement code to anything that only matches on shape.
REQUIREMENT_IDENTIFIER_RULES = (
    "REQUIREMENT IDENTIFIERS: this deck numbers its requirements. The CONTENT "
    "field of THIS slide is the ONLY place you may take an identifier from. "
    "Not the deck topic, not the design system, not another slide, not your "
    "own knowledge of the document — if the string is not written in this "
    "slide's CONTENT, it does not go on this slide.\n"
    "- If this slide's CONTENT states identifiers (a code the source document "
    "assigns to a requirement, such as those introduced by 고유번호/요구사항 "
    "번호/REQ), copy each one verbatim into the header's requirement group, "
    'in the order given, and list them in data-requirement-ids="ID1,ID2".\n'
    "- Never invent, renumber, or reformat one, and never promote an "
    "algorithm, protocol, standard, version, or date that merely resembles "
    "one (SHA-256, AES-256, ISO 9001, HTTP/2).\n"
    "- If this slide's CONTENT states none, render NO requirement group at "
    "all and leave the header's right slot empty. That is the correct, "
    "expected result on such a slide — an empty slot is never a defect to "
    "paper over, and repeating another slide's identifier to fill it is a "
    "worse error than leaving it blank."
)


#: What a page the chapter plan INSERTED is for. These two roles exist only on
#: decks that resolved a chapter plan, so their rules are handed to the slides
#: that have them instead of being added to the shared design system, where
#: every ordinary deck would carry instructions for pages it does not contain.
#: The visual idiom still comes from the deck's style brief; what is fixed here
#: is what the page IS, which is not a style question.
SECTION_STRUCTURE_BRIEF = (
    "THIS SLIDE IS A CHAPTER DIVIDER. It announces the chapter and does "
    "nothing else: the chapter numeral and name are the dominant "
    "element, and the items the CONTENT lists sit beneath them as a "
    "quiet secondary column. Compose it full-bleed in this deck's cover "
    "idiom rather than as a content page. It carries NO header band, no "
    "eyebrow-and-tick row, no requirement identifier, no divider rule "
    "imitating the header, and no argument, claim, chart, table, or "
    "body copy the CONTENT does not state."
)

AGENDA_STRUCTURE_BRIEF = (
    "THIS SLIDE IS THE DECK'S TABLE OF CONTENTS. It lists the chapters "
    "in the order the CONTENT gives them and nothing else: one line per "
    "chapter, numerals aligned in a single column, wording copied "
    "verbatim. Never add a chapter the CONTENT does not list, a "
    "description, a page number, a duration, or a status marker. It "
    "carries this deck's ordinary header like any other content page."
)


#: One sentence per story kind, delivered only by the visual-story marker.
STORY_RULES = {
    "cover": "Keep the cover minimal: one decisive title hierarchy and one focal field.",
    "closing": "End with one decisive statement and a restrained next-step cue, not a recap grid.",
    "section": "Treat the chapter label as the single visual event; keep supporting items quiet.",
    "agenda": "Use a numbered reading path with aligned chapter labels and no dashboard cards.",
    "editorial": "Build one clear editorial argument with a dominant claim and subordinate evidence.",
    "pillars": "Make the pillars visibly distinct in hierarchy without repeating identical heavy cards.",
    "metric": "Make one stated metric dominant and place its stated context immediately beside it.",
    "process": "Show the stated sequence as a directional rail; preserve order and do not invent steps.",
    "architecture": "Show the stated layers and relationships with simple DOM boxes and connectors.",
    "image": "Use the single allowed image for context while all claims and numbers remain in HTML text.",
    "dataTable": (
        "Render the supplied rows and columns as a real semantic HTML table. Highlight the decisive "
        "row or column only when CONTENT supports it; never invent a cell, total, or rank."
    ),
    "dataChart": (
        "Turn only the numeric series stated in CONTENT into a labelled HTML/CSS chart and pair it "
        "with one explicit takeaway. Preserve every value and unit verbatim; never fabricate a series."
    ),
}


__all__ = [
    "AGENDA_STRUCTURE_BRIEF",
    "CONTENT_FONT_FLOOR_PT",
    "CONTENT_FONT_FLOOR_PX",
    "INLINE_SVG_CONTRACT",
    "REQUIREMENT_IDENTIFIER_RULES",
    "SECTION_STRUCTURE_BRIEF",
    "SHARED_UNIVERSALS",
    "SOURCE_IMAGE_RULES",
    "STORY_RULES",
    "TYPE_SCALES",
    "TypeRamp",
    "composition_intent_block",
    "type_ramp",
    "type_scale_text",
]
