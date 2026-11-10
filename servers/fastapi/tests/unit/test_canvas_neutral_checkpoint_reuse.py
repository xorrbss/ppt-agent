from __future__ import annotations

from tests.support.existing_authored_rows import add_existing_authored_presentation
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import uuid

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.authored_slide_checkpoint import AuthoredSlideCheckpoint
from models.sql.presentation import PresentationModel
from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.user import User
from models.sql.workspace import (
    WorkspaceActionReceipt,
    WorkspaceAsset,
    WorkspaceConversation,
    WorkspaceMessage,
    WorkspaceMessageAttachment,
    WorkspaceOperation,
    WorkspaceOperationEvent,
    WorkspaceOutbox,
)
from models.workspace import WorkspaceOperationActionRequest
from services.authored_local_fit_policy import POLICY_REVISION
from services.document_canvas import document_canvas_for_orientation
from services.document_canvas_generation import export_size_points
from services.workspace_service import (
    AUTHORED_LOCAL_FIT_CHECKPOINT_HYDRATION_REVISION,
    _verified_authored_local_fit_checkpoint_recovery,
)


_TABLES = [
    User.__table__,
    PresentationModel.__table__,
    WorkspaceConversation.__table__,
    WorkspaceMessage.__table__,
    WorkspaceAsset.__table__,
    WorkspaceMessageAttachment.__table__,
    WorkspaceOperation.__table__,
    WorkspaceOperationEvent.__table__,
    WorkspaceOutbox.__table__,
    WorkspaceActionReceipt.__table__,
    AsyncPresentationGenerationTaskModel.__table__,
    PresentationGenerationJob.__table__,
    AuthoredSlideCheckpoint.__table__,
]


async def _database(path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: SQLModel.metadata.create_all(
                sync_connection, tables=_TABLES
            )
        )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed(maker, *, theme, operation_id: uuid.UUID):
    async with maker() as session:
        presentation = PresentationModel(
            content="canvas-neutral checkpoint reuse test",
            n_slides=1,
            language="en",
            layout={"name": "test"},
            mode="authored",
            theme=theme,
        )
        await add_existing_authored_presentation(session, presentation)
        await session.flush()

        task = AsyncPresentationGenerationTaskModel(status="pending")
        session.add(task)
        await session.flush()

        job = PresentationGenerationJob(
            task_id=task.id,
            presentation_id=presentation.id,
            workspace_operation_id=operation_id,
            request_sha256="a" * 64,
            request_payload={},
            last_error={
                "code": "authored_local_fit_geometry_unresolved",
                "stage": "render",
                "slide_index": 0,
            },
        )
        session.add(job)
        await session.flush()

        checkpoint = AuthoredSlideCheckpoint(
            job_id=job.id,
            slide_index=0,
            author_status="completed",
            illustrate_status="skipped",
            render_status="failed",
            persist_status="completed",
            failed_stage="render",
            error={"code": "authored_local_fit_geometry_unresolved"},
            illustrated_html="<html>fixture</html>",
        )
        session.add(checkpoint)
        await session.commit()
        return presentation, job, checkpoint


def _operation(*, operation_id: uuid.UUID, presentation_id: uuid.UUID, job_id: uuid.UUID) -> WorkspaceOperation:
    """An in-memory operation: the recovery function never re-reads it from
    the database, so it does not need to be persisted."""

    return WorkspaceOperation(
        id=operation_id,
        conversation_id=uuid.uuid4(),
        presentation_id=presentation_id,
        anchor_position=1,
        kind="template.apply",
        state="failed",
        idempotency_key_hash="b" * 64,
        request_hash="c" * 64,
        request_payload={"job_id": str(job_id)},
        error_code="authored_local_fit_geometry_unresolved",
        error_detail={"retryable": False},
    )


def _request() -> WorkspaceOperationActionRequest:
    return WorkspaceOperationActionRequest(
        action="retry",
        idempotency_key="retry-1",
        recovery_revision=AUTHORED_LOCAL_FIT_CHECKPOINT_HYDRATION_REVISION,
    )


def _bundle(*, html: str, manifest: dict):
    metrics = {
        "authored_local_fit": {
            "exportScaleManifest": manifest,
            "authorRevision": "rev-1",
            "originalHtmlDigest": "digest-1",
            "policyRevision": POLICY_REVISION,
        }
    }
    return SimpleNamespace(html=html, metrics=metrics, png=b"PNG-BYTES")


def _passing_receipt(*_args, **_kwargs):
    return {"status": "unchanged", "changed": False, "qualityStatus": "passed", "unresolvedReasons": []}


def _a4_landscape_geometry():
    physical = document_canvas_for_orientation("landscape")
    canvas = physical.canvas_contract()
    export_width_pt, export_height_pt = export_size_points(canvas, physical)
    return canvas.width_px, canvas.height_px, export_width_pt, export_height_pt


