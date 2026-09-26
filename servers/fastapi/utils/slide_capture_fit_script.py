"""Browser-side authored local fitter used by the Chromium DOM probe.

The fitter keeps HTML/CSS as the source of truth.  It evaluates the bounded
candidate set, appends only QID-addressable inline declarations, and reports the
exact appends for byte-preserving reconstruction by the API.
"""

from __future__ import annotations

import json

from services.authored_local_fit_policy import (
    FONT_FLOOR_PT,
    GEOMETRY_TOLERANCE_PX,
    MAX_SHRINK,
    POLICY_REVISION,
)
from utils.authored_dom_footer_zone import (
    NUMBERED_CONTENT_BOTTOM_PX,
    _PAGE_NUMBER_MAX_DISTANCE_FROM_BOTTOM_PX,
    _PAGE_NUMBER_MAX_FONT_PX,
    _PAGE_NUMBER_MIN_TOP_FRACTION,
)
from utils.authored_dom_text_escape import (
    ESCAPE_TOLERANCE_PX,
    _PAINTED_TAGS,
    _TRANSPARENT,
)
from utils.slide_capture_fit_spacing import (
    COMBINED_CANDIDATES,
    FONT_ONLY_CANDIDATES,
    LIGHT_SPACING_CANDIDATES,
    SPACING_MEMBERS,
)
from utils.slide_capture_fit_observe import OBSERVE_MEMBERS
from utils.slide_capture_fit_compare import COMPARE_MEMBERS
from utils.slide_capture_fit_parent import PARENT_MEMBERS
from utils.slide_capture_fit_geometry import GEOMETRY_MEMBERS
from utils.slide_capture_fit_morph import MORPH_MEMBERS
from utils.slide_capture_fit_search import SEARCH_MEMBERS
from utils.slide_capture_fit_runner import RUNNER_MEMBERS
from utils.slide_capture_fit_fragment_a import FRAGMENT_A
from utils.slide_capture_fit_fragment_b import FRAGMENT_B

FIT_VERSION = POLICY_REVISION
MAX_FIT_REGIONS = 16
MAX_FIT_MEASUREMENTS = 160
DEFAULT_FIT_BUDGET_MS = 6000
MIN_SPACING_PX = 2.0
_CONSTANTS_MARKER = "__LOCAL_FIT_CONSTANTS__"
_OBSERVE_MARKER = "__LOCAL_OBSERVE_MEMBERS__"
_COMPARE_MARKER = "__LOCAL_COMPARE_MEMBERS__"
_SEARCH_MARKER = "__LOCAL_SEARCH_MEMBERS__"
_RUNNER_MARKER = "__LOCAL_RUNNER_MEMBERS__"
_SPACING_MARKER = "__LOCAL_SPACING_MEMBERS__"
_PARENT_MARKER = "__LOCAL_PARENT_MEMBERS__"
_GEOMETRY_MARKER = "__LOCAL_GEOMETRY_MEMBERS__"
_MORPH_MARKER = "__LOCAL_MORPH_MEMBERS__"


def fit_constants() -> dict[str, object]:
    return {
        "version": FIT_VERSION,
        "default_floor_pt": FONT_FLOOR_PT,
        "default_max_shrink": MAX_SHRINK,
        "default_preferred_max_shrink": MAX_SHRINK,
        "default_pt_per_css_px": 0.75,
        "default_budget_ms": DEFAULT_FIT_BUDGET_MS,
        "max_regions": MAX_FIT_REGIONS,
        "max_measurements": MAX_FIT_MEASUREMENTS,
        "geometry_tolerance_px": GEOMETRY_TOLERANCE_PX,
        "escape_tolerance_px": ESCAPE_TOLERANCE_PX,
        "min_spacing_px": MIN_SPACING_PX,
        "numbered_content_bottom_px": NUMBERED_CONTENT_BOTTOM_PX,
        "page_number_max_font_px": _PAGE_NUMBER_MAX_FONT_PX,
        "page_number_max_distance_px": _PAGE_NUMBER_MAX_DISTANCE_FROM_BOTTOM_PX,
        "page_number_min_top_fraction": _PAGE_NUMBER_MIN_TOP_FRACTION,
        "painted_tags": sorted(_PAINTED_TAGS),
        "transparent_backgrounds": sorted(_TRANSPARENT),
        "light_candidates": [list(value) for value in LIGHT_SPACING_CANDIDATES],
        "font_candidates": [list(value) for value in FONT_ONLY_CANDIDATES],
        "combined_candidates": [list(value) for value in COMBINED_CANDIDATES],
        "parent_reflow_factors": [1.0, 0.85, 0.70, 0.55, 0.50, 0.40],
    }


_FRAGMENT = FRAGMENT_A + FRAGMENT_B
def fit_script_fragment() -> str:
    constants = json.dumps(fit_constants(), sort_keys=True, separators=(",", ":"))
    return (
        _FRAGMENT.replace(_CONSTANTS_MARKER, constants)
        .replace(_OBSERVE_MARKER, OBSERVE_MEMBERS)
        .replace(_COMPARE_MARKER, COMPARE_MEMBERS)
        .replace(_SEARCH_MARKER, SEARCH_MEMBERS)
        .replace(_SPACING_MARKER, SPACING_MEMBERS)
        .replace(_PARENT_MARKER, PARENT_MEMBERS)
        .replace(_GEOMETRY_MARKER, GEOMETRY_MEMBERS)
        .replace(_MORPH_MARKER, MORPH_MEMBERS)
        .replace(_RUNNER_MARKER, RUNNER_MEMBERS)
)
__all__ = [
    "DEFAULT_FIT_BUDGET_MS",
    "FIT_VERSION",
    "GEOMETRY_TOLERANCE_PX",
    "MAX_FIT_MEASUREMENTS",
    "MAX_FIT_REGIONS",
    "MIN_SPACING_PX",
    "fit_constants",
    "fit_script_fragment",
]
