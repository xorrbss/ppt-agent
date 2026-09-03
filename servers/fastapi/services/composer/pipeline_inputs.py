"""Read-side projections the Composer pipeline needs from an admitted request.

Everything here is deterministic and free of I/O: manifest units in outline
order, the deck context, the style tokens, and the source text bindings the
stamping assertion is checked against.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from services.composer.contracts import DeckContext, repeated_texts
from services.composer.plan import ComposerSlidePlan, ComposerTokens
from services.composer.receipt import ComposerPipelineError

_HEX = "0123456789ABCDEF"


def as_mapping(value: Any) -> Mapping[str, Any]:
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        value = dump(mode="json", by_alias=True)
    if not isinstance(value, Mapping):
        raise ComposerPipelineError("source_content_manifest_invalid")
    return value


def units_in_outline_order(manifest: Any, outline: Any) -> list[Mapping[str, Any]]:
    """Manifest units ordered by the admitted outline; one unit per slide."""

    manifest_value = as_mapping(manifest)
    outline_value = as_mapping(outline)
    by_id: dict[str, Mapping[str, Any]] = {}
    for unit in manifest_value.get("units") or ():
        if isinstance(unit, Mapping):
            by_id[str(unit.get("sourceUnitId") or unit.get("source_unit_id") or "")] = unit
    ordered: list[Mapping[str, Any]] = []
    for slide in outline_value.get("slides") or ():
        if not isinstance(slide, Mapping):
            raise ComposerPipelineError("source_content_outline_invalid")
        unit = by_id.get(str(slide.get("sourceUnitId") or slide.get("source_unit_id") or ""))
        if unit is None:
            raise ComposerPipelineError("source_content_source_unit_cardinality_mismatch")
        ordered.append(unit)
    if not ordered or len(ordered) != len(by_id):
        raise ComposerPipelineError("source_content_source_unit_cardinality_mismatch")
    return ordered


def source_text_bindings(
    units: Sequence[Mapping[str, Any]], outline: Any
) -> tuple[tuple[tuple[str, str], ...], ...]:
    """``(block_id, text)`` pairs per slide in reading order, text blocks only.

    Mirrors the projection the authored lane seals before Chromium render so
    ``stamp_source_block_ids`` checks exactly the same identity.
    """

    outline_value = as_mapping(outline)
    slides = list(outline_value.get("slides") or ())
    blocks_by_id: dict[str, Mapping[str, Any]] = {}
    for unit in units:
        for block in unit.get("blocks") or ():
            if isinstance(block, Mapping):
                blocks_by_id[str(block.get("sourceBlockId") or block.get("source_block_id") or "")] = block
    result: list[tuple[tuple[str, str], ...]] = []
    for slide in slides:
        pairs: list[tuple[str, str]] = []
        order = slide.get("readingOrder") or slide.get("reading_order") or ()
        for block_id in order:
            block = blocks_by_id.get(str(block_id))
            if not block or str(block.get("kind") or "") != "text":
                continue
            payload = block.get("payload") or {}
            text = payload.get("text") if isinstance(payload, Mapping) else None
            if isinstance(text, str) and text.strip():
                pairs.append((str(block_id), text))
        result.append(tuple(pairs))
    return tuple(result)


def source_page_emu(manifest: Any) -> tuple[int, int] | None:
    """The captured slide size in EMU when the manifest carries one."""

    value = as_mapping(manifest)
    raw = value.get("slideSize") or value.get("slide_size")
    if not isinstance(raw, Mapping):
        return None
    try:
        cx = int(raw.get("cx") or raw.get("width") or 0)
        cy = int(raw.get("cy") or raw.get("height") or 0)
    except (TypeError, ValueError):
        return None
    return (cx, cy) if cx > 0 and cy > 0 else None


def deck_title(units: Sequence[Mapping[str, Any]]) -> str:
    """The first title-role text block of the first page, if any."""

    for unit in units[:1]:
        blocks = sorted(
            (block for block in unit.get("blocks") or () if isinstance(block, Mapping)),
            key=lambda block: int(block.get("order") or 0),
        )
        for block in blocks:
            if str(block.get("kind") or "") != "text":
                continue
            role = str(block.get("semanticRole") or block.get("semantic_role") or "").lower()
            payload = block.get("payload") or {}
            text = payload.get("text") if isinstance(payload, Mapping) else None
            if role in {"title", "ctrtitle", "heading"} and isinstance(text, str) and text.strip():
                return " ".join(text.split())[:200]
    return ""


def build_deck_context(manifest: Any, units: Sequence[Mapping[str, Any]]) -> DeckContext:
    return DeckContext(
        page_count=len(units),
        repeated_texts=repeated_texts(units),
        source_page_emu=source_page_emu(manifest),
        deck_title=deck_title(units),
    )


def _hex_color(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    if len(text) == 7 and text[0] == "#" and all(ch.upper() in _HEX for ch in text[1:]):
        return text.upper()
    return fallback


def blend(foreground: str, background: str, amount: float) -> str:
    """Mix ``amount`` of ``foreground`` over ``background``; deterministic hex."""

    ratio = min(1.0, max(0.0, amount))
    channels = []
    for offset in (1, 3, 5):
        fg = int(foreground[offset : offset + 2], 16)
        bg = int(background[offset : offset + 2], 16)
        channels.append(round(bg + (fg - bg) * ratio))
    return "#" + "".join(f"{channel:02X}" for channel in channels)


def composer_tokens(style: Any, *, deck_title_text: str = "") -> ComposerTokens:
    """Map the restyle style tokens onto ``ComposerTokens``.

    ``accent_soft`` and ``rule`` are derived deterministically from the accent
    and background because the authored styles carry neither.
    """

    from services.source_content.pipeline import _style_tokens

    tokens = _style_tokens(style)
    background = _hex_color(tokens.background, "#FFFFFF")
    accent = _hex_color(tokens.accent, "#2563EB")
    text_secondary = _hex_color(tokens.text_secondary, "#334155")
    body_font = str(tokens.body_font or "Noto Sans KR").strip()
    heading_font = str(tokens.heading_font or body_font).strip()
    return ComposerTokens(
        background=background,
        surface=_hex_color(tokens.surface, "#FFFFFF"),
        text_primary=_hex_color(tokens.text_primary, "#0F172A"),
        text_secondary=text_secondary,
        accent=accent,
        accent_soft=blend(accent, background, 0.16),
        rule=blend(text_secondary, background, 0.28),
        font_family=f"'{body_font}', Pretendard, 'Malgun Gothic', sans-serif",
        heading_font_family=f"'{heading_font}', Pretendard, 'Malgun Gothic', sans-serif",
        deck_title=deck_title_text,
    )


def _has_running_header(plan: ComposerSlidePlan) -> bool:
    return any(component.furniture_kind == "running_header" for component in plan.furniture)


def deck_title_px(
    plans: Sequence[ComposerSlidePlan], tokens: ComposerTokens, chrome_level: int = 0
) -> float | None:
    """The largest title size, in px, that fits every non-cover slide's title.

    Mirrors how ``services.composer.estimate.estimate_fit`` measures a
    paragraph: ``wrapped_line_count`` per title run, summed across the runs of
    each slide's title, inside the title band's inner width for
    ``chrome_level`` (``services.composer.render_css.chrome_geometry``). A
    slide with a running header shares the band with it (``max-width:30%``
    on ``.cx-running-header``), so its title gets 70% of the inner width.
    Starts at ``TEMPLATE_TYPE_SCALE_PX["title"]`` and steps down by 2px until
    every qualifying title fits two lines total, never below
    ``TITLE_FLOOR_PX``. ``None`` when no slide carries a title.
    """

    del tokens  # the look does not change wrapped line counts
    from services.authored_vnext.source_render_geometry import wrapped_line_count
    from services.composer.plan import TEMPLATE_TYPE_SCALE_PX, TITLE_FLOOR_PX
    from services.composer.render_css import chrome_geometry

    titled = [
        plan for plan in plans if plan.archetype != "cover" and plan.title is not None and plan.title.paragraphs
    ]
    if not titled:
        return None
    inner_width = float(chrome_geometry(chrome_level).content_width)

    def fits(size: float) -> bool:
        for plan in titled:
            width = inner_width * 0.7 if _has_running_header(plan) else inner_width
            lines = sum(
                wrapped_line_count(paragraph.text, width_px=width, font_px=size)
                for paragraph in plan.title.paragraphs  # type: ignore[union-attr]
            )
            if lines > 2:
                return False
        return True

    size = TEMPLATE_TYPE_SCALE_PX["title"]
    while size > TITLE_FLOOR_PX and not fits(size):
        size -= 2.0
    return max(size, TITLE_FLOOR_PX)


def render_concurrency(raw: str | None, *, default: int = 2) -> int:
    """``PUBLIC_RENDER_MAX_CONCURRENCY`` when set and positive, else ``default``."""

    try:
        value = int(str(raw or "").strip())
    except ValueError:
        return default
    return value if value >= 1 else default


__all__ = [
    "as_mapping",
    "blend",
    "build_deck_context",
    "composer_tokens",
    "deck_title",
    "deck_title_px",
    "render_concurrency",
    "source_page_emu",
    "source_text_bindings",
    "units_in_outline_order",
]
