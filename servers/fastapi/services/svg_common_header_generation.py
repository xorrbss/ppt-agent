"""Document header preparation using the existing SVG page author/render loop."""
from __future__ import annotations

import asyncio
import base64
from copy import copy, deepcopy
import re
import struct
import xml.etree.ElementTree as ET

from services.provider_usage_instrumentation import provider_usage_scope
from services.generation_core.drivers.svg.timing import observe_setup
from services.svg_common_header import header_snapshot_base
from services.svg_common_header_design_checkpoint import (
    candidate_page_checkpoint, publish_header_design, read_update_header_design,
)
from services.svg_common_header_v2 import prepare_common_header_v2
from utils.hashing import canonical_json_digest, sha256_hex
from services.svg_common_header_design_calls import PROMPT_VERSION, build_header_call, preflight_header_selector
from services.svg_common_header_design_plan import design_input_without_asset_manifest

VERSION = "svg-common-header-design-checkpoint/v2"


def header_revision_context(*, snapshot, canvas, roles, manifests, physical_kwargs=None,
        runtime_profile=None, source_reflow=False, source_document=None, source_units=None,
        source_font_profile=None, planned=None, canonical_assets=False, source_manifest=None,
        source_pointer=None, asset_context=None, asset_revision=None, service=None, asset_offer=None,
        raster_reconstruction=False, raster_analysis=None, raster_pin=None):
    """Build existing page-loop inputs for an operation without touching its old job."""
    from services.svg_presentation_prepare import GenerationContext
    from services.generation_core.runtime.profile import resolve_svg_runtime_profile
    profile = runtime_profile or resolve_svg_runtime_profile(source_reflow=source_reflow,
        raster_reconstruction=raster_reconstruction)
    count = len(roles)
    if count != len(manifests) or not count:
        raise ValueError("svg_common_header_revision_context_invalid")
    if source_reflow and (not source_document or source_units is None or not canonical_assets):
        raise ValueError("svg_common_header_revision_source_context_missing")
    if raster_reconstruction and (raster_analysis is None or raster_pin is None or source_reflow):
        raise ValueError("svg_common_header_revision_raster_context_missing")
    if canonical_assets and any(value is None for value in
            (source_manifest, source_pointer, asset_context, asset_revision, service)):
        raise ValueError("svg_common_header_revision_asset_context_missing")
    return GenerationContext(source_reflow=source_reflow, raster_reconstruction=raster_reconstruction,
        raster_analysis=raster_analysis, raster_pin=raster_pin, source_document=source_document, source_units=source_units,
        canonical_assets=canonical_assets, canvas=canvas, physical_kwargs=physical_kwargs or {},
        snapshot=snapshot, source_font_profile=source_font_profile, count=count, roles=roles,
        manifests=manifests, planned=planned or [{} for _ in roles], source_manifest=source_manifest,
        source_pointer=source_pointer, asset_context=asset_context, asset_revision=asset_revision,
        service=service, semaphore=asyncio.Semaphore(profile.author_concurrency),
        render_semaphore=asyncio.Semaphore(profile.render_concurrency), runtime_profile=profile,
        asset_offer=asset_offer)


async def run_header_candidate_page(ctx, request, presentation_id, outline, language, owner, index,
        identity, *, async_session_maker=None, read_png=None, write_png=None):
    """Run the same durable author/native/source/render path under an operation owner."""
    from models.sql.workspace import WorkspaceOperation
    from services import svg_presentation_service as facade
    from services.generation_core.drivers.svg.collect import _PageHooks, _RunState, _page
    maker = async_session_maker or facade.async_session_maker
    async def checkpoint(active_owner, page_index, *, payload=None):
        return await candidate_page_checkpoint(active_owner, page_index, identity=identity,
            payload=payload, session_maker=maker, owned_job=facade.owned_job)
    candidate_ctx = copy(ctx)
    candidate_ctx.header_author_attempt_identity = deepcopy(identity)
    plan = candidate_ctx.snapshot.get("common_header_design_plan") or {}
    candidate_ctx.header_render_timeout_seconds = plan.get("max_render_seconds", 60)
    # This property changes receipt policy, not ownership or the original job.
    candidate_ctx.header_operation_public_only = isinstance(owner, WorkspaceOperation)
    hooks = _PageHooks(checkpoint=checkpoint, async_session_maker=maker,
        read_png=read_png or facade._read_png, write_png=write_png or facade._write_png)
    return await _page(candidate_ctx, request, presentation_id, outline, language, owner, index, _RunState(), hooks)


def _digest(value):
    return canonical_json_digest(value, allow_nan=True)


def _page_input_digest(ctx, request, outline, language, index):
    from services.svg_generation_inputs import svg_page_input_digest
    from services.svg_presentation_checkpoint import _digest as checkpoint_digest
    value = svg_page_input_digest(content=outline.slides[index].content, style=ctx.snapshot,
        index=index, count=ctx.count, instructions=request.instructions, language=language,
        preset=request.codex_generation_preset, assets=ctx.manifests[index],
        writing_policy=getattr(request, "writing_policy", None), header_page=ctx.header_pages[index])
    if ctx.source_reflow:
        value = checkpoint_digest({"page": value, "source_manifest": ctx.source_document.manifest_digest,
            "font_profile": ctx.source_font_profile,
            "source_page": ctx.source_units[index].model_dump(mode="json"), "source_layout_version": "svg-source-layout/v2"})
    if ctx.raster_reconstruction:
        value = checkpoint_digest({"page": value, "rasterApproval": ctx.raster_pin.approval_digest})
    return value


