"""Rung 5 box correction: the two ways a source rectangle used to lie.

Both are fixture-only (no browser): the renderer's own prediction is the
thing under test, and it is the same prediction the browser lays out.

1. ``MIN_FONT_PX`` silently invalidated the per-box shrink. ``_fit_growable_
   box`` pinned a box to the headroom it had and assumed the text would
   shrink by ``headroom / natural`` to match, but ``render_css.text_px``
   clamps every size at the floor, so the text came out *larger* than the box
   claimed and spilled onto the component below (``.cx-geo``/``.cx-text`` are
   ``overflow:visible``).
2. ``process`` was not a growable kind at all, so a wide, one-line-tall
   source strip carrying several runs was left in a box a fraction of the
   height ``render_process``'s padded, bordered, wrapped card grid needs.
"""

from __future__ import annotations

import re

from services.composer.estimate_components import GROUP_CHROME_PX
from services.composer.plan import (
    ComposerComponent,
    ComposerDeckPlan,
    ComposerSlidePlan,
    ComposerTokens,
    FitParameters,
    Paragraph,
    SourceRect,
    SourceRunRef,
)
from services.composer.render import build_context, render_slide
from services.composer.render_css import MIN_FONT_PX, density_spec
from services.composer.render_geometry import (
    _fit_growable_box,
    _natural_height_px,
    _placed,
    page_box,
    rect_box,
)

TOKENS = ComposerTokens(
    background="#FFFFFF",
    surface="#F5F7FA",
    text_primary="#0F172A",
    text_secondary="#334155",
    accent="#2563EB",
    accent_text="#2563EB",
    accent_soft="#DBEAFE",
    rule="#CBD5E1",
)
#: 16:9 at 13.333 in -- ``source_px_scale`` 1.0, so a source point maps to
#: ``PX_PER_PT x page_scale`` canvas px and the arithmetic below is exact.
PAGE_EMU = (12192000, 6858000)
PARAMS = FitParameters(rung=5, chrome_level=3, density=1, source_geometry=True, page_scale=0.9)
#: ``page_box`` for ``PARAMS``: a 1152 x 648 page centred on the 1280 x 720 canvas.
PAGE = (64.0, 36.0, 1152.0, 648.0)


def para(binding: str, text: str) -> Paragraph:
    return Paragraph(text=text, source=SourceRunRef(block_id=binding, binding_id=binding))


def rect(x: float, y: float, width: float, height: float) -> SourceRect:
    return SourceRect(x=x, y=y, width=width, height=height)


def title_component() -> ComposerComponent:
    """A title occupying canvas y 96.48 .. 148.32, exactly deck-43's own."""

    return ComposerComponent(
        component_id="title",
        kind="title",
        semantic_role="title",
        paragraphs=[para("t1", "생성은 첫 선택이 아니라 마지막 선택입니다")],
        source_rect=rect(0.045, 96.48 / 648 - 36 / 648, 0.91, 0.08),
        source_size_pt=19.44,
    )


def plan_with(*components: ComposerComponent) -> ComposerSlidePlan:
    return ComposerSlidePlan(
        slide_index=0,
        source_unit_id="source-slide:14",
        archetype="text",
        title=title_component(),
        components=list(components),
    )


def deck_for(plan: ComposerSlidePlan) -> ComposerDeckPlan:
    return ComposerDeckPlan(
        slides=[plan], tokens=TOKENS, source_page_emu=PAGE_EMU, source_px_scale=1.0
    )


def fit(plan: ComposerSlidePlan, component_id: str):
    """``_fit_growable_box`` for one component against every other box on the slide."""

    ctx = build_context(plan, TOKENS, PARAMS, deck_for(plan))
    placed = _placed(plan)
    boxes = [rect_box(component.source_rect, page_box(ctx.deck, PARAMS)) for component in placed]
    index = next(i for i, component in enumerate(placed) if component.component_id == component_id)
    others = boxes[:index] + boxes[index + 1 :]
    return ctx, boxes[index], _fit_growable_box(placed[index], ctx, boxes[index], others, ladder_scale=1.0)


