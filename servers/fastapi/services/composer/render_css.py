"""Chrome geometry, density, type sizes and the stylesheet of a Composer slide.

Only geometry and paint live here; markup is built in ``render_chrome``,
``render_components`` and ``render_geometry``. Every property used is in
``utils.renderer_css_policy.ALLOWED_CSS_PROPERTIES``; nothing references a
URL, a font file or a transform, so the stylesheet is admitted as written.

Fonts: the family stack names ``Pretendard`` first with ``Malgun Gothic`` as
its local fallback, the same stack ``utils.llm_calls.author_slide_prompt``
hands authored slides. The headless renderer resolves installed faces and the
authored-hybrid exporter embeds the bundled Pretendard files
(``servers/nextjs/public/fonts/pretendard``); the admission policy refuses
``@font-face``, so none is declared here.
"""

from __future__ import annotations

from dataclasses import dataclass

from services.composer.plan import (
    BODY_FLOOR_PX,
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    CHROME_CONTENT_AREA,
    PX_PER_PT,
    SUPPORTING_FLOOR_PX,
    TEMPLATE_TYPE_SCALE_PX,
    ComposerDeckPlan,
    ComposerTokens,
    FitParameters,
)

#: The title's left edge never moves between chrome levels: a compacted page
#: beside a full-chrome page still reads as one deck.
TITLE_LEFT_PX = 64

#: No text is ever set below this size, whatever the ladder asks for.
MIN_FONT_PX = SUPPORTING_FLOOR_PX * 0.9

#: Chrome level -> (title band, footer band, side margin) in px. These are
#: *minimum* band heights: the page is a flex column (see ``stylesheet``) so a
#: title that wraps grows its band instead of clipping or pushing content
#: off-canvas; nothing below chrome level is ever fixed-height.
_CHROME_BANDS: dict[int, tuple[int, int, int]] = {
    0: (120, 56, 64),
    1: (96, 40, 48),
    2: (64, 28, 32),
    3: (48, 24, 24),
}

#: Title/subtitle line-height. Korean glyphs in Pretendard/Malgun Gothic have
#: taller ascent/descent than a 1.15 line box, so the glyph box exceeds the
#: line box and the first line's ascenders sit above y=0, where
#: ``body{overflow:hidden}`` clips them. Body text keeps its own density
#: line-height (``DensitySpec.line_height``); this only covers the title band.
TITLE_LINE_HEIGHT = 1.3
SUBTITLE_LINE_HEIGHT = 1.35

#: Gap between the title and subtitle inside ``.cx-title-stack``.
TITLE_STACK_GAP_PX = 6

#: Vertical padding inside the title band, per chrome level, so the title
#: stack never touches the band's own edge -- the actual cause of the
#: clipping alongside the tight line-height above: a stack taller than the
#: band's min-height grows the band to exactly the stack height, and with
#: zero padding the first line's glyphs land at y=0.
_TITLE_BAND_PADDING_Y: dict[int, int] = {0: 20, 1: 16, 2: 12, 3: 10}

#: Vertical padding inside the footer band; same principle, one floor for
#: every chrome level since furniture text is always set at the supporting
#: scale.
FOOTER_BAND_PADDING_Y = 8

#: Roles that share the supporting rung of the type scale and its floor.
_SUPPORTING_ROLES = frozenset({"supporting", "caption", "furniture"})

#: Roles the grow step never enlarges. The title band sizes itself on its own
#: axis (``render_title.fitted_title_px``) so it stays identical across the
#: deck, and footer/header furniture is chrome rather than content.
_NO_GROW_ROLES = frozenset({"title", "subtitle", "furniture"})
_ROLE_SCALE_KEY = {
    "title": "title",
    "subtitle": "subtitle",
    "kpi": "subtitle",
    "supporting": "supporting",
    "caption": "supporting",
    "furniture": "supporting",
}

#: CSS class -> the ``ComposerTokens`` field its rule in ``stylesheet`` paints
#: text with, in the same order the rules appear there. An element carries
#: several classes (``class="cx-text cx-body cx-step-head"``); when more than
#: one is colour-bearing the CSS cascade picks the last declared rule, so a
#: caller resolves this by keeping the *last* match found while walking the
#: dict in order. ``publish_semantic.py`` reads this back for the declared
#: semantic bundle so it never disagrees with what this module actually
#: paints -- a role-keyed table gets ``.cx-marker``/``.cx-kpi`` wrong: both
#: carry a non-text ``data-semantic-role`` (``decoration``/``body``) while
#: painting in ``accent_text``.
TEXT_COLOR_CLASS_TOKENS: dict[str, str] = {
    "cx-title": "text_primary",
    "cx-subtitle": "text_secondary",
    "cx-running-header": "text_secondary",
    "cx-furniture": "text_secondary",
    "cx-marker": "accent_text",
    "cx-compare-head": "text_primary",
    "cx-kpi": "accent_text",
    "cx-caption": "text_secondary",
    "cx-accent": "accent_text",
    "cx-muted": "text_secondary",
}

