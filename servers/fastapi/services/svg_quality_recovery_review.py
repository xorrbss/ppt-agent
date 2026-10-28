"""Exact saved-page evidence for the one-call, owner-bound edited SVG review."""
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from services.generation_core.quality.advisory_record import content_digest
from services.generation_core.quality.review_coverage import review_coverage
from services.generation_core.quality.vision_advisory_prompt import (
    DESIGN_LOCAL_AXES, DESIGN_PRINCIPLES_VERSION, baseline_review_active, is_design_review_version,
    review_prompt_version,
)
from utils.hashing import sha256_hex


#: Every saved-review runtime sealed before 2026-10-28 carried this label,
#: whatever prompt the review it sealed had run.
LEGACY_SAVED_REVIEW_PROMPT_LABEL = "svg-quality-first-design-review/v1"


def saved_review_runtime():
    """The reviewer code identity: renderer, fonts, worker and reviewer modules.

    It names no prompt: one reviewer emits several design-review prompt
    versions, and a sealed runtime records the one its review ran
    (``labelled_review_runtime``).
    """
    from services import svg_renderer
    from services.svg_trusted_fonts import FONT_DIRECTORY, BUNDLED_FACES
    from utils.svg_template_fonts import FONT_PATH
    from services.public_render_runtime_identity import public_render_runtime_binding
    from services.generation_core.quality import vision_advisory, vision_advisory_prompt
    return dict(rendererVersion=svg_renderer.SVG_RENDERER_VERSION,
        rendererDigest=sha256_hex(Path(svg_renderer.__file__).read_bytes()),
        fontDigests=[sha256_hex(path.read_bytes()) for path in
            [FONT_PATH, *[FONT_DIRECTORY / filename for _, _, filename in BUNDLED_FACES]]],
        worker=public_render_runtime_binding(),
        reviewerDigests=[sha256_hex(Path(module.__file__).read_bytes())
                         for module in (vision_advisory, vision_advisory_prompt)])


def labelled_review_runtime(code, prompt_version):
    """The runtime a review seals: its reviewer code and the prompt version it ran."""
    if not is_design_review_version(prompt_version):
        raise ValueError("svg_quality_review_prompt_version_invalid")
    return {**code, "promptVersion": prompt_version}


def saved_review_runtime_identity(runtime, prompt_version, *, code=None):
    """Which one runtime identity a sealed runtime is for a review that ran ``prompt_version``.

    ``"true"`` when it records that version, ``"legacy"`` when it carries the
    pre-2026-10-28 constant label for a review that ran another version; any
    other label is neither (None). The two coincide for a v1 review, which is
    therefore only ``"true"``. With ``code`` (``saved_review_runtime()``)
    every other runtime field must equal it exactly.
    """
    if not isinstance(runtime, Mapping) or not is_design_review_version(prompt_version):
        return None
    label = runtime.get("promptVersion")
    identity = ("true" if label == prompt_version
                else "legacy" if label == LEGACY_SAVED_REVIEW_PROMPT_LABEL else None)
    if identity is None or code is not None and {
            key: value for key, value in runtime.items() if key != "promptVersion"} != dict(code):
        return None
    return identity


def saved_page_render_input(slide):
    content = slide.content or {}
    receipt = content.get("svgReceipt") or {}
    # Capture location and previous review operation are not rendering inputs,
    # and neither is a review's verdict on the page: the flag a review writes
    # and the classification it derives must not invalidate the evidence that
    # review sealed (or a header revision's proof of the same bytes).
    evidence = {key: value for key, value in receipt.items() if key not in {
        "png_path", "qualityRecoveryReview", "quality_recovery_execution", "quality_recovery",
        "raw_response", "raw_response_sha256", "remake", "review_flag", "flag"}}
    quality = dict(evidence.get("quality") or {})
    for key in ("compositionObservation", "classification", "verified"):
        quality.pop(key, None)
    evidence["quality"] = quality
    return dict(svg=slide.svg_content, source=content.get("source"),
        role=content.get("role"), assets=content.get("svgAssetManifest"),
        native={key: value for key, value in content.items() if key not in {
            "svgReceipt", "source", "role", "svgAssetManifest", "__svg__"}},
        evidence=evidence)


def saved_page_digest(slide, snapshot, runtime, binding):
    return content_digest(dict(**saved_page_render_input(slide), snapshot=snapshot, runtime=runtime, binding=binding))


def seal_saved_page_evidence(slides, snapshot, gate, *, runtime):
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


def _measurement_current(payload, snapshot):
    """The saved page's measurement and source receipt still describe its bytes.

    Only staleness refuses a review; a page whose measurement failed, or whose
    source readback did not verify it, is reviewed with its mandatory evidence
    invalid (``_measurement_valid``) and stays flagged. A failure placeholder is
    not the source page and claims no source receipt.
    """
    from services.generation_core.drivers.svg.quality import _failure_placeholder, _source_preservation_intact
    from services.svg_contract import source_digest
    quality = payload.get("quality") or {}
    png = payload.get("png_sha256")
    return (isinstance(png, str) and len(png) == 64 and bool(payload.get("svg_content"))
            and quality.get("measurement_complete") is True
            and quality.get("source_digest") == source_digest(payload["svg_content"])
            and quality.get("png_digest") == "sha256:" + png
            and (_failure_placeholder(payload)
                 or _source_preservation_intact(payload, snapshot=snapshot, require_rendering=False)))


