"""Authored-removal P2 (option A, 2026-11-10): existing authored decks.

The authored driver is no longer registered, so an authored deck written
before the removal behaves like a removed Template V2 deck: it is listed as
unsupported, every read or action on its content answers 422
``unsupported_presentation_driver``, and deleting it keeps working. An svg
deck, which shares the ``style_source="authored"`` literal, is unaffected.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

import services.database  # noqa: F401 - registers the full model graph
import test_workspace_operation_runner as runner_test
from api.v1.ppt.endpoints import presentation_crud, presentation_versions
from api.v1.ppt.endpoints import authored_editor_events, outlines, presentation_page_operations
from models.authored_slide_editor_event import AuthoredSlideEditorEventRequest
from models.presentation_profiles import (
    PRESENTATION_DRIVER_REGISTRY,
    PresentationProfileError,
    resolve_presentation_driver,
    resolve_presentation_profile,
)
from models.sql.presentation import PresentationModel
from models.workspace import (
    WorkspaceBootstrapRequest,
    WorkspaceCommandRequest,
    WorkspaceOperationActionRequest,
)
from models.workspace_operation_kinds import WORKSPACE_COMMAND_KINDS
from services.presentation_access_service import (
    has_unsupported_presentation_driver,
    require_supported_presentation_driver,
)
from services.workspace_error_registry import resolve_workspace_error
from services.workspace_operation_handler_registry import (
    WorkspaceOperationHandlerRegistry,
    WorkspaceOperationResult,
)
from services.workspace_operation_runner import WorkspaceOperationRunner
from services.workspace_service import (
    WorkspaceServiceError,
    apply_workspace_action,
    bootstrap_workspace,
    create_workspace_command,
)
from utils.owner_context import reset_current_owner_id, set_current_owner_id

OWNER_ID = runner_test.OWNER_ID


def _authored_identity(**extra):
    return {
        "generation_mode": "standard",
        "mode": "authored",
        "version": "v1-standard",
        "theme": {"mode": "authored"},
        **extra,
    }


@pytest.mark.parametrize(
    "identity",
    [
        _authored_identity(),
        _authored_identity(profile_driver_id="authored", profile_schema_version=1),
        {"generation_mode": "standard", "mode": None, "theme": {"mode": "authored"}},
    ],
)
def test_an_authored_identity_resolves_to_unsupported_presentation_driver(identity):
    assert "authored" not in PRESENTATION_DRIVER_REGISTRY
    with pytest.raises(PresentationProfileError) as error:
        resolve_presentation_profile(identity)
    assert error.value.code == "unsupported_presentation_driver"


def test_an_svg_deck_still_resolves_with_its_persisted_style_source():
    profile, capabilities, driver = resolve_presentation_driver(
        {"generation_mode": "standard", "mode": "svg", "version": "v1-standard",
         "theme": {"mode": "svg"}, "profile_driver_id": "svg-v1", "profile_schema_version": 1}
    )
    assert profile.driver_id == driver.driver_id == capabilities.driver_id == "svg-v1"
    assert profile.style_source == "authored"
    svg = PresentationModel(content="svg", n_slides=1, language="en", mode="svg", theme={"mode": "svg"})
    assert has_unsupported_presentation_driver(svg) is False
    require_supported_presentation_driver(svg)


def test_the_workspace_error_contract_names_the_code():
    definition = resolve_workspace_error("unsupported_presentation_driver")
    assert definition.code == "unsupported_presentation_driver"
    assert definition.status_code == 422
    assert definition.retryable is False


async def _authored_workspace(tmp_path: Path, *, kind: str = "export.pptx"):
    """An svg-seeded workspace whose deck is then rewritten as a pre-existing
    authored row (Core update: the ORM write boundary refuses authored)."""

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'authored-removal.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    presentation_id, operation_ids = await runner_test._seed_operations(
        maker, count=1, owner_id=OWNER_ID, kind=kind, revision=0, expected_revision=0,
    )
    owner_token = set_current_owner_id(OWNER_ID)
    try:
        async with maker() as session:
            await session.execute(
                update(PresentationModel)
                .where(PresentationModel.id == presentation_id)
                .values(mode="authored", theme={"mode": "authored"}, profile_driver_id="authored")
            )
            await session.commit()
    finally:
        reset_current_owner_id(owner_token)
    return engine, maker, presentation_id, operation_ids


def test_an_operation_queued_before_the_removal_fails_without_running(tmp_path: Path):
    async def scenario():
        engine, maker, _presentation_id, operation_ids = await _authored_workspace(tmp_path)
        try:
            calls = []

            async def handler(_context):
                calls.append(True)
                return WorkspaceOperationResult(payload={"status": "completed"})

            registry = WorkspaceOperationHandlerRegistry()
            registry.register("export.pptx", handler)
            runner = WorkspaceOperationRunner(
                session_factory=maker, registry=registry, max_concurrency=1, heartbeat_seconds=60,
            )
            assert await runner_test._drain_once(runner) == 1
            operation = await runner_test._operation(maker, operation_ids[0])
            assert operation.state == "failed"
            assert operation.error_code == "unsupported_presentation_driver"
            assert calls == []
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_new_work_is_refused_but_deletion_and_cancel_remain(tmp_path: Path):
    async def scenario():
        engine, maker, presentation_id, operation_ids = await _authored_workspace(tmp_path)
        owner_token = set_current_owner_id(OWNER_ID)
        try:
            for kind in WORKSPACE_COMMAND_KINDS:
                if kind == "presentation.delete":
                    continue
                async with maker() as session:
                    with pytest.raises(WorkspaceServiceError) as refused:
                        await create_workspace_command(
                            session,
                            presentation_id=presentation_id,
                            request=WorkspaceCommandRequest(
                                command="change it", command_type=kind,
                                idempotency_key=f"refused-{kind}",
                            ),
                        )
                    assert (kind, refused.value.code) == (kind, "unsupported_presentation_driver")
                    assert refused.value.status_code == 422

            async with maker() as session:
                with pytest.raises(WorkspaceServiceError) as refused:
                    await bootstrap_workspace(
                        session,
                        presentation_id=presentation_id,
                        request=WorkspaceBootstrapRequest(
                            idempotency_key="refused-bootstrap", prompt="export",
                            operation_type="export.pdf",
                        ),
                    )
                assert refused.value.code == "unsupported_presentation_driver"

            # The export queued before the removal can still be cancelled.
            async with maker() as session:
                _receipt, cancelled, _duplicate = await apply_workspace_action(
                    session,
                    operation_id=operation_ids[0],
                    request=WorkspaceOperationActionRequest(
                        action="cancel", idempotency_key="cancel-queued-export",
                    ),
                )
                assert cancelled.state in {"cancelled", "cancelling"}

            async with maker() as session:
                delete, _ = await create_workspace_command(
                    session,
                    presentation_id=presentation_id,
                    request=WorkspaceCommandRequest(
                        command="delete it", command_type="presentation.delete",
                        idempotency_key="delete-authored",
                    ),
                )
                assert delete.state == "awaiting_approval"
                _receipt, approved, _duplicate = await apply_workspace_action(
                    session,
                    operation_id=delete.id,
                    request=WorkspaceOperationActionRequest(
                        action="approve", idempotency_key="approve-delete-authored",
                    ),
                )
                assert approved.state == "queued"
        finally:
            reset_current_owner_id(owner_token)
            await engine.dispose()

    asyncio.run(scenario())


def test_content_routes_answer_422_for_a_pre_existing_authored_deck(tmp_path: Path):
    async def scenario():
        engine, maker, presentation_id, _operation_ids = await _authored_workspace(tmp_path)
        owner_token = set_current_owner_id(OWNER_ID)
        try:
            async with maker() as session:
                calls = (
                    presentation_crud.get_presentation(presentation_id, session),
                    presentation_crud.get_presentation_generation_info(presentation_id, session),
                    presentation_versions.list_presentation_versions(presentation_id, session),
                    presentation_page_operations.list_authored_page_remake_preview_history(
                        presentation_id, session
                    ),
                    authored_editor_events.record_editor_event(
                        presentation_id,
                        AuthoredSlideEditorEventRequest(
                            event_type="export_completed", event_key="refused-event"
                        ),
                        session,
                    ),
                    outlines.stream_outlines(presentation_id, None, session),
                )
                for call in calls:
                    with pytest.raises(HTTPException) as refused:
                        await call
                    assert refused.value.status_code == 422
                    assert refused.value.detail == "unsupported_presentation_driver"
        finally:
            reset_current_owner_id(owner_token)
            await engine.dispose()

    asyncio.run(scenario())
