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
from utils.color_contrast import blend, normalize_hex


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


#: Last-resort deck title, reached only when the source's cover page carries
#: no title-role text block at all. Matches the codebase-wide "no title
#: available" convention (``utils/ppt_utils.py``, ``utils/outline_utils.py``,
#: the blank-presentation default in ``endpoints/presentation_blank.py``)
#: rather than a Composer-only literal.
_DECK_TITLE_FALLBACK = "Untitled Presentation"

_DECK_TITLE_ROLES = frozenset({"title", "ctrtitle", "heading"})
_DECK_TITLE_MAX_LENGTH = 200


def _clamp_title(value: Any) -> str | None:
    """Whitespace-collapse and length-cap a title candidate; blank is absent."""

    if not isinstance(value, str):
        return None
    collapsed = " ".join(value.split())
    return collapsed[:_DECK_TITLE_MAX_LENGTH] if collapsed else None


def deck_title(
    units: Sequence[Mapping[str, Any]],
    *,
    document_title: str | None = None,
    file_name_title: str | None = None,
) -> str:
    """The deck title, resolved from the source document itself.

    1. The first title-role text block classified on the source's cover
       page (the manifest's first unit, in source order) -- taken verbatim,
       only whitespace-collapsed and length-capped. This wins because it is
       what a reader of the original deck actually saw on open; the other
       two signals are metadata the author may never have looked at.
    2. ``document_title`` -- the source's own ``docProps/core.xml``
       ``dc:title`` (``services/source_content/pptx_document_identity.py``),
       when the source declares one. A deliberate document-level title beats
       a filename, which is often just how the file happened to get saved.
    3. ``file_name_title`` -- a title candidate cleaned from the uploaded
       file's own name (same module). Reliable in practice (a user's file is
       typically named after its subject) but ranked last because a
       filename is incidental, not authored content.
    4. ``_DECK_TITLE_FALLBACK`` when none of the above carries anything --
       the last resort.
    """

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
            if role in _DECK_TITLE_ROLES and isinstance(text, str) and text.strip():
                return " ".join(text.split())[:_DECK_TITLE_MAX_LENGTH]
    return _clamp_title(document_title) or _clamp_title(file_name_title) or _DECK_TITLE_FALLBACK


def build_deck_context(manifest: Any, units: Sequence[Mapping[str, Any]]) -> DeckContext:
    manifest_value = as_mapping(manifest)
    document_title = manifest_value.get("documentTitle") or manifest_value.get("document_title")
    file_name_title = manifest_value.get("sourceFileTitle") or manifest_value.get("source_file_title")
    return DeckContext(
        page_count=len(units),
        repeated_texts=repeated_texts(units),
        source_page_emu=source_page_emu(manifest),
        deck_title=deck_title(units, document_title=document_title, file_name_title=file_name_title),
    )


def composer_tokens(style: Any, *, deck_title_text: str = "") -> ComposerTokens:
    """Map the restyle style tokens onto ``ComposerTokens``.

    ``accent_soft`` and ``rule`` are derived deterministically from the accent
    and background because the authored styles carry neither.
    """

    from services.composer.fonts import font_family_stack
    from services.source_content.pipeline import _style_tokens

    tokens = _style_tokens(style)
    background = normalize_hex(tokens.background, "#FFFFFF")
    accent = normalize_hex(tokens.accent, "#2563EB")
    accent_text = normalize_hex(tokens.accent_text, accent)
    text_secondary = normalize_hex(tokens.text_secondary, "#334155")
    # The embeddable family always leads: a style's own face may follow as a
    # secondary fallback, but if it led, Chromium would resolve an installed
    # system face while the exporter emits Pretendard, and every Korean run
    # would rasterize as `font-substitution` (services.composer.fonts).
    body_font = str(tokens.body_font or "").strip()
    heading_font = str(tokens.heading_font or body_font).strip()
    return ComposerTokens(
        background=background,
        surface=normalize_hex(tokens.surface, "#FFFFFF"),
        text_primary=normalize_hex(tokens.text_primary, "#0F172A"),
        text_secondary=text_secondary,
        accent=accent,
        accent_text=accent_text,
        accent_soft=blend(accent, background, 0.16),
        rule=blend(text_secondary, background, 0.28),
        font_family=font_family_stack(body_font),
        heading_font_family=font_family_stack(heading_font),
        deck_title=deck_title_text,
    )


def _has_running_header(plan: ComposerSlidePlan) -> bool:
    return any(component.furniture_kind == "running_header" for component in plan.furniture)


def deck_title_px(
    plans: Sequence[ComposerSlidePlan], tokens: ComposerTokens, chrome_level: int = 0,
    *, canvas: Any = None,
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
    inner_width = float(chrome_geometry(chrome_level, canvas=canvas).content_width)

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
