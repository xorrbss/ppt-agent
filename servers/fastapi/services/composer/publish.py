"""Publish a Composer deck the way the authored generation lane publishes one.

The persistence mirrors ``ProductDeckIrExecutionAdapter._publish``: one
``SlideModel`` per slide with ``layout_group="authored"`` and the rendered
HTML, a content-addressed PNG set, the immutable semantic bundle graph via
``publish_authored_semantic_bundle_graph`` and the presentation flipped to
``mode="authored"`` / published.  The Composer runs inside the ordinary
generation lane, whose handler demands the generation's own durable
publication CAS, so the terminal operation tuple is completed here through
the workspace artifact helper exactly as ``authored_presentation_publication``
does (see ``services.composer.publish_cas``).  Outside the workspace lane the
presentation-only CAS and the compatibility job row are completed instead.
Export is a separate operation exactly as for Deck IR decks.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from pydantic import ValidationError

from models.sql.presentation import (
    PRESENTATION_LIFECYCLE_PUBLISHED,
    PRESENTATION_LIFECYCLE_STAGING,
    PresentationModel,
)
from models.sql.slide import SlideModel
from services.composer.plan import ComposerDeckPlan, ComposerSlideResult
from services.composer.publish_cas import (
    FENCE_LOST_CODE,
    compensate_artifact,
    fence_inputs,
    publish_workspace_artifact,
    require_operation_fence,
    stage_workspace_artifact,
)
from services.composer.fonts import composer_publication_font_materials
from services.composer.publish_semantic import semantic_draft_from_html
from services.composer.receipt import ComposerPipelineError, ComposerPublicationError

LOGGER = logging.getLogger(__name__)

PUBLICATION_ROUTE = "composer"
AUTHORED_MODE = "authored"


def _edit_path(presentation_id: uuid.UUID) -> str:
    return f"/presentation?id={presentation_id}"


def _outline_entries(results: Sequence[ComposerSlideResult], deck: ComposerDeckPlan) -> dict[str, Any]:
    plans = {plan.slide_index: plan for plan in deck.slides}
    entries = []
    for result in results:
        plan = plans.get(result.slide_index)
        title = ""
        if plan is not None and plan.title is not None and plan.title.paragraphs:
            title = " ".join(plan.title.paragraphs[0].text.split())
        entries.append({"content": title or (plan.source_unit_id if plan is not None else "")})
    return {"slides": entries}


def _slide_rows(
    *,
    owner_id: uuid.UUID,
    presentation_id: uuid.UUID,
    results: Sequence[ComposerSlideResult],
    deck: ComposerDeckPlan,
    image_refs: Sequence[str],
    publication_route: str,
) -> list[SlideModel]:
    plans = {plan.slide_index: plan for plan in deck.slides}
    rows = []
    for index, result in enumerate(results):
        plan = plans.get(result.slide_index)
        unit_id = plan.source_unit_id if plan is not None else str(index)
        rows.append(
            SlideModel(
                owner_id=owner_id,
                presentation=presentation_id,
                layout_group=AUTHORED_MODE,
                layout=f"{AUTHORED_MODE}:{publication_route}:{unit_id}",
                index=index,
                content={
                    "__authored__": True,
                    "image": image_refs[index],
                    "role": publication_route,
                    "sourceUnitId": unit_id,
                    "composer": {
                        "rung": result.receipt.rung,
                        "archetype": plan.archetype if plan is not None else None,
                        "renders": result.receipt.renders,
                    },
                },
                html_content=result.html,
                properties={"image": image_refs[index]},
            )
        )
    return rows


def _completed_status(
    async_status: Any, *, presentation_id: uuid.UUID, path: str, publication: Mapping[str, Any]
) -> None:
    edit_path = _edit_path(presentation_id)
    async_status.status = "completed"
    async_status.message = "Composer generation completed"
    async_status.data = {
        "presentation_id": str(presentation_id),
        "path": path,
        "edit_path": edit_path,
        "publication": dict(publication),
    }
    async_status.error = None
    async_status.updated_at = datetime.now(timezone.utc)


async def publish_composer_deck(
    *,
    sql_session: Any,
    request: Any,
    presentation_id: uuid.UUID,
    generation_job: Any,
    async_status: Any,
    htmls: Sequence[str],
    pngs: Sequence[bytes],
    results: Sequence[ComposerSlideResult],
    deck: ComposerDeckPlan,
    receipt: Mapping[str, Any] | None = None,
    publication_route: str = PUBLICATION_ROUTE,
) -> dict[str, Any]:
    """Persist and publish an accepted Composer deck; commits the caller session."""

    # Lazy: these modules pull the authored runtime and the settings registry.
    from services.authored_checkpoint_errors import AuthoredStageError
    from services.authored_checkpoint_service import begin_publish, complete_publish
    from services.authored_presentation_artifacts import (
        _remove_partial_slide_pngs,
        _save_slide_pngs,
    )
    from services.authored_vnext.semantic.canonical import SemanticContractError, canonical_digest
    from services.authored_vnext.semantic.publication import publish_authored_semantic_bundle_graph
    from services.presentation_publication_service import (
        PresentationPublicationConflict,
        publish_presentation_revision,
    )

    count = len(results)
    if count == 0 or len(htmls) != count or len(pngs) != count:
        raise ComposerPublicationError("composer_publication_cardinality_mismatch")
    if any(html != result.html for html, result in zip(htmls, results)):
        raise ComposerPublicationError("composer_publication_final_html_mismatch")
    from services.composer.canvas_identity import bind_canvas_identity, publication_canvas_metadata

    try:
        canvas_metadata = publication_canvas_metadata(request, deck.canvas)
    except ValueError as error:
        raise ComposerPublicationError(str(error)) from error
    fit_revision = getattr(deck, "fit_policy_revision", None)
    if fit_revision == "composer-source-fit/v2" and any(
        'data-source-fit-policy="composer-source-fit/v2"' not in html for html in htmls
    ):
        raise ComposerPublicationError("composer_publication_fit_policy_mismatch")
    presentation = await sql_session.get(PresentationModel, presentation_id)
    if presentation is None:
        raise ComposerPublicationError("composer_presentation_missing")
    owner_id = presentation.owner_id
    if owner_id is None:
        raise ComposerPublicationError("authored_semantic_owner_required")
    if presentation.lifecycle_status != PRESENTATION_LIFECYCLE_STAGING:
        raise ComposerPublicationError("composer_presentation_not_staging")
    existing = await sql_session.scalar(
        select(SlideModel.id).where(SlideModel.presentation == presentation_id).limit(1)
    )
    if existing is not None:
        raise ComposerPublicationError("composer_presentation_not_empty")
    expected_revision = int(presentation.revision)
    fence = fence_inputs(request, generation_job)
    await require_operation_fence(sql_session, fence=fence, expected_revision=expected_revision)
    title = deck.tokens.deck_title or presentation.title or "Presentation"
    image_token = f"{publication_route}:{fence.attempt_token}" if fence.attempt_token else None
    png_list = list(pngs)
    images_saved = False
    artifact = None
    artifact_handed_to_publish = False
    try:
        # Reserve and materialize the durable artifact before begin_publish
        # takes admission/operation/job locks (authored order: the intent uses
        # an independent session and the account fence must come first).
        artifact = await stage_workspace_artifact(
            sql_session,
            fence=fence,
            request=request,
            presentation_id=presentation_id,
            pngs=png_list,
            title=title,
            expected_revision=expected_revision,
        )
        image_refs = await asyncio.to_thread(_save_slide_pngs, presentation_id, png_list, image_token)
        images_saved = True
        if fence.has_job_claim:
            await begin_publish(
                sql_session, job_id=fence.job_id, attempt_token=fence.attempt_token, fencing_token=fence.fencing_token
            )
        slides = _slide_rows(
            owner_id=owner_id,
            presentation_id=presentation_id,
            results=results,
            deck=deck,
            image_refs=image_refs,
            publication_route=publication_route,
        )
        sql_session.add_all(slides)
        await sql_session.flush()
        # The Composer declares the bundled, OFL-licensed Pretendard faces it
        # renders with, not Deck IR's Windows Malgun Gothic: the exporter only
        # admits a Korean run as native text when the family it embedded is the
        # one the run's own stack names.
        font_manifest, font_bytes, font_family, font_face = (
            await composer_publication_font_materials()
        )
        asset_manifests = None
        artifact_bytes = dict(font_bytes)
        if fit_revision == "composer-source-fit/v2":
            from services.composer.publish_source_semantic import source_publication_assets

            asset_manifests = []
            for html in htmls:
                asset_manifest, image_bytes = source_publication_assets(html)
                asset_manifests.append(asset_manifest)
                artifact_bytes.update(image_bytes)
        language = str(getattr(request, "language", None) or presentation.language or "ko")
        style = getattr(request, "_authored_style_snapshot", None)
        style_id = str(getattr(style, "id", None) or getattr(request, "authored_style", None) or "")
        bundle_operation_id = fence.operation_id or uuid.uuid5(
            uuid.NAMESPACE_URL, f"presenton:{publication_route}:{presentation_id}:{expected_revision + 1}"
        )
        try:
            manifest = await publish_authored_semantic_bundle_graph(
                sql_session,
                presentation_id=presentation_id,
                owner_id=owner_id,
                expected_presentation_revision=expected_revision,
                operation_id=bundle_operation_id,
                generation_attempt_id=fence.job_id,
                slides=slides,
                htmls=list(htmls),
                semantic_drafts=[
                    semantic_draft_from_html(
                        html, language=language, font_family=font_family, font_face=font_face, tokens=deck.tokens
                    )
                    for html in htmls
                ],
                master_contract_digest=canonical_digest(bind_canvas_identity(
                    {
                        "schemaVersion": "composer-master/v1",
                        "styleId": style_id,
                        "tokens": deck.tokens.model_dump(mode="json"),
                        "styleProfile": deck.style_profile.model_dump(mode="json") if deck.style_profile else None,
                        "canvas": deck.canvas.model_dump(mode="json", by_alias=True),
                    }, canvas_metadata
                )),
                composition_digest=canonical_digest(bind_canvas_identity(deck.model_dump(mode="json"), canvas_metadata)),
                slide_asset_manifests=asset_manifests,
                slide_font_manifests=[font_manifest for _ in slides],
                resolved_artifact_bytes=artifact_bytes,
            )
        except SemanticContractError as error:
            cause = error.__cause__
            detail = None
            if isinstance(cause, ValidationError):
                # Validation inputs and messages can contain source content.
                # Preserve only bounded contract locations and error types.
                failures = cause.errors(include_input=False, include_context=False)
                detail = json.dumps({
                    "validationErrors": [
                        {"loc": list(failure["loc"]), "type": failure["type"]}
                        for failure in failures[:64]
                    ],
                    "validationErrorCount": len(failures),
                }, ensure_ascii=True, separators=(",", ":"))
            raise ComposerPublicationError(
                str(error) or "authored_manifest_publication_failed", detail=detail
            ) from error
        publication = {
            "manifestDigest": manifest.canonical_digest,
            "revision": expected_revision + 1,
            "bundleCount": count,
            "generationRoute": publication_route,
            "styleId": style_id,
            "styleProfile": deck.style_profile.model_dump(mode="json") if deck.style_profile else None,
        }
        rung_histogram = dict((receipt or {}).get("rungHistogram") or {})
        theme = dict(presentation.theme or {})
        theme.update(
            {
                "mode": AUTHORED_MODE,
                "style": style_id,
                "primary": deck.tokens.accent,
                "language": language,
                "generationComposer": {**publication, "slideCount": count, "rungHistogram": rung_histogram},
            }
        )
        from services.authored_canvas import merge_presentation_canvas
        theme = merge_presentation_canvas(theme, canvas_metadata)
        publication["canvas"] = canvas_metadata
        presentation.title = title
        presentation.n_slides = count
        presentation.outlines = _outline_entries(results, deck)
        presentation.mode = AUTHORED_MODE
        presentation.theme = theme
        presentation.updated_at = datetime.now(timezone.utc)
        if artifact is None:
            # The workspace helper flips lifecycle/revision itself under its fence.
            presentation.lifecycle_status = PRESENTATION_LIFECYCLE_PUBLISHED
        sql_session.add(presentation)
        edit_path = _edit_path(presentation_id)
        published_path = str(artifact.public_path) if artifact is not None else edit_path
        if async_status is not None:
            _completed_status(
                async_status, presentation_id=presentation_id, path=published_path, publication=publication
            )
            sql_session.add(async_status)
        if artifact is None:
            if fence.has_job_claim:
                await complete_publish(
                    sql_session,
                    job_id=fence.job_id,
                    attempt_token=fence.attempt_token,
                    fencing_token=fence.fencing_token,
                )
            try:
                revision = await publish_presentation_revision(
                    sql_session,
                    presentation_id=presentation_id,
                    expected_revision=expected_revision,
                    operation_id=None,
                    attempt_token=None,
                    result={"publication": publication},
                    operation_kind="presentation.generate",
                )
            except PresentationPublicationConflict as error:
                raise ComposerPublicationError(FENCE_LOST_CODE, retryable=True) from error
            await sql_session.commit()
        else:
            await sql_session.flush()
            artifact_handed_to_publish = True
            revision = await publish_workspace_artifact(
                sql_session,
                artifact=artifact,
                fence=fence,
                expected_revision=expected_revision,
                result_evidence={
                    "publication": dict(publication),
                    "generationRoute": publication_route,
                    "rungHistogram": rung_histogram,
                },
            )
    except BaseException as error:
        await sql_session.rollback()
        if artifact is not None and not artifact_handed_to_publish:
            await compensate_artifact(sql_session, artifact)
        if images_saved:
            try:
                await asyncio.to_thread(_remove_partial_slide_pngs, presentation_id, png_list, image_token)
            except OSError:
                pass
        if isinstance(error, asyncio.CancelledError) or not isinstance(error, Exception):
            raise
        if isinstance(error, ComposerPipelineError):
            LOGGER.warning(
                "composer publication rejected presentation=%s code=%s retryable=%s",
                presentation_id, error.code, error.retryable, exc_info=True,
            )
            raise
        if isinstance(error, AuthoredStageError):
            raise ComposerPublicationError(error.code, retryable=error.retryable) from error
        LOGGER.exception(
            "composer publication failed presentation=%s error=%s", presentation_id, type(error).__name__
        )
        raise ComposerPublicationError(
            "composer_publication_failed",
            detail=f"{type(error).__name__}: {error}"[:300],
        ) from error
    return {
        **publication,
        "revision": revision,
        "editPath": edit_path,
        "path": published_path,
        "imageRefs": list(image_refs),
    }


__all__ = ["AUTHORED_MODE", "PUBLICATION_ROUTE", "publish_composer_deck"]