def _header_requirements(ctx, request):
    from services.svg_common_header_design_plan import _references
    from services.generation_core.authoring.author_slide_svg import svg_prompt_design
    design = getattr(request, "common_header_design", None)
    if hasattr(design, "model_dump"):
        design = design.model_dump(mode="json")
    admitted = _references(request)
    reference = getattr(request, "_svg_uploaded_design_reference", None) or ctx.snapshot.get("design_pack_template")
    return {"user_design": design, "admitted_references": admitted,
        "reference_content": svg_prompt_design(reference) if isinstance(reference, dict) else None,
        "semantics": "Design references guide visual identity only; their words, figures and claims are not source facts."}


def _header_values(ctx, request, outline):
    """Share the same source-bound actual values across authoring and selection."""
    values = getattr(ctx, "header_page_values", None)
    if values is None:
        from services.svg_common_header_values import prepare_header_page_values
        values = prepare_header_page_values(request, outline,
            source_manifest=getattr(ctx, "source_document", None))
        ctx.header_page_values = deepcopy(values)
    return values


def _design_reference_images(ctx, index):
    from services.generation_core.authoring.author_slide_svg import svg_reference_samples
    images = []
    for reference in svg_reference_samples(ctx.snapshot, ctx.roles[index], index, ctx.count):
        url = reference.get("data_url")
        if not isinstance(url, str) or not url.startswith(("data:image/png;base64,", "data:image/jpeg;base64,")):
            raise ValueError("svg_common_header_reference_image_invalid")
        images.append(base64.b64decode(url.split(",", 1)[1], validate=True))
    return images


def _snapshot(base, contract):
    result = {**base, "common_header": contract}
    result["digest"] = _digest({key: value for key, value in result.items() if key != "digest"})
    return result


HEADER_SCOPED = "header"
_HEADER_FAILURE_PREFIX = "svg_common_header_"


def header_scoped_eligible(result):
    """Whether a representative page proves its header although its body failed.

    A candidate is normally eligible only when its whole representative page
    passed. The header design, though, only owns the header: once a candidate
    is chosen its author's body is promoted as evidence and the regular page
    loop measures it again, repairs it within the page's own budget, and
    rescues it (keep-and-flag or complete-content fallback) like any other page.
    So a page whose header measured clean -- no ``svg_common_header_*``
    failure, a passed header measurement, a valid last drawing, and the
    author's body kept for promotion -- is still a fair test of that header.
    A failure placeholder shows no body to judge harmony by.
    """

    quality = result.get("quality") if isinstance(result, dict) else None
    if not isinstance(quality, dict) or not isinstance(quality.get("failures", []), list):
        return False
    if not isinstance(result.get("author_body_svg"), str) or not result["author_body_svg"]:
        return False
    if result.get("inflight"):
        return False
    attempts = result.get("attempts") or [{}]
    if not isinstance(attempts[-1], dict) or (attempts[-1].get("receipt") or {}).get("validation_error"):
        return False
    from services.generation_core.drivers.svg.terminal import PLACEHOLDER_OUTCOME
    terminal = result.get("terminal")
    if terminal and (not isinstance(terminal, dict) or terminal.get("remakeOutcome") == PLACEHOLDER_OUTCOME):
        return False
    header = quality.get("common_header")
    if not isinstance(header, dict) or header.get("passed") is not True:
        return False
    return all(isinstance(failure, dict) and isinstance(failure.get("kind"), str)
               and not failure["kind"].startswith(_HEADER_FAILURE_PREFIX)
               for failure in quality.get("failures", []))


def admit_header_scoped_candidates(pages):
    """Admit header-clean candidates only when no candidate passed outright.

    A fully passing representative page stays the only admission when one
    exists, so a deck that worked before chooses exactly as before.
    """

    if any(page.get("eligible") for page in pages.values()):
        return
    for page in pages.values():
        if page.get("header_eligible") is True:
            page["eligible"] = True
            page["eligibility"] = HEADER_SCOPED


async def _settle_assets(ctx):
    """Wait for the deck's asset offer when it was started beside the header design."""
    settle = getattr(ctx, "settle_assets", None)
    if settle is not None:
        await settle()


def _representative_index(ctx, outline, cover_exception, stage_plan=None, plan=None):
    eligible = list(range(1 if cover_exception else 0, ctx.count))
    if stage_plan is not None:
        eligible = [index for index in eligible if index in stage_plan["partitions"][0]]
    if not eligible:
        raise ValueError("svg_common_header_representative_missing")
    def offered(index):
        # A decoupled design runs before the asset offer settles, so only the
        # source images planned before it can rank a page.
        if plan is not None and design_input_without_asset_manifest(plan):
            counts = getattr(ctx, "source_asset_counts", None)
            return counts[index] if counts is not None else len(ctx.planned[index])
        return len(ctx.manifests[index])
    def risk(index):
        if ctx.source_reflow:
            page = ctx.source_units[index]
            objects = sum(block.kind in {"table", "chart", "image"} for block in page.blocks)
            return (objects, len(page.model_dump_json()), -index)
        source = outline.slides[index].content
        from services.generation_core.drivers.svg.native_requirements import (
            requested_native_data, requirements_revision)
        native = requested_native_data(source, requirements_revision(ctx.snapshot))
        return (len(native or []), offered(index), len(source), -index)
    return max(eligible, key=risk)


