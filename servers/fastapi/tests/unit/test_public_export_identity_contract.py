from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import runpy
import uuid

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

import services.public_export_operation as public_export_operation
from migration_revision_contract import (
    REVISION_ACCOUNT_DELETION_OUTBOX,
    REVISION_PUBLIC_ARTIFACT_JOURNAL_REUSE,
)
from models.sql.presentation_export_attempt import PresentationExportAttempt
from models.sql.public_artifact import (
    ArtifactRef as PublicArtifactRef,
    artifact_storage_key,
    PublicArtifact,
    PublicExportIdentity,
    PublicSlideCheckpoint,
)
from models.sql.workspace import WorkspaceArtifact, WorkspaceOperation
from services.artifact_store import DurableArtifactService, LocalDevelopmentArtifactObjectStore
from services.public_export_identity import (
    CanonicalExportIdentity,
    CanonicalSlideRenderIdentity,
    ExportEvidenceDigests,
    PublicExportFenceRejected,
    PublicExportIdentityBindingError,
    PublicExportIdentityV2Disabled,
    SlideCheckpointArtifact,
    acquire_slide_render_authority,
    claim_canonical_export_identity,
    complete_canonical_export_identity,
    find_reusable_slide_render_checkpoint,
    restore_canonical_export_identity,
    reuse_completed_export_identity,
    run_slide_render_singleflight,
)
from services.public_export_operation import (
    build_validated_checkpoint_export_bundle,
    resume_canonical_export_execution,
)
from services.workspace_operation_handler_registry import WorkspaceOperationClaim
from services.workspace_artifact_publication_service import (
    WorkspaceArtifactPublicationError,
    prepare_workspace_export_artifact_ref,
)
from utils.owner_context import reset_current_owner_id, set_current_owner_id


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
        owner_scope=str(owner_id),
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
    # SQLite is metadata-only development support for the durable artifact
    # store: its CAS-key guard refuses to run outside a development profile.
    monkeypatch.setenv("ENVIRONMENT", "local")

    async def scenario() -> None:
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{tmp_path / 'canonical-export.db'}"
        )
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda sync_connection: SQLModel.metadata.create_all(
                    sync_connection,
                    tables=[
                        WorkspaceOperation.__table__,
                        PublicArtifact.__table__,
                        PublicExportIdentity.__table__,
                        PublicSlideCheckpoint.__table__,
                        PresentationExportAttempt.__table__,
                        WorkspaceArtifact.__table__,
                    ],
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
            relocated_slides = tuple(
                replace(
                    checkpoint,
                    artifact=checkpoint.artifact.model_copy(
                        update={
                            "artifact_id": uuid.uuid4(),
                            "created_by_operation_id": uuid.uuid4(),
                        }
                    ),
                )
                for checkpoint in slides
            )
            relocated_identity = replace(identity, slides=relocated_slides)
            assert relocated_identity.canonical_digest == identity.canonical_digest
            assert relocated_identity.checkpoint_payload() != identity.checkpoint_payload()
            cache_only_metrics = public_export_operation._resource_summary(
                [], cache_only=True
            )
            assert cache_only_metrics["worker_execution_count"] == 0
            assert cache_only_metrics["worker_enforcement_layer"] == "cache-only"
            assert cache_only_metrics["cache_only_reuse"] is True

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
            render_identity = CanonicalSlideRenderIdentity(
                owner_id=owner_id,
                tenant_id="tenant-a",
                presentation_id=presentation_id,
                source_revision=7,
                slide_index=0,
                html_digest=_digest("html-0"),
                scene_digest=evidence.scene_digest,
                quality_digest=evidence.quality_digest,
                render_adapter="public-isolated-fidelity",
                renderer_version="public-worker-protocol/v2",
            )
            async with acquire_slide_render_authority(
                session,
                identity=render_identity,
                wait_seconds=1,
            ) as authority_mode:
                assert authority_mode == "process-local-development"
            assert replace(
                render_identity,
                quality_digest=_digest("changed-render-quality"),
            ).canonical_digest != render_identity.canonical_digest
            second_position_identity = replace(render_identity, slide_index=1)
            assert (
                second_position_identity.canonical_digest
                == render_identity.canonical_digest
            )
            with pytest.raises(
                PublicExportIdentityBindingError,
                match="public_slide_render_adapter_invalid",
            ):
                replace(render_identity, render_adapter="editable-hybrid")

            checkpoint_operation = WorkspaceOperation(
                id=producer_operation_id,
                owner_id=owner_id,
                owner_scope=str(owner_id),
                conversation_id=uuid.uuid4(),
                presentation_id=presentation_id,
                anchor_position=1,
                kind="export.pptx",
                state="succeeded",
                idempotency_key_hash=_digest("render-checkpoint-idempotency"),
                request_hash=_digest("render-checkpoint-request"),
                request_payload={},
                expected_revision=7,
                attempt_number=1,
                attempt_token="render-checkpoint-attempt",
                fencing_token=1,
            )
            session.add(checkpoint_operation)
            session.add(
                PublicSlideCheckpoint(
                    operation_id=producer_operation_id,
                    revision=7,
                    slide_index=0,
                    input_digest=render_identity.canonical_digest,
                    stage_version=render_identity.stage_version,
                    artifact_id=slides[0].artifact.artifact_id,
                    attempt_token_hash="sha256:" + _digest("render-checkpoint-attempt"),
                    fencing_token=1,
                )
            )
            await session.commit()
            reusable = await find_reusable_slide_render_checkpoint(
                session,
                identity=render_identity,
            )
            assert reusable == slides[0].artifact
            assert (
                await find_reusable_slide_render_checkpoint(
                    session,
                    identity=second_position_identity,
                )
                == slides[0].artifact
            )
            assert (
                await find_reusable_slide_render_checkpoint(
                    session,
                    identity=replace(
                        render_identity,
                        quality_digest=_digest("other-quality"),
                    ),
                )
                is None
            )

            producer_started = asyncio.Event()
            release_producer = asyncio.Event()
            isolated_render_calls = 0

            async def isolated_render_once(*_args, **_kwargs):
                nonlocal isolated_render_calls
                isolated_render_calls += 1
                producer_started.set()
                await release_producer.wait()
                return slides[0].artifact

            monkeypatch.setattr(
                public_export_operation,
                "execute_isolated_render_operation",
                isolated_render_once,
            )

            async def produce_once():
                return await public_export_operation.execute_isolated_render_operation(
                    initial_operation,
                    input_artifact=slides[0].artifact,
                )

            leader = asyncio.create_task(
                run_slide_render_singleflight(
                    render_identity,
                    produce_once,
                    max_in_flight=4,
                    wait_seconds=2,
                )
            )
            await producer_started.wait()
            followers = [
                asyncio.create_task(
                    run_slide_render_singleflight(
                        second_position_identity,
                        produce_once,
                        max_in_flight=4,
                        wait_seconds=2,
                    )
                )
                for _ in range(99)
            ]
            await asyncio.sleep(0)
            release_producer.set()
            render_results = await asyncio.gather(leader, *followers)
            assert isolated_render_calls == 1
            assert sum(joined for _, joined in render_results) == 99
            assert all(result == slides[0].artifact for result, _ in render_results)
            assert all(
                result.retention_class == "checkpoint"
                for result, _ in render_results
            )

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
            await session.commit()

            monkeypatch.setattr(
                public_export_operation,
                "configured_public_artifact_service",
                lambda: artifacts,
            )

            async def forbid_worker(*_args, **_kwargs):
                raise AssertionError("completed export reuse must not execute a worker")

            monkeypatch.setattr(
                public_export_operation, "execute_isolated_export_operation", forbid_worker
            )
            consumer_result = await public_export_operation.execute_canonical_export_execution(
                consumer_operation, execution=completed, session_factory=sessions
            )
            consumer_ref = consumer_result.artifact
            assert consumer_ref.artifact_id != durable_result.artifact_id
            assert consumer_ref.created_by_operation_id == consumer_operation.operation_id
            assert consumer_ref.model_copy(
                update={
                    "artifact_id": durable_result.artifact_id,
                    "created_by_operation_id": durable_result.created_by_operation_id,
                }
            ) == durable_result
            # Reuse runs no worker, so it reports no worker resource sample —
            # but it must still say what the reused bytes are, or a receipt
            # would describe an editable deck as a raster one.
            assert "role" not in consumer_result.resource_metrics
            assert consumer_result.resource_metrics == {
                "public_export_worker_evidence": {
                    "requestedExportMode": "fidelity-raster",
                    "deliveredExportMode": "fidelity-raster",
                    "fallbackReason": None,
                    "reusedCompletedIdentity": True,
                }
            }
            repeated = await public_export_operation.execute_canonical_export_execution(
                consumer_operation, execution=completed, session_factory=sessions
            )
            assert repeated.artifact == consumer_ref
            with pytest.raises(PublicExportFenceRejected):
                await public_export_operation.execute_canonical_export_execution(
                    replace(consumer_operation, fencing_token=14),
                    execution=completed,
                    session_factory=sessions,
                )

            # Exercise the actual strict staging seam, including its genuine
            # export attempt and both journals sharing the immutable CAS key.
            attempts = {}
            for operation in (recovered_operation, consumer_operation):
                session.add(
                    WorkspaceOperation(
                        id=operation.operation_id,
                        owner_id=owner_id,
                        owner_scope=str(owner_id),
                        conversation_id=operation.conversation_id,
                        presentation_id=presentation_id,
                        anchor_position=1,
                        kind=operation.kind,
                        state="running",
                        idempotency_key_hash=_digest(str(operation.operation_id)),
                        request_hash=_digest("consumer-reuse-request"),
                        request_payload={},
                        expected_revision=7,
                        attempt_number=operation.attempt_number,
                        attempt_token=operation.attempt_token,
                        fencing_token=operation.fencing_token,
                    )
                )
                attempt = PresentationExportAttempt(
                    owner_id=owner_id,
                    owner_scope=str(owner_id),
                    presentation_id=presentation_id,
                    workspace_operation_id=operation.operation_id,
                    format="pptx",
                    state="running",
                    source_revision=7,
                    source_snapshot_sha256=identity.evidence.semantic_digest,
                    request_hash=_digest("consumer-reuse-request"),
                    idempotency_key_hash=_digest(str(operation.operation_id)),
                    requested_adapter="general",
                    provenance={},
                    started_at=datetime.now(timezone.utc),
                )
                session.add(attempt)
                attempts[operation.operation_id] = attempt.id
            await session.commit()

            async def prepare(reference, operation):
                owner_token = set_current_owner_id(owner_id)
                try:
                    async with sessions() as staging_session:
                        return await prepare_workspace_export_artifact_ref(
                            staging_session,
                            reference=reference,
                            operation_id=operation.operation_id,
                            presentation_id=presentation_id,
                            source_revision=7,
                            kind="export.pptx",
                            export_attempt_id=attempts[operation.operation_id],
                        )
                finally:
                    reset_current_owner_id(owner_token)

            producer_journal = await prepare(durable_result, recovered_operation)
            with pytest.raises(
                WorkspaceArtifactPublicationError,
                match="workspace_public_artifact_binding_invalid",
            ):
                await prepare(durable_result, consumer_operation)
            consumer_journal = await prepare(consumer_ref, consumer_operation)
            assert producer_journal.artifact_id != consumer_journal.artifact_id
            assert producer_journal.storage_key == consumer_journal.storage_key
            assert consumer_journal.export_attempt_id == attempts[consumer_operation.operation_id]
            assert consumer_journal.public_artifact_id == consumer_ref.artifact_id
            producer_row = await session.get(PublicArtifact, durable_result.artifact_id)
            assert producer_row.to_ref() == durable_result
            identity_row = await session.get(PublicExportIdentity, completed.singleflight.identity_id)
            assert identity_row.result_artifact_id == durable_result.artifact_id
            assert identity_row.claimed_by_operation_id == recovered_operation.operation_id
            journals = list(
                (await session.scalars(select(WorkspaceArtifact))).all()
            )
            assert len(journals) == 2

            local_values = dict(
                owner_id=owner_id,
                operation_id=consumer_operation.operation_id,
                presentation_id=presentation_id,
                kind="export.pptx",
                status="staging",
                storage_key="workspace-artifacts/local-reuse-test.pptx",
                sha256=consumer_ref.sha256,
                size_bytes=consumer_ref.size_bytes,
                source_revision=7,
            )
            session.add(WorkspaceArtifact(**local_values))
            await session.flush()
            with pytest.raises(IntegrityError):
                async with session.begin_nested():
                    session.add(WorkspaceArtifact(**local_values))
                    await session.flush()
            await session.rollback()
            await session.execute(
                update(PublicArtifact)
                .where(PublicArtifact.id == consumer_ref.artifact_id)
                .values(lifecycle_state="tombstoned")
            )
            await session.commit()
            with pytest.raises(
                WorkspaceArtifactPublicationError,
                match="workspace_public_artifact_binding_invalid",
            ):
                await prepare(consumer_ref, consumer_operation)

        migration = runpy.run_path(
            str(
                Path(__file__).resolve().parents[2]
                / "alembic/versions/a1e5f8c2d7b0_scope_workspace_artifact_storage_uniqueness.py"
            )
        )
        # Pin this migration's own identity and its place in the chain.  The
        # global head deliberately is not asserted here: an unrelated migration
        # added later moves the head without changing anything this contract
        # covers, and asserting it made that a false failure.
        assert migration["revision"] == REVISION_PUBLIC_ARTIFACT_JOURNAL_REUSE
        assert migration["down_revision"] == REVISION_ACCOUNT_DELETION_OUTBOX

        def reject_lossy_downgrade(connection):
            with Operations.context(MigrationContext.configure(connection)):
                with pytest.raises(
                    RuntimeError,
                    match="workspace_artifact_shared_keys_prevent_downgrade",
                ):
                    migration["downgrade"]()

        async with engine.begin() as connection:
            await connection.run_sync(reject_lossy_downgrade)

        await engine.dispose()

    asyncio.run(scenario())


def _hybrid_identity(owner_id: uuid.UUID) -> CanonicalExportIdentity:
    checkpoint = SlideCheckpointArtifact(
        slide_index=0,
        artifact=PublicArtifactRef(
            artifact_id=uuid.uuid4(),
            owner_id=owner_id,
            tenant_id="tenant-a",
            kind="slide-png",
            sha256=_digest("png"),
            size_bytes=64,
            media_type="image/png",
            storage_key=artifact_storage_key(
                owner_id=owner_id, tenant_id="tenant-a", digest=_digest("png")
            ),
            created_by_operation_id=uuid.uuid4(),
            retention_class="checkpoint",
        ),
        validation_digest=_digest("validation"),
    )
    return CanonicalExportIdentity.build(
        owner_id=owner_id,
        tenant_id="tenant-a",
        presentation_id=uuid.uuid4(),
        source_revision=7,
        export_format="pptx",
        export_mode="editable-hybrid",
        render_adapter="public-isolated-hybrid",
        exporter_version="pptx-exporter-2.0",
        evidence=ExportEvidenceDigests(
            scene_digest=_digest("scene"),
            semantic_digest=_digest("semantic"),
            quality_digest=_digest("quality"),
            exporter_digest=_digest("exporter"),
            font_manifest_digest=_digest("fonts"),
        ),
        slides=(checkpoint,),
    )


def test_hybrid_export_bundle_names_the_authored_sources_it_measures() -> None:
    """The worker reads its own verified cache, never a database, for Hybrid."""

    owner_id = uuid.uuid4()
    identity = _hybrid_identity(owner_id)
    source = PublicArtifactRef(
        artifact_id=uuid.uuid4(),
        owner_id=owner_id,
        tenant_id="tenant-a",
        kind="slide-html",
        sha256=_digest("html"),
        size_bytes=32,
        media_type="text/html",
        storage_key=artifact_storage_key(
            owner_id=owner_id, tenant_id="tenant-a", digest=_digest("html")
        ),
        created_by_operation_id=uuid.uuid4(),
        retention_class="ephemeral",
    )

    bundle = json.loads(
        build_validated_checkpoint_export_bundle(
            identity, source_slides=(source,), font_embedding=True
        )
    )

    assert bundle["exportMode"] == "editable-hybrid"
    assert bundle["fontEmbedding"] is True
    assert bundle["sourceSlides"] == [
        {
            "slideIndex": 0,
            "artifactRef": json.loads(
                json.dumps(source.model_dump(mode="json", by_alias=True))
            ),
        }
    ]
    # The bundle names references only; authored markup never rides the wire.
    assert "<" not in json.dumps(bundle)

    with pytest.raises(
        public_export_operation.PublicExportOperationError,
        match="public_export_hybrid_sources_invalid",
    ):
        build_validated_checkpoint_export_bundle(identity, source_slides=())


def test_fidelity_export_bundle_never_names_authored_sources() -> None:
    owner_id = uuid.uuid4()
    identity = replace(
        _hybrid_identity(owner_id),
        export_mode="fidelity-raster",
        render_adapter="public-isolated-fidelity",
    )

    bundle = json.loads(build_validated_checkpoint_export_bundle(identity))

    assert "sourceSlides" not in bundle and "fontEmbedding" not in bundle
    assert "html" not in json.dumps(bundle).lower()


def _declared_slide(slide_index: int) -> dict:
    return {
        "schemaVersion": "public-hybrid-declared-semantic/v1",
        "slideIndex": slide_index,
        "bundleDigest": "sha256:" + _digest(f"bundle-{slide_index}"),
        "fontSetDigest": "sha256:" + _digest("fonts"),
        "declaredElementIds": ["q-a"],
        "declaredElements": [
            {
                "elementId": "q-a",
                "kind": "shape",
                "sourceDigest": "sha256:" + _digest("q-a"),
                "childIds": ["q-b"],
            }
        ],
    }


def test_hybrid_bundle_carries_the_declared_graph_only_when_fully_bound() -> None:
    """The graph decides what may be promoted, so its binding is not optional."""

    owner_id = uuid.uuid4()
    identity = _hybrid_identity(owner_id)
    source = PublicArtifactRef(
        artifact_id=uuid.uuid4(),
        owner_id=owner_id,
        tenant_id="tenant-a",
        kind="slide-html",
        sha256=_digest("html"),
        size_bytes=32,
        media_type="text/html",
        storage_key=artifact_storage_key(
            owner_id=owner_id, tenant_id="tenant-a", digest=_digest("html")
        ),
        created_by_operation_id=uuid.uuid4(),
        retention_class="ephemeral",
    )
    semantic = _declared_slide(0)
    attempt_id = str(uuid.uuid4())
    renderer_digest = "sha256:" + _digest("runtime")

    bundle = json.loads(
        build_validated_checkpoint_export_bundle(
            identity,
            source_slides=(source,),
            semantic_slides=(semantic,),
            export_attempt_id=attempt_id,
            renderer_source_digest=renderer_digest,
            font_embedding=True,
        )
    )
    assert bundle["exportAttemptId"] == attempt_id
    assert bundle["rendererSourceDigest"] == renderer_digest
    assert [item["slideIndex"] for item in bundle["semanticSlides"]] == [0]
    assert bundle["semanticSlides"][0]["declaredElements"][0]["childIds"] == ["q-b"]
    # Only declared identity and structure cross the boundary; the graph
    # carries no markup, only element ids, kinds and ownership edges.
    declared = json.dumps(bundle["semanticSlides"])
    assert "<" not in declared and "html" not in declared.lower()

    # A graph without the attempt/renderer identity it is sealed against, or
    # one that does not cover every slide, is refused rather than half-shipped.
    for kwargs in (
        {"export_attempt_id": None, "renderer_source_digest": renderer_digest},
        {"export_attempt_id": attempt_id, "renderer_source_digest": None},
    ):
        with pytest.raises(
            public_export_operation.PublicExportOperationError,
            match="public_export_hybrid_semantic_binding_invalid",
        ):
            build_validated_checkpoint_export_bundle(
                identity,
                source_slides=(source,),
                semantic_slides=(semantic,),
                **kwargs,
            )
    with pytest.raises(
        public_export_operation.PublicExportOperationError,
        match="public_export_hybrid_semantic_binding_invalid",
    ):
        build_validated_checkpoint_export_bundle(
            identity,
            source_slides=(source,),
            semantic_slides=(semantic, _declared_slide(1)),
            export_attempt_id=attempt_id,
            renderer_source_digest=renderer_digest,
        )

    # Without a graph the bundle is byte-identical to the observed-only form.
    without = json.loads(
        build_validated_checkpoint_export_bundle(
            identity, source_slides=(source,), font_embedding=True
        )
    )
    assert "semanticSlides" not in without
    assert "exportAttemptId" not in without


def test_declared_semantic_slide_payload_projects_the_assembler_wire_shape() -> None:
    """Only declared identity and structure cross; no HTML, no session, no bounds."""

    class _Registry:
        active_qids = ["q-a", "q-b"]

    class _Element:
        def __init__(self, payload):
            self._payload = payload

        def model_dump(self, **_kwargs):
            return dict(self._payload)

    class _Component:
        identity_registry = _Registry()
        elements = [
            _Element(
                {
                    "elementId": "q-a",
                    "kind": "shape",
                    "parentId": None,
                    "childIds": ["q-b"],
                    "requiredFeatures": [],
                    "payload": {"preset": "rectangle"},
                }
            ),
            _Element(
                {
                    "elementId": "q-b",
                    "kind": "text",
                    "parentId": "q-a",
                    "childIds": [],
                }
            ),
        ]

    class _FontManifest:
        digest = "sha256:" + _digest("fonts")

    class _Bundle:
        bundle_digest = "sha256:" + _digest("bundle")
        font_manifest = _FontManifest()
        semantic_component = _Component()

    payload = public_export_operation.declared_semantic_slide_payload(_Bundle(), 3)

    assert payload["schemaVersion"] == "public-hybrid-declared-semantic/v1"
    assert payload["slideIndex"] == 3
    assert payload["declaredElementIds"] == ["q-a", "q-b"]
    container, text = payload["declaredElements"]
    assert container["childIds"] == ["q-b"] and container["kind"] == "shape"
    # A shape container with children is the only group candidate this deck
    # kind produces, so childIds must survive the projection.
    assert text["parentId"] == "q-a"
    for element in payload["declaredElements"]:
        assert re.fullmatch(r"sha256:[0-9a-f]{64}", element["sourceDigest"])
        # A non-chart/table payload is structure the assembler must not read.
        assert "payload" not in element

    class _Empty:
        semantic_component = None

    assert public_export_operation.declared_semantic_slide_payload(_Empty(), 0) is None


def test_hybrid_declared_graph_ships_only_on_explicit_opt_in() -> None:
    assert public_export_operation.public_export_hybrid_semantic_enabled({}) is False
    assert (
        public_export_operation.public_export_hybrid_semantic_enabled(
            {"PUBLIC_EXPORT_HYBRID_SEMANTIC_ENABLED": "1"}
        )
        is False
    )
    assert (
        public_export_operation.public_export_hybrid_semantic_enabled(
            {"PUBLIC_EXPORT_HYBRID_SEMANTIC_ENABLED": "true"}
        )
        is True
    )
