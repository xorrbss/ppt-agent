from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from models.authored_chapter_plan import AuthoredChapterPlan
from models.authored_header_contract import AuthoredHeaderContract
from models.authored_illustration_plan import AuthoredIllustrationPlan
from models.generate_presentation_request import GeneratePresentationRequest
from models.presentation_and_path import PresentationPathAndEditPath
from models.presentation_outline_model import PresentationOutlineModel
from models.sql.async_presentation_generation_status import AsyncPresentationGenerationTaskModel
from models.sql.authored_exact_provenance import AuthoredExactProvenance
from models.sql.presentation import (
    PRESENTATION_LIFECYCLE_PUBLISHED,
    PRESENTATION_LIFECYCLE_STAGING,
    PresentationModel,
)
from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.slide import SlideModel
from services.authored_checkpoint_service import (
    AuthoredStageError,
    begin_publish,
    complete_publish,
    fail_stage,
)
from services.authored_presentation_artifacts import (
    _remove_partial_slide_pngs,
    _save_slide_pngs,
)
from services.authored_presentation_contracts import AUTHORED_TEMPLATE
from services.workspace_artifact_publication_service import (
    WorkspaceArtifactPublicationError,
    compensate_workspace_artifact,
    prepare_workspace_generation_artifact,
    publish_workspace_generation_artifact,
)
from services.presentation_export_attempt_service import sha256_canonical_json
from services.authored_vnext.semantic.canonical import SemanticContractError
from services.authored_vnext.semantic.publication import (
    publish_authored_semantic_bundle_graph,
)
from services.authored_vnext.semantic.editable_scene import (
    PROFILE_ID as EDITABLE_HYBRID_PROFILE_ID,
)
from models.authored_vnext.deck import DeckSceneIR
from utils.llm_calls.author_slide import Brand


LOGGER = logging.getLogger(__name__)


def _authored_company_standard_projection(
    request: GeneratePresentationRequest,
    htmls: list[str],
) -> dict[str, object] | None:
    """Keep company furniture identity separate from the Authored HTML body.

    New Authored generation no longer consumes the heuristic Scene compiler.
    Until a bounded furniture overlay can preserve arbitrary Authored fidelity,
    the selected standard is persisted as a sealed, explicitly unapplied
    compatibility constraint instead of being presented as visual output.
    """

    resolved = request._authored_master_resolution
    composition = request._authored_composition_pin
    if resolved is None and composition is None:
        return None
    if resolved is None or composition is None:
        raise AuthoredStageError(
            slide_index=0,
            stage="persist",
            code="authored_company_standard_checkpoint_incomplete",
            retryable=False,
        )
    if resolved.pin.master_digest != composition.master_digest:
        raise AuthoredStageError(
            slide_index=0,
            stage="persist",
            code="authored_company_standard_digest_mismatch",
            retryable=False,
        )
    return {
        "schemaVersion": "authored-company-standard-projection/v1",
        "body": {
            "kind": "authored-html",
            "sourceDigest": "sha256:" + sha256_canonical_json(htmls),
        },
        "furniture": {
            "digest": composition.company_standard_pin.digest,
            "masterDigest": composition.master_digest,
            "applied": False,
            "fallbackReason": "arbitrary_authored_html_furniture_overlay_unsupported",
        },
        "masterPin": resolved.pin.model_dump(mode="json", by_alias=True),
        "compositionPin": composition.model_dump(mode="json", by_alias=True),
        "safeArea": resolved.contract.canvas.safe_area.model_dump(
            mode="json", by_alias=True
        ),
        "safeAreaEnforced": False,
        "visualParity": "not-verified",
        "promotionReady": False,
    }


