"""Server-owned v2 admission bounds; prototype limits are not measured SLAs."""
from __future__ import annotations

from collections.abc import Mapping
import re
import secrets

from utils.hashing import canonical_json_digest

INTERNAL_HEADER_DESIGN_PLAN_KEY = "_internal_svg_header_design_plan"
HEADER_DESIGN_PLAN_VERSION = "svg-common-header-design-plan/v2"
_HASH = re.compile(r"^sha256:[a-f0-9]{64}$")
_LIMITS = {
    "candidate_count": 2, "header_call_limit": 4,
    "representative_body_count": 2, "representative_body_attempt_limit": 2,
    "input_token_limit": 32768, "output_token_limit": 16384,
    "call_timeout_seconds": 180, "selection_image_max_pixels": 8388608,
    "selection_image_max_bytes": 8388608, "selection_image_max_count": 2,
    "max_svg_bytes": 262144, "max_svg_nodes": 2048, "max_render_seconds": 60,
}


def _dump(value):
    return value.model_dump(mode="json", by_alias=True) if hasattr(value, "model_dump") else value


def _references(request):
    design = _dump(getattr(request, "common_header_design", None)) or {}
    requested = design.get("reference_snapshot_ids", design.get("referenceSnapshotIds", []))
    snapshot_id = getattr(request, "svg_design_reference_snapshot_id", None)
    uploaded = getattr(request, "_svg_uploaded_design_reference", None)
    frozen = getattr(request, "_svg_style_snapshot", None)
    if uploaded is None and isinstance(frozen, dict):
        uploaded = frozen.get("design_pack_template")
    admitted = {}
    if snapshot_id is not None and isinstance(uploaded, dict):
        provenance = uploaded.get("provenance", {})
        if provenance.get("snapshot_id") == str(snapshot_id) and isinstance(uploaded.get("digest"), str):
            admitted[str(snapshot_id)] = uploaded["digest"]
    if any(str(snapshot) not in admitted for snapshot in requested):
        raise ValueError("svg_common_header_design_reference_admission_required")
    return [{"snapshot_id": str(snapshot), "digest": admitted[str(snapshot)]} for snapshot in requested]


def _identity(request):
    from services.authored_canvas import request_canvas_metadata
    from services.svg_authoring_profile import request_authoring_profile

    names = ("content", "slides_markdown", "instructions", "language", "tone", "writing_policy",
             "common_header_design", "common_header_mode", "common_header_cover_exception", "n_slides",
             "source_mode", "svg_design_reference_pin", "svg_design_reference_snapshot_id", "master_pin",
             "codex_generation_preset", "authored_style", "reference_snapshot_ids")
    values = {name: _dump(getattr(request, name, None)) for name in names}
    # Public UUID/model values are converted by the DTO's own JSON serializer.
    public = request.model_dump(mode="json") if hasattr(request, "model_dump") else None
    if public is not None:
        values = {name: public.get(name) for name in names}
    return {"request": values, "canvas": _dump(request_canvas_metadata(request)), "references": _references(request),
            "authoring_profile": request_authoring_profile(request),
            "source_binding": _dump(getattr(request, "_svg_source_binding", None))}


def validate_header_design_plan(value):
    if value is None:
        return None
    expected = {"schema", "stage", "capability_version", "required_capabilities", "seed", "input_digest", "plan_digest", *_LIMITS}
    if (not isinstance(value, Mapping) or set(value) != expected
            or value.get("schema") != HEADER_DESIGN_PLAN_VERSION or value.get("stage") != "header"
            or value.get("capability_version") != "svg-static/v2"
            or value.get("required_capabilities") != ["structuredOutput", "vision"]
            or not isinstance(value.get("seed"), str) or not re.fullmatch(r"[a-f0-9]{32}", value["seed"])
            or any(type(value.get(key)) is not int or value[key] != limit for key, limit in _LIMITS.items())
            or any(not isinstance(value.get(key), str) or not _HASH.fullmatch(value[key]) for key in ("input_digest", "plan_digest"))
            or value.get("plan_digest") != canonical_json_digest({key: item for key, item in value.items() if key != "plan_digest"})):
        raise ValueError("svg_common_header_design_plan_invalid")
    return {**value, "required_capabilities": list(value["required_capabilities"])}


def seal_new_header_design_plan(request, *, explicit_revision=False):
    """Called only for fresh admission; frozen jobs never acquire new spend."""
    if type(explicit_revision) is not bool:
        raise ValueError("svg_common_header_design_revision_invalid")
    existing = validate_header_design_plan(getattr(request, "_svg_header_design_plan", None))
    if existing is not None:
        return require_header_design_plan(request)
    from services.svg_generation_admission import _common_header_preservation_conflict
    if (getattr(request, "common_header_mode", None) != "uniform"
            or _common_header_preservation_conflict(request)):
        return None
    frozen = getattr(request, "_svg_style_snapshot", None)
    if frozen is not None or (getattr(request, "_svg_authoring_profile_selected", False) and not explicit_revision):
        # In particular, preserve historical v1 snapshots and absent plans.
        return None
    from services.svg_authoring_profile import new_svg_admission_profile
    new_svg_admission_profile(request)
    plan = {"schema": HEADER_DESIGN_PLAN_VERSION, "stage": "header", **_LIMITS,
            "capability_version": "svg-static/v2", "required_capabilities": ["structuredOutput", "vision"],
            "seed": secrets.token_hex(16), "input_digest": canonical_json_digest(_identity(request))}
    plan["plan_digest"] = canonical_json_digest(plan)
    request._svg_header_design_plan = plan
    return validate_header_design_plan(plan)


def require_header_design_plan(request):
    plan = validate_header_design_plan(getattr(request, "_svg_header_design_plan", None))
    if plan is None:
        raise ValueError("svg_common_header_design_plan_missing")
    from services.svg_generation_admission import _common_header_preservation_conflict
    if (getattr(request, "common_header_mode", None) != "uniform"
            or _common_header_preservation_conflict(request)
            or plan["input_digest"] != canonical_json_digest(_identity(request))):
        raise ValueError("svg_common_header_design_plan_conflict")
    return plan


def svg_header_design_admission_budget(request, slide_count):
    if validate_header_design_plan(getattr(request, "_svg_header_design_plan", None)) is None:
        return 0
    if type(slide_count) is not int or slide_count < 1:
        raise ValueError("svg_common_header_design_plan_conflict")
    return require_header_design_plan(request)["header_call_limit"]


def svg_header_representative_body_admission_budget(request, slide_count):
    if svg_header_design_admission_budget(request, slide_count) == 0:
        return 0
    # One additional first author call and its bounded repair remain distinct
    # from the N final page calls and the missing-title slot.
    return require_header_design_plan(request)["representative_body_attempt_limit"]
