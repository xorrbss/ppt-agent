"""Server-owned SVG creation admission; existing authored DTO remains frozen."""
from __future__ import annotations

from fastapi import HTTPException

from utils.env_settings.authored_vnext import is_authored_vnext_master_enabled
from utils.hashing import canonical_json_digest


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
    reason = "svg_source_selection_required" if not selected else template_reason
    return {"enabled": reason is None, "reason": reason, "canvasSource": "selected-svg",
            "inputFormats": ["pptx"], "productVerified": False}


def pin_fresh_common_header_mode(request: object) -> None:
    """Pin fresh DTOs only; carriers and frozen worker restores keep absence.

    Under ``SVG_COMMON_HEADER_DEFAULT=reduced`` a fresh prompt-only request
    that names no mode takes the deterministic reduced header ("uniform"
    without a design plan), and a fresh "uniform" request whose caller did not
    set the cover exception leaves the cover without the header.
    """
    if ("common_header_mode" not in getattr(type(request), "model_fields", {})
            or getattr(request, "_svg_style_snapshot", None) is not None
            or getattr(request, "_svg_authoring_profile_selected", False)):
        return
    from services.svg_common_header_design_plan import fresh_header_mode, reduced_header_by_default
    if getattr(request, "common_header_mode", None) is None:
        request.common_header_mode = ("free" if getattr(request, "svg_template_mode", None) == "template-redesign"
                                      else fresh_header_mode(request))
        request._common_header_defaulted = True
        if request.common_header_mode == "uniform" and reduced_header_by_default(request):
            request._common_header_reduced = True
    if (request.common_header_mode == "uniform" and reduced_header_by_default(request, explicit_mode=True)
            and "common_header_cover_exception" not in request.model_fields_set):
        request.common_header_cover_exception = True
        request._common_header_cover_defaulted = True


def _common_header_preservation_conflict(request: object) -> bool:
    if getattr(request, "common_header_mode", None) != "uniform":
        return False
    if getattr(request, "source_mode", None) not in {"source_content_restyle", "raster_reconstruction"}:
        return False
    if getattr(request, "full_page_preserve_pages", ()):
        return True
    analysis = getattr(request, "_raster_analysis", None)
    pages = analysis.get("pages", ()) if isinstance(analysis, dict) else getattr(analysis, "pages", ())
    return any((page.get("render_mode") if isinstance(page, dict) else getattr(page, "render_mode", None))
               == "full_page_preserve" for page in pages)


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
        (getattr(request, "generation_engine", None) == "deck-ir", "svg_engine_conflict"),
    )
    for condition, code in blocked:
        if condition:
            return code
    references = (
        getattr(request, "svg_template_pin", None),
        getattr(request, "svg_design_reference_snapshot_id", None),
        getattr(request, "master_pin", None),
    )
    if sum(value is not None for value in references) > 1:
        return "svg_design_reference_conflict"
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
            resolve_source_template_style(style_id)
        except ValueError as error:
            return str(error).split(":", 1)[0]
    else:
        from utils.authored_styles import resolve_authored_style

        resolve_authored_style(getattr(request, "authored_style", None))
    return None


def require_svg_creation_admission(request: object) -> None:
    from services.svg_common_header_design_plan import fresh_header_mode_conflict
    if (getattr(request, "strategy", None) in (None, "svg")
            and (_common_header_preservation_conflict(request) or fresh_header_mode_conflict(request))):
        # This is a conflicting user choice, not a reason to silently select
        # another generation strategy through the capability predicate.
        raise HTTPException(status_code=422, detail="svg_common_header_source_preservation_conflict")
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
        enrich_master_reference,
        project_master_reference,
    )

    try:
        rules = project_master_reference(resolved.contract, canvas.width_px, canvas.height_px)
        rules = enrich_master_reference(
            rules, resolved.contract, owner_id=resolved.owner_id,
            width=canvas.width_px, height=canvas.height_px,
        )
    except MasterProjectionError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return rules, resolved.pin.model_dump(mode="json", by_alias=True)


