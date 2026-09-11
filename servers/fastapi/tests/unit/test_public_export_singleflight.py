"""Cluster-wide export single-flight: one identity means one execution.

The export identity row in ``PublicExportIdentity`` is the authority, so these
tests exercise the seam a submission actually goes through — claim the identity,
then run ``execute_canonical_export_execution`` with a worker that counts its own
calls. What the assertions care about is how many times the worker ran, and what
the second submission got back.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import io
from pathlib import Path
from typing import Any, Mapping
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

import services.public_export_operation as public_export_operation
from models.sql.presentation_export_attempt import PresentationExportAttempt
from models.sql.public_artifact import (
    ArtifactRef as PublicArtifactRef,
    artifact_storage_key,
    PublicArtifact,
    PublicExportIdentity,
    PublicSlideCheckpoint,
)
from models.sql.workspace import WorkspaceArtifact, WorkspaceOperation
from services.artifact_store import (
    DurableArtifactService,
    LocalDevelopmentArtifactObjectStore,
)
from services.public_export_identity import (
    CanonicalExportIdentity,
    ExportEvidenceDigests,
    SlideCheckpointArtifact,
    claim_canonical_export_identity,
)
from services.public_export_operation import (
    PublicExportOperationError,
    execute_canonical_export_execution,
)
from services.workspace_operation_handler_registry import WorkspaceOperationClaim


PPTX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.presentationml.presentation"
)


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _operation_claim(
    *,
    owner_id: uuid.UUID,
    presentation_id: uuid.UUID,
    attempt_token: str,
    fencing_token: int,
    operation_id: uuid.UUID | None = None,
) -> WorkspaceOperationClaim:
    return WorkspaceOperationClaim(
        operation_id=operation_id or uuid.uuid4(),
        owner_id=owner_id,
        owner_scope=str(owner_id),
        conversation_id=uuid.uuid4(),
        presentation_id=presentation_id,
        kind="export.pptx",
        request_payload={},
        expected_revision=7,
        attempt_number=1,
        attempt_token=attempt_token,
        fencing_token=fencing_token,
    )


@dataclass(frozen=True)
class _WorkerResult:
    """The subset of the isolated worker result this seam reads."""

    ok: bool
    role: str
    output_artifact: Mapping[str, Any]
    resource_metrics: Mapping[str, Any]
    execution_evidence: Mapping[str, Any]
    error_code: str | None = None


class _ExportHarness:
    """One owner, one presentation, one committed pair of slide checkpoints."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.owner_id = uuid.uuid4()
        self.presentation_id = uuid.uuid4()
        self.producer_operation_id = uuid.uuid4()
        self.tenant_id = "tenant-a"
        self.worker_calls: list[uuid.UUID] = []

    async def start(self) -> None:
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{self.tmp_path / 'export-singleflight.db'}"
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
        self.engine = engine
        self.sessions = async_sessionmaker(engine, expire_on_commit=False)
        self.object_store = LocalDevelopmentArtifactObjectStore(
            self.tmp_path / "objects"
        )
        self.artifacts = DurableArtifactService(self.object_store)
        async with self.sessions() as session:
            self.slides = [
                SlideCheckpointArtifact(
                    slide_index=index,
                    artifact=await self.artifacts.persist_bytes(
                        session,
                        owner_id=self.owner_id,
                        tenant_id=self.tenant_id,
                        kind="slide-png",
                        payload=f"png-{index}".encode(),
                        media_type="image/png",
                        created_by_operation_id=self.producer_operation_id,
                        retention_class="checkpoint",
                    ),
                    validation_digest=_digest(f"validation-{index}"),
                )
                for index in range(2)
            ]
            await session.commit()

    async def stop(self) -> None:
        await self.engine.dispose()

    def identity(self, *, font_manifest_digest: str) -> CanonicalExportIdentity:
        return CanonicalExportIdentity.build(
            owner_id=self.owner_id,
            tenant_id=self.tenant_id,
            presentation_id=self.presentation_id,
            source_revision=7,
            export_format="pptx",
            export_mode="fidelity-raster",
            render_adapter="public-isolated-fidelity",
            exporter_version="pptx-exporter-2.0",
            evidence=ExportEvidenceDigests(
                scene_digest=_digest("scene"),
                semantic_digest=_digest("semantic"),
                quality_digest=_digest("quality"),
                exporter_digest=_digest("exporter"),
                font_manifest_digest=font_manifest_digest,
            ),
            slides=self.slides,
        )

    async def source_checkpoint(
        self, identity: CanonicalExportIdentity
    ) -> PublicArtifactRef:
        async with self.sessions() as session:
            reference = await self.artifacts.persist_bytes(
                session,
                owner_id=self.owner_id,
                tenant_id=self.tenant_id,
                kind="checkpoint",
                payload=identity.checkpoint_payload(),
                media_type="application/json",
                created_by_operation_id=self.producer_operation_id,
                retention_class="checkpoint",
            )
            await session.commit()
            return reference

    async def claim(
        self,
        identity: CanonicalExportIdentity,
        source_checkpoint: PublicArtifactRef,
        operation: WorkspaceOperationClaim,
        *,
        worker_id: str,
        lease_seconds: int = 30,
        now: datetime | None = None,
    ):
        async with self.sessions() as session:
            execution = await claim_canonical_export_identity(
                session,
                identity=identity,
                source_checkpoint=source_checkpoint,
                operation_claim=operation,
                worker_id=worker_id,
                lease_seconds=lease_seconds,
                now=now,
            )
            await session.commit()
            return execution

    async def worker(
        self,
        claim: WorkspaceOperationClaim,
        *,
        input_artifact: Mapping[str, Any],
        deadline_seconds: int | None = None,
    ) -> _WorkerResult:
        """Stand in for the isolated exporter: real bytes, counted calls."""

        self.worker_calls.append(claim.operation_id)
        payload = b"exported-pptx-bytes"
        digest = sha256(payload).hexdigest()
        reference = PublicArtifactRef(
            artifact_id=uuid.uuid4(),
            owner_id=self.owner_id,
            tenant_id=self.tenant_id,
            kind="presentation-pptx",
            sha256=digest,
            size_bytes=len(payload),
            media_type=PPTX_MEDIA_TYPE,
            storage_key=artifact_storage_key(
                owner_id=self.owner_id,
                tenant_id=self.tenant_id,
                digest=digest,
            ),
            created_by_operation_id=claim.operation_id,
            retention_class="published",
        )
        await self.object_store.put_stream(reference, io.BytesIO(payload))
        return _WorkerResult(
            ok=True,
            role="export",
            output_artifact=reference.model_dump(mode="json", by_alias=True),
            resource_metrics={"trustedSchedulerQueueWaitMs": 0},
            execution_evidence={"deliveredExportMode": "fidelity-raster"},
        )

    async def published_artifact_count(self) -> int:
        return len(await self.published_artifacts())

    async def published_digests(self) -> set[str]:
        return {row.sha256 for row in await self.published_artifacts()}

    async def published_artifacts(self) -> list[PublicArtifact]:
        async with self.sessions() as session:
            rows = await session.execute(
                select(PublicArtifact).where(
                    PublicArtifact.kind == "presentation-pptx"
                )
            )
            return list(rows.scalars().all())

    async def identity_rows(self) -> list[PublicExportIdentity]:
        async with self.sessions() as session:
            rows = await session.execute(select(PublicExportIdentity))
            return list(rows.scalars().all())


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # SQLite is metadata-only development support for the durable artifact
    # store: its CAS-key guard refuses to run outside a development profile.
    monkeypatch.setenv("ENVIRONMENT", "local")
    built = _ExportHarness(tmp_path)

    async def start() -> _ExportHarness:
        await built.start()
        monkeypatch.setattr(
            public_export_operation,
            "configured_public_artifact_service",
            lambda: built.artifacts,
        )
        monkeypatch.setattr(
            public_export_operation,
            "execute_isolated_export_operation",
            built.worker,
        )
        return built

    yield start
    asyncio.run(built.stop())


