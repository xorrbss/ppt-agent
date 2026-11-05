import asyncio
from datetime import timedelta
from types import SimpleNamespace
import uuid

from fastapi import HTTPException
import pytest
from sqlalchemy import select

from models.sql.authored_slide_checkpoint import AuthoredSlideCheckpoint
from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.public_admission import PublicOperationAdmission, PublicProviderLogicalCall
from models.sql.workspace import WorkspaceOperation
from services import presentation_generation_pipeline as pipeline
from services import svg_partial_recovery as partial_recovery
from services import svg_presentation_checkpoint as svg_checkpoints
from utils import owner_context
from utils.datetime_utils import get_current_utc_datetime
from utils.hashing import sha256_hex
from test_authored_checkpoint_service import _checkpoint_database, _running_authored_job


async def _seed_pages(maker, job, count):
    async with maker() as session:
        stored = await session.get(PresentationGenerationJob, job.id)
        stored.request_payload = {"strategy": "svg"}
        session.add_all(AuthoredSlideCheckpoint(job_id=job.id, slide_index=index, role="content")
                        for index in range(count))
        await session.commit()
    job.request_payload = {"strategy": "svg"}


async def _pages(maker, job):
    async with maker() as session:
        return list(await session.scalars(select(AuthoredSlideCheckpoint).where(
            AuthoredSlideCheckpoint.job_id == job.id
        ).order_by(AuthoredSlideCheckpoint.slide_index)))