def _require_immutable_composition_publication(
    *,
    request: GeneratePresentationRequest,
    master_generation,
    native_export_evidence: dict[str, object] | None,
) -> None:
    """Seal preview, publication, and native export to one composition pin."""

    if master_generation is None:
        return
    deck = master_generation.result.deck
    composition = deck.composition
    scene = deck.scene
    requested = request._authored_composition_pin
    if (
        scene is None
        or composition is None
        or requested is None
        or requested != composition
        or master_generation.composition_pin != composition
        or scene.composition_digest != composition.digest
    ):
        raise AuthoredStageError(
            slide_index=0,
            stage="persist",
            code="authored_composition_publication_mismatch",
            retryable=False,
            message="생성 요청과 미리보기의 회사표준/저작 스타일 구성이 일치하지 않습니다.",
        )

    if request.export_as != "pptx":
        return
    preview = (
        native_export_evidence.get("preview_snapshot")
        if isinstance(native_export_evidence, dict)
        else None
    )
    native = (
        native_export_evidence.get("native_pptx_snapshot")
        if isinstance(native_export_evidence, dict)
        else None
    )
    if (
        not isinstance(preview, dict)
        or not isinstance(native, dict)
        or preview != native
        or preview.get("composition_digest") != composition.digest
        or native_export_evidence.get("resolved_composition_digest")
        != composition.digest
        or native_export_evidence.get("scene_digest") != scene.scene_digest
        or native_export_evidence.get("scene_composition_digest")
        != scene.composition_digest
        or native_export_evidence.get("scene_slide_digests")
        != [slide.slide_digest for slide in scene.slides]
        or native_export_evidence.get("readback_verified") is not True
    ):
        raise AuthoredStageError(
            slide_index=0,
            stage="persist",
            code="authored_composition_export_evidence_mismatch",
            retryable=False,
            message="미리보기와 PowerPoint 내보내기의 구성 검증 결과가 일치하지 않습니다.",
        )


