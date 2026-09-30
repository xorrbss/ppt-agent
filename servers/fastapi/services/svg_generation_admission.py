"""Server-owned SVG creation admission; existing authored DTO remains frozen."""
from __future__ import annotations

from fastapi import HTTPException

from services.generation_core.style.design_pack_projection import (
    DesignPackProjectionError,
    assert_design_pack_projects,
)
from utils.env_settings.authored_vnext import is_authored_vnext_master_enabled
from utils.hashing import canonical_json_digest


def _design_pack_admission_reason() -> str:
    try:
        assert_design_pack_projects()
    except DesignPackProjectionError as error:
        return str(error)
    raise AssertionError("design pack projection unexpectedly succeeded")


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
    return {"enabled": reason is None, "reason": reason, "canvasSource": "selected-a4",
            "inputFormats": ["pptx"], "productVerified": False}


def svg_creation_admission_violation(request: object) -> str | None:
    """First reason ``request`` would fail svg admission, or ``None`` if it passes.

    Field-only checks (no ``strategy`` read): callers decide *when* to ask this
    -- an explicit ``strategy == "svg"`` request that violates it is a 422; an
    omitted-strategy request that violates it silently prefers authored
    instead (``generation_core.admission.resolve_generation_strategy``).
    """
    allowed_source_modes = {None, "source_content_restyle", "raster_reconstruction"}
    blocked = (
        (getattr(request, "source_mode", None) not in allowed_source_modes, "svg_source_restyle_not_supported"),
        (getattr(request, "design_pack_pin", None), _design_pack_admission_reason),
        (getattr(request, "generation_engine", None) == "deck-ir", "svg_engine_conflict"),
        (getattr(request, "generation_mode", "auto") == "registered-template", "svg_registered_template_not_supported"),
    )
    for condition, code in blocked:
        if condition:
            return code() if callable(code) else code
    if getattr(request, "source_mode", None) == "source_content_restyle":
        page_size = getattr(request, "page_size", None)
        orientation = getattr(request, "orientation", None)
        if page_size is not None or orientation is not None:
            if page_size != "A4" or orientation not in {"landscape", "portrait"}:
                return "document_canvas_selectors_invalid"
        policy = getattr(request, "content_policy", None)
        if policy is None or policy.text != "verbatim" or policy.allow_summarization:
            return "svg_source_verbatim_required"
        if getattr(request, "export_as", "pptx") != "pptx":
            return "source_content_format_not_supported"
    source_restyle = getattr(request, "source_mode", None) in {"source_content_restyle", "raster_reconstruction"}
    if source_restyle:
        from utils.svg_template_rules import resolve_source_template_style

        style_id = getattr(request, "authored_style", None)
        if not style_id:
            return "svg_source_template_selection_required"
        try:
            style = resolve_source_template_style(style_id)
        except ValueError as error:
            return str(error).split(":", 1)[0]
    else:
        from utils.authored_styles import resolve_authored_style

        style = resolve_authored_style(getattr(request, "authored_style", None))
    if getattr(style, "reference_mode", "") == "exact_reconstruction":
        return "svg_exact_reconstruction_not_supported"
    return None


def require_svg_creation_admission(request: object) -> None:
    if getattr(request, "strategy", None) != "svg":
        return
    violation = svg_creation_admission_violation(request)
    if violation is not None:
        raise HTTPException(status_code=422, detail=violation)


def _resolve_master_rules(request: object, canvas: object) -> tuple[dict | None, dict | None]:
    """Resolve+project a company-standard master pin for svg, or ``(None, None)``.

    Reuses the existing authored_vnext master resolution directly (frozen,
    read-only import) rather than depending on ``request._authored_master_contract``
    having already been populated by the unrelated authored-mode admission
    path in ``presentation_generate.py`` -- pin_svg_style is called from more
    than one place (durable-payload staging has no such ordering guarantee).
    """
    from models.generate_presentation_request import AuthoredMasterPinInput

    pin_request = getattr(request, "master_pin", None)
    if pin_request is None:
        return None, None
    if not isinstance(pin_request, AuthoredMasterPinInput):
        pin_request = AuthoredMasterPinInput.model_validate(pin_request)
    if not is_authored_vnext_master_enabled():
        raise HTTPException(status_code=404, detail="authored_vnext_master_disabled")
    from utils.owner_context import get_current_owner_id

    owner_id = get_current_owner_id()
    if owner_id is None:
        raise HTTPException(status_code=401, detail="authored_vnext_owner_required")
    from services.authored_vnext.generation.errors import GenerationNotFoundError, GenerationPipelineError
    from services.authored_vnext.generation.legacy_pin import resolve_legacy_master_pin

    try:
        resolved = resolve_legacy_master_pin(owner_id=owner_id, requested=pin_request)
    except GenerationPipelineError as error:
        status_code = 404 if isinstance(error, GenerationNotFoundError) else 409
        raise HTTPException(status_code=status_code, detail=error.code) from error
    from services.generation_core.style.master_projection import (
        MasterProjectionError,
        project_master_contract,
    )

    try:
        rules = project_master_contract(resolved.contract, canvas.width_px, canvas.height_px)
    except MasterProjectionError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return rules, resolved.pin.model_dump(mode="json", by_alias=True)


def pin_svg_style(request: object) -> dict:
    """Snapshot before durable admission, never reselect on job takeover."""
    from services.authored_canvas import request_canvas_contract
    from services.generation_core.drivers.svg.svg_style_adapter import adapt_svg_style

    frozen = getattr(request, "_svg_style_snapshot", None)
    if frozen is None:
        require_svg_creation_admission(request)
        canvas = request_canvas_contract(request)
        source_restyle = getattr(request, "source_mode", None) in {"source_content_restyle", "raster_reconstruction"}
        master_rules, master_pin = _resolve_master_rules(request, canvas)
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
                request=request, master_rules=master_rules, master_pin=master_pin,
            )
            if source_restyle:

                # Historical field name: binds the source-restyle OUTPUT
                # canvas, including a selected A4 target in v2 input bindings.
                frozen["source_canvas_digest"] = canvas_digest(
                    request._document_canvas.model_dump(mode="json", by_alias=True)
                )
                frozen["selected_template_id"] = style.id
                digest_payload = {key: value for key, value in frozen.items() if key != "digest"}
                frozen["digest"] = canonical_json_digest(digest_payload, allow_nan=True)
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
