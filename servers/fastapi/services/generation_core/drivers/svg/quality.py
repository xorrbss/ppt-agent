"""The SVG driver's deck quality pass: per-page classification and advisory.

Runs once every page has an outcome and before publication. Each page is
reported as a :class:`PageOutcome`, classified by the shared rule
(``generation_core/quality/page_outcome.py``), and the classification is
written back into that page's checkpoint quality, so a resumed attempt and
the published receipt read the same verdict. The deck then gets one
contact-sheet vision advisory over the page renders when the runtime profile
asks for it. Frozen requests retain that optional behaviour; the fresh quality
policy requires complete, source-bound design evidence before publication.
"""
from __future__ import annotations
from copy import deepcopy

import base64
import asyncio
import struct
from collections.abc import Mapping

from services.generation_core.quality.advisory_record import advisory_record, content_digest, record_matches
from services.generation_core.quality.vision_advisory_prompt import (
    DESIGN_AXES, DESIGN_REVIEW_PROMPT_VERSION, DESIGN_PRINCIPLES_VERSION,
    baseline_review_active, baseline_design_principles_digest,
)
from services.generation_core.quality.page_outcome import (
    PageOutcome, classify_page_outcomes, deck_quality_summary,
)
from utils.hashing import sha256_hex
from services.generation_core.drivers.svg.composition_observation import observe_svg_deck_composition


def page_outcome(index: int, payload: dict) -> PageOutcome:
    quality = payload.get("quality") or {}
    return PageOutcome(
        index=index, source_format="svg", source=payload.get("svg_content") or "",
        png_path=payload.get("png_path"), quality=quality,
        attempts=sum(a.get("kind") != "local" for a in payload.get("attempts", [])),
        failures=tuple(item for item in quality.get("failures", []) if isinstance(item, dict)),
        verified=quality.get("verified") is True, flag=payload.get("terminal"))


def _quality_policy(snapshot):
    from services.svg_authoring_profile import read_quality_recovery_policy
    return read_quality_recovery_policy(snapshot or {})


async def classify_pages(pages, *, job, checkpoint, snapshot=None) -> dict[str, object]:
    """Classify every page in place and persist it; return the deck summary."""

    outcomes = classify_page_outcomes([page_outcome(i, payload) for i, (payload, _) in enumerate(pages)])
    composition = observe_svg_deck_composition(pages, **({"quality_policy": True} if snapshot and _quality_policy(snapshot) else {}))
    for outcome, (payload, _) in zip(outcomes, pages, strict=True):
        payload["quality"] = {**payload["quality"], "classification": outcome.classification,
                              "verified": outcome.verified,
                              "compositionObservation": composition["pages"][outcome.index]}
        if outcome.flag is not None:
            payload["flag"] = dict(outcome.flag)
        await checkpoint(job, outcome.index, payload=payload)
    return {**deck_quality_summary(outcomes), "compositionObservation": composition}


async def deck_advisory(ctx, pages, *, job, read_png, run_advisory, round_ordinal: int | None = None, final_selected=False, source_evidence=None):
    """One vision advisory over the deck's renders, bound to their digests."""

    profile = getattr(ctx, "runtime_profile", None)
    policy = _quality_policy(getattr(ctx, "snapshot", {}))
    if any(payload.get("cached_author_render") for payload, _ in pages):
        if not all(payload.get("cached_author_render") for payload, _ in pages):
            raise ValueError("svg_cached_author_render_mixed_deck")
        # The existing advisory is optional and never gates publication.
        # A zero-provider recovery cannot invoke it.
        digests = [sha256_hex((payload.get("svg_content") or "").encode())
                   for payload, _ in pages]
        return advisory_record({"skipped": "zero_provider_cached_author_recovery"}, digests)
    if not policy and not getattr(profile, "vision_advisory", False):
        return None
    if policy:
        return await _quality_first_deck_advisory(ctx, pages, job=job, read_png=read_png,
            run_advisory=run_advisory, phase=round_ordinal or 0, final_selected=final_selected, source_evidence=source_evidence)
    from services.provider_usage_instrumentation import provider_usage_scope

    renders = [png if png is not None else read_png(payload) for payload, png in pages]
    # Bound to the SVG bytes publication stores, which is what an export reads.
    digests = [sha256_hex((payload.get("svg_content") or "").encode()) for payload, _ in pages]
    if any(not png for png in renders):
        # The sheet numbers what it shows; a missing render would shift every
        # later slide's number, so no advisory is better than a misattributed one.
        return advisory_record({"skipped": "render_missing"}, digests)
    with provider_usage_scope(job_id=job.id if job else None, operation="authored_generation",
                              stage="vqa",
                              attempt_number=job.attempt_number if job else 0, round_ordinal=round_ordinal,
                              **({"workspace_operation_id": job.workspace_operation_id}
                                 if isinstance(getattr(job, "request_payload", None), dict)
                                 and job.request_payload.get("_svg_partial_recovery") else {})):
        vision = await run_advisory(renders, slide_count=len(pages))
    record = advisory_record(vision, digests)
    # Each independently sealed page remains reusable when a later review
    # updates a subset. Legacy v1 record matching ignores this additive field.
    from types import SimpleNamespace
    from services.svg_export_visual_review import split_page_advisories

    slides = [SimpleNamespace(svg_content=payload.get("svg_content") or "",
                              content={"svgReceipt": {**payload, "png_sha256": sha256_hex(png)}})
              for (payload, _), png in zip(pages, renders, strict=True)]
    record["pageAdvisories"] = split_page_advisories(vision, slides, range(len(pages)))
    return record


