"""SVG selector resolution independent from Authored style promotion gates."""
from decimal import Decimal, ROUND_HALF_UP

from models.svg_canvas import SvgCanvasInput
from services.document_canvas import (
    DocumentCanvasMetadata, LogicalSize, PhysicalSize, SafeArea,
    document_canvas_for_orientation,
)

SVG_ASPECT_RATIOS = ("16:9", "4:3", "9:16", "9:13", "1:1", "21:9")
SVG_PRESET_IDS = ("wide-16x9", "standard-4x3", "tall-9x16", "tall-9x13",
                  "square-1x1", "ultrawide-21x9", "a4-landscape", "a4-portrait")


def svg_canvas_for_aspect_ratio(ratio: str) -> DocumentCanvasMetadata:
    from services.authored_canvas import canvas_metadata_for_aspect_ratio

    if ratio == "4:3":
        width, height = 960, 720
    else:
        legacy = canvas_metadata_for_aspect_ratio(ratio)
        width, height = legacy.width_px, legacy.height_px
    return DocumentCanvasMetadata(
        contract_version="document-canvas/v1",
        logical_size=LogicalSize(width=width, height=height),
        physical_size=PhysicalSize(width_emu=width * 9525, height_emu=height * 9525),
        safe_area=SafeArea(x=64, y=64, width=width - 128, height=height - 128),
    )


def custom_svg_canvas(value: object) -> DocumentCanvasMetadata:
    selected = SvgCanvasInput.model_validate(value)
    if selected.exact_emu:
        from services.document_canvas import document_canvas_from_source_slide_size
        return document_canvas_from_source_slide_size(width_emu=selected.width_emu, height_emu=selected.height_emu)
    scale = Decimal(1280) / max(selected.width_mm, selected.height_mm)
    width = int((selected.width_mm * scale).to_integral_value(rounding=ROUND_HALF_UP))
    height = int((selected.height_mm * scale).to_integral_value(rounding=ROUND_HALF_UP))
    # The contract measures rounding error on height. Select the closest
    # height for a rounded portrait width as well.
    height = int((Decimal(width) * selected.height_mm / selected.width_mm).to_integral_value(rounding=ROUND_HALF_UP))
    return DocumentCanvasMetadata(
        contract_version="document-canvas/v1",
        logical_size=LogicalSize(width=width, height=height),
        physical_size=PhysicalSize(width_emu=int(selected.width_mm * 36000),
                                   height_emu=int(selected.height_mm * 36000)),
        safe_area=SafeArea(x=64, y=64, width=width - 128, height=height - 128),
    )


def has_explicit_svg_canvas(request: object) -> bool:
    frozen = getattr(request, "_svg_source_binding", None)
    if isinstance(frozen, dict) and frozen.get("version") == "svg-source-input/v1":
        return False
    return (getattr(request, "svg_canvas", None) is not None
            or getattr(request, "page_size", None) is not None
            or "aspect_ratio" in getattr(request, "model_fields_set", set()))


def selected_svg_canvas(request: object) -> DocumentCanvasMetadata:
    custom = getattr(request, "svg_canvas", None)
    if custom is not None:
        return custom_svg_canvas(custom)
    if getattr(request, "page_size", None) == "A4":
        return document_canvas_for_orientation(request.orientation)
    return svg_canvas_for_aspect_ratio(getattr(request, "aspect_ratio", "16:9"))


def svg_canvas_capabilities(strategy: str | None) -> dict:
    enabled = strategy == "svg"
    return {
        "enabled": enabled,
        "presets": [{"id": key, "enabled": enabled,
                     "reason": None if enabled else "svg_strategy_required"} for key in SVG_PRESET_IDS],
        "custom": {"enabled": enabled, "minMm": 50, "maxMm": 1000,
                   "maxDecimalPlaces": 2, "minAspectRatio": 0.25, "maxAspectRatio": 4},
    }