def _design_input(ctx, request, outline, plan, index, previous=None):
    from services.generation_core.authoring.author_slide_svg import SVG_AUTHOR_RULES, svg_prompt_design
    pages = [{"page_id": slide.page_id, "page_index": i, "title": slide.title,
              "source_digest": _digest(slide.content)} for i, slide in enumerate(outline.slides)]
    page_values = _header_values(ctx, request, outline)
    representative = {"page_id": outline.slides[index].page_id, "source_ref": pages[index]["source_digest"]}
    if not design_input_without_asset_manifest(plan):
        representative["required_assets"] = {
            key: {name: item for name, item in value.items() if name not in {"data_url", "dataUrl", "vector"}}
            for key, value in ctx.manifests[index].items()}
    representative["source"] = outline.slides[index].content
    value = {"version": VERSION, "plan_digest": plan["plan_digest"], "seed": plan["seed"],
        "purpose": request.content, "instructions": request.instructions,
        "language": getattr(request, "language", None), "writing_policy": getattr(request, "writing_policy", None),
        "header_requirements": _header_requirements(ctx, request),
        "canvas": {"width": ctx.canvas.width_px, "height": ctx.canvas.height_px,
                   "physical": (getattr(request, "_document_canvas", None).model_dump(mode="json")
                     if hasattr(getattr(request, "_document_canvas", None), "model_dump")
                     else getattr(request, "_document_canvas", None))},
        "style": svg_prompt_design(header_snapshot_base(ctx.snapshot)), "pages": pages,
        "available_value_refs": sorted({key for page in page_values for key in page["values"]}),
        "available_values": deepcopy(page_values), "page_values_digest": _digest(page_values),
        "representative": representative,
        "capability": {"version": plan["capability_version"], "svg_rules": SVG_AUTHOR_RULES},
        "limits": {key: plan[key] for key in ("max_svg_bytes", "max_svg_nodes", "max_render_seconds")}}
    if previous is not None:
        value["known_defects"] = previous
    return value


async def _paid_step(document, *, key, stage, ordinal, payload, images, plan, job, save):
    input_digest = _digest(payload)
    existing = document.setdefault("steps", {}).get(key)
    if existing is not None:
        if existing.get("input_digest") != input_digest:
            raise ValueError("svg_common_header_checkpoint_input_conflict")
        if existing.get("status") != "succeeded":
            raise ValueError("svg_common_header_provider_result_unknown")
        return deepcopy(existing["response"])
    if len(document["steps"]) >= plan["header_call_limit"]:
        raise ValueError("svg_common_header_call_limit_exceeded")
    from utils.provider_overload import safe_provider_diagnostics
    loop = asyncio.get_running_loop()

    async def save_terminal(event):
        raw = event.get("raw_response")
        if not isinstance(raw, str) or event.get("output_digest") != sha256_hex(raw.encode("utf-8")):
            raise ValueError("svg_common_header_terminal_evidence_invalid")
        diagnostics = safe_provider_diagnostics(diagnostics={
            **(event.get("diagnostics") or {}), "usage": event.get("usage"),
            "responseId": event.get("response_id"), "terminalEvent": "response.completed"})
        evidence = {"terminal_event": "response.completed", "raw_response": raw,
            "output_digest": event["output_digest"], "diagnostics": diagnostics}
        if "responseId" in diagnostics:
            evidence["response_id"] = diagnostics["responseId"]
        if "usage" in diagnostics:
            evidence["usage"] = diagnostics["usage"]
        step = document["steps"][key]
        if step.get("terminal_evidence", evidence) != evidence:
            raise ValueError("svg_common_header_terminal_evidence_conflict")
        step["terminal_evidence"] = evidence
        # The raw provider completion is evidence, not permission to publish or
        # replay this step. Only the normal execute return can mark success.
        await save()

    def on_terminal(event):
        if event.get("terminal_event") == "response.completed":
            asyncio.run_coroutine_threadsafe(save_terminal(deepcopy(event)), loop).result()

    execute = build_header_call(payload, plan=plan, stage=stage, ordinal=ordinal,
        images=images, on_terminal=on_terminal)
    projection = getattr(execute, "input_projection", None)
    if projection is not None and projection.get("source_context_digest") != input_digest:
        raise ValueError("svg_common_header_checkpoint_input_conflict")
    document["steps"][key] = {"status": "started", "input_digest": input_digest,
        "stage": stage, "ordinal": ordinal, "semantic_slot": f"svg-header-{stage}-{ordinal}"}
    if projection is not None:
        document["steps"][key]["input_projection"] = deepcopy(projection)
        document["steps"][key]["input_envelope"] = deepcopy(execute.input_envelope)
    await save()
    try:
        from models.sql.workspace import WorkspaceOperation
        with provider_usage_scope(job_id=job.id, operation="authored_generation", stage="header",
                attempt_number=job.attempt_number,
                public_only=isinstance(job, WorkspaceOperation) or bool(getattr(job, "public_only", False))):
            response = await execute()
    except BaseException as error:
        document["steps"][key]["status"] = "unknown"
        document["steps"][key]["failure_diagnostics"] = safe_provider_diagnostics(error)
        await save()
        raise
    document["steps"][key].update(status="succeeded", response=response)
    await save()
    return response


