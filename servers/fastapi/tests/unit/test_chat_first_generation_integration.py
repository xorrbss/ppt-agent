from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks, HTTPException, Request
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from api.v1.ppt.endpoints import presentation_crud, presentation_generate
from enums.tone import Tone
from enums.verbosity import Verbosity
from models.generate_presentation_request import GeneratePresentationRequest
from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.presentation import (
    PRESENTATION_LIFECYCLE_PUBLISHED,
    PRESENTATION_LIFECYCLE_STAGING,
    PresentationModel,
)
from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.presentation_export_attempt import PresentationExportAttempt
from models.sql.public_admission import (
    PublicOperationAdmission,
    PublicPolicyProfile,
    PublicProviderLogicalCall,
    PublicQuotaCounter,
    PublicResourceLease,
)
from models.sql.public_artifact import PublicArtifact
from models.sql.user import User
from models.sql.workspace import (
    WorkspaceArtifact,
    WorkspaceAsset,
    WorkspaceConversation,
    WorkspaceMessage,
    WorkspaceMessageAttachment,
    WorkspaceOperation,
    WorkspaceOperationEvent,
    WorkspaceOutbox,
)
from services import (
    public_generation_slide_count,
    workspace_asset_service,
    workspace_legacy_write_gate,
    workspace_operation_runner,
)
from services.compatibility_migration.admission import (
    stamp_new_authored_presentation,
)
from services.public_policy_versions import PUBLIC_DEFAULT_POLICY_VERSION
from services.source_content.errors import SourceContentError
from services.workspace_artifact_publication_service import (
    WorkspaceArtifactPublicationError,
    prepare_workspace_generation_artifact,
    publish_workspace_generation_artifact,
)
from utils.owner_context import reset_current_owner_id, set_current_owner_id


def test_chat_first_public_admission_derives_count_from_reviewed_slides():
    request = GeneratePresentationRequest(
        presentation_id=uuid.uuid4(),
        content="Reviewed outline",
        slides_markdown=["# One", "# Two", "# Three"],
        n_slides=None,
    )

    admission_slide_count = presentation_generate._generation_admission_slide_count(
        request,
        SimpleNamespace(n_slides=30),
    )

    assert admission_slide_count == 3


def test_v1_async_generation_uses_strict_v2_admission_before_claim(
    tmp_path: Path,
    monkeypatch,
):
    """The browser-compatible v1 route must stamp the v2 operation contract."""

    async def scenario():
        engine, maker = await _database(tmp_path / "v1-v2-admission.db")
        async with maker() as session, session.begin():
            session.add(User(id=OWNER_A, username="owner-a", hashed_password="x"))
        owner_token = set_current_owner_id(OWNER_A)
        observed: dict[str, object] = {}
        try:
            monkeypatch.setattr(
                presentation_crud,
                "_chat_first_create_enabled",
                lambda: True,
            )
            monkeypatch.setattr(
                presentation_generate,
                "_chat_first_generation_enabled",
                lambda: True,
            )

            async def strict_admit(session, **kwargs):
                observed.update(kwargs)
                operation = await session.get(WorkspaceOperation, kwargs["operation_id"])
                assert operation is not None
                deadline = operation.created_at.replace(microsecond=0)
                # This is the minimum durable projection promised by v2.  The
                # production admission service computes the real deadline.
                from datetime import timedelta

                operation.contract_version = "public-operation/v2"
                operation.execution_profile_version = "public-generation-export/v2"
                operation.phase = "admitted"
                operation.deadline_at = deadline + timedelta(minutes=5)
                operation.fencing_token = 1
                return SimpleNamespace(
                    id=uuid.uuid4(),
                    fence=1,
                    deadline_at=operation.deadline_at,
                )

            monkeypatch.setattr(
                presentation_generate,
                "admit_public_operation_compatible_v2",
                strict_admit,
            )
            monkeypatch.setattr(
                presentation_generate,
                "check_if_api_request_is_valid",
                lambda *_args, **_kwargs: _async_return((None,)),
            )
            monkeypatch.setattr(
                presentation_generate,
                "_require_local_legacy_async_generation",
                lambda *_args, **_kwargs: None,
            )

            presentation_id = uuid.uuid4()
            async with maker() as session:
                await _create(
                    session,
                    presentation_id=presentation_id,
                    idempotency_key=f"presentation-create:{presentation_id}",
                )
            request = GeneratePresentationRequest(
                presentation_id=presentation_id,
                content="Browser v1 generation must use v2 admission",
                n_slides=3,
                language="en",
                strategy="authored",
            )
            async with maker() as session:
                request_http = Request(
                    {
                        "type": "http",
                        "http_version": "1.1",
                        "method": "POST",
                        "scheme": "http",
                        "path": "/api/v1/ppt/presentation/generate/async",
                        "raw_path": b"/api/v1/ppt/presentation/generate/async",
                        "query_string": b"",
                        "headers": [],
                        "client": ("test", 1234),
                        "server": ("test", 80),
                    }
                )
                request_http.state.request_id = "v1-v2-admission"
                queued = await presentation_generate.generate_presentation_async(
                    request_http,
                    request,
                    BackgroundTasks(),
                    idempotency_key=f"presentation-generate:{presentation_id}",
                    sql_session=session,
                )
                operation = await session.scalar(
                    select(WorkspaceOperation).where(
                        WorkspaceOperation.presentation_id == presentation_id,
                        WorkspaceOperation.kind == "presentation.generate",
                    )
                )
                assert queued.status == "pending"
                assert observed["operation_id"] == operation.id
                assert operation.contract_version == "public-operation/v2"
                assert operation.execution_profile_version == "public-generation-export/v2"
                # Admission is the only phase the route may reach: the
                # runner is what claims the operation and moves it to
                # "active" (services/public_operation_admission.py).
                assert operation.phase == "admitted"
                assert operation.deadline_at is not None
                assert operation.fencing_token == 1
                assert observed["operation_kind"] == "generation"
        finally:
            reset_current_owner_id(owner_token)
            await engine.dispose()

    asyncio.run(scenario())


async def _async_return(value):
    return value


class _DialectOnlySession:
    """A session the deprecated async route may only inspect for its dialect.

    ``generate_presentation_async`` admits the local SQLite runtime before
    it reaches ``queue_presentation_generation``.  The tests below stub that
    call, so the route must never touch the session for anything else; any
    other attribute access fails loudly instead of being silently tolerated.
    """

    def get_bind(self):
        return "sqlite"