def geo_boxes(html: str) -> dict[str, tuple[float, float]]:
    """``qid -> (top, bottom)`` of every ``.cx-geo`` wrapper's declared box."""

    pattern = re.compile(
        r'<div data-qid="([^"]+)-geo"[^>]*style="left:[\d.]+px;top:([\d.]+)px;'
        r'width:[\d.]+px;height:([\d.]+)px'
    )
    return {
        match.group(1): (float(match.group(2)), float(match.group(2)) + float(match.group(3)))
        for match in pattern.finditer(html)
    }


def font_sizes(html: str, binding: str) -> list[float]:
    return [
        float(size)
        for size in re.findall(
            rf'data-source-block-id="{re.escape(binding)}"[^>]*style="font-size:([\d.]+)px', html
        )
    ]


def kicker(*, source_size_pt: float, paragraphs: int = 2) -> ComposerComponent:
    """A two-line kicker in a one-line-tall strip ending on the title's own top."""

    return ComposerComponent(
        component_id="kicker",
        kind="paragraph",
        semantic_role="body",
        paragraphs=[para(f"k{index}", f"절감 동인 {index}") for index in range(paragraphs)],
        # canvas y 70.56 .. 96.48: the strip bottom is the title box's top edge.
        source_rect=rect(0.045, (70.56 - 36) / 648, 0.675, 25.92 / 648),
        source_size_pt=source_size_pt,
    )


def blocker(*, bottom_px: float) -> ComposerComponent:
    """A neighbour directly above the kicker, so only ``bottom_px`` .. 70.56 is free."""

    top = 40.0
    return ComposerComponent(
        component_id="blocker",
        kind="paragraph",
        semantic_role="body",
        paragraphs=[para("x1", "위쪽 이웃")],
        source_rect=rect(0.045, (top - 36) / 648, 0.675, (bottom_px - top) / 648),
        source_size_pt=9.0,
    )


# --------------------------------------------------------------------------
# Defect 1: the font floor invalidates a linear per-box shrink.
# --------------------------------------------------------------------------


def test_shrink_below_the_font_floor_reports_the_height_the_text_really_takes() -> None:
    """The regression: 10.5 pt at ``page_scale`` 0.9 is 12.6 px, and two lines
    of it need ~40.3 px in a 25.92 px strip. The linear answer is a 0.643 box
    scale -- 8.1 px of type -- but ``text_px`` clamps at ``MIN_FONT_PX``, so
    the text really renders at 10.8 px and takes ~35.2 px. With only 5 px of
    free canvas above the strip the box cannot hold that, and the correction
    must say so instead of pinning the box to the 30.9 px it has."""

    plan = plan_with(kicker(source_size_pt=10.5), blocker(bottom_px=65.56))
    ctx, original, fitted = fit(plan, "kicker")

    natural = _natural_height_px(kicker(source_size_pt=10.5), ctx, width_px=original[2], box_scale=1.0)
    floored = _natural_height_px(
        kicker(source_size_pt=10.5), ctx, width_px=original[2], box_scale=fitted.scale
    )
    linear_scale = original[3] / natural

    # The naive answer would have set the type below the absolute floor...
    assert 12.6 * linear_scale < MIN_FONT_PX
    # ...so the real rendering is taller than both the source box and the
    # headroom the old code pinned the box to.
    assert floored > original[3]
    assert floored > (original[1] - 65.56) + original[3]
    # The box carries that real height, never the smaller number it wished for.
    assert fitted.box[3] == floored
    # The unfittable remainder is reported rather than silently overflowing.
    assert fitted.residual_px > 0.0
    assert abs(fitted.residual_px - (floored - ((original[1] - 65.56) + original[3]))) < 1e-6
    # And the box was lifted as far as the neighbour above allowed, no further.
    assert fitted.box[1] == 65.56