async def generation_source_parent(session, presentation):
    """The closed generation that published this deck, as a review parent.

    For a deck publication left without ``qualityRecoveryPublication`` (no
    current design review to bind): the identity alone, like
    ``header_revision_source_parent``; ``require_quality_recovery_source_parent``
    then proves its lineage exactly as for a recorded publication.
    """
    from sqlalchemy import select
    from models.sql.presentation_generation_job import PresentationGenerationJob
    from models.sql.workspace import WorkspaceOperation
    rows = (await session.execute(select(PresentationGenerationJob, WorkspaceOperation).join(
        WorkspaceOperation, WorkspaceOperation.id == PresentationGenerationJob.workspace_operation_id).where(
            PresentationGenerationJob.presentation_id == presentation.id,
            PresentationGenerationJob.state == "published",
            WorkspaceOperation.presentation_id == presentation.id,
            WorkspaceOperation.owner_id == presentation.owner_id,
            WorkspaceOperation.kind == "presentation.generate",
            WorkspaceOperation.state == "succeeded",
            WorkspaceOperation.result_revision <= presentation.revision))).all()
    revisions = [operation.result_revision for _, operation in rows if type(operation.result_revision) is int]
    latest = [(job, operation) for job, operation in rows if revisions and operation.result_revision == max(revisions)]
    if len(latest) != 1:
        raise ValueError("svg_quality_recovery_review_parent_invalid")
    job, operation = latest[0]
    parent = dict(parentOperationId=str(operation.id), parentJobId=str(job.id),
        sourceGenerationRevision=operation.result_revision, sourceRevision=operation.result_revision)
    return {**parent, "digest": content_digest(parent)}


def prepare_saved_review(presentation, slides, indices, pngs, *, operation_id, owner_scope,
                         source_parent_authority=None, source_parent=None):
    from services.svg_authoring_profile import read_quality_recovery_plan
    from services.generation_core.drivers.svg.quality import _design_context, _gate_intact, _render_resolution
    from utils.owner_context import get_current_owner_id, get_current_tenant_id
    meta = (presentation.theme or {}).get("svg") or {}
    snapshot = meta.get("styleSnapshot") or {}
    from services.svg_document_mutations import validate_saved_template_pages
    validate_saved_template_pages(snapshot, slides)
    publication = meta.get("qualityRecoveryPublication") or source_parent or {}
    if source_parent_authority is not None:
        from services.svg_export_operation import header_revision_source_parent
        publication = header_revision_source_parent(source_parent_authority, presentation, slides)
    parent_revision = publication.get("sourceGenerationRevision", publication.get("sourceRevision"))
    if (type(parent_revision) is not int or parent_revision > presentation.revision
            or not publication.get("parentOperationId") or not publication.get("parentJobId")
            or publication.get("digest") != content_digest({key: value for key, value in publication.items() if key != "digest"})):
        raise ValueError("svg_quality_recovery_review_parent_invalid")
    code = saved_review_runtime()
    binding = dict(presentationId=str(presentation.id), ownerId=str(get_current_owner_id()),
        tenantId=str(get_current_tenant_id()), jobId=publication["parentJobId"])
    if binding["ownerId"] != str(presentation.owner_id):
        raise ValueError("svg_quality_review_owner_invalid")
    pages = [({**deepcopy((slide.content or {}).get("svgReceipt") or {}),
               "svg_content": slide.svg_content}, png) for slide, png in zip(slides, pngs, strict=True)]
    if any(not _measurement_current(payload, snapshot) for payload, _ in pages):
        raise ValueError("svg_quality_review_measurement_stale")
    canvas = snapshot.get("canvas") or {}
    if any(not (resolution := _render_resolution(png, canvas)) or not resolution["sufficient"] for png in pngs):
        raise ValueError("svg_quality_review_resolution_insufficient")
    prior_gate = _gate_intact(meta.get("advisory"))
    saved = (meta.get("advisory") or {}).get("savedPageReviewEvidence") or {}
    prior_version = ((meta.get("advisory") or {}).get("vision") or {}).get("promptVersion")
    reusable = {}
    # The prior review's evidence is reusable under this reviewer code
    # whichever prompt that review ran.
    if (prior_gate is not None and saved.get("schema") == "svg-saved-page-review-evidence/v1"
            and saved.get("gateDigest") == prior_gate["digest"]
            and saved.get("digest") == content_digest({key: value for key, value in saved.items() if key != "digest"})
            and saved.get("binding") == binding
            and saved_review_runtime_identity(saved.get("runtime"), prior_version, code=code) is not None
            and code["worker"].get("checkpointReuseEligible") is True
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
                    and page.get("inputDigest") == saved_page_digest(
                        slides[index], snapshot, saved["runtime"], binding)):
                reusable[index] = deepcopy(page)
    # Explicit subset pages refresh locally; every deck page refreshes globally.
    changed = sorted(set(indices) | (set(range(len(slides))) - set(reusable)))
    plan = read_quality_recovery_plan(snapshot)
    if not plan:
        raise ValueError("svg_quality_review_single_call_capacity_exceeded")
    job = SimpleNamespace(id=publication["parentJobId"], workspace_operation_id=operation_id,
                          presentation_id=presentation.id)
    header_pages = None
    if snapshot.get("common_header") is not None:
        from services.svg_common_header import validate_common_header_outline
        outline = presentation.outlines
        if (not isinstance(outline, dict) or not isinstance(outline.get("slides"), list)
                or len(outline["slides"]) != len(slides)):
            raise ValueError("svg_common_header_review_binding_missing")
        validate_common_header_outline(snapshot, outline)
        if any(page.get("content") != (slide.content or {}).get("source")
               for page, slide in zip(outline["slides"], slides, strict=True)):
            raise ValueError("svg_common_header_review_source_mismatch")
        header_pages = [deepcopy(page["common_header"]) for page in outline["slides"]]
    ctx = SimpleNamespace(snapshot=snapshot, count=len(slides),
        roles=[(slide.content or {}).get("role") or "content" for slide in slides],
        quality_review_contents=[(slide.content or {}).get("source") for slide in slides],
        header_pages=header_pages)
    context = _design_context(ctx, pages, job=job, read_png=lambda payload: None,
                              review_principles_version=DESIGN_PRINCIPLES_VERSION, measurement_evidence=True)
    baseline = baseline_review_active(context)
    capacity = plan["slide_count"] if baseline else plan["review_group_size"]
    if len(changed) > capacity or baseline and plan["slide_count"] != len(slides):
        raise ValueError("svg_quality_review_single_call_capacity_exceeded")
    context.update(reviewScope="full" if baseline else "edited", deckComplete=True, changedPageIndices=changed,
                   representativeIndices=[], priorCandidates={})
    # The scope fixes the prompt this review runs, so its runtime binding is
    # set only now.
    runtime = labelled_review_runtime(code, review_prompt_version(context))
    context["runtimeBinding"] = content_digest(runtime)
    return dict(context=context, snapshot=snapshot, pages=pages, pngs=pngs, changed=changed,
        reusable=reusable, runtime=runtime, publication=publication,
        currentInputDigests=[saved_page_digest(slide, snapshot, runtime, binding) for slide in slides],
        sourceGenerationRevision=parent_revision, ownerScope=owner_scope)


