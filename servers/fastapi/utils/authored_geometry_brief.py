"""The GEOMETRY paragraph shown to the model when no composition envelope
applies (2026-09-05).

An ordinary deck with ``AUTHORED_COMPOSITION_ENVELOPE_ENABLED`` unset carries
no server-frozen text/qid budget any more (see
``services.authored_composition_policy.SlideCapacity.prompt_brief``, which
deliberately returns "" in that case: :mod:`tests.unit
.test_authored_free_composition_default` pins that as the free-composition
default). Removing the budget also removed the ONLY place the author prompt
stated the page's own geometry contract, and a real run measured the result:
13 gate defects on the best 24-slide deck, every one geometric (clipping,
off-canvas, overlap), plus a closing page that dropped its header band
entirely.

This module states the geometry rules alone -- no character, qid, or element
counts, and no font sizes -- so it can be shown even when nothing else is.
The bottom line is never hard-coded a second time: it is read from
``utils.authored_infographic.contracts.content_bottom_px``, the same source
of truth ``author_slide_rules`` already states the footer safe zone from.
"""

from __future__ import annotations

from utils.authored_infographic.contracts import content_bottom_px

_HEADER_SENTENCE = (
    "The header band, eyebrow, title, divider, and body wrapper from the "
    "header contract are mandatory on this slide, including a dark, "
    "full-bleed closing page — never omit any of them."
)

_FRAME_SENTENCE = (
    "The frame is exactly 1280x720px; every element, including absolutely "
    "positioned ones, must lie entirely inside it."
)

_OVERLAP_SENTENCE = (
    "Nothing may overlap unless it is a deliberate layered card, and text "
    "must never be clipped by an ancestor with overflow:hidden."
)


def geometry_brief(
    *,
    page_number_mode: str | None = None,
    header_in_force: bool = False,
    canvas_width_px: int = 1280,
    canvas_height_px: int = 720,
) -> str:
    """The GEOMETRY paragraph for one slide with no composition envelope.

    ``page_number_mode`` decides the bottom content line the same way the
    design-system rules already do (648px with a page-number footer, 688px
    without one). ``header_in_force`` states whether THIS slide is bound by
    the deck's header contract; when it is, that rule leads the paragraph,
    since a dropped header band is the more expensive failure to catch late.
    """
    bottom_inset = 72 if page_number_mode in {"template", "companyStandard"} else 32
    bottom = canvas_height_px - bottom_inset
    bottom_sentence = (
        "The content field must end at or above the deck's bottom content "
        f"line, y={bottom}px."
    )
    sentences = [_HEADER_SENTENCE] if header_in_force else []
    frame_sentence = (
        f"The frame is exactly {canvas_width_px}x{canvas_height_px}px; every element, "
        "including absolutely positioned ones, must lie entirely inside it."
    )
    sentences.extend([frame_sentence, bottom_sentence, _OVERLAP_SENTENCE])
    return "GEOMETRY: " + " ".join(sentences)


def relaxed_geometry_brief(plan: object, index: int) -> str:
    """The GEOMETRY paragraph a relaxed-envelope slide gets instead of a
    blank composition brief.

    Reads the deck's real page-number mode and header contract off ``plan``
    when they are there, and degrades to their safe defaults (numbered
    footer line, no header contract in force) for stand-in plans that don't
    carry them -- the same tolerance ``author_deck._capacity_brief`` already
    gives ``capacity_plan``/``text_plan``. ``plan`` is typed as ``object``
    here (duck-typed via ``getattr``) so this module never has to import
    ``AuthoredDeckPlan`` from the caller it is meant to stay thin under.
    """
    header_contract_at = getattr(plan, "header_contract_at", None)
    contract = header_contract_at(index) if callable(header_contract_at) else None
    roles = getattr(plan, "roles", None)
    role = roles[index] if roles else ""
    header_in_force = contract is not None and contract.applies_to_role(role)
    return geometry_brief(
        page_number_mode=getattr(plan, "page_number_mode", None),
        header_in_force=header_in_force,
        canvas_width_px=getattr(plan, "canvas_width_px", 1280),
        canvas_height_px=getattr(plan, "canvas_height_px", 720),
    )


__all__ = ["geometry_brief", "relaxed_geometry_brief"]