async def _stage_paid_step(document, *, key, plan, **kwargs):
    """``_paid_step`` whose provider failure ends the optional stage, when the plan says so.

    The step keeps the unsettled state ``_paid_step`` recorded (it is never
    bought again); the caller then takes the reduced or free header. An
    integrity conflict is not a provider failure and still fails the deck.
    """
    from services.svg_common_header_design_plan import PROVIDER_STEP_FAILED, reduces_optional_stage_failures
    try:
        return await _paid_step(document, key=key, plan=plan, **kwargs)
    except Exception as error:
        step = document.get("steps", {}).get(key)
        if (reduces_optional_stage_failures(plan) and isinstance(step, dict)
                and step.get("status") in {"started", "unknown"}
                and str(error) != "svg_common_header_checkpoint_input_conflict"):
            raise ValueError(PROVIDER_STEP_FAILED) from error
        raise


def _failure_cause(error):
    """Closed-vocabulary evidence of why the optional stage failed."""
    from utils.provider_overload import safe_provider_diagnostics
    cause = error.__cause__
    if cause is None:
        return None
    evidence = safe_provider_diagnostics(cause)
    code = str(cause) if isinstance(cause, ValueError) else ""
    if len(code) <= 128 and re.fullmatch(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)+", code):
        evidence["code"] = code
    return evidence


def _checked_candidates(response, plan, revision):
    result = response.get("result")
    if not isinstance(result, dict) or set(result) != {"candidates"}:
        raise ValueError("svg_common_header_candidate_response_invalid")
    candidates = result["candidates"]
    if not isinstance(candidates, list) or len(candidates) != 2:
        raise ValueError("svg_common_header_candidate_response_invalid")
    seen = set()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise ValueError("svg_common_header_candidate_response_invalid")
        candidate_id = candidate.get("candidate_id")
        if (not isinstance(candidate_id, str) or not candidate_id or len(candidate_id) > 128
                or candidate_id in seen or type(candidate.get("revision")) is not int
                or candidate.get("revision") != revision):
            raise ValueError("svg_common_header_candidate_identity_invalid")
        seen.add(candidate_id)
        svg = candidate.get("svg")
        if not isinstance(svg, str) or len(svg.encode()) > plan["max_svg_bytes"] or "<!" in svg or "<?" in svg:
            raise ValueError("svg_common_header_candidate_capacity_exceeded")
        if sum(1 for _ in ET.fromstring(svg).iter()) > plan["max_svg_nodes"]:
            raise ValueError("svg_common_header_candidate_capacity_exceeded")
        if not isinstance(candidate.get("fields"), list) or any(not isinstance(field, dict) for field in candidate["fields"]):
            raise ValueError("svg_common_header_candidate_response_invalid")
    return candidates


def _structural_similarity(candidates, mapping):
    """Report repetition without replacing the selector's context-aware judgment."""
    structures, svg_bytes = {}, {}
    for candidate in candidates:
        svg = candidate["svg"]
        geometry = [{"tag": node.tag, "attributes": {key: value for key, value in node.attrib.items()
            if key not in {"id", "fill", "stroke", "opacity", "fill-opacity", "stroke-opacity"}}}
            for node in ET.fromstring(svg).iter()]
        fields = [{key: value for key, value in field.items()
            if key not in {"node_id", "dependent_node_ids"}} for field in candidate.get("fields", [])]
        structure = _digest({"geometry": geometry, "fields": fields, "body_box": candidate.get("body_box")})
        structures[candidate["candidate_id"]] = structure
        svg_bytes[candidate["candidate_id"]] = sha256_hex(svg.encode())
    display_ids = list(mapping)
    first, second = (mapping[display_id] for display_id in display_ids)
    return {"version": "svg-common-header-structural-similarity/v1",
        "display_ids": display_ids, "byte_identical_svg": svg_bytes[first] == svg_bytes[second],
        "same_geometry_fields_and_body_box": structures[first] == structures[second],
        "policy": "Diagnostic only. Distinct composition is the default request; user-specified or brand-required repetition is allowed. Evaluate suitability and meaningful differences from the completed screens and supplied instructions; this signal does not reject or select a candidate."}


