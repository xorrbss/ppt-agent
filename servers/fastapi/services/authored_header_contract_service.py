"""Resolve the deck-scoped header contract for one authored generation.

This is the single place that decides a deck's top area. It runs once, before
any slide is authored, and it is one of the two places the user's own
instructions reach the design; the other is the chapter plan resolved just
before it, whose outcome arrives here as ``chapter_breadcrumb``.
"""

from __future__ import annotations

import logging
from typing import Optional

from models.authored_header_contract import (
    AuthoredHeaderContract,
    default_header_contract,
)
from models.presentation_outline_model import PresentationOutlineModel
from utils.llm_calls.author_header_contract import generate_header_contract
from utils.llm_calls.author_slide import (
    NO_PAGE_NUMBER_MODE,
    TEMPLATE_PAGE_NUMBER_MODE,
)

LOGGER = logging.getLogger(__name__)

#: Slides shown to the contract call. The first few carry the deck's front
#: matter and the last one its closing, which is where a numbering scheme is
#: either visible or absent; sending all 100 would be cost with no new signal.
_SAMPLE_HEAD = 5
_SAMPLE_TAIL = 1


def _style_brief(style: object) -> Optional[str]:
    brief = getattr(style, "brief", None)
    return brief.strip() if isinstance(brief, str) and brief.strip() else None


def _content_sample(contents: list[str]) -> tuple[str, ...]:
    """Return a bounded, order-preserving excerpt of the deck's slide text."""
    if len(contents) <= _SAMPLE_HEAD + _SAMPLE_TAIL:
        return tuple(contents)
    return tuple(contents[:_SAMPLE_HEAD]) + tuple(contents[-_SAMPLE_TAIL:])


async def resolve_header_contract(
    outline: PresentationOutlineModel,
    brand,
    style: object = None,
    *,
    instructions: Optional[str] = None,
    chapter_breadcrumb: bool = False,
) -> AuthoredHeaderContract:
    """Decide this deck's header once, from its content, style, and instructions.

    Whether the header reserves a slot for RFP requirement identifiers is part of
    that one decision: the call reads a sample of the deck's own slide text and
    judges whether the source document actually numbers its requirements. Each
    slide's identifiers are then read straight out of that slide's text while it
    is authored, so nothing here has to recognise them by shape.
    """
    contents = [slide.content for slide in outline.slides]
    primary = getattr(brand, "primary", None) or "#2563EB"
    contract = await generate_header_contract(
        topic=getattr(brand, "topic", "") or "",
        language=getattr(brand, "language", None) or "Korean",
        primary=primary,
        fonts=getattr(brand, "fonts", None) or "Noto Sans KR",
        style_brief=_style_brief(style),
        instructions=instructions,
        content_sample=_content_sample(contents),
        chapter_breadcrumb=chapter_breadcrumb,
    )
    LOGGER.info(
        # The band is per-slide now, so what identifies a deck's header is the
        # rhythm it was frozen with, not a height no slide has to obey.
        "[authored] header contract resolved: band=%d..%dpx titleGap=%dpx "
        "contentGap=%dpx rightSlot=%s requirements=%s pageNumbers=%s "
        "eyebrow=%s",
        contract.band_min_px,
        contract.band_max_px,
        contract.title_gap_px,
        contract.content_gap_px,
        contract.right_slot.mode,
        contract.requirements.enabled,
        contract.page_number.enabled,
        contract.eyebrow.form,
    )
    return contract


def page_number_mode_for(contract: Optional[AuthoredHeaderContract]) -> str:
    """Map the deck's frozen contract onto the slide-prompt page-number mode.

    Slides are numbered only when the deck's own request asked for numbering, so
    an absent contract means an unnumbered deck rather than the historical
    always-numbered default.
    """
    if contract is not None and contract.page_number.enabled:
        return TEMPLATE_PAGE_NUMBER_MODE
    return NO_PAGE_NUMBER_MODE


def load_header_contract(theme: object) -> Optional[AuthoredHeaderContract]:
    """Rebuild a persisted contract, treating an unreadable one as absent."""
    if not isinstance(theme, dict):
        return None
    payload = theme.get("headerContract")
    if not isinstance(payload, dict):
        return None
    try:
        return AuthoredHeaderContract.model_validate(payload)
    except Exception as error:  # noqa: BLE001 — persisted data is untrusted here
        LOGGER.warning("Persisted header contract is unusable: %s", error)
        return None


__all__ = [
    "default_header_contract",
    "load_header_contract",
    "page_number_mode_for",
    "resolve_header_contract",
]