#: Token an element with no class in ``TEXT_COLOR_CLASS_TOKENS`` paints with:
#: inherited from ``body{color:...}`` in ``stylesheet``.
DEFAULT_TEXT_COLOR_TOKEN = "text_primary"

#: CSS classes whose rule in ``stylesheet`` sets ``font-weight:700``
#: unconditionally, regardless of the source formatting a bound paragraph
#: carries. A title's own inline style never repeats this -- ``render_text.
#: render_bound_text`` only adds ``font-weight:700`` inline when every run of
#: the *source* paragraph was itself bold -- so a reader that only looked at
#: inline style would still declare 400 for an (unbolded-source) title even
#: though the browser computes 700 for it via this class, reproducing the
#: exact divergence this module exists to close. ``publish_semantic.py``
#: therefore declares 700 when either this class is present *or* the inline
#: style says so.
BOLD_CLASS_NAMES = frozenset({"cx-title", "cx-marker", "cx-step-head", "cx-compare-head", "cx-kpi", "cx-accent"})


@dataclass(frozen=True)
class ChromeGeometry:
    """Bands and margins of one chrome level; the content area is what is left."""

    level: int
    title_band: int
    footer_band: int
    margin: int

    @property
    def content_left(self) -> int:
        return self.margin

    @property
    def content_top(self) -> int:
        return self.title_band

    @property
    def content_width(self) -> int:
        return CANVAS_WIDTH - 2 * self.margin

    @property
    def content_height(self) -> int:
        return CANVAS_HEIGHT - self.title_band - self.footer_band

    @property
    def subtitle_in_band(self) -> bool:
        """Levels 0-1 keep the subtitle under the title; 2-3 are one title line."""

        return self.level <= 1

    @property
    def title_padding_y(self) -> int:
        """Top/bottom padding inside the title band at this chrome level."""

        return _TITLE_BAND_PADDING_Y[self.level]


def chrome_geometry(level: int) -> ChromeGeometry:
    title_band, footer_band, margin = _CHROME_BANDS[int(level)]
    geometry = ChromeGeometry(int(level), title_band, footer_band, margin)
    expected = CHROME_CONTENT_AREA[int(level)]
    if (geometry.content_width, geometry.content_height) != expected:
        raise ValueError(f"chrome level {level} disagrees with CHROME_CONTENT_AREA")
    return geometry


@dataclass(frozen=True)
class DensitySpec:
    line_height: float
    gap: int
    panels: bool
    #: Loose (density 0) vs dense (density >= 1) -- lets ``stylesheet`` pick a
    #: process step's own padding/gap without re-deriving it from ``gap``.
    dense: bool = False


def density_spec(density: int) -> DensitySpec:
    if int(density) >= 1:
        return DensitySpec(line_height=1.4, gap=12, panels=False, dense=True)
    return DensitySpec(line_height=1.6, gap=24, panels=True, dense=False)


def px(value: float) -> str:
    """Format a CSS length; integers stay integers, others keep two decimals."""

    rounded = round(float(value), 2)
    if rounded == int(rounded):
        return f"{int(rounded)}px"
    return f"{rounded:.2f}px"


def template_px(role: str, type_scale: float, type_grow: float = 1.0) -> float:
    """Template type scale at rungs 0-3; each role stops at its design floor.

    ``type_grow`` is the grow step's multiplier (1.0 for every ladder rung).
    It is applied after the floor because it only ever grows, and never to
    ``_NO_GROW_ROLES``.
    """

    key = _ROLE_SCALE_KEY.get(role, "body")
    base = TEMPLATE_TYPE_SCALE_PX[key]
    floor = SUPPORTING_FLOOR_PX if role in _SUPPORTING_ROLES else BODY_FLOOR_PX
    size = max(base * float(type_scale), min(base, floor))
    return size if role in _NO_GROW_ROLES else size * float(type_grow)


def text_px(
    role: str,
    source_size_pt: float | None,
    params: FitParameters,
    deck: ComposerDeckPlan,
    *,
    box_scale: float = 1.0,
) -> float:
    """Font size of one text element under the ladder's parameters.

    Rung 4 (``source_relative``) and rung 5 (``source_geometry``) set text at
    the block's own source size mapped onto the canvas; elements without a
    source size fall back to the template scale. Nothing goes below
    ``MIN_FONT_PX``.
    """

    size: float
    if (params.source_relative or params.source_geometry) and source_size_pt:
        factor = params.page_scale if params.source_geometry else params.source_factor
        size = float(source_size_pt) * PX_PER_PT * deck.source_px_scale * factor
    else:
        size = template_px(role, params.type_scale, params.type_grow)
    return max(size * float(box_scale), MIN_FONT_PX)


