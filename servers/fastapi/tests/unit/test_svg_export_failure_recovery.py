"""A failed or cancelled SVG attempt releases its fence before returning."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

import services.public_export_join as export_join
import services.public_export_operation as public_export_operation
import services.svg_export_identity as svg_export_identity
from services.artifact_store import fail_export_identity
from services.public_export_errors import PublicExportOperationError
from services.public_export_identity import (
    PublicExportFenceRejected,
    complete_canonical_export_identity,
)
from tests.unit.test_svg_export_identity_join import _Harness, _request


@pytest.mark.parametrize("failure", ["execute", "cancel", "completion", "already_failed"])
def test_failed_identity_is_reclaimed_immediately_and_old_completion_is_fenced(
    tmp_path, monkeypatch, failure,
):
    async def scenario():
        monkeypatch.setenv("ENVIRONMENT", "local")
        export = _Harness(tmp_path)
        await export.start()
        try:
            for module in (svg_export_identity, public_export_operation):
                monkeypatch.setattr(module, "configured_public_artifact_service", lambda: export.artifacts)
            claim = await export.operation()
            executions, joins = [], []
            prepare = svg_export_identity.prepare_canonical_export_execution
            resolve = export_join.resolve_export_join
            complete = svg_export_identity.complete_canonical_export_identity
            frozen = datetime.now(timezone.utc)

            async def prepare_recorded(*args, **kwargs):
                execution = await prepare(*args, **kwargs)
                executions.append(execution)
                return execution

            async def no_wait(_seconds):
                pytest.fail("a released identity must never wait for its old lease")

            async def resolve_recorded(*args, **kwargs):
                outcome = await resolve(*args, **kwargs, clock=lambda: frozen, sleep=no_wait)
                joins.append(outcome)
                return outcome

            monkeypatch.setattr(svg_export_identity, "prepare_canonical_export_execution", prepare_recorded)
            monkeypatch.setattr(svg_export_identity, "resolve_export_join", resolve_recorded)
            entered = asyncio.Event()

            async def execute(_refs):
                if failure == "cancel":
                    entered.set()
                    await asyncio.Event().wait()
                if failure == "completion":
                    return await export.executor(claim)(_refs)
                if failure == "already_failed":
                    execution = executions[0]
                    async with export.sessions() as session:
                        assert await fail_export_identity(
                            session, identity_id=execution.singleflight.identity_id,
                            worker_id=execution.worker_id,
                            attempt_token=execution.singleflight.attempt_token,
                            fencing_token=execution.singleflight.fencing_token,
                            error_code="svg_export_valueerror",
                        )
                        await session.commit()
                raise PublicExportOperationError("svg_export_valueerror")

            async def reject_completion(*args, **kwargs):
                raise PublicExportOperationError("svg_export_result_invalid")

            if failure == "completion":
                monkeypatch.setattr(svg_export_identity, "complete_canonical_export_identity", reject_completion)
            task = asyncio.create_task(svg_export_identity.export_svg_through_identity(
                claim, tenant_id=export.tenant_id, request=_request(),
                session_factory=export.sessions, grant=None, execute=execute,
            ))
            if failure == "cancel":
                await asyncio.wait_for(entered.wait(), timeout=30)
                task.cancel()
            with pytest.raises(asyncio.CancelledError if failure == "cancel" else PublicExportOperationError):
                await task
            failed, = await export.identity_rows()
            assert failed.state == "failed"
            assert failed.error_code == (
                "CancelledError" if failure == "cancel" else
                "svg_export_result_invalid" if failure == "completion" else "svg_export_valueerror"
            )
            monkeypatch.setattr(svg_export_identity, "complete_canonical_export_identity", complete)
            result, _identity = await export.export(claim, _request())
            current, = await export.identity_rows()
            assert current.state == "succeeded"
            assert current.attempt_count == 2
            assert current.fencing_token == failed.fencing_token + 1
            assert executions[1].singleflight.attempt_token != executions[0].singleflight.attempt_token
            assert all(join.waited_seconds == 0 and join.poll_count == 0 for join in joins)
            with pytest.raises(PublicExportFenceRejected, match="public_export_singleflight_fence_stale"):
                async with export.sessions() as session:
                    await complete_canonical_export_identity(
                        session, execution=executions[0], operation_claim=claim,
                        result_artifact=result.artifact,
                    )
        finally:
            await export.engine.dispose()

    asyncio.run(scenario())


def test_repeated_cancellation_waits_for_the_fenced_release_transaction(tmp_path, monkeypatch):
    async def scenario():
        monkeypatch.setenv("ENVIRONMENT", "local")
        export = _Harness(tmp_path)
        await export.start()
        try:
            for module in (svg_export_identity, public_export_operation):
                monkeypatch.setattr(module, "configured_public_artifact_service", lambda: export.artifacts)
            claim = await export.operation()
            entered, releasing, finish_release = asyncio.Event(), asyncio.Event(), asyncio.Event()

            async def execute(_refs):
                entered.set()
                await asyncio.Event().wait()

            async def release(*args, **kwargs):
                releasing.set()
                await finish_release.wait()
                return await fail_export_identity(*args, **kwargs)

            monkeypatch.setattr(export_join, "fail_export_identity", release)
            task = asyncio.create_task(svg_export_identity.export_svg_through_identity(
                claim, tenant_id=export.tenant_id, request=_request(),
                session_factory=export.sessions, grant=None, execute=execute,
            ))
            await asyncio.wait_for(entered.wait(), timeout=30)
            task.cancel()
            await asyncio.wait_for(releasing.wait(), timeout=30)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
            finish_release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            row, = await export.identity_rows()
            assert row.state == "failed"
        finally:
            await export.engine.dispose()

    asyncio.run(scenario())