def test_same_identity_executes_once_and_hands_back_the_first_artifact(
    harness,
) -> None:
    async def scenario() -> None:
        export = await harness()
        identity = export.identity(font_manifest_digest=_digest("fonts"))
        source_checkpoint = await export.source_checkpoint(identity)
        started = datetime(2026, 9, 12, tzinfo=timezone.utc)

        winner_operation = _operation_claim(
            owner_id=export.owner_id,
            presentation_id=export.presentation_id,
            attempt_token="winner-attempt",
            fencing_token=11,
        )
        loser_operation = _operation_claim(
            owner_id=export.owner_id,
            presentation_id=export.presentation_id,
            attempt_token="loser-attempt",
            fencing_token=12,
        )
        winner = await export.claim(
            identity,
            source_checkpoint,
            winner_operation,
            worker_id="node-a",
            now=started,
        )
        loser = await export.claim(
            identity,
            source_checkpoint,
            loser_operation,
            worker_id="node-b",
            now=started + timedelta(seconds=1),
        )
        assert winner.singleflight.disposition == "claimed"
        assert winner.should_execute is True
        assert loser.singleflight.disposition == "in_flight"
        assert loser.should_execute is False
        assert len(await export.identity_rows()) == 1

        # The loser is refused execution while the winner's lease is live: the
        # stable in-flight outcome, not a second worker.
        with pytest.raises(
            PublicExportOperationError,
            match="public_export_singleflight_in_flight",
        ):
            await execute_canonical_export_execution(
                loser_operation,
                execution=loser,
                session_factory=export.sessions,
            )
        assert export.worker_calls == []
        assert await export.published_artifact_count() == 0

        produced = await execute_canonical_export_execution(
            winner_operation,
            execution=winner,
            session_factory=export.sessions,
        )
        assert export.worker_calls == [winner_operation.operation_id]
        assert await export.published_artifact_count() == 1

        # Re-submission after completion joins the completed identity and is
        # handed the winner's bytes; no second worker, no second object.
        rejoin_operation = _operation_claim(
            owner_id=export.owner_id,
            presentation_id=export.presentation_id,
            attempt_token="rejoin-attempt",
            fencing_token=13,
        )
        rejoined = await export.claim(
            identity,
            source_checkpoint,
            rejoin_operation,
            worker_id="node-c",
            now=started + timedelta(seconds=2),
        )
        assert rejoined.singleflight.disposition == "completed"
        reused = await execute_canonical_export_execution(
            rejoin_operation,
            execution=rejoined,
            session_factory=export.sessions,
        )
        assert export.worker_calls == [winner_operation.operation_id]
        assert reused.artifact.sha256 == produced.artifact.sha256
        assert reused.artifact.storage_key == produced.artifact.storage_key
        assert reused.resource_metrics["public_export_worker_evidence"][
            "reusedCompletedIdentity"
        ] is True
        # Reuse registers a consumer-scoped metadata row so the producer's ref
        # stays immutable; the bytes are the winner's, and only one object and
        # one identity row exist.
        assert reused.artifact.artifact_id != produced.artifact.artifact_id
        assert reused.artifact.created_by_operation_id == (
            rejoin_operation.operation_id
        )
        assert await export.published_digests() == {produced.artifact.sha256}
        assert len(await export.identity_rows()) == 1

    asyncio.run(scenario())


