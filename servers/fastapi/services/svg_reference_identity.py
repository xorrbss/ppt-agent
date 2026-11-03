"""Canvas-independent visual identity signals from admitted source bytes."""
from __future__ import annotations

import base64
from collections import Counter
import colorsys
import io

from utils.svg_color_identity import (
    _rgb as _rgb,
    _luminance as _luminance,
    _contrast as _contrast,
    contrast_foreground as contrast_foreground,
    identity_border as identity_border,
)


def representative_page_indices(page_count: int) -> list[int]:
    """Bounded cover, early content, middle and ending samples, in source order."""
    if page_count <= 0:
        return []
    return sorted({0, min(1, page_count - 1), page_count // 2, page_count - 1})


def visual_palette(examples: list[dict]) -> tuple[list[str], str | None]:
    """Estimate broad palette and background from rendered page edge pixels."""
    from PIL import Image

    colors: Counter[str] = Counter()
    borders: Counter[str] = Counter()
    for example in examples:
        payload = base64.b64decode(example["data_url"].split(",", 1)[1])
        with Image.open(io.BytesIO(payload)) as image:
            sample = image.convert("RGB")
            sample.thumbnail((128, 128))
            quantized = sample.quantize(colors=8)
            palette = quantized.getpalette()
            for count, index in quantized.getcolors():
                color = "#" + "".join(f"{value:02X}" for value in palette[index * 3:index * 3 + 3])
                colors[color] += count
            for x in range(sample.width):
                for y in (0, sample.height - 1):
                    borders["#" + "".join(f"{value:02X}" for value in sample.getpixel((x, y)))] += 1
            for y in range(sample.height):
                for x in (0, sample.width - 1):
                    borders["#" + "".join(f"{value:02X}" for value in sample.getpixel((x, y)))] += 1
    return [color for color, _count in colors.most_common(12)], next(iter(borders.most_common(1)), (None, 0))[0]


def color_identity(color_values: list[dict], examples: list[dict]) -> tuple[list[str], dict]:
    observed = [item["value"] for item in color_values]
    sampled, border = visual_palette(examples)
    palette = list(dict.fromkeys(observed + sampled))[:16]
    backgrounds = [item["value"] for item in color_values if "background" in item.get("roles", [])]
    background = next(iter(backgrounds), border or "#FFFFFF")
    text = [item["value"] for item in color_values
            if set(item.get("roles", [])) & {"text", "text-fill"}]
    foreground = next((color for color in text if _contrast(background, color) >= 4.5), None)
    if foreground is None:
        foreground = max(palette + ["#111827", "#FFFFFF"], key=lambda color: _contrast(background, color))
    accents = [color for color in palette if color != foreground and _contrast(background, color) >= 1.5]
    # A theme defines colours a deck may never show (Office's default
    # hyperlink #0000FF, unused accent slots). Prefer colours the slides use,
    # as shape or text paint or in the rendered pages; theme-only colours
    # stand in only when the slides show none.
    shown = {item["value"] for item in color_values if set(item.get("roles", [])) - {"theme"}} | set(sampled)
    accent = max([color for color in accents if color in shown] or accents,
                 key=lambda color: colorsys.rgb_to_hsv(*_rgb(color))[1], default=foreground)
    return palette, {
        "background": background, "foreground": foreground, "accent": accent,
        "basis": "observed-colors-and-rendered-edges" if examples else "observed-colors-with-contrast-fallback",
    }


def typography_identity(font_names: list[str], hierarchy: list[dict]) -> dict:
    from services.svg_trusted_fonts import bundled_svg_font_family

    family = bundled_svg_font_family(",".join(font_names))
    values = [item for item in hierarchy if isinstance(item.get("size_pt"), (int, float)) and item["size_pt"] > 0]
    body_candidates = [item for item in values if item.get("role") in {"body", "body-level-0", "text"}]
    body = max(body_candidates or values, key=lambda item: item.get("count", 0), default=None)
    heading_candidates = [item for item in values if item.get("role") in {"title", "center-title"}]
    heading = max(heading_candidates or values, key=lambda item: item["size_pt"], default=None)
    result = {
        "heading": {"family": family, "weight": 700},
        "body": {"family": family, "weight": 400},
        "basis": "observed-relative-sizes" if values else "visual-reference-only",
        "sizing_guidance": "Preserve relative type emphasis; choose readable sizes for the output canvas and content.",
    }
    if body and heading:
        result["heading"]["relative_to_body"] = round(heading["size_pt"] / body["size_pt"], 3)
        result["body"]["relative_to_body"] = 1.0
    return result