def _selection_input(ctx, request, outline, plan, document, candidates, pages, index, read_png):
    mapping = document.get("display_mapping")
    ids = [candidate["candidate_id"] for candidate in candidates]
    if mapping is None:
        ordered = sorted(ids, key=lambda candidate_id: _digest([plan["seed"], candidate_id]))
        mapping = {f"choice-{position + 1}": candidate_id for position, candidate_id in enumerate(ordered)}
        document["display_mapping"] = mapping
    if set(mapping.values()) != set(ids):
        raise ValueError("svg_common_header_display_mapping_conflict")
    document["structural_similarity"] = _structural_similarity(candidates, mapping)
    images, records = [], []
    by_id = {candidate["candidate_id"]: candidate for candidate in candidates}
    for display_id, candidate_id in mapping.items():
        candidate, page = by_id[candidate_id], pages[candidate_id]
        payload = page.get("payload") or {}
        quality = payload.get("quality") or {}
        eligible = page.get("eligible") is True
        png = read_png(payload) if eligible else None
        if page.get("eligibility") == HEADER_SCOPED:
            failures = quality.get("failures") or []
            required_checks = {"passed": True, "scope": HEADER_SCOPED, "failures": [], "error": None}
            body_checks = {"passed": False, "failures": failures,
                           "page_outcome": (payload.get("terminal") or {}).get("remakeOutcome"),
                           "handling": "page_rescue_after_selection"}
        else:
            required_checks = {"passed": quality.get("passed") is True,
                "failures": quality.get("failures", []), "error": page.get("error")}
            body_checks = None
        record = {"display_id": display_id, "candidate_revision": candidate["revision"],
            "eligible": eligible, "required_checks": required_checks,
            **({"body_checks": body_checks} if body_checks is not None else {}),
            "svg_digest": sha256_hex((payload.get("svg_content") or "").encode()),
            "body_input_digest": payload.get("input_digest"),
            "source_preservation": {"digest": _digest(payload.get("source_preservation")),
                "verified": (payload.get("source_preservation") or {}).get("rendering_verified")},
            "native_bindings_digest": _digest(payload.get("nativeDeclarations")),
            "header_digest": page.get("header_digest")}
        if eligible:
            if not png or len(png) < 24 or not png.startswith(b"\x89PNG\r\n\x1a\n") or png[12:16] != b"IHDR":
                raise ValueError("svg_common_header_selection_render_missing")
            width, height = struct.unpack(">II", png[16:24])
            if (width * height > plan["selection_image_max_pixels"]
                    or len(png) > plan["selection_image_max_bytes"]
                    or width * ctx.canvas.height_px != height * ctx.canvas.width_px):
                raise ValueError("svg_common_header_selection_image_capacity_exceeded")
            previous_sizes = [(item["width"], item["height"]) for item in records if "width" in item]
            if previous_sizes and (width, height) != previous_sizes[0]:
                raise ValueError("svg_common_header_selection_scale_mismatch")
            record.update(image_index=len(images), png_digest=sha256_hex(png), width=width, height=height)
            images.append(png)
        records.append(record)
    if len(images) > plan["selection_image_max_count"]:
        raise ValueError("svg_common_header_selection_image_capacity_exceeded")
    source_context = (ctx.source_units[index].model_dump(mode="json") if ctx.source_reflow
                      else outline.slides[index].content)
    from services.svg_renderer import SVG_RENDERER_VERSION
    from services.svg_common_header_v2 import MEASUREMENT_VERSION
    from services.generation_core.authoring.author_slide_svg import svg_prompt_design
    page_values = _header_values(ctx, request, outline)
    value = {"prompt_version": PROMPT_VERSION, "plan_digest": plan["plan_digest"],
        "selector_capability": document["selector_capability"], "policy": plan,
        "structural_similarity": document["structural_similarity"],
        "renderer_version": SVG_RENDERER_VERSION, "validator_version": MEASUREMENT_VERSION,
        "header_requirements": _header_requirements(ctx, request),
        "purpose": request.content, "instructions": request.instructions,
        "available_values": deepcopy(page_values), "page_values_digest": _digest(page_values),
        "language": getattr(request, "language", None), "writing_policy": getattr(request, "writing_policy", None),
        "source_context": source_context, "style": svg_prompt_design(header_snapshot_base(ctx.snapshot)),
        "representative_page_id": outline.slides[index].page_id, "candidates": records}
    value["selection_input_digest"] = _digest(value)
    return value, images


def _validated_selection(response, selection_input, mapping, pages, candidates):
    result = response.get("result")
    if (not isinstance(result, dict) or result.get("selection_input_digest") != selection_input["selection_input_digest"]
            or not isinstance(result.get("candidate_selection"), dict)
            or not isinstance(result.get("representative_visual_review"), dict)):
        raise ValueError("svg_common_header_selection_response_invalid")
    choice, review = result["candidate_selection"], result["representative_visual_review"]
    if (set(result) != {"selection_input_digest", "candidate_selection", "representative_visual_review"}
            or set(choice) != {"selected_id", "candidate_revision", "reason"}
            or set(review) != {"candidate_id", "passed", "harmony", "known_defects"}
            or not isinstance(choice.get("reason"), str) or not choice["reason"].strip()
            or not isinstance(review.get("harmony"), str) or not review["harmony"].strip()
            or type(review.get("passed")) is not bool
            or not isinstance(review.get("known_defects"), list)
            or any(not isinstance(item, str) for item in review["known_defects"])):
        raise ValueError("svg_common_header_selection_response_invalid")
    display_id = choice.get("selected_id")
    if display_id is None:
        if review.get("candidate_id") is not None or choice.get("candidate_revision") is not None:
            raise ValueError("svg_common_header_selection_response_invalid")
        return None, result
    candidate_id = mapping.get(display_id)
    by_id = {candidate["candidate_id"]: candidate for candidate in candidates}
    if (candidate_id not in by_id or not pages[candidate_id].get("eligible")
            or type(choice.get("candidate_revision")) is not int
            or choice.get("candidate_revision") != by_id[candidate_id]["revision"]
            or review.get("candidate_id") != display_id or type(review.get("passed")) is not bool
            or not isinstance(review.get("known_defects"), list)):
        raise ValueError("svg_common_header_selection_response_invalid")
    if review["passed"] is not True:
        raise ValueError("svg_common_header_representative_visual_review_failed")
    return candidate_id, result


