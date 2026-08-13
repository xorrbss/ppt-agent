"""Prompt and design-system builders for authored slide generation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any


LOCAL_FONT_FALLBACKS = {
    "noto sans kr": ("Noto Sans KR", "Malgun Gothic", "sans-serif"),
    "pretendard": ("Pretendard", "Malgun Gothic", "sans-serif"),
    "noto serif kr": ("Noto Serif KR", "Batang", "serif"),
    "inter": ("Inter", "Aptos", "sans-serif"),
    "roboto": ("Roboto", "Aptos", "sans-serif"),
    "dm sans": ("DM Sans", "Aptos", "sans-serif"),
    "source serif 4": ("Source Serif 4", "Cambria", "serif"),
    "ibm plex mono": ("IBM Plex Mono", "Consolas", "monospace"),
}


def css_font_stack(value: str) -> str:
    family = (value or "Noto Sans KR").split(",", 1)[0].strip().strip("\"'")
    logical, local, generic = LOCAL_FONT_FALLBACKS.get(
        " ".join(family.lower().split()),
        (family, "Aptos", "sans-serif"),
    )
    logical = logical.replace("\\", "\\\\").replace('"', '\\"')
    local = local.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{logical}","{local}",{generic}'


def apply_style_defaults(brand: Any, style: Any | None) -> Any:
    """Apply preset brand defaults without overriding explicit request values."""
    if style is None or style.id == "default":
        return brand
    primary = brand.primary
    fonts = brand.fonts
    if not brand.primary_is_explicit:
        primary = (
            getattr(style, "primary_color", None)
            or getattr(style, "preview_accent", None)
            or primary
        )
    if not brand.fonts_are_explicit:
        fonts = (
            getattr(style, "body_font", None)
            or getattr(style, "heading_font", None)
            or fonts
        )
    return replace(brand, primary=primary, fonts=fonts)


def build_design_system(
    brand: Any,
    style: Any | None,
    *,
    default_style_brief: str,
    design_system_rules: str,
    illustration_slot_rules: str,
    normalize_reference_mode: Callable[[str | None], str],
    exact_reconstruction: str,
    style_reference: str,
) -> str:
    """Build the concrete shared brief for a brand and optional authored style."""
    if style is None or style.id == "default":
        style_brief = default_style_brief.format(
            primary=brand.primary or "#2563EB",
            font_stack=css_font_stack(brand.fonts or "Noto Sans KR"),
        )
    else:
        style_brief = style.brief.strip()
        token_lines = []
        background = getattr(style, "background_color", None)
        heading_font = getattr(style, "heading_font", None)
        body_font = getattr(style, "body_font", None)
        mono_font = getattr(style, "mono_font", None)
        if background:
            token_lines.append(f"- Default canvas background: {background}.")
        if heading_font or body_font or mono_font:
            token_lines.append(
                "- Typography roles: "
                f"headings {heading_font or brand.fonts}; "
                f"body {body_font or brand.fonts}; "
                f"labels/data {mono_font or body_font or brand.fonts}."
            )
        if token_lines:
            style_brief = f"{style_brief}\n" + "\n".join(token_lines)
        reference_mode = normalize_reference_mode(
            getattr(style, "reference_mode", style_reference)
        )
        if (
            reference_mode != exact_reconstruction
            and getattr(style, "illustration_prompt", None)
        ):
            style_brief = f"{style_brief}\n{illustration_slot_rules}"
    return design_system_rules.format(
        primary=brand.primary or "#2563EB",
        fonts=brand.fonts or "Noto Sans KR",
        language=brand.language or "Korean",
        style_brief=style_brief,
    )


def author_prompt(
    content: str,
    design_system: str,
    brand: Any,
    role: str,
    index: int,
    n: int,
) -> str:
    if role == "COVER":
        page_marker = (
            "PAGE MARKER FOR THIS SLIDE: OMIT IT. This is the cover; render no page number "
            "and no footer text."
        )
    else:
        page_marker = (
            f'PAGE MARKER FOR THIS SLIDE: render exactly "{index:02d}" once, as the only '
            "footer text, using the fixed bottom-center page-number system."
        )
    wordmark = (
        f" Brand wordmark: {brand.wordmark}. If used, keep it outside the reserved footer zone."
        if brand.wordmark
        else ""
    )
    return (
        f"Author slide {index + 1} of {n}. Deck topic: {brand.topic}. This slide's ROLE: "
        f"{role}. CONTENT: {content}.{wordmark}\n\n{page_marker}\n\n{design_system}\n\n"
        "Design THIS slide bespoke and premium within the shared system, vary the layout to "
        "suit the role, output ONLY the complete self-contained HTML document."
    )


__all__ = [
    "apply_style_defaults",
    "author_prompt",
    "build_design_system",
    "css_font_stack",
]