def test_a_different_font_manifest_digest_is_a_different_export(harness) -> None:
    async def scenario() -> None:
        export = await harness()
        first_identity = export.identity(font_manifest_digest=_digest("fonts-a"))
        second_identity = export.identity(font_manifest_digest=_digest("fonts-b"))
        assert first_identity.canonical_digest != second_identity.canonical_digest
        started = datetime(2026, 9, 12, tzinfo=timezone.utc)

        results = []
        for index, identity in enumerate((first_identity, second_identity)):
            source_checkpoint = await export.source_checkpoint(identity)
            operation = _operation_claim(
                owner_id=export.owner_id,
                presentation_id=export.presentation_id,
                attempt_token=f"font-attempt-{index}",
                fencing_token=20 + index,
            )
            execution = await export.claim(
                identity,
                source_checkpoint,
                operation,
                worker_id=f"node-{index}",
                now=started + timedelta(seconds=index),
            )
            assert execution.singleflight.disposition == "claimed"
            results.append(
                await execute_canonical_export_execution(
                    operation,
                    execution=execution,
                    session_factory=export.sessions,
                )
            )

        assert len(export.worker_calls) == 2
        rows = await export.identity_rows()
        assert len(rows) == 2
        assert {row.font_manifest_digest for row in rows} == {
            first_identity.evidence.font_manifest_digest,
            second_identity.evidence.font_manifest_digest,
        }
        assert all(row.state == "succeeded" for row in rows)
        # Same input bytes, so the CAS key is shared; the two identities are
        # still separate rows pointing at their own completion.
        assert results[0].artifact.sha256 == results[1].artifact.sha256

    asyncio.run(scenario())