def test_recovery_expires_phase1_partial_before_lease_and_releases_quota(
    tmp_path: Path,
    monkeypatch,
):
    """An expired partial admission must not remain claimable or hold quota."""

    async def scenario():
        engine, maker = await _database(tmp_path / "partial-admission-recovery.db")
        async with maker() as session, session.begin():
            session.add(User(id=OWNER_A, username="owner-a", hashed_password="x"))
        owner_token = set_current_owner_id(OWNER_A)
        now = datetime(2026, 9, 2, 3, 0, tzinfo=timezone.utc)
        try:
            monkeypatch.setattr(workspace_operation_runner, "_utc_now", lambda: now)
            presentation_id = uuid.uuid4()
            policy_version = "partial-test/v1"
            expired = now - timedelta(minutes=1)
            future_lease = now + timedelta(minutes=10)
            async with maker() as session:
                await _create(
                    session,
                    presentation_id=presentation_id,
                    idempotency_key=f"presentation-create:{presentation_id}",
                )
                operation = await session.scalar(
                    select(WorkspaceOperation).where(
                        WorkspaceOperation.presentation_id == presentation_id,
                        WorkspaceOperation.kind == "presentation.generate",
                    )
                )
                assert operation is not None
                operation.state = "running"
                operation.attempt_number = 1
                operation.attempt_token = "stuck-attempt"
                operation.fencing_token = 0
                operation.lease_expires_at = future_lease
                operation.heartbeat_at = future_lease
                operation.deadline_at = None

                task = AsyncPresentationGenerationTaskModel(
                    status="processing",
                    message="stuck",
                    owner_id=OWNER_A,
                )
                job = PresentationGenerationJob(
                    task_id=task.id,
                    presentation_id=presentation_id,
                    workspace_operation_id=operation.id,
                    request_sha256="a" * 64,
                    request_payload={"content": "stuck"},
                    state="running",
                    resume_allowed=True,
                )
                operation.request_payload = {"job_id": str(job.id)}
                policy = PublicPolicyProfile(
                    version=policy_version,
                    max_slides=100,
                    global_active_generation=10,
                    global_queued_generation=10,
                    owner_active_generation=10,
                    owner_queued_generation=10,
                    tenant_active_generation=10,
                    tenant_queued_generation=10,
                    global_active_export=10,
                    global_queued_export=10,
                    owner_active_export=10,
                    owner_queued_export=10,
                    tenant_active_export=10,
                    tenant_queued_export=10,
                    global_active_resource_units=100,
                    global_queued_resource_units=100,
                    tenant_active_resource_units=100,
                    tenant_queued_resource_units=100,
                    owner_active_resource_units=100,
                    owner_queued_resource_units=100,
                    retry_request_limit=1,
                    estimated_cost_limit_microunits=1,
                    actual_cost_limit_microunits=1,
                    generation_deadline_seconds=60,
                    export_deadline_seconds=60,
                )
                admission = PublicOperationAdmission(
                    operation_id=operation.id,
                    policy_version=policy_version,
                    owner_id=OWNER_A,
                    tenant_id="default",
                    trace_id_hash="b" * 64,
                    operation_kind="generation",
                    resource_units=1,
                    policy_digest="c" * 64,
                    deadline_at=expired,
                    fence=0,
                    state="active",
                    planned_request_limit=1,
                    retry_request_limit=1,
                    estimated_cost_limit_microunits=1,
                    actual_cost_limit_microunits=1,
                )
                counters = [
                    PublicQuotaCounter(
                        policy_version=policy_version,
                        scope_type=scope,
                        scope_key=key,
                        operation_kind="generation",
                        active_count=1,
                        active_resource_units=1,
                    )
                    for scope, key in (
                        # Every admission reserves one slot in all three
                        # scopes, so recovery must release all three.
                        ("global", "global"),
                        ("tenant", "default"),
                        ("owner", str(OWNER_A)),
                    )
                ]
                session.add(policy)
                await session.flush()
                session.add_all([task, job, admission, *counters])
                await session.commit()

            recovered = await workspace_operation_runner.recover_expired_workspace_operations(
                session_factory=maker
            )
            assert recovered == 1
            async with maker() as session:
                operation = await session.get(WorkspaceOperation, operation.id)
                admission = await session.get(PublicOperationAdmission, admission.id)
                job = await session.get(PresentationGenerationJob, job.id)
                task = await session.get(AsyncPresentationGenerationTaskModel, task.id)
                assert operation.state == "deadline_exceeded"
                assert admission.state == "deadline_exceeded"
                assert job.state == "failed"
                assert job.resume_allowed is False
                assert task.status == "error"
                assert task.error["code"] == "public_deadline_exceeded"
                counts = (
                    await session.execute(
                        select(PublicQuotaCounter).where(
                            PublicQuotaCounter.policy_version == policy_version
                        )
                    )
                ).scalars()
                assert all(row.active_count == 0 for row in counts)
        finally:
            reset_current_owner_id(owner_token)
            await engine.dispose()

    asyncio.run(scenario())


OWNER_A = uuid.UUID("11111111-1111-4111-8111-111111111111")
OWNER_B = uuid.UUID("22222222-2222-4222-8222-222222222222")
TABLES = [
    User.__table__,
    PublicArtifact.__table__,
    PublicPolicyProfile.__table__,
    PublicQuotaCounter.__table__,
    PublicOperationAdmission.__table__,
    PublicProviderLogicalCall.__table__,
    PublicResourceLease.__table__,
    PresentationModel.__table__,
    WorkspaceConversation.__table__,
    WorkspaceMessage.__table__,
    WorkspaceAsset.__table__,
    WorkspaceMessageAttachment.__table__,
    WorkspaceOperation.__table__,
    WorkspaceOperationEvent.__table__,
    WorkspaceOutbox.__table__,
    PresentationExportAttempt.__table__,
    WorkspaceArtifact.__table__,
    AsyncPresentationGenerationTaskModel.__table__,
    PresentationGenerationJob.__table__,
]


