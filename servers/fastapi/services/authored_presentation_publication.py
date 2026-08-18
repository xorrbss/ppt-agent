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
from utils.llm_calls.author_slide import Brand


LOGGER = logging.getLogger(__name__)


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
    requested = request._authored_composition_pin
    if (
        composition is None
        or requested is None
        or requested != composition
        or master_generation.composition_pin != composition
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
    pngs: list[bytes],
    out_path: str,
    attempt_token: str | None,
    master_generation=None,
    native_export_evidence: dict[str, object] | None = None,
    header_contract: AuthoredHeaderContract | None = None,
    chapter_plan: AuthoredChapterPlan | None = None,
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
        if master_generation is not None:
            deck = master_generation.result.deck
            pin = deck.master
            presentation.theme["authored_vnext"] = {
                "schemaVersion": "legacy-authored-vnext-projection/v1",
                "deckId": deck.deck_id,
                "deckRevision": deck.revision,
                "deckDigest": deck.digest,
                "sourceDigest": deck.source_digest,
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
                edit_path=f"/presentation?id={presentation_id}",
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
