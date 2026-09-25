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
from utils.get_env_programme import is_authored_prompt_bundle_enabled


#: The floor below which nothing that communicates slide content may compute.
#: One number for the whole prompt: the previous brief carried 13px, 15px and
#: 12px in three different places, and the capability registry's own floor
#: (``AUTHORED_FONT_FLOOR_PX``) is 12.
CONTENT_FONT_FLOOR_PX = 12
#: The same floor in points, at the CSS 96dpi ratio the exporter uses (4px = 3pt).
CONTENT_FONT_FLOOR_PT = CONTENT_FONT_FLOOR_PX * 3 // 4

#: How far above the footer boundary the fill directive aims. Aiming AT the
#: boundary made every rounding error a footer intrusion; a band leaves the
#: page number its own air while still asking the page to be full.
FILL_TARGET_MARGIN_PX = 28


#: The ``data-semantic-role`` vocabulary. It reads like composition guidance,
#: but it is a contract with two other components: the semantic-role repair
#: pass and the v2 capacity gate both key off these values, and a deck whose
#: title owner is unmarked is blocked outright. It therefore ships whatever
#: ``AUTHORED_PROMPT_BUNDLE_ENABLED`` is set to, which is why it was lifted out
#: of ``composition_intent_block`` into its own always-on block.
SEMANTIC_ROLE_RULES = """\
- SEMANTIC ROLES (the repair pass and the capacity gate read these marks):
  - Mark every text element with `data-semantic-role`, one of: title, body, supporting,
    decoration, image. Mark exactly ONE element per slide `data-semantic-role="title"` —
    the slide's title, on a cover the main title — on the element that directly contains
    that text, never `decoration`. `body` is the slide's readable content — sentences,
    bullets, descriptions, table text, card copy (most text owners on a content slide are
    `body`); `supporting` is short structural text only — sub-headings, eyebrows, labels,
    numbers with units, captions. A content slide must have body owners. Empty shapes and
    rules are `decoration`."""


#: The sentence every slide prompt ends on, under every prompt profile. August's
#: wording differed only by a comma splice, and the ``data-qid`` sentence is a
#: contract rather than guidance -- ``data-qid`` is the sole edit-identity marker
#: the projection, the repair passes and the hybrid export all key off -- so
#: there is no earlier version of this text a profile could usefully restore.
SLIDE_OUTPUT_CONTRACT = (
    "Design THIS slide bespoke and premium within the shared system, vary the layout to "
    "suit the role. Give a unique stable data-qid to every element that owns text, and to "
    "every image, table, chart, and card that carries text; a plain decorative shape or a "
    "pure wrapper does not need one. Output ONLY the complete self-contained HTML "
    "document."
)


def composition_intent_block() -> str:
    """G1 — what the page is FOR, stated before any of the fit constraints.

    Every other priority sentence in the brief points away from ambition
    ("prioritise fit over visual ambition"), and with the composition envelope
    off there was no sentence at all asking the page to carry its argument.
    The fill sentence deliberately states a DIRECTION; the geometry brief
    states the same line as a BOUND. Neither repeats the other's job.

    Gated by ``AUTHORED_PROMPT_BUNDLE_ENABLED`` and therefore empty by default:
    a blind paired rating of 24 slides preferred the pre-bundle parent 13 / 7
    with 4 ties, and bench run 37 flagged 17 of 24 slides for footer/card
    intrusion, so this half of the bundle only ships when asked for.
    """
    if not is_authored_prompt_bundle_enabled():
        return ""
    numbered = content_bottom_px("template")
    unnumbered = content_bottom_px("none")
    # Aim a band ABOVE the boundary, never at it. The 2026-09-05 wording told the
    # model to finish "close to" the line itself; the blind rating of run 27 lost
    # pages whose last line or card edge sat on the page number, so the fill
    # target and the hard boundary are now two different numbers.
    numbered_target = numbered - FILL_TARGET_MARGIN_PX
    unnumbered_target = unnumbered - FILL_TARGET_MARGIN_PX
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
  - Fill the page down to about y={numbered_target}px on a numbered page \
(y={unnumbered_target}px on an
    unnumbered deck) by adding evidence and structure rather than white space. \
y={numbered}px
    (y={unnumbered}px) is a HARD boundary the footer owns: nothing — not a card edge, not a
    last line, not a shadow — may touch or cross it, so aim for the target and keep the
    remaining band clear."""


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


#: The pre-bundle type scale, restored VERBATIM from
#: ``git show 85f915eff:servers/fastapi/utils/llm_calls/author_slide_rules.py``
#: (its ``_TYPE_SCALES``). This is what a deck reads while
#: ``AUTHORED_PROMPT_BUNDLE_ENABLED`` is false: a floor per verbosity rather
#: than a ramp. The ramp above asks for 18px body copy on a 1280x720 page while
#: the composition intent asks the same page to render claim and evidence in
#: full, and the two together overflowed the card and the footer band on 17 of
#: 24 slides in bench run 37. Do not edit this text: it is a parent baseline,
#: kept so the knob can restore the wording the blind rating actually preferred.
PRE_BUNDLE_TYPE_SCALES = {
    "concise": """- TYPE SCALE: this deck is written concise, so every slide carries little text and has room to
  read well. In ordinary authored generation and style-reference mode, set body copy, bullets,
  labels, and table cells at 22px or larger, the slide's own title between 40px and 56px, and
  keep no visible content text (headings, body, bullets, tables, chart labels, metrics,
  callouts, captions, footnotes) below 15px (11pt equivalent). Express text sizes with
  `font-size` in px; do not use CSS `font` shorthand or relative units to evade this floor.
  Spare room on a slide goes into a larger type scale and more generous leading, not into one
  large empty band.""",
    "standard": """- TYPE SCALE: in ordinary authored generation and style-reference mode, set body copy, bullets,
  labels, and table cells at 18px or larger, and keep no visible content text (headings, body,
  bullets, tables, chart labels, metrics, callouts, captions, footnotes) below 13px (10pt
  equivalent). Express text sizes with `font-size` in px; do not use CSS `font` shorthand or
  relative units to evade this floor. Spare room on a slide goes into a larger type scale and
  more generous leading, not into one large empty band. If content does not fit, shorten it or
  allocate more space instead of shrinking it until it becomes unnecessarily hard to read.""",
    "text-heavy": """- READABILITY FLOOR: in ordinary authored generation and style-reference mode, every visible
  text element that communicates slide content (headings, body, bullets, tables, chart labels,
  metrics, callouts, captions, and footnotes) must be at least 12px (9pt equivalent). Express
  text sizes with `font-size` in px; do not use CSS `font` shorthand or relative units to evade
  this floor. If content does not fit, shorten it or allocate more space instead of shrinking it
  until it becomes unnecessarily hard-to-read.""",
}

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
    "PRE_BUNDLE_TYPE_SCALES",
    "REQUIREMENT_IDENTIFIER_RULES",
    "SECTION_STRUCTURE_BRIEF",
    "SEMANTIC_ROLE_RULES",
    "SHARED_UNIVERSALS",
    "SLIDE_OUTPUT_CONTRACT",
    "SOURCE_IMAGE_RULES",
    "STORY_RULES",
    "TYPE_SCALES",
    "TypeRamp",
    "composition_intent_block",
    "type_ramp",
    "type_scale_text",
]
