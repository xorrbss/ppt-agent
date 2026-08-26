from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from threading import Event, Thread

from pydantic import ValidationError

from services.source_locked.analysis import analyze_source
from services.source_locked.contracts import (
    AdmissionContractV1,
    AnalysisStage,
    ProgressStageV1,
    SourceLockedArtifactV1,
    SourceLockedGenerationResponseV1,
    SourceLockedRestorationVerificationV1,
    StageStatus,
)
from services.source_locked.digests import idempotency_payload_digest, sha256_digest
from services.source_locked.errors import SourceLockedError
from services.source_locked.generation_store import (
    DurableSourceLockedGenerationStore,
    generation_identity,
)
from services.source_locked.job_store import DurableSourceLockedJobStore
from services.source_locked.seal import WriteOnceSourceAnalysisSealStore


PhaseCallback = Callable[[AnalysisStage], bool]
CommitCallback = Callable[
    [SourceLockedGenerationResponseV1, bytes | None],
    SourceLockedGenerationResponseV1 | None,
]


def _restore(*, admission_contract, source_snapshot, manifest, source_bytes):
    from services.source_locked.restoration import restore_source_locked_v1

    return restore_source_locked_v1(
        admission_contract=admission_contract,
        source_snapshot=source_snapshot,
        manifest=manifest,
        source_bytes=source_bytes,
    )


def generate_source_locked_result(
    *,
    owner_id: str,
    admission_contract: AdmissionContractV1,
    source_bytes: bytes,
    idempotency_key: str,
    expected_payload_digest: str,
    generation_store: DurableSourceLockedGenerationStore,
    phase_completed: PhaseCallback | None = None,
    prepare_commit: Callable[[], bool] | None = None,
    commit_result: CommitCallback | None = None,
    restoration: Callable = _restore,
) -> SourceLockedGenerationResponseV1 | None:
    """Generate one result, returning ``None`` when an accepted cancel wins."""

    source_digest = sha256_digest(source_bytes)
    calculated_payload_digest = idempotency_payload_digest(admission_contract, source_digest)
    if calculated_payload_digest != expected_payload_digest:
        raise SourceLockedError(
            "IDEMPOTENCY_PAYLOAD_DIGEST_MISMATCH",
            AnalysisStage.SOURCE_ANALYSIS,
            "The idempotency payload digest does not match the admitted source snapshot",
        )

    existing = generation_store.lookup(
        owner_id=owner_id,
        idempotency_key=idempotency_key,
        idempotency_payload_digest=calculated_payload_digest,
    )
    if existing is not None:
        if phase_completed is not None:
            for stage in AnalysisStage:
                if not phase_completed(stage):
                    return None
        if prepare_commit is not None and not prepare_commit():
            return None
        if commit_result is not None:
            return commit_result(existing, None)
        return existing

    analysis = analyze_source(
        admission_contract=admission_contract,
        data=source_bytes,
        idempotency_key=idempotency_key,
        expected_idempotency_payload_digest=expected_payload_digest,
        seal_store=WriteOnceSourceAnalysisSealStore(),
    )
    if phase_completed is not None and not phase_completed(AnalysisStage.SOURCE_ANALYSIS):
        return None

    try:
        restored = restoration(
            admission_contract=admission_contract,
            source_snapshot=analysis.source_snapshot,
            manifest=analysis.manifest,
            source_bytes=source_bytes,
        )
    except SourceLockedError:
        raise
    except Exception as exc:
        raise SourceLockedError(
            "SOURCE_RESTORATION_FAILED",
            AnalysisStage.SLIDE_IMAGE_RESTORATION,
            "Source-locked slide and image restoration failed",
        ) from exc
    if phase_completed is not None and not phase_completed(
        AnalysisStage.SLIDE_IMAGE_RESTORATION
    ):
        return None

    verification_in = restored.verification
    try:
        verification = SourceLockedRestorationVerificationV1(
            source_type=verification_in.source_type,
            source_snapshot_sha256=verification_in.source_snapshot_sha256,
            output_package_sha256=verification_in.output_package_sha256,
            page_count=verification_in.page_count,
            slide_count=verification_in.slide_count,
            direct_occurrence_count=verification_in.direct_occurrence_count,
            rendered_page_count=verification_in.rendered_page_count,
            authoritative_image_digests=tuple(verification_in.authoritative_image_digests),
            missing_content_count=verification_in.missing_content_count,
            byte_exact_source_package=verification_in.byte_exact_source_package,
            fallback_allowed=verification_in.fallback_allowed,
        )
        if restored.artifact_media_type != SourceLockedArtifactV1.model_fields["media_type"].default:
            raise SourceLockedError(
                "ARTIFACT_MEDIA_TYPE_INVALID",
                AnalysisStage.MISSING_CONTENT_VALIDATION,
                "The restoration worker did not produce a PPTX artifact",
            )
        generation_record_id, artifact_id = generation_identity(
            owner_id,
            idempotency_key,
            calculated_payload_digest,
        )
        artifact_bytes = restored.artifact_bytes
        artifact = SourceLockedArtifactV1(
            artifact_id=artifact_id,
            filename=f"source-locked-{artifact_id.removeprefix('sla_')[:16]}.pptx",
            byte_length=len(artifact_bytes),
            sha256=sha256_digest(artifact_bytes),
            download_url=f"/api/v1/ppt/source-locked/v1/artifacts/{artifact_id}",
            created_at=datetime.now(timezone.utc),
            source_type=analysis.summary.source_type,
            slide_count=analysis.summary.slide_count,
        )
        response = SourceLockedGenerationResponseV1(
            generation_record_id=generation_record_id,
            admission_contract=analysis.admission_contract,
            source_snapshot=analysis.source_snapshot,
            manifest=analysis.manifest,
            summary=analysis.summary,
            analysis_seal=analysis.analysis_seal,
            restoration_verification=verification,
            artifact=artifact,
            progress_stages=tuple(
                ProgressStageV1(stage=stage, status=StageStatus.COMPLETED)
                for stage in AnalysisStage
            ),
        )
    except SourceLockedError:
        raise
    except (ValidationError, ValueError) as exc:
        raise SourceLockedError(
            "RESTORATION_READBACK_INVALID",
            AnalysisStage.MISSING_CONTENT_VALIDATION,
            "Restoration readback did not satisfy the source-locked contract",
        ) from exc

    if phase_completed is not None and not phase_completed(
        AnalysisStage.MISSING_CONTENT_VALIDATION
    ):
        return None
    if prepare_commit is not None and not prepare_commit():
        return None
    if commit_result is not None:
        return commit_result(response, artifact_bytes)
    return generation_store.persist(
        owner_id=owner_id,
        response=response,
        artifact_bytes=artifact_bytes,
    )


