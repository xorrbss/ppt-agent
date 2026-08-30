from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from models.sql.public_artifact import PublicArtifact, PublicExportIdentity
from services.artifact_store import DurableArtifactService, LocalDevelopmentArtifactObjectStore
from services.public_export_identity import (
    CanonicalExportIdentity,
    ExportEvidenceDigests,
    PublicExportFenceRejected,
    PublicExportIdentityBindingError,
    PublicExportIdentityV2Disabled,
    SlideCheckpointArtifact,
    claim_canonical_export_identity,
    complete_canonical_export_identity,
    restore_canonical_export_identity,
    reuse_completed_export_identity,
)
from services.public_export_operation import (
    build_validated_checkpoint_export_bundle,
    resume_canonical_export_execution,
)
from services.workspace_operation_handler_registry import WorkspaceOperationClaim


PPTX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.presentationml.presentation"
)


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _operation_claim(
    *,
    operation_id: uuid.UUID,
    owner_id: uuid.UUID,
    presentation_id: uuid.UUID,
    attempt_number: int,
    attempt_token: str,
    fencing_token: int,
) -> WorkspaceOperationClaim:
    return WorkspaceOperationClaim(
        operation_id=operation_id,
        owner_id=owner_id,
        owner_scope="owner",
        conversation_id=uuid.uuid4(),
        presentation_id=presentation_id,
        kind="export.pptx",
        request_payload={},
        expected_revision=7,
        attempt_number=attempt_number,
        attempt_token=attempt_token,
        fencing_token=fencing_token,
    )