async def prepare_document_header(ctx, request, presentation_id, outline, language, job, **hooks):
    from services.svg_common_header_design_plan import (
        HEADER_DESIGN_PLAN_VERSION, OPTIONAL_STAGE_FAILURES, permits_header_fallback, reduced_header_outcome,
        require_header_design_plan,
    )
    plan = ctx.snapshot["common_header_design_plan"]
    if plan.get("schema") != HEADER_DESIGN_PLAN_VERSION:
        return await _prepare_designed_document_header(
            ctx, request, presentation_id, outline, language, job, **hooks)
    if require_header_design_plan(request) != plan:
        raise ValueError("svg_common_header_design_plan_conflict")
    from services import svg_presentation_service as facade
    storage = {"session_maker": hooks["async_session_maker"], "owned_job": facade.owned_job}
    document = await read_update_header_design(job, **storage)
    if document.get("fallback") is not None:
        return await _publish_reduced_header(ctx, request, outline, job, document, storage)
    # Prove the alternative on every page before starting a design or body call.
    reduced_header_outcome(plan, request, ctx.snapshot, outline, ctx.canvas)
    try:
        return await _prepare_designed_document_header(
            ctx, request, presentation_id, outline, language, job, **hooks)
    except ValueError as error:
        document = await read_update_header_design(job, **storage)
        if not permits_header_fallback(plan, str(error), document):
            raise
        outcome = reduced_header_outcome(plan, request, ctx.snapshot, outline, ctx.canvas)
        selection = {"version": "svg-common-header-fallback/v1", "plan_digest": plan["plan_digest"],
            "input_digest": plan["input_digest"], "reason": str(error), "mode": outcome["mode"],
            "notice": "svg_common_header_reduced" if outcome["mode"] == "reduced" else "svg_common_header_free",
            "outcome": outcome}
        selection["digest"] = _digest(selection)
        if str(error) in OPTIONAL_STAGE_FAILURES:
            cause = _failure_cause(error)
            if cause is not None:
                document["fallback_cause"] = cause
        document.update(fallback=selection, status="reduced", plan_digest=plan["plan_digest"],
                        input_digest=plan["input_digest"], version=VERSION)
        await read_update_header_design(job, payload=document, **storage)
        return await _publish_reduced_header(ctx, request, outline, job, document, storage)


async def _publish_reduced_header(ctx, request, outline, job, document, storage):
    from services.svg_common_header_design_checkpoint import publish_header_fallback
    selection = document["fallback"]
    proposed = header_snapshot_base(ctx.snapshot)
    proposed = {**proposed, "common_header_selection": selection}
    outcome = selection["outcome"]
    if outcome["contract"] is not None:
        proposed["common_header"] = outcome["contract"]
    proposed["digest"] = _digest({key: value for key, value in proposed.items() if key != "digest"})
    prepared = outline.model_copy(deep=True)
    for slide, binding in zip(prepared.slides, outcome["bindings"], strict=True):
        slide.common_header = deepcopy(binding)
    await publish_header_fallback(job, snapshot=proposed, outline=prepared, **storage)
    request._svg_style_snapshot = proposed
    outline.slides = prepared.slides
    ctx.snapshot = proposed
    ctx.header_pages = outcome["bindings"] if outcome["contract"] is not None else None
    return ctx


