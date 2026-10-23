"""Publish a verified SVG deck: source-preservation receipt check, slide
persistence, and the ordered presentation-revision handoff.

Moved out of ``services/svg_presentation_publish.py`` into the SVG driver
package (P0 split, see ``docs/generation-core-architecture-20260925.md``).
``services/svg_presentation_publish.py`` stays as a one-line facade re-export
for importers and tests; ``services/svg_presentation_service.py`` re-exports
``_publish`` from there in turn.
"""
from __future__ import annotations

import asyncio
import uuid
from collections.abc import Mapping

from sqlalchemy import delete, select

from models.presentation_and_path import PresentationPathAndEditPath
from copy import deepcopy
from models.sql.presentation import PresentationModel, PRESENTATION_LIFECYCLE_PUBLISHED
from models.sql.slide import SlideModel
from services.authored_canvas import merge_presentation_canvas, request_canvas_metadata
from services.authored_checkpoint_service import begin_publish, complete_publish
from services.authored_presentation_artifacts import _save_slide_pngs, authored_title
from services.generation_core.drivers.svg.native_declarations import PAYLOAD_KEY as NATIVE_DECLARATIONS_KEY
from services.generation_core.drivers.svg.native_publish import canvas_point_per_px, page_native_objects
from services.generation_core.native_objects import SLIDE_CONTENT_KEY as NATIVE_OBJECTS_KEY
from services.generation_core.quality.flag_classification import FAILURE_PLACEHOLDER_OUTCOME
from services.generation_core.quality.page_outcome import review_page_indices
from services.svg_presentation_checkpoint import _save_source_page_pngs
from services.svg_page_publication import set_svg_page


def svg_slide_content(payload: dict, *, role, source, pt_per_px: float,
                      source_native_objects=None, source_native_summary=None) -> dict:
    """``slide.content`` of one published page: receipt, asset manifest, native objects.

    The page's raw native declarations never reach the receipt; only the
    publication summary (``native_publish.page_native_objects``) does.
    """
    from services.svg_cached_author_render_recovery import approved_sidecars
    sidecars = approved_sidecars(payload)
    native_objects, native_summary = ((source_native_objects, source_native_summary)
        if source_native_summary is not None else page_native_objects(
            payload, pt_per_px=pt_per_px,
            verified_fixed_axis_migrations=sidecars))
    preservation = payload.get("source_preservation", {})
    if preservation is None:
        preservation = {}
    source_charts = (preservation.get("whole_page_native_groups") or []) + (preservation.get("source_native_charts") or [])
    if source_charts:
        from services.generation_core.native_objects import NativeObjects
        from services.svg_source_charts import chart_semantics
        from services.source_content.digests import digest_json

        projected, projected_summary = page_native_objects(
            payload, pt_per_px=pt_per_px,
            verified_fixed_axis_migrations=sidecars)
        required = {item["element_id"] for item in source_charts}
        charts = tuple(item for item in NativeObjects.from_wire(projected).objects
                       if item.kind == "chart" and item.id in required) if projected else ()
        if {item.id for item in charts} != required:
            raise ValueError("svg_source_native_chart_unprojectable")
        expected_numeric = {item["element_id"]: item["numeric_digest"]
                            for item in preservation.get("source_native_charts") or []}
        if any(item.id in expected_numeric and digest_json(chart_semantics(item.payload)) != expected_numeric[item.id]
               for item in charts):
            raise ValueError("svg_source_chart_numeric_mismatch")
        tables = NativeObjects.from_wire(source_native_objects).objects if source_native_objects else ()
        native_objects = NativeObjects((*tables, *charts)).to_wire()
        source_summary = source_native_summary or {"declared": 0, "accepted": 0, "dropped": []}
        native_summary = {**source_summary,
            "declared": source_summary["declared"] + len(required),
            "accepted": len(tables) + len(charts),
            "whole_page_native_charts": projected_summary}
    receipt = {key: value for key, value in payload.items()
               if key not in {"svg_content", "attempts", "svgAssetManifest", "deck_visual_intents",
                              NATIVE_DECLARATIONS_KEY, "cached_author_render_archive",
                              "cached_author_render"}}
    if native_summary:
        receipt["nativeObjects"] = native_summary
    return {"__svg__": True, "role": role, "source": source,
            **({"svgAssetManifest": payload["svgAssetManifest"]} if payload.get("svgAssetManifest") else {}),
            **({NATIVE_OBJECTS_KEY: native_objects} if native_objects else {}),
            "svgReceipt": receipt}