async def _database(path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_safety(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")

    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: SQLModel.metadata.create_all(
                sync_connection,
                tables=TABLES,
            )
        )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _create(
    session,
    *,
    presentation_id: uuid.UUID,
    idempotency_key: str,
    content: str = "Build a durable strategy deck",
    theme: dict | None = None,
):
    return await presentation_crud.create_presentation(
        content=content,
        presentation_id=presentation_id,
        n_slides=8,
        language="en",
        file_paths=[],
        tone=Tone.PROFESSIONAL,
        verbosity=Verbosity.STANDARD,
        instructions="Use concise evidence",
        include_table_of_contents=True,
        include_title_slide=True,
        web_search=False,
        theme=theme,
        idempotency_key=idempotency_key,
        sql_session=session,
    )


def test_create_replay_is_single_turn_and_payload_mismatch_conflicts(
    tmp_path: Path,
    monkeypatch,
):
    async def scenario():
        engine, maker = await _database(tmp_path / "create-replay.db")
        monkeypatch.setattr(
            presentation_crud,
            "_chat_first_create_enabled",
            lambda: True,
        )
        presentation_id = uuid.uuid4()
        key = f"presentation-create:{presentation_id}"
        try:
            async with maker() as session:
                first = await _create(
                    session,
                    presentation_id=presentation_id,
                    idempotency_key=key,
                )
            async with maker() as session:
                replay = await _create(
                    session,
                    presentation_id=presentation_id,
                    idempotency_key=key,
                )
            assert first.id == replay.id == presentation_id

            async with maker() as session:
                assert await session.scalar(
                    select(func.count()).select_from(WorkspaceMessage)
                ) == 1
                operation = (
                    await session.execute(select(WorkspaceOperation))
                ).scalar_one()
                assert operation.state == "awaiting_approval"
                assert operation.presentation_id == presentation_id

            async with maker() as session:
                with pytest.raises(HTTPException) as caught:
                    await _create(
                        session,
                        presentation_id=presentation_id,
                        idempotency_key=key,
                        content="A materially different request",
                    )
                assert caught.value.status_code == 409
                assert caught.value.detail == "idempotency_key_payload_conflict"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_outline_stream_admits_a_staged_deck_at_full_rollout(
    tmp_path: Path,
    monkeypatch,
):
    """A fully enabled cohort must still be able to generate a new deck.

    ``legacy_write_requires_presentation_scope`` rejects before touching the
    database once the rollout reaches 100%, so calling it from the outline
    stream made the staging bootstrap allowance unreachable and left every new
    presentation stuck with no outline.
    """

    async def scenario():
        engine, maker = await _database(tmp_path / "outline-full-rollout.db")
        monkeypatch.setattr(
            presentation_crud, "_chat_first_create_enabled", lambda: True
        )
        presentation_id = uuid.uuid4()
        try:
            # The blanket gate the endpoint must not use rejects outright here.
            with pytest.raises(HTTPException) as preflight:
                workspace_legacy_write_gate.require_legacy_presentation_write_path()
            assert preflight.value.status_code == 409

            async with maker() as session:
                presentation = await _create(
                    session,
                    presentation_id=presentation_id,
                    idempotency_key=f"presentation-create:{presentation_id}",
                )
                # The guard the endpoint actually relies on still admits it.
                assert (
                    await workspace_legacy_write_gate.require_outline_bootstrap_write_path(
                        session, presentation
                    )
                    is None
                )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_created_staging_deck_admits_outline_only_until_generation_activation(
    tmp_path: Path,
    monkeypatch,
):
    async def scenario():
        engine, maker = await _database(tmp_path / "outline-bootstrap.db")
        monkeypatch.setattr(
            presentation_crud,
            "_chat_first_create_enabled",
            lambda: True,
        )
        monkeypatch.setenv("CHAT_FIRST_WORKSPACE_ENABLED", "true")
        monkeypatch.setenv(
            "CHAT_FIRST_WORKSPACE_RUNTIME_PROFILE",
            "sqlite-local",
        )
        presentation_id = uuid.uuid4()
        admit_outline = (
            workspace_legacy_write_gate.require_outline_bootstrap_write_path
        )
        try:
            async with maker() as session:
                presentation = await _create(
                    session,
                    presentation_id=presentation_id,
                    idempotency_key=f"presentation-create:{presentation_id}",
                )
                assert (
                    await admit_outline(
                        session,
                        presentation,
                    )
                    is None
                )

                operation = (
                    await session.execute(select(WorkspaceOperation))
                ).scalar_one()
                operation.state = "queued"
                session.add(operation)
                await session.commit()

            async with maker() as session:
                presentation = await session.get(PresentationModel, presentation_id)
                with pytest.raises(HTTPException) as caught:
                    await admit_outline(
                        session,
                        presentation,
                    )
                assert caught.value.status_code == 409
                assert caught.value.detail == "chat_first_command_required"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_create_persists_theme_but_only_safe_theme_metadata_in_first_message(
    tmp_path: Path,
    monkeypatch,
):
    async def scenario():
        engine, maker = await _database(tmp_path / "create-theme.db")
        monkeypatch.setattr(
            presentation_crud,
            "_chat_first_create_enabled",
            lambda: True,
        )
        presentation_id = uuid.uuid4()
        key = f"presentation-create:{presentation_id}"
        theme = {
            "id": "customer-brand",
            "name": "Customer Brand",
            "description": "Approved customer palette",
            "user": "local",
            "logo": None,
            "logo_url": "/assets/brand-logo.png",
            "company_name": "Example Co",
            "data": {
                "colors": {"primary": "#123456"},
                "private_api_key": "must-never-appear-in-chat-metadata",
            },
        }
        try:
            async with maker() as session:
                first = await _create(
                    session,
                    presentation_id=presentation_id,
                    idempotency_key=key,
                    theme=theme,
                )
            # The server stamps its own authored mode marker on top of the
            # admitted theme; nothing else about the caller theme changes.
            stamped_theme = first.theme
            assert {key: stamped_theme[key] for key in theme} == theme
            assert stamped_theme["mode"] == "authored"
            assert stamped_theme["authored_vnext"]["canvas"]["contractVersion"] == "authored-canvas/v1"

            async with maker() as session:
                stored = await session.get(PresentationModel, presentation_id)
                message = (
                    await session.execute(select(WorkspaceMessage))
                ).scalar_one()
                assert stored is not None
                assert stored.theme == stamped_theme
                settings = message.message_metadata["generation_settings"]
                assert settings["theme_id"] == "customer-brand"
                assert settings["theme_name"] == "Customer Brand"
                assert settings["theme_source"] == "custom"
                assert "theme" not in settings
                assert "data" not in settings
                assert "logo_url" not in settings
                assert "private_api_key" not in str(message.message_metadata)

            async with maker() as session:
                replay = await _create(
                    session,
                    presentation_id=presentation_id,
                    idempotency_key=key,
                    theme=theme,
                )
                assert replay.id == first.id

            changed_theme = {
                **theme,
                "data": {"colors": {"primary": "#654321"}},
            }
            async with maker() as session:
                with pytest.raises(HTTPException) as caught:
                    await _create(
                        session,
                        presentation_id=presentation_id,
                        idempotency_key=key,
                        theme=changed_theme,
                    )
                assert caught.value.status_code == 409
                assert caught.value.detail == "idempotency_key_payload_conflict"

            async with maker() as session:
                with pytest.raises(HTTPException) as caught:
                    await _create(
                        session,
                        presentation_id=uuid.uuid4(),
                        idempotency_key="presentation-create:invalid-theme",
                        theme={**theme, "unexpected_secret": "do-not-store"},
                    )
                assert caught.value.status_code == 422
                assert caught.value.detail == "invalid_presentation_theme"

            oversized_theme = {
                **theme,
                "data": {"payload": "x" * (256 * 1024)},
            }
            async with maker() as session:
                with pytest.raises(HTTPException) as caught:
                    await _create(
                        session,
                        presentation_id=uuid.uuid4(),
                        idempotency_key="presentation-create:oversized-theme",
                        theme=oversized_theme,
                    )
                assert caught.value.status_code == 422
                assert caught.value.detail == "presentation_theme_too_large"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_legacy_create_ignores_chat_first_theme_admission(tmp_path: Path, monkeypatch):
    async def scenario():
        engine, maker = await _database(tmp_path / "legacy-create-theme.db")
        monkeypatch.setattr(
            presentation_crud,
            "_chat_first_create_enabled",
            lambda: False,
        )
        try:
            async with maker() as session:
                created = await _create(
                    session,
                    presentation_id=uuid.uuid4(),
                    idempotency_key="legacy-create",
                    theme={"unexpected": "legacy clients do not admit themes"},
                )
            assert created.lifecycle_status == PRESENTATION_LIFECYCLE_PUBLISHED
            # The legacy path drops the caller theme entirely: what remains is
            # only the server-issued authored marker.
            assert created.theme["mode"] == "authored"
            assert created.theme["authored_vnext"]["canvas"]["contractVersion"] == "authored-canvas/v1"
            async with maker() as session:
                assert await session.scalar(
                    select(func.count()).select_from(WorkspaceMessage)
                ) == 0
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_create_id_cannot_replay_across_owners(tmp_path: Path, monkeypatch):
    async def scenario():
        engine, maker = await _database(tmp_path / "create-owner.db")
        monkeypatch.setattr(
            presentation_crud,
            "_chat_first_create_enabled",
            lambda: True,
        )
        presentation_id = uuid.uuid4()
        key = f"presentation-create:{presentation_id}"
        try:
            async with maker() as session, session.begin():
                session.add_all(
                    [
                        User(id=OWNER_A, username="owner-a", hashed_password="x"),
                        User(id=OWNER_B, username="owner-b", hashed_password="x"),
                    ]
                )

            owner_a_token = set_current_owner_id(OWNER_A)
            try:
                async with maker() as session:
                    await _create(
                        session,
                        presentation_id=presentation_id,
                        idempotency_key=key,
                    )
            finally:
                reset_current_owner_id(owner_a_token)

            owner_b_token = set_current_owner_id(OWNER_B)
            try:
                async with maker() as session:
                    with pytest.raises(HTTPException) as caught:
                        await _create(
                            session,
                            presentation_id=presentation_id,
                            idempotency_key=key,
                        )
                    assert caught.value.status_code == 409
                    assert caught.value.detail == "presentation_id_unavailable"
            finally:
                reset_current_owner_id(owner_b_token)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_create_copies_attachments_to_durable_workspace_storage(
    tmp_path: Path,
    monkeypatch,
):
    async def scenario():
        engine, maker = await _database(tmp_path / "create-attachment.db")
        monkeypatch.setattr(
            presentation_crud,
            "_chat_first_create_enabled",
            lambda: True,
        )
        monkeypatch.setattr(
            presentation_crud,
            "validate_document_paths",
            lambda paths: list(paths),
        )
        asset_root = tmp_path / "workspace-assets"
        monkeypatch.setattr(
            workspace_asset_service,
            "get_writable_path",
            lambda _name: str(asset_root),
        )
        source = tmp_path / "evidence.txt"
        source.write_text("durable evidence", encoding="utf-8")
        presentation_id = uuid.uuid4()
        key = f"presentation-create:{presentation_id}"

        async def create_with_source(session):
            return await presentation_crud.create_presentation(
                content="Use the attached evidence",
                presentation_id=presentation_id,
                n_slides=6,
                language="en",
                file_paths=[str(source)],
                tone=Tone.PROFESSIONAL,
                verbosity=Verbosity.STANDARD,
                instructions=None,
                include_table_of_contents=False,
                include_title_slide=True,
                web_search=False,
                idempotency_key=key,
                sql_session=session,
            )

        try:
            async with maker() as session:
                created = await create_with_source(session)
            assert created.file_paths is not None
            durable_path = Path(created.file_paths[0])
            assert durable_path != source
            assert durable_path.read_text(encoding="utf-8") == "durable evidence"

            # A lost create response may be retried with the same client IDs.
            # It must bind to the original asset instead of copying a second one.
            async with maker() as session:
                replay = await create_with_source(session)
            assert replay.id == created.id

            async with maker() as session:
                asset = (await session.execute(select(WorkspaceAsset))).scalar_one()
                operation = (
                    await session.execute(select(WorkspaceOperation))
                ).scalar_one()
                assert asset.state == "ready"
                assert asset.sha256 == sha256(b"durable evidence").hexdigest()
                assert str(asset.id) in operation.request_payload["asset_ids"]

            source.write_text("different evidence", encoding="utf-8")
            async with maker() as session:
                with pytest.raises(HTTPException) as caught:
                    await create_with_source(session)
                assert caught.value.status_code == 409
                assert caught.value.detail == "idempotency_key_payload_conflict"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_generate_activates_and_links_bootstrap_operation(tmp_path: Path, monkeypatch):
    async def scenario():
        engine, maker = await _database(tmp_path / "activate-generation.db")
        async with maker() as session, session.begin():
            session.add(User(id=OWNER_A, username="owner-a", hashed_password="x"))
            session.add(_public_policy_profile(PUBLIC_DEFAULT_POLICY_VERSION))
        owner_token = set_current_owner_id(OWNER_A)
        monkeypatch.setattr(
            presentation_crud,
            "_chat_first_create_enabled",
            lambda: True,
        )
        monkeypatch.setattr(
            presentation_generate,
            "_chat_first_generation_enabled",
            lambda: True,
        )
        monkeypatch.setattr(
            presentation_generate,
            "wake_workspace_operation_runner",
            lambda: None,
        )

        async def accept_request(_request, _session):
            return (uuid.uuid4(),)

        monkeypatch.setattr(
            presentation_generate,
            "check_if_api_request_is_valid",
            accept_request,
        )
        presentation_id = uuid.uuid4()
        create_key = f"presentation-create:{presentation_id}"
        generate_key = f"presentation-generate:{presentation_id}"
        request = GeneratePresentationRequest(
            presentation_id=presentation_id,
            content="Build a durable strategy deck",
            n_slides=8,
            language="en",
            strategy="authored",
            files=["C:/temporary-upload/source.txt"],
        )
        validated_files: list[list[str]] = []

        async def load_document_context(paths, _language):
            validated_files.append(paths)
            return "durable context"

        monkeypatch.setattr(
            presentation_generate,
            "_load_document_context",
            load_document_context,
        )
        try:
            async with maker() as session:
                await _create(
                    session,
                    presentation_id=presentation_id,
                    idempotency_key=create_key,
                )
                presentation = await session.get(PresentationModel, presentation_id)
                assert presentation is not None
                presentation.file_paths = ["C:/workspace-assets/durable-source.txt"]
                await session.commit()
            async with maker() as session:
                first_status = await presentation_generate.queue_presentation_generation(
                    request,
                    BackgroundTasks(),
                    session,
                    export_cookie_header="session=must-not-persist",
                    idempotency_key=generate_key,
                )
            async with maker() as session:
                replay_status = await presentation_generate.queue_presentation_generation(
                    request,
                    BackgroundTasks(),
                    session,
                    export_cookie_header="session=must-not-persist",
                    idempotency_key=generate_key,
                )
            assert replay_status.id == first_status.id

            async with maker() as session:
                persisted_presentation = await session.get(
                    PresentationModel,
                    presentation_id,
                )
                assert persisted_presentation is not None
                assert persisted_presentation.file_paths is None
                operation = (
                    await session.execute(select(WorkspaceOperation))
                ).scalar_one()
                job = (
                    await session.execute(select(PresentationGenerationJob))
                ).scalar_one()
                status = await session.get(
                    AsyncPresentationGenerationTaskModel,
                    job.task_id,
                )
                assert status is not None
                assert operation.state == "queued"
                assert operation.request_payload["job_id"] == str(job.id)
                assert job.workspace_operation_id == operation.id
                assert job.presentation_id == presentation_id
                assert job.export_cookie_required is False
                assert job.request_payload["files"] is None
                assert "C:/workspace-assets/durable-source.txt" not in str(
                    job.request_payload
                )
                presentation = await session.get(PresentationModel, presentation_id)
                assert presentation is not None
                assert presentation.file_paths is None
                assert await session.scalar(
                    select(func.count()).select_from(WorkspaceMessage)
                ) == 1
            assert validated_files == [["C:/workspace-assets/durable-source.txt"]]
        finally:
            reset_current_owner_id(owner_token)
            await engine.dispose()

    asyncio.run(scenario())


def test_bare_generate_bootstrap_persists_and_claims_reference_snapshots(
    tmp_path: Path,
    monkeypatch,
):
    async def scenario():
        engine, maker = await _database(tmp_path / "bare-reference-generation.db")
        async with maker() as session, session.begin():
            session.add(User(id=OWNER_A, username="owner-a", hashed_password="x"))
            session.add(_public_policy_profile(PUBLIC_DEFAULT_POLICY_VERSION))
        owner_token = set_current_owner_id(OWNER_A)
        monkeypatch.setattr(
            presentation_generate,
            "_chat_first_generation_enabled",
            lambda: True,
        )
        monkeypatch.setattr(
            presentation_generate,
            "wake_workspace_operation_runner",
            lambda: None,
        )
        snapshot_id = uuid.uuid4()
        asset_id = uuid.uuid4()
        claimed: list[tuple[list[uuid.UUID], uuid.UUID]] = []

        async def admit(_session, *, snapshot_ids, **_kwargs):
            assert snapshot_ids == [snapshot_id]
            return (SimpleNamespace(asset_bindings=(SimpleNamespace(asset_id=asset_id),)),)

        async def claim(_session, *, snapshot_ids, conversation_id):
            claimed.append((snapshot_ids, conversation_id))

        async def accept_request(_request, _session):
            return None

        monkeypatch.setattr(
            presentation_generate,
            "admit_reference_snapshots",
            admit,
        )
        monkeypatch.setattr(
            presentation_generate,
            "claim_reference_snapshots",
            claim,
        )
        monkeypatch.setattr(
            presentation_generate,
            "check_if_api_request_is_valid",
            accept_request,
        )
        request = GeneratePresentationRequest(
            content="Build from the canonical uploaded reference",
            n_slides=4,
            language="en",
            strategy="authored",
            reference_snapshot_ids=[snapshot_id],
        )
        try:
            async with maker() as session:
                session.add(
                    WorkspaceAsset(
                        id=asset_id,
                        state="ready",
                        original_name="reference.txt",
                        media_type="text/plain",
                        size_bytes=9,
                        storage_key="owner/asset/reference.txt",
                        sha256="a" * 64,
                        version=3,
                    )
                )
                await session.commit()

            async with maker() as session:
                status = await presentation_generate.queue_presentation_generation(
                    request,
                    BackgroundTasks(),
                    session,
                    idempotency_key="bare-reference-generation",
                )
                assert status.status == "pending"

            async with maker() as session:
                presentation = (
                    await session.execute(select(PresentationModel))
                ).scalar_one()
                conversation = (
                    await session.execute(select(WorkspaceConversation))
                ).scalar_one()
                operation = (
                    await session.execute(select(WorkspaceOperation))
                ).scalar_one()
                assert presentation.reference_snapshot_ids == [str(snapshot_id)]
                assert presentation.profile_driver_id == "authored"
                assert presentation.profile_schema_version == 1
                assert presentation.mode == "authored"
                assert presentation.version == "v1-standard"
                assert presentation.theme["mode"] == "authored"
                assert presentation.theme["authored_vnext"]["canvas"]["contractVersion"] == "authored-canvas/v1"
                assert operation.request_payload["asset_ids"] == [str(asset_id)]
                assert operation.request_payload["generation_settings"][
                    "reference_snapshot_ids"
                ] == [str(snapshot_id)]
                assert claimed == [([snapshot_id], conversation.id)]
        finally:
            reset_current_owner_id(owner_token)
            await engine.dispose()

    asyncio.run(scenario())


def test_generate_admission_rejection_is_atomic_and_replays_failed_task(
    tmp_path: Path,
    monkeypatch,
):
    async def scenario():
        engine, maker = await _database(tmp_path / "admission-rejected.db")
        async with maker() as session, session.begin():
            session.add(User(id=OWNER_A, username="owner-a", hashed_password="x"))
            session.add(_public_policy_profile(PUBLIC_DEFAULT_POLICY_VERSION))
        owner_token = set_current_owner_id(OWNER_A)
        monkeypatch.setattr(
            presentation_crud,
            "_chat_first_create_enabled",
            lambda: True,
        )
        monkeypatch.setattr(
            presentation_generate,
            "_chat_first_generation_enabled",
            lambda: True,
        )
        monkeypatch.setattr(
            presentation_generate,
            "wake_workspace_operation_runner",
            lambda: None,
        )
        validation_calls = 0

        async def reject_request(_request, _session):
            nonlocal validation_calls
            validation_calls += 1
            raise HTTPException(
                status_code=422,
                detail="template_v2_generation_invalid",
            )

        monkeypatch.setattr(
            presentation_generate,
            "check_if_api_request_is_valid",
            reject_request,
        )
        presentation_id = uuid.uuid4()
        generate_key = f"presentation-generate:{presentation_id}"
        request = GeneratePresentationRequest(
            presentation_id=presentation_id,
            content="Keep this first message after rejected admission",
            n_slides=8,
            language="en",
            strategy="authored",
        )
        try:
            async with maker() as session:
                await _create(
                    session,
                    presentation_id=presentation_id,
                    idempotency_key=f"presentation-create:{presentation_id}",
                    content=request.content,
                )

            async with maker() as session:
                with pytest.raises(HTTPException) as caught:
                    await presentation_generate.queue_presentation_generation(
                        request,
                        BackgroundTasks(),
                        session,
                        idempotency_key=generate_key,
                    )
                assert caught.value.status_code == 422
                assert caught.value.detail == "template_v2_generation_invalid"
                assert caught.value.headers == {
                    presentation_generate.CHAT_FIRST_WORKSPACE_PROJECTED_HEADER: "true"
                }

            async with maker() as session:
                operation = (
                    await session.execute(select(WorkspaceOperation))
                ).scalar_one()
                job = (
                    await session.execute(select(PresentationGenerationJob))
                ).scalar_one()
                status = await session.get(
                    AsyncPresentationGenerationTaskModel,
                    job.task_id,
                )
                event_row = (
                    await session.execute(
                        select(WorkspaceOperationEvent).where(
                            WorkspaceOperationEvent.operation_id == operation.id,
                            WorkspaceOperationEvent.event_type == "operation.error",
                        )
                    )
                ).scalar_one()
                outbox = (
                    await session.execute(
                        select(WorkspaceOutbox).where(
                            WorkspaceOutbox.operation_id == operation.id,
                            WorkspaceOutbox.event_cursor == event_row.cursor,
                        )
                    )
                ).scalar_one()
                first_message = (
                    await session.execute(select(WorkspaceMessage))
                ).scalar_one()

                assert first_message.role == "user"
                assert first_message.content == request.content
                assert first_message.message_metadata is not None
                assert first_message.message_metadata["generation_settings"] == {
                    "n_slides": 8,
                    "language": "en",
                    "tone": "professional",
                    "verbosity": "standard",
                    "instructions": "Use concise evidence",
                    "include_table_of_contents": True,
                    "include_title_slide": True,
                    "web_search": False,
                    "aspect_ratio": "16:9",
                }
                assert operation.state == "failed"
                assert operation.error_code == "template_v2_generation_invalid"
                assert operation.error_detail == {
                    "code": "template_v2_generation_invalid",
                    "detail": "template_v2_generation_invalid",
                    "status_code": 422,
                    "retryable": False,
                    "stage": "admission",
                }
                assert operation.request_payload["job_id"] == str(job.id)
                assert job.workspace_operation_id == operation.id
                assert job.state == "failed"
                assert job.current_stage == "admission"
                assert job.resume_allowed is False
                assert job.request_payload[
                    presentation_generate._INTERNAL_ADMISSION_RETRY_KEY
                ] is True
                assert status is not None
                assert status.status == "error"
                assert status.error == operation.error_detail
                assert event_row.payload["retryable"] is False
                assert outbox.topic == "workspace.operation.event"
                assert await session.scalar(
                    select(func.count()).select_from(WorkspaceMessage)
                ) == 1

            # A response-loss replay with the same key returns the durable
            # failure. It must not repeat validation or create another turn,
            # task, job, event, or outbox record.
            async with maker() as session:
                replay = await presentation_generate.queue_presentation_generation(
                    request,
                    BackgroundTasks(),
                    session,
                    idempotency_key=generate_key,
                )
                assert replay.status == "error"
                assert replay.error is not None
                assert replay.error["code"] == "template_v2_generation_invalid"
                assert await session.scalar(
                    select(func.count()).select_from(PresentationGenerationJob)
                ) == 1
                assert await session.scalar(
                    select(func.count()).select_from(
                        AsyncPresentationGenerationTaskModel
                    )
                ) == 1
                assert await session.scalar(
                    select(func.count())
                    .select_from(WorkspaceOperationEvent)
                    .where(
                        WorkspaceOperationEvent.event_type == "operation.error"
                    )
                ) == 1
                assert await session.scalar(
                    select(func.count())
                    .select_from(WorkspaceOutbox)
                    .where(
                        WorkspaceOutbox.event_cursor == event_row.cursor,
                        WorkspaceOutbox.operation_id == operation.id,
                    )
                ) == 1
                assert await session.scalar(
                    select(func.count()).select_from(WorkspaceMessage)
                ) == 1
            assert validation_calls == 1
        finally:
            reset_current_owner_id(owner_token)
            await engine.dispose()

    asyncio.run(scenario())


def test_raw_path_admission_failure_requires_reattach_without_persisting_locator(
    tmp_path: Path,
    monkeypatch,
):
    async def scenario():
        engine, maker = await _database(tmp_path / "admission-retry.db")
        async with maker() as session, session.begin():
            session.add(User(id=OWNER_A, username="owner-a", hashed_password="x"))
            session.add(_public_policy_profile(PUBLIC_DEFAULT_POLICY_VERSION))
        owner_token = set_current_owner_id(OWNER_A)
        monkeypatch.setattr(
            presentation_crud,
            "_chat_first_create_enabled",
            lambda: True,
        )
        monkeypatch.setattr(
            presentation_generate,
            "_chat_first_generation_enabled",
            lambda: True,
        )
        monkeypatch.setattr(
            presentation_generate,
            "wake_workspace_operation_runner",
            lambda: None,
        )

        async def accept_request(_request, _session):
            return (uuid.uuid4(),)

        async def unavailable_documents(_paths, _language):
            raise RuntimeError("private loader detail must not be persisted")

        monkeypatch.setattr(
            presentation_generate,
            "check_if_api_request_is_valid",
            accept_request,
        )
        monkeypatch.setattr(
            presentation_generate,
            "_load_document_context",
            unavailable_documents,
        )
        presentation_id = uuid.uuid4()
        durable_path = "C:/workspace-assets/retry-source.txt"
        request = GeneratePresentationRequest(
            presentation_id=presentation_id,
            content="Retry document admission safely",
            n_slides=8,
            language="en",
            strategy="authored",
            files=["C:/temporary-upload/source.txt"],
        )
        try:
            async with maker() as session:
                await _create(
                    session,
                    presentation_id=presentation_id,
                    idempotency_key=f"presentation-create:{presentation_id}",
                )
                presentation = await session.get(PresentationModel, presentation_id)
                assert presentation is not None
                presentation.file_paths = [durable_path]
                await session.commit()

            async with maker() as session:
                with pytest.raises(HTTPException) as caught:
                    await presentation_generate.queue_presentation_generation(
                        request,
                        BackgroundTasks(),
                        session,
                        idempotency_key=f"presentation-generate:{presentation_id}",
                    )
                assert caught.value.status_code == 409
                assert caught.value.detail == "legacy_file_reference_reattach_required"
                assert "private loader detail" not in str(caught.value.detail)
                assert caught.value.headers == {
                    presentation_generate.CHAT_FIRST_WORKSPACE_PROJECTED_HEADER: "true"
                }

            async with maker() as session:
                operation = (
                    await session.execute(select(WorkspaceOperation))
                ).scalar_one()
                job = (
                    await session.execute(select(PresentationGenerationJob))
                ).scalar_one()
                status = await session.get(
                    AsyncPresentationGenerationTaskModel,
                    job.task_id,
                )
                assert status is not None
                assert operation.state == "failed"
                assert operation.error_code == "legacy_file_reference_reattach_required"
                assert operation.error_detail is not None
                assert operation.error_detail["retryable"] is False
                assert "private loader detail" not in str(operation.error_detail)
                assert job.state == "failed"
                assert job.resume_allowed is False
                assert job.request_payload["files"] is None
                assert durable_path not in str(job.request_payload)

                restored = presentation_generate._request_from_durable_payload(
                    job.request_payload
                )
                assert restored.files is None

                async def retry_rejected(_request, _session):
                    raise HTTPException(
                        status_code=422,
                        detail="template_v2_generation_invalid",
                    )

                monkeypatch.setattr(
                    presentation_generate,
                    "check_if_api_request_is_valid",
                    retry_rejected,
                )
                with pytest.raises(
                    presentation_generate.GenerationAdmissionRetryError
                ) as caught:
                    await presentation_generate.generate_presentation_handler(
                        restored,
                        presentation_id,
                        async_status=status,
                        generation_job=job,
                        sql_session=session,
                    )
                assert caught.value.code == "template_v2_generation_invalid"
                assert caught.value.status_code == 422
                assert caught.value.retryable is False
                await session.refresh(status)
                assert status.status == "error"
                assert status.error == caught.value.detail
        finally:
            reset_current_owner_id(owner_token)
            await engine.dispose()

    asyncio.run(scenario())


def test_admission_projection_race_does_not_mark_the_response(
    tmp_path: Path,
    monkeypatch,
):
    async def scenario():
        engine, maker = await _database(tmp_path / "admission-projection-race.db")
        async with maker() as session, session.begin():
            session.add(User(id=OWNER_A, username="owner-a", hashed_password="x"))
            session.add(_public_policy_profile(PUBLIC_DEFAULT_POLICY_VERSION))
        owner_token = set_current_owner_id(OWNER_A)
        monkeypatch.setattr(
            presentation_crud,
            "_chat_first_create_enabled",
            lambda: True,
        )
        monkeypatch.setattr(
            presentation_generate,
            "_chat_first_generation_enabled",
            lambda: True,
        )

        async def reject_request(_request, _session):
            raise HTTPException(
                status_code=422,
                detail="template_v2_generation_invalid",
            )

        async def lose_projection_race(*_args, **_kwargs):
            return False

        monkeypatch.setattr(
            presentation_generate,
            "check_if_api_request_is_valid",
            reject_request,
        )
        monkeypatch.setattr(
            presentation_generate,
            "_record_chat_first_generation_admission_failure",
            lose_projection_race,
        )
        presentation_id = uuid.uuid4()
        request = GeneratePresentationRequest(
            presentation_id=presentation_id,
            content="Do not claim a projection that lost its race",
            n_slides=8,
            language="en",
            strategy="authored",
        )
        try:
            async with maker() as session:
                await _create(
                    session,
                    presentation_id=presentation_id,
                    idempotency_key=f"presentation-create:{presentation_id}",
                )

            async with maker() as session:
                with pytest.raises(HTTPException) as caught:
                    await presentation_generate.queue_presentation_generation(
                        request,
                        BackgroundTasks(),
                        session,
                        idempotency_key=f"presentation-generate:{presentation_id}",
                    )
                assert caught.value.status_code == 422
                assert caught.value.detail == "template_v2_generation_invalid"
                assert not caught.value.headers
        finally:
            reset_current_owner_id(owner_token)
            await engine.dispose()

    asyncio.run(scenario())


def test_generate_async_wraps_unprojected_non_http_failure_without_marker(
    monkeypatch,
):
    async def fail_before_projection(*_args, **_kwargs):
        raise RuntimeError("private admission implementation detail")

    monkeypatch.setattr(
        presentation_generate,
        "queue_presentation_generation",
        fail_before_projection,
    )

    async def scenario():
        request_http = Request(
            {
                "type": "http",
                "http_version": "1.1",
                "method": "POST",
                "scheme": "http",
                "path": "/api/v1/ppt/presentation/generate/async",
                "raw_path": b"/api/v1/ppt/presentation/generate/async",
                "query_string": b"",
                "headers": [],
                "client": ("test", 1234),
                "server": ("test", 80),
            }
        )
        request_http.state.request_id = "request-before-projection"
        request = GeneratePresentationRequest(
            presentation_id=uuid.uuid4(),
            content="Fail safely before projection",
            n_slides=8,
            language="en",
        )
        with pytest.raises(HTTPException) as caught:
            await presentation_generate.generate_presentation_async(
                request_http,
                request,
                BackgroundTasks(),
                idempotency_key="unprojected-failure",
                sql_session=_DialectOnlySession(),
            )
        assert caught.value.status_code == 500
        assert caught.value.detail == "Presentation generation failed"
        assert "private admission implementation detail" not in str(
            caught.value.detail
        )
        assert not caught.value.headers

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("status_code", "detail"),
    (
        (400, "invalid_generation_request"),
        (401, "not_authenticated"),
        (403, "generation_forbidden"),
        (404, "presentation_not_found"),
        (409, "idempotency_key_payload_conflict"),
        (409, "idempotency_record_incomplete"),
    ),
)
def test_generate_async_preserves_unprojected_http_failure_without_marker(
    monkeypatch,
    status_code: int,
    detail: str,
):
    async def fail_before_projection(*_args, **_kwargs):
        raise HTTPException(status_code=status_code, detail=detail)

    monkeypatch.setattr(
        presentation_generate,
        "queue_presentation_generation",
        fail_before_projection,
    )

    async def scenario():
        request_http = Request(
            {
                "type": "http",
                "http_version": "1.1",
                "method": "POST",
                "scheme": "http",
                "path": "/api/v1/ppt/presentation/generate/async",
                "raw_path": b"/api/v1/ppt/presentation/generate/async",
                "query_string": b"",
                "headers": [],
                "client": ("test", 1234),
                "server": ("test", 80),
            }
        )
        request = GeneratePresentationRequest(
            presentation_id=uuid.uuid4(),
            content="Fail before a workspace projection",
            n_slides=8,
            language="en",
        )
        with pytest.raises(HTTPException) as caught:
            await presentation_generate.generate_presentation_async(
                request_http,
                request,
                BackgroundTasks(),
                idempotency_key="unprojected-http-failure",
                sql_session=_DialectOnlySession(),
            )
        assert caught.value.status_code == status_code
        assert caught.value.detail == detail
        assert not caught.value.headers

    asyncio.run(scenario())