async def _prepare_designed_document_header(ctx, request, presentation_id, outline, language, job, *,
        checkpoint, async_session_maker, read_png, write_png, page_runner, page_hooks_factory, state_factory):
    """Two isolated real page attempts, one independent screen decision, exact body reuse."""
    plan = ctx.snapshot["common_header_design_plan"]
    selector_capability = preflight_header_selector()
    if job is None or getattr(request, "source_mode", None) == "source-preserve":
        raise ValueError("svg_common_header_design_scope_unsupported")
    if ctx.raster_reconstruction and any(page.render_mode != "redesign" for page in ctx.raster_analysis.pages):
        raise ValueError("svg_common_header_preserved_raster_unsupported")
    if ctx.source_reflow and ctx.snapshot.get("source_authoring_contract") != {"version": "svg-source-whole-page/v1"}:
        raise ValueError("svg_common_header_source_authoring_contract_unsupported")
    manifest = getattr(ctx, "partial_recovery_manifest", None)
    if isinstance(manifest, dict) and manifest.get("append_only_recovery") is True:
        # The producer envelope keeps its own identity. A consumer of already
        # published frozen bindings must not relabel or rerun its header calls.
        from models.sql.workspace import WorkspaceOperation
        from services.svg_partial_recovery import verify_episode, verified_header_producer
        from services.svg_common_header_design_checkpoint import _envelope
        async with async_session_maker() as source_session:
            operation = await source_session.get(WorkspaceOperation, job.workspace_operation_id)
            if operation is None:
                raise ValueError("svg_common_header_publication_conflict")
            verified = await verify_episode(source_session, operation, job)
            producer = await verified_header_producer(source_session,operation,job)
            if (verified != manifest or producer is None
                    or producer.svg_style_snapshot != ctx.snapshot
                    or producer.authored_outline != outline.model_dump(mode="json")):
                raise ValueError("svg_common_header_publication_conflict")
            envelope = _envelope(producer, producer.svg_header_design_checkpoint)
            source = envelope["document"]
            selection = ctx.snapshot.get("common_header_selection")
            if (source.get("status") != "published" or not isinstance(selection, dict)
                    or source.get("selection") != selection
                    or source.get("selection_input_digest") != selection.get("selection_input_digest")
                    or envelope.get("published_inputs") != {
                        "snapshot_digest": ctx.snapshot.get("digest"),
                        "outline_digest": _digest(producer.authored_outline)}):
                raise ValueError("svg_common_header_publication_conflict")
        return ctx
    from services import svg_presentation_service as facade
    storage = {"session_maker": async_session_maker, "owned_job": facade.owned_job}
    document = await read_update_header_design(job, **storage)
    if not document:
        document = {"version": VERSION, "plan_digest": plan["plan_digest"], "input_digest": plan["input_digest"],
            "steps": {}, "candidate_pages": {}, "status": "preparing", "selector_capability": selector_capability}
    if document.get("version") != VERSION or document.get("plan_digest") != plan["plan_digest"]:
        raise ValueError("svg_common_header_checkpoint_input_conflict")
    if document.get("selector_capability") != selector_capability:
        raise ValueError("svg_common_header_selector_model_changed")
    async def save():
        await read_update_header_design(job, payload=deepcopy(document), **storage)
    if document.get("status") == "published":
        if (ctx.snapshot.get("common_header_selection") or {}).get("selection_input_digest") != document.get("selection_input_digest"):
            raise ValueError("svg_common_header_publication_conflict")
        return ctx
    page_values = _header_values(ctx, request, outline)
    if document.get("header_page_values_digest") not in {None, _digest(page_values)}:
        raise ValueError("svg_common_header_page_values_conflict")
    document["header_page_values"] = deepcopy(page_values)
    document["header_page_values_digest"] = _digest(page_values)
    cover_exception = bool(getattr(request, "common_header_cover_exception", False))
    index = document.setdefault("representative_index", _representative_index(ctx, outline, cover_exception,
        getattr(request, "_svg_stage_plan", None), plan))
    if document.get("representative_page_id", outline.slides[index].page_id) != outline.slides[index].page_id:
        raise ValueError("svg_common_header_representative_identity_conflict")
    document["representative_page_id"] = outline.slides[index].page_id
    base = header_snapshot_base(ctx.snapshot)
    await save()
    candidate_contexts, failures = {}, []
    candidates = None
    for revision in (1, 2):
        design_input = _design_input(ctx, request, outline, plan, index, failures or None)
        design_input["candidate_revision"] = revision
        reference_images = _design_reference_images(ctx, index)
        design_input["reference_images"] = [{"image_index": i, "digest": sha256_hex(png)} for i, png in enumerate(reference_images)]
        with observe_setup(request, "header_design"):
            response = await _stage_paid_step(document, key=f"design-{revision}", stage="design", ordinal=revision,
                payload=design_input, images=reference_images, plan=plan, job=job, save=save)
        # The representative pages author with the asset offer; every candidate
        # context below is a copy of the settled one.
        await _settle_assets(ctx)
        try:
            candidates = _checked_candidates(response, plan, revision)
            failures = []
            for candidate in candidates:
                try:
                    contract, bindings = prepare_common_header_v2(candidate, outline, ctx.canvas,
                        snapshot=base, cover_exception=cover_exception, page_values=page_values)
                    candidate_ctx = copy(ctx)
                    candidate_ctx.snapshot = _snapshot(base, contract)
                    candidate_ctx.header_pages = bindings
                    candidate_ctx.header_render_timeout_seconds = plan["max_render_seconds"]
                    candidate_ctx.header_author_attempt_identity = {"candidate_revision": str(revision),
                        "candidate_id": candidate["candidate_id"], "page_id": outline.slides[index].page_id}
                    candidate_contexts[candidate["candidate_id"]] = candidate_ctx
                except (ValueError, ET.ParseError) as error:
                    from services.svg_common_header_design_plan import known_header_design_rejection
                    if not known_header_design_rejection(plan, error):
                        raise
                    failures.append({"candidate_id": candidate["candidate_id"], "error": str(error)})
            if candidate_contexts:
                break
        except (ValueError, ET.ParseError) as error:
            from services.svg_common_header_design_plan import known_header_design_rejection
            if not known_header_design_rejection(plan, error):
                raise
            failures = [{"error": str(error)}]
        if revision == 2:
            raise ValueError("svg_common_header_candidates_ineligible")
    pages = {}
    async def representative(candidate):
        candidate_id = candidate["candidate_id"]
        if candidate_id not in candidate_contexts:
            return candidate_id, {"eligible": False, "error": next((item["error"] for item in failures
                if item.get("candidate_id") == candidate_id), "svg_common_header_candidate_invalid")}
        candidate_ctx = candidate_contexts[candidate_id]
        identity = candidate_ctx.header_author_attempt_identity
        async def candidate_checkpoint(active_job, page_index, *, payload=None):
            return await candidate_page_checkpoint(active_job, page_index, identity=identity,
                payload=payload, **storage)
        hooks = page_hooks_factory(checkpoint=candidate_checkpoint, async_session_maker=async_session_maker,
                                   read_png=read_png, write_png=write_png)
        try:
            result, png = await page_runner(candidate_ctx, request, presentation_id, outline, language,
                job, index, state_factory(), hooks)
            return candidate_id, {"eligible": (result.get("quality", {}).get("passed") is True
                    and not result.get("terminal") and not result.get("flag")
                    and not (result.get("attempts", [{}])[-1].get("receipt") or {}).get("validation_error")),
                "header_eligible": header_scoped_eligible(result),
                "payload": result, "header_digest": candidate_ctx.snapshot["common_header"]["digest"]}
        except Exception as error:
            from services.svg_common_header_design_plan import (
                HEADER_DESIGN_PLAN_VERSION, REPRESENTATIVE_PAGE_FAILED, reduces_optional_stage_failures,
            )
            if reduces_optional_stage_failures(plan):
                # The page's own checkpoint keeps whatever it recorded; the
                # reduced header never publishes or reruns this candidate.
                raise ValueError(REPRESENTATIVE_PAGE_FAILED) from error
            if plan.get("schema") == HEADER_DESIGN_PLAN_VERSION:
                raise
            saved = await candidate_checkpoint(job, index) or {}
            if saved.get("inflight") or "unknown" in str(getattr(error, "code", error)).lower() or "uncertain" in str(error).lower():
                raise
            if not saved.get("attempts"):
                raise
            return candidate_id, {"eligible": False, "payload": saved,
                "error": str(getattr(error, "code", type(error).__name__)),
                "header_digest": candidate_ctx.snapshot["common_header"]["digest"]}
    # Both use the same already-admitted runtime semaphore; no new concurrency policy.
    with observe_setup(request, "header_representative"):
        outcomes = await asyncio.gather(
            *(representative(candidate) for candidate in candidates), return_exceptions=True)
    for outcome in outcomes:
        if isinstance(outcome, BaseException):
            raise outcome
        pages[outcome[0]] = outcome[1]
    admit_header_scoped_candidates(pages)
    if not any(page.get("eligible") for page in pages.values()):
        raise ValueError("svg_common_header_representative_pages_ineligible")
    selection_input, images = _selection_input(ctx, request, outline, plan, document, candidates, pages, index, read_png)
    await save()
    for ordinal in (1, 2):
        with observe_setup(request, "header_selection"):
            selection_response = await _stage_paid_step(
                document, key=f"selection-{ordinal}", stage="selection", ordinal=ordinal,
                payload=selection_input, images=images, plan=plan, job=job, save=save)
        try:
            candidate_id, selection = _validated_selection(selection_response, selection_input,
                document["display_mapping"], pages, candidates)
            if candidate_id is None:
                raise ValueError("svg_common_header_all_candidates_unsuitable")
            break
        except ValueError as error:
            if ordinal == 2:
                raise
            selection_input = {**selection_input, "known_selection_defect": str(error)}
            selection_input["selection_input_digest"] = _digest({key: value for key, value in selection_input.items()
                if key != "selection_input_digest"})
    selected_ctx = candidate_contexts[candidate_id]
    candidate_payload = deepcopy(pages[candidate_id]["payload"])
    original_input_digest = _page_input_digest(selected_ctx, request, outline, language, index)
    if candidate_payload.get("input_digest") != original_input_digest:
        raise ValueError("svg_common_header_selected_body_input_conflict")
    candidate_payload["common_header_candidate_input_digest"] = original_input_digest
    candidate_payload["common_header_candidate_style_digest"] = selected_ctx.snapshot["digest"]
    selected_outline = outline.model_copy(deep=True)
    for slide, binding in zip(selected_outline.slides, selected_ctx.header_pages, strict=True):
        slide.common_header = binding
    metadata = {"version": "svg-common-header-selection/v2", "plan_digest": plan["plan_digest"],
        "selection_input_digest": selection_input["selection_input_digest"],
        "page_values_digest": _digest(page_values),
        "selected_candidate_id": candidate_id, "selected_revision": selected_ctx.snapshot["common_header"]["revision"],
        "representative_page_id": outline.slides[index].page_id, "display_mapping": document["display_mapping"],
        "candidate_selection": selection["candidate_selection"],
        "representative_visual_review": selection["representative_visual_review"],
        "provider_step": f"selection-{ordinal}"}
    selected_ctx.snapshot["common_header_selection"] = metadata
    selected_ctx.snapshot["digest"] = _digest({key: value for key, value in selected_ctx.snapshot.items() if key != "digest"})
    # Selection metadata is separately bound, not a body-authoring input. Keep
    # the exact candidate style for its author receipt and record final linkage.
    document.update(status="selected", selection_input_digest=selection_input["selection_input_digest"],
        selected_candidate_id=candidate_id, selection=metadata)
    await save()
    await publish_header_design(job, snapshot=selected_ctx.snapshot, outline=selected_outline, **storage)
    request._svg_style_snapshot = selected_ctx.snapshot
    outline.slides = selected_outline.slides
    body_svg = candidate_payload.get("author_body_svg")
    if not body_svg:
        raise ValueError("svg_common_header_selected_body_missing")
    # Promote author evidence only. Recreate final composition, measures and
    # native/publication evidence through the regular page loop without spend.
    for key in ("quality", "png_path", "png_sha256", "svgAssetManifest", "system_furniture", "terminal", "flag",
                "terminal_error"):
        candidate_payload.pop(key, None)
    candidate_payload["svg_content"] = body_svg
    composition = (candidate_payload.get("source_preservation") or {}).get("common_header_composition")
    if composition:
        candidate_payload["source_preservation"] = deepcopy(composition["body_receipt"])
    candidate_payload["common_header_candidate_lineage"] = deepcopy(selected_ctx.header_author_attempt_identity)
    candidate_payload["input_digest"] = _page_input_digest(selected_ctx, request, outline, language, index)
    current = await checkpoint(job, index) or {}
    if current.get("attempts") or current.get("svg_content") or current.get("inflight"):
        if current.get("common_header_candidate_lineage") != candidate_payload["common_header_candidate_lineage"]:
            raise ValueError("svg_common_header_page_promotion_conflict")
    else:
        await checkpoint(job, index, payload={**current, **candidate_payload})
    selected_ctx.header_author_attempt_identity = None
    document["status"] = "published"
    await save()
    return selected_ctx
