"""Archetype component library for authored infographic slides.

A planned figure slide is authored against a server-owned stylesheet: the
model reproduces a filled skeleton (``contracts.slot_contract``), the static
checker holds it to that skeleton (``checker``), and the library CSS rendered
with the deck's brand tokens is injected into the slide document before
renderer admission (``finalize_infographic_slide_html``).
"""

from __future__ import annotations

from models.authored_infographic_plan import AuthoredSlideInfographic

from utils.authored_infographic.checker import (
    CONTRACT_ERROR_PREFIX,
    InfographicContractError,
    check_infographic_contract,
    enforce_infographic_contract,
)
from utils.authored_infographic.contracts import slot_contract
from utils.authored_infographic.library import (
    has_library,
    inject_library_css,
    library_classes,
    library_css,
)
from utils.authored_infographic.skeleton import render_skeleton
from utils.authored_infographic.tokens import tokens_for_brand


def finalize_infographic_slide_html(
    html: str,
    *,
    brand: object,
    slide: AuthoredSlideInfographic | None,
) -> str:
    """Hold a figure slide to its contract, then give it the library stylesheet.

    A quiet page, or a slide with no plan, is returned untouched. The check
    runs before injection so a violation is reported against the model's own
    document, and the returned HTML is what renderer admission validates.
    """
    if slide is None or not slide.has_figure:
        return html
    enforce_infographic_contract(html, slide)
    return inject_library_css(html, library_css(brand))


__all__ = [
    "CONTRACT_ERROR_PREFIX",
    "InfographicContractError",
    "check_infographic_contract",
    "enforce_infographic_contract",
    "finalize_infographic_slide_html",
    "has_library",
    "inject_library_css",
    "library_classes",
    "library_css",
    "render_skeleton",
    "slot_contract",
    "tokens_for_brand",
]