async def publish_authored_presentation(
    *,
    request: GeneratePresentationRequest,
    presentation_id: uuid.UUID,
    outline: PresentationOutlineModel,
    language: str | None,
    sql_session: AsyncSession,
    generation_job: PresentationGenerationJob | None,
    existing: PresentationModel | None,
    title: str,
    brand: Brand,
    style,
    roles: list[str],
    exact_provenance,
    htmls: list[str],
    semantic_components: list[dict[str, object]],
    pngs: list[bytes],
    out_path: str,
    attempt_token: str | None,
    master_generation=None,
    canonical_scene: DeckSceneIR | None = None,
    native_export_evidence: dict[str, object] | None = None,
    header_contract: AuthoredHeaderContract | None = None,
    chapter_plan: AuthoredChapterPlan | None = None,
    illustration_plan: AuthoredIllustrationPlan | None = None,
) -> tuple[str, float]:
    persist_started = time.monotonic()
    images_ready = False
    images_started = False
    published_path = out_path
    workspace_artifact = None
    artifact_handed_to_publish = False
    image_publication_token = (
        f"{generation_job.workspace_operation_id}:{attempt_token}"
        if generation_job is not None
        and generation_job.workspace_operation_id is not None
        and attempt_token
        else None
    )
    presentation = existing or PresentationModel(
        id=presentation_id,
        content=request.content,
        n_slides=len(htmls),
        language=language or brand.language,
        lifecycle_status=PRESENTATION_LIFECYCLE_STAGING,
    )
    expected_revision = presentation.revision
    if presentation.owner_id is None:
        raise AuthoredStageError(
            slide_index=0,
            stage="persist",
            code="authored_semantic_owner_required",
            retryable=False,
        )
    if len(semantic_components) != len(htmls):
        raise AuthoredStageError(
            slide_index=0,
            stage="persist",
            code="authored_semantic_component_count_mismatch",
            retryable=False,
        )
    try:
        _require_immutable_composition_publication(
            request=request,
            master_generation=master_generation,
            native_export_evidence=native_export_evidence,
        )
        # Reserve and materialize every external artifact before acquiring the
        # caller transaction's publication fence.  In particular, the durable
        # artifact intent uses an independent session; opening it after
        # begin_publish would deadlock SQLite's single writer and could commit
        # unrelated caller UoW state.
        if (
            generation_job is not None
            and generation_job.workspace_operation_id is not None
        ):
            workspace_artifact = await prepare_workspace_generation_artifact(
                sql_session,
                source_path=out_path,
                operation_id=generation_job.workspace_operation_id,
                presentation_id=presentation_id,
                source_revision=expected_revision + 1,
            )
            published_path = workspace_artifact.public_path

        images_started = True
        if image_publication_token is None:
            # Preserve the two-argument helper contract used by the legacy path
            # and its narrow test doubles.
            image_refs = _save_slide_pngs(presentation_id, pngs)
        else:
            image_refs = _save_slide_pngs(
                presentation_id,
                pngs,
                image_publication_token,
            )
        images_ready = True

        if generation_job is not None and attempt_token:
            await begin_publish(
                sql_session,
                job_id=generation_job.id,
                attempt_token=attempt_token,
            )

        presentation.content = request.content
        presentation.n_slides = len(htmls)
        presentation.language = language or brand.language
        presentation.title = title
        presentation.file_paths = request.files
        presentation.reference_snapshot_ids = [
            str(snapshot_id) for snapshot_id in request.reference_snapshot_ids
        ] or None
        presentation.outlines = outline.model_dump()
        presentation.layout = None
        presentation.structure = None
        presentation.tone = request.tone.value
        presentation.verbosity = request.verbosity.value
        presentation.instructions = request.instructions
        presentation.include_table_of_contents = request.include_table_of_contents
        presentation.include_title_slide = request.include_title_slide
        presentation.web_search = request.web_search
        presentation.mode = AUTHORED_TEMPLATE
        presentation.lifecycle_status = PRESENTATION_LIFECYCLE_STAGING
        presentation.theme = {
            "mode": AUTHORED_TEMPLATE,
            "style": style.id,
            "primary": brand.primary,
            "fonts": brand.fonts,
            "language": brand.language,
        }
        company_standard = _authored_company_standard_projection(request, htmls)
        if company_standard is not None:
            presentation.theme["authoredCompanyStandard"] = company_standard
        if header_contract is not None:
            # The deck's frozen top-area design. Persisting it is what lets a
            # later chat request restyle the header without re-deciding it from
            # scratch, and what keeps a regenerated slide consistent with the
            # slides around it.
            presentation.theme["headerContract"] = header_contract.model_dump(
                mode="json"
            )
        if chapter_plan is not None:
            # The deck's frozen chapter structure, in the same final-outline
            # indices the persisted outline uses. A single-page remake rebuilds
            # its plan from here for the same reason it rebuilds the header
            # contract: without it the remade page writes a bare section-name
            # eyebrow while its 63 siblings carry a chapter number.
            presentation.theme["chapterPlan"] = chapter_plan.model_dump(mode="json")
        if illustration_plan is not None:
            # The deck's frozen illustration slots, in the same final-outline
            # indices the persisted outline uses. A single-page remake replays it
            # rather than re-deciding: a page that was authored without a picture
            # must not come back from a remake carrying one, and a page that had
            # one must not lose it. An EMPTY plan is still a plan and is stored,
            # because "this deck gets no pictures" is a decision.
            presentation.theme["illustrationPlan"] = illustration_plan.model_dump(
                mode="json"
            )
        if master_generation is not None:
            deck = master_generation.result.deck
            scene = deck.scene
            if scene is None:
                raise AuthoredStageError(
                    slide_index=0,
                    stage="persist",
                    code="authored_vnext_scene_required",
                    retryable=False,
                )
            pin = deck.master
            presentation.theme["authored_vnext"] = {
                "schemaVersion": "authored-vnext-scene-projection/v1",
                "deckId": deck.deck_id,
                "deckRevision": deck.revision,
                "deckDigest": deck.digest,
                "sourceDigest": deck.source_digest,
                "sceneDigest": scene.scene_digest,
                "scene": scene.model_dump(mode="json", by_alias=True),
                "masterPin": pin.model_dump(mode="json", by_alias=True),
                **(
                    {
                        "compositionPin": deck.composition.model_dump(
                            mode="json", by_alias=True
                        )
                    }
                    if deck.composition is not None
                    else {}
                ),
                "exportArtifactDigest": (
                    master_generation.result.export_artifact_digest
                ),
                "aiTextZones": master_generation.ai_text_zones.model_dump(
                    mode="json"
                ),
                **(
                    {
                        "sourceSemanticSnapshotDigest": master_generation.source_semantic_snapshot.get(
                            "digest"
                        ),
                        "sourceSemanticSnapshot": master_generation.source_semantic_snapshot,
                    }
                    if isinstance(master_generation.source_semantic_snapshot, dict)
                    and isinstance(
                        master_generation.source_semantic_snapshot.get("digest"),
                        str,
                    )
                    else {}
                ),
            }
        slides = [
            SlideModel(
                presentation=presentation_id,
                layout_group=AUTHORED_TEMPLATE,
                layout=f"{AUTHORED_TEMPLATE}:{roles[i]}",
                index=i,
                content={
                    "__authored__": True,
                    "image": image_refs[i],
                    "role": roles[i],
                    **(
                        {
                            "masterOwned": True,
                            "layoutId": master_generation.result.deck.slides[
                                i
                            ].slide.layout_id,
                            "masterPin": master_generation.result.deck.master.model_dump(
                                mode="json", by_alias=True
                            ),
                            **(
                                {
                                    "compositionPin": master_generation.result.deck.composition.model_dump(
                                        mode="json", by_alias=True
                                    )
                                }
                                if master_generation.result.deck.composition is not None
                                else {}
                            ),
                            "slideSpecDigest": master_generation.result.deck.slides[
                                i
                            ].slide.spec_digest,
                            "sceneSlideDigest": scene.slides[i].slide_digest,
                            "sceneSlide": scene.slides[i].model_dump(
                                mode="json", by_alias=True
                            ),
                        }
                        if master_generation is not None
                        else {}
                    ),
                },
                html_content=htmls[i],
                properties={"image": image_refs[i]},
            )
            for i in range(len(htmls))
        ]
        sql_session.add(presentation)
        sql_session.add_all(slides)
        operation_id = (
            generation_job.workspace_operation_id
            if generation_job is not None
            and generation_job.workspace_operation_id is not None
            else uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"presenton:authored:{presentation_id}:{expected_revision + 1}",
            )
        )
        master_resolution = request._authored_master_resolution
        composition_pin = request._authored_composition_pin
        master_contract_digest = (
            master_resolution.pin.master_digest
            if master_resolution is not None
            else "sha256:"
            + sha256_canonical_json(
                {
                    "schemaVersion": "authored-unpinned-master/v1",
                    "styleId": style.id,
                    "brand": {
                        "primary": brand.primary,
                        "fonts": brand.fonts,
                        "language": brand.language,
                    },
                }
            )
        )
        composition_digest = (
            composition_pin.digest
            if composition_pin is not None
            else "sha256:"
            + sha256_canonical_json(
                {
                    "schemaVersion": "authored-unapplied-composition/v1",
                    "masterContractDigest": master_contract_digest,
                    "furnitureApplied": False,
                }
            )
        )
        scene_blocker = None
        if canonical_scene is None:
            # Unpinned/legacy callers do not own a durable generation deck.
            # Keep them explicitly blocked instead of projecting an in-memory
            # Scene that Workbench and export cannot subsequently restore.
            scene_blocker = "editable_hybrid_generation_deck_unavailable"
        elif (
            canonical_scene.revision != expected_revision + 1
            or canonical_scene.deck_id != f"deck:semantic-{presentation_id.hex}"
        ):
            raise AuthoredStageError(
                slide_index=0,
                stage="persist",
                code="authored_semantic_scene_identity_mismatch",
                retryable=False,
            )
        try:
            semantic_manifest = await publish_authored_semantic_bundle_graph(
                sql_session,
                presentation_id=presentation_id,
                owner_id=presentation.owner_id,
                expected_presentation_revision=expected_revision,
                operation_id=operation_id,
                generation_attempt_id=(
                    generation_job.id if generation_job is not None else None
                ),
                slides=slides,
                htmls=htmls,
                semantic_drafts=semantic_components,
                master_contract_digest=master_contract_digest,
                composition_digest=composition_digest,
                canonical_scene=canonical_scene,
            )
        except SemanticContractError as error:
            raise AuthoredStageError(
                slide_index=0,
                stage="persist",
                code=str(error),
                retryable=False,
                cause=error,
            ) from error
        projection = {
            "schemaVersion": "authored-editable-hybrid-scene-projection/v1",
            "profileId": EDITABLE_HYBRID_PROFILE_ID,
            "deckId": (
                canonical_scene.deck_id
                if canonical_scene is not None
                else f"deck:semantic-{presentation_id.hex}"
            ),
            "deckRevision": expected_revision + 1,
            "deckDigest": semantic_manifest.canonical_digest,
            "sourceDigest": (
                canonical_scene.source_digest if canonical_scene is not None else None
            ),
            "sceneDigest": (
                canonical_scene.scene_digest if canonical_scene is not None else None
            ),
            "scene": (
                canonical_scene.model_dump(mode="json", by_alias=True)
                if canonical_scene is not None
                else None
            ),
            "status": "shadow" if canonical_scene is not None else "blocked",
            "reasonCode": (
                "editable_hybrid_mvp_completion_not_sealed"
                if canonical_scene is not None
                else "editable_hybrid_scene_unavailable"
            ),
            "blockerDetail": scene_blocker,
            "promotionReady": False,
            "deploymentAuthorized": False,
        }
        presentation.theme = {
            **dict(presentation.theme or {}),
            "authored_vnext": projection,
        }
        if canonical_scene is not None:
            for slide, scene_slide in zip(
                slides, canonical_scene.slides, strict=True
            ):
                slide.content = {
                    **dict(slide.content or {}),
                    "sceneSlideDigest": scene_slide.slide_digest,
                    "sceneSlide": scene_slide.model_dump(mode="json", by_alias=True),
                }
        if exact_provenance is not None:
            sql_session.add(
                AuthoredExactProvenance(
                    presentation_id=presentation_id,
                    provenance=exact_provenance,
                )
            )
        if workspace_artifact is None:
            publication = await sql_session.execute(
                update(PresentationModel)
                .where(
                    PresentationModel.id == presentation_id,
                    PresentationModel.lifecycle_status
                    == PRESENTATION_LIFECYCLE_STAGING,
                    PresentationModel.revision == expected_revision,
                )
                .values(
                    lifecycle_status=PRESENTATION_LIFECYCLE_PUBLISHED,
                    revision=PresentationModel.revision + 1,
                    updated_at=datetime.now(timezone.utc),
                )
            )
            if publication.rowcount != 1:
                raise AuthoredStageError(
                    slide_index=max(0, len(pngs) - 1),
                    stage="persist",
                    code="authored_attempt_lost",
                )
        if generation_job is not None and attempt_token:
            response = PresentationPathAndEditPath(
                presentation_id=presentation_id,
                path=published_path,
                edit_path=(
                    "/authored-vnext/workbench?authoredDeckId="
                    f"{master_generation.result.deck.deck_id}"
                    if master_generation is not None
                    else f"/presentation?id={presentation_id}"
                ),
            )
            async_status = await sql_session.get(
                AsyncPresentationGenerationTaskModel,
                generation_job.task_id,
            )
            if async_status is None:
                raise RuntimeError("presentation_generation_task_missing")
            async_status.status = "completed"
            async_status.message = "Presentation generation completed"
            async_status.data = response.model_dump(mode="json")
            async_status.error = None
            async_status.updated_at = datetime.now(timezone.utc)
            sql_session.add(async_status)
            if workspace_artifact is None:
                await complete_publish(
                    sql_session,
                    job_id=generation_job.id,
                    attempt_token=attempt_token,
                )
        if workspace_artifact is not None:
            try:
                # From here the publication helper owns reconciliation and
                # compensation.  A returned revision means the complete tuple
                # committed, including after an ambiguous commit ACK.
                artifact_handed_to_publish = True
                await publish_workspace_generation_artifact(
                    sql_session,
                    artifact=workspace_artifact,
                    expected_revision=expected_revision,
                    attempt_token=attempt_token or "",
                    result_evidence=native_export_evidence,
                )
            except WorkspaceArtifactPublicationError as error:
                raise AuthoredStageError(
                    slide_index=max(0, len(pngs) - 1),
                    stage="persist",
                    code=(
                        "authored_attempt_lost"
                        if error.code in {"operation_lease_lost", "revision_conflict"}
                        else error.code
                    ),
                    retryable=True,
                    cause=error,
                ) from error
        else:
            await sql_session.commit()
    except BaseException as exc:
        LOGGER.exception(
            "Authored presentation publication failed for %s",
            presentation_id,
        )
        await sql_session.rollback()
        if workspace_artifact is not None and not artifact_handed_to_publish:
            try:
                await compensate_workspace_artifact(sql_session, workspace_artifact)
            except Exception:
                # The artifact service durably records cleanup_pending before
                # raising.  Never hide the original generation failure.
                LOGGER.warning(
                    "Unable to compensate unpublished workspace artifact %s",
                    workspace_artifact.artifact_id,
                    exc_info=True,
                )

        # Workspace publication reconciles an ambiguous commit before raising,
        # so any raised outcome is unpublished and this attempt-scoped image set
        # is safe to remove.  The legacy path keeps a complete immutable set on
        # ambiguous commit, but removes a partial copy.
        if images_started and (workspace_artifact is not None or not images_ready):
            try:
                _remove_partial_slide_pngs(
                    presentation_id,
                    pngs,
                    image_publication_token,
                )
            except OSError:
                LOGGER.warning(
                    "Unable to remove partial authored slide images for %s",
                    presentation_id,
                    exc_info=True,
                )

        if isinstance(exc, asyncio.CancelledError):
            raise
        if not isinstance(exc, Exception):
            raise
        if isinstance(exc, AuthoredStageError):
            # In particular, preserve authored_attempt_lost so a stale worker
            # cannot relabel lease loss as a retryable slide failure or mutate
            # the newer owner's durable status in the outer handler.
            raise
        error = AuthoredStageError(
            slide_index=max(0, len(pngs) - 1),
            stage="persist",
            code="authored_slide_persist_failed",
            cause=exc,
        )
        if generation_job is not None and attempt_token:
            await fail_stage(
                job_id=generation_job.id,
                slide_index=error.slide_index,
                stage="persist",
                attempt_token=attempt_token,
                error=error,
                request_id=generation_job.request_id,
            )
        raise error from exc
    return published_path, time.monotonic() - persist_started