def run_source_locked_job(
    *,
    owner_id: str,
    job_id: str,
    job_store: DurableSourceLockedJobStore,
    generation_store: DurableSourceLockedGenerationStore,
) -> None:
    runner_token = job_store.claim(owner_id=owner_id, job_id=job_id)
    if runner_token is None:
        return
    stop_heartbeat = Event()

    def _heartbeat() -> None:
        while not stop_heartbeat.wait(15):
            if not job_store.heartbeat(
                owner_id=owner_id,
                job_id=job_id,
                runner_token=runner_token,
            ):
                return

    heartbeat = Thread(target=_heartbeat, name=f"source-locked-{job_id[:20]}", daemon=True)
    heartbeat.start()
    try:
        contract, source_bytes, idempotency_key, expected_digest = job_store.read_input(
            owner_id=owner_id,
            job_id=job_id,
        )

        def _commit_result(
            response: SourceLockedGenerationResponseV1,
            artifact_bytes: bytes | None,
        ) -> SourceLockedGenerationResponseV1 | None:
            return job_store.publish_and_succeed(
                owner_id=owner_id,
                job_id=job_id,
                runner_token=runner_token,
                publisher=(
                    (lambda: response)
                    if artifact_bytes is None
                    else lambda: generation_store.persist(
                        owner_id=owner_id,
                        response=response,
                        artifact_bytes=artifact_bytes,
                    )
                ),
            )

        generate_source_locked_result(
            owner_id=owner_id,
            admission_contract=contract,
            source_bytes=source_bytes,
            idempotency_key=idempotency_key,
            expected_payload_digest=expected_digest,
            generation_store=generation_store,
            phase_completed=lambda stage: job_store.advance(
                owner_id=owner_id,
                job_id=job_id,
                runner_token=runner_token,
                completed_stage=stage,
            ),
            prepare_commit=lambda: job_store.prepare_commit(
                owner_id=owner_id,
                job_id=job_id,
                runner_token=runner_token,
            ),
            commit_result=_commit_result,
        )
    except SourceLockedError as exc:
        job_store.fail(
            owner_id=owner_id,
            job_id=job_id,
            runner_token=runner_token,
            error=exc.detail,
        )
    except Exception:
        job_store.fail(
            owner_id=owner_id,
            job_id=job_id,
            runner_token=runner_token,
            error=SourceLockedError(
                "SOURCE_LOCKED_JOB_FAILED",
                AnalysisStage.MISSING_CONTENT_VALIDATION,
                "The durable source-locked generation job failed",
            ).detail,
        )
    finally:
        stop_heartbeat.set()
        heartbeat.join(timeout=1)