async def _publish(request, presentation_id, outline, language, session, job, snapshot, roles, pages,
                   *, quality_summary=None, advisory=None):
    from services.svg_authoring_profile import read_quality_recovery_policy
    from services.generation_core.drivers.svg import quality as deck_quality
    # A review flag a page carries in is this run's own stage review
    # (collect._flag_stage_review re-derives it from the persisted stage
    # record on every run, and classify_pages already classified it): it is
    # kept, and publication's verdicts merge onto it. Publication's own flags
    # are never checkpointed, so none can arrive stale from an earlier attempt.
    reclassify = False
    design_review = (deck_quality.apply_quality_recovery_publication(snapshot, pages, advisory, job=job)
                     if read_quality_recovery_policy(snapshot) else None)
    # Producer evidence binds publication only to a current, intact gate
    # (one that judged no page included: its record stays the deck's evidence).
    gate = advisory["vision"]["designGate"] if (design_review or {}).get("gateDigest") else None
    reclassify = reclassify or bool((design_review or {}).get("flaggedPages"))
    source_preservation = None
    if getattr(request, "_source_content_svg_reflow", False):
        from services.svg_source_layout import source_pages, verify_source_receipt
        manifest = request._source_content_manifest
        if len(source_pages(manifest, chart_contract=(snapshot or {}).get("source_chart_contract"))) != len(pages):
            raise ValueError("svg_source_page_count_mismatch")
        for index, (payload, _) in enumerate(pages):
            if payload.get("cached_author_render"):
                from services.svg_page_publication import validate_readability_publication
                policy = payload.get("readability_policy") or {}
                if (policy.get("version") != "svg-physical-readability/v2"
                        or policy.get("quality_target_font_pt", 0) < 9.5):
                    raise ValueError("svg_cached_author_render_quality_policy_invalid")
                validate_readability_publication(policy, payload.get("quality"))
            terminal = payload.get("terminal")
            if isinstance(terminal, Mapping) and terminal.get("remakeOutcome") == FAILURE_PLACEHOLDER_OUTCOME:
                continue  # not the source page: it claims no source receipt, and is flagged
            if (payload.get("source_preservation") or {}).get("source_chart_contract") != (snapshot or {}).get("source_chart_contract"):
                raise ValueError("svg_source_chart_snapshot_mismatch")
            verify_source_receipt(payload["svg_content"], payload.get("source_preservation"), manifest, index)
            if terminal:
                continue  # flag-only: verbatim (checked above), flagged, reviewed as unverified
            # A residual measured failure publishes flagged by classification.
            if payload["source_preservation"].get("rendering_verified") is not True:
                deck_quality.flag_source_visibility(payload, index)
                reclassify = True
        source_preservation = {"schemaVersion": "svg-source-preservation/v1",
            "manifestDigest": manifest.manifest_digest, "pageCount": len(pages)}
    # Flags added above are classified exactly as the deck pass classifies.
    if reclassify:
        quality_summary = deck_quality.reclassify_pages(pages, summary=quality_summary)
    # Decided here so each flag names the row it was generated for.
    slide_ids = [uuid.uuid4() for _ in pages]
    flagged_slides = [({**entry, "slideId": str(slide_ids[entry["index"]])}
                       if type(entry.get("index")) is int and 0 <= entry["index"] < len(slide_ids) else entry)
                      for entry in (quality_summary or {}).get("flaggedSlides", [])]
    from services.account_deletion_service import require_active_account_generation
    from services.compatibility_migration.admission import stamp_new_svg_presentation
    from services.presentation_publication_service import publish_presentation_revision
    from utils.owner_context import get_current_owner_id

    owner = get_current_owner_id()
    if owner is None:
        raise RuntimeError("svg_owner_required")
    job_id = job.id if job else None
    attempt_token = job.attempt_token if job else None
    operation_id = job.workspace_operation_id if job else None
    fencing_token = getattr(job, "_workspace_fencing_token", None) if job else None
    await session.rollback()  # release read transaction before ordered publication locks
    await require_active_account_generation(session, user_id=owner)
    if job:
        await begin_publish(session, job_id=job_id, attempt_token=attempt_token,
                            fencing_token=fencing_token)
    presentation = await session.scalar(select(PresentationModel).where(
        PresentationModel.id == presentation_id, PresentationModel.owner_id == owner).with_for_update())
    if presentation is None:
        presentation = PresentationModel(id=presentation_id, owner_id=owner, content=request.content,
                                         n_slides=len(pages), language=language or "ko")
        session.add(presentation)
        await session.flush()
    expected_revision = presentation.revision
    if presentation.mode not in (None, "authored", "svg") or (expected_revision > 0 and not presentation.is_svg()):
        raise RuntimeError("svg_document_mode_conflict")
    if source_preservation:
        image_refs = await asyncio.to_thread(_save_source_page_pngs, presentation_id, pages, attempt_token)
    else:
        image_refs = await asyncio.to_thread(_save_slide_pngs, presentation_id,
                                             [png for _, png in pages], attempt_token)
    stamp_new_svg_presentation(presentation)
    presentation.title = authored_title(request, outline)
    presentation.n_slides = len(pages)
    presentation.outlines = outline.model_dump(mode="json")
    presentation.content = request.content
    presentation.instructions = request.instructions
    presentation.language = language or "ko"
    presentation.layout = None
    presentation.structure = None
    presentation.reference_snapshot_ids = [str(x) for x in request.reference_snapshot_ids] or None
    quality = [payload["quality"] for payload, _ in pages]
    initial_quality = [payload.get("initial_quality", payload["quality"]) for payload, _ in pages]
    review = review_page_indices(quality)
    # Non-blocking; a cheap sum over evidence already computed per page.
    render_advisory_count = sum(len(payload.get("rasterRenderAdvisories", [])) for payload, _ in pages)
    presentation.theme = merge_presentation_canvas({
        **(presentation.theme or {}), "mode": "svg", "style": request.authored_style,
        **({"commonHeaderDesign": request.common_header_design.model_dump(mode="json")}
           if getattr(request, "common_header_design", None) is not None else {}),
        "svg": {"schemaVersion": "svg-document/v1", "styleSnapshot": snapshot,
                **({"sourcePreservation": source_preservation} if source_preservation else {}),
                "qualityStatus": "review_required" if review else "passed",
                "reviewPages": review, "quality": quality,
                **({"qualitySummary": {key: value for key, value in quality_summary.items()
                                       if key != "flaggedSlides"},
                    "flaggedSlides": flagged_slides}
                   if quality_summary else {}),
                **({"advisory": advisory} if advisory else {}),
                **({"designReview": design_review} if design_review else {}),
                "initialQuality": initial_quality,
                **({"renderAdvisoryCount": render_advisory_count} if render_advisory_count else {}),
                "codexGenerationPreset": request.codex_generation_preset},
    }, request_canvas_metadata(request))
    if gate is not None:
        from services.generation_core.quality.advisory_record import content_digest
        publication = {"schema": "svg-quality-recovery-publication/v1", "gateDigest": gate["digest"],
            "parentOperationId": str(operation_id), "parentJobId": str(job_id),
            "sourceGenerationRevision": expected_revision + 1,
            "sourceRevision": expected_revision + 1, "snapshotDigest": content_digest(snapshot),
            "sourceContentDigests": [content_digest(item.content) for item in outline.slides],
            "inputDigests": [payload.get("input_digest") for payload, _ in pages]}
        presentation.theme["svg"]["qualityRecoveryPublication"] = {**publication,
            "digest": content_digest(publication)}
    presentation.lifecycle_status = PRESENTATION_LIFECYCLE_PUBLISHED
    pt_per_px = canvas_point_per_px(request_canvas_metadata(request))
    await session.execute(delete(SlideModel).where(SlideModel.presentation == presentation_id))
    published_slides = []
    for index, (payload, png) in enumerate(pages):
        native_kwargs = {}
        if source_preservation and (payload.get("source_preservation") or {}).get("native_tables"):
            from services.svg_source_native_tables import source_table_native_objects
            native_objects, native_summary = source_table_native_objects(payload["svg_content"],
                payload["source_preservation"], manifest, index, pt_per_px=pt_per_px)
            native_kwargs = dict(source_native_objects=native_objects, source_native_summary=native_summary)
        slide_content = svg_slide_content(payload, role=roles[index], source=outline.slides[index].content,
                                          pt_per_px=pt_per_px, **native_kwargs)
        fields = {}
        set_svg_page(fields, svg_content=payload["svg_content"], content=slide_content, png=png)
        saved_slide = SlideModel(id=slide_ids[index], presentation=presentation_id, owner_id=owner,
                              layout_group="svg", layout="svg:content", index=index,
                              **fields, source_format="svg",
                              properties={"image": image_refs[index]}, speaker_note=outline.slides[index].content)
        published_slides.append(saved_slide)
        session.add(saved_slide)
    if gate is not None:
        from services.svg_quality_recovery_review import seal_saved_page_evidence
        from services.svg_export_visual_review import split_page_advisories, slide_page_digests
        from models.sql.workspace import WorkspaceOperation
        parent = await session.get(WorkspaceOperation, operation_id)
        if parent is None or parent.owner_id != owner or parent.presentation_id != presentation_id:
            raise ValueError("svg_quality_recovery_review_binding_missing")
        # The ordered queries above may autoflush the initial theme. Mutate a
        # new JSON value so the final producer evidence remains dirty afterward.
        publication_theme = deepcopy(presentation.theme)
        svg_theme = publication_theme["svg"]
        records = split_page_advisories(advisory["vision"], published_slides, range(len(published_slides)))
        svg_theme["pageAdvisories"] = records
        svg_theme["advisory"]["pageAdvisories"] = records
        binding = dict(schemaVersion="svg-visual-review-operation-binding/v1",
            presentationId=str(presentation_id), ownerId=str(owner), ownerScope=str(parent.owner_scope),
            sourceRevision=expected_revision + 1, operationId=str(operation_id),
            reviewedPageIndices=list(range(len(published_slides))), sourceDigests=slide_page_digests(published_slides),
            pngDigests=[(slide.content["svgReceipt"]).get("png_sha256") for slide in published_slides],
            pageRecordDigests=[record["digest"] for record in records])
        svg_theme["advisory"]["operationBinding"] = {**binding, "digest": content_digest(binding)}
        svg_theme["advisory"]["savedPageReviewEvidence"] = seal_saved_page_evidence(
            published_slides, snapshot, gate)
        presentation.theme = publication_theme
    session.add(presentation)
    await session.flush()
    if job:
        # Never fails publication: an absent snapshot keeps the legacy export proof.
        from services.svg_source_input_snapshot import publish_source_input_snapshot
        await publish_source_input_snapshot(session, request=request, job=job, presentation_id=presentation_id,
            owner_id=owner, result_revision=expected_revision + 1, published_slides=published_slides)
    # complete_publish still requires the running operation; terminalize the
    # public operation only after its linked job transition in this transaction.
    if job:
        await complete_publish(session, job_id=job_id, attempt_token=attempt_token,
                               fencing_token=fencing_token)
    result = {"presentation_id": str(presentation_id), "source_format": "svg",
              "quality_status": "review_required" if review else "passed",
              "quality_pages": {"passed": len(pages) - len(review), "flagged": len(review)},
              "edit_path": f"/presentation?id={presentation_id}", "path": ""}
    await publish_presentation_revision(session, presentation_id=presentation_id,
                                        expected_revision=expected_revision, operation_id=operation_id,
                                        attempt_token=attempt_token, result=result,
                                        operation_kind="presentation.generate",
                                        source_manifest=manifest if source_preservation else None)
    await session.commit()
    return PresentationPathAndEditPath(presentation_id=presentation_id, path="",
                                       edit_path=f"/presentation?id={presentation_id}")


async def svg_completion_status(session, presentation_id):
    """``(message, quality_gate_status, page_counts)`` for a published SVG deck.

    Read back from what publication recorded, so the generation status can never
    claim a verdict the deck does not carry.
    """
    theme = await session.scalar(select(PresentationModel.theme).where(PresentationModel.id == presentation_id))
    svg = (theme or {}).get("svg") or {}
    review = list(svg.get("reviewPages") or [])
    total = len(svg.get("quality") or [])
    pages = {"passed": total - len(review), "flagged": len(review)}
    status = "review_required" if review else "passed"
    detail = ("preservation verified" if svg.get("sourcePreservation") and not review
              else f"{pages['passed']} passed, {pages['flagged']} flagged for review")
    return f"SVG presentation generation completed; {detail}", status, pages