def test_chat_first_generate_rejects_unlinked_legacy_idempotency_replay(
    tmp_path: Path,
    monkeypatch,
):
    async def scenario():
        engine, maker = await _database(tmp_path / "unlinked-generation-replay.db")
        monkeypatch.setattr(
            presentation_crud,
            "_chat_first_create_enabled",
            lambda: True,
        )
        monkeypatch.setattr(
            presentation_generate,
            "_chat_first_generation_enabled",
            lambda: True,
        )
        presentation_id = uuid.uuid4()
        generate_key = f"presentation-generate:{presentation_id}"
        request = GeneratePresentationRequest(
            presentation_id=presentation_id,
            content="Build a durable strategy deck",
            n_slides=8,
            language="en",
            strategy="authored",
        )
        try:
            async with maker() as session:
                await _create(
                    session,
                    presentation_id=presentation_id,
                    idempotency_key=f"presentation-create:{presentation_id}",
                )
                task = AsyncPresentationGenerationTaskModel(
                    status="pending",
                    message="Legacy queue",
                )
                session.add(task)
                await session.flush()
                session.add(
                    PresentationGenerationJob(
                        task_id=task.id,
                        presentation_id=presentation_id,
                        workspace_operation_id=None,
                        idempotency_key_hash=(
                            presentation_generate.idempotency_key_hash(generate_key)
                        ),
                        request_sha256=presentation_generate.request_sha256(
                            presentation_generate._durable_request_payload(request)
                        ),
                        request_payload=(
                            presentation_generate._durable_request_payload(request)
                        ),
                        export_cookie_required=False,
                    )
                )
                await session.commit()

            async with maker() as session:
                with pytest.raises(HTTPException) as caught:
                    await presentation_generate.queue_presentation_generation(
                        request,
                        BackgroundTasks(),
                        session,
                        idempotency_key=generate_key,
                    )
                assert caught.value.status_code == 409
                assert (
                    caught.value.detail
                    == "chat_first_idempotency_record_unlinked"
                )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