def test_canonical_export_identity_is_feature_gated_digest_bound_and_fenced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{tmp_path / 'canonical-export.db'}"
        )
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda sync_connection: SQLModel.metadata.create_all(
                    sync_connection,
                    tables=[PublicArtifact.__table__, PublicExportIdentity.__table__],
                )
            )
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        artifacts = DurableArtifactService(
            LocalDevelopmentArtifactObjectStore(tmp_path / "objects")
        )
        owner_id = uuid.uuid4()
        presentation_id = uuid.uuid4()
        producer_operation_id = uuid.uuid4()

        async with sessions() as session:
            slides = []
            for slide_index in range(2):
                payload = f"png-{slide_index}".encode()
                reference = await artifacts.persist_bytes(
                    session,
                    owner_id=owner_id,
                    tenant_id="tenant-a",
                    kind="slide-png",
                    payload=payload,
                    media_type="image/png",
                    created_by_operation_id=producer_operation_id,
                    retention_class="checkpoint",
                )
                slides.append(
                    SlideCheckpointArtifact(
                        slide_index=slide_index,
                        artifact=reference,
                        validation_digest=_digest(f"validation-{slide_index}"),
                    )
                )
            evidence = ExportEvidenceDigests(
                scene_digest=_digest("scene"),
                semantic_digest=_digest("semantic"),
                quality_digest=_digest("quality"),
                exporter_digest=_digest("exporter"),
                font_manifest_digest=_digest("fonts"),
            )
            identity = CanonicalExportIdentity.build(
                owner_id=owner_id,
                tenant_id="tenant-a",
                presentation_id=presentation_id,
                source_revision=7,
                export_format="pptx",
                export_mode="fidelity-raster",
                render_adapter="chromium-raster-v2",
                exporter_version="pptx-exporter-2.0",
                evidence=evidence,
                slides=slides,
            )

            for field_name in (
                "scene_digest",
                "semantic_digest",
                "quality_digest",
                "exporter_digest",
            ):
                changed_evidence = replace(
                    evidence,
                    **{field_name: _digest(f"changed-{field_name}")},
                )
                assert replace(identity, evidence=changed_evidence).canonical_digest != identity.canonical_digest
            assert replace(identity, render_adapter="other-adapter").canonical_digest != identity.canonical_digest

            bundle = json.loads(build_validated_checkpoint_export_bundle(identity))
            assert bundle["canonicalExportIdentity"] == identity.canonical_digest
            assert [slide["slideIndex"] for slide in bundle["slides"]] == [0, 1]
            assert [slide["artifactRef"]["artifactId"] for slide in bundle["slides"]] == [
                str(reference.artifact_id) for reference in (item.artifact for item in slides)
            ]
            assert "html" not in json.dumps(bundle).lower()

            source_checkpoint = await artifacts.persist_bytes(
                session,
                owner_id=owner_id,
                tenant_id="tenant-a",
                kind="checkpoint",
                payload=identity.checkpoint_payload(),
                media_type="application/json",
                created_by_operation_id=producer_operation_id,
                retention_class="checkpoint",
            )
            durable_checkpoint_payload = await artifacts.read_ref_bytes(
                source_checkpoint
            )
            restored_identity = restore_canonical_export_identity(
                source_checkpoint,
                durable_checkpoint_payload,
            )
            assert restored_identity == identity
            tampered_checkpoint = bytearray(durable_checkpoint_payload)
            tampered_checkpoint[-2] = ord("0") if tampered_checkpoint[-2] != ord("0") else ord("1")
            with pytest.raises(
                PublicExportIdentityBindingError,
                match="public_export_checkpoint_digest_mismatch",
            ):
                restore_canonical_export_identity(
                    source_checkpoint,
                    bytes(tampered_checkpoint),
                )
            initial_operation = _operation_claim(
                operation_id=producer_operation_id,
                owner_id=owner_id,
                presentation_id=presentation_id,
                attempt_number=1,
                attempt_token="operation-attempt-1",
                fencing_token=11,
            )
            with pytest.raises(
                PublicExportIdentityV2Disabled,
                match="public_export_identity_v2_disabled",
            ):
                await claim_canonical_export_identity(
                    session,
                    identity=identity,
                    source_checkpoint=source_checkpoint,
                    operation_claim=initial_operation,
                    worker_id="node-a",
                    lease_seconds=30,
                )

            monkeypatch.setenv("PUBLIC_EXPORT_IDENTITY_V2_ENABLED", "true")
            started = datetime(2026, 8, 30, tzinfo=timezone.utc)
            first = await claim_canonical_export_identity(
                session,
                identity=identity,
                source_checkpoint=source_checkpoint,
                operation_claim=initial_operation,
                worker_id="node-a",
                lease_seconds=30,
                now=started,
            )
            assert first.should_execute
            await session.commit()

            dispositions = [first.singleflight.disposition]
            monkeypatch.setattr(
                "services.public_export_operation.configured_public_artifact_service",
                lambda: artifacts,
            )
            resumed_operation = _operation_claim(
                operation_id=uuid.uuid4(),
                owner_id=owner_id,
                presentation_id=presentation_id,
                attempt_number=1,
                attempt_token="resumed-operation-attempt",
                fencing_token=1,
            )
            async with sessions() as resumed_session:
                resumed = await resume_canonical_export_execution(
                    resumed_session,
                    resumed_operation,
                    source_checkpoint=source_checkpoint,
                    worker_id="node-resume",
                    lease_seconds=30,
                    now=started + timedelta(seconds=1),
                )
                assert resumed.identity == identity
                dispositions.append(resumed.singleflight.disposition)

            for index in range(2, 100):
                duplicate_operation = _operation_claim(
                    operation_id=uuid.uuid4(),
                    owner_id=owner_id,
                    presentation_id=presentation_id,
                    attempt_number=1,
                    attempt_token=f"duplicate-attempt-{index}",
                    fencing_token=1,
                )
                async with sessions() as duplicate_session:
                    duplicate = await claim_canonical_export_identity(
                        duplicate_session,
                        identity=identity,
                        source_checkpoint=source_checkpoint,
                        operation_claim=duplicate_operation,
                        worker_id=f"node-{index % 3}",
                        lease_seconds=30,
                        now=started + timedelta(seconds=1),
                    )
                    dispositions.append(duplicate.singleflight.disposition)
            assert dispositions.count("claimed") == 1
            assert dispositions.count("in_flight") == 99

            stale_result = await artifacts.persist_bytes(
                session,
                owner_id=owner_id,
                tenant_id="tenant-a",
                kind="presentation-pptx",
                payload=b"stale-pptx",
                media_type=PPTX_MEDIA_TYPE,
                created_by_operation_id=producer_operation_id,
                retention_class="published",
            )
            recovered_operation = _operation_claim(
                operation_id=uuid.uuid4(),
                owner_id=owner_id,
                presentation_id=presentation_id,
                attempt_number=1,
                attempt_token="operation-attempt-2",
                fencing_token=12,
            )
            recovered = await claim_canonical_export_identity(
                session,
                identity=identity,
                source_checkpoint=source_checkpoint,
                operation_claim=recovered_operation,
                worker_id="node-b",
                lease_seconds=30,
                now=started + timedelta(seconds=31),
            )
            assert recovered.should_execute
            assert recovered.singleflight.fencing_token == first.singleflight.fencing_token + 1

            with pytest.raises(
                PublicExportFenceRejected,
                match="public_export_singleflight_fence_stale",
            ):
                await complete_canonical_export_identity(
                    session,
                    execution=first,
                    operation_claim=initial_operation,
                    result_artifact=stale_result,
                )

            durable_result = await artifacts.persist_bytes(
                session,
                owner_id=owner_id,
                tenant_id="tenant-a",
                kind="presentation-pptx",
                payload=b"durable-pptx",
                media_type=PPTX_MEDIA_TYPE,
                created_by_operation_id=recovered_operation.operation_id,
                retention_class="published",
            )
            candidate = await complete_canonical_export_identity(
                session,
                execution=recovered,
                operation_claim=recovered_operation,
                result_artifact=durable_result,
            )
            candidate.validate_for_claim(recovered_operation)
            with pytest.raises(
                PublicExportFenceRejected,
                match="public_export_publication_fence_stale",
            ):
                candidate.validate_for_claim(initial_operation)
            await session.commit()

            consumer_operation = _operation_claim(
                operation_id=uuid.uuid4(),
                owner_id=owner_id,
                presentation_id=presentation_id,
                attempt_number=1,
                attempt_token="operation-attempt-3",
                fencing_token=13,
            )
            completed = await claim_canonical_export_identity(
                session,
                identity=identity,
                source_checkpoint=source_checkpoint,
                operation_claim=consumer_operation,
                worker_id="node-c",
                lease_seconds=30,
                now=started + timedelta(seconds=32),
            )
            reused = reuse_completed_export_identity(
                execution=completed,
                operation_claim=consumer_operation,
            )
            assert reused.reused_singleflight_result is True
            assert reused.artifact == durable_result
            reused.validate_for_claim(consumer_operation)

        await engine.dispose()

    asyncio.run(scenario())
