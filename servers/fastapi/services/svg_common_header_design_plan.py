"""Versioned header policy; local estimates are not provider capacity SLAs."""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import io
import json
import math
import re
import secrets

from utils.hashing import canonical_json_digest

INTERNAL_HEADER_DESIGN_PLAN_KEY = "_internal_svg_header_design_plan"
LEGACY_HEADER_DESIGN_PLAN_VERSION = "svg-common-header-design-plan/v2"
HEADER_DESIGN_PLAN_VERSION = "svg-common-header-design-plan/v3"
_HASH = re.compile(r"^sha256:[a-f0-9]{64}$")
_LIMITS = {
    "candidate_count": 2, "header_call_limit": 4,
    "representative_body_count": 2, "representative_body_attempt_limit": 2,
    "input_token_limit": 32768, "output_token_limit": 16384,
    "call_timeout_seconds": 180, "selection_image_max_pixels": 8388608,
    "selection_image_max_bytes": 8388608, "selection_image_max_count": 2,
    "max_svg_bytes": 262144, "max_svg_nodes": 2048, "max_render_seconds": 60,
}
_POLICY = {
    "version": "svg-header-capacity-fallback/v1",
    "estimator": "unicode-lexical-conservative/v1",
    "context_token_limit": 57344,
    "reasoning_headroom_tokens": 8192,
    "message_headroom_tokens": 1024,
    "context_byte_limit": 8388608,
    "request_byte_limit": 33554432,
    "providerCapacityVerified": False,
    "fallbacks": ["reduced", "free"],
    "fit_preflight": "bound-titles-before-header-and-representative-spend",
}
_BOUNDED_FAILURES = frozenset({
    "svg_common_header_input_capacity_exceeded",
    "svg_common_header_candidates_ineligible",
    "svg_common_header_all_candidates_unsuitable",
    "svg_common_header_representative_visual_review_failed",
})


def uses_header_design(plan):
    return isinstance(plan, Mapping) and plan.get("schema") in {
        LEGACY_HEADER_DESIGN_PLAN_VERSION, HEADER_DESIGN_PLAN_VERSION,
    }


def fresh_header_mode(request):
    return ("free" if getattr(request, "source_mode", None) in {
        "source_content_restyle", "raster_reconstruction",
    } else "uniform")


def fresh_header_mode_conflict(request):
    return (getattr(request, "common_header_mode", None) == "uniform"
            and fresh_header_mode(request) == "free"
            and getattr(request, "_svg_style_snapshot", None) is None
            and getattr(request, "_svg_header_design_plan", None) is None
            and not getattr(request, "_svg_authoring_profile_selected", False))


def estimate_header_text_tokens(text):
    """Local lexical estimate: ASCII subwords, Unicode codepoints and symbols.

    This deliberately does not convert serialized byte counts into tokens.
    It is an admission estimate, not a claim about an OAuth model tokenizer.
    """
    return sum(math.ceil(len(part) / 3) if part.isascii() and part.isalnum()
               else len(part) * (2 if not part.isascii() else 1)
               for part in re.findall(r"[A-Za-z0-9]+|\s+|[^A-Za-z0-9\s]", text))


def evaluate_header_capacity(plan, *, text, schema, images=(), envelope):
    if plan.get("schema") != HEADER_DESIGN_PLAN_VERSION:
        if envelope["serializedContextBytes"] + 1024 > plan["input_token_limit"]:
            raise ValueError("svg_common_header_input_capacity_exceeded")
        return envelope
    validate_header_design_plan(plan)
    policy = plan["policy"]
    image_tokens = 0
    from PIL import Image
    for image in images:
        with Image.open(io.BytesIO(image)) as decoded:
            width, height = decoded.size
        if (len(image) > plan["selection_image_max_bytes"]
                or width * height > plan["selection_image_max_pixels"]):
            raise ValueError("svg_common_header_input_capacity_exceeded")
        image_tokens += 85 + 170 * math.ceil(width / 512) * math.ceil(height / 512)
    prompt_tokens = estimate_header_text_tokens(text)
    schema_tokens = estimate_header_text_tokens(json.dumps(schema, ensure_ascii=False))
    projected = prompt_tokens + schema_tokens + image_tokens + policy["message_headroom_tokens"]
    if (len(images) > plan["selection_image_max_count"]
            or projected > plan["input_token_limit"]
            or projected + plan["output_token_limit"] + policy["reasoning_headroom_tokens"]
                > policy["context_token_limit"]
            or envelope["serializedContextBytes"] > policy["context_byte_limit"]
            or envelope["serializedRequestBytes"] > policy["request_byte_limit"]):
        raise ValueError("svg_common_header_input_capacity_exceeded")
    return {**envelope, "estimatedPromptTokens": prompt_tokens, "estimatedSchemaTokens": schema_tokens,
            "estimatedImageTokens": image_tokens, "estimatedInputTokens": projected,
            "tokenEstimator": policy["estimator"], "providerCapacityVerified": False}


