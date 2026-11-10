"""The type floor the browser fitters shrink to, and the page-number ceiling.

Moved out of ``utils/authored_fragmentation_gate.py`` unchanged so the shared
capture path (``utils/slide_capture.py``, ``utils/slide_capture_fit.py``) does
not import the authored fragmentation gate; the gate re-exports both names.
"""

from __future__ import annotations

from models.authored_vnext.capability_registry import AUTHORED_FONT_FLOOR_PX
from utils.authored_dom_footer_zone import _PAGE_NUMBER_MAX_FONT_PX
from utils.get_env_programme import (
    get_authored_type_floor_px,
    is_authored_fragmentation_gate_enabled,
)
from utils.runtime_settings import RuntimeSettings


def page_number_max_font_px(settings: RuntimeSettings | None = None) -> float:
    """The largest type a page-number box may carry and still be furniture.

    The clamp raises the 12px page number to the gate floor, so detection in
    the footer gate, the fitter and this gate must accept that size too.
    """

    return max(_PAGE_NUMBER_MAX_FONT_PX, fit_font_floor_px(settings))


def fit_font_floor_px(settings: RuntimeSettings | None = None) -> float:
    """The floor the in-render fitter shrinks to: the gate floor when the gate
    is on, the registry's ``AUTHORED_FONT_FLOOR_PX`` otherwise."""

    if is_authored_fragmentation_gate_enabled(settings):
        return float(get_authored_type_floor_px(settings))
    return float(AUTHORED_FONT_FLOOR_PX)


__all__ = ["fit_font_floor_px", "page_number_max_font_px"]
