"""Server-owned SVG creation admission; existing authored DTO remains frozen."""
from __future__ import annotations

from fastapi import HTTPException


def require_svg_creation_admission(request: object) -> None:
    if getattr(request, "strategy", None) != "svg":
        return
    blocked = (
        (getattr(request, "source_mode", None), "svg_source_restyle_not_supported"),
        (getattr(request, "master_pin", None), "svg_company_standard_not_supported"),
        (getattr(request, "design_pack_pin", None), "svg_design_pack_not_supported"),
        (getattr(request, "generation_engine", None) == "deck-ir", "svg_engine_conflict"),
        (getattr(request, "generation_mode", "auto") == "registered-template", "svg_registered_template_not_supported"),
    )
    for condition, code in blocked:
        if condition:
            raise HTTPException(status_code=422, detail=code)
    from utils.authored_styles import resolve_authored_style

    style = resolve_authored_style(getattr(request, "authored_style", None))
    if getattr(style, "reference_mode", "") == "exact_reconstruction":
        raise HTTPException(status_code=422, detail="svg_exact_reconstruction_not_supported")


def pin_svg_style(request: object) -> dict:
    """Snapshot before durable admission, never reselect on job takeover."""
    from services.authored_canvas import request_canvas_contract
    from utils.authored_styles import resolve_authored_style
    from utils.svg_style_adapter import adapt_svg_style

    frozen = getattr(request, "_svg_style_snapshot", None)
    if frozen is None:
        require_svg_creation_admission(request)
        canvas = request_canvas_contract(request)
        try:
            frozen = adapt_svg_style(
                resolve_authored_style(getattr(request, "authored_style", None)),
                width=canvas.width_px, height=canvas.height_px,
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        request._svg_style_snapshot = frozen
    return frozen