def test_preparation_can_retry_but_an_author_attempt_cannot(tmp_path, monkeypatch):
    async def scenario():
        engine, maker = await _checkpoint_database(tmp_path, monkeypatch)
        try:
            monkeypatch.setattr(svg_checkpoints, "async_session_maker", maker)
            _, job = await _running_authored_job(maker, total_slides=1)
            await _seed_pages(maker, job, 1)
            payload = {"source_font_profile": None}
            await svg_checkpoints._checkpoint(job, 0, payload=payload)
            row, = await _pages(maker, job)
            assert row.author_started_at is None
            assert row.render_started_at is None
            owner = uuid.uuid4()
            deadline = get_current_utc_datetime() + timedelta(hours=1)
            source = SimpleNamespace(
                id=uuid.uuid4(), owner_id=owner, owner_scope=str(owner),
                presentation_id=job.presentation_id, conversation_id=uuid.uuid4(),
                kind="presentation.generate", state="failed", fencing_token=1,
                contract_version="public-operation/v2", execution_profile_version="public-generation-export/v2",
                deadline_at=deadline, retry_of_operation_id=None,
                request_payload={"job_id": str(job.id), "generation_request": job.request_payload},
            )
            child = SimpleNamespace(**{**vars(source), "id": uuid.uuid4(), "retry_of_operation_id": source.id})
            admission = SimpleNamespace(
                id=uuid.uuid4(), owner_id=owner, tenant_id="test-tenant", operation_kind="generation",
                state="failed", fence=1, deadline_at=deadline, slide_count=1,
                initial_request_count=1, retry_request_count=0,
                estimated_reserved_microunits=0, actual_reserved_microunits=0,
                actual_cost_unavailable_count=0, estimated_consumed_microunits=0,
                actual_consumed_microunits=0, planned_request_limit=3, retry_request_limit=1,
                estimated_cost_limit_microunits=100, actual_cost_limit_microunits=100,
            )
            call = SimpleNamespace(
                stage="illustration", slide_index=-1, retry_class="initial", state="reported",
                outcome="succeeded", reconciled_at=get_current_utc_datetime(),
                actual_cost_microunits=0, estimated_cost_microunits=0,
                provider_id="openai", model_id="test-model",
            )

            class HistorySession:
                async def scalar(self, statement):
                    return {WorkspaceOperation: source, PublicOperationAdmission: admission,
                            PresentationGenerationJob: job}[statement.column_descriptions[0]["entity"]]

                async def scalars(self, statement):
                    return {PublicProviderLogicalCall: [call], AuthoredSlideCheckpoint: [row]}[
                        statement.column_descriptions[0]["entity"]]

            monkeypatch.setattr(pipeline, "current_principal_context",
                                lambda: SimpleNamespace(owner_id=owner, tenant_id="test-tenant"))
            session = HistorySession()
            assert await pipeline._unused_terminal_generation_retry_budget(
                session, source_operation=source, operation=child
            ) == (2, 1, 100, 100)
            await svg_checkpoints._checkpoint(job, 0, payload={**payload, "attempts": [{"kind": "provider"}]})
            row, = await _pages(maker, job)
            assert row.author_started_at is not None
            assert row.author_completed_at is None
            with pytest.raises(HTTPException, match="generation_retry_provider_history_present"):
                await pipeline._unused_terminal_generation_retry_budget(
                    session, source_operation=source, operation=child
                )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_authored_page_waiting_for_render_satisfies_real_recovery_predicates(tmp_path, monkeypatch):
    from services import authored_canvas, document_canvas_generation

    async def scenario():
        engine, maker = await _checkpoint_database(tmp_path, monkeypatch)
        try:
            monkeypatch.setattr(svg_checkpoints, "async_session_maker", maker)
            status, job = await _running_authored_job(maker, total_slides=2)
            await _seed_pages(maker, job, 2)
            owner, operation_id = uuid.uuid4(), uuid.uuid4()
            monkeypatch.setattr(owner_context, "get_current_owner_id", lambda: owner)
            monkeypatch.setattr(owner_context, "get_current_tenant_id", lambda: "test-tenant")
            svg = '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720"/>'
            raw = svg
            svg_hash = sha256_hex(svg.encode())
            policy = document_canvas_generation.svg_readability_policy(
                authored_canvas.canvas_metadata_for_aspect_ratio("16:9"), source_restyle=True
            )
            preservation = {
                "contract_version": "svg-source-preservation/v1", "manifest_digest": "sha256:" + "b" * 64,
                "source_authoring_contract": {"version": "svg-source-whole-page/v1"},
                "source_chart_contract": {"version": "svg-source-native-chart/v1"},
                "whole_page_native_groups": [{"element_id": "chart-1"}],
            }
            payload = {
                "schema_version": "svg-slide-checkpoint/v2", "source_format": "svg",
                "svg_content": svg, "input_digest": "c" * 64, "model_attempt_count": 1,
                "readability_policy": policy, "source_preservation": preservation,
                "source_manifest_digest": "d" * 64,
                "attempts": [{"kind": "provider", "raw_response": raw,
                              "receipt": {"raw_digest": svg_hash, "source_digest": svg_hash}}],
            }
            for index in range(2):
                await svg_checkpoints._checkpoint(job, index, payload=payload)
            rows = await _pages(maker, job)
            waiting = rows[0]
            assert waiting.render_started_at is None
            job.workspace_operation_id = operation_id
            terminal = {
                "schema": "svg-provider-terminal/v1", "terminal_event": "response.completed",
                "raw_response": raw, "output_digest": svg_hash, "job_id": str(job.id),
                "operation_id": str(operation_id), "owner_id": str(owner),
                "tenant_id": "test-tenant", "input_digest": payload["input_digest"],
            }
            terminal["receipt_digest"] = partial_recovery.digest(terminal)
            witness = partial_recovery._source_author_render_witness(
                svg, waiting.svg_checkpoint, raw, job=job, row=waiting, completed=terminal
            )
            assert witness["phase"] == "author-only"

            # Negative controls show the production predicates reject the old running state.
            waiting.render_status = "running"
            assert "phase" not in partial_recovery._source_author_render_witness(
                svg, waiting.svg_checkpoint, raw, job=job, row=waiting, completed=terminal
            )
        finally:
            await engine.dispose()

    asyncio.run(scenario())
