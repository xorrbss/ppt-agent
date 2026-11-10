from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from models.public_v2_contracts import (
    AuthorizedDownloadGrant,
    CancelOperationReceiptV2,
    OperationEventPageV2,
    OperationEventV2,
    OperationLinksV2,
    OperationReceiptV2,
    OperationStatusV2,
    PresentationExportRequestV2,
)
from api.v2.orchestration import (
    PublicRequestActor,
    PublicV2OrchestrationError,
    get_public_request_actor,
)
from api.v2.router import API_V2_PUBLIC_ROUTER
from models.sql.public_admission import PublicOperationAdmission, PublicPolicyProfile
from models.sql.public_artifact import (
    PublicArtifact,
    PublicArtifactPublication,
    artifact_storage_key,
)
from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from models.sql.user import User
from models.sql.workspace import WorkspaceConversation, WorkspaceOperation, WorkspaceOperationEvent
from models.sql.async_presentation_generation_status import AsyncPresentationGenerationTaskModel
from models.sql.presentation_generation_job import PresentationGenerationJob
from services.public_v2_orchestrator import (
    PUBLIC_V2_POSTGRESQL_STRICT_PROFILE,
    SqlPublicV2Orchestrator,
    create_public_v2_orchestrator,
    require_generation_content_integrity_for_export,
    resolve_public_v2_integration_profile,
)
from services.artifact_store import SignedArtifactDownload
from services.authored_vnext.narrative_ir.content_graph import (
    seal_content_integrity_receipt,
)
from models.authored_vnext.narrative_ir_contracts import model_payload
from services.authored_vnext.narrative_ir.validation import canonical_digest
from services.source_content.errors import SourceContentError


def test_public_v2_requires_explicit_strict_postgresql_profile() -> None:
    assert resolve_public_v2_integration_profile({}) is None
    assert (
        resolve_public_v2_integration_profile(
            {"PUBLIC_V2_INTEGRATION_PROFILE": "postgresql-strict"}
        )
        == PUBLIC_V2_POSTGRESQL_STRICT_PROFILE
    )
    with pytest.raises(RuntimeError, match="public_v2.integration_profile_invalid"):
        resolve_public_v2_integration_profile(
            {"PUBLIC_V2_INTEGRATION_PROFILE": "development"}
        )

    with pytest.raises(RuntimeError, match="public_v2.integration_profile_invalid"):
        create_public_v2_orchestrator(
            object(),  # type: ignore[arg-type]
            integration_profile="development",
        )


def test_v2_api_delegates_to_db_profile_and_never_runs_executor_in_request_thread(
    tmp_path: Path,
) -> None:
    """The HTTP edge is a projection over durable authority, not an executor."""

    async def scenario() -> None:
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'authority.db'}")
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda sync_connection: SQLModel.metadata.create_all(
                    sync_connection, tables=[PublicPolicyProfile.__table__]
                )
            )
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as session, session.begin():
            session.add(
                PublicPolicyProfile(
                    version="public-test/v1",
                    max_slides=30,
                    global_active_generation=4,
                    global_queued_generation=8,
                    owner_active_generation=1,
                    owner_queued_generation=2,
                    tenant_active_generation=2,
                    tenant_queued_generation=4,
                    global_active_export=4,
                    global_queued_export=8,
                    owner_active_export=1,
                    owner_queued_export=2,
                    tenant_active_export=2,
                    tenant_queued_export=4,
                    global_active_resource_units=100,
                    global_queued_resource_units=100,
                    tenant_active_resource_units=50,
                    tenant_queued_resource_units=50,
                    owner_active_resource_units=25,
                    owner_queued_resource_units=25,
                    retry_request_limit=1,
                    estimated_cost_limit_microunits=100,
                    actual_cost_limit_microunits=100,
                    generation_deadline_seconds=300,
                    export_deadline_seconds=300,
                )
            )

        now = datetime(2026, 8, 30, tzinfo=timezone.utc)
        actor = PublicRequestActor(
            uuid.uuid4(),
            "tenant-db",
            "trace-db",
            frozenset(
                {
                    "presentations:write",
                    "operations:read",
                    "operations:cancel",
                    "artifacts:read",
                }
            ),
        )

        class DbAuthority:
            def __init__(self) -> None:
                self.receipts: dict[str, OperationReceiptV2] = {}
                self.payloads: dict[str, str] = {}
                self.calls: list[str] = []
                self.heavy_executor_calls = 0

            async def _policy(self) -> PublicPolicyProfile:
                async with maker() as session:
                    profile = (
                        await session.execute(
                            select(PublicPolicyProfile).where(
                                PublicPolicyProfile.version == "public-test/v1"
                            )
                        )
                    ).scalar_one_or_none()
                if profile is None or not profile.enabled:
                    raise PublicV2OrchestrationError(
                        status_code=503,
                        reason_code="operation_authority_unavailable",
                    )
                return profile

            def _receipt(self, operation_id: str, presentation_id: str) -> OperationReceiptV2:
                base = f"/api/v2/operations/{operation_id}"
                return OperationReceiptV2(
                    operation_id=operation_id,
                    receipt_id=f"rcpt_{operation_id}",
                    presentation_id=presentation_id,
                    status="queued",
                    accepted_at=now,
                    absolute_deadline_at=now + timedelta(seconds=300),
                    links=OperationLinksV2(
                        self=base, events=f"{base}/events", cancel=f"{base}/cancel"
                    ),
                )

            async def submit_generation(self, *, actor, idempotency_key, request):
                profile = await self._policy()
                assert profile.max_slides == 30
                payload = request.model_dump_json()
                if idempotency_key in self.payloads and self.payloads[idempotency_key] != payload:
                    raise PublicV2OrchestrationError(
                        status_code=409, reason_code="idempotency_key_conflict"
                    )
                if idempotency_key in self.receipts:
                    self.calls.append("duplicate")
                    return self.receipts[idempotency_key].model_copy(
                        update={"duplicate": True}
                    )
                self.payloads[idempotency_key] = payload
                receipt = self._receipt("op_db_1", "prs_db_1")
                self.receipts[idempotency_key] = receipt
                self.calls.append("submit")
                return receipt

            async def submit_export(self, *, actor, presentation_id, idempotency_key, request):
                if request.source_revision != 42:
                    raise PublicV2OrchestrationError(
                        status_code=409, reason_code="stale_revision", retry_after_seconds=7
                    )
                self.calls.append("export")
                return self._receipt("op_export_db", presentation_id)

            async def get_operation(self, *, actor, operation_id):
                self.calls.append("status")
                return OperationStatusV2(
                    operation_id=operation_id,
                    presentation_id="prs_db_1",
                    kind=(
                        "presentation.export"
                        if operation_id == "op_export_db"
                        else "presentation.generate"
                    ),
                    status="succeeded",
                    phase="publishing",
                    progress_percent=100,
                    source_revision=42,
                    accepted_at=now,
                    updated_at=now,
                    absolute_deadline_at=now + timedelta(seconds=300),
                )

            async def list_operation_events(self, *, actor, operation_id, after, limit):
                self.calls.append("events")
                return OperationEventPageV2(
                    operation_id=operation_id,
                    events=[
                        OperationEventV2(
                            event_id="evt_db_1", operation_id=operation_id, sequence=1,
                            occurred_at=now, status="queued", phase="admission",
                            progress_percent=0,
                        )
                    ],
                )

            async def cancel_operation(self, *, actor, operation_id):
                self.calls.append("cancel")
                return CancelOperationReceiptV2(
                    operation_id=operation_id, status="running",
                    cancellation_requested=True, requested_at=now,
                )

            async def authorize_download(self, *, actor, artifact_id):
                self.calls.append("download")
                return AuthorizedDownloadGrant(
                    url="https://download.example.test/artifact",
                    issued_at=now, expires_at=now + timedelta(minutes=3),
                )

        authority = DbAuthority()
        app = FastAPI()
        app.include_router(API_V2_PUBLIC_ROUTER)
        app.dependency_overrides[get_public_request_actor] = lambda: actor
        app.state.public_v2_orchestrator = authority

        body = {
            "schemaVersion": "presentation-generation-request/v2",
            "content": "DB-backed public deck",
            "slideCount": 5,
        }
        with TestClient(app, follow_redirects=False) as client:
            first = client.post(
                "/api/v2/presentations/generations",
                headers={"Idempotency-Key": "db-key"}, json=body,
            )
            duplicate = client.post(
                "/api/v2/presentations/generations",
                headers={"Idempotency-Key": "db-key"}, json=body,
            )
            conflict = client.post(
                "/api/v2/presentations/generations",
                headers={"Idempotency-Key": "db-key"},
                json={**body, "content": "different"},
            )
            stale = client.post(
                "/api/v2/presentations/prs_db_1/exports",
                headers={"Idempotency-Key": "export-key"},
                json={
                    "schemaVersion": "presentation-export-request/v2",
                    "sourceRevision": 41,
                    "output": {"format": "pptx", "mode": "fidelity-raster"},
                },
            )
            generation_status = client.get("/api/v2/operations/op_db_1")
            export = client.post(
                "/api/v2/presentations/prs_db_1/exports",
                headers={"Idempotency-Key": "export-key"},
                json={
                    "schemaVersion": "presentation-export-request/v2",
                    "sourceRevision": 42,
                    "output": {"format": "pptx", "mode": "fidelity-raster"},
                },
            )
            export_status = client.get("/api/v2/operations/op_export_db")
            events = client.get("/api/v2/operations/op_db_1/events")
            cancelled = client.post("/api/v2/operations/op_db_1/cancel")
            download = client.get("/api/v2/artifacts/artifact_db/download")

        assert first.status_code == 202
        assert duplicate.status_code == 202 and duplicate.json()["duplicate"] is True
        assert conflict.status_code == 409
        assert conflict.json() == {"detail": {"reasonCode": "idempotency_key_conflict"}}
        assert stale.status_code == 409
        assert stale.headers["retry-after"] == "7"
        assert generation_status.status_code == 200
        assert generation_status.json()["sourceRevision"] == 42
        assert export.status_code == 202
        assert export.json()["operationId"] == "op_export_db"
        assert export_status.status_code == 200
        assert export_status.json()["kind"] == "presentation.export"
        assert export_status.json()["sourceRevision"] == 42
        assert events.status_code == 200 and events.json()["events"][0]["sequence"] == 1
        assert cancelled.status_code == 202 and cancelled.json()["cancellationRequested"] is True
        assert download.status_code == 303 and download.headers["cache-control"] == "no-store"
        assert authority.calls == [
            "submit", "duplicate", "status", "export", "status", "events", "cancel", "download"
        ]
        assert authority.heavy_executor_calls == 0

        await engine.dispose()

    asyncio.run(scenario())