def permits_header_fallback(plan, reason, document):
    if plan.get("schema") != HEADER_DESIGN_PLAN_VERSION or reason not in _BOUNDED_FAILURES:
        return False
    validate_header_design_plan(plan)
    if (document.get("plan_digest", plan["plan_digest"]) != plan["plan_digest"]
            or document.get("input_digest", plan["input_digest"]) != plan["input_digest"]):
        raise ValueError("svg_common_header_checkpoint_input_conflict")
    if (document.get("state") in {"unknown", "started", "inflight"}
            or document.get("inflight") is not None):
        return False
    # An observed provider attempt is never discarded or bought a second time.
    return all(step.get("status") == "succeeded" for step in document.get("steps", {}).values())


def known_header_design_rejection(plan, error):
    if plan.get("schema") != HEADER_DESIGN_PLAN_VERSION:
        return True
    from xml.etree.ElementTree import ParseError
    return isinstance(error, ParseError) or str(error) in {
        "svg_common_header_candidate_response_invalid", "svg_common_header_candidate_capacity_exceeded",
        "svg_common_header_template_invalid", "svg_common_header_geometry_invalid",
        "svg_common_header_physical_readability_invalid", "svg_common_header_title_overflow",
        "svg_common_header_body_space_unresolved", "svg_common_header_brand_overlap",
        "svg_common_header_complexity_limit", "svg_common_header_field_invalid",
        # Defects in the model's own candidate (its SVG, fields or fonts), found
        # by prepare_common_header_v2. They reject that candidate, so a deck
        # whose two candidates both fail takes the bounded fallback instead of
        # failing outright; binding, digest and page-identity errors stay fatal.
        "svg_common_header_paint_outside_header", "svg_common_header_text_overlap",
        "svg_common_header_invisible_text", "svg_common_header_unbound_text",
        "svg_common_header_field_fill_required", "svg_common_header_field_attributes_invalid",
        "svg_common_header_field_dependency_invalid", "svg_common_header_element_id_invalid",
        "svg_common_header_body_brand_overlap", "svg_common_header_asset_invalid",
        "svg_common_header_asset_source_invalid", "svg_common_header_title_field_required",
        "svg_common_header_candidate_invalid", "svg_common_header_font_identity_invalid",
        "svg_template_font_unsupported",
    }


def validate_header_fallback(plan, selection, document, candidate_pages):
    """One authority for fallback publication and replay; never infer success."""
    if (not isinstance(selection, dict) or selection.get("version") != "svg-common-header-fallback/v1"
            or not permits_header_fallback(plan, selection.get("reason"), document)
            or selection.get("plan_digest") != plan["plan_digest"]
            or selection.get("input_digest") != plan["input_digest"]
            or selection.get("digest") != canonical_json_digest(
                {key: value for key, value in selection.items() if key != "digest"}, allow_nan=True)
            or document.get("fallback") != selection
            or document.get("plan_digest") != plan.get("plan_digest")
            or document.get("input_digest") != plan.get("input_digest")
            or document.get("status") not in {"reduced", "published"}):
        raise ValueError("svg_header_design_fallback_conflict")
    if _unsettled_header_outcome(candidate_pages):
        raise ValueError("svg_common_header_provider_result_unknown")
    outcome = selection.get("outcome")
    if (not isinstance(outcome, dict) or set(outcome) != {"mode", "contract", "bindings"}
            or outcome.get("mode") != selection.get("mode")
            or outcome["mode"] not in plan["policy"]["fallbacks"]
            or not isinstance(outcome["bindings"], list)):
        raise ValueError("svg_header_design_fallback_conflict")
    if outcome["mode"] == "free" and (not plan["free_compatible"]
            or outcome["contract"] is not None or any(item is not None for item in outcome["bindings"])):
        raise ValueError("svg_common_header_fallback_incompatible")
    if outcome["mode"] == "reduced" and (not isinstance(outcome["contract"], dict)
            or outcome["contract"].get("version") not in {"svg-common-header/v1", "svg-common-header/v2"}):
        raise ValueError("svg_header_design_fallback_conflict")
    return outcome


def _unsettled_header_outcome(value):
    if isinstance(value, dict):
        if (value.get("inflight") is not None
                or value.get("status") in {"unknown", "started", "inflight", "in_flight"}
                or value.get("state") in {"unknown", "started", "inflight", "in_flight"}):
            return True
        return any(_unsettled_header_outcome(item) for item in value.values())
    if isinstance(value, list):
        return any(_unsettled_header_outcome(item) for item in value)
    return isinstance(value, str) and value in {
        "public_provider_result_unknown", "public_provider_timeout",
        "provider_outcome:public_provider_result_unknown", "svg_common_header_provider_result_unknown",
    }


