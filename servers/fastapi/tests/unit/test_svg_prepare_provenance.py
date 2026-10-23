"""Real preparation keeps executor diagnostics out of retry and publication data."""

import asyncio
from datetime import timedelta
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import pytest
from sqlalchemy import select

from models.presentation_outline_model import PresentationOutlineModel, SlideOutlineModel
from models.sql.authored_slide_checkpoint import AuthoredSlideCheckpoint
from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.public_admission import PublicOperationAdmission, PublicProviderLogicalCall
from models.sql.workspace import WorkspaceOperation
from services import presentation_generation_pipeline as pipeline
from services import svg_presentation_checkpoint as checkpoints
from services import svg_presentation_prepare as prepare
from services import svg_presentation_service as facade
from services.authored_canvas import canvas_metadata_for_aspect_ratio
from services.generation_core.assets.offer import AssetOffer, OFFER_SCHEMA_VERSION
from services.generation_core.drivers.svg.publish import svg_slide_content
from utils.datetime_utils import get_current_utc_datetime
from test_authored_checkpoint_service import _checkpoint_database, _linked_operation, _running_authored_job


@pytest.fixture(params=[None, "3"], ids=["default-profile", "explicit-profile"])
def prepared_generation(request, tmp_path, monkeypatch):
    if request.param is None:
        monkeypatch.delenv("SVG_AUTHOR_CONCURRENCY", raising=False)
    else:
        monkeypatch.setenv("SVG_AUTHOR_CONCURRENCY", request.param)
    generation_request = SimpleNamespace(
        strategy="svg", authored_style="default", include_title_slide=False,
        instructions=None, _source_content_authored_reflow=False,
    )
    outline = PresentationOutlineModel(slides=[SlideOutlineModel(content="Quarterly results")])
    snapshot = {"digest": "sha256:style", "contract_version": "svg-static/v1"}
    canvas = canvas_metadata_for_aspect_ratio("16:9").canvas_contract()
    monkeypatch.setattr(facade, "require_svg_creation_admission", lambda _: None)
    monkeypatch.setattr(facade, "pin_svg_style", lambda _: snapshot)
    monkeypatch.setattr(facade, "request_canvas_contract", lambda _: canvas)
    monkeypatch.setattr("services.svg_generation_admission.prepare_private_svg_recovery_authority", AsyncMock())
    monkeypatch.setattr("services.svg_trusted_fonts.read_trusted_pretendard_faces", lambda: ())
    monkeypatch.setattr("services.authored_source_images.collect_source_image_candidates", AsyncMock(return_value=[]))
    monkeypatch.setattr("services.artifact_store.configured_public_artifact_service", lambda: object())
    monkeypatch.setattr("services.svg_generation_assets.publication_context", AsyncMock(return_value=(object(), 1)))
    pointer = {"ref": str(uuid.uuid4()), "sha256": "a" * 64}
    monkeypatch.setattr("services.svg_generation_assets.prepare_source_manifest", AsyncMock(
        return_value=(SimpleNamespace(slots=[], offered_pages=[]), pointer)))

    async def illustration_offer(_request, _outline, _roles, job, *, checkpoint, **_kwargs):
        # A settled deck-level provider result, before the first page author call.
        seed = await checkpoint(job, 0)
        seed["assetOfferPlan"] = {"schemaVersion": OFFER_SCHEMA_VERSION, "slots": [], "receipt": {}}
        await checkpoint(job, 0, payload=seed)
        return AssetOffer()

    monkeypatch.setattr("services.generation_core.drivers.svg.asset_slots.resolve_offer", illustration_offer)

    async def scenario():
        engine, maker = await _checkpoint_database(tmp_path, monkeypatch)
        try:
            monkeypatch.setattr(facade, "async_session_maker", maker)
            monkeypatch.setattr(checkpoints, "async_session_maker", maker)
            _, job = await _running_authored_job(maker, total_slides=1)
            await _linked_operation(maker, job)
            job._workspace_fencing_token = 9
            job.request_payload = {"strategy": "svg", "include_title_slide": False}
            async with maker() as session:
                stored = await session.get(PresentationGenerationJob, job.id)
                stored.request_payload = job.request_payload
                session.add(AuthoredSlideCheckpoint(job_id=job.id, slide_index=0, role="content"))
                await session.commit()
            async with maker() as session:
                ctx = await prepare._prepare_generation(
                    generation_request, job.presentation_id, outline, session, job,
                )
            async with maker() as session:
                rows = list(await session.scalars(select(AuthoredSlideCheckpoint).where(
                    AuthoredSlideCheckpoint.job_id == job.id,
                )))
            assert ctx.canonical_assets is True
            return ctx, job, rows
        finally:
            await engine.dispose()

    return asyncio.run(scenario())


def test_real_prepare_retains_provenance_only_in_context_and_allows_retry(prepared_generation, monkeypatch):
    ctx, job, rows = prepared_generation
    row, = rows
    assert set(row.svg_checkpoint) == {
        "deck_visual_intents", "assetOfferPlan", "source_image_assignment", "source_manifest_ref",
    }
    assert row.author_started_at is None
    assert row.render_started_at is None
    metadata = ctx.runtime_profile_provenance["author_concurrency"]
    assert metadata["source"] in {"default", "env"}
    assert metadata["effective"] == ctx.runtime_profile.author_concurrency
    assert "executor_capacity" in metadata

    owner = uuid.uuid4()
    deadline = get_current_utc_datetime() + timedelta(hours=1)
    source = SimpleNamespace(
        id=job.workspace_operation_id, owner_id=owner, owner_scope=str(owner),
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
            return {PublicProviderLogicalCall: [call], AuthoredSlideCheckpoint: rows}[
                statement.column_descriptions[0]["entity"]]

    monkeypatch.setattr(pipeline, "current_principal_context",
                        lambda: SimpleNamespace(owner_id=owner, tenant_id="test-tenant"))
    assert asyncio.run(pipeline._unused_terminal_generation_retry_budget(
        HistorySession(), source_operation=source, operation=child,
    )) == (2, 1, 100, 100)


def test_published_receipt_cannot_inherit_prepare_provenance(prepared_generation):
    ctx, _job, rows = prepared_generation
    payload = {**rows[0].svg_checkpoint,
               "svg_content": '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720"/>'}
    content = svg_slide_content(payload, role="content", source="generated", pt_per_px=0.75)
    assert "svgReceipt" in content
    serialized = json.dumps(content["svgReceipt"])
    assert "runtime_profile_provenance" not in serialized
    assert "executor_capacity" not in serialized
    assert "clamped_to_executor" not in serialized
    assert ctx.runtime_profile_provenance
