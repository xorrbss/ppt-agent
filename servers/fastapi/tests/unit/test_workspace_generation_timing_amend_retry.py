"""Regression coverage for the durable generation-timing amend retry path.

Bench evidence (run 27/28 vs run 29, same sha): a fully successful
``presentation.generate`` operation can publish with only its export
artifact and no ``generation_timing_receipt``, while every per-call
``provider_usage_receipts`` row is complete. Root cause: when something else
(a separate publisher, or a duplicate finalize delivery) has *already*
CAS-published the operation's minimal result and its ``operation.result``
event before this handler's own finalize runs, ``finalize_workspace_operation_success``
falls into its "publication may atomically consume the same fencing token"
fallback and tries to CAS-amend the durable timing receipt into that
already-published row (``amend_generation_timing_projection``). That CAS is
keyed on ``WorkspaceOperation.version`` and can lose the race under
contention (heavier SQLite lock contention in run 29: 8 "database is locked"
errors vs. 4 in run 27/28). Losing the race used to make the whole finalize
call return ``False`` with **no logging at all** -- a real generation success
silently completing without its timing receipt.

``amend_generation_timing_projection_with_error_marker`` (in
``services/workspace_generation_timing_attachment.py``) replaces that
single-shot call: it retries the CAS against a freshly re-read row a bounded
number of times, and only if every attempt still loses the race does it log
an ERROR and write a best-effort ``timingReceiptError`` marker onto the
operation result -- so the drop is loud, not silent, and the operation still
publishes either way.

These tests exercise the real SQLite-backed CAS (no ORM/session mocking)
through ``finalize_workspace_operation_success`` itself, injecting the
"lost race" by faking the lower-level single-shot
``amend_generation_timing_projection`` for a controlled number of calls.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from pathlib import Path
import uuid

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from models.sql.presentation import PresentationModel
from models.sql.user import User
from models.sql.workspace import (
    WorkspaceConversation,
    WorkspaceOperation,
    WorkspaceOperationEvent,
    WorkspaceOutbox,
)
from services.authored_generation_timing_receipt import PUBLIC_GENERATION_TIMING_STAGES
from services.authored_quality_receipt_v2 import public_attempt_id
import services.workspace_generation_timing_attachment as timing_attachment
from services.workspace_operation_handler_registry import (
    WorkspaceOperationClaim,
    WorkspaceOperationResult,
)
from services.workspace_operation_runner import finalize_workspace_operation_success


TABLES = [
    User.__table__,
    PresentationModel.__table__,
    WorkspaceConversation.__table__,
    WorkspaceOperation.__table__,
    WorkspaceOperationEvent.__table__,
    WorkspaceOutbox.__table__,
]


def _v2_digest(payload: dict) -> str:
    clean = dict(payload)
    clean.pop("receiptDigest", None)
    return "sha256:" + hashlib.sha256(
        json.dumps(clean, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )
    ).hexdigest()


def _valid_v2_receipt() -> dict:
    payload = dict(
        schemaVersion="generation-attempt-timing-receipt/v2",
        attemptId=public_attempt_id("amend-retry-attempt"),
        attemptNumber=1,
        revision=3,
        outcome="succeeded",
        stages={
            name: {"state": "not_started"} for name in PUBLIC_GENERATION_TIMING_STAGES
        },
        queueWaitMillis=0,
        wallMillis=11,
        logicalCalls=0,
        retryReason=None,
        tokenAvailability="not_started",
        costAvailability="not_started",
        worker="workspace-generation-worker",
        terminalError=None,
        validity="valid",
        invalidReasons=[],
        providerUsage=None,
        receiptDigest="",
    )
    payload["receiptDigest"] = _v2_digest(payload)
    return payload


async def _database(path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_safety(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")

    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: SQLModel.metadata.create_all(
                sync_connection, tables=TABLES
            )
        )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed_running_generate_operation(maker):
    operation_id = uuid.uuid4()
    attempt_token = "amend-retry-attempt-token"
    async with maker() as session, session.begin():
        presentation = PresentationModel(
            content="before",
            n_slides=1,
            language="en",
            layout={"name": "test"},
            revision=3,
            mode="svg",
        )
        session.add(presentation)
        await session.flush()
        conversation = WorkspaceConversation(presentation_id=presentation.id)
        session.add(conversation)
        await session.flush()
        operation = WorkspaceOperation(
            id=operation_id,
            conversation_id=conversation.id,
            presentation_id=presentation.id,
            anchor_position=1,
            kind="presentation.generate",
            state="running",
            idempotency_key_hash="a" * 64,
            request_hash="b" * 64,
            request_payload={"kind": "presentation.generate"},
            expected_revision=3,
            attempt_number=1,
            attempt_token=attempt_token,
            fencing_token=1,
        )
        session.add(operation)

    claim = WorkspaceOperationClaim(
        operation_id=operation_id,
        owner_id=None,
        owner_scope="anonymous",
        conversation_id=conversation.id,
        presentation_id=presentation.id,
        kind="presentation.generate",
        request_payload={"kind": "presentation.generate"},
        expected_revision=3,
        attempt_number=1,
        attempt_token=attempt_token,
        fencing_token=1,
    )
    return operation_id, claim


def _flaky_amend(fail_times: int):
    """Fail the first ``fail_times`` calls, then delegate to the real CAS."""

    real = timing_attachment.amend_generation_timing_projection
    calls = {"n": 0}

    async def _fake(session, *, operation, safe_result, now):
        calls["n"] += 1
        if calls["n"] <= fail_times:
            return False
        return await real(session, operation=operation, safe_result=safe_result, now=now)

    return _fake, calls


def test_amend_retries_past_a_transient_cas_conflict_and_attaches_the_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asyncio.run(_amend_retries_past_a_transient_cas_conflict(tmp_path, monkeypatch))


async def _amend_retries_past_a_transient_cas_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, maker = await _database(tmp_path / "amend-retry-success.db")
    try:
        operation_id, claim = await _seed_running_generate_operation(maker)

        # First finalize call: this is the state run 29 was actually in --
        # some earlier publish already committed a minimal result with no
        # timing receipt (here modeled directly as the first successful
        # finalize, matching how run 27/28's own first successful finalize
        # behaves before a receipt is layered on).
        minimal_result = WorkspaceOperationResult(
            payload={"status": "completed", "publication": {"kind": "pptx"}},
            result_revision=4,
        )
        assert await finalize_workspace_operation_success(
            claim, minimal_result, session_factory=maker
        )

        # Second finalize call for the same already-succeeded operation now
        # carries the full timing receipt and must go through the "publication
        # already consumed the fencing token" amend fallback. Fake the CAS to
        # lose the race twice before letting the real CAS through, modeling
        # the version bump race observed under contention.
        fake_amend, calls = _flaky_amend(fail_times=2)
        monkeypatch.setattr(timing_attachment, "amend_generation_timing_projection", fake_amend)

        receipt = _valid_v2_receipt()
        full_result = WorkspaceOperationResult(
            payload={
                "status": "completed",
                "publication": {"kind": "pptx"},
                "generation_timing_receipt": receipt,
            },
            result_revision=4,
        )
        assert await finalize_workspace_operation_success(
            claim, full_result, session_factory=maker
        )
        assert calls["n"] == 3

        async with maker() as session:
            operation = await session.get(WorkspaceOperation, operation_id)
            assert operation is not None
            assert operation.result is not None
            assert "generation_timing_receipt" in operation.result
            assert operation.result["generation_timing_receipt"]["attemptId"] == receipt["attemptId"]
            assert "timingReceiptError" not in operation.result
    finally:
        await engine.dispose()


def test_amend_logs_loudly_and_marks_the_result_when_retries_are_exhausted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    asyncio.run(
        _amend_logs_loudly_and_marks_the_result_when_retries_are_exhausted(
            tmp_path, monkeypatch, caplog
        )
    )


async def _amend_logs_loudly_and_marks_the_result_when_retries_are_exhausted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    engine, maker = await _database(tmp_path / "amend-retry-exhausted.db")
    try:
        operation_id, claim = await _seed_running_generate_operation(maker)

        minimal_result = WorkspaceOperationResult(
            payload={"status": "completed", "publication": {"kind": "pptx"}},
            result_revision=4,
        )
        assert await finalize_workspace_operation_success(
            claim, minimal_result, session_factory=maker
        )

        # Every attempt loses the CAS race -- the sustained-contention case.
        fake_amend, calls = _flaky_amend(fail_times=999)
        monkeypatch.setattr(timing_attachment, "amend_generation_timing_projection", fake_amend)

        receipt = _valid_v2_receipt()
        full_result = WorkspaceOperationResult(
            payload={
                "status": "completed",
                "publication": {"kind": "pptx"},
                "generation_timing_receipt": receipt,
            },
            result_revision=4,
        )
        with caplog.at_level(logging.ERROR, logger="services.workspace_generation_timing_attachment"):
            # The operation still finishes publishing even though its timing
            # receipt could not be amended in -- dropping the receipt must
            # never fail the (already terminally succeeded) operation.
            assert await finalize_workspace_operation_success(
                claim, full_result, session_factory=maker
            )
        assert calls["n"] == timing_attachment._TIMING_AMEND_MAX_ATTEMPTS

        error_messages = [
            record.getMessage() for record in caplog.records if record.levelno >= logging.ERROR
        ]
        assert any("Dropped generation timing receipt" in message for message in error_messages)
        assert any(str(operation_id) in message for message in error_messages)

        async with maker() as session:
            operation = await session.get(WorkspaceOperation, operation_id)
            assert operation is not None
            assert operation.result is not None
            # The receipt itself never lands, but the drop is visible on the
            # published result instead of being silently absent.
            assert "generation_timing_receipt" not in operation.result
            assert (
                operation.result.get("timingReceiptError")
                == "generation_timing_receipt_amend_conflict"
            )
    finally:
        await engine.dispose()