def seal_saved_review(prepared, slides, vision, claim):
    from services.generation_core.drivers.svg.quality import _seal_design_gate
    from services.generation_core.quality.advisory_record import advisory_record
    review = review_coverage(vision).design_review
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
    if baseline_review_active(prepared["context"]):
        # The v2 parser validates every current page on all five axes.
        vision = {**vision, "designReview": deepcopy(review)}
    else:
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


def apply_saved_review_flags(slides, gate):
    """Replace each page's design verdict with this review's and re-classify the deck.

    Every page the gate judged gets publication's verdict for it
    (``drivers/svg/quality._design_flag``): a passed page loses its design
    reasons, a blocking or unreviewed page is flagged with the new ones. Other
    reasons and the terminal record stay. A review that judged no page
    (``gate_design_verdicts`` is None) changes no page's verdict. Classification
    then runs over every page exactly as publication runs it
    (``reclassify_pages``) and each slide whose receipt changed gets the new
    receipt. Returns those slide positions and the deck's ``designReview``
    (``gate_design_review``).
    """
    from services.generation_core.drivers.svg import quality as deck_quality
    from services.svg_page_publication import REVIEW_FLAG_KEY, replace_design_verdict
    payloads = [{**deepcopy((slide.content or {}).get("svgReceipt") or {}), "svg_content": slide.svg_content}
                for slide in slides]
    verdicts = deck_quality.gate_design_verdicts(gate, payloads)
    design_review = deck_quality.gate_design_review(gate, verdicts)
    if verdicts is None:
        return [], design_review
    pages = []
    for payload, verdict in zip(payloads, verdicts, strict=True):
        flag = replace_design_verdict(payload.get(REVIEW_FLAG_KEY), verdict)
        if flag is None:
            payload.pop(REVIEW_FLAG_KEY, None)
        else:
            payload[REVIEW_FLAG_KEY] = flag
        payload.pop("flag", None)  # classification's copy; re-derived below
        pages.append((payload, None))
    deck_quality.reclassify_pages(pages)
    changed = []
    for index, (slide, (payload, _)) in enumerate(zip(slides, pages, strict=True)):
        receipt = {key: value for key, value in payload.items() if key != "svg_content"}
        if receipt != ((slide.content or {}).get("svgReceipt") or {}):
            slide.content = {**deepcopy(slide.content or {}), "svgReceipt": receipt}
            changed.append(index)
    return changed, design_review
