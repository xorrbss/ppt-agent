"""G11 — one checked-in exemplar slide per authored style.

The 2026-09-05 diagnosis measured zero exemplars in the effective prompt: the
model is asked for "McKinsey / Apple-keynote quality" and shown nothing that
qualifies. An exemplar is the cheapest way to state density and visual language,
because it demonstrates rather than describes them.

An exemplar is ONE slide of a deck a human rated excellent for that style, with
every image elided (the picture bytes teach nothing and would cost megabytes),
capped so it stays a demonstration rather than a template to copy, and required
to pass the same ``admit_authored_html`` policy a generated slide passes — a
file that could not be rendered is not an example of anything.

The loader lives in its own module because ``utils/authored_styles.py`` is
already past the 500-line file-size ratchet; it is re-exported from there so the
style layer keeps one public surface.
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from pathlib import Path

from utils.get_env_programme import is_authored_exemplar_enabled


LOGGER = logging.getLogger(__name__)

AUTHORED_STYLE_EXEMPLARS_DIRECTORY = (
    Path(__file__).resolve().parent.parent / "authored_styles" / "exemplars"
)

#: The hard cap from design D24. A longer exemplar stops being a reference and
#: starts being most of the prompt.
MAX_EXEMPLAR_CHARS = 12_000

#: Style ids are English identifiers everywhere else in the style layer; the
#: same pattern is re-applied here so a caller-supplied id can never leave the
#: exemplars directory.
_STYLE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")


@lru_cache(maxsize=64)
def _read_exemplar(style_id: str) -> str | None:
    """Read one exemplar file, treating anything unusable as absent.

    Cached on the id alone, never on the feature flag, so toggling the knob at
    runtime takes effect without carrying a stale answer.
    """
    if not _STYLE_ID_PATTERN.fullmatch(style_id):
        return None
    path = AUTHORED_STYLE_EXEMPLARS_DIRECTORY / f"{style_id}.html"
    try:
        html = path.read_text(encoding="utf-8")
    except OSError:
        return None
    html = html.strip()
    if not html:
        return None
    if len(html) > MAX_EXEMPLAR_CHARS:
        LOGGER.warning(
            "Authored style exemplar %s is %d chars, above the %d cap; ignoring it",
            style_id,
            len(html),
            MAX_EXEMPLAR_CHARS,
        )
        return None
    return html


def load_style_exemplar(style_id: str | None) -> str | None:
    """The exemplar slide HTML for a style, or ``None`` when there is none."""
    if not style_id or not is_authored_exemplar_enabled():
        return None
    return _read_exemplar(style_id)


def exemplar_block(style_id: str | None) -> str:
    """The EXEMPLAR paragraph appended to the design system, or ``""``.

    The wording names exactly what may be taken from it. An exemplar copied as a
    layout would give the whole deck one silhouette, which is the defect the
    2026-08-22 story plan was reverted for.
    """
    html = load_style_exemplar(style_id)
    if html is None:
        return ""
    return (
        "\nEXEMPLAR (one slide from a deck rated excellent for this style; reuse its "
        "visual language and density, never its text or layout): "
        f"{html}"
    )


__all__ = [
    "AUTHORED_STYLE_EXEMPLARS_DIRECTORY",
    "MAX_EXEMPLAR_CHARS",
    "exemplar_block",
    "load_style_exemplar",
]