def _design_context(ctx, pages, *, job, read_png, review_principles_version=None):
    """Actual selected style/source evidence, without copying unrelated deck SVGs."""
    from utils.owner_context import get_current_owner_id, get_current_tenant_id
    from services.generation_core.drivers.svg.quality_recovery import _quality_json_value
    from services.generation_core.drivers.svg.native_requirements import requested_native_data
    snapshot = _quality_json_value(ctx.snapshot)
    references = snapshot.get("references") or {}
    roles = getattr(ctx, "roles", [])
    examples, seen, examples_by_digest = [], set(), {}
    from services.generation_core.drivers.svg.reference_selection import select_reference_role
    contents = getattr(ctx, "quality_review_contents", [])
    review_roles = list(roles or ["content"])
    page_reference_roles = {}
    page_example_digests = {}
    for index, (payload, _) in enumerate(pages):
        receipt = next((attempt.get("receipt") for attempt in reversed(payload.get("attempts") or [])
                        if isinstance(attempt, Mapping) and isinstance(attempt.get("receipt"), Mapping)
                        and (attempt["receipt"].get("reference_selection") or attempt["receipt"].get("design_example_inputs"))), {})
        selected = receipt.get("reference_selection")
        if selected is None and index < len(contents):
            selected = select_reference_role(snapshot, roles[index] if index < len(roles) else "content",
                                             contents[index], payload.get("visual_intent"))
        if selected:
            chosen = selected.get("reference_roles") or ([selected["selected_role"]] if selected.get("selected_role") else [])
            review_roles.extend(chosen)
            page_reference_roles[index] = chosen
        supplied = receipt.get("design_example_inputs")
        if isinstance(supplied, list):
            page_example_digests[index] = sorted({item["digest"].removeprefix("sha256:") for item in supplied
                if isinstance(item, Mapping) and isinstance(item.get("digest"), str)})
    for role in dict.fromkeys(review_roles):
        actual_role = role if references.get(role) else "content"
        for example in references.get(actual_role) or []:
            data_url = example.get("data_url")
            if not isinstance(data_url, str):
                continue
            # Digest the actual admitted image, never a model-provided hash.
            _, encoded = data_url.split(",", 1)
            digest = sha256_hex(base64.b64decode(encoded, validate=True))
            if digest not in seen:
                record = dict(role=actual_role, roles=[actual_role], data_url=data_url, digest=digest)
                examples.append(record)
                examples_by_digest[digest] = record
                seen.add(digest)
            elif actual_role not in examples_by_digest[digest]["roles"]:
                examples_by_digest[digest]["roles"].append(actual_role)
    def design_only(value):
        if isinstance(value, Mapping):
            return {key: design_only(item) for key, item in value.items()
                    if key not in {"data_url", "dataUrl", "svg_content", "svgContent", "references", "diagnostics"}}
        if isinstance(value, (list, tuple)):
            return [design_only(item) for item in value]
        return value
    style_fields = {"style_id", "canvas", "tokens", "common_guidance", "mapped_guidance", "overrides",
                    "art_direction", "composition_guidance", "master_template", "design_pack_template",
                    "composition_mode", "reference_semantics", "design_quality_guidance", "common_header"}
    style = design_only({key: value for key, value in snapshot.items() if key in style_fields
                        and (key != "common_header" or value is not None)})
    header_pages = getattr(ctx, "header_pages", None)
    if "common_header" in style and (not isinstance(header_pages, (list, tuple))
            or len(header_pages) != len(pages)
            or any(not isinstance(binding, Mapping) for binding in header_pages)):
        raise ValueError("svg_common_header_review_binding_missing")
    source_pages = []
    for index, (payload, _) in enumerate(pages):
        source_pages.append(dict(index=index, content=contents[index] if index < len(contents) else None,
            sourceNativeRequirements=requested_native_data(contents[index] if index < len(contents) else None),
            referenceRoles=page_reference_roles.get(index, [roles[index] if index < len(roles) else "content"]),
            authorExampleDigests=page_example_digests.get(index),
            preservation=payload.get("source_preservation"), visualIntent=payload.get("visual_intent"),
            inputDigest=payload.get("input_digest")))
        if "common_header" in style:
            # These are the bindings validated with the frozen style before
            # authoring. Keep their original title, page identity and evidence.
            source_pages[-1]["common_header"] = _quality_json_value(header_pages[index])
    prior_candidates = {}
    prior_pages = getattr(ctx, "quality_review_prior_pages", {}) or {}
    target_axes = getattr(ctx, "quality_review_target_axes", {}) or {}
    target_goals = getattr(ctx, "quality_review_target_goals", {}) or {}
    for index, (payload, png) in sorted(prior_pages.items()):
        if type(index) is not int or not 0 <= index < len(pages):
            raise ValueError("svg_quality_recovery_comparison_index_invalid")
        png = png if png is not None else read_png(payload)
        targets = target_axes.get(index) or []
        if not png or not targets or any(name not in DESIGN_AXES for name in targets):
            raise ValueError("svg_quality_recovery_comparison_input_invalid")
        prior_candidates[index] = dict(png=png, pageDigest=sha256_hex((payload.get("svg_content") or "").encode()),
            pngDigest=sha256_hex(png), inputDigest=payload.get("input_digest"), targetAxes=targets,
            mandatoryEvidenceValid=bool(_candidate_local_valid(payload, ctx=ctx, index=index)))
        if (_quality_policy(snapshot) or {}).get("version") == 2:
            prior_candidates[index]["targetGoals"] = target_goals.get(index, [])
    context = _quality_json_value(dict(policy=_quality_policy(snapshot), style=style, priorCandidates=prior_candidates,
        styleDigest=content_digest(style), snapshotDigest=content_digest(snapshot), examples=examples,
        runtimeBinding=getattr(ctx, "quality_recovery_runtime_binding", None),
        mandatoryEvidence=[bool(_candidate_local_valid(payload, ctx=ctx, index=index))
                           for index, (payload, _) in enumerate(pages)],
        exampleDigests=sorted(seen), pages=source_pages,
        roles=list(roles), deckComplete=len(pages) == getattr(ctx, "count", len(pages)),
        composition=observe_svg_deck_composition(pages, quality_policy=True),
        binding=dict(jobId=str(job.id) if job else None,
            operationId=str(job.workspace_operation_id) if job and getattr(job, "workspace_operation_id", None) else None,
            presentationId=str(job.presentation_id) if job and getattr(job, "presentation_id", None) else None,
            ownerId=str(get_current_owner_id()) if get_current_owner_id() else None,
            tenantId=str(get_current_tenant_id()) if get_current_tenant_id() else None)))
    request = getattr(job, "request_payload", None)
    if isinstance(request, Mapping) and isinstance(request.get("instructions"), str):
        # Admitted user constraints, once per deck; never authored declarations.
        context["sourceDirectives"] = request["instructions"]
    if baseline_review_active(context):
        if review_principles_version is not None:
            baseline_design_principles_digest({"reviewPrinciplesVersion": review_principles_version})
            context["reviewPrinciplesVersion"] = review_principles_version
        # Stable anonymous positioning binds to the displayed bytes, never author order.
        vector = content_digest([sha256_hex(png if png is not None else read_png(payload)) for payload, png in pages])
        context["blindVariantLabel"] = "A" if int(vector[-1], 16) % 2 else "B"
    return context