def test_sql_adapter_projects_durable_operation_and_delegates_cancel_without_executor(
    tmp_path: Path,
) -> None:
    """Exercise the production adapter against durable rows; admission is stubbed."""

    async def scenario() -> None:
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'adapter.db'}")
        tables = [
            User.__table__, PresentationModel.__table__, WorkspaceConversation.__table__,
            WorkspaceOperation.__table__, WorkspaceOperationEvent.__table__,
            PublicPolicyProfile.__table__, PublicOperationAdmission.__table__,
            PublicArtifact.__table__,
        ]
        async with engine.begin() as connection:
            await connection.run_sync(lambda sync: SQLModel.metadata.create_all(sync, tables=tables))
        maker = async_sessionmaker(engine, expire_on_commit=False)
        owner = uuid.uuid4()
        presentation_id = uuid.uuid4()
        operation_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        async with maker() as session, session.begin():
            session.add(User(id=owner, username="adapter-owner", hashed_password="test"))
            session.add(PresentationModel(id=presentation_id, owner_id=owner, content="durable", n_slides=1, language="en", mode="authored", theme={}))
            conversation = WorkspaceConversation(id=uuid.uuid4(), owner_id=owner, owner_scope=str(owner), presentation_id=presentation_id)
            session.add(conversation)
            conversation_id = conversation.id
            operation = WorkspaceOperation(
                id=operation_id, owner_id=owner, owner_scope=str(owner), conversation_id=conversation_id,
                presentation_id=presentation_id, anchor_position=1, kind="presentation.generate",
                state="queued", idempotency_key_hash="a" * 64, request_hash="b" * 64,
                request_payload={}, contract_version="public-operation/v2",
                execution_profile_version="public-generation-export/v2", phase="admission",
                deadline_at=now + timedelta(minutes=5), expected_revision=0,
            )
            session.add(operation)
            session.add(PublicOperationAdmission(
                operation_id=operation_id, policy_version="public-test/v1", owner_id=owner,
                tenant_id="tenant-db", trace_id_hash="c" * 64, operation_kind="generation",
                resource_units=1, policy_digest="d" * 64, deadline_at=now + timedelta(minutes=5),
                state="queued", slide_count=1, planned_request_limit=1, retry_request_limit=0,
                estimated_cost_limit_microunits=1, actual_cost_limit_microunits=1,
            ))
            session.add(WorkspaceOperationEvent(
                owner_id=owner, operation_id=operation_id, sequence=1, operation_version=0,
                event_type="queued", payload={"state": "queued", "phase": "admission"},
            ))

        cancel_calls: list[uuid.UUID] = []
        async def cancel(operation_uuid, *, expected_version, session_factory):
            cancel_calls.append(operation_uuid)
            return True

        async def admission(**kwargs):
            raise AssertionError("submission admission must not run for projection test")

        adapter = SqlPublicV2Orchestrator(maker, admission=admission, cancel=cancel)
        actor = PublicRequestActor(
            owner,
            "tenant-db",
            "trace-db",
            frozenset({"operations:read", "operations:cancel"}),
        )
        app = FastAPI()
        app.include_router(API_V2_PUBLIC_ROUTER)
        app.dependency_overrides[get_public_request_actor] = lambda: actor
        app.state.public_v2_orchestrator = adapter
        with TestClient(app, follow_redirects=False) as client:
            status = client.get(f"/api/v2/operations/{operation_id}")
            events = client.get(f"/api/v2/operations/{operation_id}/events")
            resumed_events = client.get(
                f"/api/v2/operations/{operation_id}/events?after=1"
            )
            cancelled = client.post(f"/api/v2/operations/{operation_id}/cancel")
            app.dependency_overrides[get_public_request_actor] = lambda: PublicRequestActor(
                owner,
                "tenant-other",
                "trace-other",
                frozenset({"operations:read"}),
            )
            cross_tenant_status = client.get(f"/api/v2/operations/{operation_id}")
        assert status.status_code == 200 and status.json()["status"] == "queued"
        assert events.status_code == 200 and events.json()["events"][0]["sequence"] == 1
        assert events.json()["nextCursor"] == "1"
        assert resumed_events.status_code == 200
        assert resumed_events.json() == {
            "schemaVersion": "operation-event-page/v2",
            "operationId": str(operation_id),
            "events": [],
            "nextCursor": None,
        }
        assert cancelled.status_code == 202 and cancelled.json()["cancellationRequested"] is True
        assert cross_tenant_status.status_code == 503
        assert cross_tenant_status.json() == {
            "detail": {"reasonCode": "operation_authority_unavailable"}
        }
        assert cancel_calls == [operation_id]
        await engine.dispose()

    asyncio.run(scenario())


