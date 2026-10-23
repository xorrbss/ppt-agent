"""Review retry slots reserve request capacity; money is reserved per call."""

import asyncio
from types import SimpleNamespace
import uuid

import pytest

from models.sql.presentation import PresentationModel
from models.sql.public_admission import PublicPolicyProfile
from models.sql.user import User
from models.sql.workspace import WorkspaceConversation, WorkspaceOperation
from services.public_operation_admission import (
    PUBLIC_DEFAULT_MAX_SLIDES, PUBLIC_DEFAULT_POLICY_VERSION,
    PrincipalContext, PublicAdmissionRejected, activate_public_operation, admit_public_operation,
    plan_provider_requests, reserve_public_provider_call,
)
from services.svg_authoring_profile import seal_new_quality_recovery_plan, svg_quality_review_admission_budget
from tests.unit.test_public_operation_admission import _database


def test_80_page_retry_capacity_does_not_charge_the_cost_cap_up_front(tmp_path):
    async def scenario():
        engine, maker = await _database(tmp_path / "review-cost.db")
        try:
            owner = uuid.uuid4()
            async with maker() as session, session.begin():
                session.add(User(id=owner, username="review-cost", hashed_password="test-only"))
                session.add(PublicPolicyProfile(version=PUBLIC_DEFAULT_POLICY_VERSION,
                    max_slides=PUBLIC_DEFAULT_MAX_SLIDES,
                    global_active_generation=4, global_queued_generation=8,
                    owner_active_generation=1, owner_queued_generation=1,
                    tenant_active_generation=2, tenant_queued_generation=3,
                    global_active_export=4, global_queued_export=8,
                    owner_active_export=1, owner_queued_export=1,
                    tenant_active_export=2, tenant_queued_export=3,
                    global_active_resource_units=100, global_queued_resource_units=100,
                    tenant_active_resource_units=50, tenant_queued_resource_units=50,
                    owner_active_resource_units=25, owner_queued_resource_units=25,
                    retry_request_limit=1, estimated_cost_limit_microunits=13,
                    actual_cost_limit_microunits=12, generation_deadline_seconds=900,
                    export_deadline_seconds=600))
                presentation = PresentationModel(owner_id=owner, content="review budget", n_slides=80,
                    language="en", mode="svg", theme={"mode": "svg"})
                session.add(presentation)
                await session.flush()
                conversation = WorkspaceConversation(owner_id=owner, owner_scope=str(owner),
                    presentation_id=presentation.id)
                session.add(conversation)
                await session.flush()
                operation = WorkspaceOperation(owner_id=owner, owner_scope=str(owner),
                    conversation_id=conversation.id, presentation_id=presentation.id,
                    anchor_position=1, kind="presentation.generate", idempotency_key_hash="1" * 64,
                    request_hash="2" * 64, request_payload={})
                session.add(operation)
                await session.flush()
                request = SimpleNamespace(vision_qa=False)
                seal_new_quality_recovery_plan(request, 80)
                calls = svg_quality_review_admission_budget(request, 80)
                assert calls == 102
                admission = await admit_public_operation(session,
                    principal=PrincipalContext(owner, "review-cost", "review-cost"),
                    operation_id=operation.id, operation_kind="generation",
                    request_plan=plan_provider_requests(slide_count=80, image_budget=0,
                        repair_budget=0, vqa_budget=calls),
                    estimated_cost_limit_microunits=1, actual_cost_limit_microunits=1)
                assert admission.planned_request_limit == 4 + 80 + calls
                assert admission.estimated_reserved_microunits == 0
                assert admission.estimated_consumed_microunits == 0
                admission_id = admission.id
            async with maker() as session, session.begin():
                await activate_public_operation(session, admission_id)
            async with maker() as session, session.begin():
                with pytest.raises(PublicAdmissionRejected) as caught:
                    await reserve_public_provider_call(session, admission_id=admission_id, stage="vqa",
                        slide_index=None, logical_call_ordinal=1, retry_class="initial",
                        estimated_cost_microunits=2)
                assert caught.value.code == "public_provider_estimated_cost_limit_exceeded"
        finally:
            await engine.dispose()

    asyncio.run(scenario())