def test_a_floored_kicker_is_lifted_into_free_canvas_instead_of_hitting_the_title() -> None:
    """deck-43 slide 13 exactly: the same 10.5 pt two-line kicker, but with the
    free canvas above the strip that the real page has. Growing down is
    blocked by the title, so the box is lifted until its bottom rests on the
    title's own top -- at the source's own type size, no shrink at all."""

    plan = plan_with(kicker(source_size_pt=10.5))
    deck = deck_for(plan)
    html = render_slide(plan, TOKENS, PARAMS, deck)
    boxes = geo_boxes(html)

    kicker_top, kicker_bottom = boxes["cx-kicker"]
    title_top, _title_bottom = boxes["cx-title"]
    assert kicker_top < 70.56  # lifted above its own source rectangle
    assert kicker_bottom <= title_top + 1e-6  # and never reaching the title
    assert kicker_bottom - kicker_top > 25.92  # grown past the strip it was given
    # No shrink was needed, so the runs keep the source's own size, above the floor.
    for binding in ("k0", "k1"):
        assert font_sizes(html, binding) == [round(10.5 * 96 / 72 * 0.9, 2)]
        assert font_sizes(html, binding)[0] > MIN_FONT_PX


# --------------------------------------------------------------------------
# Defect 2: ``process`` had no natural height and was never corrected.
# --------------------------------------------------------------------------


def process_kicker(steps: int) -> ComposerComponent:
    """``steps`` runs of one source line, classified as a process block, in the
    same one-line-tall strip ending on the title's top edge."""

    return ComposerComponent(
        component_id="steps",
        kind="process",
        semantic_role="body",
        groups=[[para(f"s{index}", f"단계 {index}")] for index in range(steps)],
        source_rect=rect(0.045, (70.56 - 36) / 648, 0.675, 24.19 / 648),
        source_size_pt=9.45,
    )


def _expected_process_height(ctx, *, rows: int, font_px: float) -> float:
    """One step per row-cell, one line each: rows of (line + card chrome),
    separated by the ``.cx-process`` grid gap. Built from the shared constants
    the stylesheet itself uses, not from the code under test."""

    density = density_spec(1)
    chrome = GROUP_CHROME_PX[1]
    return rows * (font_px * density.line_height + chrome) + density.gap * (rows - 1)


def test_process_natural_height_follows_the_card_grid_the_renderer_draws() -> None:
    """Five steps wrap to two rows of three (``estimate_components.
    process_per_row``), each a padded, bordered card. That is many times the
    24 px strip the source drew, and it is what the prediction must say."""

    plan = plan_with(process_kicker(5))
    ctx, original, _fitted = fit(plan, "steps")
    font_px = round(9.45 * 96 / 72 * 0.9, 6)

    height = _natural_height_px(process_kicker(5), ctx, width_px=original[2], box_scale=1.0)
    assert abs(height - _expected_process_height(ctx, rows=2, font_px=font_px)) < 0.01
    assert height > 3 * original[3]

    # Two steps share one row, so the same component is one card tall.
    two = _natural_height_px(process_kicker(2), ctx, width_px=original[2], box_scale=1.0)
    assert abs(two - _expected_process_height(ctx, rows=1, font_px=font_px)) < 0.01


def test_a_process_strip_is_corrected_instead_of_painting_over_the_title() -> None:
    """The measured deck-43 slides 14/15/16 defect: before the fix ``process``
    was outside ``_GROWABLE_KINDS``, so this box came back untouched (scale
    1.0, source height) while its card grid painted straight across the title
    below it."""

    plan = plan_with(process_kicker(5))
    _ctx, original, fitted = fit(plan, "steps")
    assert fitted.box != original  # not returned unchanged any more

    html = render_slide(plan, TOKENS, PARAMS, deck_for(plan))
    boxes = geo_boxes(html)
    steps_top, steps_bottom = boxes["cx-steps"]
    title_top, _title_bottom = boxes["cx-title"]
    assert steps_bottom <= title_top + 1e-6
    assert steps_top < 70.56
    assert steps_bottom - steps_top > 24.19
    assert fitted.residual_px == 0.0
