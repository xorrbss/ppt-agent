"""Title fit: the title's size is independent of the body's type scale.

A three-run title at 40px used to be crushed by whatever ``type_scale`` the
ladder picked for the *body* (rung 2's 0.75-0.8 range), which shrank the body
to its floor without ever fixing the title's own overflow. The renderer now
sizes the title on its own axis: ``deck.title_px`` (a deck-wide size fit by
``services.composer.pipeline_inputs.deck_title_px``) or the template size,
scaled by ``type_scale`` only once the ladder has moved past rungs 0-2 (rung 3
still uses the template/type-scale path, just no longer alone), then stepped
down toward ``TITLE_FLOOR_PX`` if this slide's own title still wraps past two
lines at the actual chrome level's band width -- mirroring the same
``wrapped_line_count`` step-down ``deck_title_px`` already runs across the
whole deck, just re-checked per slide because a slide can render at a chrome
level ``deck_title_px`` did not assume.
"""

from __future__ import annotations

from services.authored_vnext.source_render_geometry import wrapped_line_count
from services.composer.plan import (
    ComposerDeckPlan,
    ComposerSlidePlan,
    FitParameters,
    TEMPLATE_TYPE_SCALE_PX,
    TITLE_FLOOR_PX,
)
from services.composer.render_css import (
    ChromeGeometry,
    SUBTITLE_LINE_HEIGHT,
    TITLE_LINE_HEIGHT,
    TITLE_STACK_GAP_PX,
)

#: Size step used to walk a title down toward its floor while it still wraps.
TITLE_STEP_PX = 2.0
#: Subtitle tracks the fitted title size rather than a fixed template size.
SUBTITLE_SCALE = 0.65
SUBTITLE_FLOOR_PX = 18.0
#: A running header shares the title band (CSS ``.cx-running-header{max-width:30%}``).
RUNNING_HEADER_SHARE = 0.7


def title_base_px(deck: ComposerDeckPlan, params: FitParameters) -> float:
    """Title size before line-count fitting.

    ``deck.title_px`` wins when set, else the template size. Body-only shrink
    (``params.type_scale`` at rungs 0-2) never reaches the title; only rungs
    3 and up apply it, and the result never drops below ``TITLE_FLOOR_PX``.
    """

    base = deck.title_px if deck.title_px is not None else TEMPLATE_TYPE_SCALE_PX["title"]
    if params.rung >= 3:
        base = base * float(params.type_scale)
    return max(base, TITLE_FLOOR_PX)


def title_line_count(texts: list[str], size_px: float, width_px: float) -> int:
    """Total line boxes the title stack renders at ``size_px``.

    Each run is its own block element (see ``render_chrome._title_band``), so
    this sums one width-based wrap count per run rather than wrapping the
    concatenated text. Wrapping is a width computation and does not depend on
    line-height, so this is the same count ``.cx-title`` renders at any
    ``TITLE_LINE_HEIGHT``.
    """

    return sum(wrapped_line_count(text, width_px=width_px, font_px=size_px) for text in texts)


def fit_title_px(
    texts: list[str],
    base_px: float,
    width_px: float,
    *,
    max_lines: int = 2,
    floor: float = TITLE_FLOOR_PX,
    step: float = TITLE_STEP_PX,
) -> float:
    """Largest size <= ``base_px``, stepped down to ``floor``, keeping the
    title's runs within ``max_lines`` total; returns ``floor`` once reached,
    wrapping past ``max_lines`` there rather than shrinking further.

    Reducing toward ``floor`` is tried first and exhausted before returning:
    the band only grows past ``max_lines`` worth of height once the title is
    already at its floor size and still wraps, per ``deck_title_px``'s rule.
    """

    size = base_px
    while size > floor:
        lines = title_line_count(texts, size, width_px)
        if lines <= max_lines:
            return size
        size = max(floor, size - step)
    return floor


def title_band_height_px(
    title_lines: int,
    title_px: float,
    *,
    padding_y: float,
    subtitle_lines: int = 0,
    subtitle_px: float = 0.0,
) -> float:
    """Estimate the rendered title band height: padding + stacked line boxes.

    Mirrors what ``render_css.stylesheet`` emits for ``.cx-title-band``
    padding, the ``.cx-title-stack`` gap, and the ``.cx-title`` /
    ``.cx-subtitle`` line-heights, so a caller can predict whether the band
    grows past its ``min-height`` before any Chromium round-trip. This is the
    one place in this module that turns a line count into a pixel height, and
    it always multiplies by the same line-height the CSS renders with.
    """

    height = 2.0 * padding_y + title_lines * title_px * TITLE_LINE_HEIGHT
    if subtitle_lines:
        height += TITLE_STACK_GAP_PX + subtitle_lines * subtitle_px * SUBTITLE_LINE_HEIGHT
    return height


def _has_running_header(plan: ComposerSlidePlan) -> bool:
    return any(component.furniture_kind == "running_header" for component in plan.furniture)


def title_band_inner_width(geometry: ChromeGeometry, *, has_header: bool) -> float:
    """Width available to the title stack, matching ``deck_title_px``'s estimate."""

    width = float(geometry.content_width)
    return width * RUNNING_HEADER_SHARE if has_header else width


def fitted_title_px(
    plan: ComposerSlidePlan, deck: ComposerDeckPlan, params: FitParameters, geometry: ChromeGeometry
) -> float:
    """The title size ``render_chrome`` and ``render_components`` both read.

    Source-relative and source-geometry rungs (4, 5) already size every run
    from its own source point size; this fit only applies to the template
    rungs (0-3), so it returns the unfit base size there without checking.
    """

    base = title_base_px(deck, params)
    if plan.title is None or params.source_relative or params.source_geometry:
        return base
    width = title_band_inner_width(geometry, has_header=_has_running_header(plan))
    texts = [paragraph.text for paragraph in plan.title.paragraphs]
    return fit_title_px(texts, base, width)


def subtitle_px_from_title(title_px: float) -> float:
    return max(title_px * SUBTITLE_SCALE, SUBTITLE_FLOOR_PX)


__all__ = [
    "RUNNING_HEADER_SHARE",
    "SUBTITLE_FLOOR_PX",
    "SUBTITLE_SCALE",
    "TITLE_STEP_PX",
    "fit_title_px",
    "fitted_title_px",
    "subtitle_px_from_title",
    "title_band_height_px",
    "title_band_inner_width",
    "title_base_px",
    "title_line_count",
]
