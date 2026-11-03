"""Fresh SVG setup is admitted only by its own complete durable accounting."""
import asyncio
from datetime import timedelta
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import select
from sqlmodel import SQLModel

from models.generate_presentation_request import GeneratePresentationRequest
from models.sql.authored_slide_checkpoint import AuthoredSlideCheckpoint
from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.provider_usage_receipt import ProviderUsageReceipt
from models.sql.public_admission import PublicOperationAdmission, PublicProviderLogicalCall
from models.sql.slide import SlideModel
from models.sql.workspace import WorkspaceOperation
from services import database, svg_presentation_checkpoint as checkpoints, svg_presentation_service as facade
from services.authored_checkpoint_errors import AuthoredStageError
from services.generation_core.drivers.svg import collect
from services.provider_usage_instrumentation import ProviderUsageScope
from services.public_operation_admission_models import _logical_call_ordinal
from services.svg_common_header_design_plan import seal_new_header_design_plan
from utils.datetime_utils import get_current_utc_datetime
from utils.hashing import sha256_hex
from test_authored_checkpoint_service import (
    _checkpoint_database, _linked_operation, _principal, _running_authored_job,
)


def _setup_pair(job, admission_id, stage, *, attempt=1, slot=None):
    slot = slot or ("stream.primary" if stage == "outline" else "structured.initial.attempt-1")
    scope = ProviderUsageScope(job_id=job.id, operation="authored_generation",
        stage=stage, attempt_number=attempt)
    key = scope.public_logical_call_key(slot)
    ordinal = _logical_call_ordinal(operation_id=job.workspace_operation_id,
        stage=stage, slide_index=-1, logical_call_ordinal=None, logical_call_key=key)
    preimage = (f"public-provider-call/v2:{job.workspace_operation_id}:{stage}:-1:{ordinal}:"
                f"{sha256_hex(key.encode())}")
    scope._receipt_request_ids.append(scope.next_runtime_request_id(slot))
    retry_class = ("retry" if "repair" in slot else ("resume" if attempt > 1 else "initial"))
    return (
        PublicProviderLogicalCall(admission_id=admission_id, stage=stage, slide_index=-1,
            logical_call_ordinal=ordinal, idempotency_key_hash=sha256_hex(preimage.encode()),
            retry_class=retry_class, provider_id="openai", model_id="test-model",
            state="reported", outcome="succeeded", reconciled_at=get_current_utc_datetime(),
            estimated_cost_microunits=3, actual_cost_microunits=2),
        ProviderUsageReceipt(job_id=job.id, operation="authored_generation", stage=stage,
            provider="openai", model="test-model", receipt_key_hash=scope.next_receipt_key_hash("provider"),
            retry_class=retry_class, outcome="succeeded", usage_state="reported", request_count=1,
            latency_ms=1, reported_cost_microunits=2, pricing_version="test-v1"),
    )


