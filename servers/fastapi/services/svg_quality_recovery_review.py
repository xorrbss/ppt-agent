"""Exact saved-page evidence for the one-call, owner-bound edited SVG review."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from services.generation_core.quality.advisory_record import content_digest
from services.generation_core.quality.vision_advisory_prompt import DESIGN_LOCAL_AXES
from utils.hashing import sha256_hex


def saved_review_runtime():
    from services import svg_renderer
    from services.svg_trusted_fonts import FONT_DIRECTORY, BUNDLED_FACES
    from utils.svg_template_fonts import FONT_PATH
    from services.public_render_runtime_identity import public_render_runtime_binding
    from services.generation_core.quality.vision_advisory_prompt import DESIGN_REVIEW_PROMPT_VERSION
    from services.generation_core.quality import vision_advisory, vision_advisory_prompt
    return dict(rendererVersion=svg_renderer.SVG_RENDERER_VERSION,
        rendererDigest=sha256_hex(Path(svg_renderer.__file__).read_bytes()),
        fontDigests=[sha256_hex(path.read_bytes()) for path in
            [FONT_PATH, *[FONT_DIRECTORY / filename for _, _, filename in BUNDLED_FACES]]],
        worker=public_render_runtime_binding(), promptVersion=DESIGN_REVIEW_PROMPT_VERSION,
        reviewerDigests=[sha256_hex(Path(module.__file__).read_bytes())
                         for module in (vision_advisory, vision_advisory_prompt)])


def saved_page_digest(slide, snapshot, runtime, binding):
    content = slide.content or {}
    receipt = content.get("svgReceipt") or {}
    # Capture location and previous review operation are not rendering inputs.
    evidence = {key: value for key, value in receipt.items() if key not in {
        "png_path", "qualityRecoveryReview", "quality_recovery_execution", "quality_recovery",
        "raw_response", "raw_response_sha256", "remake"}}
    quality = dict(evidence.get("quality") or {})
    quality.pop("compositionObservation", None)
    evidence["quality"] = quality
    return content_digest(dict(svg=slide.svg_content, source=content.get("source"),
        role=content.get("role"), assets=content.get("svgAssetManifest"),
        native={key: value for key, value in content.items() if key not in {
            "svgReceipt", "source", "role", "svgAssetManifest", "__svg__"}},
        evidence=evidence, snapshot=snapshot, runtime=runtime, binding=binding))


def seal_saved_page_evidence(slides, snapshot, gate, *, runtime=None):
    runtime = runtime if runtime is not None else saved_review_runtime()
    binding = {key: gate["binding"].get(key) for key in
        ("presentationId", "ownerId", "tenantId", "jobId")}
    records = []
    for page in gate["pages"]:
        index = page["index"]
        records.append(dict(index=index, evaluated=page.get("evaluated") is True,
            axes={name: deepcopy(page.get("axes", {}).get(name)) for name in DESIGN_LOCAL_AXES},
            issues=list(page.get("issues") or []),
            inputDigest=saved_page_digest(slides[index], snapshot, runtime, binding)))
    body = dict(schema="svg-saved-page-review-evidence/v1", gateDigest=gate["digest"],
        runtime=runtime, binding=binding, pages=records)
    return {**body, "digest": content_digest(body)}


def prepare_saved_review(presentation, slides, indices, pngs, *, operation_id, owner_scope):
    from services.svg_authoring_profile import read_quality_recovery_plan
    from services.generation_core.drivers.svg.quality import (
        _design_context, _gate_intact, _candidate_local_valid, _render_resolution)
    from utils.owner_context import get_current_owner_id, get_current_tenant_id
    meta = (presentation.theme or {}).get("svg") or {}
    snapshot = meta.get("styleSnapshot") or {}
    publication = meta.get("qualityRecoveryPublication") or {}
    parent_revision = publication.get("sourceGenerationRevision", publication.get("sourceRevision"))
    if (type(parent_revision) is not int or parent_revision > presentation.revision
            or not publication.get("parentOperationId") or not publication.get("parentJobId")
            or publication.get("digest") != content_digest({key: value for key, value in publication.items() if key != "digest"})):
        raise ValueError("svg_quality_recovery_review_parent_invalid")
    runtime = saved_review_runtime()
    binding = dict(presentationId=str(presentation.id), ownerId=str(get_current_owner_id()),
        tenantId=str(get_current_tenant_id()), jobId=publication["parentJobId"])
    if binding["ownerId"] != str(presentation.owner_id):
        raise ValueError("svg_quality_review_owner_invalid")
    pages = [({**deepcopy((slide.content or {}).get("svgReceipt") or {}),
               "svg_content": slide.svg_content}, png) for slide, png in zip(slides, pngs, strict=True)]
    if any(not _candidate_local_valid(payload, snapshot=snapshot) for payload, _ in pages):
        raise ValueError("svg_quality_review_measurement_stale")
    canvas = snapshot.get("canvas") or {}
    if any(not (resolution := _render_resolution(png, canvas)) or not resolution["sufficient"] for png in pngs):
        raise ValueError("svg_quality_review_resolution_insufficient")
    prior_gate = _gate_intact(meta.get("advisory"))
    saved = (meta.get("advisory") or {}).get("savedPageReviewEvidence") or {}
    reusable = {}
    if (prior_gate is not None and saved.get("schema") == "svg-saved-page-review-evidence/v1"
            and saved.get("gateDigest") == prior_gate["digest"]
            and saved.get("digest") == content_digest({key: value for key, value in saved.items() if key != "digest"})
            and saved.get("binding") == binding and saved.get("runtime") == runtime
            and runtime["worker"].get("checkpointReuseEligible") is True
            and isinstance(saved.get("pages"), list)):
        seen = set()
        for page in saved["pages"]:
            index = page.get("index")
            if type(index) is not int or not 0 <= index < len(slides) or index in seen:
                raise ValueError("svg_quality_review_reuse_evidence_invalid")
            seen.add(index)
            gate_page = next((record for record in prior_gate.get("pages", []) if record.get("index") == index), None)
            if (page.get("evaluated") is True and set(page.get("axes") or {}) == set(DESIGN_LOCAL_AXES)
                    and gate_page is not None and gate_page.get("evaluated") is True
                    and page["axes"] == {name: gate_page.get("axes", {}).get(name) for name in DESIGN_LOCAL_AXES}
                    and page.get("issues") == gate_page.get("issues", [])
                    and page.get("inputDigest") == saved_page_digest(slides[index], snapshot, runtime, binding)):
                reusable[index] = deepcopy(page)
    # Explicit subset pages refresh locally; every deck page refreshes globally.
    changed = sorted(set(indices) | (set(range(len(slides))) - set(reusable)))
    plan = read_quality_recovery_plan(snapshot)
    if not plan or len(changed) > plan["review_group_size"]:
        raise ValueError("svg_quality_review_single_call_capacity_exceeded")
    job = SimpleNamespace(id=publication["parentJobId"], workspace_operation_id=operation_id,
                          presentation_id=presentation.id)
    ctx = SimpleNamespace(snapshot=snapshot, count=len(slides),
        roles=[(slide.content or {}).get("role") or "content" for slide in slides],
        quality_review_contents=[(slide.content or {}).get("source") for slide in slides],
        quality_recovery_runtime_binding=content_digest(runtime))
    context = _design_context(ctx, pages, job=job, read_png=lambda payload: None)
    context.update(reviewScope="edited", deckComplete=True, changedPageIndices=changed,
                   representativeIndices=[], priorCandidates={})
    return dict(context=context, snapshot=snapshot, pages=pages, pngs=pngs, changed=changed,
        reusable=reusable, runtime=runtime, publication=publication,
        currentInputDigests=[saved_page_digest(slide, snapshot, runtime, binding) for slide in slides],
        sourceGenerationRevision=parent_revision, ownerScope=owner_scope)


def seal_saved_review(prepared, slides, vision, claim):
    from services.generation_core.drivers.svg.quality import _seal_design_gate
    from services.generation_core.quality.advisory_record import advisory_record
    review = vision.get("designReview") or {}
    local = {page["index"]: page for page in review.get("localPages", [])}
    global_pages = {page["index"]: page for page in review.get("pages", [])}
    combined = []
    for index in range(len(slides)):
        page = local.get(index) if index in prepared["changed"] else prepared["reusable"].get(index)
        global_page = global_pages.get(index)
        if page is None or global_page is None:
            continue
        combined.append(dict(index=index, evaluated=page["evaluated"] and global_page["evaluated"],
            axes={**page["axes"], **global_page["axes"]}, issues=page.get("issues", []),
            localEvidenceReused=index not in prepared["changed"]))
    vision = {**vision, "designReview": dict(pages=combined, candidateComparisons=[], deckFlow=review.get("deckFlow")),
        "slides": [dict(index=page["index"], issues=page["issues"]) for page in combined]}
    gate = _seal_design_gate(prepared["context"], prepared["pages"], prepared["pngs"], vision)
    vision["designGate"] = gate
    record = advisory_record(vision, gate["pageDigests"])
    record["savedPageReviewEvidence"] = seal_saved_page_evidence(slides,
        prepared["snapshot"], gate, runtime=prepared["runtime"])
    publication = dict(schema="svg-quality-recovery-publication/v2", gateDigest=gate["digest"],
        parentOperationId=prepared["publication"]["parentOperationId"],
        parentJobId=prepared["publication"]["parentJobId"],
        sourceGenerationRevision=prepared["sourceGenerationRevision"], sourceRevision=claim.expected_revision,
        reviewOperationId=str(claim.operation_id), ownerScope=prepared["ownerScope"],
        snapshotDigest=gate["snapshotDigest"],
        sourceContentDigests=[content_digest((slide.content or {}).get("source")) for slide in slides],
        inputDigests=[payload.get("input_digest") for payload, _ in prepared["pages"]])
    return record, {**publication, "digest": content_digest(publication)}