def test_api_submit_sql_adapter_persists_operation_job_and_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Submit reaches the production adapter and seals durable rows in one commit."""

    async def scenario() -> None:
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'submit.db'}")
        tables = [
            User.__table__, PresentationModel.__table__, WorkspaceConversation.__table__,
            WorkspaceOperation.__table__, PublicPolicyProfile.__table__,
            PublicOperationAdmission.__table__, AsyncPresentationGenerationTaskModel.__table__,
            PresentationGenerationJob.__table__,
        ]
        async with engine.begin() as connection:
            await connection.run_sync(lambda sync: SQLModel.metadata.create_all(sync, tables=tables))
        maker = async_sessionmaker(engine, expire_on_commit=False)
        owner = uuid.uuid4()
        actor = PublicRequestActor(
            owner,
            "tenant-submit",
            "trace-submit",
            frozenset({"presentations:write"}),
        )
        admission_calls: list[uuid.UUID] = []
        executor_calls: list[str] = []

        async def fake_admission(session, *, principal, operation_id, operation_kind, **kwargs):
            admission_calls.append(operation_id)
            row = PublicOperationAdmission(
                operation_id=operation_id, policy_version="submit-test/v1", owner_id=principal.owner_id,
                tenant_id=principal.tenant_id, trace_id_hash="a" * 64, operation_kind=operation_kind,
                resource_units=1, policy_digest="b" * 64, deadline_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                state="queued", slide_count=5, planned_request_limit=1, retry_request_limit=0,
                estimated_cost_limit_microunits=1, actual_cost_limit_microunits=1,
            )
            session.add(row)
            await session.flush()
            return row

        async def fake_bootstrap(session, *, presentation_id, request, commit, initial_operation_state):
            conversation = WorkspaceConversation(owner_id=owner, owner_scope=str(owner), presentation_id=presentation_id)
            session.add(conversation)
            await session.flush()
            operation = WorkspaceOperation(
                owner_id=owner, owner_scope=str(owner), conversation_id=conversation.id,
                presentation_id=presentation_id, anchor_position=1, kind=request.operation_type,
                state=initial_operation_state, idempotency_key_hash="c" * 64, request_hash="d" * 64,
                request_payload={}, expected_revision=request.expected_revision,
                contract_version="public-operation/v2",
                execution_profile_version="public-generation-export/v2",
                phase="admission", deadline_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            )
            session.add(operation)
            await session.flush()
            return conversation, None, operation, False

        async def no_references(*args, **kwargs):
            return []

        async def no_claim(*args, **kwargs):
            return None

        monkeypatch.setattr("services.public_v2_orchestrator.bootstrap_workspace", fake_bootstrap)
        monkeypatch.setattr("services.public_v2_orchestrator.admit_reference_snapshots", no_references)
        monkeypatch.setattr("services.public_v2_orchestrator.claim_reference_snapshots", no_claim)
        monkeypatch.setattr("services.public_v2_orchestrator.append_operation_event", no_claim)
        monkeypatch.setattr("services.public_v2_orchestrator.wake_workspace_operation_runner", lambda: None)

        adapter = SqlPublicV2Orchestrator(maker, admission=fake_admission)
        app = FastAPI()
        app.include_router(API_V2_PUBLIC_ROUTER)
        app.dependency_overrides[get_public_request_actor] = lambda: actor
        app.state.public_v2_orchestrator = adapter
        with TestClient(app) as client:
            response = client.post(
                "/api/v2/presentations/generations", headers={"Idempotency-Key": "submit-key"},
                json={"schemaVersion": "presentation-generation-request/v2", "content": "persist me", "slideCount": 5},
            )
        assert response.status_code == 202
        operation_id = uuid.UUID(response.json()["operationId"])
        async with maker() as session:
            operation = await session.get(WorkspaceOperation, operation_id)
            admission = await session.scalar(select(PublicOperationAdmission).where(PublicOperationAdmission.operation_id == operation_id))
            job = await session.scalar(select(PresentationGenerationJob).where(PresentationGenerationJob.workspace_operation_id == operation_id))
        assert operation is not None and operation.state == "queued"
        assert operation.contract_version == "public-operation/v2"
        assert operation.deadline_at is not None and operation.fencing_token == 0
        assert admission is not None and admission.state == "queued" and admission.fence == 0
        assert job is not None and job.state == "queued"
        assert admission_calls == [operation_id]
        assert executor_calls == []

        async def _row_counts(session_factory):
            async with session_factory() as verification:
                return (
                    len((await verification.scalars(select(PresentationModel))).all()),
                    len((await verification.scalars(select(WorkspaceOperation))).all()),
                    len((await verification.scalars(select(PublicOperationAdmission))).all()),
                )

        # A missing public policy must fail closed without provisional rows.
        before_rows = await _row_counts(maker)
        app.state.public_v2_orchestrator = SqlPublicV2Orchestrator(maker)
        with TestClient(app) as client:
            unavailable = client.post(
                "/api/v2/presentations/generations", headers={"Idempotency-Key": "sqlite-key"},
                json={"schemaVersion": "presentation-generation-request/v2", "content": "must rollback", "slideCount": 5},
            )
        after_rows = await _row_counts(maker)
        assert unavailable.status_code == 409, unavailable.json()
        assert unavailable.json() == {"detail": {"reasonCode": "public_policy_unavailable"}}
        assert after_rows == before_rows
        await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("authority", ["public-v2", "workspace-legacy"])
def test_sql_adapter_requires_current_publication_and_rejects_unwired_export_capabilities(
    tmp_path: Path, authority: str,
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'publication.db'}")
        tables = [
            User.__table__,
            PresentationModel.__table__,
            WorkspaceConversation.__table__,
            WorkspaceOperation.__table__,
            PublicPolicyProfile.__table__,
            PublicOperationAdmission.__table__,
            PublicArtifact.__table__,
            PublicArtifactPublication.__table__,
        ]
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda sync: SQLModel.metadata.create_all(sync, tables=tables)
            )
        maker = async_sessionmaker(engine, expire_on_commit=False)
        owner = uuid.uuid4()
        presentation_id = uuid.uuid4()
        operation_id = uuid.uuid4()
        current_artifact_id = uuid.uuid4()
        stale_artifact_id = uuid.uuid4()
        actor = PublicRequestActor(owner, "tenant-publication", "trace-publication")
        deadline = datetime.now(timezone.utc) + timedelta(minutes=5)

        class ArtifactService:
            async def create_signed_download(self, reference, *, expires_seconds):
                assert reference.artifact_id == current_artifact_id
                assert expires_seconds == 180
                return SignedArtifactDownload(
                    url="https://download.example.test/current",
                    expires_at=datetime.now(timezone.utc) + timedelta(seconds=180),
                    etag='"current"',
                )

        adapter = SqlPublicV2Orchestrator(
            maker,
            artifact_service_factory=ArtifactService,
        )
        unsupported = [
            PresentationExportRequestV2.model_validate(
                {
                    "sourceRevision": 3,
                    "output": {
                        "format": "pptx",
                        "mode": "fidelity-raster",
                        "requiredCapabilities": ["native-text"],
                    },
                }
            ),
        ]
        for request in unsupported:
            with pytest.raises(PublicV2OrchestrationError) as rejected:
                await adapter.submit_export(
                    actor=actor,
                    presentation_id=str(presentation_id),
                    idempotency_key="unsupported",
                    request=request,
                )
            assert rejected.value.status_code == 422
            assert rejected.value.reason_code == "capability_not_supported"

        # Isolated hybrid export must not fail as an unwired capability.
        hybrid_request = PresentationExportRequestV2.model_validate(
            {
                "sourceRevision": 3,
                "output": {
                    "format": "pptx",
                    "mode": "editable-hybrid",
                    "fallbackPolicy": "whole-deck-fidelity",
                    "fontEmbedding": "subset",
                },
            }
        )
        try:
            await adapter.submit_export(
                actor=actor,
                presentation_id=str(presentation_id),
                idempotency_key="hybrid-admitted",
                request=hybrid_request,
            )
        except PublicV2OrchestrationError as error:
            assert error.reason_code != "capability_not_supported"

        async with maker() as session, session.begin():
            session.add(User(id=owner, username="publication-owner", hashed_password="test"))
            session.add(
                PublicPolicyProfile(
                    version="publication-test/v1",
                    max_slides=30,
                    global_active_generation=4,
                    global_queued_generation=8,
                    owner_active_generation=1,
                    owner_queued_generation=2,
                    tenant_active_generation=2,
                    tenant_queued_generation=4,
                    global_active_export=4,
                    global_queued_export=8,
                    owner_active_export=1,
                    owner_queued_export=2,
                    tenant_active_export=2,
                    tenant_queued_export=4,
                    global_active_resource_units=100,
                    global_queued_resource_units=100,
                    tenant_active_resource_units=50,
                    tenant_queued_resource_units=50,
                    owner_active_resource_units=25,
                    owner_queued_resource_units=25,
                    retry_request_limit=1,
                    estimated_cost_limit_microunits=100,
                    actual_cost_limit_microunits=100,
                    generation_deadline_seconds=300,
                    export_deadline_seconds=300,
                )
            )
            session.add(
                PresentationModel(
                    id=presentation_id,
                    owner_id=owner,
                    content="published",
                    n_slides=1,
                    language="en",
                    mode="authored",
                    theme={},
                    revision=3,
                )
            )
            conversation = WorkspaceConversation(
                owner_id=owner,
                owner_scope=str(owner),
                presentation_id=presentation_id,
            )
            session.add(conversation)
            await session.flush()
            session.add(
                WorkspaceOperation(
                    id=operation_id,
                    owner_id=owner,
                    owner_scope=str(owner),
                    conversation_id=conversation.id,
                    presentation_id=presentation_id,
                    anchor_position=1,
                    kind="export.pptx",
                    state="succeeded",
                    idempotency_key_hash="a" * 64,
                    request_hash="b" * 64,
                    request_payload={"principal_tenant_id": "tenant-publication"},
                    expected_revision=3,
                    result_revision=3,
                    contract_version="public-operation/v2" if authority == "public-v2" else None,
                    execution_profile_version="public-generation-export/v2" if authority == "public-v2" else None,
                    phase="publishing" if authority == "public-v2" else None,
                    deadline_at=deadline if authority == "public-v2" else None,
                    fencing_token=2,
                )
            )
            session.add(
                PublicOperationAdmission(
                    operation_id=operation_id,
                    policy_version="publication-test/v1",
                    owner_id=owner,
                    tenant_id="tenant-publication",
                    trace_id_hash="c" * 64,
                    operation_kind="export",
                    resource_units=1,
                    policy_digest="d" * 64,
                    deadline_at=deadline,
                    fence=2 if authority == "public-v2" else 0,
                    state="succeeded",
                    slide_count=1,
                    planned_request_limit=0,
                    retry_request_limit=0,
                    estimated_cost_limit_microunits=1,
                    actual_cost_limit_microunits=1,
                )
            )
            for artifact_id, digest in (
                (current_artifact_id, "1" * 64),
                (stale_artifact_id, "2" * 64),
            ):
                session.add(
                    PublicArtifact(
                        id=artifact_id,
                        owner_id=owner,
                        tenant_id="tenant-publication",
                        kind="presentation-pptx",
                        sha256=digest,
                        size_bytes=12,
                        media_type=(
                            "application/vnd.openxmlformats-officedocument."
                            "presentationml.presentation"
                        ),
                        storage_key=artifact_storage_key(
                            owner_id=owner,
                            tenant_id="tenant-publication",
                            digest=digest,
                        ),
                        created_by_operation_id=operation_id,
                        retention_class="published",
                        lifecycle_state="committed",
                    )
                )
            session.add(
                PublicArtifactPublication(
                    operation_id=operation_id,
                    publication_name="presentation.current",
                    revision=3,
                    artifact_id=current_artifact_id,
                    quality_digest="3" * 64,
                    attempt_token_hash="sha256:" + "e" * 64,
                    fencing_token=2,
                )
            )
            session.add(
                PublicArtifactPublication(
                    operation_id=operation_id,
                    publication_name="presentation.stale",
                    revision=3,
                    artifact_id=stale_artifact_id,
                    quality_digest="4" * 64,
                    attempt_token_hash="sha256:" + "f" * 64,
                    fencing_token=1,
                )
            )

        if authority == "public-v2":
            status = await adapter.get_operation(actor=actor, operation_id=str(operation_id))
            assert [item.artifact_id for item in status.artifacts] == [str(current_artifact_id)]
            assert status.source_revision == 3
        else:
            with pytest.raises(PublicV2OrchestrationError) as legacy_status:
                await adapter.get_operation(actor=actor, operation_id=str(operation_id))
            assert legacy_status.value.reason_code == "operation_authority_unavailable"
        grant = await adapter.authorize_download(
            actor=actor,
            artifact_id=str(current_artifact_id),
        )
        assert grant.url == "https://download.example.test/current"
        with pytest.raises(PublicV2OrchestrationError) as stale:
            await adapter.authorize_download(
                actor=actor,
                artifact_id=str(stale_artifact_id),
            )
        assert stale.value.status_code == 404
        assert stale.value.reason_code == "artifact_not_found"
        from dataclasses import replace
        for other_actor in (replace(actor, owner_id=uuid.uuid4()), replace(actor, tenant_id="other-tenant")):
            with pytest.raises(PublicV2OrchestrationError) as unauthorized:
                await adapter.authorize_download(actor=other_actor, artifact_id=str(current_artifact_id))
            assert unauthorized.value.status_code == 404
        if authority == "workspace-legacy":
            # A legacy download must never become a bypass for malformed v2,
            # conflicting tenant capture, or nonterminal admission authority.
            for mutation in ({"contract_version": "public-operation/v2",
                              "execution_profile_version": "public-generation-export/v2",
                              "phase": "publishing", "deadline_at": deadline},
                             {"request_payload": {"principal_tenant_id": "other-tenant"}},
                             {"state": "running"}):
                async with maker() as session:
                    row = await session.get(WorkspaceOperation, operation_id)
                    original = {field: getattr(row, field) for field in mutation}
                    for field, value in mutation.items():
                        setattr(row, field, value)
                    await session.commit()
                with pytest.raises(PublicV2OrchestrationError) as invalid:
                    await adapter.authorize_download(actor=actor, artifact_id=str(current_artifact_id))
                assert invalid.value.reason_code == "operation_authority_unavailable"
                async with maker() as session:
                    row = await session.get(WorkspaceOperation, operation_id)
                    for field, value in original.items():
                        setattr(row, field, value)
                    await session.commit()
            async with maker() as session:
                admitted = await session.scalar(select(PublicOperationAdmission).where(
                    PublicOperationAdmission.operation_id == operation_id))
                admitted.state = "failed"
                await session.commit()
            with pytest.raises(PublicV2OrchestrationError) as invalid:
                await adapter.authorize_download(actor=actor, artifact_id=str(current_artifact_id))
            assert invalid.value.reason_code == "operation_authority_unavailable"
        await engine.dispose()

    asyncio.run(scenario())


def test_public_export_requires_generation_authority_in_the_actor_tenant(
    tmp_path: Path, monkeypatch,
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{tmp_path / 'cross-tenant-export.db'}"
        )
        tables = [
            User.__table__,
            PresentationModel.__table__,
            WorkspaceConversation.__table__,
            WorkspaceOperation.__table__,
            PublicOperationAdmission.__table__,
            SlideModel.__table__,
        ]
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda sync: SQLModel.metadata.create_all(sync, tables=tables)
            )
        maker = async_sessionmaker(engine, expire_on_commit=False)
        owner = uuid.uuid4()
        presentation_id = uuid.uuid4()
        generation_id = uuid.uuid4()
        deadline = datetime.now(timezone.utc) + timedelta(minutes=5)
        async with maker() as session, session.begin():
            session.add(User(id=owner, username="tenant-owner", hashed_password="test"))
            session.add(
                PresentationModel(
                    id=presentation_id,
                    owner_id=owner,
                    content="tenant-a presentation",
                    n_slides=1,
                    language="en",
                    mode="authored",
                    theme={},
                )
            )
            conversation = WorkspaceConversation(
                owner_id=owner,
                owner_scope=str(owner),
                presentation_id=presentation_id,
            )
            session.add(conversation)
            await session.flush()
            session.add(
                WorkspaceOperation(
                    id=generation_id,
                    owner_id=owner,
                    owner_scope=str(owner),
                    conversation_id=conversation.id,
                    presentation_id=presentation_id,
                    anchor_position=1,
                    kind="presentation.generate",
                    state="succeeded",
                    idempotency_key_hash="a" * 64,
                    request_hash="b" * 64,
                    request_payload={},
                    expected_revision=0,
                    result_revision=0,
                    contract_version="public-operation/v2",
                    execution_profile_version="public-generation-export/v2",
                    phase="completed",
                    deadline_at=deadline,
                )
            )
            session.add(
                PublicOperationAdmission(
                    operation_id=generation_id,
                    policy_version="tenant-test/v1",
                    owner_id=owner,
                    tenant_id="tenant-a",
                    trace_id_hash="c" * 64,
                    operation_kind="generation",
                    resource_units=1,
                    policy_digest="d" * 64,
                    deadline_at=deadline,
                    state="succeeded",
                    slide_count=1,
                    planned_request_limit=1,
                    retry_request_limit=0,
                    estimated_cost_limit_microunits=1,
                    actual_cost_limit_microunits=1,
                )
            )

        adapter = SqlPublicV2Orchestrator(maker)
        request = PresentationExportRequestV2.model_validate(
            {
                "sourceRevision": 0,
                "output": {"format": "pptx", "mode": "fidelity-raster"},
            }
        )
        with pytest.raises(PublicV2OrchestrationError) as rejected:
            await adapter.submit_export(
                actor=PublicRequestActor(owner, "tenant-b", "trace-b"),
                presentation_id=str(presentation_id),
                idempotency_key="tenant-b-export",
                request=request,
            )
        assert rejected.value.status_code == 404
        assert rejected.value.reason_code == "presentation_not_found"
        from services.svg_contract import source_digest
        from services.svg_export_operation import svg_export_admission_snapshot
        from services.workspace_operation_principal import PRINCIPAL_TENANT_PAYLOAD_KEY
        from unittest.mock import AsyncMock
        import services.public_v2_orchestrator as orchestrator
        svg='<svg xmlns="http://www.w3.org/2000/svg"><text>owned</text></svg>'
        publisher_id = uuid.uuid4()
        async with maker() as session,session.begin():
            presentation=await session.get(PresentationModel,presentation_id)
            presentation.mode = "svg"
            presentation.revision = 2
            generation=await session.get(WorkspaceOperation,generation_id)
            generation.result_revision=1
            session.add(SlideModel(presentation=presentation_id,owner_id=owner,index=0,
                layout_group='svg',layout='svg:content',source_format='svg',svg_content=svg,
                content={'source':'owned','nativeObjects':{'objects':[]},'svgReceipt':{'quality':{
                    'passed':True,'verified':True,'source_digest':source_digest(svg)}}}))
            session.add(WorkspaceOperation(id=publisher_id,owner_id=owner,owner_scope=str(owner),
                conversation_id=generation.conversation_id,presentation_id=presentation_id,
                anchor_position=2,kind='slide.regenerate',state='succeeded',expected_revision=1,
                result_revision=2,idempotency_key_hash='e'*64,request_hash='f'*64,
                request_payload={PRINCIPAL_TENANT_PAYLOAD_KEY:'tenant-a'}))
        request=request.model_copy(update={'source_revision':2})
        monkeypatch.setattr(orchestrator,'reserve_conversation_positions',AsyncMock(
            side_effect=[SimpleNamespace(start=index) for index in range(3, 20)]))
        monkeypatch.setattr(orchestrator,'append_operation_event',AsyncMock())
        monkeypatch.setattr(adapter,'_admit',AsyncMock(return_value=SimpleNamespace(deadline_at=deadline)))
        for tenant,foreign_owner in [('tenant-b',owner),('tenant-a',uuid.uuid4())]:
            with pytest.raises(PublicV2OrchestrationError) as denied:
                await adapter.submit_export(actor=PublicRequestActor(foreign_owner,tenant,'trace'),
                    presentation_id=str(presentation_id),idempotency_key=f'deny-{tenant}-{foreign_owner}',request=request)
            assert denied.value.reason_code=='presentation_not_found'
        with pytest.raises(PublicV2OrchestrationError) as stale:
            await adapter.submit_export(actor=PublicRequestActor(owner,'tenant-a','trace'),
                presentation_id=str(presentation_id),idempotency_key='stale',request=request.model_copy(update={'source_revision':1}))
        assert stale.value.reason_code=='stale_revision'
        async def change_publisher(**changes):
            async with maker() as session, session.begin():
                publisher = await session.get(WorkspaceOperation, publisher_id)
                for key, value in changes.items():
                    setattr(publisher, key, value)

        # Real mutation publishers share the same owner/tenant/CAS lineage proof.
        for kind in ('slide.regenerate', 'presentation.slides.reorder', 'version.restore',
                     'presentation.edit', 'slide.edit', 'slide.delete'):
            await change_publisher(kind=kind)
            receipt = await adapter.submit_export(
                actor=PublicRequestActor(owner, 'tenant-a', 'trace'),
                presentation_id=str(presentation_id), idempotency_key='publisher-' + kind, request=request,
            )
            async with maker() as session:
                export = await session.get(WorkspaceOperation, uuid.UUID(receipt.operation_id))
                assert export.request_payload['svg_source_revision_binding']['publicationOperationIds'] == [str(publisher_id)]

        # Header revisions require the source-authority verifier's result, not
        # merely a design.apply target string. Keep the handoff bound to this
        # exact current publication and pass it to both export gates.
        import services.svg_export_operation as svg_export
        header_payload = {PRINCIPAL_TENANT_PAYLOAD_KEY: 'tenant-a',
                          'context': {'target': 'common_header'}}
        await change_publisher(kind='design.apply', request_payload=header_payload)
        verified_authority = object()
        authority = AsyncMock(return_value=None)
        parent_gate = AsyncMock()
        original_snapshot = svg_export.svg_export_admission_snapshot

        def header_snapshot(presentation, slides, *, header_revision_authority=None, require_passed=False):
            assert header_revision_authority is verified_authority
            assert require_passed is (request.quality_policy == 'release-v1')
            return original_snapshot(presentation, slides)

        with monkeypatch.context() as header_patch:
            header_patch.setattr(svg_export, 'require_header_revision_lineage_authority', authority)
            header_patch.setattr(svg_export, 'require_quality_recovery_export_parent', parent_gate)
            header_patch.setattr(svg_export, 'svg_export_admission_snapshot', header_snapshot)
            with pytest.raises(PublicV2OrchestrationError) as unproven:
                await adapter.submit_export(actor=PublicRequestActor(owner, 'tenant-a', 'trace'),
                    presentation_id=str(presentation_id), idempotency_key='header-unproven', request=request)
            assert unproven.value.reason_code == 'svg_header_revision_export_authority_required'
            parent_gate.assert_not_awaited()
            await change_publisher(request_payload={**header_payload, 'context': {'target': 'other'}})
            with pytest.raises(PublicV2OrchestrationError) as other_design:
                await adapter.submit_export(actor=PublicRequestActor(owner, 'tenant-a', 'trace'),
                    presentation_id=str(presentation_id), idempotency_key='header-other-design', request=request)
            assert other_design.value.reason_code == 'export_source_revision_lineage_invalid'
            assert authority.await_count == 1
            await change_publisher(request_payload=header_payload)
            authority.return_value = verified_authority
            receipt = await adapter.submit_export(actor=PublicRequestActor(owner, 'tenant-a', 'trace'),
                presentation_id=str(presentation_id), idempotency_key='header-proven-current', request=request)
            assert [item.id for item in authority.await_args.kwargs['publication_operations']] == [publisher_id]
            assert parent_gate.await_args.kwargs == {'header_revision_authority': verified_authority,
                                                     'require_passed': request.quality_policy == 'release-v1'}
            async with maker() as session:
                export = await session.get(WorkspaceOperation, uuid.UUID(receipt.operation_id))
                assert export.request_payload['svg_source_revision_binding']['publicationOperationIds'] == [str(publisher_id)]
            # A second closed header revision keeps the first sealed publisher
            # in the lineage instead of comparing its old scene to revision 3.
            second_publisher_id = uuid.uuid4()
            async with maker() as session, session.begin():
                presentation = await session.get(PresentationModel, presentation_id)
                presentation.revision = 3
                previous = await session.get(WorkspaceOperation, publisher_id)
                session.add(WorkspaceOperation(id=second_publisher_id, owner_id=owner, owner_scope=str(owner),
                    conversation_id=previous.conversation_id, presentation_id=presentation_id,
                    anchor_position=99, kind='design.apply', state='succeeded', expected_revision=2,
                    result_revision=3, idempotency_key_hash='7'*64, request_hash='8'*64,
                    request_payload=header_payload))
            repeated = await adapter.submit_export(actor=PublicRequestActor(owner, 'tenant-a', 'trace'),
                presentation_id=str(presentation_id), idempotency_key='header-proven-repeat',
                request=request.model_copy(update={'source_revision': 3}))
            assert [item.id for item in authority.await_args.kwargs['publication_operations']] == [publisher_id, second_publisher_id]
            async with maker() as session, session.begin():
                export = await session.get(WorkspaceOperation, uuid.UUID(repeated.operation_id))
                assert export.request_payload['svg_source_revision_binding']['publicationOperationIds'] == [str(publisher_id), str(second_publisher_id)]
                presentation = await session.get(PresentationModel, presentation_id)
                presentation.revision = 2
                second = await session.get(WorkspaceOperation, second_publisher_id)
                second.state = 'failed'
        await change_publisher(request_payload={PRINCIPAL_TENANT_PAYLOAD_KEY: 'tenant-a'})

        # Advisory/no-op results cannot stand in for a missing source publication.
        for kind in ('quality.review', 'slide.regenerate.preview', 'slide.regenerate.batch.preview',
                     'export.pptx', 'export.pdf', 'raster.analysis', 'presentation.delete'):
            await change_publisher(kind=kind)
            with pytest.raises(PublicV2OrchestrationError) as denied:
                await adapter.submit_export(
                    actor=PublicRequestActor(owner, 'tenant-a', 'trace'),
                    presentation_id=str(presentation_id), idempotency_key='nonpublisher-' + kind, request=request,
                )
            assert denied.value.reason_code == 'export_source_revision_lineage_required'

        await change_publisher(kind='slide.regenerate')
        for label, changes, reason in (
            ('failed', {'state': 'failed'}, 'export_source_revision_lineage_required'),
            ('foreign-owner', {'owner_id': uuid.uuid4()}, 'export_source_revision_lineage_required'),
            ('foreign-tenant', {'request_payload': {PRINCIPAL_TENANT_PAYLOAD_KEY: 'tenant-b'}}, 'export_source_revision_lineage_invalid'),
            ('missing-tenant', {'request_payload': {}}, 'export_source_revision_lineage_invalid'),
            ('gap', {'expected_revision': 0}, 'export_source_revision_lineage_invalid'),
            ('no-op', {'expected_revision': 2}, 'export_source_revision_lineage_required'),
        ):
            await change_publisher(**changes)
            with pytest.raises(PublicV2OrchestrationError) as denied:
                await adapter.submit_export(
                    actor=PublicRequestActor(owner, 'tenant-a', 'trace'), presentation_id=str(presentation_id),
                    idempotency_key='lineage-' + label, request=request,
                )
            assert denied.value.reason_code == reason
            await change_publisher(state='succeeded', owner_id=owner, expected_revision=1,
                                   request_payload={PRINCIPAL_TENANT_PAYLOAD_KEY: 'tenant-a'})

        async with maker() as session, session.begin():
            slide = await session.scalar(select(SlideModel).where(SlideModel.presentation == presentation_id))
            clean_content = dict(slide.content)
            slide.content = {**clean_content, 'svgReceipt': {'quality': {'passed': True, 'verified': True,
                                                                       'source_digest': 'sha256:' + '0' * 64}}}
        with pytest.raises(PublicV2OrchestrationError) as invalid_source:
            await adapter.submit_export(
                actor=PublicRequestActor(owner, 'tenant-a', 'trace'), presentation_id=str(presentation_id),
                idempotency_key='lineage-stale-source-measurement', request=request,
            )
        assert invalid_source.value.reason_code == 'svg_export_published_source_required'
        async with maker() as session, session.begin():
            slide = await session.scalar(select(SlideModel).where(SlideModel.presentation == presentation_id))
            slide.content = clean_content
        admitted=await adapter.submit_export(actor=PublicRequestActor(owner,'tenant-a','trace'),
            presentation_id=str(presentation_id),idempotency_key='owned-revision2',request=request)
        async with maker() as session:
            operation=await session.get(WorkspaceOperation,uuid.UUID(admitted.operation_id))
            presentation=await session.get(PresentationModel,presentation_id)
            slides=(await session.execute(select(SlideModel).where(SlideModel.presentation==presentation_id))).scalars().all()
            binding=operation.request_payload['svg_source_revision_binding']
            assert binding['sourceRevision']==2 and binding['generationRevision']==1
            assert binding['snapshotSha256']==svg_export_admission_snapshot(presentation,slides)
            slides[0].content={**slides[0].content,'nativeObjects':{'objects':[{'id':'changed'}]}}
            assert binding['snapshotSha256']!=svg_export_admission_snapshot(presentation,slides)
        await engine.dispose()

    asyncio.run(scenario())


def test_registered_template_export_requires_passed_content_integrity() -> None:
    operation_id = uuid.uuid4()
    digest = "sha256:" + "a" * 64
    route_unsigned = {
        "schemaVersion": "generation-route-receipt/v1",
        "requestedMode": "registered-template",
        "selectedRoute": "source-native",
        "designPackPin": {
            "designPackId": "test-pack",
            "version": 1,
            "digest": "sha256:" + "d" * 64,
        },
        "narrativeIrDigest": None,
        "fallbackReason": None,
        "outputCapability": "fidelity",
    }
    route_digest = "sha256:" + sha256(
        json.dumps(
            route_unsigned,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    receipt = seal_content_integrity_receipt(
        operation_id=operation_id,
        attempt_binding_digest=canonical_digest({"operationId": str(operation_id), "jobId": "", "attemptId": "", "fencingToken": None}),
        source_revision=1,
        result_revision=2,
        generation_lane="new-authoring",
        content_graph_digest=digest,
        route_strategy_receipt_digest=route_digest,
        narrative_digest="sha256:" + "b" * 64,
        preserve_source_input_digest=None,
        provider_call_budget={"actualLogicalCalls": 0, "actualPhysicalCalls": 0},
        pages=[
            {
                "pageIndex": 0,
                "expectedContentUnitIds": ["unit-1"],
                "boundContentUnitIds": ["unit-1"],
                "renderedContentUnitIds": ["unit-1"],
                "expectedDigest": digest,
                "boundDigest": digest,
                "renderedDigest": digest,
                "sourceOwnershipDigest": "sha256:" + "c" * 64,
                "expectedProtectedChromeDigest": digest,
                "observedProtectedChromeDigest": digest,
                "browserReadbackDigest": digest,
            }
        ],
    )
    route_payload = {
        "generation_route_receipt": {
            **route_unsigned,
            "receiptDigest": route_digest,
        },
        "narrative_checkpoint": {
            "operationId": str(operation_id),
            "narrative": {"contentGraphDigest": digest, "narrativeDigest": "sha256:" + "b" * 64},
            "providerCallBudget": model_payload(receipt.provider_call_budget),
        },
    }
    missing = SimpleNamespace(
        id=operation_id,
        request_payload=route_payload,
        state="succeeded",
        expected_revision=1,
        result_revision=2,
        result={},
    )
    with pytest.raises(PublicV2OrchestrationError) as rejected:
        require_generation_content_integrity_for_export(
            missing,
            source_revision=2,
        )
    assert rejected.value.reason_code == "content_integrity_receipt_required"

    passed = SimpleNamespace(
        id=operation_id,
        request_payload=route_payload,
        state="succeeded",
        expected_revision=1,
        result_revision=2,
        result={"contentIntegrityReceipt": model_payload(receipt)},
    )
    require_generation_content_integrity_for_export(passed, source_revision=2)


_SOURCE_RESTYLE_SUBMISSION = {
    "schemaVersion": "presentation-generation-request/v2",
    "strategy": "authored",
    "content": "restyle the uploaded deck",
    "slideCount": 5,
    "authoredStyle": "default",
    "masterPin": {
        "masterId": "master:company",
        "revision": 3,
        "digest": "sha256:" + ("a" * 64),
    },
    "sourceMode": "source_content_restyle",
    "sourceContentManifestPin": {
        "manifestId": "manifest:source-content",
        "digest": "sha256:" + ("b" * 64),
    },
    "contentPolicy": {"text": "outline", "allowSummarization": True},
}


async def _submit_source_restyle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    admit_manifest,
    submission: dict[str, object] | None = None,
) -> tuple[object, object, dict[str, object], list[object], list[int]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'restyle.db'}")
    tables = [
        User.__table__, PresentationModel.__table__, WorkspaceConversation.__table__,
        WorkspaceOperation.__table__, PublicPolicyProfile.__table__,
        PublicOperationAdmission.__table__, AsyncPresentationGenerationTaskModel.__table__,
        PresentationGenerationJob.__table__,
    ]
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: SQLModel.metadata.create_all(sync, tables=tables))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    owner = uuid.uuid4()
    actor = PublicRequestActor(
        owner, "tenant-restyle", "trace-restyle", frozenset({"presentations:write"}),
    )
    request_plans: list[object] = []
    captured_generation_settings: dict[str, object] = {}
    route_slide_counts: list[int] = []

    def fake_route_resolver(**kwargs):
        from services.public_generation_route import resolve_authored_generation_route

        route_slide_counts.append(kwargs["slide_count"])
        return resolve_authored_generation_route(**kwargs)

    async def fake_admission(session, *, principal, operation_id, operation_kind, **kwargs):
        request_plans.append(kwargs["request_plan"])
        row = PublicOperationAdmission(
            operation_id=operation_id, policy_version="restyle-test/v1", owner_id=principal.owner_id,
            tenant_id=principal.tenant_id, trace_id_hash="a" * 64, operation_kind=operation_kind,
            resource_units=1, policy_digest="b" * 64, deadline_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            state="queued", slide_count=kwargs["request_plan"].slide_count, planned_request_limit=1,
            retry_request_limit=0, estimated_cost_limit_microunits=1, actual_cost_limit_microunits=1,
        )
        session.add(row)
        await session.flush()
        return row

    async def fake_bootstrap(session, *, presentation_id, request, commit, initial_operation_state):
        captured_generation_settings.update(request.generation_settings)
        conversation = WorkspaceConversation(owner_id=owner, owner_scope=str(owner), presentation_id=presentation_id)
        session.add(conversation)
        await session.flush()
        operation = WorkspaceOperation(
            owner_id=owner, owner_scope=str(owner), conversation_id=conversation.id,
            presentation_id=presentation_id, anchor_position=1, kind=request.operation_type,
            state=initial_operation_state, idempotency_key_hash="c" * 64, request_hash="d" * 64,
            request_payload={}, expected_revision=request.expected_revision,
            contract_version="public-operation/v2",
            execution_profile_version="public-generation-export/v2",
            phase="admission", deadline_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        )
        session.add(operation)
        await session.flush()
        return conversation, None, operation, False

    async def nothing(*args, **kwargs):
        return None

    async def no_references(*args, **kwargs):
        return []

    async def admit_with_owner(candidate, *, sql_session, owner_id, tenant_id):
        assert owner_id == owner
        assert tenant_id == actor.tenant_id
        assert candidate.source_content_manifest_pin.manifest_id == "manifest:source-content"
        assert candidate.reference_snapshot_ids == []
        return await admit_manifest(candidate)

    monkeypatch.setattr("services.public_v2_orchestrator.bootstrap_workspace", fake_bootstrap)
    monkeypatch.setattr("services.public_v2_orchestrator.admit_reference_snapshots", no_references)
    monkeypatch.setattr("services.public_v2_orchestrator.claim_reference_snapshots", nothing)
    monkeypatch.setattr("services.public_v2_orchestrator.append_operation_event", nothing)
    monkeypatch.setattr("services.public_v2_orchestrator.wake_workspace_operation_runner", lambda: None)
    monkeypatch.setattr("services.presentation_generation_pipeline._validate_authored_master_pin_admission", nothing)
    monkeypatch.setattr(
        "services.public_generation_slide_count.admit_source_content_request",
        admit_with_owner,
    )

    app = FastAPI()
    app.include_router(API_V2_PUBLIC_ROUTER)
    app.dependency_overrides[get_public_request_actor] = lambda: actor
    app.state.public_v2_orchestrator = SqlPublicV2Orchestrator(
        maker,
        admission=fake_admission,
        generation_route_resolver=fake_route_resolver,
    )
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v2/presentations/generations",
                headers={"Idempotency-Key": "restyle-key"},
                json=submission or _SOURCE_RESTYLE_SUBMISSION,
            )
        return response, maker, captured_generation_settings, request_plans, route_slide_counts
    finally:
        await engine.dispose()


@pytest.mark.parametrize("verbatim", [False, True])
def test_api_submit_authored_source_restyle_is_rejected_as_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, verbatim: bool,
) -> None:
    """An authored (reflow) restyle was removed on 2026-11-09: a 422 before any durable row."""

    async def scenario() -> None:
        async def admit_manifest(candidate):
            candidate._source_document_outline = SimpleNamespace(source_slide_count=43)

        submission = dict(_SOURCE_RESTYLE_SUBMISSION)
        if verbatim:
            submission.update(
                contentPolicy={"text": "verbatim", "allowSummarization": False},
            )
        response, maker, generation_settings, request_plans, route_counts = (
            await _submit_source_restyle(
                tmp_path, monkeypatch, admit_manifest=admit_manifest, submission=submission,
            )
        )
        assert response.status_code == 422, response.json()
        assert response.json() == {"detail": {"reasonCode": "authored_strategy_removed"}}
        assert route_counts == []
        assert request_plans == []
        assert generation_settings == {}
        async with maker() as session:
            assert (await session.scalars(select(WorkspaceOperation))).all() == []
            assert (await session.scalars(select(PresentationModel))).all() == []
            assert (await session.scalars(select(PresentationGenerationJob))).all() == []

    asyncio.run(scenario())


def test_api_submit_source_restyle_rejects_unadmittable_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Manifest rejection fails closed before any durable row or admission exists."""

    async def scenario() -> None:
        async def admit_manifest(candidate):
            raise SourceContentError("source_content_manifest_invalid", status_code=409)

        # Omitted strategy (svg): an explicit authored restyle is refused
        # before the manifest is even read.
        submission = {
            key: value for key, value in _SOURCE_RESTYLE_SUBMISSION.items()
            if key not in {"strategy", "masterPin"}
        }
        response, maker, _settings, request_plans, route_counts = (
            await _submit_source_restyle(
                tmp_path, monkeypatch, admit_manifest=admit_manifest, submission=submission,
            )
        )
        assert response.status_code == 409
        assert route_counts == []
        assert response.json() == {"detail": {"reasonCode": "source_content_manifest_invalid"}}
        assert request_plans == []
        async with maker() as session:
            assert (await session.scalars(select(WorkspaceOperation))).all() == []
            assert (await session.scalars(select(PresentationModel))).all() == []

    asyncio.run(scenario())
