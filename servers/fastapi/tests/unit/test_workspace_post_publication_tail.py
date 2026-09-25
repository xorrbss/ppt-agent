"""A heartbeat that observes publication must not cancel the handler's tail.

Publication marks the operation succeeded and clears its attempt token before
the handler returns; ``presentation.generate`` then still submits the chained
``export_as`` export.  The runner used to cancel the handler at the next
heartbeat, silently dropping that export (seen on a real raster run: the
attempt-timing ledger recorded ``outcome: cancelled`` for a succeeded
operation, and no export operation existed).
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
import uuid

from sqlalchemy import select, update

from models.sql.workspace import WorkspaceOperation
from services import workspace_post_publication_tail as tail_module
from services.workspace_operation_error_definitions import (
    OPERATION_ERROR_DEFINITIONS,
)
from services.workspace_operation_handler_registry import (
    WorkspaceOperationHandlerRegistry,
    WorkspaceOperationResult,
)
from services.workspace_operation_runner import WorkspaceOperationRunner
from services.workspace_post_publication_tail import (
    POST_PUBLICATION_TAIL_ERROR_KEY,
    POST_PUBLICATION_TAIL_TIMEOUT_CODE,
)
from tests.unit.test_workspace_operation_runner import (
    _database,
    _drain_once,
    _operation,
    _seed_operations,
)
from utils.datetime_utils import get_current_utc_datetime


async def _publish(maker, context) -> uuid.UUID:
    """What publish_presentation_revision does to the running operation."""

    async with maker() as session:
        changed = await session.execute(
            update(WorkspaceOperation)
            .where(WorkspaceOperation.id == context.claim.operation_id)
            .where(WorkspaceOperation.attempt_token == context.claim.attempt_token)
            .where(WorkspaceOperation.state == "running")
            .values(
                state="succeeded",
                result={"status": "completed"},
                result_revision=1,
                attempt_token=None,
                lease_expires_at=None,
                completed_at=get_current_utc_datetime(),
                version=WorkspaceOperation.version + 1,
            )
        )
        assert changed.rowcount == 1
        await session.commit()
    return context.claim.conversation_id


def _runner(maker, handler) -> WorkspaceOperationRunner:
    registry = WorkspaceOperationHandlerRegistry()
    registry.register("test.operation", handler)
    # A heartbeat far shorter than the tail: it always observes publication
    # while the handler is still working.
    return WorkspaceOperationRunner(
        session_factory=maker,
        registry=registry,
        max_concurrency=1,
        heartbeat_seconds=0.01,
    )


def test_tail_completes_and_the_chained_export_survives(tmp_path: Path):
    asyncio.run(_tail_completes(tmp_path))


async def _tail_completes(tmp_path: Path):
    engine, maker = await _database(tmp_path / "tail.db")
    outcome: dict[str, object] = {}
    try:
        presentation_id, operation_ids = await _seed_operations(
            maker, revision=0, expected_revision=0
        )

        async def handler(context):
            conversation_id = await _publish(maker, context)
            try:
                # Longer than several heartbeats: the lease is already gone.
                await asyncio.sleep(0.2)
                # The chained export: one more operation, committed by the tail.
                async with maker() as session:
                    session.add(
                        WorkspaceOperation(
                            conversation_id=conversation_id,
                            presentation_id=presentation_id,
                            anchor_position=99,
                            kind="export.pptx",
                            idempotency_key_hash=f"{999:064x}",
                            request_hash=f"{998:064x}",
                            request_payload={},
                            expected_revision=1,
                        )
                    )
                    await session.commit()
                outcome["tail"] = "completed"
            except asyncio.CancelledError:
                outcome["tail"] = "cancelled"
                raise
            return WorkspaceOperationResult(
                payload={"status": "completed"}, result_revision=1
            )

        assert await _drain_once(_runner(maker, handler)) == 1
        assert outcome == {"tail": "completed"}
        operation = await _operation(maker, operation_ids[0])
        assert operation.state == "succeeded"
        assert POST_PUBLICATION_TAIL_ERROR_KEY not in (operation.result or {})
        async with maker() as session:
            exports = (
                await session.execute(
                    select(WorkspaceOperation.id)
                    .where(WorkspaceOperation.kind == "export.pptx")
                    .execution_options(skip_owner_scope=True)
                )
            ).all()
        assert len(exports) == 1
    finally:
        await engine.dispose()


def test_tail_past_the_bound_is_cancelled_and_recorded(
    tmp_path: Path, monkeypatch, caplog
):
    monkeypatch.setattr(tail_module, "POST_PUBLICATION_TAIL_TIMEOUT_SECONDS", 0.1)
    caplog.set_level(logging.ERROR, logger=tail_module.__name__)
    asyncio.run(_tail_times_out(tmp_path, caplog))


async def _tail_times_out(tmp_path: Path, caplog):
    engine, maker = await _database(tmp_path / "tail-timeout.db")
    outcome: dict[str, object] = {}
    try:
        _, operation_ids = await _seed_operations(
            maker, revision=0, expected_revision=0
        )

        async def handler(context):
            await _publish(maker, context)
            try:
                await asyncio.sleep(30)
                outcome["tail"] = "completed"
            except asyncio.CancelledError:
                outcome["tail"] = "cancelled"
                raise
            return WorkspaceOperationResult(payload={}, result_revision=1)

        assert await _drain_once(_runner(maker, handler)) == 1
        assert outcome == {"tail": "cancelled"}
        operation = await _operation(maker, operation_ids[0])
        # The deck stays published; the cut tail is visible on the result.
        assert operation.state == "succeeded"
        assert operation.result_revision == 1
        assert (
            operation.result[POST_PUBLICATION_TAIL_ERROR_KEY]
            == POST_PUBLICATION_TAIL_TIMEOUT_CODE
        )
        assert any(
            POST_PUBLICATION_TAIL_TIMEOUT_CODE in record.getMessage()
            and str(operation.id) in record.getMessage()
            for record in caplog.records
        )
    finally:
        await engine.dispose()


def test_tail_timeout_code_is_registered():
    definition = OPERATION_ERROR_DEFINITIONS[POST_PUBLICATION_TAIL_TIMEOUT_CODE]
    assert definition.retryable is False
