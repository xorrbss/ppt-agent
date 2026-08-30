from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from api.v2.contracts import (
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
from models.sql.user import User
from models.sql.workspace import WorkspaceConversation, WorkspaceOperation, WorkspaceOperationEvent
from models.sql.async_presentation_generation_status import AsyncPresentationGenerationTaskModel
from models.sql.presentation_generation_job import PresentationGenerationJob
from services.public_v2_orchestrator import (
    PUBLIC_V2_POSTGRESQL_STRICT_PROFILE,
    SqlPublicV2Orchestrator,
    create_public_v2_orchestrator,
    resolve_public_v2_integration_profile,
)
from services.artifact_store import SignedArtifactDownload


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
        actor = PublicRequestActor(uuid.uuid4(), "tenant-db", "trace-db")

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
                return self._receipt("op_export_db", presentation_id)

            async def get_operation(self, *, actor, operation_id):
                self.calls.append("status")
                return OperationStatusV2(
                    operation_id=operation_id,
                    presentation_id="prs_db_1",
                    kind="presentation.generate",
                    status="running",
                    phase="authoring",
                    progress_percent=30,
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
            status = client.get("/api/v2/operations/op_db_1")
            events = client.get("/api/v2/operations/op_db_1/events")
            cancelled = client.post("/api/v2/operations/op_db_1/cancel")
            download = client.get("/api/v2/artifacts/artifact_db/download")

        assert first.status_code == 202
        assert duplicate.status_code == 202 and duplicate.json()["duplicate"] is True
        assert conflict.status_code == 409
        assert conflict.json() == {"detail": {"reasonCode": "idempotency_key_conflict"}}
        assert stale.status_code == 409
        assert stale.headers["retry-after"] == "7"
        assert status.status_code == 200 and status.json()["phase"] == "authoring"
        assert events.status_code == 200 and events.json()["events"][0]["sequence"] == 1
        assert cancelled.status_code == 202 and cancelled.json()["cancellationRequested"] is True
        assert download.status_code == 303 and download.headers["cache-control"] == "no-store"
        assert authority.calls == ["submit", "duplicate", "status", "events", "cancel", "download"]
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
        actor = PublicRequestActor(owner, "tenant-db", "trace-db")
        app = FastAPI()
        app.include_router(API_V2_PUBLIC_ROUTER)
        app.dependency_overrides[get_public_request_actor] = lambda: actor
        app.state.public_v2_orchestrator = adapter
        with TestClient(app, follow_redirects=False) as client:
            status = client.get(f"/api/v2/operations/{operation_id}")
            events = client.get(f"/api/v2/operations/{operation_id}/events")
            cancelled = client.post(f"/api/v2/operations/{operation_id}/cancel")
            app.dependency_overrides[get_public_request_actor] = lambda: PublicRequestActor(
                owner, "tenant-other", "trace-other"
            )
            cross_tenant_status = client.get(f"/api/v2/operations/{operation_id}")
        assert status.status_code == 200 and status.json()["status"] == "queued"
        assert events.status_code == 200 and events.json()["events"][0]["sequence"] == 1
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
        actor = PublicRequestActor(owner, "tenant-submit", "trace-submit")
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

        # The real admission callable must reject SQLite as a non-authoritative
        # backend, and the adapter must roll back the provisional rows.
        before_rows = await _row_counts(maker)
        app.state.public_v2_orchestrator = SqlPublicV2Orchestrator(maker)
        with TestClient(app) as client:
            unavailable = client.post(
                "/api/v2/presentations/generations", headers={"Idempotency-Key": "sqlite-key"},
                json={"schemaVersion": "presentation-generation-request/v2", "content": "must rollback", "slideCount": 5},
            )
        after_rows = await _row_counts(maker)
        assert unavailable.status_code == 503
        assert unavailable.json() == {"detail": {"reasonCode": "operation_authority_unavailable"}}
        assert after_rows == before_rows
        await engine.dispose()

    asyncio.run(scenario())


def test_sql_adapter_requires_current_publication_and_rejects_unwired_export_capabilities(
    tmp_path: Path,
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
                        "mode": "editable-hybrid",
                        "fallbackPolicy": "reject",
                    },
                }
            ),
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
                    request_payload={},
                    expected_revision=3,
                    result_revision=3,
                    contract_version="public-operation/v2",
                    execution_profile_version="public-generation-export/v2",
                    phase="publishing",
                    deadline_at=deadline,
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
                    fence=2,
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

        status = await adapter.get_operation(
            actor=actor,
            operation_id=str(operation_id),
        )
        assert [item.artifact_id for item in status.artifacts] == [
            str(current_artifact_id)
        ]
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
        await engine.dispose()

    asyncio.run(scenario())