def _document_canvas_theme():
    return {
        "documentCanvas": document_canvas_for_orientation("landscape").model_dump(
            mode="json", by_alias=True
        )
    }


def test_sixteen_by_nine_presentation_reuses_checkpoint_as_before(tmp_path):
    async def scenario():
        _engine, maker = await _database(tmp_path / "sixteen-nine.db")
        operation_id = uuid.uuid4()
        presentation, job, checkpoint = await _seed(maker, theme=None, operation_id=operation_id)
        operation = _operation(operation_id=operation_id, presentation_id=presentation.id, job_id=job.id)
        bundle = _bundle(
            html="<html>fixture</html>",
            manifest={
                "width_pt": 960.0, "height_pt": 540.0,
                "viewport_width": 1280, "viewport_height": 720,
            },
        )
        async with maker() as session:
            with patch(
                "services.authored_checkpoint_artifacts.read_valid_evidence_artifact",
                new=AsyncMock(return_value=bundle),
            ) as read_evidence, patch(
                "services.authored_local_fit_evidence_status.reusable_checkpoint_receipt",
                side_effect=_passing_receipt,
            ):
                result = await _verified_authored_local_fit_checkpoint_recovery(
                    session, operation, _request()
                )
        assert result is not None and result.id == checkpoint.id
        assert read_evidence.await_args.kwargs == {
            "expected_width_px": 1280, "expected_height_px": 720,
        }

    asyncio.run(scenario())


def test_a4_presentation_with_matching_manifest_is_reused(tmp_path):
    async def scenario():
        _engine, maker = await _database(tmp_path / "a4-match.db")
        operation_id = uuid.uuid4()
        presentation, job, checkpoint = await _seed(
            maker, theme=_document_canvas_theme(), operation_id=operation_id
        )
        operation = _operation(operation_id=operation_id, presentation_id=presentation.id, job_id=job.id)
        width_px, height_px, width_pt, height_pt = _a4_landscape_geometry()
        bundle = _bundle(
            html="<html>fixture</html>",
            manifest={
                "width_pt": width_pt, "height_pt": height_pt,
                "viewport_width": width_px, "viewport_height": height_px,
            },
        )
        async with maker() as session:
            with patch(
                "services.authored_checkpoint_artifacts.read_valid_evidence_artifact",
                new=AsyncMock(return_value=bundle),
            ) as read_evidence, patch(
                "services.authored_local_fit_evidence_status.reusable_checkpoint_receipt",
                side_effect=_passing_receipt,
            ):
                result = await _verified_authored_local_fit_checkpoint_recovery(
                    session, operation, _request()
                )
        assert result is not None and result.id == checkpoint.id
        assert read_evidence.await_args.kwargs == {
            "expected_width_px": width_px, "expected_height_px": height_px,
        }

    asyncio.run(scenario())


def test_a4_presentation_with_sixteen_by_nine_manifest_is_not_reused(tmp_path):
    async def scenario():
        _engine, maker = await _database(tmp_path / "a4-mismatch.db")
        operation_id = uuid.uuid4()
        presentation, job, _checkpoint = await _seed(
            maker, theme=_document_canvas_theme(), operation_id=operation_id
        )
        operation = _operation(operation_id=operation_id, presentation_id=presentation.id, job_id=job.id)
        bundle = _bundle(
            html="<html>fixture</html>",
            manifest={
                "width_pt": 960.0, "height_pt": 540.0,
                "viewport_width": 1280, "viewport_height": 720,
            },
        )
        async with maker() as session:
            with patch(
                "services.authored_checkpoint_artifacts.read_valid_evidence_artifact",
                new=AsyncMock(return_value=bundle),
            ), patch(
                "services.authored_local_fit_evidence_status.reusable_checkpoint_receipt",
                side_effect=_passing_receipt,
            ):
                result = await _verified_authored_local_fit_checkpoint_recovery(
                    session, operation, _request()
                )
        assert result is None

    asyncio.run(scenario())


def test_unresolvable_theme_is_not_reused(tmp_path):
    async def scenario():
        _engine, maker = await _database(tmp_path / "unresolvable.db")
        operation_id = uuid.uuid4()
        malformed_theme = {"documentCanvas": {"contractVersion": "document-canvas/v1"}}
        presentation, job, _checkpoint = await _seed(
            maker, theme=malformed_theme, operation_id=operation_id
        )
        operation = _operation(operation_id=operation_id, presentation_id=presentation.id, job_id=job.id)
        async with maker() as session:
            with patch(
                "services.authored_checkpoint_artifacts.read_valid_evidence_artifact",
                new=AsyncMock(),
            ) as read_evidence:
                result = await _verified_authored_local_fit_checkpoint_recovery(
                    session, operation, _request()
                )
        assert result is None
        read_evidence.assert_not_awaited()

    asyncio.run(scenario())
