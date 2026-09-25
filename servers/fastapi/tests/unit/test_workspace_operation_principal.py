"""Handlers run as the operation's durable principal: owner and tenant."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path
import uuid

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
)

OWNER = runner_test.OWNER_ID


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
            seen: list[tuple[uuid.UUID | None, str | None]] = []

            async def handler(_context):
                seen.append((get_current_owner_id(), get_current_tenant_id()))
                return WorkspaceOperationResult(payload={"status": "completed"})

            runner = _runner(maker, handler)
            assert await runner_test._drain_once(runner) == 1
            assert await runner_test._drain_once(runner) == 1
            assert seen == [(OWNER, "tenant-a"), (OWNER, None)]  # legacy: owner only, no invented tenant
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