async def _world(tmp_path, monkeypatch, *, stages=("content_brief", "outline"), payload=None):
    engine, maker = await _checkpoint_database(tmp_path, monkeypatch)
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all, tables=[
            ProviderUsageReceipt.__table__, PublicProviderLogicalCall.__table__, SlideModel.__table__,
        ])
    monkeypatch.setattr(facade, "async_session_maker", maker)
    monkeypatch.setattr(checkpoints, "async_session_maker", maker)
    monkeypatch.setattr(database, "async_session_maker", maker)
    _, job = await _running_authored_job(maker, total_slides=10)
    owner = uuid.uuid4()
    operation_id, admission_id = await _linked_operation(maker, job, owner_id=owner, admitted=True)
    request = GeneratePresentationRequest(content="A manufacturing customer-service plan",
        n_slides=10, strategy="svg", common_header_mode="uniform")
    design_plan = seal_new_header_design_plan(request)
    stage_plan = {"partitions": [list(range(10))], "source_lineage": {"execution_authority": {
        "owner_id": str(owner), "tenant_id": "tenant-a", "manifest_digest": "sha256:" + "a" * 64,
        "request_digest": "sha256:" + "b" * 64,
    }}}
    async with maker() as session:
        stored = await session.get(PresentationGenerationJob, job.id)
        stored.attempt_number = 1
        stored.current_stage = "outline"
        stored.request_payload = {**request.model_dump(mode="json"),
            "_internal_svg_header_design_plan": design_plan,
            collect.INTERNAL_SVG_STAGE_PLAN_KEY: stage_plan, **(payload or {})}
        stored.svg_style_snapshot = {"common_header_design_plan": design_plan}
        stored.authored_outline = {"slides": [{"content": f"# Page {i}", "title": f"Page {i}"}
                                            for i in range(10)]}
        operation = await session.get(WorkspaceOperation, operation_id)
        operation.attempt_number = 1
        operation.expected_revision = 0
        admission = await session.get(PublicOperationAdmission, admission_id)
        admission.slide_count = 10
        admission.planned_request_limit = 55
        admission.initial_request_count = len(stages)
        admission.estimated_consumed_microunits = 3 * len(stages)
        admission.actual_consumed_microunits = 2 * len(stages)
        pairs = [_setup_pair(stored, admission_id, stage) for stage in stages]
        session.add_all([item for pair in pairs for item in pair])
        await session.commit()
        job = stored
    job._workspace_fencing_token = 9
    return SimpleNamespace(engine=engine, maker=maker, job=job, owner=owner, plan=stage_plan,
        admission_id=admission_id, operation_id=operation_id, pairs=pairs)


async def _pages(world):
    async with world.maker() as session:
        return list(await session.scalars(select(AuthoredSlideCheckpoint).where(
            AuthoredSlideCheckpoint.job_id == world.job.id).order_by(AuthoredSlideCheckpoint.slide_index)))


async def _enter(world, gate):
    with _principal(world.owner):
        if gate == "preflight":
            return await collect._stage_preflight(world.plan, world.job, checkpoints._checkpoint, 10)
        return await checkpoints.initialize_svg_checkpoints(job_id=world.job.id,
            attempt_token=world.job.attempt_token, roles=["cover"] + ["content"] * 9, fencing_token=9)


@pytest.mark.parametrize("mode,stages,payload", [
    ("prompt_only", ("content_brief", "outline"), {}),
    ("document_upload", ("content_brief", "outline"), {"files": ["document.pdf"]}),
    ("brief_disabled", ("outline",), {}),
    ("ui_outline", (), {"slides_markdown": ["# Page"] * 10}),
    ("source_restyle", (), {"source_mode": "source_content_restyle"}),
    ("raster_source", (), {"source_mode": "raster_reconstruction"}),
])
def test_fresh_setup_bootstraps_all_input_modes(tmp_path, monkeypatch, mode, stages, payload):
    async def scenario():
        world = await _world(tmp_path, monkeypatch, stages=stages, payload=payload)
        try:
            assert "_internal_svg_header_title_plan" not in world.job.request_payload
            assert world.job.request_payload["_internal_svg_header_design_plan"]
            assert await _enter(world, "preflight") == {}
            assert await _pages(world) == []  # Preflight is read-only.
            await _enter(world, "initialize")
            pages = await _pages(world)
            assert len(pages) == 10
            assert [page.role for page in pages] == ["cover"] + ["content"] * 9
            assert all(page.author_status == "pending" and page.author_started_at is None for page in pages)
            ids = [page.id for page in pages]
            await _enter(world, "initialize")
            assert [page.id for page in await _pages(world)] == ids
            assert await _enter(world, "preflight") == {}
        finally:
            await world.engine.dispose()
    asyncio.run(scenario())


