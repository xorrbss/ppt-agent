"""SVG exports join the digest-bound export identity (``svg-native``).

These drive ``export_svg_through_identity`` against a real SQLite database and
the durable artifact service, with a counting stand-in for the svg worker. What
matters is how many times the worker ran and what a duplicate submission of the
same revision and format got back.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import io
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

import services.public_export_operation as public_export_operation
import services.svg_export_identity as svg_export_identity
from models.sql.public_admission import PublicOperationAdmission
from models.sql.public_artifact import (
    ArtifactRef,
    PublicArtifact,
    PublicExportIdentity,
    PublicSlideCheckpoint,
    artifact_storage_key,
)
from models.sql.workspace import WorkspaceOperation
from services.artifact_store import DurableArtifactService, LocalDevelopmentArtifactObjectStore
from services.public_export_errors import PublicExportOperationError
from services.public_export_operation import PublicWorkerArtifactResult
from services.svg_export_identity import SvgExportRequest, export_svg_through_identity
from services.workspace_operation_handler_registry import WorkspaceOperationClaim

PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
READBACK = {"sourcePreservation": {"passed": True, "pages": []}}


def _slide(label: str) -> SimpleNamespace:
    return SimpleNamespace(content={"svgReceipt": {"quality": {"passed": True, "label": label}}})


def _request(**overrides) -> SvgExportRequest:
    values = dict(
        export_format="pptx",
        svgs=['<svg id="a"/>', '<svg id="b"/>'],
        notes=["", "note"],
        canvas={"widthPx": 1280, "heightPx": 720},
        slides=[_slide("a"), _slide("b")],
        native_objects=[{"objects": [{"id": "t1", "kind": "table"}]}, None],
    )
    values.update(overrides)
    return SvgExportRequest(**values)


class _Harness:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.owner_id = uuid.uuid4()
        self.presentation_id = uuid.uuid4()
        self.tenant_id = "tenant-a"
        self.worker_calls: list[uuid.UUID] = []

    async def start(self) -> None:
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{self.tmp_path / 'svg-identity.db'}")
        async with self.engine.begin() as connection:
            await connection.run_sync(lambda sync: SQLModel.metadata.create_all(sync, tables=[
                WorkspaceOperation.__table__, PublicOperationAdmission.__table__, PublicArtifact.__table__,
                PublicExportIdentity.__table__, PublicSlideCheckpoint.__table__]))
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self.store = LocalDevelopmentArtifactObjectStore(self.tmp_path / "objects")
        self.artifacts = DurableArtifactService(self.store)

    async def operation(self, fmt: str = "pptx") -> WorkspaceOperationClaim:
        claim = WorkspaceOperationClaim(
            operation_id=uuid.uuid4(), owner_id=self.owner_id, owner_scope=str(self.owner_id),
            conversation_id=uuid.uuid4(), presentation_id=self.presentation_id, kind=f"export.{fmt}",
            request_payload={}, expected_revision=3, attempt_number=1,
            attempt_token=f"attempt-{uuid.uuid4().hex}", fencing_token=1)
        async with self.sessions() as session:
            session.add(WorkspaceOperation(
                id=claim.operation_id, owner_id=self.owner_id, owner_scope=str(self.owner_id),
                conversation_id=claim.conversation_id, presentation_id=self.presentation_id, anchor_position=1,
                kind=claim.kind, state="running", idempotency_key_hash=sha256(claim.attempt_token.encode()).hexdigest(),
                request_hash="1" * 64, request_payload={}, expected_revision=3, attempt_number=1,
                attempt_token=claim.attempt_token, fencing_token=1))
            session.add(PublicOperationAdmission(
                operation_id=claim.operation_id, policy_version="test-policy", owner_id=self.owner_id,
                tenant_id=self.tenant_id, trace_id_hash="a" * 64, operation_kind="export", state="active",
                resource_units=1, policy_digest="b" * 64,
                deadline_at=datetime.now(timezone.utc) + timedelta(hours=1), planned_request_limit=1,
                retry_request_limit=1, estimated_cost_limit_microunits=1, actual_cost_limit_microunits=1))
            await session.commit()
        return claim

    def executor(self, claim: WorkspaceOperationClaim):
        async def execute(slide_refs):
            """Stand in for the svg worker: real registered bytes, counted calls."""
            assert all(ref.kind == "slide-svg" and ref.retention_class == "checkpoint" for ref in slide_refs)
            self.worker_calls.append(claim.operation_id)
            payload = b"svg-native-pptx-bytes"
            digest = sha256(payload).hexdigest()
            reference = ArtifactRef(
                artifact_id=uuid.uuid4(), owner_id=self.owner_id, tenant_id=self.tenant_id,
                kind=f"presentation-{claim.kind.removeprefix('export.')}", sha256=digest,
                size_bytes=len(payload), media_type=PPTX if claim.kind == "export.pptx" else "application/pdf",
                storage_key=artifact_storage_key(owner_id=self.owner_id, tenant_id=self.tenant_id, digest=digest),
                created_by_operation_id=claim.operation_id, retention_class="published")
            await self.store.put_stream(reference, io.BytesIO(payload))
            async with self.sessions() as session:
                registered = await self.artifacts.register_ref(session, reference)
                await session.commit()
            return PublicWorkerArtifactResult(artifact=registered, resource_metrics={
                "role": "export", "public_export_worker_evidence": {"structuralReadback": READBACK}})
        return execute

    async def export(self, claim, request: SvgExportRequest):
        return await export_svg_through_identity(
            claim, tenant_id=self.tenant_id, request=request, session_factory=self.sessions, grant=None,
            execute=self.executor(claim))

    async def publish(self, claim, result) -> None:
        """What ``publish_workspace_export_artifact`` records on success."""
        async with self.sessions() as session:
            await session.execute(update(WorkspaceOperation).where(WorkspaceOperation.id == claim.operation_id)
                                  .values(state="succeeded", result=dict(result.resource_metrics)))
            await session.commit()

    async def identity_rows(self) -> list[PublicExportIdentity]:
        async with self.sessions() as session:
            return list((await session.scalars(select(PublicExportIdentity))).all())


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # SQLite is development-only metadata support for the durable artifact store.
    monkeypatch.setenv("ENVIRONMENT", "local")
    built = _Harness(tmp_path)

    async def start() -> _Harness:
        await built.start()
        for module in (svg_export_identity, public_export_operation):
            monkeypatch.setattr(module, "configured_public_artifact_service", lambda: built.artifacts)
        return built

    yield start
    asyncio.run(built.engine.dispose())


def test_same_revision_and_format_reuses_the_completed_export_without_the_worker(harness, monkeypatch) -> None:
    import services.svg_export_whole_page_verifier as whole_page_verifier

    monkeypatch.setattr(whole_page_verifier, "whole_page_verifier_digest", lambda: "a" * 64)

    async def scenario() -> None:
        export = await harness()
        first_claim = await export.operation()
        first, identity = await export.export(first_claim, _request())
        await export.publish(first_claim, first)
        assert export.worker_calls == [first_claim.operation_id]
        assert first.resource_metrics["public_export_worker_evidence"]["joined"] is False

        second_claim = await export.operation()
        second, second_identity = await export.export(second_claim, _request())
        assert export.worker_calls == [first_claim.operation_id]  # no second worker
        assert second_identity.canonical_digest == identity.canonical_digest
        assert second.artifact.sha256 == first.artifact.sha256
        assert second.artifact.created_by_operation_id == second_claim.operation_id
        evidence = second.resource_metrics["public_export_worker_evidence"]
        assert evidence["reusedCompletedIdentity"] is True
        assert evidence["joined"] is True
        assert evidence["joinedOperationId"] == str(first_claim.operation_id)
        assert evidence["deliveredExportMode"] == "svg-native"
        assert evidence["structuralReadback"] == READBACK  # the producer's readback of these bytes

        rows = await export.identity_rows()
        assert len(rows) == 1
        assert (rows[0].output_mode, rows[0].state, rows[0].export_mode) == ("svg-native", "succeeded", "pptx")
        assert rows[0].result_artifact_id == first.artifact.artifact_id
        async with export.sessions() as session:
            checkpoints = list((await session.scalars(select(PublicSlideCheckpoint))).all())
            kinds = {row.kind for row in (await session.scalars(
                select(PublicArtifact).where(PublicArtifact.retention_class == "checkpoint"))).all()}
        assert {row.stage_version for row in checkpoints} == {"svg-slide/v1"}
        assert len(checkpoints) == 4  # two slides, bound to each operation's fence
        assert kinds == {"slide-svg", "checkpoint"}

        monkeypatch.setattr(whole_page_verifier, "whole_page_verifier_digest", lambda: "b" * 64)
        fresh_claim = await export.operation()
        fresh, fresh_identity = await export.export(fresh_claim, _request())
        await export.publish(fresh_claim, fresh)
        assert export.worker_calls == [first_claim.operation_id, fresh_claim.operation_id]
        assert fresh_identity.canonical_digest != identity.canonical_digest
        assert fresh_identity.evidence.quality_digest != identity.evidence.quality_digest
        assert fresh_identity.evidence.scene_digest == identity.evidence.scene_digest
        assert fresh_identity.evidence.semantic_digest == identity.evidence.semantic_digest
        assert fresh_identity.evidence.exporter_digest == identity.evidence.exporter_digest
        assert fresh.artifact.sha256 == first.artifact.sha256
        assert fresh.resource_metrics["public_export_worker_evidence"]["joined"] is False

        unchanged_claim = await export.operation()
        unchanged, unchanged_identity = await export.export(unchanged_claim, _request())
        assert export.worker_calls == [first_claim.operation_id, fresh_claim.operation_id]
        assert unchanged_identity.canonical_digest == fresh_identity.canonical_digest
        assert unchanged.resource_metrics["public_export_worker_evidence"]["joinedOperationId"] == str(fresh_claim.operation_id)
        rows = await export.identity_rows()
        assert len(rows) == 2
        original = next(row for row in rows if row.canonical_digest == identity.canonical_digest)
        assert original.state == "succeeded"
        assert original.result_artifact_id == first.artifact.artifact_id

    asyncio.run(scenario())


def test_a_changed_native_objects_sidecar_is_a_new_identity_and_runs_the_worker(harness) -> None:
    async def scenario() -> None:
        export = await harness()
        first_claim = await export.operation()
        first, identity = await export.export(first_claim, _request())
        await export.publish(first_claim, first)
        changed_claim = await export.operation()
        _changed, changed = await export.export(changed_claim, _request(
            native_objects=[{"objects": [{"id": "t1", "kind": "chart"}]}, None]))
        assert export.worker_calls == [first_claim.operation_id, changed_claim.operation_id]
        assert changed.evidence.semantic_digest != identity.evidence.semantic_digest
        assert changed.evidence.scene_digest == identity.evidence.scene_digest
        assert len(await export.identity_rows()) == 2

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["svg", "font_embedding", "native_objects", "quality_policy", "slide_quality"])
def test_every_pre_export_input_splits_the_identity(harness, change) -> None:
    async def scenario() -> None:
        export = await harness()
        claim = await export.operation()
        base = _request()
        variant = {
            "svg": _request(svgs=['<svg id="a"/>', '<svg id="c"/>']),
            "font_embedding": _request(font_embedding=True),
            "native_objects": _request(native_objects=[None, None]),
            "quality_policy": _request(quality_policy="release-v1"),
            "slide_quality": _request(slides=[_slide("a"), _slide("changed")]),
        }[change]
        identities = []
        for request in (base, variant):
            checkpoints = await svg_export_identity.persist_svg_slide_checkpoints(
                claim, tenant_id=export.tenant_id, request=request, session_factory=export.sessions)
            identities.append(svg_export_identity.build_svg_export_identity(
                claim, tenant_id=export.tenant_id, request=request, checkpoints=checkpoints))
        assert identities[0].canonical_digest != identities[1].canonical_digest
        assert identities[0].evidence != identities[1].evidence
        assert {identity.export_mode for identity in identities} == {"svg-native"}

    asyncio.run(scenario())


def test_native_objects_do_not_split_a_pdf_identity(harness) -> None:
    async def scenario() -> None:
        export = await harness()
        claim = await export.operation("pdf")
        identities = []
        for native in ([{"objects": []}, None], None):
            request = _request(export_format="pdf", native_objects=native)
            checkpoints = await svg_export_identity.persist_svg_slide_checkpoints(
                claim, tenant_id=export.tenant_id, request=request, session_factory=export.sessions)
            identities.append(svg_export_identity.build_svg_export_identity(
                claim, tenant_id=export.tenant_id, request=request, checkpoints=checkpoints))
        assert identities[0].canonical_digest == identities[1].canonical_digest

    asyncio.run(scenario())


def test_a_reuse_that_needs_readback_waits_for_the_producer_to_publish(harness) -> None:
    async def scenario() -> None:
        export = await harness()
        preserved = _request(source_preservation=[{"svg_digest": "a"}, {"svg_digest": "b"}])
        first_claim = await export.operation()
        first, _identity = await export.export(first_claim, preserved)
        second_claim = await export.operation()
        # Completed identity, producer not yet published: retry, never judge without readback.
        with pytest.raises(PublicExportOperationError, match="public_export_readback_evidence_pending"):
            await export.export(second_claim, preserved)
        await export.publish(first_claim, first)
        reused, _ = await export.export(second_claim, preserved)
        assert export.worker_calls == [first_claim.operation_id]
        assert reused.resource_metrics["public_export_worker_evidence"]["structuralReadback"] == READBACK

    asyncio.run(scenario())
