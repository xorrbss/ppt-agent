"""Handlers run as the operation's durable principal: owner and tenant."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path
import uuid

import pytest
from starlette.requests import Request
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

import test_workspace_operation_runner as runner_test
import test_workspace_service as service_test
from models.sql.public_admission import PublicOperationAdmission
from models.sql.workspace import WorkspaceOperation
from models.workspace import WorkspaceCommandRequest
from services.workspace_error_definitions import WORKSPACE_ERROR_REGISTRY
from services.workspace_operation_handler_registry import (
    WorkspaceOperationClaim,
    WorkspaceOperationHandlerRegistry,
    WorkspaceOperationResult,
)
from services.workspace_operation_principal import (
    PRINCIPAL_CONFLICT_CODE,
    PRINCIPAL_TENANT_PAYLOAD_KEY,
    resolve_operation_tenant,
)
from services.workspace_operation_runner import WorkspaceOperationRunner
from services.workspace_service import create_workspace_command
from utils.datetime_utils import get_current_utc_datetime
from utils.owner_context import (
    get_current_owner_id,
    get_current_tenant_id,
    reset_current_tenant_id,
    set_current_tenant_id,
    reset_current_owner_id,
    set_current_owner_id,
)

OWNER = runner_test.OWNER_ID


@pytest.mark.parametrize("kind", ["generation", "export"])
@pytest.mark.parametrize("current,captured,admitted,bridge,other_owner,allowed", [
    ("tenant-a", "tenant-a", "tenant-a", False, False, True),
    ("tenant-a", "tenant-b", "tenant-b", False, False, False),
    ("default", "local-development", "local-development", True, False, True),
    ("default", None, "local-development", True, False, True),
    ("default", "local-development", "local-development", False, False, False),
    ("default", "tenant-b", "tenant-b", True, False, False),
    ("default", "local-development", "tenant-b", True, False, False),
    ("default", "local-development", "local-development", True, True, False),
])
def test_export_tenant_handoff_requires_bound_owner_and_exact_admission(
    tmp_path, monkeypatch, kind, current, captured, admitted, bridge, other_owner, allowed,
):
    from types import SimpleNamespace
    from services.workspace_operation_principal import (
        bound_export_operation_principal, presentation_export_principal,
    )
    from services.workspace_operation_handler_registry import NonRetryableWorkspaceOperationError

    monkeypatch.setattr("utils.env_settings.public_service.get_public_process_identity_settings",
                        lambda: SimpleNamespace(service_tenant_id="default"))

    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'export-principal.db'}")
        async with engine.begin() as connection:
            await connection.run_sync(lambda sync: SQLModel.metadata.create_all(sync, tables=runner_test.RUNNER_TABLES))
        maker = async_sessionmaker(engine, expire_on_commit=False)
        try:
            presentation_id, (operation_id,) = await runner_test._seed_operations(
                maker, owner_id=OWNER, kind="presentation.generate" if kind == "generation" else "export.pptx")
            async with maker() as session, session.begin():
                await session.execute(update(WorkspaceOperation).where(WorkspaceOperation.id == operation_id).values(
                    state="succeeded", request_payload={PRINCIPAL_TENANT_PAYLOAD_KEY: captured} if captured else {}))
                session.add(PublicOperationAdmission(
                    operation_id=operation_id, policy_version="public-test/v1", owner_id=OWNER,
                    tenant_id=admitted, trace_id_hash="3" * 64, operation_kind=kind, state="succeeded",
                    resource_units=5, policy_digest="4" * 64,
                    deadline_at=get_current_utc_datetime() + timedelta(minutes=5),
                    planned_request_limit=0, retry_request_limit=0,
                    estimated_cost_limit_microunits=0, actual_cost_limit_microunits=0))
            owner_token = set_current_owner_id(uuid.uuid4() if other_owner else OWNER)
            tenant_token = set_current_tenant_id(current)
            try:
                async with maker() as session:
                    scope = (presentation_export_principal(session, presentation_id=presentation_id,
                                                           allow_local_development=bridge)
                             if kind == "generation" else bound_export_operation_principal(
                                 session, operation_id=operation_id, allow_local_development=bridge))
                    if allowed:
                        with pytest.raises(RuntimeError, match="scoped body failure"):
                            async with scope:
                                assert get_current_tenant_id() == admitted
                                assert get_current_owner_id() == OWNER
                                raise RuntimeError("scoped body failure")
                    else:
                        with pytest.raises(NonRetryableWorkspaceOperationError, match=PRINCIPAL_CONFLICT_CODE):
                            async with scope:
                                raise AssertionError("unbound export principal was allowed")
                assert get_current_tenant_id() == current
            finally:
                reset_current_tenant_id(tenant_token)
                reset_current_owner_id(owner_token)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("issuer,method,admin,peer,host,environment,other_owner,allowed", [
    ("presenton-loopback-development", "jwt", True, "127.0.0.1", "localhost", "development", False, True),
    ("presenton-local-session", "jwt", True, "127.0.0.1", "localhost", "development", False, False),
    ("presenton-loopback-development", "oidc", True, "127.0.0.1", "localhost", "development", False, False),
    ("presenton-loopback-development", "jwt", False, "127.0.0.1", "localhost", "development", False, False),
    ("presenton-loopback-development", "jwt", True, "203.0.113.1", "localhost", "development", False, False),
    ("presenton-loopback-development", "jwt", True, "127.0.0.1", "example.com", "development", False, False),
    ("presenton-loopback-development", "jwt", True, "127.0.0.1", "localhost", "production", False, False),
    ("presenton-loopback-development", "jwt", True, "127.0.0.1", "localhost", "development", True, False),
])
def test_local_export_bridge_uses_selected_authentication_not_forwarded_headers(
    monkeypatch, issuer, method, admin, peer, host, environment, other_owner, allowed,
):
    from api.v1.auth.principal import PrincipalEnvelope
    from services.workspace_operation_principal import local_development_export_bridge

    monkeypatch.setattr("api.v1.auth.principal.get_environment_name", lambda *_: environment)
    request = Request({"type": "http", "scheme": "http", "method": "GET", "path": "/api/v1/ppt/workspace",
                       "client": (peer, 1234), "server": (host, 8000),
                       "headers": [(b"host", host.encode()), (b"x-forwarded-for", b"127.0.0.1")]})
    request.state.auth_principal = PrincipalEnvelope(
        user_id=OWNER, username="test", is_admin=admin, method=method, tenant_id="default",
        trace_id="test", subject=str(OWNER), issuer=issuer, audience="presenton-api")
    token = set_current_owner_id(uuid.uuid4() if other_owner else OWNER)
    try:
        assert local_development_export_bridge(request) is allowed
    finally:
        reset_current_owner_id(token)


@pytest.mark.parametrize("command_type", [
    "export.pptx", "presentation.slides.reorder", "version.restore",
    "presentation.edit", "slide.edit", "slide.delete",
])
@pytest.mark.parametrize("current,captured,admitted,bridge,other_owner,mode,allowed", [
    ("local-development", "local-development", "local-development", "default", False, "svg", True),
    ("default", "local-development", "local-development", "loopback", False, "svg", True),
    ("default", None, "local-development", "loopback", False, "svg", True),
    ("default", "local-development", "local-development", "default", False, "svg", False),
    ("default", "local-development", "local-development", "session", False, "svg", False),
    ("default", "local-development", "local-development", "remote", False, "svg", False),
    ("default", "local-development", "local-development", "production", False, "svg", False),
    ("default", "local-development", "local-development", "nonadmin", False, "svg", False),
    ("default", "tenant-b", "tenant-b", "loopback", False, "svg", False),
    ("default", "local-development", "tenant-b", "loopback", False, "svg", False),
    ("default", "local-development", "local-development", "loopback", True, "svg", False),
    ("tenant-a", "local-development", "local-development", "loopback", False, "svg", False),
    ("tenant-a", "tenant-a", None, "default", False, "svg", True),
    ("tenant-a", None, None, "default", False, "svg", True),
    ("default", "local-development", "local-development", "default", False, "authored", False),
    ("default", "local-development", "local-development", "loopback", False, "authored", True),
    ("default", "local-development", "local-development", "default", False, "theme-svg", False),
    ("default", "local-development", "local-development", "loopback", False, "theme-svg", True),
])
def test_ui_export_admission_and_worker_payload_share_generation_tenant(
    tmp_path, monkeypatch, command_type, current, captured, admitted, bridge, other_owner, mode, allowed,
):
    from types import SimpleNamespace
    from sqlalchemy import select
    from api.v1.auth.principal import PrincipalEnvelope
    from models.sql.presentation import PresentationModel
    from models.sql.user import User
    from models.workspace import WorkspaceOperationActionRequest
    from services.workspace_operation_principal import local_development_export_bridge
    from services.workspace_service import WorkspaceServiceError, apply_workspace_action

    monkeypatch.setattr("utils.env_settings.public_service.get_public_process_identity_settings",
                        lambda: SimpleNamespace(service_tenant_id="default"))
    monkeypatch.setattr("api.v1.auth.principal.get_environment_name",
                        lambda *_: "production" if bridge == "production" else "development")
    seen = []

    async def admit(_session, **kwargs):
        seen.append((kwargs["principal"].tenant_id, get_current_tenant_id(), kwargs["operation_id"]))
        return object()

    monkeypatch.setattr("services.workspace_service.admit_public_operation", admit)

    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'ui-export-principal.db'}")
        tables = list(dict.fromkeys([*runner_test.RUNNER_TABLES, *service_test.WORKSPACE_SERVICE_TABLES]))
        async with engine.begin() as connection:
            await connection.run_sync(lambda sync: SQLModel.metadata.create_all(sync, tables=tables))
        maker = async_sessionmaker(engine, expire_on_commit=False)
        try:
            presentation_id, (source_id, failed_id) = await runner_test._seed_operations(
                maker, count=2, owner_id=OWNER, kind="presentation.generate")
            active_owner = uuid.uuid4() if other_owner else OWNER
            failed_payload = {PRINCIPAL_TENANT_PAYLOAD_KEY: (
                captured if mode == "authored" and captured else "default"), "command_type": command_type}
            failed_payload["context"] = {"tenant_id": failed_payload[PRINCIPAL_TENANT_PAYLOAD_KEY]}
            async with maker() as session, session.begin():
                if other_owner:
                    session.add(User(id=active_owner, username=f"other-{active_owner}", hashed_password="test-only"))
                await session.execute(update(PresentationModel).where(PresentationModel.id == presentation_id).values(
                    mode=None if mode == "theme-svg" else mode,
                    theme={"mode": "svg" if mode == "theme-svg" else mode}))
                await session.execute(update(WorkspaceOperation).where(WorkspaceOperation.id == source_id).values(
                    state="succeeded", request_payload={PRINCIPAL_TENANT_PAYLOAD_KEY: captured} if captured else {}))
                await session.execute(update(WorkspaceOperation).where(WorkspaceOperation.id == failed_id).values(
                    kind=command_type, state="failed", request_payload=failed_payload, expected_revision=0,
                    error_detail={"retryable": True}))
                if admitted is not None:
                    session.add(PublicOperationAdmission(
                        operation_id=source_id, policy_version="public-test/v1", owner_id=OWNER,
                        tenant_id=admitted, trace_id_hash="3" * 64, operation_kind="generation", state="succeeded",
                        resource_units=5, policy_digest="4" * 64,
                        deadline_at=get_current_utc_datetime() + timedelta(minutes=5),
                        planned_request_limit=0, retry_request_limit=0,
                        estimated_cost_limit_microunits=0, actual_cost_limit_microunits=0))
            owner_token = set_current_owner_id(active_owner)
            tenant_token = set_current_tenant_id(current)
            try:
                bridge_kwargs = {}
                if bridge != "default":
                    request = Request({"type": "http", "scheme": "http", "method": "POST", "path": "/api/v1/ppt/workspace",
                                       "client": ("203.0.113.1" if bridge == "remote" else "127.0.0.1", 1234),
                                       "server": ("localhost", 8000),
                                       "headers": [(b"host", b"localhost"), (b"x-forwarded-for", b"127.0.0.1")]})
                    request.state.auth_principal = PrincipalEnvelope(
                        user_id=OWNER, username="test", is_admin=bridge != "nonadmin", method="jwt", tenant_id=current,
                        trace_id="test", subject=str(OWNER), audience="presenton-api",
                        issuer="presenton-local-session" if bridge == "session" else "presenton-loopback-development")
                    bridge_kwargs["allow_local_development"] = local_development_export_bridge(request)
                protected = command_type == "export.pptx" or mode != "authored"
                permitted = allowed if protected else not other_owner
                expected_tenant = (admitted or captured or current) if protected else current
                command = WorkspaceCommandRequest(command="Update", command_type=command_type,
                                                   idempotency_key="ui-source-tenant", expected_revision=0,
                                                   context={"tenant_id": current})
                action = WorkspaceOperationActionRequest(action="retry", idempotency_key="ui-source-retry",
                                                          expected_operation_version=0, expected_revision=0)
                if permitted:
                    async with maker() as session:
                        operation, duplicate = await create_workspace_command(
                            session, presentation_id=presentation_id, request=command, **bridge_kwargs)
                    assert not duplicate
                    assert operation.request_payload[PRINCIPAL_TENANT_PAYLOAD_KEY] == expected_tenant
                    assert operation.request_payload["context"]["tenant_id"] == expected_tenant
                    async with maker() as session:
                        _, retried, replayed = await apply_workspace_action(
                            session, operation_id=failed_id, request=action, **bridge_kwargs)
                    assert not replayed and retried.retry_of_operation_id == failed_id
                    assert retried.request_payload[PRINCIPAL_TENANT_PAYLOAD_KEY] == (
                        expected_tenant if protected else failed_payload[PRINCIPAL_TENANT_PAYLOAD_KEY])
                    assert retried.request_payload["context"]["tenant_id"] == (
                        expected_tenant if protected else failed_payload["context"]["tenant_id"])
                    expected_seen = ([(expected_tenant, expected_tenant, operation.id),
                                      (expected_tenant, expected_tenant, retried.id)]
                                     if command_type == "export.pptx" else [])
                    assert seen == expected_seen
                else:
                    for invoke in (
                        lambda session: create_workspace_command(
                            session, presentation_id=presentation_id, request=command, **bridge_kwargs),
                        lambda session: apply_workspace_action(
                            session, operation_id=failed_id, request=action, **bridge_kwargs),
                    ):
                        async with maker() as session:
                            with pytest.raises(WorkspaceServiceError) as denied:
                                await invoke(session)
                            assert denied.value.status_code in {403, 404}
                    assert not seen
                assert get_current_tenant_id() == current
                assert get_current_owner_id() == active_owner
                async with maker() as session:
                    original = await session.scalar(select(WorkspaceOperation).where(
                        WorkspaceOperation.id == failed_id).execution_options(skip_owner_scope=True))
                    assert original.request_payload == failed_payload
            finally:
                reset_current_tenant_id(tenant_token)
                reset_current_owner_id(owner_token)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


async def _set_payload(maker, operation_id: uuid.UUID, payload: dict) -> None:
    async with maker() as session, session.begin():
        await session.execute(
            update(WorkspaceOperation)
            .where(WorkspaceOperation.id == operation_id)
            .values(request_payload=payload)
            .execution_options(skip_owner_scope=True)
        )


def _runner(maker, handler) -> WorkspaceOperationRunner:
    registry = WorkspaceOperationHandlerRegistry()
    registry.register("test.operation", handler)
    return WorkspaceOperationRunner(
        session_factory=maker, registry=registry, max_concurrency=1, heartbeat_seconds=60,
    )


def test_handler_runs_as_captured_principal_without_leaking_between_claims(tmp_path: Path) -> None:
    async def scenario() -> None:
        engine, maker = await runner_test._database(tmp_path / "principal.db")
        try:
            _, (captured, legacy) = await runner_test._seed_operations(maker, count=2, owner_id=OWNER)
            await _set_payload(maker, captured, {PRINCIPAL_TENANT_PAYLOAD_KEY: "tenant-a"})
            await _set_payload(maker, legacy, {"ordinal": 1})  # submitted before the capture
            # Keyed by operation, not claim order: both share one owner and are
            # seeded in the same instant, so the fair-order query's final
            # ``created_at, id`` tiebreak may claim either one first.
            seen: dict[uuid.UUID, tuple[uuid.UUID | None, str | None]] = {}

            async def handler(context):
                seen[context.claim.operation_id] = (get_current_owner_id(), get_current_tenant_id())
                return WorkspaceOperationResult(payload={"status": "completed"})

            runner = _runner(maker, handler)
            assert await runner_test._drain_once(runner) == 1
            assert await runner_test._drain_once(runner) == 1
            # legacy: owner only, no invented tenant
            assert seen == {captured: (OWNER, "tenant-a"), legacy: (OWNER, None)}
            # The runner's own task never carried the handler's principal.
            assert get_current_tenant_id() is None and get_current_owner_id() is None
            for operation_id in (captured, legacy):
                assert (await runner_test._operation(maker, operation_id)).state == "succeeded"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_conflicting_tenant_sources_fail_with_the_registered_code(tmp_path: Path) -> None:
    async def scenario() -> None:
        engine, maker = await runner_test._database(tmp_path / "conflict.db")
        try:
            _, (operation_id,) = await runner_test._seed_operations(maker, owner_id=OWNER)
            await _set_payload(maker, operation_id, {
                PRINCIPAL_TENANT_PAYLOAD_KEY: "tenant-a", "context": {"tenant_id": "tenant-b"},
            })
            called: list[bool] = []

            async def handler(_context):
                called.append(True)

            assert await runner_test._drain_once(_runner(maker, handler)) == 1
            operation = await runner_test._operation(maker, operation_id)
            assert not called
            assert operation.state == "failed"
            assert operation.error_code == PRINCIPAL_CONFLICT_CODE
            assert operation.error_detail["retryable"] is False
        finally:
            await engine.dispose()

    asyncio.run(scenario())
    registered = WORKSPACE_ERROR_REGISTRY[PRINCIPAL_CONFLICT_CODE]
    assert registered.retryable is False and registered.status_code == 403


def test_disable_auth_operation_runs_without_a_principal(tmp_path: Path) -> None:
    async def scenario() -> None:
        engine, maker = await runner_test._database(tmp_path / "anonymous.db")
        try:
            _, (operation_id,) = await runner_test._seed_operations(maker)  # owner_id None
            seen: list[tuple[uuid.UUID | None, str | None]] = []

            async def handler(_context):
                seen.append((get_current_owner_id(), get_current_tenant_id()))

            assert await runner_test._drain_once(_runner(maker, handler)) == 1
            assert seen == [(None, None)]
            assert (await runner_test._operation(maker, operation_id)).state == "succeeded"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def _claim(operation_id: uuid.UUID, payload: dict) -> WorkspaceOperationClaim:
    return WorkspaceOperationClaim(
        operation_id=operation_id, owner_id=OWNER, owner_scope="owner", conversation_id=uuid.uuid4(),
        presentation_id=uuid.uuid4(), kind="presentation.generate", request_payload=payload,
        expected_revision=0, attempt_number=1, attempt_token="attempt", fencing_token=1,
    )


def test_admitted_operation_takes_the_admission_tenant(tmp_path: Path) -> None:
    async def scenario() -> None:
        # Without the FK pragma: the admission's policy-profile parent is not under test.
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'admitted.db'}")
        async with engine.begin() as connection:
            await connection.run_sync(lambda sync: SQLModel.metadata.create_all(
                sync, tables=runner_test.RUNNER_TABLES))
        maker = async_sessionmaker(engine, expire_on_commit=False)
        try:
            _, (operation_id,) = await runner_test._seed_operations(maker, owner_id=OWNER)
            async with maker() as session, session.begin():
                session.add(PublicOperationAdmission(
                    operation_id=operation_id, policy_version="public-test/v1", owner_id=OWNER,
                    tenant_id="local-development", trace_id_hash="3" * 64, operation_kind="generation",
                    resource_units=5, policy_digest="4" * 64,
                    deadline_at=get_current_utc_datetime() + timedelta(minutes=5),
                    planned_request_limit=0, retry_request_limit=0,
                    estimated_cost_limit_microunits=0, actual_cost_limit_microunits=0,
                ))
            # A generation request payload carries no tenant: the admission is the authority.
            generation_payload = {"generation_request": {"content": "x"}}
            assert await resolve_operation_tenant(
                _claim(operation_id, generation_payload), session_factory=maker,
            ) == "local-development"
            # Agreeing sources are fine; a disagreeing one is a conflict.
            assert await resolve_operation_tenant(
                _claim(operation_id, {PRINCIPAL_TENANT_PAYLOAD_KEY: "local-development"}), session_factory=maker,
            ) == "local-development"
            try:
                await resolve_operation_tenant(
                    _claim(operation_id, {PRINCIPAL_TENANT_PAYLOAD_KEY: "other"}), session_factory=maker,
                )
            except Exception as error:  # noqa: BLE001 - asserting the exact typed code below
                assert getattr(error, "code", None) == PRINCIPAL_CONFLICT_CODE
            else:
                raise AssertionError("conflicting tenants were accepted")
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_submission_captures_the_tenant_outside_the_idempotency_hash(tmp_path: Path) -> None:
    async def scenario() -> None:
        engine, maker = await service_test._database(tmp_path / "capture.db")
        try:
            presentation = await service_test._create_presentation(maker)
            request = WorkspaceCommandRequest(
                command="Tighten the summary", command_type="presentation.edit",
                idempotency_key="principal-capture", expected_revision=0,
            )
            token = set_current_tenant_id("tenant-a")
            try:
                async with maker() as session:
                    operation, duplicate = await create_workspace_command(
                        session, presentation_id=presentation.id, request=request)
                    await session.commit()
                async with maker() as session:
                    replay, replay_duplicate = await create_workspace_command(
                        session, presentation_id=presentation.id, request=request)
            finally:
                reset_current_tenant_id(token)
            assert duplicate is False and replay_duplicate is True
            assert replay.id == operation.id
            assert operation.request_payload[PRINCIPAL_TENANT_PAYLOAD_KEY] == "tenant-a"
            # The request hash is the command's, unchanged by the captured principal.
            from services.workspace_service import canonical_payload_hash

            assert operation.request_hash == canonical_payload_hash({
                "presentation_id": str(presentation.id), "command": request.command,
                "command_type": request.command_type, "context": request.context,
                "expected_revision": request.expected_revision, "asset_ids": [],
            })

            # Without a principal (DISABLE_AUTH) nothing is captured.
            async with maker() as session:
                anonymous, _ = await create_workspace_command(
                    session, presentation_id=presentation.id,
                    request=request.model_copy(update={"idempotency_key": "principal-anonymous"}))
            assert PRINCIPAL_TENANT_PAYLOAD_KEY not in anonymous.request_payload
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_bootstrap_turn_captures_the_tenant_outside_the_idempotency_hash(tmp_path: Path) -> None:
    from services.workspace_service import bootstrap_workspace

    async def scenario() -> None:
        engine, maker = await service_test._database(tmp_path / "bootstrap.db")
        try:
            presentation = await service_test._create_presentation(maker)
            request = service_test._bootstrap_request(key="principal-bootstrap")
            token = set_current_tenant_id("tenant-b")
            try:
                async with maker() as session:
                    _, _, operation, duplicate = await bootstrap_workspace(
                        session, presentation_id=presentation.id, request=request)
                async with maker() as session:
                    _, _, replay, replay_duplicate = await bootstrap_workspace(
                        session, presentation_id=presentation.id, request=request)
            finally:
                reset_current_tenant_id(token)
            assert duplicate is False and replay_duplicate is True
            assert replay.id == operation.id and replay.request_hash == operation.request_hash
            assert operation.request_payload[PRINCIPAL_TENANT_PAYLOAD_KEY] == "tenant-b"
        finally:
            await engine.dispose()

    asyncio.run(scenario())