async def _seed_running_operation(
    maker,
    *,
    kind="presentation.generate",
    request_payload=None,
):
    async with maker() as session, session.begin():
        presentation = PresentationModel(
            content="artifact publication",
            n_slides=2,
            language="en",
            lifecycle_status=PRESENTATION_LIFECYCLE_STAGING,
            revision=0,
        )
        # Every persisted presentation must carry a resolvable profile
        # identity; the server stamps it on creation, so the fixture uses
        # the same helper instead of hand-writing the columns.
        stamp_new_authored_presentation(presentation)
        session.add(presentation)
        await session.flush()
        conversation = WorkspaceConversation(presentation_id=presentation.id)
        session.add(conversation)
        await session.flush()
        operation = WorkspaceOperation(
            conversation_id=conversation.id,
            presentation_id=presentation.id,
            anchor_position=1,
            kind=kind,
            state="running",
            idempotency_key_hash="a" * 64,
            request_hash="b" * 64,
            request_payload=request_payload or {},
            expected_revision=0,
            attempt_token="attempt-1",
        )
        session.add(operation)
        await session.flush()
        task = AsyncPresentationGenerationTaskModel(
            status="processing",
            message="Publishing presentation",
        )
        session.add(task)
        await session.flush()
        session.add(
            PresentationGenerationJob(
                task_id=task.id,
                presentation_id=presentation.id,
                workspace_operation_id=operation.id,
                request_sha256="c" * 64,
                request_payload={},
                state="running",
                attempt_number=1,
                attempt_token="attempt-1",
            )
        )
    return presentation.id, operation.id


