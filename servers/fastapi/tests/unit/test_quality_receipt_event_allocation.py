"""Private receipts share the fenced event allocator with progress writers."""

import asyncio

import pytest

from models.sql.workspace import WorkspaceOperation, WorkspaceOutbox
from services.authored_quality_receipt_v3_pipeline import create_generation_quality_receipt_v3
from services.public_quality_receipt_v3_store import (
    PostgresOperationQualityReceiptV3Port,
    append_public_review_evidence,
    quality_evidence_digest,
)
from sqlalchemy import event, func, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql.dml import Update
from test_authored_quality_receipt_v3_pipeline import _receipt_v2
from test_public_generation_progress_narration import _running_context
from test_workspace_operation_runner import _events, _operation


async def _receipt():
    class Capture:
        async def append_quality_receipt_v3(self, *, receipt, evidence):
            self.receipt, self.evidence = receipt, evidence

    capture = Capture()
    await create_generation_quality_receipt_v3(
        receipt_v2=_receipt_v2(), job_id=None, slide_roles=("content",),
        provider_attempt_counts=(1,), repaired_slide_indices=(),
        capacity_evidence={"browserObserved": True, "checks": {"font": True}},
        safety_evidence={"status": "passed", "residuals": []}, browser_metrics=({},),
        visual_review=None, source_manifest=None, source_readback=None,
        durability_profile="public", persistence_port=capture,
    )
    return capture.receipt, capture.evidence


@pytest.mark.parametrize("review", [False, True])
def test_progress_interleaves_between_receipt_read_and_atomic_append(tmp_path, monkeypatch, review):
    async def scenario():
        engine, maker, context = await _running_context(tmp_path)
        try:
            allocations = []

            @event.listens_for(engine.sync_engine, "before_execute")
            def record_allocation(_connection, statement, _multiparams, _params, _options):
                if isinstance(statement, Update) and "RETURNING" in str(statement):
                    allocations.append(statement)

            receipt, evidence = await _receipt()
            if review:
                async with maker() as session:
                    operation = await session.get(WorkspaceOperation, context.claim.operation_id)
                    operation.kind = "quality.review"
                    await session.commit()
            before = await _operation(maker, context.claim.operation_id)
            async with maker() as session:
                original_scalars = session.scalars
                interleaved = False

                async def interleave(*args, **kwargs):
                    nonlocal interleaved
                    result = await original_scalars(*args, **kwargs)
                    if not interleaved:
                        interleaved = True
                        assert await context.progress(stage="authored_author", current=2, total=6)
                    return result

                monkeypatch.setattr(session, "scalars", interleave)

                async def append():
                    if review:
                        await append_public_review_evidence(
                            session, operation_id=context.claim.operation_id,
                            attempt_token=context.claim.attempt_token,
                            fencing_token=context.claim.fencing_token,
                            receipt=receipt.to_dict(), evidence=evidence,
                        )
                    else:
                        port = PostgresOperationQualityReceiptV3Port(
                            session, operation_id=context.claim.operation_id,
                            attempt_token=context.claim.attempt_token,
                            fencing_token=context.claim.fencing_token,
                        )
                        await port.append_quality_receipt_v3(receipt=receipt, evidence=evidence)

                await append()
                await session.commit()
                await append()  # Idempotent replay must not reserve another sequence.
                await session.commit()
                outbox_count = await session.scalar(select(func.count()).select_from(WorkspaceOutbox))
            events = await _events(maker, context.claim.operation_id)
            assert [event.sequence for event in events] == list(range(1, len(events) + 1))
            progress, stored = events[-2:]
            assert progress.event_type == "operation.progress"
            assert stored.event_type == "quality.receipt.v3"
            assert stored.operation_version == progress.operation_version == before.version + 1
            expected = {
                "schemaVersion": "export-review-evidence/v1" if review else "quality-receipt-event/v3",
                "receipt": receipt.to_dict(), "evidence": evidence,
            }
            if review:
                expected["snapshotDigest"] = quality_evidence_digest(evidence)
            assert stored.payload == expected
            assert outbox_count == len(events) - 1  # Private evidence has no public notification.
            operation = await _operation(maker, context.claim.operation_id)
            assert operation.next_event_sequence == stored.sequence + 1
            assert operation.progress["current"] == 2
            # Both backends use this same UPDATE/RETURNING allocator. The
            # receipt reserves a sequence without advancing operation version.
            compiled = allocations[-1].compile(dialect=postgresql.dialect())
            sql = str(compiled)
            assert "next_event_sequence=(workspace_operations.next_event_sequence +" in sql
            assert "workspace_operations.attempt_token =" in sql
            assert "workspace_operations.fencing_token =" in sql
            assert "workspace_operations.state IN" in sql
            assert " RETURNING " in sql
            assert "version=" not in sql
            assert context.claim.attempt_token in compiled.params.values()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("stale", ["attempt", "fence"])
def test_receipt_append_preserves_attempt_and_fence_validation(tmp_path, stale):
    async def scenario():
        engine, maker, context = await _running_context(tmp_path)
        try:
            receipt, evidence = await _receipt()
            before = await _operation(maker, context.claim.operation_id)
            async with maker() as session:
                port = PostgresOperationQualityReceiptV3Port(
                    session, operation_id=context.claim.operation_id,
                    attempt_token="obsolete" if stale == "attempt" else context.claim.attempt_token,
                    fencing_token=context.claim.fencing_token + (stale == "fence"),
                )
                with pytest.raises(RuntimeError, match="quality_receipt_v3_operation_lease_lost"):
                    await port.append_quality_receipt_v3(receipt=receipt, evidence=evidence)
                await session.rollback()
            after = await _operation(maker, context.claim.operation_id)
            assert after.next_event_sequence == before.next_event_sequence
        finally:
            await engine.dispose()

    asyncio.run(scenario())
