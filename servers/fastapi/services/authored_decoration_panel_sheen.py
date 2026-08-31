"""Exact, source-preserving geometry for the approved empty panel-sheen family."""

from __future__ import annotations

import math
import re


_POLYGON = re.compile(
    r"polygon\(\s*64%\s+0(?:px|%)?\s*,\s*100%\s+0(?:px|%)?\s*,"
    r"\s*100%\s+100%\s*,\s*0(?:px|%)?\s+100%\s*\)"
)
_GRADIENT = "linear-gradient(135deg, rgba(255, 255, 255, 0.13), rgba(255, 255, 255, 0.03))"


def _px(value):
    match = re.fullmatch(r"(-?\d+(?:\.\d+)?)px", str(value))
    number = float(match[1]) if match else None
    return number if number is not None and math.isfinite(number) else None


def panel_sheen_family(qid, node, css):
    """A name or empty/supporting role alone is never decoration authority."""
    if not (qid.endswith(".metric.diagonal") and node.get("class", "").split() == ["metric-diagonal"]
            and _POLYGON.fullmatch(str(css.get("clipPath")))
            and css.get("backgroundImage") == _GRADIENT
            and css.get("backgroundColor") == "rgba(0, 0, 0, 0)"
            and css.get("transform") == "none" and css.get("boxSizing") == "border-box"):
        return False
    width, height = _px(css.get("width")), _px(css.get("height"))
    if width is None or height is None or not 16 <= width < height <= 720:
        return False
    if any(css.get("min" + axis) != "0px" or css.get("max" + axis) != "none" for axis in ("Width", "Height")):
        return False
    if any(_px(css.get("padding" + side)) != 0 or _px(css.get("border" + side + "Width")) != 0 for side in ("Top", "Right", "Bottom", "Left")):
        return False
    if any(css.get("border" + corner + "Radius") != "0px" for corner in ("TopLeft", "TopRight", "BottomRight", "BottomLeft")):
        return False
    return [_px(token) for token in str(css.get("transformOrigin")).split()] == [width / 2, height / 2]


def without_approved_panel_sheen_clip(css):
    """Exclude only the one approved rule from the existing static safety scan."""
    def rule(match):
        selector, body = match[1], match[2]
        if selector.strip() != ".metric-diagonal":
            return match[0]
        def declaration(value):
            return value[1] if _POLYGON.fullmatch(value[2].strip()) else value[0]
        safe_body = re.sub(r"(^|;)\s*clip-path\s*:\s*([^;]+)(?=;|$)", declaration, body)
        return selector + "{" + safe_body + "}"
    return re.sub(r"([^{}]+)\{([^{}]*)\}", rule, css)


def panel_sheen_geometry(css, scale, delta):
    """Uniform layout resize on the Chromium grid, leaving transform untouched."""
    values = {key: _px(css.get(key)) for key in ("left", "top", "right", "bottom", "width", "height")}
    if any(value is None for value in values.values()):
        return None
    width, height = values["width"] * scale, values["height"] * scale
    if any(abs(value * 64 - round(value * 64)) > 1e-8 for value in (width, height)):
        return None  # Snapping either dimension must never change the aspect ratio.
    x = round((values["left"] + delta[0] + (values["width"] - width) / 2) * 64) / 64
    y = round((values["top"] + delta[1] + (values["height"] - height) / 2) * 64) / 64
    return {
        "left": x, "top": y, "width": width, "height": height,
        "right": values["right"] - x + values["left"] + values["width"] - width,
        "bottom": values["bottom"] - y + values["top"] + values["height"] - height,
        "transformOrigin": (width / 2, height / 2),
    }