def test_artifact_publication_hashes_and_commits_one_revision(
    tmp_path: Path,
    monkeypatch,
):
    async def scenario():
        engine, maker = await _database(tmp_path / "artifact-success.db")
        app_data = tmp_path / "app-data"
        export = app_data / "exports" / "deck.pptx"
        export.parent.mkdir(parents=True)
        payload = b"immutable-presentation-bytes"
        export.write_bytes(payload)
        monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))
        try:
            presentation_id, operation_id = await _seed_running_operation(maker)
            async with maker() as prepare_session:
                artifact = await prepare_workspace_generation_artifact(
                    prepare_session,
                    source_path=str(export),
                    operation_id=operation_id,
                    presentation_id=presentation_id,
                    source_revision=1,
                )
            assert artifact.sha256 == sha256(payload).hexdigest()
            assert artifact.size_bytes == len(payload)
            async with maker() as session:
                revision = await publish_workspace_generation_artifact(
                    session,
                    artifact=artifact,
                    expected_revision=0,
                    attempt_token="attempt-1",
                )
            assert revision == 1

            async with maker() as session:
                presentation = await session.get(PresentationModel, presentation_id)
                operation = await session.get(WorkspaceOperation, operation_id)
                stored_artifact = await session.get(
                    WorkspaceArtifact,
                    artifact.artifact_id,
                )
                assert presentation is not None
                assert presentation.lifecycle_status == PRESENTATION_LIFECYCLE_PUBLISHED
                assert presentation.revision == 1
                assert operation is not None
                assert operation.state == "succeeded"
                assert operation.result_revision == 1
                assert operation.result == artifact.result_payload()
                assert stored_artifact is not None
                assert stored_artifact.status == "ready"
                assert artifact.absolute_path.read_bytes() == payload
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_template_application_publication_completes_same_target_with_source_metadata(
    tmp_path: Path,
    monkeypatch,
):
    async def scenario():
        engine, maker = await _database(tmp_path / "template-application-publish.db")
        app_data = tmp_path / "app-data-template-application"
        export = app_data / "exports" / "deck.pptx"
        export.parent.mkdir(parents=True)
        export.write_bytes(b"target-owned-template-application")
        monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))
        source_id = uuid.uuid4()
        try:
            presentation_id, operation_id = await _seed_running_operation(
                maker,
                kind="template.apply",
                request_payload={
                    "generation_settings": {
                        "source_presentation_id": str(source_id),
                        "source_revision": 7,
                        "authored_style": "signal",
                        "vision_qa": True,
                    }
                },
            )
            async with maker() as prepare_session:
                artifact = await prepare_workspace_generation_artifact(
                    prepare_session,
                    source_path=str(export),
                    operation_id=operation_id,
                    presentation_id=presentation_id,
                    source_revision=1,
                )
            async with maker() as session:
                revision = await publish_workspace_generation_artifact(
                    session,
                    artifact=artifact,
                    expected_revision=0,
                    attempt_token="attempt-1",
                )
            assert revision == 1

            async with maker() as session:
                target = await session.get(PresentationModel, presentation_id)
                operation = await session.get(WorkspaceOperation, operation_id)
            assert target is not None
            assert target.lifecycle_status == PRESENTATION_LIFECYCLE_PUBLISHED
            assert target.revision == 1
            assert operation is not None
            assert operation.presentation_id == presentation_id
            assert operation.result == {
                **artifact.result_payload(),
                "kind": "template.apply",
                "source_presentation_id": str(source_id),
                "source_revision": 7,
                "target_presentation_id": str(presentation_id),
                "presentation_id": str(presentation_id),
                "authored_style": "signal",
            }
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_failed_db_commit_compensates_exact_promoted_artifact(
    tmp_path: Path,
    monkeypatch,
):
    async def scenario():
        engine, maker = await _database(tmp_path / "artifact-compensation.db")
        app_data = tmp_path / "app-data"
        export = app_data / "exports" / "deck.pptx"
        export.parent.mkdir(parents=True)
        export.write_bytes(b"partial external write")
        monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))
        try:
            presentation_id, operation_id = await _seed_running_operation(maker)
            async with maker() as prepare_session:
                artifact = await prepare_workspace_generation_artifact(
                    prepare_session,
                    source_path=str(export),
                    operation_id=operation_id,
                    presentation_id=presentation_id,
                    source_revision=1,
                )
            async with maker() as session:
                async def fail_commit():
                    raise RuntimeError("simulated commit failure")

                monkeypatch.setattr(session, "commit", fail_commit)
                with pytest.raises(RuntimeError, match="simulated commit failure"):
                    await publish_workspace_generation_artifact(
                        session,
                        artifact=artifact,
                        expected_revision=0,
                        attempt_token="attempt-1",
                    )
            assert not artifact.absolute_path.exists()

            async with maker() as session:
                presentation = await session.get(PresentationModel, presentation_id)
                operation = await session.get(WorkspaceOperation, operation_id)
                assert presentation is not None
                assert presentation.lifecycle_status == PRESENTATION_LIFECYCLE_STAGING
                assert presentation.revision == 0
                assert operation is not None
                assert operation.state == "running"
                stored_artifact = await session.get(
                    WorkspaceArtifact,
                    artifact.artifact_id,
                )
                assert stored_artifact is not None
                assert stored_artifact.status == "deleted"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_stale_attempt_never_publishes_artifact(tmp_path: Path, monkeypatch):
    async def scenario():
        engine, maker = await _database(tmp_path / "artifact-stale.db")
        app_data = tmp_path / "app-data"
        export = app_data / "exports" / "deck.pptx"
        export.parent.mkdir(parents=True)
        export.write_bytes(b"stale attempt")
        monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))
        try:
            presentation_id, operation_id = await _seed_running_operation(maker)
            async with maker() as prepare_session:
                artifact = await prepare_workspace_generation_artifact(
                    prepare_session,
                    source_path=str(export),
                    operation_id=operation_id,
                    presentation_id=presentation_id,
                    source_revision=1,
                )
            async with maker() as session:
                with pytest.raises(WorkspaceArtifactPublicationError) as caught:
                    await publish_workspace_generation_artifact(
                        session,
                        artifact=artifact,
                        expected_revision=0,
                        attempt_token="stale-attempt",
                    )
                assert caught.value.code == "operation_lease_lost"
            assert not artifact.absolute_path.exists()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def _source_content_request(
    presentation_id: uuid.UUID, *, n_slides: int
) -> GeneratePresentationRequest:
    return GeneratePresentationRequest(
        presentation_id=presentation_id,
        content="Restyle the uploaded deck",
        n_slides=n_slides,
        language="en",
        template="authored",
        strategy="authored",
        authored_style="default",
        sourceMode="source_content_restyle",
        sourceContentManifestPin={
            "manifestId": "scm_source-deck",
            "digest": "sha256:" + "b" * 64,
        },
        contentPolicy={"text": "verbatim"},
    )


