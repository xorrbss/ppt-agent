"""Structured (AI-edit zone) authoring constraints for one authored slide.

Extracted from ``utils.llm_calls.author_deck`` (above the file-size ratchet).
A structured authoring context freezes zone geometry, a colour/font allowlist
and per-zone content budgets; this module fails the candidate closed when the
model leaves that contract and pins the surviving zone containers to the
contract's own geometry.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any, Mapping


_ZONE_ID_PATTERN = re.compile(r"^ai-zone:[a-z0-9][a-z0-9._-]*$")
_HEX_COLOR_PATTERN = re.compile(r"#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?\b")
_RGB_COLOR_PATTERN = re.compile(
    r"rgba?\(\s*(\d{1,3})\s*[, ]\s*(\d{1,3})\s*[, ]\s*(\d{1,3})(?:\s*[,/]\s*[\d.]+%?)?\s*\)",
    re.IGNORECASE,
)
_FONT_FAMILY_PATTERN = re.compile(r"font-family\s*:\s*([^;}]+)", re.IGNORECASE)
_SAFE_FONT_FALLBACKS = frozenset(
    {
        "sans-serif",
        "serif",
        "monospace",
        "malgun gothic",
        "aptos",
        "batang",
        "cambria",
        "consolas",
    }
)
_VOID_HTML_TAGS = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
)


class _ZoneHTMLProbe(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.active_zone: tuple[str, int] | None = None
        self.zone_occurrences: dict[str, int] = {}
        self.zone_chars: dict[str, int] = {}
        self.zone_items: dict[str, int] = {}
        self.zone_blocks: dict[str, set[str]] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name.casefold(): value for name, value in attrs}
        zone_id = values.get("data-ai-zone-id")
        if zone_id is not None:
            if self.active_zone is not None:
                raise ValueError("structured zones cannot be nested")
            self.active_zone = (zone_id, len(self.stack))
            self.zone_occurrences[zone_id] = self.zone_occurrences.get(zone_id, 0) + 1
            self.zone_chars.setdefault(zone_id, 0)
            self.zone_items.setdefault(zone_id, 0)
            self.zone_blocks.setdefault(zone_id, set())
        if self.active_zone is not None:
            active_id = self.active_zone[0]
            block_id = values.get("data-ai-block-id")
            if block_id is not None:
                self.zone_blocks[active_id].add(block_id)
            if "data-ai-item" in values:
                self.zone_items[active_id] += 1
        if tag.casefold() not in _VOID_HTML_TAGS:
            self.stack.append(tag.casefold())

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.casefold() not in _VOID_HTML_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if not self.stack:
            return
        if self.active_zone is not None and len(self.stack) - 1 == self.active_zone[1]:
            self.active_zone = None
        self.stack.pop()

    def handle_data(self, data: str) -> None:
        if self.active_zone is not None:
            self.zone_chars[self.active_zone[0]] += len(data.strip())


def _normalized_color(value: str) -> str:
    lowered = value.lower()
    if len(lowered) == 4:
        return "#" + "".join(character * 2 for character in lowered[1:])
    return lowered


def _constrain_structured_zone_html(
    html: str,
    context: Mapping[str, Any] | None,
) -> str:
    """Fail closed and pin model-authored zone containers to contract geometry."""

    if context is None:
        return html
    if context.get("schemaVersion") != "authored-vnext-ai-edit-zone-context/v1":
        raise ValueError("unsupported structured authoring context")
    raw_zones = context.get("zones")
    design = context.get("templateDesignContext")
    if not isinstance(raw_zones, list) or not isinstance(design, Mapping):
        raise ValueError("structured authoring context is incomplete")
    token_context = design.get("tokens")
    color_context = token_context.get("colors") if isinstance(token_context, Mapping) else None
    if not isinstance(color_context, Mapping):
        raise ValueError("inheritTemplate requires template color tokens")
    allowed_colors = {
        _normalized_color(str(value))
        for value in color_context.values()
        if isinstance(value, str) and _HEX_COLOR_PATTERN.fullmatch(value)
    }
    allowed_fonts = {
        str(value).strip().strip("\"'").lower()
        for value in design.get("allowedFonts", [])
        if isinstance(value, str) and str(value).strip()
    }
    if not allowed_colors or not allowed_fonts:
        raise ValueError("inheritTemplate requires an explicit color and font allowlist")

    css_sources = re.findall(r"<style\b[^>]*>(.*?)</style\s*>", html, re.I | re.S)
    css_sources.extend(re.findall(r"\bstyle\s*=\s*[\"']([^\"']*)[\"']", html, re.I | re.S))
    emitted_css = "\n".join(css_sources)
    invented_colors = {
        _normalized_color(value)
        for value in _HEX_COLOR_PATTERN.findall(emitted_css)
        if _normalized_color(value) not in allowed_colors
    }
    for match in _RGB_COLOR_PATTERN.finditer(emitted_css):
        channels = tuple(int(value) for value in match.groups())
        if any(value > 255 for value in channels):
            invented_colors.add(match.group(0).lower())
            continue
        normalized = "#" + "".join(f"{value:02x}" for value in channels)
        if normalized not in allowed_colors:
            invented_colors.add(normalized)
    if re.search(r"\b(?:hsl|hsla|lab|lch|oklab|oklch|color)\s*\(", emitted_css, re.I):
        invented_colors.add("unsupported-functional-color")
    if invented_colors:
        raise ValueError(
            "structured zone output invented colors: " + ", ".join(sorted(invented_colors))
        )
    invented_fonts: set[str] = set()
    for declaration in _FONT_FAMILY_PATTERN.findall(emitted_css):
        for family in declaration.split(","):
            normalized = family.strip().strip("\"'").lower()
            if normalized and normalized not in allowed_fonts and normalized not in _SAFE_FONT_FALLBACKS:
                invented_fonts.add(normalized)
    if invented_fonts:
        raise ValueError(
            "structured zone output invented fonts: " + ", ".join(sorted(invented_fonts))
        )

    probe = _ZoneHTMLProbe()
    probe.feed(html)
    probe.close()
    rules: list[str] = []
    seen_ids: set[str] = set()
    for raw_zone in raw_zones:
        if not isinstance(raw_zone, Mapping):
            raise ValueError("structured zone must be an object")
        zone_id = raw_zone.get("zoneId")
        bounds = raw_zone.get("bounds")
        if not isinstance(zone_id, str) or not _ZONE_ID_PATTERN.fullmatch(zone_id):
            raise ValueError("structured zone has an invalid zoneId")
        if zone_id in seen_ids:
            raise ValueError("structured zone IDs must be unique")
        seen_ids.add(zone_id)
        if not isinstance(bounds, Mapping):
            raise ValueError("structured zone bounds are required")
        try:
            x = float(bounds["x"])
            y = float(bounds["y"])
            width = float(bounds["width"])
            height = float(bounds["height"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("structured zone bounds are invalid") from error
        if (
            x < 0
            or y < 0
            or width <= 0
            or height <= 0
            or x + width > 1280
            or y + height > 720
        ):
            raise ValueError("structured zone leaves the 1280x720 canvas")
        generation_state = raw_zone.get("generationState")
        occurrence_count = probe.zone_occurrences.get(zone_id, 0)
        if generation_state in {"hidden", "blank"}:
            if occurrence_count:
                raise ValueError("empty structured zones must not render content")
            continue
        if occurrence_count != 1:
            raise ValueError("structured zone output must contain each zone exactly once")
        raw_blocks = raw_zone.get("blocks")
        if not isinstance(raw_blocks, list):
            raise ValueError("structured zone blocks must be a list")
        expected_blocks = {
            str(block.get("blockId"))
            for block in raw_blocks
            if isinstance(block, Mapping) and isinstance(block.get("blockId"), str)
        }
        if len(expected_blocks) != len(raw_blocks):
            raise ValueError("structured zone blocks require unique blockId values")
        if probe.zone_blocks.get(zone_id, set()) != expected_blocks:
            raise ValueError("structured zone output does not match its semantic block plan")
        max_chars = raw_zone.get("maxChars")
        max_items = raw_zone.get("maxItems")
        if not isinstance(max_chars, int) or probe.zone_chars.get(zone_id, 0) > max_chars:
            raise ValueError("structured zone output exceeds maxChars")
        if not isinstance(max_items, int) or probe.zone_items.get(zone_id, 0) > max_items:
            raise ValueError("structured zone output exceeds maxItems")
        text_align = raw_zone.get("textAlign", "inherit")
        if text_align not in {"inherit", "left", "center", "right"}:
            raise ValueError("structured zone has an invalid textAlign")
        selector_id = zone_id.replace("\\", "\\\\").replace('"', '\\"')
        text_align_rule = (
            "" if text_align == "inherit" else f"text-align:{text_align}!important;"
        )
        rules.append(
            f'[data-ai-zone-id="{selector_id}"]{{position:absolute!important;'
            f"left:{x:g}px!important;top:{y:g}px!important;width:{width:g}px!important;"
            f"height:{height:g}px!important;overflow:hidden!important;box-sizing:border-box!important;"
            f"{text_align_rule}}}"
        )
    constraint_style = (
        '<style data-ai-zone-constraints="v1">' + "".join(rules) + "</style>"
    )
    constrained, count = re.subn(
        r"</head\s*>",
        constraint_style + "</head>",
        html,
        count=1,
        flags=re.IGNORECASE,
    )
    if count != 1:
        raise ValueError("structured zone output has no closing head")
    return constrained


__all__ = ["_constrain_structured_zone_html"]