def reduced_header_outcome(plan, request, snapshot, outline, canvas):
    """Measure exact titles on every page before any optional header spend."""
    validate_header_design_plan(plan)
    from services.svg_common_header import header_snapshot_base, prepare_common_header

    base = header_snapshot_base(snapshot)
    try:
        contract, bindings = prepare_common_header(base, outline, canvas,
            cover_exception=bool(getattr(request, "common_header_cover_exception", False)))
        design = getattr(request, "common_header_design", None)
        if design is not None and (design.document_values or design.page_values):
            from services.svg_common_header_reduced import preserve_fixed_header_values
            contract, bindings = preserve_fixed_header_values(request, outline, canvas, base, contract, plan=plan)
        return {"mode": "reduced", "contract": contract, "bindings": bindings}
    except ValueError as error:
        if str(error) not in {"svg_common_header_title_overflow", "svg_common_header_brand_overlap",
                              "svg_common_header_body_space_unresolved"}:
            raise
        if not plan["free_compatible"]:
            raise ValueError("svg_common_header_fallback_incompatible") from error
        return {"mode": "free", "contract": None, "bindings": [None] * len(outline.slides)}


def preflight_header_policy(snapshot, request, outline, canvas):
    plan = snapshot["common_header_design_plan"]
    from services.svg_common_header_design_calls import preflight_header_selector
    if plan.get("schema") == LEGACY_HEADER_DESIGN_PLAN_VERSION:
        return preflight_header_selector()
    validate_header_design_plan(plan)
    if (snapshot.get("common_header_selection") or {}).get("version") == "svg-common-header-fallback/v1":
        return None
    if snapshot.get("common_header_selection") is None:
        reduced_header_outcome(plan, request, snapshot, outline, canvas)
    return preflight_header_selector()


def preflight_admitted_header(snapshot, request, canvas):
    """Reject already-bound impossible titles before setup purchases anything."""
    plan = snapshot.get("common_header_design_plan") or {}
    if plan.get("schema") != HEADER_DESIGN_PLAN_VERSION:
        return
    from models.presentation_outline_model import PresentationOutlineModel, SlideOutlineModel
    from utils.outline_utils import explicit_outline_title
    markdown = getattr(request, "slides_markdown", None)
    if not markdown:
        return
    titles = [explicit_outline_title(content) for content in markdown]
    if any(title is None for title in titles):
        return  # Missing titles are bound by the existing setup/title budget.
    outline = PresentationOutlineModel(slides=[SlideOutlineModel(content=content, title=title,
        page_id=canonical_json_digest({"index": index, "content": content}))
        for index, (content, title) in enumerate(zip(markdown, titles, strict=True))])
    reduced_header_outcome(plan, request, snapshot, outline, canvas)


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
    expected = {"schema", "stage", "capability_version", "required_capabilities", "seed",
                "input_digest", "plan_digest", *_LIMITS}
    if isinstance(value, Mapping) and value.get("schema") == HEADER_DESIGN_PLAN_VERSION:
        expected.update({"policy", "free_compatible"})
    if (not isinstance(value, Mapping) or set(value) != expected
            or not uses_header_design(value) or value.get("stage") != "header"
            or (value.get("schema") == HEADER_DESIGN_PLAN_VERSION and value.get("policy") != _POLICY)
            or (value.get("schema") == HEADER_DESIGN_PLAN_VERSION and type(value.get("free_compatible")) is not bool)
            or value.get("capability_version") != "svg-static/v2"
            or value.get("required_capabilities") != ["structuredOutput", "vision"]
            or not isinstance(value.get("seed"), str) or not re.fullmatch(r"[a-f0-9]{32}", value["seed"])
            or any(type(value.get(key)) is not int or value[key] != limit for key, limit in _LIMITS.items())
            or any(not isinstance(value.get(key), str) or not _HASH.fullmatch(value[key])
                   for key in ("input_digest", "plan_digest"))
            or value.get("plan_digest") != canonical_json_digest(
                {key: item for key, item in value.items() if key != "plan_digest"})):
        raise ValueError("svg_common_header_design_plan_invalid")
    return deepcopy(dict(value))


def seal_new_header_design_plan(request, *, explicit_revision=False):
    """Called only for fresh admission; frozen jobs never acquire new spend."""
    if type(explicit_revision) is not bool:
        raise ValueError("svg_common_header_design_revision_invalid")
    if fresh_header_mode_conflict(request):
        raise ValueError("svg_common_header_source_preservation_conflict")
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
            "policy": deepcopy(_POLICY),
            "free_compatible": getattr(request, "common_header_design", None) is None,
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