async def _admit_source_content_request_plan(
    monkeypatch, request: GeneratePresentationRequest
):
    observed: dict[str, object] = {}

    async def capture_admission(session, **kwargs):
        observed.update(kwargs)
        return SimpleNamespace(id=uuid.uuid4())

    monkeypatch.setattr(
        presentation_generate,
        "admit_public_operation_compatible_v2",
        capture_admission,
    )
    monkeypatch.setattr(
        presentation_generate,
        "current_principal_context",
        lambda: object(),
    )
    await presentation_generate._admit_generation_operation_v2_compatible(
        object(),
        request=request,
        presentation=SimpleNamespace(n_slides=28),
        operation=SimpleNamespace(id=uuid.uuid4()),
    )
    return observed["request_plan"]


def test_source_content_admission_charges_source_pages_not_slide_hint(monkeypatch):
    """A page-locked restyle authors one slide per source page.

    Admission sizes the immutable deadline from its count, so it must charge
    the manifest's page count rather than the caller's ``n_slides`` hint.
    """

    async def scenario():
        request = _source_content_request(uuid.uuid4(), n_slides=28)
        manifest_admissions: list[object] = []

        async def admit_manifest(candidate, *, sql_session, owner_id):
            manifest_admissions.append(candidate)
            candidate._source_document_outline = SimpleNamespace(
                source_slide_count=35
            )

        monkeypatch.setattr(
            public_generation_slide_count,
            "admit_source_content_request",
            admit_manifest,
        )

        first = await _admit_source_content_request_plan(monkeypatch, request)
        replay = await _admit_source_content_request_plan(monkeypatch, request)

        assert first.slide_count == 35
        # A replay reuses the frozen outline, so the count cannot drift.
        assert replay.slide_count == 35
        assert len(manifest_admissions) == 1

    asyncio.run(scenario())


