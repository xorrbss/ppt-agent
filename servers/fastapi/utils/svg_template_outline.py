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
still finds exactly one ``##`` page title. Their numbers are single digits with a dot
(``### 1. ...``): a two-digit ``01`` would count as a required figure
(``services/svg_presentation_checkpoint._required_numeric_claims``) that a draft writing
``1`` would then miss.
"""

from __future__ import annotations

TEMPLATE_SECTIONS_CLAUSE = (
    "\nThis deck is written into a registered template whose body pages are structured, so each"
    " content slide's body (not the title slide) follows that structure instead of being one flat"
    " list: first a key-message line, one sentence stating the slide's conclusion with its main"
    " figure; then two to four sub-sections, each a ### heading numbered with a single digit and a"
    " dot (### 1. Heading, ### 2. Heading) naming one aspect of the source section, followed by"
    " that aspect's own material: its bullets, its Markdown table rows or its series of figures."
    " Use the source's own sub-headings and groupings when it has them; otherwise group the"
    " slide's facts by aspect (scope, figures, schedule, organisation, steps, risks, costs)."
    " Sub-sections are never padding: when a slide's source covers one aspect only, write one"
    " sub-section, and never split one fact across two. Keep every figure, date, label and named"
    " item of the source inside its sub-section. The bullet rules above apply within each"
    " sub-section; the ## title stays the slide's only ## heading."
)


def outline_sections_clause(enabled: bool) -> str:
    """The clause for a template-redesign outline when the knob is on, else ``""``."""
    return TEMPLATE_SECTIONS_CLAUSE if enabled else ""


__all__ = ["TEMPLATE_SECTIONS_CLAUSE", "outline_sections_clause"]
