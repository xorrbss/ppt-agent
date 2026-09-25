"""Server-owned SVG creation admission; existing authored DTO remains frozen."""
from __future__ import annotations

from fastapi import HTTPException


def source_svg_capability(*, strategy: str | None, source_content: bool | None,
                          has_company_standard: bool | None, reference_mode: str | None,
                          style_id: str | None = None) -> dict:
    selected = strategy == "svg" and source_content is True
    template_reason = None
    if selected:
        try:
            from utils.svg_template_rules import resolve_source_template_style

            if not style_id:
                raise ValueError("svg_source_template_selection_required")
            resolve_source_template_style(style_id)
        except ValueError as error:
            template_reason = str(error).split(":", 1)[0]
    reason = (
        "svg_source_selection_required" if not selected
        else "svg_company_standard_not_supported" if has_company_standard
        else "svg_exact_reconstruction_not_supported" if reference_mode == "exact_reconstruction"
        else template_reason
    )
    return {"enabled": reason is None, "reason": reason, "canvasSource": "source-manifest",
            "inputFormats": ["pptx"], "productVerified": False}


def require_svg_creation_admission(request: object) -> None:
    if getattr(request, "strategy", None) != "svg":
        return
    blocked = (
        (getattr(request, "source_mode", None) not in {None, "source_content_restyle", "raster_reconstruction"}, "svg_source_restyle_not_supported"),
        (getattr(request, "master_pin", None), "svg_company_standard_not_supported"),
        (getattr(request, "design_pack_pin", None), "svg_design_pack_not_supported"),
        (getattr(request, "generation_engine", None) == "deck-ir", "svg_engine_conflict"),
        (getattr(request, "generation_mode", "auto") == "registered-template", "svg_registered_template_not_supported"),
    )
    for condition, code in blocked:
        if condition:
            raise HTTPException(status_code=422, detail=code)
    if getattr(request, "source_mode", None) == "source_content_restyle":
        if (getattr(request, "page_size", None) is not None
                or getattr(request, "orientation", None) is not None):
            raise HTTPException(status_code=422, detail="svg_source_canvas_override_not_supported")
        policy = getattr(request, "content_policy", None)
        if policy is None or policy.text != "verbatim" or policy.allow_summarization:
            raise HTTPException(status_code=422, detail="svg_source_verbatim_required")
        if getattr(request, "export_as", "pptx") != "pptx":
            raise HTTPException(status_code=422, detail="source_content_format_not_supported")
    source_restyle = getattr(request, "source_mode", None) in {"source_content_restyle", "raster_reconstruction"}
    if source_restyle:
        from utils.svg_template_rules import resolve_source_template_style

        style_id = getattr(request, "authored_style", None)
        if not style_id:
            raise HTTPException(status_code=422, detail="svg_source_template_selection_required")
        try:
            style = resolve_source_template_style(style_id)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error).split(":", 1)[0]) from error
    else:
        from utils.authored_styles import resolve_authored_style

        style = resolve_authored_style(getattr(request, "authored_style", None))
    if getattr(style, "reference_mode", "") == "exact_reconstruction":
        raise HTTPException(status_code=422, detail="svg_exact_reconstruction_not_supported")


def pin_svg_style(request: object) -> dict:
    """Snapshot before durable admission, never reselect on job takeover."""
    from services.authored_canvas import request_canvas_contract
    from utils.svg_style_adapter import adapt_svg_style

    frozen = getattr(request, "_svg_style_snapshot", None)
    if frozen is None:
        require_svg_creation_admission(request)
        canvas = request_canvas_contract(request)
        source_restyle = getattr(request, "source_mode", None) in {"source_content_restyle", "raster_reconstruction"}
        try:
            if source_restyle:
                from services.document_canvas import canvas_digest
                from utils.svg_template_rules import resolve_source_template_style

                if getattr(request, "_document_canvas", None) is None:
                    raise ValueError("svg_source_canvas_binding_required")
                style = resolve_source_template_style(getattr(request, "authored_style", None))
            else:
                from utils.authored_styles import resolve_authored_style

                style = resolve_authored_style(getattr(request, "authored_style", None))
            frozen = adapt_svg_style(
                style,
                width=canvas.width_px, height=canvas.height_px,
                source_template=source_restyle,
            )
            if source_restyle:
                import hashlib
                import json

                frozen["source_canvas_digest"] = canvas_digest(
                    request._document_canvas.model_dump(mode="json", by_alias=True)
                )
                frozen["selected_template_id"] = style.id
                digest_payload = {key: value for key, value in frozen.items() if key != "digest"}
                frozen["digest"] = "sha256:" + hashlib.sha256(
                    json.dumps(
                        digest_payload,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        request._svg_style_snapshot = frozen
    if getattr(request, "source_mode", None) in {"source_content_restyle", "raster_reconstruction"}:
        from services.document_canvas import canvas_digest

        canvas = getattr(request, "_document_canvas", None)
        expected_style_id = getattr(request, "authored_style", None)
        template = frozen.get("source_template") if isinstance(frozen, dict) else None
        if (
            canvas is None
            or not isinstance(template, dict)
            or frozen.get("selected_template_id") != expected_style_id
            or template.get("style_id") != expected_style_id
            or frozen.get("source_canvas_digest")
            != canvas_digest(canvas.model_dump(mode="json", by_alias=True))
        ):
            raise HTTPException(status_code=409, detail="svg_source_style_binding_mismatch")
    return frozen