def stylesheet(
    tokens: ComposerTokens,
    geometry: ChromeGeometry,
    density: DensitySpec,
) -> str:
    """The whole stylesheet for one slide. Text sizes are inline per element."""

    heading_family = tokens.heading_font_family or tokens.font_family
    gap = density.gap
    #: A process step's own padding/gap: matches ``estimate_components.
    #: GROUP_CHROME_PX`` (chrome subtracted from a step's own width and added
    #: back as its height) and ``PARAGRAPH_GAP_PX`` (the gap between a step's
    #: header and body) so the estimate that decided to try a dense rung and
    #: the CSS the browser actually renders never disagree about how much of
    #: a step card is chrome versus text -- the loose values (density 0) were
    #: already exact; only the dense variant was still fixed at the loose
    #: card's own size, which wasted the space density 1 is meant to reclaim.
    step_pad_y, step_pad_x, step_gap = (6, 8, 5) if density.dense else (12, 14, 10)
    band_paint = (
        f"background:{tokens.surface};border-bottom:3px solid {tokens.accent};"
        if geometry.level <= 1
        else f"border-bottom:2px solid {tokens.accent};"
    )
    return (
        "html,body{width:1280px;height:720px;margin:0;padding:0;overflow:hidden;}"
        f"body{{background:{tokens.background};color:{tokens.text_primary};"
        f"font-family:{tokens.font_family};position:relative;"
        "display:flex;flex-direction:column;}"
        "body *{box-sizing:border-box;}"
        ".cx-text{margin:0;min-width:0;overflow:visible;overflow-wrap:anywhere;"
        "word-break:keep-all;line-break:strict;white-space:normal;}"
        ".cx-title-band{flex:0 0 auto;width:1280px;"
        f"min-height:{px(geometry.title_band)};padding:{px(geometry.title_padding_y)} {px(geometry.margin)} "
        f"{px(geometry.title_padding_y)} {px(TITLE_LEFT_PX)};"
        f"display:flex;align-items:center;justify-content:space-between;gap:24px;{band_paint}}}"
        f".cx-title-stack{{display:flex;flex-direction:column;gap:{px(TITLE_STACK_GAP_PX)};min-width:0;flex:1 1 auto;}}"
        f".cx-title{{font-family:{heading_family};font-weight:700;line-height:{TITLE_LINE_HEIGHT};"
        f"letter-spacing:-0.01em;color:{tokens.text_primary};}}"
        f".cx-subtitle{{color:{tokens.text_secondary};line-height:{SUBTITLE_LINE_HEIGHT};}}"
        f".cx-running-header{{flex:0 0 auto;max-width:30%;text-align:right;color:{tokens.text_secondary};}}"
        ".cx-footer-band{flex:0 0 auto;width:1280px;"
        f"min-height:{px(geometry.footer_band)};padding:{px(FOOTER_BAND_PADDING_Y)} {px(geometry.margin)};display:flex;"
        f"align-items:center;justify-content:space-between;gap:24px;border-top:1px solid {tokens.rule};}}"
        ".cx-footer-group{display:flex;gap:16px;min-width:0;align-items:center;}"
        f".cx-furniture{{color:{tokens.text_secondary};line-height:1.3;}}"
        ".cx-content{flex:1 1 auto;min-height:0;width:1280px;"
        f"padding:0 {px(geometry.margin)};"
        f"display:grid;gap:{px(gap)};align-content:start;align-items:start;overflow:visible;}}"
        ".cx-cover{flex:1 1 auto;min-height:0;width:1280px;display:flex;"
        f"align-items:center;justify-content:center;padding:0 {px(geometry.margin)};}}"
        f".cx-cover-stack{{display:flex;flex-direction:column;align-items:center;"
        f"text-align:center;gap:{px(gap)};max-width:100%;min-height:0;}}"
        f".cx-cover-rule{{width:96px;height:4px;background:{tokens.accent};}}"
        #: No ``min-height:0`` here: a grid item that opts out of the automatic
        #: minimum size lets its "auto" row track collapse below the item's
        #: own rendered content height when the outer ``.cx-content`` box is
        #: constrained (it always is, by the flex column layout) -- the next
        #: row then starts inside that collapsed space and visibly overlaps
        #: this one instead of the whole sequence overflowing past the
        #: canvas the way ``overflow:visible`` intends.
        #: ``flex:0 0 auto`` is only meaningful when a ``.cx-content`` this
        #: sits in has switched to ``display:flex`` (``render_chrome._content``,
        #: the wide-row-plus-images case): the segment keeps its natural
        #: content height rather than shrinking, so the image row -- the
        #: only flexible sibling -- is the one that gives up space first.
        #: A grid ``.cx-content`` ignores ``flex`` on its items entirely.
        f".cx-region{{display:grid;gap:{px(gap)};align-content:start;align-items:start;min-width:0;flex:0 0 auto;}}"
        f".cx-col{{display:flex;flex-direction:column;gap:{px(gap)};min-width:0;}}"
        ".cx-row{min-width:0;flex:0 0 auto;}"
        ".cx-component{min-width:0;position:relative;}"
        f".cx-stack{{display:flex;flex-direction:column;gap:{px(max(gap // 2, 6))};}}"
        f".cx-body{{line-height:{density.line_height};}}"
        f".cx-panel{{background:{tokens.surface};border-radius:12px;padding:{px(gap)};}}"
        ".cx-item{display:grid;grid-template-columns:auto minmax(0,1fr);column-gap:10px;align-items:start;}"
        f".cx-marker{{color:{tokens.accent_text};font-weight:700;line-height:{density.line_height};font-style:normal;}}"
        f".cx-process{{display:grid;gap:{px(gap)};}}"
        f".cx-step{{position:relative;background:{tokens.surface};border-top:3px solid {tokens.accent};"
        f"padding:{px(step_pad_y)} {px(step_pad_x)};display:flex;flex-direction:column;"
        f"gap:{px(step_gap)};min-width:0;}}"
        ".cx-step-head{font-weight:700;}"
        f".cx-arrow{{position:absolute;top:50%;margin-top:-8px;right:{px(-(gap / 2 + 5))};"
        "width:0;height:0;border-top:8px solid transparent;border-bottom:8px solid transparent;"
        f"border-left:10px solid {tokens.accent};}}"
        f".cx-comparison{{display:grid;gap:{px(gap)};}}"
        ".cx-compare-col{display:flex;flex-direction:column;gap:8px;min-width:0;}"
        f".cx-compare-head{{background:{tokens.accent_soft};color:{tokens.text_primary};"
        "font-weight:700;padding:8px 12px;border-radius:8px;}"
        ".cx-table{border-collapse:collapse;width:100%;table-layout:fixed;}"
        f".cx-table th,.cx-table td{{border:1px solid {tokens.rule};padding:{px(max(gap // 3, 6))} 10px;"
        "text-align:left;vertical-align:top;overflow-wrap:anywhere;word-break:keep-all;line-break:strict;}"
        f".cx-table th{{background:{tokens.accent_soft};font-weight:700;}}"
        #: No ``min-height:0`` here either, for the same reason as ``.cx-region``.
        f".cx-images{{display:grid;gap:{px(gap)};}}"
        ".cx-figure{margin:0;min-width:0;min-height:0;display:flex;align-items:center;justify-content:center;}"
        ".cx-image{width:100%;height:100%;object-fit:contain;display:block;}"
        #: ``render_image_region.flex_image_row`` only: the figure's grid cell is
        #: already a definite, fixed-fraction size (never ``auto``), so the
        #: image is capped to it with ``max-*`` instead of forced to fill it
        #: -- it can shrink with the row but never exceed the cell, so it can
        #: never leave the canvas or paint over the text above it.
        ".cx-image-flex{max-width:100%;max-height:100%;object-fit:contain;display:block;}"
        f".cx-quote{{border-left:4px solid {tokens.accent};padding-left:16px;font-style:italic;}}"
        f".cx-kpi{{font-weight:700;color:{tokens.accent_text};line-height:1.2;}}"
        f".cx-caption{{color:{tokens.text_secondary};line-height:1.35;}}"
        f".cx-accent{{color:{tokens.accent_text};font-weight:700;}}"
        f".cx-muted{{color:{tokens.text_secondary};}}"
        ".cx-geo{position:absolute;min-width:0;}"
        f".cx-side-band{{position:absolute;background:{tokens.accent_soft};}}"
    )


__all__ = [
    "BOLD_CLASS_NAMES",
    "DEFAULT_TEXT_COLOR_TOKEN",
    "FOOTER_BAND_PADDING_Y",
    "MIN_FONT_PX",
    "SUBTITLE_LINE_HEIGHT",
    "TEXT_COLOR_CLASS_TOKENS",
    "TITLE_LEFT_PX",
    "TITLE_LINE_HEIGHT",
    "TITLE_STACK_GAP_PX",
    "ChromeGeometry",
    "DensitySpec",
    "chrome_geometry",
    "density_spec",
    "px",
    "stylesheet",
    "template_px",
    "text_px",
]
