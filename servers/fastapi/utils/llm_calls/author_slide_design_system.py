"""Design system construction for authored slide prompts."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any

from services.authored_vnext.capability_registry import prompt_capability_clause
from utils.authored_fragmentation_gate import fragmentation_prompt_clause
from utils.authored_style_exemplars import exemplar_block
from utils.llm_calls.author_deck_tokens import deck_tokens_block
from utils.llm_calls.author_prompt_profile import current_prompt_sections
from utils.llm_calls.author_slide_brief import (
    INLINE_SVG_CONTRACT,
    SEMANTIC_ROLE_RULES,
    SHARED_UNIVERSALS,
    composition_intent_block,
)

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
    page_number_system: str,
    type_scale: str,
    header_system: str = "",
    canvas_width_px: int = 1280,
    canvas_height_px: int = 720,
    canvas_safe_area: object | None = None,
) -> str:
    """Build the concrete shared brief for a brand and optional authored style."""
    from utils.llm_calls import author_slide_prompt

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
            # A deck whose illustration share is 0 authors no slot on any slide,
            # so the ~2 030 characters of slot contract would be paid on every
            # call of every slide to describe an element none of them may emit.
            and author_slide_prompt.get_authored_illustration_share() > 0
        ):
            style_brief = f"{style_brief}\n{illustration_slot_rules}"
    # Empty when the composition bundle is off; the always-on semantic-role
    # block that follows it supplies its own separator, so the design system
    # never carries a blank line where the intent block used to be.
    intent_block = composition_intent_block()
    rules = design_system_rules.format(
        primary=brand.primary or "#2563EB",
        fonts=brand.fonts or "Noto Sans KR",
        language=brand.language or "Korean",
        style_brief=style_brief,
        composition_intent=f"{intent_block}\n" if intent_block else "",
        semantic_roles=SEMANTIC_ROLE_RULES,
        shared_universals=SHARED_UNIVERSALS,
        inline_svg_contract=INLINE_SVG_CONTRACT,
        # Empty when the deck has no header contract, which keeps the prompt
        # byte-identical to the pre-contract behaviour.
        header_system=f"{header_system}\n" if header_system else "",
        page_number_system=page_number_system,
        type_scale=type_scale,
    )
    if (canvas_width_px, canvas_height_px) in {(720, 1280), (720, 1040)}:
        portrait_ratio = "9:13" if canvas_height_px == 1040 else "9:16"
        portrait_size = f"720x{canvas_height_px}"
        replacements = (
            ("single 16:9 slide", f"single {portrait_ratio} portrait slide"),
            ("1280x720px", f"{portrait_size}px"),
            ("1280x720", portrait_size),
            ("y=648px", f"y={canvas_height_px - 72}px"),
            ("y=688px", f"y={canvas_height_px - 32}px"),
            ("y=749", f"y={canvas_height_px + 29}"),
            ("an element is never 1280px wide", "an element is never 720px wide"),
            ("a 72px inset on each side leaves 1136px", "a 72px inset on each side leaves 576px"),
            ("width:1280px", "width:720px"),
        )
        for old, new in replacements:
            rules = rules.replace(old, new)
        rules += (
            "\n- PORTRAIT COMPOSITION (authoritative for this deck): use the full "
            f"{portrait_size} canvas without rotation or landscape scaling. Place cover titles "
            "and subtitles toward the top; use one readable vertical body column; stack "
            "comparisons and three-card groups vertically; keep images and charts within "
            "the 592px safe content width with captions below; and compose closing "
            "messages and next actions as a vertical sequence.\n"
        )
    # Deck-invariant tail, in the order design section G8 fixes: the rules, then
    # this deck's literal tokens, then the style's exemplar slide. The exemplar
    # is last because it is the longest and the most concrete: everything above
    # it is the contract it is an instance of. None of the three existed on
    # 2026-08-19, so the ``august-0819`` profile states the rules and stops.
    # The fragmentation clause below is empty while the gate is off, which
    # keeps the deck-invariant tail byte-identical to the pre-gate text.
    from utils.llm_calls.document_canvas_prompt import canvas_clause
    document_clause = canvas_clause(canvas_width_px, canvas_height_px, canvas_safe_area)
    if not current_prompt_sections().deck_invariant_tail:
        return rules + document_clause
    return (
        rules
        + prompt_capability_clause()
        + fragmentation_prompt_clause()
        + deck_tokens_block(style, brand)
        + exemplar_block(getattr(style, "id", None) if style is not None else None)
        + document_clause
    )