def _local_review_digest(context, payload, png, index):
    quality = {key: value for key, value in (payload.get("quality") or {}).items()
               if key not in {"compositionObservation"}}
    return content_digest(dict(pageDigest=sha256_hex((payload.get("svg_content") or "").encode()),
        pngDigest=sha256_hex(png), inputDigest=payload.get("input_digest"),
        source=context["pages"][index], preservation=payload.get("source_preservation"), quality=quality,
        snapshot=context["snapshotDigest"], runtime=context["runtimeBinding"], policy=context["policy"],
        binding=context["binding"],
        **({"reviewPrinciplesVersion": context["reviewPrinciplesVersion"]}
           if "reviewPrinciplesVersion" in context else {}),
        **({"sourceDirectives": context["sourceDirectives"]} if "sourceDirectives" in context else {})))


def _render_resolution(png, canvas):
    if not isinstance(png, bytes) or len(png) < 24 or png[:8] != b"\x89PNG\r\n\x1a\n" or png[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", png[16:24])
    return dict(width=width, height=height,
        sufficient=width >= (canvas.get("width") or 1) and height >= (canvas.get("height") or 1))


async def _quality_first_deck_advisory(ctx, pages, *, job, read_png, run_advisory, phase, final_selected=False, source_evidence=None):
    """Bound physical review calls to the pre-admitted plan, keeping legacy calls intact."""
    from services.svg_authoring_profile import read_quality_recovery_execution_plan
    from services.authored_checkpoint_errors import AuthoredStageError
    from services.provider_usage_instrumentation import provider_usage_scope
    plan = read_quality_recovery_execution_plan(ctx.snapshot, manifest=getattr(ctx, "partial_recovery_manifest", None))
    if (plan is None or plan["slide_count"] != len(pages) or type(phase) is not int
            or not 0 <= phase < plan["review_round_limit"]):
        raise ValueError("svg_quality_recovery_review_plan_invalid")
    checkpoint = getattr(ctx, "quality_recovery_review_group_checkpoint", None)
    if checkpoint is None or job is None:
        raise ValueError("svg_quality_recovery_review_authority_missing")
    renders = [png if png is not None else read_png(payload) for payload, png in pages]
    if any(not isinstance(png, bytes) or not png for png in renders):
        return advisory_record({"skipped": "render_missing"},
            [sha256_hex((payload.get("svg_content") or "").encode()) for payload, _ in pages])
    context = _design_context(ctx, pages, job=job, read_png=read_png,
                              review_principles_version=DESIGN_PRINCIPLES_VERSION)
    final_control = plan.get("final_selected_review") if final_selected else None
    if final_selected and final_control is None:
        raise ValueError("svg_final_selected_review_not_admitted")
    physical_offset = final_control["physical_offset"] if final_control else 0
    evidence = source_evidence or getattr(ctx, "quality_final_selected_source", None)
    from services.svg_final_selected_continuation import INITIAL_NAMESPACE, NAMESPACE
    review_namespace = NAMESPACE if final_selected else INITIAL_NAMESPACE if evidence and phase == 0 else None
    reused_groups, evidence_consumers = ({}, [])
    live_producers = evidence.get("liveReviewProducers") if isinstance(evidence, Mapping) else None
    if live_producers is not None and (not baseline_review_active(context) or not final_selected):
        raise ValueError("svg_live_final_selected_review_policy_invalid")
    if evidence is not None and phase == 0 and live_producers is None:
        from services.svg_final_selected_continuation import reusable_local_groups
        context["priorCandidates"] = {}
        reused_groups, evidence_consumers = reusable_local_groups(context, pages, renders, evidence)
    page_digests = [sha256_hex((payload.get("svg_content") or "").encode()) for payload, _ in pages]
    png_digests = [sha256_hex(png) for png in renders]
    if (not all(context["mandatoryEvidence"])
            or any(payload.get("png_sha256") != png_digest
                   for (payload, _), png_digest in zip(pages, png_digests, strict=True))
            or any(prior["mandatoryEvidenceValid"] is not True for prior in context["priorCandidates"].values())):
        return advisory_record({"skipped": "mandatory_evidence_invalid"}, page_digests)
    canvas = context["style"].get("canvas") or {}
    all_review_images = renders + [prior["png"] for prior in context["priorCandidates"].values()]
    if any(not (resolution := _render_resolution(png, canvas)) or not resolution["sufficient"]
           for png in all_review_images):
        return advisory_record({"skipped": "render_resolution_insufficient"}, page_digests)
    if not context["runtimeBinding"] or not all(context["binding"].get(key)
            for key in ("jobId", "presentationId", "ownerId", "tenantId")):
        raise ValueError("svg_quality_recovery_review_binding_missing")
    per_phase = plan["phase_group_limit"]
    group_size = plan["review_group_size"]
    full_review = len(pages) <= group_size
    global_retry_limit = plan["global_format_retry_limit"]
    if final_control and baseline_review_active(context):
        per_phase = final_control["review_call_limit"]
        group_size = final_control["local_group_size"]
        global_retry_limit = final_control["global_format_retry_limit"]
        full_review = False
    groups = [list(range(start, min(start + group_size, len(pages))))
              for start in range(0, len(pages), group_size)]
    if len(groups) + (not full_review) + global_retry_limit != per_phase:
        raise ValueError("svg_quality_recovery_review_allocation_invalid")
    local_pages, local_comparisons, records = {}, {}, []
    if live_producers is not None:
        context["priorCandidates"] = {}
        local_pages, evidence_consumers = _reusable_live_local_pages(context, pages, renders, live_producers)
    reservations_ready = asyncio.Event()
    reserved_ordinals = set()
    def local_reserved(ordinal):
        if ordinal < len(groups):
            reserved_ordinals.add(ordinal)
            if len(reserved_ordinals) == len(groups):
                reservations_ready.set()
    prior_gate = _gate_intact(getattr(ctx, "quality_recovery_first_advisory", None)) if phase else None
    changed = set(context["priorCandidates"])
    if phase and prior_gate is not None and not full_review:
        for index, (payload, _) in enumerate(pages):
            old = next((page for page in prior_gate["pages"] if page["index"] == index), None)
            group_changed = baseline_review_active(context) and any(
                member in changed for member in groups[index // group_size])
            if (not group_changed and index not in changed and old and old.get("evaluated") is True
                    and old.get("localEvidenceDigest") == _local_review_digest(context, payload, renders[index], index)):
                axes = {name: old["axes"][name] for name in DESIGN_AXES[:-1]}
                local_pages[index] = dict(index=index, evaluated=True, axes=axes, issues=old.get("issues", []),
                                         localEvidenceReused=True)
                if baseline_review_active(context):
                    local_pages[index]["designTargets"] = [target for target in old.get("designTargets", [])
                                                          if target["axis"] in DESIGN_AXES[:-1]]

    async def request_group(ordinal, indices, group_context, group_renders):
        input_digest = _group_review_input_digest(group_context, group_renders, phase, ordinal, indices)
        saved = await checkpoint(ordinal)
        if saved:
            if saved.get("input_digest") != input_digest:
                raise ValueError("svg_quality_recovery_review_group_conflict")
            if saved.get("status") != "completed":
                raise AuthoredStageError(slide_index=None, stage="render", code="public_provider_result_unknown", retryable=False)
            sealed = saved.get("advisory")
            if (not isinstance(sealed, Mapping) or sealed.get("inputDigest") != input_digest
                    or sealed.get("digest") != content_digest({key: value for key, value in sealed.items() if key != "digest"})):
                raise ValueError("svg_quality_recovery_review_group_evidence_invalid")
            local_reserved(ordinal)
            return saved["advisory"]
        marker = dict(status="review_inflight", input_digest=input_digest,
            group_id=f"phase-{phase}/group-{ordinal}", indices=indices, review_scope=group_context["reviewScope"],
            physical_ordinal=physical_offset + phase * per_phase + ordinal)
        if review_namespace:
            marker.update(namespace=review_namespace,
                          physical_purpose="final-selected" if final_selected else "initial-continuation")
        await checkpoint(ordinal, marker)
        local_reserved(ordinal)
        async def dispatch():
            # Fresh generation inherits the public fence. Explicit workspace IDs
            # select the existing typed partial-recovery admission path.
            with provider_usage_scope(job_id=job.id, operation="authored_generation", stage="vqa",
                    attempt_number=job.attempt_number, round_ordinal=marker["physical_ordinal"],
                    **({"workspace_operation_id": job.workspace_operation_id}
                       if (isinstance(getattr(job, "request_payload", None), dict)
                           and job.request_payload.get("_svg_partial_recovery")) else {})):
                return await run_advisory(group_renders, slide_count=len(group_renders),
                                          required=True, design_context=group_context)
        try:
            if ordinal < len(groups):
                await reservations_ready.wait()
            semaphore = getattr(ctx, "semaphore", None)
            if semaphore is not None:
                async with semaphore:
                    vision = await dispatch()
            else:
                vision = await dispatch()
        except BaseException as exc:
            from utils.provider_overload import safe_provider_diagnostics
            await checkpoint(ordinal, {"status": "review_unknown", "failureDiagnostics": safe_provider_diagnostics(exc)})
            raise
        sealed = dict(groupId=marker["group_id"], globalPageIndices=indices,
                      inputDigest=input_digest, reviewScope=group_context["reviewScope"], vision=vision)
        sealed["digest"] = content_digest(sealed)
        await checkpoint(ordinal, {**marker, "status": "completed", "advisory": sealed})
        return sealed

    async def local_group(ordinal, indices):
        if ordinal in reused_groups:
            local_reserved(ordinal)
            return deepcopy(reused_groups[ordinal])
        to_review = [index for index in indices if index not in local_pages] if not full_review else indices
        if not to_review:
            local_reserved(ordinal)
            return None
        group_context = _local_group_context(context, to_review, full_review)
        return await request_group(ordinal, to_review, group_context, [renders[index] for index in to_review])

    tasks = [asyncio.create_task(local_group(ordinal, indices)) for ordinal, indices in enumerate(groups)]
    try:
        results = await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    for result in results:
        if result is None:
            continue
        records.append(result)
        mapping = result["globalPageIndices"]
        review = result["vision"].get("designReview") or {}
        for page in review.get("pages", []):
            index = mapping[page["index"]]
            local_pages[index] = {**page, "index": index}
        for comparison in review.get("candidateComparisons", []):
            index = mapping[comparison["index"]]
            local_comparisons[index] = {**comparison, "index": index}
    if full_review:
        vision = results[0]["vision"]
    else:
        if (len(local_pages) != len(pages) or any(page.get("evaluated") is not True
                or set(page.get("axes", {})) != set(DESIGN_AXES[:-1]) for page in local_pages.values())):
            vision = dict(promptVersion=DESIGN_REVIEW_PROMPT_VERSION, slideCount=len(pages),
                reviewedPageIndices=list(range(len(pages))), reviewedPngDigests=png_digests, slides=[],
                designReview=dict(pages=list(local_pages.values()), candidateComparisons=[],
                                  deckFlow={"evaluated": False}), skipped="local_review_coverage_incomplete",
                reviewGroups=records)
            vision["designGate"] = _seal_design_gate(context, pages, renders, vision)
            return advisory_record(vision, page_digests)
        # This call uses the entire contact sheet and observed family
        # representatives only for global flow; fine detail remains local.
        representatives = sorted({group["representative"] for group in context["composition"]["groups"]})
        global_context = {**context, "reviewScope": "global", "representativeIndices": representatives,
                          "examples": [], "exampleDigests": []}
        for format_attempt in range(global_retry_limit + 1):
            global_result = await request_group(len(groups) + format_attempt, list(range(len(pages))), global_context, renders)
            records.append(global_result)
            rejected = global_result["vision"]
            if (rejected.get("skipped") != "design_review_schema_or_coverage_invalid"
                    or rejected.get("schemaRejectionReason") not in {
                        "global_review_fields", "global_finding_fields", "global_group_fields",
                        "axis_keys", "axis_semantics", "evidence_bounds",
                    }):
                break
            if format_attempt < global_retry_limit:
                global_context = {**global_context, "formatRetry": {
                    "attempt": format_attempt + 1,
                    "reason": rejected.get("schemaRejectionReason"),
                    "fields": rejected.get("schemaRejectionFields"),
                }}
        global_review = global_result["vision"].get("designReview") or {}
        global_pages = {page["index"]: page for page in global_review.get("pages", [])}
        combined = []
        for index in range(len(pages)):
            local, global_page = local_pages.get(index), global_pages.get(index)
            if not local or not global_page:
                continue
            combined.append(dict(index=index, evaluated=local["evaluated"] and global_page["evaluated"],
                axes={**local["axes"], **global_page["axes"]}, issues=local.get("issues", []),
                localEvidenceReused=local.get("localEvidenceReused", False)))
            if baseline_review_active(context):
                combined[-1]["designTargets"] = local.get("designTargets", []) + global_page.get("designTargets", [])
        global_comparisons = {item["index"]: item for item in global_review.get("candidateComparisons", [])}
        comparisons = []
        for index in sorted(changed):
            local, global_comparison = local_comparisons.get(index), global_comparisons.get(index)
            if local and global_comparison:
                comparisons.append(dict(index=index, evaluated=local["evaluated"] and global_comparison["evaluated"],
                    priorAxes={**local["priorAxes"], **global_comparison["priorAxes"]},
                    visibleEvidence=local["visibleEvidence"] + " " + global_comparison["visibleEvidence"]))
                if baseline_review_active(context):
                    if local.get("blindedPresentation") != global_comparison.get("blindedPresentation"):
                        comparisons[-1]["evaluated"] = False
                    comparisons[-1].update(axisChanges={**local.get("axisChanges", {}),
                        **global_comparison.get("axisChanges", {})},
                        blindedPresentation=local.get("blindedPresentation"))
        vision = dict(promptVersion=DESIGN_REVIEW_PROMPT_VERSION, slideCount=len(pages),
            reviewedPageIndices=list(range(len(pages))), reviewedPngDigests=png_digests,
            model=";".join(sorted({str(record["vision"].get("model") or "") for record in records})),
            slides=[dict(index=page["index"], issues=page["issues"]) for page in combined if page["issues"]],
            designReview=dict(pages=combined, candidateComparisons=comparisons,
                              deckFlow=global_review.get("deckFlow") or {"evaluated": False}))
    vision = {**vision, "reviewGroups": records}
    if evidence_consumers:
        vision["localEvidenceConsumers"] = evidence_consumers
    if review_namespace:
        vision["reviewNamespace"] = review_namespace
    vision["designGate"] = _seal_design_gate(context, pages, renders, vision)
    record = advisory_record(vision, page_digests)
    return record


def _reusable_live_local_pages(context, pages, renders, producers):
    """Consume same-operation page proofs; their original verdicts remain unchanged."""
    from services.svg_final_selected_continuation import NAMESPACE
    reused, consumers = {}, []
    if len(pages) <= 5:
        return reused, consumers
    for producer in producers:
        gate = _gate_intact(producer)
        if gate is None or any(gate.get(key) != context.get(key) for key in
                ("policy", "binding", "runtimeBinding", "snapshotDigest", "styleDigest", "exampleDigests")):
            continue
        by_index = {page.get("index"): page for page in gate.get("pages", [])}
        reusable_indices = set()
        for start in range(0, len(pages), 5):
            indices = list(range(start, min(start + 5, len(pages))))
            if all(index in by_index and by_index[index].get("evaluated") is True
                   and by_index[index].get("localEvidenceDigest") == _local_review_digest(
                       context, pages[index][0], renders[index], index)
                   and index < len(gate["pageDigests"])
                   and gate["pageDigests"][index] == sha256_hex((pages[index][0].get("svg_content") or "").encode())
                   and gate["pngDigests"][index] == sha256_hex(renders[index]) for index in indices):
                reusable_indices.update(indices)
        for page in gate.get("pages", []):
            index = page.get("index")
            if (type(index) is not int or index in reused or index not in reusable_indices
                    or page.get("evaluated") is not True
                    or page.get("localEvidenceDigest") != _local_review_digest(
                        context, pages[index][0], renders[index], index)
                    or gate["pageDigests"][index] != sha256_hex((pages[index][0].get("svg_content") or "").encode())
                    or gate["pngDigests"][index] != sha256_hex(renders[index])):
                continue
            reused[index] = dict(index=index, evaluated=True,
                axes={name: deepcopy(page["axes"][name]) for name in DESIGN_AXES[:-1]},
                issues=deepcopy(page.get("issues", [])), localEvidenceReused=True,
                designTargets=[deepcopy(target) for target in page.get("designTargets", [])
                               if target["axis"] in DESIGN_AXES[:-1]])
            record = next((record for record in gate.get("reviewGroups", [])
                           if record.get("reviewScope") == "local" and index in record.get("globalPageIndices", [])), None)
            group_indices = list(range((index // 5) * 5, min((index // 5 + 1) * 5, len(pages))))
            if (record is None or record.get("globalPageIndices") != group_indices
                    or record.get("digest") != content_digest({key: value for key, value in record.items() if key != "digest"})):
                # A projected page without its physical producer is insufficient.
                reused.pop(index)
                continue
            if not any(consumer["producer_group_id"] == record["groupId"]
                       and consumer["producer_record_digest"] == record["digest"] for consumer in consumers):
                consumers.append(dict(schema="svg-local-review-consumer/v1", namespace=NAMESPACE,
                    producer_operation_id=gate["binding"]["operationId"], producer_job_id=gate["binding"]["jobId"],
                    producer_group_id=record["groupId"], producer_record_digest=record["digest"],
                    producer_input_digest=record["inputDigest"], consumer_binding=deepcopy(context["binding"]),
                    indices=deepcopy(record["globalPageIndices"]), provider_response_reused=False,
                    original_verdict_preserved=True, producer_gate_digest=gate["digest"]))
    return reused, consumers


def _seal_design_gate(context, pages, renders, vision):
    if baseline_review_active(context):
        baseline_design_principles_digest(context)
    page_digests = [sha256_hex((payload.get("svg_content") or "").encode()) for payload, _ in pages]
    png_digests = [sha256_hex(png) for png in renders]
    base = dict(schemaVersion="svg-quality-first-design-gate/v1", policy=context["policy"],
        binding=context["binding"], pageDigests=page_digests, pngDigests=png_digests,
        styleDigest=context["styleDigest"], snapshotDigest=context["snapshotDigest"],
        runtimeBinding=context["runtimeBinding"], exampleDigests=context["exampleDigests"],
        inputDigest=content_digest({key: value for key, value in context.items()
                                   if key not in {"examples", "priorCandidates"}}),
        deckContextDigest=content_digest(dict(pages=page_digests, renders=png_digests,
            sources=context["pages"], style=context["styleDigest"], examples=context["exampleDigests"],
            roles=context["roles"], policy=context["policy"], binding=context["binding"],
            **({"sourceDirectives": context["sourceDirectives"]} if "sourceDirectives" in context else {}))))
    if "reviewPrinciplesVersion" in context:
        base["reviewPrinciplesVersion"] = context["reviewPrinciplesVersion"]
    review = vision.get("designReview") or {}
    reviewed = review.get("pages") or []
    review_by_index = {page.get("index"): page for page in reviewed if isinstance(page, Mapping)
                       and type(page.get("index")) is int}
    coverage = (len(reviewed) == len(pages) == len(review_by_index)
                and set(review_by_index) == set(range(len(pages)))
                and vision.get("promptVersion") == DESIGN_REVIEW_PROMPT_VERSION
                and vision.get("reviewedPageIndices") == list(range(len(pages)))
                and vision.get("reviewedPngDigests") == png_digests
                and context["deckComplete"] and (review.get("deckFlow") or {}).get("evaluated") is True)
    results, blocking, incomplete, advisories = [], [], [], []
    canvas = context["style"].get("canvas") or {}
    for index, ((payload, _), png) in enumerate(zip(pages, renders, strict=True)):
        page = review_by_index.get(index) or {}
        axes = page.get("axes") or {}
        resolution = _render_resolution(png, canvas)
        evaluated = (coverage and page.get("evaluated") is True and set(axes) == set(DESIGN_AXES)
                     and all(isinstance(axis, Mapping) and axis.get("verdict") in {"passed", "advisory", "failed"}
                             and isinstance(axis.get("visibleEvidence"), str) and axis["visibleEvidence"].strip()
                             for axis in axes.values())
                     and resolution is not None and resolution["sufficient"])
        failed_axes = [name for name, axis in axes.items() if isinstance(axis, Mapping)
                       and (axis.get("verdict") == "failed" or axis.get("severity") in {"major", "critical"})]
        minor = [name for name, axis in axes.items() if isinstance(axis, Mapping) and axis.get("verdict") == "advisory"]
        critical_issues = [issue for issue in page.get("issues", []) if issue in {"illegible", "broken_figure"}]
        mandatory_valid = (context["mandatoryEvidence"][index] and not critical_issues
                           and payload.get("png_sha256") == png_digests[index])
        status = "unreviewed" if not evaluated else "failed" if failed_axes or not mandatory_valid else "passed"
        if status == "unreviewed":
            incomplete.append(index)
        elif status == "failed":
            blocking.append(index)
        if minor:
            advisories.append(index)
        results.append(dict(index=index, evaluated=evaluated, status=status, axes=axes,
            failedAxes=failed_axes, issues=page.get("issues", []), resolution=resolution,
            mandatoryEvidenceValid=mandatory_valid,
            localEvidenceDigest=_local_review_digest(context, payload, png, index),
            deckContextDigest=base["deckContextDigest"],
            comparisonContextDigest=content_digest(dict(sources=context["pages"],
                style=context["styleDigest"], examples=context["exampleDigests"], policy=context["policy"],
                roles=context["roles"], binding=context["binding"],
                **({"sourceDirectives": context["sourceDirectives"]} if "sourceDirectives" in context else {}),
                neighbours=[digest for i, digest in enumerate(page_digests) if i != index]))))
        if baseline_review_active(context):
            results[-1].update(designTargets=deepcopy(page.get("designTargets", [])),
                designImprovementStatus="unjudged" if not evaluated else
                "needed" if page.get("designTargets") else "no_grounded_target")
    comparisons = []
    comparison_cohort = content_digest(dict(context=base["deckContextDigest"],
        priors={index: {key: value for key, value in prior.items() if key != "png"}
                for index, prior in context["priorCandidates"].items()}))
    for comparison in review.get("candidateComparisons", []):
        prior = context["priorCandidates"].get(comparison["index"])
        if prior is None:
            continue
        body = dict(index=comparison["index"], evaluated=comparison["evaluated"],
            priorAxes=comparison["priorAxes"], visibleEvidence=comparison["visibleEvidence"],
            priorPageDigest=prior["pageDigest"], priorPngDigest=prior["pngDigest"],
            candidatePageDigest=page_digests[comparison["index"]], candidatePngDigest=png_digests[comparison["index"]],
            targetAxes=prior["targetAxes"], priorInputDigest=prior["inputDigest"],
            priorMandatoryEvidenceValid=prior["mandatoryEvidenceValid"], deckContextDigest=base["deckContextDigest"],
            comparisonCohortDigest=comparison_cohort, comparisonMode="joint-targets-current-source-context/v1")
        if baseline_review_active(context):
            body.update(axisChanges=deepcopy(comparison.get("axisChanges", {})),
                        blindedPresentation=deepcopy(comparison.get("blindedPresentation")),
                        targetGoals=deepcopy(prior.get("targetGoals", [])))
        comparisons.append({**body, "digest": content_digest(body)})
    base.update(status="unreviewed" if incomplete else "failed" if blocking else "passed",
        pageCoverageComplete=coverage and not incomplete, pages=results, blockingPages=blocking,
        unreviewedPages=incomplete, advisoryPages=advisories, deckFlow=review.get("deckFlow"),
        reason=vision.get("skipped"), candidateComparisons=comparisons,
        reviewGroups=vision.get("reviewGroups", []))
    if baseline_review_active(context):
        base["designImprovementStatus"] = ("unjudged" if incomplete else
            "needed" if any(page.get("designTargets") for page in results) else "satisfied")
    if vision.get("localEvidenceConsumers"):
        base["localEvidenceConsumers"] = deepcopy(vision["localEvidenceConsumers"])
    if vision.get("reviewNamespace"):
        base["reviewNamespace"] = vision["reviewNamespace"]
    return {**base, "digest": content_digest(base)}


def _gate_intact(advisory):
    if not isinstance(advisory, Mapping):
        return None
    gate = (advisory.get("vision") or {}).get("designGate")
    if (not isinstance(gate, Mapping) or not isinstance(gate.get("pageDigests"), list)
            or not record_matches(advisory, gate["pageDigests"])
            or gate.get("schemaVersion") != "svg-quality-first-design-gate/v1"
            or len(gate.get("pngDigests", [])) != len(gate["pageDigests"])):
        return None
    body = {key: value for key, value in gate.items() if key != "digest"}
    if gate.get("digest") != content_digest(body):
        return None
    if baseline_review_active(gate):
        try:
            baseline_design_principles_digest(gate)
        except ValueError:
            return None
    from utils.owner_context import get_current_owner_id, get_current_tenant_id
    owner, tenant = get_current_owner_id(), get_current_tenant_id()
    binding = gate.get("binding") or {}
    if (binding.get("ownerId") != (str(owner) if owner else None)
            or binding.get("tenantId") != (str(tenant) if tenant else None)):
        return None
    return gate


def _current_gate(advisory, pages):
    if not isinstance(advisory, Mapping):
        return None
    digests = [sha256_hex((payload.get("svg_content") or "").encode()) for payload, _ in pages]
    if not record_matches(advisory, digests):
        return None
    gate = _gate_intact(advisory)
    if gate is None or gate.get("pageDigests") != digests:
        return None
    for index, (payload, png) in enumerate(pages):
        current_png_digest = sha256_hex(png) if png else payload.get("png_sha256")
        if (current_png_digest != gate["pngDigests"][index]
                or payload.get("png_sha256") != gate["pngDigests"][index]):
            return None
    return gate


def quality_recovery_decisions(ctx, pages, advisory):
    if not _quality_policy(getattr(ctx, "snapshot", {})):
        return {}
    gate = _current_gate(advisory, pages)
    if (gate is None or gate.get("snapshotDigest") != content_digest(ctx.snapshot)
            or gate.get("runtimeBinding") != getattr(ctx, "quality_recovery_runtime_binding", None)):
        return {}
    decisions = {}
    for page in gate["pages"]:
        if page.get("evaluated") is not True:
            continue
        failed = [axis for axis in page["axes"].values() if axis.get("verdict") == "failed"]
        targets = page.get("designTargets", []) if (gate.get("policy") or {}).get("version") == 2 else []
        if not failed and not targets:
            continue
        scope = "design_rewrite" if targets or any(axis["repairScope"] == "design_rewrite" for axis in failed) else "local_repair"
        decisions[page["index"]] = dict(repair_scope=scope,
            target_axes=list(dict.fromkeys([*page["failedAxes"], *[target["axis"] for target in targets]])),
            issues=[dict(axis=name, **axis) for name, axis in page["axes"].items() if axis.get("verdict") == "failed"])
        if targets:
            decisions[page["index"]]["design_targets"] = deepcopy(targets)
    return decisions


def _candidate_local_valid(payload, *, ctx=None, index=None, snapshot=None):
    from services.svg_contract import source_digest
    quality = payload.get("quality") or {}
    png_digest = payload.get("png_sha256")
    valid = (quality.get("passed") is True and quality.get("verified") is True and not quality.get("failures")
            and quality.get("measurement_complete") is True and payload.get("svg_content")
            and isinstance(png_digest, str) and len(png_digest) == 64
            and quality.get("source_digest") == source_digest(payload["svg_content"])
            and quality.get("png_digest") == "sha256:" + png_digest
            and (payload.get("source_preservation") or {}).get("status") not in {"failed", "unverified"})
    if not valid:
        return False
    preservation = payload.get("source_preservation") or {}
    expected_source = bool(getattr(ctx, "source_reflow", False) or (snapshot or {}).get("source_authoring_contract"))
    if expected_source or preservation.get("contract_version") == "svg-source-preservation/v1":
        if (preservation.get("contract_version") != "svg-source-preservation/v1"
                or preservation.get("rendering_verified") is not True
                or preservation.get("svg_digest") != source_digest(payload["svg_content"])
                or not preservation.get("source_digest") or not preservation.get("manifest_digest")
                or any(value is False for value in (preservation.get("checks") or {}).values())):
            return False
        document = getattr(ctx, "source_document", None)
        if document is not None:
            from services.svg_source_layout import verify_source_receipt
            try:
                verify_source_receipt(payload["svg_content"], preservation, document, index,
                                     _validated_pages=getattr(ctx, "source_units", None))
            except (ValueError, KeyError, TypeError):
                return False
    return True


def compare_quality_recovery_candidates(ctx, prior, new, prior_advisory, new_advisory, index):
    """Uncertain improvement keeps the prior bytes without certifying completion."""
    reject = lambda reason: dict(selected="prior", reason=reason)
    if not _quality_policy(getattr(ctx, "snapshot", {})):
        return reject("quality_policy_inactive")
    if not _candidate_local_valid(new, ctx=ctx, index=index):
        return reject("candidate_mandatory_evidence_invalid")
    old_gate = _gate_intact(prior_advisory)
    new_gate = _gate_intact(new_advisory)
    if old_gate is None or new_gate is None:
        return reject("comparison_evidence_stale")
    if (old_gate.get("snapshotDigest") != content_digest(ctx.snapshot)
            or new_gate.get("snapshotDigest") != content_digest(ctx.snapshot)
            or new_gate.get("runtimeBinding") != getattr(ctx, "quality_recovery_runtime_binding", None)
            or old_gate.get("runtimeBinding") != new_gate.get("runtimeBinding")):
        return reject("comparison_runtime_or_style_changed")
    old_page = next((page for page in old_gate.get("pages", []) if page.get("index") == index), None)
    new_page = next((page for page in new_gate.get("pages", []) if page.get("index") == index), None)
    if not old_page or not new_page or not old_page.get("evaluated") or not new_page.get("evaluated"):
        return reject("comparison_review_incomplete")
    if new_page.get("mandatoryEvidenceValid") is not True:
        return reject("candidate_design_evidence_invalid")
    if (not 0 <= index < len(old_gate["pageDigests"]) or not index < len(new_gate["pageDigests"])
            or old_gate.get("pageDigests", [])[index] != sha256_hex((prior.get("svg_content") or "").encode())
            or new_gate.get("pageDigests", [])[index] != sha256_hex((new.get("svg_content") or "").encode())):
        return reject("comparison_source_stale")
    if (old_gate["pngDigests"][index] != prior.get("png_sha256")
            or new_gate["pngDigests"][index] != new.get("png_sha256")):
        return reject("comparison_render_stale")
    baseline_policy = (new_gate.get("policy") or {}).get("version") == 2
    goals = old_page.get("designTargets", []) if baseline_policy else []
    target_axes = list(dict.fromkeys([*(old_page.get("failedAxes") or []), *[goal["axis"] for goal in goals]]))
    if not target_axes:
        return reject("no_targeted_defect")
    paired = next((item for item in new_gate.get("candidateComparisons", []) if item.get("index") == index), None)
    if paired is None or paired.get("evaluated") is not True:
        return reject("baseline_deck_context_review_required")
    paired_body = {key: value for key, value in paired.items() if key != "digest"}
    if (paired.get("digest") != content_digest(paired_body)
            or paired.get("priorPageDigest") != old_gate["pageDigests"][index]
            or paired.get("priorPngDigest") != old_gate["pngDigests"][index]
            or paired.get("candidatePageDigest") != new_gate["pageDigests"][index]
            or paired.get("candidatePngDigest") != new_gate["pngDigests"][index]
            or paired.get("deckContextDigest") != new_gate["deckContextDigest"]
            or paired.get("comparisonMode") != "joint-targets-current-source-context/v1"
            or not paired.get("comparisonCohortDigest")
            or set(paired.get("targetAxes", [])) != set(target_axes)
            or old_gate.get("binding") != new_gate.get("binding")
            or old_gate.get("styleDigest") != new_gate.get("styleDigest")
            or old_gate.get("exampleDigests") != new_gate.get("exampleDigests")
            or prior.get("input_digest") != new.get("input_digest")
            or paired.get("priorInputDigest") != prior.get("input_digest")):
        return reject("comparison_binding_invalid")
    baseline_axes = paired["priorAxes"]
    ranks = {"passed": 0, "advisory": 1, "failed": 2, "unreviewed": 3}
    if baseline_policy:
        presentation = paired.get("blindedPresentation") or {}
        changes = paired.get("axisChanges") or {}
        if (presentation.get("candidateLabel") not in {"A", "B"}
                or presentation.get("priorLabel") not in {"A", "B"}
                or presentation["candidateLabel"] == presentation["priorLabel"]
                or presentation.get("displayOrder") != ["A", "B"]
                or presentation.get("chronologyExposed") is not False or presentation.get("versionExposed") is not False
                or set(changes) != set(DESIGN_AXES) or paired.get("targetGoals", []) != goals):
            return reject("blinded_comparison_evidence_invalid")
        if any(change.get("change") == "unjudged" for change in changes.values()):
            return reject("comparison_improvement_unjudged")
        if any(changes[name].get("change") != "improved" for name in target_axes):
            return reject("target_goal_not_improved")
        if any(new_page["axes"][name]["verdict"] in {"failed", "unreviewed"} for name in target_axes):
            return reject("target_defect_not_resolved")
        if any(changes[name].get("change") == "worsened"
               and new_page["axes"][name]["severity"] in {"major", "critical"} for name in DESIGN_AXES):
            return reject("candidate_major_regression")
    if any(new_page["axes"][name]["verdict"] in {"failed", "unreviewed"}
           or ranks[new_page["axes"][name]["verdict"]] >= ranks[baseline_axes[name]["verdict"]]
           for name in target_axes if not baseline_policy or name in (old_page.get("failedAxes") or [])):
        return reject("target_defect_not_resolved")
    if any(ranks[new_page["axes"][name]["verdict"]] > ranks[baseline_axes[name]["verdict"]]
           and new_page["axes"][name]["severity"] in {"major", "critical"} for name in DESIGN_AXES):
        return reject("candidate_major_regression")
    return dict(selected="candidate", reason="target_improved_without_major_regression", targetAxes=target_axes)


def reusable_final_deck_advisory(ctx, pages, advisory, *, job, read_png):
    """Reuse the actual producer evidence unchanged, including an original failed verdict."""
    if (_quality_policy(ctx.snapshot) or {}).get("version") != 2:
        return None
    gate = _current_gate(advisory, pages)
    if gate is None:
        return None
    renders = [png if png is not None else read_png(payload) for payload, png in pages]
    if any(not isinstance(png, bytes) or not png for png in renders):
        return None
    context = _design_context(ctx, pages, job=job, read_png=read_png,
                              review_principles_version=gate.get("reviewPrinciplesVersion"))
    candidate = _seal_design_gate(context, pages, renders, advisory.get("vision") or {})
    identities = ("policy", "binding", "pageDigests", "pngDigests", "styleDigest", "snapshotDigest",
                  "runtimeBinding", "exampleDigests", "inputDigest", "deckContextDigest")
    if (any(gate.get(key) != candidate.get(key) for key in identities)
            or len(gate.get("pages", [])) != len(candidate["pages"])
            or any(old.get("localEvidenceDigest") != current.get("localEvidenceDigest")
                   for old, current in zip(gate["pages"], candidate["pages"], strict=True))):
        return None
    return deepcopy(advisory)


def enforce_quality_recovery_publication(snapshot, pages, advisory, *, job=None):
    if not _quality_policy(snapshot):
        return
    from services.authored_checkpoint_errors import AuthoredStageError
    gate = _current_gate(advisory, pages)
    expected_binding = dict(jobId=str(job.id) if job else None,
        operationId=str(job.workspace_operation_id) if job and getattr(job, "workspace_operation_id", None) else None,
        presentationId=str(job.presentation_id) if job and getattr(job, "presentation_id", None) else None)
    if (gate is None or gate.get("policy") != _quality_policy(snapshot) or gate.get("status") != "passed"
            or ((_quality_policy(snapshot) or {}).get("version") == 2
                and gate.get("designImprovementStatus") != "satisfied")
            or gate.get("snapshotDigest") != content_digest(snapshot)
            or any((gate.get("binding") or {}).get(key) != value for key, value in expected_binding.items())
            or gate.get("pageCoverageComplete") is not True
            or any(not _candidate_local_valid(payload, snapshot=snapshot) for payload, _ in pages)):
        raise AuthoredStageError(slide_index=None, stage="render",
            code="svg_quality_recovery_review_required" if gate else "svg_quality_recovery_review_unavailable", retryable=False)


__all__ = ["classify_pages", "deck_advisory", "page_outcome", "quality_recovery_decisions",
           "compare_quality_recovery_candidates", "enforce_quality_recovery_publication"]


def _group_review_input_digest(group_context, group_renders, phase, ordinal, indices):
    public_context = {key: value for key, value in group_context.items() if key not in {'examples', 'priorCandidates'}}
    if baseline_review_active(group_context):
        public_context["reviewPrinciplesDigest"] = baseline_design_principles_digest(group_context)
    input_digest = content_digest(dict(context=public_context, examples=group_context['exampleDigests'], pngDigests=[sha256_hex(png) for png in group_renders], priorCandidates={index: {key: value for key, value in prior.items() if key != 'png'} for index, prior in group_context['priorCandidates'].items()}, groupId=f'phase-{phase}/group-{ordinal}', indices=indices))
    return input_digest

def _local_group_context(context, to_review, full_review=False):
    group_context = {**context, 'reviewScope': 'full' if full_review else 'local', 'globalPageIndices': to_review, 'deckComplete': full_review, 'pages': [{**context['pages'][index], 'index': local, 'deckIndex': index} for local, index in enumerate(to_review)], 'roles': [context['roles'][index] if index < len(context['roles']) else 'content' for index in to_review], 'composition': context['composition'] if full_review else {}, 'priorCandidates': {local: context['priorCandidates'][index] for local, index in enumerate(to_review) if index in context['priorCandidates']}}
    relevant_roles = {role for page in group_context['pages'] for role in page['referenceRoles']}
    actual_example_digests = {digest for page in group_context['pages'] for digest in page.get('authorExampleDigests') or []}
    group_context['examples'] = [example for example in context['examples'] if (example['digest'] in actual_example_digests if actual_example_digests else bool(set(example.get('roles') or [example['role']]) & relevant_roles))]
    group_context['exampleDigests'] = sorted((example['digest'] for example in group_context['examples']))
    return group_context

