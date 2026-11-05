"""The outline clause that gives a template-redesign deck the template's page structure.

A framed template's body pages (the real customer deck measured 2026-11-05) open with a
key-message bar and hold two to four numbered sub-sections, each with its own component.
The outline prompt asks for a bullet list per slide and nothing else, so a source's
sub-headings and key sentences flatten into bullets, the body composition classifier
(``services/svg_templates/body_composition.content_kinds``) sees one section per page, and
the author gets no sections to compose. Behind ``SVG_TEMPLATE_OUTLINE_SECTIONS`` (off by
default, ``utils/get_env_concurrency.py``) the clause below is appended to the outline's
system prompt for template-redesign decks only.

Sub-sections are ``###`` headings so that ``utils.outline_utils.explicit_outline_title``
still finds exactly one ``##`` page title; :func:`demote_extra_titles` makes that hold on
the returned outline too. Their numbers are single digits with a dot (``### 1. ...``): a
two-digit ``01`` would count as a required figure
(``services/svg_presentation_checkpoint._required_numeric_claims``) that a draft writing
``1`` would then miss.

Markdown table rows are named as a sub-section's material only when the content structure
policy's ``tables`` part is on: every queued deck carries the writing policy, whose pipe
rule forbids table pipes unless that part adds its exception
(``utils/content_structure_policy.py``), and table pages without its cell-text clause fell
back in an earlier measurement.
"""

from __future__ import annotations

import re

from models.presentation_outline_model import PresentationOutlineModel
from utils.content_structure_policy import StructurePolicy

_WITH_TABLES = "its bullets, its Markdown table rows or its series of figures"
#: Measured 2026-11-05 (template A, policy off, 12 decks per arm): outlines held 23-50 Markdown
#: table rows per deck and the decks 5.9 native tables each at 8.9 first drafts of 10; a clause
#: that named bullets and figures only dropped the unstructured input's rows from 42 to 0 and its
#: tables from 8.3 to 1.0 per deck. A table the source itself tabulates stays a table.
_WITHOUT_TABLES = ("its bullets, a Markdown table where the source itself tabulates those facts (keep its"
                   " pipes and separator row), or its series of figures")

_CLAUSE = (
    "\nThis deck is written into a registered template whose body pages are structured, so each"
    " content slide's body (not the title slide) follows that structure instead of being one flat"
    " list: first a key-message line, one sentence stating the slide's conclusion, with its main"
    " figure when it has one; then one to four sub-sections, as many as the source gives aspects"
    " (two to four on most slides, one when the source covers one aspect only), each a ### heading"
    " numbered with a single digit and a dot (### 1. Heading, ### 2. Heading) naming one aspect of"
    " the source section, followed by that aspect's own material: {material}."
    " Use the source's own sub-headings and groupings when it has them; otherwise group the"
    " slide's facts by aspect (scope, figures, schedule, organisation, steps, risks, costs) and"
    " name each group with words the source uses for it, never with a heading the source does not"
    " support. Sub-sections are never padding, and never split one fact across two. Keep every"
    " figure, date, label and named item of the source inside its sub-section. For content slides"
    " this replaces the rule above that the body is bullets and nothing else: the key-message line"
    " and the ### headings are the only non-bullet lines, and the bullets stay the material inside"
    " each sub-section, written as that rule says. The ## title stays the slide's only ## heading."
)
TEMPLATE_SECTIONS_CLAUSE = _CLAUSE.format(material=_WITHOUT_TABLES)
TEMPLATE_SECTIONS_CLAUSE_WITH_TABLES = _CLAUSE.format(material=_WITH_TABLES)


def outline_sections_clause(enabled: bool, structure_policy: StructurePolicy | None = None) -> str:
    """The clause for a template-redesign outline when the knob is on, else ``""``. Table rows are
    named as material only with the content structure policy's ``tables`` part on."""
    if not enabled:
        return ""
    if structure_policy is not None and structure_policy.tables:
        return TEMPLATE_SECTIONS_CLAUSE_WITH_TABLES
    return TEMPLATE_SECTIONS_CLAUSE


_LEVEL_TWO = re.compile(r"^(\s{0,3})##(?=\s+\S)")


def demote_extra_titles(outline: PresentationOutlineModel) -> PresentationOutlineModel:
    """Every ``##`` heading after a slide's first one becomes ``###``.

    A model asked for ``###`` sub-sections may still write them as ``##``; then
    :func:`utils.outline_utils.explicit_outline_title` finds several titles, returns none, and a
    framed template with a title field fails ``svg_template_title_required``. The first ``##``
    line keeps its level; a slide without one is returned as it is (no title is invented). Lines
    of a Markdown table (starting with ``|``) are never touched. A new model is returned; an
    outline with nothing to demote serialises identically.
    """
    slides = []
    for slide in outline.slides:
        lines, seen = [], False
        for line in slide.content.splitlines(keepends=True):
            if not line.lstrip().startswith("|") and _LEVEL_TWO.match(line):
                if seen:
                    line = _LEVEL_TWO.sub(r"\1###", line, count=1)
                seen = True
            lines.append(line)
        content = "".join(lines)
        slides.append(slide if content == slide.content else slide.model_copy(update={"content": content}))
    return PresentationOutlineModel(title=outline.title, slides=slides)


__all__ = ["TEMPLATE_SECTIONS_CLAUSE", "TEMPLATE_SECTIONS_CLAUSE_WITH_TABLES", "demote_extra_titles",
           "outline_sections_clause"]