def test_a_losing_racer_publishes_no_second_artifact(harness) -> None:
    async def scenario() -> None:
        export = await harness()
        identity = export.identity(font_manifest_digest=_digest("fonts"))
        source_checkpoint = await export.source_checkpoint(identity)
        started = datetime(2026, 9, 12, tzinfo=timezone.utc)

        operations = [
            _operation_claim(
                owner_id=export.owner_id,
                presentation_id=export.presentation_id,
                attempt_token=f"racer-{index}",
                fencing_token=30 + index,
            )
            for index in range(8)
        ]
        executions = []
        for index, operation in enumerate(operations):
            executions.append(
                await export.claim(
                    identity,
                    source_checkpoint,
                    operation,
                    worker_id=f"node-{index}",
                    now=started,
                )
            )
        dispositions = [
            execution.singleflight.disposition for execution in executions
        ]
        assert dispositions.count("claimed") == 1
        assert dispositions.count("in_flight") == len(operations) - 1

        outcomes = await asyncio.gather(
            *(
                execute_canonical_export_execution(
                    operation,
                    execution=execution,
                    session_factory=export.sessions,
                )
                for operation, execution in zip(
                    operations, executions, strict=True
                )
            ),
            return_exceptions=True,
        )
        succeeded = [
            outcome
            for outcome in outcomes
            if not isinstance(outcome, BaseException)
        ]
        refused = [
            outcome
            for outcome in outcomes
            if isinstance(outcome, PublicExportOperationError)
        ]
        assert len(succeeded) == 1
        assert len(refused) == len(operations) - 1
        assert all(
            "public_export_singleflight_in_flight" in str(outcome)
            for outcome in refused
        )
        assert len(export.worker_calls) == 1
        assert await export.published_artifact_count() == 1
        rows = await export.identity_rows()
        assert len(rows) == 1
        assert rows[0].state == "succeeded"
        assert rows[0].result_artifact_id == succeeded[0].artifact.artifact_id

    asyncio.run(scenario())


def test_a_crashed_attempt_row_does_not_deadlock_the_retry(harness) -> None:
    """A crashed winner leaves a ``running`` row behind; the lease frees it.

    ``claim_export_identity`` reclaims a row whose lease has expired (and any
    ``failed`` row) by bumping the fencing token, so the retry executes rather
    than blocking forever behind a worker that is gone.
    """

    async def scenario() -> None:
        export = await harness()
        identity = export.identity(font_manifest_digest=_digest("fonts"))
        source_checkpoint = await export.source_checkpoint(identity)
        started = datetime(2026, 9, 12, tzinfo=timezone.utc)

        crashed_operation = _operation_claim(
            owner_id=export.owner_id,
            presentation_id=export.presentation_id,
            attempt_token="crashed-attempt",
            fencing_token=41,
        )
        crashed = await export.claim(
            identity,
            source_checkpoint,
            crashed_operation,
            worker_id="node-crashed",
            lease_seconds=30,
            now=started,
        )
        assert crashed.singleflight.disposition == "claimed"
        # The worker dies here: no completion, no failure, just a running row.

        # Inside the lease the retry still sees a live attempt and waits.
        blocked_operation = _operation_claim(
            owner_id=export.owner_id,
            presentation_id=export.presentation_id,
            attempt_token="blocked-attempt",
            fencing_token=42,
        )
        blocked = await export.claim(
            identity,
            source_checkpoint,
            blocked_operation,
            worker_id="node-retry",
            now=started + timedelta(seconds=5),
        )
        assert blocked.singleflight.disposition == "in_flight"

        # Past the lease the same stale row is reclaimed, not deadlocked.
        retry_operation = _operation_claim(
            owner_id=export.owner_id,
            presentation_id=export.presentation_id,
            attempt_token="retry-attempt",
            fencing_token=43,
        )
        retry = await export.claim(
            identity,
            source_checkpoint,
            retry_operation,
            worker_id="node-retry",
            now=started + timedelta(seconds=31),
        )
        assert retry.singleflight.disposition == "claimed"
        assert retry.should_execute is True
        assert (
            retry.singleflight.fencing_token
            == crashed.singleflight.fencing_token + 1
        )
        assert retry.singleflight.identity_id == crashed.singleflight.identity_id

        produced = await execute_canonical_export_execution(
            retry_operation,
            execution=retry,
            session_factory=export.sessions,
        )
        assert export.worker_calls == [retry_operation.operation_id]
        rows = await export.identity_rows()
        assert len(rows) == 1
        assert rows[0].state == "succeeded"
        assert rows[0].attempt_count == 2
        assert rows[0].result_artifact_id == produced.artifact.artifact_id

        # The crashed attempt's fence is dead: it can never complete the row it
        # abandoned, so a late return from the lost worker cannot overwrite the
        # retry's published result.
        with pytest.raises(Exception) as fenced:
            async with export.sessions() as session:
                from services.public_export_identity import (
                    complete_canonical_export_identity,
                )

                await complete_canonical_export_identity(
                    session,
                    execution=replace(
                        crashed,
                        identity=crashed.identity,
                    ),
                    operation_claim=crashed_operation,
                    result_artifact=produced.artifact.model_copy(
                        update={
                            "created_by_operation_id": (
                                crashed_operation.operation_id
                            )
                        }
                    ),
                )
        assert "public_export_singleflight_fence_stale" in str(fenced.value)

    asyncio.run(scenario())
