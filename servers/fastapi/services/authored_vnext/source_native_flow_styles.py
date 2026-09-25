"""Source-native slot typography and source-local CSS emission."""
from __future__ import annotations

import json
from typing import Mapping
from . import source_render_geometry as geometry
from .composition import SealedContentUnit


def _unit_render_text(unit: SealedContentUnit) -> str:
    if unit.text.strip():
        return " ".join(unit.text.split())
    if unit.data not in (None, [], {}):
        return json.dumps(unit.data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return ""


def _flow_mapping(slot: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = slot.get(key)
    if isinstance(value, Mapping):
        return value
    raw = slot.get("raw")
    if isinstance(raw, Mapping):
        value = raw.get(key)
        if isinstance(value, Mapping):
            return value
    return {}


def _flow_number(value: object, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _flow_role(slot: Mapping[str, object]) -> str:
    return str(
        slot.get("sourceFlowRole")
        or slot.get("semanticRole")
        or "body"
    ).strip().lower()


def _flow_font_px(
    slot: Mapping[str, object], *, role: str, adaptive_scale: float
) -> float:
    return geometry.flow_font_px(
        geometry.slot_declared_font_px(slot),
        role=role,
        adaptive_scale=adaptive_scale,
    )


def _flow_text_css(
    slot: Mapping[str, object], *, role: str, adaptive_scale: float,
    _flow_mapping=_flow_mapping, _flow_number=_flow_number, _flow_font_px=_flow_font_px,
) -> str:
    style = _flow_mapping(slot, "textStyle")
    font_px = _flow_font_px(slot, role=role, adaptive_scale=adaptive_scale)
    family = str(
        style.get("eastAsianFont")
        or style.get("latinFont")
        or "Arial, sans-serif"
    ).replace("'", "")
    weight = max(100, min(1000, int(_flow_number(style.get("fontWeight"), 700 if role == "title" else 500))))
    alignment = str(style.get("alignment") or "left")
    if alignment not in {"left", "center", "right", "justify"}:
        alignment = "left"
    declared_color = str(style.get("color") or "").strip()
    # Authored Design Packs can intentionally leave a slot color unspecified
    # so that it inherits the local panel/container foreground.  Supplying a
    # dark fallback here breaks that contract on retained dark chrome.  Only a
    # sealed canonical color may override the container's computed color.
    color = declared_color if declared_color.startswith("#") else "inherit"
    return (
        f"font-family:'{family}';font-size:{font_px:.2f}px;"
        f"font-weight:{weight};"
        f"line-height:"
        f"{geometry.TITLE_LINE_HEIGHT if role == 'title' else geometry.BODY_LINE_HEIGHT};"
        f"text-align:{alignment};color:{color};letter-spacing:-0.01em"
    )


def _flow_overlay_css(slot: Mapping[str, object], *, _flow_mapping=_flow_mapping, _flow_number=_flow_number) -> str:
    from .source_native_generation import SourceNativeGenerationError
    bounds = _flow_mapping(slot, "bounds")
    spacing = _flow_mapping(slot, "spacing")
    x = _flow_number(bounds.get("x"))
    y = _flow_number(bounds.get("y"))
    width = _flow_number(bounds.get("width"))
    height = _flow_number(bounds.get("height"))
    if width <= 0 or height <= 0:
        raise SourceNativeGenerationError("source_native_flow_geometry_invalid")
    inset_left = max(0.0, _flow_number(spacing.get("insetLeft")))
    inset_top = max(0.0, _flow_number(spacing.get("insetTop")))
    inset_right = max(0.0, _flow_number(spacing.get("insetRight")))
    inset_bottom = max(0.0, _flow_number(spacing.get("insetBottom")))
    surface = str(slot.get("sourceFlowSurface") or "")
    background = (
        "background:inherit!important;color:inherit!important;"
        if surface == "container-inherited"
        else "background:transparent!important;"
    )
    return (
        "position:absolute!important;box-sizing:border-box!important;"
        f"left:{x:.3f}px!important;top:{y:.3f}px!important;"
        f"width:{width:.3f}px!important;height:{height:.3f}px!important;"
        f"padding:{inset_top:.3f}px {inset_right:.3f}px {inset_bottom:.3f}px {inset_left:.3f}px!important;"
        f"margin:0!important;{background}"
        "min-width:0!important;min-height:0!important;overflow:hidden!important"
    )


def _semantic_weight_override(
    *, semantic_role: str, process_header: bool
) -> tuple[int, bool] | None:
    """Font weight the emitter appends, and whether it restates the size.

    The sizes themselves come from `geometry.semantic_text_metrics`, so this
    only decides the purely presentational part of the declaration.
    """

    if process_header:
        return 700, True
    if semantic_role == "title":
        return 750, False
    if semantic_role in {"heading", "section-heading"}:
        return 700, True
    if semantic_role in geometry.EMPHASIS_NUMBER_ROLES:
        return 800, True
    if semantic_role in geometry.FLOOR_FONT_ROLES:
        return 650, True
    if semantic_role == "lede":
        return 500, True
    return None