def test_source_content_admission_defers_manifest_rejection_to_validation(
    monkeypatch,
):
    """An unadmittable manifest keeps the caller count so validation projects it."""

    async def scenario():
        request = _source_content_request(uuid.uuid4(), n_slides=28)

        async def reject_manifest(candidate, *, sql_session, owner_id):
            raise SourceContentError(
                "source_content_manifest_invalid", status_code=409
            )

        monkeypatch.setattr(
            public_generation_slide_count,
            "admit_source_content_request",
            reject_manifest,
        )

        plan = await _admit_source_content_request_plan(monkeypatch, request)

        assert plan.slide_count == 28
        assert request._source_document_outline is None

    asyncio.run(scenario())


def _public_policy_profile(version: str) -> PublicPolicyProfile:
    return PublicPolicyProfile(
        version=version,
        max_slides=100,
        global_active_generation=10,
        global_queued_generation=10,
        owner_active_generation=10,
        owner_queued_generation=10,
        tenant_active_generation=10,
        tenant_queued_generation=10,
        global_active_export=10,
        global_queued_export=10,
        owner_active_export=10,
        owner_queued_export=10,
        tenant_active_export=10,
        tenant_queued_export=10,
        global_active_resource_units=100,
        global_queued_resource_units=100,
        tenant_active_resource_units=100,
        tenant_queued_resource_units=100,
        owner_active_resource_units=100,
        owner_queued_resource_units=100,
        retry_request_limit=1,
        estimated_cost_limit_microunits=1,
        actual_cost_limit_microunits=1,
        generation_deadline_seconds=60,
        export_deadline_seconds=60,
    )


async def _seed_v2_operation_with_phase1_admission(
    maker,
    *,
    presentation_id: uuid.UUID,
    policy_version: str,
    now: datetime,
    running: bool,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    """Persist a v2-contract generation whose admission row never gained a fence."""

    async with maker() as session:
        await _create(
            session,
            presentation_id=presentation_id,
            idempotency_key=f"presentation-create:{presentation_id}",
        )
        operation = await session.scalar(
            select(WorkspaceOperation).where(
                WorkspaceOperation.presentation_id == presentation_id,
                WorkspaceOperation.kind == "presentation.generate",
            )
        )
        assert operation is not None
        operation.contract_version = "public-operation/v2"
        operation.execution_profile_version = "public-generation-export/v2"
        operation.deadline_at = now + timedelta(minutes=10)
        operation.fencing_token = 0
        if running:
            operation.state = "running"
            operation.phase = "active"
            operation.attempt_number = 1
            operation.attempt_token = "stuck-attempt"
            operation.lease_expires_at = now - timedelta(minutes=1)
            operation.heartbeat_at = now - timedelta(minutes=1)
        else:
            operation.state = "queued"
            operation.phase = "queued"
        task = AsyncPresentationGenerationTaskModel(
            status="processing" if running else "pending",
            message="stuck",
            owner_id=OWNER_A,
        )
        job = PresentationGenerationJob(
            task_id=task.id,
            presentation_id=presentation_id,
            workspace_operation_id=operation.id,
            request_sha256="a" * 64,
            request_payload={"content": "stuck"},
            state="running" if running else "queued",
            resume_allowed=True,
        )
        operation.request_payload = {"job_id": str(job.id)}
        admission = PublicOperationAdmission(
            operation_id=operation.id,
            policy_version=policy_version,
            owner_id=OWNER_A,
            tenant_id="default",
            trace_id_hash="b" * 64,
            operation_kind="generation",
            resource_units=1,
            policy_digest="c" * 64,
            deadline_at=now + timedelta(minutes=10),
            fence=0,
            state="active" if running else "queued",
            planned_request_limit=1,
            retry_request_limit=1,
            estimated_cost_limit_microunits=1,
            actual_cost_limit_microunits=1,
        )
        counter_kwargs = (
            {"active_count": 1, "active_resource_units": 1}
            if running
            else {"queued_count": 1, "queued_resource_units": 1}
        )
        counters = [
            PublicQuotaCounter(
                policy_version=policy_version,
                scope_type=scope,
                scope_key=key,
                operation_kind="generation",
                **counter_kwargs,
            )
            # An admission reserves one slot in every scope, global included.
            for scope, key in (
                ("global", "global"),
                ("tenant", "default"),
                ("owner", str(OWNER_A)),
            )
        ]
        session.add(_public_policy_profile(policy_version))
        await session.flush()
        session.add_all([task, job, admission, *counters])
        await session.commit()
        return operation.id, admission.id, job.id, task.id


async def _assert_invalid_admission_terminalized(
    maker,
    *,
    operation_id: uuid.UUID,
    admission_id: uuid.UUID,
    job_id: uuid.UUID,
    task_id: uuid.UUID,
    policy_version: str,
) -> None:
    async with maker() as session:
        operation = await session.get(WorkspaceOperation, operation_id)
        admission = await session.get(PublicOperationAdmission, admission_id)
        job = await session.get(PresentationGenerationJob, job_id)
        task = await session.get(AsyncPresentationGenerationTaskModel, task_id)
        assert operation.state == "failed"
        assert operation.error_code == "public_phase2_admission_invalid"
        assert admission.state == "failed"
        assert job.state == "failed"
        assert job.resume_allowed is False
        assert task.status == "error"
        assert task.error["code"] == "public_phase2_admission_invalid"
        counters = (
            await session.execute(
                select(PublicQuotaCounter).where(
                    PublicQuotaCounter.policy_version == policy_version
                )
            )
        ).scalars()
        assert all(
            row.active_count == 0 and row.queued_count == 0 for row in counters
        )


def test_claim_terminalizes_v2_operation_with_invalid_admission(
    tmp_path: Path,
    monkeypatch,
):
    """A fence-less admission row must fail its operation, not pin the queue head."""

    async def scenario():
        engine, maker = await _database(tmp_path / "invalid-admission-claim.db")
        async with maker() as session, session.begin():
            session.add(User(id=OWNER_A, username="owner-a", hashed_password="x"))
        owner_token = set_current_owner_id(OWNER_A)
        # The deadline check constraint compares against the real created_at.
        now = datetime.now(timezone.utc)
        policy_version = "invalid-admission-claim/v1"
        try:
            monkeypatch.setattr(
                presentation_crud, "_chat_first_create_enabled", lambda: True
            )
            ids = await _seed_v2_operation_with_phase1_admission(
                maker,
                presentation_id=uuid.uuid4(),
                policy_version=policy_version,
                now=now,
                running=False,
            )

            claim = await workspace_operation_runner.claim_next_workspace_operation(
                session_factory=maker
            )

            assert claim is None
            operation_id, admission_id, job_id, task_id = ids
            await _assert_invalid_admission_terminalized(
                maker,
                operation_id=operation_id,
                admission_id=admission_id,
                job_id=job_id,
                task_id=task_id,
                policy_version=policy_version,
            )
            # The row is terminal, so the next poll no longer trips over it.
            assert (
                await workspace_operation_runner.claim_next_workspace_operation(
                    session_factory=maker
                )
                is None
            )
        finally:
            reset_current_owner_id(owner_token)
            await engine.dispose()

    asyncio.run(scenario())


def test_recovery_terminalizes_v2_operation_with_invalid_admission(
    tmp_path: Path,
    monkeypatch,
):
    """Recovery must fail such a row instead of aborting the whole sweep."""

    async def scenario():
        engine, maker = await _database(tmp_path / "invalid-admission-recovery.db")
        async with maker() as session, session.begin():
            session.add(User(id=OWNER_A, username="owner-a", hashed_password="x"))
        owner_token = set_current_owner_id(OWNER_A)
        # The deadline check constraint compares against the real created_at.
        now = datetime.now(timezone.utc)
        policy_version = "invalid-admission-recovery/v1"
        try:
            monkeypatch.setattr(
                presentation_crud, "_chat_first_create_enabled", lambda: True
            )
            ids = await _seed_v2_operation_with_phase1_admission(
                maker,
                presentation_id=uuid.uuid4(),
                policy_version=policy_version,
                now=now,
                running=True,
            )

            recovered = await workspace_operation_runner.recover_expired_workspace_operations(
                session_factory=maker
            )

            assert recovered == 1
            operation_id, admission_id, job_id, task_id = ids
            await _assert_invalid_admission_terminalized(
                maker,
                operation_id=operation_id,
                admission_id=admission_id,
                job_id=job_id,
                task_id=task_id,
                policy_version=policy_version,
            )
        finally:
            reset_current_owner_id(owner_token)
            await engine.dispose()

    asyncio.run(scenario())