def _resolve_design_reference(request: object, canvas: object) -> dict | None:
    """Resolve a new SVG design reference once under the request's owner."""
    if getattr(request, "svg_template_pin", None) is not None:
        selection = getattr(request, "_svg_template_selection", None)
        if not isinstance(selection, dict):
            raise HTTPException(status_code=422, detail="svg_template_admission_required")
        return selection.get("style_projection") or None
    if getattr(request, "svg_design_reference_snapshot_id", None) is not None:
        uploaded = getattr(request, "_svg_uploaded_design_reference", None)
        if not isinstance(uploaded, dict):
            raise HTTPException(status_code=422, detail="svg_uploaded_design_reference_admission_required")
        return uploaded
    return None


async def prepare_private_svg_recovery_authority(request: object, session) -> list[dict]:
    """Keep live owner-bound source permission outside immutable style snapshots."""
    from services.svg_authoring_profile import request_quality_recovery_policy, read_quality_recovery_policy
    from services.svg_recovery_source_evidence import verified_recovery_source_evidence

    if getattr(request, "common_header_mode", None) not in {"free", "page_free"}:
        # Every svg admission awaits this step before pin_svg_style, whose
        # header preflight measures titles synchronously.
        from services.svg_common_header_space_advance import ensure_space_advance_measurement

        await ensure_space_advance_measurement()
    request._svg_recovery_source_evidence = []
    frozen = getattr(request, "_svg_style_snapshot", None)
    policy = read_quality_recovery_policy(frozen) if frozen is not None else request_quality_recovery_policy(request)
    if policy is None or getattr(request, "source_mode", None) in {"source_content_restyle", "raster_reconstruction"}:
        return []
    if frozen is not None:
        candidates = frozen.get("validated_recovery_candidates") or []
    else:
        from utils.authored_styles import resolve_authored_style

        candidates = getattr(resolve_authored_style(getattr(request, "authored_style", None)),
                             "validated_recovery_candidates", ())
    evidence = await verified_recovery_source_evidence(candidates, session)
    request._svg_recovery_source_evidence = evidence
    return evidence


