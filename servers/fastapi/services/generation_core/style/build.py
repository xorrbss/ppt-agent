"""Build a :class:`StyleContext` once per deck.

Pure function over data the authored path already produces (``Brand``, the
resolved style preset, an optional master contract): authored is frozen, so
this module never calls into HTML-specific code and the ``resolve_brand``
facade left at its historical import path keeps working unchanged.
"""
from __future__ import annotations

import re
from typing import Mapping, Optional, Sequence

from models.generate_presentation_request import GeneratePresentationRequest
from models.presentation_outline_model import PresentationOutlineModel
from utils.llm_calls.author_slide import Brand
from utils.outline_utils import get_presentation_title_from_presentation_outline

from .context import BriefLine, FontChoice, Palette, StyleContext

_DEFAULT_PRIMARY = "#2563EB"

# Section headings shared by every authored style brief (utils/authored_styles.py
# briefs are hand-written against this fixed vocabulary).
_COMMON_SECTIONS = {
    "MOOD", "PALETTE", "TYPOGRAPHY", "LAYOUT SYSTEM", "SIGNATURE ELEMENTS",
    "DATA VISUALIZATION", "IMAGE DIRECTION", "SLIDE ARCHETYPES", "AVOID",
}
# A brief line naming HTML/CSS/DOM mechanics or containing an inline HTML
# exemplar does not apply to a non-HTML driver.
_HTML_ONLY_TERMS = re.compile(
    r"\b(?:HTML|CSS|DOM|flexbox|flex|grid-template|position:absolute|exemplar)\b|<[/a-zA-Z]",
    re.I,
)
_ALL_FORMATS = frozenset({"svg", "html"})
_HTML_ONLY = frozenset({"html"})


def _is_korean(*candidates: Optional[str]) -> bool:
    for c in candidates:
        s = (c or "").lower()
        if "korea" in s or "?쒓뎅" in s or s.startswith("ko"):
            return True
    return False


def authored_title(
    request: GeneratePresentationRequest, outline: PresentationOutlineModel
) -> str:
    """A clean deck title for authored mode. Prefer the user's topic (request.content
    first line) over the verbose generated first-slide outline (which often carries
    presenter/date/agenda scaffolding that bloats the title and filename)."""
    content = (request.content or "").strip()
    if content:
        first_line = content.splitlines()[0].lstrip("#").strip()
        if first_line:
            return first_line[:80]
    return get_presentation_title_from_presentation_outline(outline)


def resolve_brand(
    request: GeneratePresentationRequest,
    outline: PresentationOutlineModel,
    language: Optional[str],
) -> Brand:
    """Build the shared design tokens for the deck. Theme injection point: brand primary
    colour + fonts + wordmark go into every slide's design-system brief. User-supplied
    request fields win; otherwise a brand-blue primary + language-aware fonts (Noto Sans
    KR for Korean, else a clean sans) and no wordmark."""
    korean = _is_korean(language, request.language)
    return Brand(
        topic=authored_title(request, outline),
        language=language or ("Korean" if korean else "the deck's language"),
        primary=(request.primary_color or _DEFAULT_PRIMARY).strip() or _DEFAULT_PRIMARY,
        fonts=(request.fonts or ("Noto Sans KR" if korean else "Inter")).strip(),
        wordmark=(request.wordmark or "").strip(),
        primary_is_explicit=bool((request.primary_color or "").strip()),
        fonts_are_explicit=bool((request.fonts or "").strip()),
        verbosity=request.verbosity.value,
    )


def tag_brief_lines(brief: str) -> tuple[BriefLine, ...]:
    """Split a style preset's free-text brief into sections and tag each line
    with the formats it applies to. An HTML/CSS-specific instruction is kept
    (not dropped) but tagged html-only, so a driver can filter for its own
    format without losing the line for anyone else."""
    section = "GENERAL"
    lines: list[BriefLine] = []
    for raw in brief.splitlines():
        line = raw.strip()
        if line in _COMMON_SECTIONS:
            section = line
            continue
        if not line:
            continue
        applies_to = _HTML_ONLY if _HTML_ONLY_TERMS.search(line) else _ALL_FORMATS
        lines.append(BriefLine(section=section, text=line, applies_to=applies_to))
    return tuple(lines)


def _request_brand_overrides(request: GeneratePresentationRequest) -> tuple[Optional[str], Optional[str], str]:
    """Explicit request-level primary colour / fonts / wordmark, trimmed.

    Deliberately independent of :func:`resolve_brand`: that function needs an
    outline to build authored's deck ``topic`` (unused by svg), and several
    svg call sites (durable-payload staging, style pinning) run before an
    outline exists. Only the three explicit overrides are needed here.
    """
    primary = (request.primary_color or "").strip() or None
    fonts = (request.fonts or "").strip() or None
    wordmark = (request.wordmark or "").strip()
    return primary, fonts, wordmark


def build_style_context(
    request: GeneratePresentationRequest,
    *,
    style: object,
    mapped_guidance: str = "",
    reference_images: Mapping[str, Sequence[Mapping[str, str]]] | None = None,
    master_rules: Mapping[str, object] | None = None,
    master_pin: Mapping[str, object] | None = None,
    design_pack_rules: Mapping[str, object] | None = None,
) -> StyleContext:
    """Compose the request's brand tokens with the resolved style preset.

    ``style`` is whatever the caller already resolved (an ``AuthoredStyle`` or
    a source-restyle catalogue style); this function reads only the fields
    every style preset exposes. ``master_rules`` / ``design_pack_rules`` are
    already-projected rule sets (see the sibling ``*_projection`` modules),
    never markup.
    """
    explicit_primary, explicit_fonts, wordmark = _request_brand_overrides(request)
    # A style preset usually carries its own primary/fonts; only an explicit
    # request override should displace them (the "Facts" bug this session
    # fixes: svg previously read only the preset's tokens).
    primary = (
        explicit_primary
        or getattr(style, "primary_color", None)
        or getattr(style, "preview_accent", _DEFAULT_PRIMARY)
    )
    palette = Palette(
        primary=primary,
        background=getattr(style, "background_color", None) or getattr(style, "preview_bg", "#FFFFFF"),
        swatches=tuple(getattr(style, "preview_palette", None) or ()),
    )
    default_font = "Noto Sans KR"
    fonts = FontChoice(
        heading=explicit_fonts or getattr(style, "heading_font", None) or default_font,
        body=explicit_fonts or getattr(style, "body_font", None) or default_font,
        mono=getattr(style, "mono_font", None) or "monospace",
    )
    return StyleContext(
        preset_id=getattr(style, "id", "blueprint"),
        palette=palette,
        fonts=fonts,
        wordmark=wordmark,
        brief_lines=tag_brief_lines(getattr(style, "brief", "") or ""),
        mapped_guidance=mapped_guidance,
        reference_images={role: tuple(refs) for role, refs in (reference_images or {}).items()},
        master_rules=master_rules,
        master_pin=master_pin,
        design_pack_rules=design_pack_rules,
    )


__all__ = [
    "authored_title",
    "build_style_context",
    "resolve_brand",
    "tag_brief_lines",
]
