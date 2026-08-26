from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
import os
from pathlib import Path

from pptx import Presentation
import pytest

from services.source_locked.admission import admit_source_locked_v1
from services.source_locked.analysis import analyze_source
from services.source_locked.contracts import (
    AnalysisStage,
    GenerationMode,
    PageCountPolicy,
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
from services.source_locked.lifecycle import ArtifactLifecyclePolicy
from services.source_locked.restoration import restore_source_locked_v1
from services.source_locked.seal import WriteOnceSourceAnalysisSealStore


def _source() -> bytes:
    presentation = Presentation()
    presentation.slides.add_slide(presentation.slide_layouts[6])
    output = BytesIO()
    presentation.save(output)
    return output.getvalue()


def _response(
    source: bytes,
    *,
    owner_id: str,
    idempotency_key: str,
    created_at: datetime,
) -> SourceLockedGenerationResponseV1:
    contract = admit_source_locked_v1(
        ["source.pptx"],
        generation_mode=GenerationMode.SOURCE_LOCKED,
        page_count_policy=PageCountPolicy.SOURCE,
        preserve_all_image_occurrences=True,
    )
    payload_digest = idempotency_payload_digest(contract, sha256_digest(source))
    analysis = analyze_source(
        admission_contract=contract,
        data=source,
        idempotency_key=idempotency_key,
        expected_idempotency_payload_digest=payload_digest,
        seal_store=WriteOnceSourceAnalysisSealStore(),
    )
    restored = restore_source_locked_v1(
        admission_contract=contract,
        source_snapshot=analysis.source_snapshot,
        manifest=analysis.manifest,
        source_bytes=source,
    )
    record_id, artifact_id = generation_identity(owner_id, idempotency_key, payload_digest)
    verification = SourceLockedRestorationVerificationV1(
        source_type=restored.verification.source_type,
        source_snapshot_sha256=restored.verification.source_snapshot_sha256,
        output_package_sha256=restored.verification.output_package_sha256,
        page_count=restored.verification.page_count,
        slide_count=restored.verification.slide_count,
        direct_occurrence_count=restored.verification.direct_occurrence_count,
        rendered_page_count=restored.verification.rendered_page_count,
        authoritative_image_digests=restored.verification.authoritative_image_digests,
        missing_content_count=0,
        byte_exact_source_package=True,
    )
    artifact = SourceLockedArtifactV1(
        artifact_id=artifact_id,
        filename=f"source-locked-{artifact_id.removeprefix('sla_')[:16]}.pptx",
        byte_length=len(source),
        sha256=sha256_digest(source),
        download_url=f"/api/v1/ppt/source-locked/v1/artifacts/{artifact_id}",
        created_at=created_at,
        source_type="pptx",
        slide_count=1,
    )
    return SourceLockedGenerationResponseV1(
        generation_record_id=record_id,
        admission_contract=analysis.admission_contract,
        source_snapshot=analysis.source_snapshot,
        manifest=analysis.manifest,
        summary=analysis.summary,
        analysis_seal=analysis.analysis_seal,
        restoration_verification=verification,
        artifact=artifact,
        progress_stages=tuple(
            ProgressStageV1(stage=stage, status=StageStatus.COMPLETED)
            for stage in (
                AnalysisStage.SOURCE_ANALYSIS,
                AnalysisStage.SLIDE_IMAGE_RESTORATION,
                AnalysisStage.MISSING_CONTENT_VALIDATION,
            )
        ),
    )


def test_store_binds_authority_to_owner_and_enforces_lifecycle(tmp_path, monkeypatch) -> None:
    source = _source()
    policy = ArtifactLifecyclePolicy(
        retention_seconds=60,
        max_artifact_bytes=len(source) + 10,
        max_owner_bytes=len(source) + 10,
        max_total_bytes=(len(source) * 3) + 30,
        orphan_grace_seconds=1,
    )
    store = DurableSourceLockedGenerationStore(tmp_path / "source-locked", lifecycle_policy=policy)
    created_at = datetime.now(timezone.utc)
    owner_a = _response(
        source,
        owner_id="owner-a",
        idempotency_key="same-key",
        created_at=created_at,
    )
    owner_b = _response(
        source,
        owner_id="owner-b",
        idempotency_key="same-key",
        created_at=created_at,
    )
    store.persist(owner_id="owner-a", response=owner_a, artifact_bytes=source)
    store.persist(owner_id="owner-b", response=owner_b, artifact_bytes=source)

    assert store.lookup(
        owner_id="owner-a",
        idempotency_key="same-key",
        idempotency_payload_digest=owner_a.analysis_seal.idempotency_payload_digest,
    ).generation_record_id == owner_a.generation_record_id
    assert store.lookup(
        owner_id="owner-b",
        idempotency_key="same-key",
        idempotency_payload_digest=owner_b.analysis_seal.idempotency_payload_digest,
    ).generation_record_id == owner_b.generation_record_id
    with pytest.raises(SourceLockedError) as hidden:
        store.artifact_path(owner_a.artifact.artifact_id, owner_id="owner-b")
    assert hidden.value.detail.code == "ARTIFACT_NOT_FOUND"

    over_capacity = _response(
        source,
        owner_id="owner-a",
        idempotency_key="second-key",
        created_at=created_at,
    )
    with pytest.raises(SourceLockedError) as capacity:
        store.persist(owner_id="owner-a", response=over_capacity, artifact_bytes=source)
    assert capacity.value.detail.code == "OWNER_ARTIFACT_CAPACITY_EXCEEDED"

    orphan = store.artifact_root / ("sla_" + ("f" * 64) + ".pptx")
    orphan.write_bytes(b"orphan")
    old = (created_at - timedelta(seconds=10)).timestamp()
    os.utime(orphan, (old, old))
    stats = store.run_lifecycle_maintenance(now=created_at)
    assert stats.orphan_artifacts_removed == 1
    assert not orphan.exists()

    protected_path, _ = store.artifact_path(
        owner_a.artifact.artifact_id,
        owner_id="owner-a",
    )
    original_unlink = Path.unlink

    def fail_for_open_artifact(path: Path, *args, **kwargs):
        if path.resolve() == protected_path.resolve():
            raise PermissionError("artifact is still open")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_for_open_artifact)
    expired_at = created_at + timedelta(seconds=61)
    stats = store.run_lifecycle_maintenance(now=expired_at)
    assert stats.expired_records_removed == 1
    assert protected_path.is_file()
    with store._connect() as connection:
        remaining = connection.execute(
            "SELECT owner_id FROM generation_records_v2"
        ).fetchall()
    assert [row["owner_id"] for row in remaining] == ["owner-a"]