def pin_svg_style(request: object) -> dict:
    """Snapshot before durable admission, never reselect on job takeover."""
    from services.authored_canvas import request_canvas_contract
    from services.generation_core.drivers.svg.svg_style_adapter import adapt_svg_style
    from services.svg_authoring_profile import (
        new_svg_admission_profile, seal_new_quality_recovery_plan,
        validate_profile_agreement, validate_quality_recovery_agreement,
    )

    frozen = getattr(request, "_svg_style_snapshot", None)
    require_svg_creation_admission(request)
    if frozen is None:
        pin_fresh_common_header_mode(request)
        fresh_readability = not getattr(request, "_svg_authoring_profile_selected", False)
        try:
            # Direct durable-payload callers also seal a known fresh count
            # before authoring-profile selection makes this a replay choice.
            count = getattr(request, "n_slides", None)
            markdown = getattr(request, "slides_markdown", None)
            if markdown:
                count = len(markdown)
            if getattr(request, "source_mode", None) == "source_content_restyle":
                outline = getattr(request, "_source_document_outline", None)
                count = getattr(outline, "source_slide_count", None)
            selected_plan = getattr(request, "_svg_quality_recovery_plan", None)
            if selected_plan is not None:
                count = selected_plan["slide_count"]
            seal_new_quality_recovery_plan(request, count)
            authoring_profile = new_svg_admission_profile(request)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        require_svg_creation_admission(request)
        canvas = request_canvas_contract(request)
        source_restyle = getattr(request, "source_mode", None) in {"source_content_restyle", "raster_reconstruction"}
        master_rules, master_pin = _resolve_master_rules(request, canvas)
        design_reference_rules = _resolve_design_reference(request, canvas)
        try:
            from services.svg_common_header_design_plan import seal_new_header_design_plan
            if fresh_readability:
                seal_new_header_design_plan(request)
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
                design_reference_rules=design_reference_rules,
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
            if authoring_profile is not None:
                frozen["authoring_profile"] = authoring_profile
                frozen["digest"] = canonical_json_digest(
                    {key: value for key, value in frozen.items() if key != "digest"}, allow_nan=True,
                )
            recovery_plan = getattr(request, "_svg_quality_recovery_plan", None)
            if recovery_plan is not None:
                frozen["quality_recovery_plan"] = dict(recovery_plan)
                frozen["digest"] = canonical_json_digest(
                    {key: value for key, value in frozen.items() if key != "digest"}, allow_nan=True,
                )
            header_design_plan = getattr(request, "_svg_header_design_plan", None)
            if header_design_plan is not None:
                from services.svg_common_header_design_plan import require_header_design_plan
                frozen["common_header_design_plan"] = require_header_design_plan(request)
                frozen["digest"] = canonical_json_digest(
                    {key: value for key, value in frozen.items() if key != "digest"}, allow_nan=True,
                )
                from services.svg_common_header_design_plan import preflight_admitted_header
                preflight_admitted_header(frozen, request, canvas)
            elif (getattr(request, "_common_header_reduced", False)
                    and getattr(request, "common_header_mode", None) == "uniform"):
                from copy import deepcopy
                from services.svg_common_header import REDUCED_HEADER_STYLE, REDUCED_HEADER_STYLE_KEY
                frozen[REDUCED_HEADER_STYLE_KEY] = deepcopy(REDUCED_HEADER_STYLE)
                frozen["digest"] = canonical_json_digest(
                    {key: value for key, value in frozen.items() if key != "digest"}, allow_nan=True,
                )
            if fresh_readability:
                from services.authored_canvas import request_canvas_metadata
                from services.document_canvas_generation import svg_readability_policy

                frozen["readability_policy"] = svg_readability_policy(
                    request_canvas_metadata(request), source_restyle=source_restyle,
                    instructions=getattr(request, "instructions", None),
                )
                # Native chart/table requirement revision, pinned once so a resumed deck keeps the
                # literal grammar (and outline-time prose chart lines) it was authored with.
                from services.generation_core.drivers.svg.native_requirements import (
                    ADMITTED_REQUIREMENTS_REVISION, REQUIREMENTS_VERSION_V5, SNAPSHOT_REVISION_KEY)
                from utils.get_env_concurrency import get_svg_native_chart_integrity_v5_env
                frozen[SNAPSHOT_REVISION_KEY] = (
                    REQUIREMENTS_VERSION_V5 if get_svg_native_chart_integrity_v5_env()
                    else ADMITTED_REQUIREMENTS_REVISION)
                from utils.get_env_concurrency import get_svg_server_charts_env
                if get_svg_server_charts_env():
                    # Charts drawn by the server from the declaration (server_chart.py),
                    # pinned like the revision above: a deck keeps the renderer revision
                    # it was admitted with. The current revision is trusted by construction
                    # (no drawing or paint proof), so V4 and V5 decks both admit it.
                    from services.generation_core.drivers.svg.server_chart import (
                        SERVER_CHART_CURRENT, SNAPSHOT_KEY as SERVER_CHART_KEY)
                    frozen[SERVER_CHART_KEY] = SERVER_CHART_CURRENT
                from services.generation_core.drivers.svg.icon_flatten import (
                    ASSET_PROJECTION_CURRENT, ASSET_PROJECTION_KEY,
                )
                frozen[ASSET_PROJECTION_KEY] = ASSET_PROJECTION_CURRENT
                # Page-number ink rule, pinned the same way: a fresh deck's page number
                # reads on its page; a stored deck without the pin keeps the style primary.
                from services.svg_contract import PAGE_NUMBER_INK_KEY, PAGE_NUMBER_INK_READABLE
                frozen[PAGE_NUMBER_INK_KEY] = PAGE_NUMBER_INK_READABLE
                if getattr(request, "source_mode", None) == "source_content_restyle":
                    frozen["source_authoring_contract"] = {"version": "svg-source-whole-page/v1"}
                    from services.svg_source_charts import admitted_source_chart_contract
                    from copy import deepcopy
                    chart_contract = admitted_source_chart_contract(request)
                    if chart_contract is not None:
                        frozen["source_chart_contract"] = deepcopy(chart_contract)
                    # The author output view is pinned once, like the contract above, so
                    # every attempt and replay of this job reads its responses the same way.
                    from services.svg_author_output_view import admission_output_view
                    output_view = admission_output_view(request)
                    if output_view is not None:
                        frozen["source_output_view"] = output_view
                    # The existing source measurement embeds this frozen font
                    # material; whole-page authors must receive the same family.
                    fonts = frozen["source_template"]["fonts"]
                    frozen["tokens"]["heading_font"] = fonts["heading"]["family"]
                    frozen["tokens"]["body_font"] = fonts["body"]["family"]
                frozen["digest"] = canonical_json_digest(
                    {key: value for key, value in frozen.items() if key != "digest"}, allow_nan=True,
                )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        if getattr(request, "svg_template_pin", None) is not None:
            from services.svg_templates.generation import apply_template_snapshot
            apply_template_snapshot(frozen, request)
        request._svg_style_snapshot = frozen
    try:
        if getattr(request, "svg_template_pin", None) is not None:
            from services.svg_templates.generation import template_selection
            selected = template_selection(frozen)
            if selected is None or selected != getattr(request, "_svg_template_selection", None):
                raise ValueError("svg_template_execution_snapshot_conflict")
        from services.generation_core.drivers.svg.icon_flatten import asset_projection_revision
        asset_projection_revision(frozen)
        validate_profile_agreement(request, frozen)
        validate_quality_recovery_agreement(request, frozen)
        from services.svg_common_header_design_plan import require_header_design_plan, validate_header_design_plan
        design_plan = validate_header_design_plan(getattr(request, "_svg_header_design_plan", None))
        if design_plan is not None:
            if frozen.get("common_header_design_plan") != require_header_design_plan(request):
                raise ValueError("svg_common_header_design_plan_conflict")
        elif frozen.get("common_header_design_plan") is not None:
            raise ValueError("svg_common_header_design_plan_missing")
        from services.document_canvas_generation import validate_svg_readability_policy

        validate_svg_readability_policy(frozen.get("readability_policy"))
        if frozen.get("source_authoring_contract") not in (None, {"version": "svg-source-whole-page/v1"}):
            raise ValueError("svg_source_authoring_contract_invalid")
        from services.svg_author_output_view import snapshot_output_view
        snapshot_output_view(frozen)
        from services.svg_source_charts import validate_source_chart_contract
        validate_source_chart_contract(frozen.get("source_chart_contract"))
        if frozen.get("source_chart_contract") and frozen.get("source_authoring_contract") != {"version": "svg-source-whole-page/v1"}:
            raise ValueError("svg_source_chart_contract_invalid")
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if getattr(request, "source_mode", None) in {"source_content_restyle", "raster_reconstruction"}:
        from services.document_canvas import canvas_digest

        canvas = getattr(request, "_document_canvas", None)
        from utils.svg_template_rules import resolve_source_template_style
        expected_style_id = resolve_source_template_style(getattr(request, "authored_style", None)).id
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
        binding = getattr(request, "_svg_source_binding", None)
        if binding is not None and binding.get("sourceChartContract") != frozen.get("source_chart_contract"):
            raise HTTPException(status_code=409, detail="svg_source_chart_snapshot_mismatch")
    return frozen