@pytest.mark.parametrize("gate", ["initialize", "preflight"])
@pytest.mark.parametrize("violation", [
    "other_attempt", "other_admission", "other_receipt_attempt", "other_receipt_job",
    "unknown", "inflight", "unreported", "failed", "extra_author", "page_call",
    "request_count", "counter", "reserved", "cost", "budget", "slide_count", "expired",
    "published", "result_revision", "job_unknown", "operation_unknown", "stale_attempt",
    "missing_subset", "unexplained_receipt", "wrong_provider", "wrong_operation", "missing_receipt",
])
def test_bootstrap_rejects_unproven_history(tmp_path, monkeypatch, gate, violation):
    async def scenario():
        world = await _world(tmp_path, monkeypatch)
        try:
            async with world.maker() as session:
                job = await session.get(PresentationGenerationJob, world.job.id)
                operation = await session.get(WorkspaceOperation, world.operation_id)
                admission = await session.get(PublicOperationAdmission, world.admission_id)
                call = await session.get(PublicProviderLogicalCall, world.pairs[0][0].id)
                receipt = await session.get(ProviderUsageReceipt, world.pairs[0][1].id)
                if violation == "other_attempt":
                    changed, _ = _setup_pair(job, admission.id, call.stage, attempt=2)
                    call.logical_call_ordinal = changed.logical_call_ordinal
                    call.idempotency_key_hash = changed.idempotency_key_hash
                elif violation == "other_admission":
                    foreign = PublicOperationAdmission(**{
                        **admission.model_dump(), "id": uuid.uuid4(), "operation_id": uuid.uuid4()})
                    session.add(foreign)
                    call.admission_id = foreign.id
                elif violation == "other_receipt_attempt":
                    _, changed = _setup_pair(job, admission.id, call.stage, attempt=2)
                    receipt.receipt_key_hash = changed.receipt_key_hash
                elif violation == "other_receipt_job":
                    receipt.receipt_key_hash = sha256_hex(str(uuid.uuid4()).encode())
                elif violation == "unknown":
                    call.state, call.outcome = "unavailable", "public_provider_result_unknown"
                elif violation == "inflight":
                    call.state, call.outcome, call.reconciled_at = "reserved", None, None
                elif violation == "unreported":
                    receipt.usage_state, receipt.reported_cost_microunits = "unavailable", None
                elif violation == "failed":
                    call.state, call.outcome = "failed", "failed"
                    receipt.outcome = "failed"
                elif violation == "extra_author":
                    session.add_all(_setup_pair(job, admission.id, "author"))
                    admission.initial_request_count += 1
                    admission.estimated_consumed_microunits += 3
                    admission.actual_consumed_microunits += 2
                elif violation == "page_call":
                    call.slide_index = 0
                elif violation == "request_count":
                    receipt.request_count = 2
                elif violation == "counter":
                    admission.initial_request_count += 1
                elif violation == "reserved":
                    admission.estimated_reserved_microunits = 1
                elif violation == "cost":
                    admission.actual_consumed_microunits += 1
                elif violation == "budget":
                    admission.planned_request_limit = 10
                elif violation == "slide_count":
                    admission.slide_count = 9
                elif violation == "expired":
                    admission.deadline_at = get_current_utc_datetime() - timedelta(seconds=1)
                elif violation == "published":
                    session.add(SlideModel(presentation=job.presentation_id, owner_id=world.owner,
                        layout_group="svg", layout="svg", index=0, content={}, properties={},
                        svg_content="<svg/>", source_format="svg"))
                elif violation == "result_revision":
                    operation.result_revision = 1
                elif violation == "job_unknown":
                    job.last_error = {"code": "public_provider_result_unknown"}
                elif violation == "operation_unknown":
                    operation.error_detail = {"inflight": {"stage": "author"}}
                elif violation == "stale_attempt":
                    operation.attempt_token = "new-attempt"
                elif violation == "missing_subset":
                    session.add(AuthoredSlideCheckpoint(job_id=job.id, slide_index=0, role="cover"))
                elif violation == "unexplained_receipt":
                    extra = ProviderUsageReceipt(**{
                        **receipt.model_dump(), "id": uuid.uuid4(), "receipt_key_hash": "f" * 64})
                    session.add(extra)
                elif violation == "wrong_provider":
                    receipt.provider = "anthropic"
                elif violation == "wrong_operation":
                    receipt.operation = "generate"
                elif violation == "missing_receipt":
                    await session.delete(receipt)
                await session.commit()
            with pytest.raises((RuntimeError, AuthoredStageError, ValueError)):
                await _enter(world, gate)
            assert len(await _pages(world)) == (1 if violation == "missing_subset" else 0)
        finally:
            await world.engine.dispose()
    asyncio.run(scenario())


def test_completed_schema_repair_setup_is_accounted(tmp_path, monkeypatch):
    async def scenario():
        world = await _world(tmp_path, monkeypatch)
        try:
            async with world.maker() as session:
                admission = await session.get(PublicOperationAdmission, world.admission_id)
                session.add_all(_setup_pair(world.job, admission.id, "content_brief",
                                           slot="structured.schema-repair.attempt-1"))
                admission.retry_request_count += 1
                admission.estimated_consumed_microunits += 3
                admission.actual_consumed_microunits += 2
                await session.commit()
            await _enter(world, "preflight")
            await _enter(world, "initialize")
            assert len(await _pages(world)) == 10
        finally:
            await world.engine.dispose()
    asyncio.run(scenario())


def test_outline_density_expansion_setup_is_accounted(tmp_path, monkeypatch):
    """A thin outline spends one more outline-stage call under its own slot prefix."""
    from utils.llm_calls.outline_density import EXPANSION_SEMANTIC_SLOT_PREFIX
    from utils.structured_semantic_slots import structured_semantic_slot

    slot = structured_semantic_slot(
        validation_attempt=0, parse_attempt=0, prefix=EXPANSION_SEMANTIC_SLOT_PREFIX) + ".attempt-1"

    async def scenario():
        world = await _world(tmp_path, monkeypatch)
        try:
            async with world.maker() as session:
                admission = await session.get(PublicOperationAdmission, world.admission_id)
                session.add_all(_setup_pair(world.job, admission.id, "outline", slot=slot))
                admission.initial_request_count += 1
                admission.planned_request_limit += 1
                admission.estimated_consumed_microunits += 3
                admission.actual_consumed_microunits += 2
                await session.commit()
            await _enter(world, "preflight")
            await _enter(world, "initialize")
            assert len(await _pages(world)) == 10
        finally:
            await world.engine.dispose()
    asyncio.run(scenario())


def test_prompt_only_initializer_accepts_reported_setup_without_title_plan(tmp_path, monkeypatch):
    """R10: two completed deck setup calls, pending header design, zero pages."""
    async def scenario():
        world = await _world(tmp_path, monkeypatch)
        try:
            assert "_internal_svg_header_title_plan" not in world.job.request_payload
            assert world.job.svg_style_snapshot["common_header_design_plan"]
            await _enter(world, "initialize")
            assert len(await _pages(world)) == 10
        finally:
            await world.engine.dispose()
    asyncio.run(scenario())


def test_staged_preparation_does_not_fabricate_sealed_title_usage(tmp_path, monkeypatch):
    from services.svg_common_header_title_budget import seal_new_external_header_title_plan

    async def scenario():
        request = GeneratePresentationRequest(content="External outline", strategy="svg", common_header_mode="uniform",
            slides_markdown=["Business content without a headline"] * 10)
        plan = seal_new_external_header_title_plan(request)
        world = await _world(tmp_path, monkeypatch, stages=(),
                             payload={"_internal_svg_header_title_plan": plan})
        try:
            assert plan is not None
            assert await _enter(world, "preflight") == {}
            with pytest.raises(RuntimeError, match="svg_checkpoint_missing"):
                await _enter(world, "initialize")
            assert await _pages(world) == []
        finally:
            await world.engine.dispose()
    asyncio.run(scenario())
