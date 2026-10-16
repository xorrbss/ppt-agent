import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from models.authored_vnext.deck import MasterDigestPin
from models.authored_vnext.master.contract import DeckMasterContract
from models.generate_presentation_request import AuthoredMasterPinInput, GeneratePresentationRequest
from services.authored_vnext.generation.errors import PublishedMasterRequiredError
from services.authored_vnext.generation.legacy_pin import ResolvedLegacyMasterPin
from services.generation_core.style.design_pack_projection import DesignPackProjectionError
from services.svg_generation_admission import (
    _resolve_design_reference,
    _resolve_master_rules,
    require_svg_creation_admission,
    source_svg_capability,
)
from tests.support.authored_vnext_master_fixture import master_contract_payload


def _v2_header_request():
    return GeneratePresentationRequest(content="운영 보고", template="authored", strategy="svg",
        common_header_mode="uniform", slides_markdown=["# 운영 현황\n승인 상태를 원문대로 유지한다."])


def test_header_v2_fresh_admission_funds_initial_and_retry_calls_separately():
    from services.presentation_generation_staging import _durable_request_payload
    from services.public_operation_admission_models import plan_provider_requests
    from services.public_v2_orchestrator import _staged_svg_provider_request_plan
    from services.svg_common_header_design_plan import (
        svg_header_design_admission_budget, svg_header_representative_body_admission_budget,
    )
    from services.generation_provider_admission import generation_provider_capabilities

    request = _v2_header_request()
    _durable_request_payload(request)
    budgets = {"header_design_budget": svg_header_design_admission_budget(request, 1),
               "representative_body_budget": svg_header_representative_body_admission_budget(request, 1)}
    baseline = plan_provider_requests(slide_count=1, image_budget=0, repair_budget=0, vqa_budget=0)
    funded = plan_provider_requests(slide_count=1, image_budget=0, repair_budget=0, vqa_budget=0, **budgets)
    assert funded.initial_request_limit == baseline.initial_request_limit + 3
    assert funded.retry_request_limit == baseline.retry_request_limit + 3
    stages = {"partitions": [[0]], "illustration_budget": 0}
    staged_baseline = _staged_svg_provider_request_plan(1, stages)
    staged = _staged_svg_provider_request_plan(1, stages, **budgets)
    assert staged.initial_request_limit == staged_baseline.initial_request_limit + 3
    assert staged.retry_request_limit == staged_baseline.retry_request_limit + 3
    assert request.vision_qa is False
    assert "vision" in generation_provider_capabilities(request, "svg-v1")


def test_header_v2_durable_seed_roundtrip_and_caller_intent_replay(tmp_path, monkeypatch):
    from copy import deepcopy
    from services.presentation_generation_staging import _durable_request_payload, _request_from_durable_payload
    from services.svg_common_header_design_plan import INTERNAL_HEADER_DESIGN_PLAN_KEY, require_header_design_plan
    from services.svg_common_header_design_replay import matches_header_design_replay

    first = _durable_request_payload(_v2_header_request())
    second = _durable_request_payload(_v2_header_request())
    restored = _request_from_durable_payload(deepcopy(first))
    assert require_header_design_plan(restored) == first[INTERNAL_HEADER_DESIGN_PLAN_KEY]
    assert _durable_request_payload(restored) == first
    assert first[INTERNAL_HEADER_DESIGN_PLAN_KEY]["seed"] != second[INTERNAL_HEADER_DESIGN_PLAN_KEY]["seed"]
    assert matches_header_design_replay(first, second)
    changed = _v2_header_request()
    from models.common_header_design import CommonHeaderDesignInput
    changed.common_header_design = CommonHeaderDesignInput(instructions="사용자 고정 브랜드의 오른쪽 정렬을 유지한다.")
    assert not matches_header_design_replay(first, _durable_request_payload(changed))
    restored.instructions = "원래 입장 이후 변경된 요구"
    with pytest.raises(ValueError, match="design_plan_conflict"):
        require_header_design_plan(restored)

    # Explicit operation retry keeps settled financial history, producing
    # ownership and frozen input separate from a new command's identity.
    import asyncio
    from datetime import datetime, timedelta, timezone
    from models.sql.workspace import WorkspaceOperation
    from models.sql.public_admission import PublicOperationAdmission, PublicProviderLogicalCall
    from models.sql.provider_runtime_reservation import ProviderRuntimeBudget, ProviderRuntimeReservation
    from models.sql.provider_usage_receipt import ProviderUsageReceipt
    from services.workspace_svg_header_retry import (header_operation_request_hash, header_retry_source,
        require_header_retry_lineage, require_retry_page_context, retry_runtime_cost_limits, seed_header_retry_checkpoint)
    from services.public_operation_admission_models import _logical_call_ordinal
    from utils.hashing import sha256_hex

    owner, deck, conversation = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    now = datetime.now(timezone.utc)
    deadline = now + timedelta(hours=1)
    source = WorkspaceOperation(owner_id=owner, owner_scope=str(owner), presentation_id=deck,
        conversation_id=conversation, kind="design.apply", state="failed", expected_revision=2,
        request_payload={"presentation_id": str(deck), "command": "상단 수정", "command_type": "design.apply",
            "context": {"target": "common_header", "common_header_design": {}}, "expected_revision": 2,
            "asset_ids": [], "principal_tenant_id": "header-retry", "svg_header_revision": {
                "plan": first[INTERNAL_HEADER_DESIGN_PLAN_KEY]}},
        request_hash="", idempotency_key_hash="source", anchor_position=1,
        attempt_number=1, fencing_token=1, contract_version="public-operation/v2",
        execution_profile_version="public-generation-export/v2", deadline_at=deadline, completed_at=now)
    source.request_hash = header_operation_request_hash(source)
    admission = PublicOperationAdmission(operation_id=source.id, owner_id=owner, tenant_id="header-retry",
        operation_kind="generation", policy_version="test", policy_digest="a" * 64, resource_units=1,
        trace_id_hash="b" * 64, state="failed", fence=1, deadline_at=deadline, terminal_at=now,
        slide_count=1, planned_request_limit=4, retry_request_limit=4,
        estimated_cost_limit_microunits=100, actual_cost_limit_microunits=100)
    operations, admissions = {source.id: source}, {source.id: admission}
    logical_calls, runtime_budgets, reservations = {}, {}, {}

    class RetrySession:
        async def scalar(self, statement):
            entity = statement.column_descriptions[0]["entity"]
            ids = list(statement.compile().params.values())
            if entity is WorkspaceOperation:
                return next((operations[value] for value in ids if value in operations), None)
            if entity is PublicOperationAdmission:
                return next((admissions[value] for value in ids if value in admissions), None)
            assert entity is ProviderUsageReceipt
            return None

        async def scalars(self, statement):
            entity = statement.column_descriptions[0]["entity"]
            ids = list(statement.compile().params.values())
            table = {PublicProviderLogicalCall: logical_calls, ProviderRuntimeBudget: runtime_budgets,
                     ProviderRuntimeReservation: reservations}[entity]
            return next((table[value] for value in ids if value in table), [])

        async def get(self, model, identity):
            assert model is WorkspaceOperation
            return operations.get(identity)

    session = RetrySession()

    async def retry_contract():
        async def manifest():
            return await header_retry_source(session, source=source, expected_revision=2)

        empty = await manifest()
        assert empty["limits"]["planned_request_limit"] == 4
        command = source.request_payload.pop("command")
        with pytest.raises(ValueError, match="retry_lineage_conflict"):
            await manifest()
        source.request_payload["command"] = command
        slot = "svg-header-design-1"
        ordinal = _logical_call_ordinal(operation_id=source.id, stage="header", slide_index=-1,
            logical_call_ordinal=None, logical_call_key=slot)
        call = PublicProviderLogicalCall(admission_id=admission.id, stage="header", slide_index=-1,
            logical_call_ordinal=ordinal, retry_class="initial", state="reported", outcome="succeeded",
            reconciled_at=now, provider_id="openai", model_id="model", estimated_cost_microunits=20,
            actual_cost_microunits=10, idempotency_key_hash=sha256_hex(
                f"public-provider-call/v2:{source.id}:header:-1:{ordinal}:{sha256_hex(slot.encode())}".encode()))
        logical_calls[admission.id] = [call]
        admission.initial_request_count = 1
        admission.estimated_consumed_microunits = 20
        admission.actual_consumed_microunits = 10
        source.svg_header_design_checkpoint = {"version": "svg-common-header-design-checkpoint/v2",
            "identity": {"owner_id": str(source.id), "owner_type": "operation", "request_digest": source.request_hash},
            "document": {"status": "preparing", "steps": {"design-1": {"status": "succeeded", "stage": "design",
                "ordinal": 1, "semantic_slot": slot, "response": {"semantic_slot": slot,
                    "provider": "openai", "model": "model", "result": {"candidates": []}}}}}, "candidate_pages": {}}
        budget = ProviderRuntimeBudget(workspace_operation_id=source.id, owner_id=owner, tenant_id="header-retry",
            estimated_limit=70, actual_limit=80, estimated_consumed=20, actual_consumed=10, request_count=1)
        runtime_budgets[source.id] = [budget]
        from services.provider_usage_instrumentation import ProviderUsageScope
        def runtime_key(stage, slot, index=None):
            scope = ProviderUsageScope(job_id=source.id, operation="authored_generation", stage=stage,
                attempt_number=1, slide_index=index, public_only=True)
            return sha256_hex(f"provider-reservation-v1:{source.id}:{scope.next_runtime_request_id(slot)}".encode())
        reservation = ProviderRuntimeReservation(budget_id=budget.id, reservation_key_hash=runtime_key("header", slot),
            state="reconciled", estimated_cost=20, actual_cost=10, outcome="success",
            lease_token_hash="d" * 64, lease_expires_at=deadline)
        reservations[budget.id] = [reservation]
        settled = await manifest()
        assert list(settled["limits"].values()) == [3, 4, 80, 90]
        assert retry_runtime_cost_limits(settled, 60, 90) == (40, 70)
        original = deepcopy(source.model_dump(mode="json"))
        child = WorkspaceOperation(owner_id=owner, owner_scope=str(owner), presentation_id=deck,
            conversation_id=conversation, kind="design.apply", state="queued", expected_revision=2,
            retry_of_operation_id=source.id, request_payload={**deepcopy(source.request_payload),
                "retry_of_operation_id": str(source.id), "svg_header_retry": settled},
            request_hash="", idempotency_key_hash="child", anchor_position=2)
        child.request_hash = header_operation_request_hash(child)
        seed_header_retry_checkpoint(child, source)
        assert await require_header_retry_lineage(session, child) == settled
        assert source.model_dump(mode="json") == original
        assert child.svg_header_design_checkpoint["identity"] != source.svg_header_design_checkpoint["identity"]
        assert child.svg_header_design_checkpoint["replay_source"]["checkpoint_digest"] == settled["ancestors"][0]["checkpoint_digest"]
        child.svg_header_design_checkpoint["document"]["steps"]["design-1"]["response"]["model"] = "other"
        with pytest.raises(ValueError, match="retry_checkpoint_conflict"):
            await require_header_retry_lineage(session, child)
        for attribute, invalid in (("state", "reserved"), ("actual_cost_microunits", None),
                                   ("idempotency_key_hash", "e" * 64), ("stage", "author")):
            saved = getattr(call, attribute)
            setattr(call, attribute, invalid)
            with pytest.raises(ValueError, match="retry_provider_history_present"):
                await manifest()
            setattr(call, attribute, saved)
        reservation.state = "uncertain"
        with pytest.raises(ValueError, match="retry_provider_history_present"):
            await manifest()
        reservation.state = "reconciled"
        saved_reservation_key = reservation.reservation_key_hash
        reservation.reservation_key_hash = "c" * 64
        with pytest.raises(ValueError, match="retry_provider_history_present"):
            await manifest()
        reservation.reservation_key_hash = saved_reservation_key
        source.svg_header_design_checkpoint["document"]["steps"]["design-1"]["status"] = "unknown"
        with pytest.raises(ValueError, match="retry_provider_history_present"):
            await manifest()
        source.svg_header_design_checkpoint["document"]["steps"]["design-1"]["status"] = "succeeded"
        admission.estimated_cost_limit_microunits = 19
        with pytest.raises(ValueError, match="retry_budget_exhausted"):
            await manifest()
        admission.estimated_cost_limit_microunits = 100
        with pytest.raises(ValueError, match="retry_lineage_conflict"):
            await header_retry_source(session, source=source, expected_revision=3)
        from services.generation_core.authoring.author_slide_svg import svg_author_semantic_slot
        from services.svg_common_header_design_checkpoint import _candidate_key
        from services.svg_contract import source_digest
        from services.svg_common_header_generation import _paid_step
        body_identity = {"candidate_revision": "1", "candidate_id": "candidate-a", "page_id": "page-a"}
        body_key = _candidate_key(0, body_identity)
        body_raw = "<svg/>"
        body_receipt = {"author_attempt_identity": body_identity, "attempt_ordinal": 1,
            "provider": "openai", "requested_model": "model", "raw_digest": source_digest(body_raw),
            "style_digest": "sha256:" + "f" * 64}
        source.svg_header_design_checkpoint["document"]["parent_publication"] = {
            "outline": {"slides": [{"page_id": "page-a"}]}}
        source.svg_header_design_checkpoint["candidate_pages"][body_key] = {"input_digest": "sha256:" + "e" * 64,
            "header_author_attempt_identity": body_identity, "inflight": None,
            "quality": {"passed": False}, "png_sha256": "a" * 64,
            "author_body_svg": body_raw, "attempts": [{"kind": "provider", "raw_response": body_raw,
                                                       "receipt": body_receipt}]}
        local_key = _candidate_key(0, {**body_identity, "candidate_id": "candidate-b"})
        source.svg_header_design_checkpoint["candidate_pages"][local_key] = {"input_digest": "sha256:" + "d" * 64,
            "author_body_svg": body_raw, "svg_content": body_raw,
            "attempts": [{"kind": "local", "receipt": {"version": "svg-common-header-body-reuse/v2"}}]}
        body_slot = svg_author_semantic_slot(0, 1, body_identity)
        body_ordinal = _logical_call_ordinal(operation_id=source.id, stage="author", slide_index=0,
            logical_call_ordinal=None, logical_call_key=body_slot)
        body_call = PublicProviderLogicalCall(admission_id=admission.id, stage="author", slide_index=0,
            logical_call_ordinal=body_ordinal, retry_class="initial", state="reported", outcome="succeeded",
            reconciled_at=now, provider_id="openai", model_id="model", estimated_cost_microunits=20,
            actual_cost_microunits=10, idempotency_key_hash=sha256_hex(
                f"public-provider-call/v2:{source.id}:author:0:{body_ordinal}:{sha256_hex(body_slot.encode())}".encode()))
        logical_calls[admission.id].append(body_call)
        admission.initial_request_count = budget.request_count = 2
        admission.estimated_consumed_microunits = budget.estimated_consumed = 40
        admission.actual_consumed_microunits = budget.actual_consumed = 20
        reservations[budget.id].append(ProviderRuntimeReservation(budget_id=budget.id,
            reservation_key_hash=runtime_key("author", body_slot, 0), state="reconciled", estimated_cost=20, actual_cost=10,
            outcome="success", lease_token_hash="f" * 64, lease_expires_at=deadline))
        body_manifest = await manifest()
        assert list(body_manifest["limits"].values()) == [2, 4, 60, 80]
        assert retry_runtime_cost_limits(body_manifest, 60, 90) == (20, 60)
        child.request_payload = {**deepcopy(source.request_payload), "retry_of_operation_id": str(source.id),
                                 "svg_header_retry": body_manifest}
        child.request_hash = header_operation_request_hash(child)
        seed_header_retry_checkpoint(child, source)
        assert await require_header_retry_lineage(session, child) == body_manifest
        replayed_page = child.svg_header_design_checkpoint["candidate_pages"][body_key]
        assert replayed_page["attempts"] == source.svg_header_design_checkpoint["candidate_pages"][body_key]["attempts"]
        assert "quality" not in replayed_page and "png_sha256" not in replayed_page
        assert source.svg_header_design_checkpoint["candidate_pages"][body_key]["quality"] == {"passed": False}
        assert replayed_page["retry_render_parent"]["checkpoint_digest"] == body_manifest["ancestors"][0]["checkpoint_digest"]
        for field in ("author_body_svg", "svg_content"):
            saved_body = replayed_page[field]
            replayed_page[field] = "changed body"
            with pytest.raises(ValueError, match="retry_checkpoint_conflict"):
                await require_header_retry_lineage(session, child)
            replayed_page[field] = saved_body
        removed = child.svg_header_design_checkpoint["candidate_pages"].pop(body_key)
        with pytest.raises(ValueError, match="retry_checkpoint_conflict"):
            await require_header_retry_lineage(session, child)
        child.svg_header_design_checkpoint["candidate_pages"][body_key] = removed
        local_page = child.svg_header_design_checkpoint["candidate_pages"].pop(local_key)
        with pytest.raises(ValueError, match="retry_checkpoint_conflict"):
            await require_header_retry_lineage(session, child)
        child.svg_header_design_checkpoint["candidate_pages"][local_key] = local_page
        local_page["author_body_svg"] = "changed local body"
        with pytest.raises(ValueError, match="retry_checkpoint_conflict"):
            await require_header_retry_lineage(session, child)
        local_page["author_body_svg"] = body_raw
        replay_context = SimpleNamespace(snapshot={"digest": body_receipt["style_digest"]})
        with patch("services.svg_common_header_generation._page_input_digest", return_value=replayed_page["input_digest"]):
            require_retry_page_context(child, replay_context, SimpleNamespace(language="Korean"), None, 0,
                                       body_identity, replayed_page)
            replay_context.snapshot["digest"] = "changed-style"
            with pytest.raises(ValueError, match="retry_checkpoint_unproven"):
                require_retry_page_context(child, replay_context, SimpleNamespace(language="Korean"), None, 0,
                                           body_identity, replayed_page)
        child.svg_header_design_checkpoint["candidate_pages"][body_key]["attempts"][0]["raw_response"] = "changed"
        with pytest.raises(ValueError, match="retry_checkpoint_unproven"):
            await require_header_retry_lineage(session, child)
        seed_header_retry_checkpoint(child, source)
        stored_step = child.svg_header_design_checkpoint["document"]["steps"]["design-1"]
        stored_step["input_digest"] = "saved-input"
        with patch("services.svg_common_header_generation._digest", return_value="saved-input"), \
             patch("services.svg_common_header_generation.build_header_call", side_effect=AssertionError("replayed provider call")):
            assert await _paid_step(child.svg_header_design_checkpoint["document"], key="design-1", stage="design",
                ordinal=1, payload={}, images=[], plan=first[INTERNAL_HEADER_DESIGN_PLAN_KEY], job=child,
                save=None) == stored_step["response"]
        seed_header_retry_checkpoint(child, source)
        child.state = "failed"
        child.completed_at = now
        child.attempt_number = child.fencing_token = 1
        child.contract_version = "public-operation/v2"
        child.execution_profile_version = "public-generation-export/v2"
        child.deadline_at = deadline
        operations[child.id] = child
        child_admission = PublicOperationAdmission(operation_id=child.id, owner_id=owner, tenant_id="header-retry",
            operation_kind="generation", policy_version="test", policy_digest="a" * 64, resource_units=1,
            trace_id_hash="b" * 64, state="failed", fence=1, deadline_at=deadline, terminal_at=now,
            slide_count=1, **body_manifest["limits"])
        admissions[child.id] = child_admission
        nested = await header_retry_source(session, source=child, expected_revision=2)
        assert nested["limits"] == body_manifest["limits"]
        assert nested["consumed"] == body_manifest["consumed"]
        assert len(nested["ancestors"]) == 2
        source.svg_header_design_checkpoint["candidate_pages"][body_key]["inflight"] = 2
        with pytest.raises(ValueError, match="retry_provider_history_present"):
            await manifest()

    with patch("services.public_operation_admission.current_principal_context",
               return_value=SimpleNamespace(owner_id=owner, tenant_id="header-retry")), \
         patch("models.sql.workspace.get_workspace_owner_scope", return_value=str(owner)):
        asyncio.run(retry_contract())

    async def admitted_command_retry():
        from sqlalchemy import select, func
        from sqlmodel import SQLModel
        from models.sql.user import User
        from models.sql.presentation import PresentationModel
        from models.sql.slide import SlideModel
        from models.sql.workspace import WorkspaceConversation
        from models.sql.public_admission import PublicPolicyProfile, PublicQuotaCounter, PublicResourceLease
        from models.sql.presentation_generation_job import PresentationGenerationJob
        from models.sql.async_presentation_generation_status import AsyncPresentationGenerationTaskModel
        from models.presentation_outline_model import PresentationOutlineModel, SlideOutlineModel
        from models.workspace import WorkspaceCommandRequest, WorkspaceOperationActionRequest
        from services.public_operation_admission import finish_public_operation_v2, _policy_digest
        from services.workspace_service import create_workspace_command, apply_workspace_action, WorkspaceServiceError
        from services.workspace_operation_principal import operation_principal
        from tests.unit.test_workspace_service import _database
        from utils.hashing import canonical_json_digest

        engine, maker = await _database(tmp_path / "header-retry-admission.db")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(lambda sync: SQLModel.metadata.create_all(sync, tables=[
                    SlideModel.__table__, AsyncPresentationGenerationTaskModel.__table__, PresentationGenerationJob.__table__,
                    PublicPolicyProfile.__table__, PublicQuotaCounter.__table__, PublicOperationAdmission.__table__,
                    PublicProviderLogicalCall.__table__, PublicResourceLease.__table__, ProviderRuntimeBudget.__table__,
                    ProviderRuntimeReservation.__table__, ProviderUsageReceipt.__table__]))
            policy = PublicPolicyProfile(version="public-default/v5", max_slides=100, retry_request_limit=8,
                estimated_cost_limit_microunits=10000, actual_cost_limit_microunits=10000,
                generation_deadline_seconds=900, export_deadline_seconds=600,
                **{f"{scope}_{state}_{kind}": 8 for scope in ("global", "owner", "tenant")
                   for state in ("active", "queued") for kind in ("generation", "export")},
                **{f"{scope}_{state}_resource_units": 10000 for scope in ("global", "owner", "tenant")
                   for state in ("active", "queued")})
            async with maker() as session:
                user = User(username="header-retry-owner", hashed_password="unused")
                session.add_all([user, policy])
                await session.commit()
            with operation_principal(user.id, "header-retry-db"):
                async with maker() as session:
                    source_request = _v2_header_request()
                    payload = _durable_request_payload(source_request)
                    style = {"tokens": {}}
                    style["digest"] = canonical_json_digest(style)
                    deck = PresentationModel(owner_id=user.id, title="현재 상단 재시도", content=source_request.content,
                        n_slides=1, language="ko", mode="svg", revision=2, theme={"mode": "svg", "svg": {"styleSnapshot": style}},
                        outlines=PresentationOutlineModel(slides=[SlideOutlineModel(content=source_request.slides_markdown[0],
                            title="운영 현황", page_id="retry-db-page")]).model_dump(mode="json"))
                    session.add(deck)
                    await session.flush()
                    conversation = WorkspaceConversation(owner_id=user.id, owner_scope=str(user.id), presentation_id=deck.id)
                    session.add(conversation)
                    await session.flush()
                    generation = WorkspaceOperation(owner_id=user.id, owner_scope=str(user.id), conversation_id=conversation.id,
                        presentation_id=deck.id, anchor_position=1, kind="presentation.generate", state="succeeded",
                        idempotency_key_hash="1" * 64, request_hash="2" * 64,
                        request_payload={"principal_tenant_id": "header-retry-db"}, expected_revision=0, result_revision=2,
                        attempt_number=1, max_attempts=1, fencing_token=1, completed_at=now, deadline_at=deadline, phase="succeeded",
                        contract_version="public-operation/v2", execution_profile_version="public-generation-export/v2")
                    task = AsyncPresentationGenerationTaskModel(owner_id=user.id, status="completed")
                    session.add_all([generation, task])
                    await session.flush()
                    job = PresentationGenerationJob(task_id=task.id, presentation_id=deck.id, workspace_operation_id=generation.id,
                        request_sha256="3" * 64, request_payload=payload, state="published", total_slides=1, completed_slides=1)
                    page = SlideModel(owner_id=user.id, presentation=deck.id, index=0, layout_group="svg", layout="svg",
                        svg_content='<svg xmlns="http://www.w3.org/2000/svg"/>', source_format="svg", content={"source": "unchanged"}, properties={})
                    session.add_all([job, page, PublicOperationAdmission(operation_id=generation.id, owner_id=user.id,
                        tenant_id="header-retry-db", operation_kind="generation", policy_version=policy.version,
                        policy_digest=_policy_digest(policy), resource_units=1, trace_id_hash="4" * 64, state="succeeded",
                        fence=1, deadline_at=deadline, terminal_at=now, slide_count=1, planned_request_limit=4,
                        retry_request_limit=4, estimated_cost_limit_microunits=10000, actual_cost_limit_microunits=10000)])
                    await session.commit()
                    deck_id, page_id, job_id = deck.id, page.id, job.id
                with monkeypatch.context() as local:
                    local.setattr("utils.llm_calls.svg_common_header_design.preflight_header_selector", lambda: {
                        "provider": "openai", "model": "fixture", "registry_capabilities": {"structuredOutput": True, "vision": True},
                        "client_capabilities": {}})
                    local.setattr("services.workspace_operation_runner.notify_workspace_operation_state_change", lambda *a, **k: None)
                    async with maker() as session:
                        operation, duplicate = await create_workspace_command(session, presentation_id=deck_id,
                            request=WorkspaceCommandRequest(command="상단 디자인 수정", command_type="design.apply",
                                idempotency_key="header-command", expected_revision=2,
                                context={"target": "common_header", "common_header_design": {}}))
                        assert not duplicate and operation.expected_revision == 2
                        source_id = operation.id
                        row = await session.scalar(select(PublicOperationAdmission).where(PublicOperationAdmission.operation_id == source_id))
                        await finish_public_operation_v2(session, admission_id=row.id, state="failed", expected_fence=operation.fencing_token)
                        operation.state, operation.attempt_number, operation.completed_at = "failed", 1, now
                        operation.error_code, operation.error_detail = "fixture_local_failure", {"retryable": False}
                        await session.commit()
                    async with maker() as session:
                        original_operation = await session.get(WorkspaceOperation, source_id)
                        original_row = await session.scalar(select(PublicOperationAdmission).where(PublicOperationAdmission.operation_id == source_id))
                        frozen = deepcopy(original_operation.model_dump(mode="json"))
                        frozen_admission = deepcopy(original_row.model_dump(mode="json"))
                        frozen_job = deepcopy((await session.get(PresentationGenerationJob, job_id)).model_dump(mode="json"))
                        frozen_page = deepcopy((await session.get(SlideModel, page_id)).model_dump(mode="json"))
                        action = WorkspaceOperationActionRequest(action="retry", idempotency_key="header-explicit-retry",
                            expected_revision=2, expected_operation_version=original_operation.version)
                        receipt, child, duplicate = await apply_workspace_action(session, operation_id=source_id, request=action)
                        assert not duplicate and child.id != source_id and child.retry_of_operation_id == source_id
                        assert child.state == "queued" and child.expected_revision == 2
                        assert receipt.result_operation_id == child.id
                        child_row = await session.scalar(select(PublicOperationAdmission).where(PublicOperationAdmission.operation_id == child.id))
                        assert child_row.state == "queued" and child_row.tenant_id == "header-retry-db"
                        assert child.contract_version == "public-operation/v2" and child_row.fence == child.fencing_token
                        assert child.request_payload["svg_header_revision"] == frozen["request_payload"]["svg_header_revision"]
                        await require_header_retry_lineage(session, child, child_row)
                        assert (await session.get(WorkspaceOperation, source_id)).model_dump(mode="json") == frozen
                        assert (await session.get(PresentationGenerationJob, job_id)).model_dump(mode="json") == frozen_job
                        assert (await session.get(SlideModel, page_id)).model_dump(mode="json") == frozen_page
                        await session.refresh(original_row)
                        assert original_row.model_dump(mode="json") == frozen_admission
                        assert (await session.get(PresentationModel, deck_id)).revision == 2
                        assert all(counter.queued_count == 1 for counter in await session.scalars(select(PublicQuotaCounter)))
                        assert await session.scalar(select(func.count()).select_from(PresentationGenerationJob)) == 1
                    async with maker() as session:
                        _, replayed, duplicate = await apply_workspace_action(session, operation_id=source_id, request=action)
                        assert duplicate and replayed.id == child.id
                        with pytest.raises(WorkspaceServiceError, match="revision_conflict"):
                            await apply_workspace_action(session, operation_id=source_id,
                                request=action.model_copy(update={"idempotency_key": "header-stale-retry", "expected_revision": 3}))
        finally:
            await engine.dispose()

    asyncio.run(admitted_command_retry())


def test_header_v2_frozen_v1_replay_keeps_zero_budget_and_exact_payload():
    from copy import deepcopy
    from models.presentation_outline_model import PresentationOutlineModel, SlideOutlineModel
    from services.authored_canvas import request_canvas_metadata
    from services.presentation_generation_staging import _durable_request_payload, _request_from_durable_payload
    from services.svg_common_header import prepare_common_header, render_common_header
    from services.svg_common_header_design_plan import (
        INTERNAL_HEADER_DESIGN_PLAN_KEY, svg_header_design_admission_budget,
        svg_header_representative_body_admission_budget,
    )
    from services.svg_generation_admission import pin_svg_style
    from utils.hashing import canonical_json_digest

    request = _v2_header_request()
    request.common_header_mode = "free"
    snapshot = deepcopy(pin_svg_style(request))
    request.common_header_mode = "uniform"
    outline = PresentationOutlineModel(slides=[SlideOutlineModel(content="원문", title="운영 현황")])
    contract, bindings = prepare_common_header(snapshot, outline, request_canvas_metadata(request))
    snapshot["common_header"] = contract
    snapshot["digest"] = canonical_json_digest({k: v for k, v in snapshot.items() if k != "digest"}, allow_nan=True)
    request._svg_style_snapshot = deepcopy(snapshot)
    payload = _durable_request_payload(request)
    restored = _request_from_durable_payload(deepcopy(payload))
    assert contract["version"] == "svg-common-header/v1"
    assert INTERNAL_HEADER_DESIGN_PLAN_KEY not in payload
    assert svg_header_design_admission_budget(restored, 1) == 0
    assert svg_header_representative_body_admission_budget(restored, 1) == 0
    assert _durable_request_payload(restored) == payload
    assert restored._svg_style_snapshot == snapshot
    assert render_common_header(contract, bindings[0], index=0, count=1) is not None
    from services.svg_authoring_profile import select_svg_authoring_profile
    historical = _v2_header_request()
    select_svg_authoring_profile(historical, "current-v4", trusted_internal=True)
    assert INTERNAL_HEADER_DESIGN_PLAN_KEY not in _durable_request_payload(historical)


def test_header_v2_real_fields_reject_title_and_stable_page_id_tampering(monkeypatch):
    from copy import deepcopy
    from models.presentation_outline_model import PresentationOutlineModel, SlideOutlineModel
    from services.authored_canvas import request_canvas_metadata
    from services.svg_common_header import validate_common_header_outline
    from services.svg_common_header_v2 import prepare_common_header_v2
    from services.svg_contract import finalize_svg

    request = _v2_header_request()
    outline = PresentationOutlineModel(slides=[SlideOutlineModel(
        page_id="stable-page-one", content="원본 운영 현황", title="운영 현황")])
    candidate = {"candidate_id": "source-bound", "revision": 1,
        "svg": '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720"><text id="heading" fill="#111111"/></svg>',
        "body_box": {"x": 64, "y": 150, "width": 1152, "height": 520},
        "fields": [{"node_id": "heading", "role": "title", "value_ref": "title",
            "box": {"x": 64, "y": 25, "width": 1152, "height": 70}, "font_family": "Noto Sans KR",
            "font_weight": 700, "font_size_px": 32, "line_height_px": 45,
            "baseline_offset_px": 38, "max_lines": 2, "alignment": "left", "optional": False}]}
    contract, bindings = prepare_common_header_v2(candidate, outline, request_canvas_metadata(request), snapshot={})
    outline.slides[0].common_header = bindings[0]
    snapshot = {"common_header": contract}
    validate_common_header_outline(snapshot, outline)
    body = '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720"><rect x="64" y="200" width="100" height="50" fill="#123456"/></svg>'
    final = finalize_svg(body, index=0, n=1, style_snapshot=snapshot, header_page=bindings[0])
    assert "svg-system-common-header-v2-contract" in final and "운영 현황" in final
    for key, value in (("page_id", "rebound-page"), ("title", "변조된 제목")):
        altered = deepcopy(outline)
        setattr(altered.slides[0], key, value)
        with pytest.raises(ValueError, match="svg_common_header"):
            validate_common_header_outline(snapshot, altered)

    from xml.etree import ElementTree as ET
    from services.generation_core.drivers.svg.render import _common_header_body_repair_hint
    from services.svg_common_header_export_evidence import apply_common_header_v2_quality
    import json

    wrapped_body = body.replace('<rect x="64" y="200" width="100" height="50" fill="#123456"/>',
        '<text x="64" y="220" font-family="Noto Sans KR" font-size="32" fill="#123456">'
        '\n<tspan x="64" y="220">본문</tspan>\n</text>')
    wrapped_final = finalize_svg(wrapped_body, index=0, n=1, style_snapshot=snapshot, header_page=bindings[0])
    metrics = []
    for index, node in enumerate(ET.fromstring(wrapped_final).iter()):
        tag = node.tag.rsplit("}", 1)[-1]
        if tag not in {"text", "tspan"}:
            continue
        owned = bool((node.text or "").strip())
        box = (bindings[0]["paint_boxes"].get(node.get("id")) or
               [{"x": 64, "y": 200, "width": 100, "height": 20}])[0]
        metrics.append({"qid": f"svg-{index}", "tag": tag, "css_visible": True,
            "effective_opacity": 1, "has_owned_text": owned, "bbox": box,
            "text_fragments": [box] if owned else [], "text_ink_fragments": [box] if owned else [],
            "font_loaded": True, "resolved_font_family": "Noto Sans KR", "font_size": 32})

    def measured_body(items):
        quality = {"passed": True, "geometry_passed": True, "failures": [],
                   "browser_metrics": {"elements": deepcopy(items)}}
        apply_common_header_v2_quality(quality, wrapped_final, contract, bindings[0], None)
        return quality

    assert measured_body(metrics)["common_header"]["passed"] is True
    valid_metrics = deepcopy(metrics)
    child = next(item for item in metrics if item["tag"] == "tspan")
    child["text_ink_fragments"] = [{"x": 1200, "y": 200, "width": 16.6, "height": 20}]
    intrusion = measured_body(metrics)
    assert intrusion["failures"] == [{"kind": "svg_common_header_body_intrusion", "qid": child["qid"]}]
    hint = json.loads(_common_header_body_repair_hint(intrusion, contract))
    assert hint["body_box"] == contract["body_box"]
    assert hint["witnesses"][0]["qid"] == child["qid"]
    assert hint["witnesses"][0]["measured_boxes"][0]["width"] == 16.6
    missing = deepcopy(metrics)
    next(item for item in missing if item["tag"] == "tspan").update(text_fragments=[], text_ink_fragments=[])
    assert measured_body(missing)["passed"] is False

    # A new explicit header revision may remeasure a pristine older-verifier
    # publication, preserving its exact bytes/PNG and never calling an author.
    import asyncio
    from services import svg_renderer, svg_presentation_checkpoint
    from services.svg_contract import source_digest
    from services.svg_master_furniture import master_furniture_receipt
    from services.svg_common_header_generation import _page_input_digest
    from services.svg_common_header_design_checkpoint import _candidate_key
    from services import svg_common_header_design_checkpoint as header_checkpoints
    from services.workspace_svg_header_body_remeasurement import (
        remeasure_header_body, render_remeasured_body_candidate, require_body_remeasurement_witness,
    )
    from utils.hashing import sha256_hex, canonical_json_digest
    from utils.owner_context import (set_current_owner_id, reset_current_owner_id,
        set_current_tenant_id, reset_current_tenant_id)
    png = b"stable-render"
    png_hash = sha256_hex(png)
    furniture = master_furniture_receipt(wrapped_final, snapshot, index=0, n=1,
        width=1280, height=720, header_page=bindings[0])
    current_quality = dict(passed=True, verified=True, classification="passed", failures=[],
        measurement_complete=True, geometry_passed=True, source_digest=source_digest(wrapped_final),
        png_digest="sha256:" + png_hash, browser_metrics={"elements": valid_metrics},
        trusted_font_materialization={"worker_materialized": True})
    apply_common_header_v2_quality(current_quality, wrapped_final, contract, bindings[0], furniture)
    previous_quality = deepcopy(current_quality)
    previous_quality["common_header"]["verifier_digest"] = "sha256:" + "a" * 64
    receipt = dict(input_digest="frozen-source-input", source_digest=sha256_hex(wrapped_final.encode()),
        system_furniture=furniture, quality=previous_quality, png_sha256=png_hash, png_path="old.png")
    owner_id, presentation_id, operation_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    row = SimpleNamespace(id=uuid.uuid4(), owner_id=owner_id, presentation=presentation_id,
        index=0, source_format="svg", svg_content=wrapped_final, content={"svgReceipt": receipt})
    owner = SimpleNamespace(id=operation_id, owner_id=owner_id, presentation_id=presentation_id,
        owner_scope=str(owner_id), kind="design.apply", state="running", attempt_token=uuid.uuid4(),
        fencing_token=1, expected_revision=4, request_hash="new-explicit-request",
        request_payload={"principal_tenant_id": "fresh-body-tenant"})
    parent = dict(revision=4, theme={"svg": {"styleSnapshot": deepcopy(snapshot)}},
        outline=outline.model_dump(mode="json"), pages=[dict(id=str(row.id),
            svg_content=wrapped_final, content=deepcopy(row.content))])
    ctx = SimpleNamespace(snapshot=deepcopy(snapshot), count=1,
        canvas=SimpleNamespace(width_px=1280, height_px=720), source_reflow=False,
        raster_reconstruction=False, canonical_assets=False, manifests=[{}],
        source_font_profile=None, physical_kwargs={}, render_semaphore=asyncio.Semaphore(1))
    ctx.header_pages = bindings
    ctx.snapshot["common_header_design_plan"] = {"max_render_seconds": 60}
    captures, stored = [], {}

    async def capture(svg, **kwargs):
        captures.append(svg)
        return svg_renderer.SvgRenderResult(png, deepcopy(current_quality), "wrapper", source_digest(svg))

    async def save_candidate(active_owner, page_index, *, identity, payload=None, **kwargs):
        assert active_owner is owner and page_index == 0
        key = _candidate_key(page_index, identity)
        if payload is not None:
            stored[key] = deepcopy(payload)
        return deepcopy(stored.get(key, {}))

    monkeypatch.setattr(svg_renderer, "render_svg_with_metrics", capture)
    monkeypatch.setattr(svg_presentation_checkpoint, "_read_png",
        lambda value: png if value.get("png_sha256") == png_hash else None)
    monkeypatch.setattr(svg_presentation_checkpoint, "_write_png",
        lambda value: dict(png_sha256=sha256_hex(value), png_path="new.png"))
    monkeypatch.setattr(header_checkpoints, "candidate_page_checkpoint", save_candidate)
    before = deepcopy(row.content)
    owner_token, tenant_token = set_current_owner_id(owner_id), set_current_tenant_id("fresh-body-tenant")
    try:
        async def verify_remeasurement():
            body, preservation, witness = await remeasure_header_body(row, owner=owner,
                ctx=ctx, request=request, outline=outline, index=0, parent=parent, session_factory=None)
            assert row.content == before and len(captures) == 1
            assert witness["source_receipt_digest"] == canonical_json_digest(receipt, allow_nan=True)
            assert witness["previous_verifier_digest"] != witness["current_verifier_digest"]
            identity = {"candidate_revision": "1", "candidate_id": "fresh-local", "page_id": bindings[0]["page_id"]}
            seeded = dict(svg_content=body, author_body_svg=body, source_preservation=preservation,
                input_digest=_page_input_digest(ctx, request, outline, request.language, 0),
                attempts=[{"kind": "local", "receipt": {"version": "svg-common-header-body-reuse/v2",
                    "body_remeasurement_digest": witness["digest"]}}],
                body_reuse_remeasurement=witness)
            result, _ = await render_remeasured_body_candidate(ctx, request, outline, owner, 0,
                identity=identity, saved=seeded, parent=parent, session_factory=None)
            assert result["svg_content"] == wrapped_final and len(captures) == 2
            assert all(attempt["kind"] == "local" for attempt in result["attempts"])
            require_body_remeasurement_witness(result, owner=owner, index=0, parent=parent)
            assert row.content == before and previous_quality["common_header"]["verifier_digest"] == "sha256:" + "a" * 64
            for field in ("body", "source", "owner", "verifier", "native", "removed"):
                altered = deepcopy(result)
                proof = altered["body_reuse_remeasurement"]
                if field == "body":
                    altered["author_body_svg"] += "changed"
                    proof["body_digest"] = canonical_json_digest(altered["author_body_svg"])
                elif field == "source":
                    proof["source_input_digest"] = "other-input"
                elif field == "owner":
                    proof["owner_id"] = str(uuid.uuid4())
                elif field == "native":
                    altered["nativeDeclarations"] = {"objects": []}
                elif field == "verifier":
                    proof["current_verifier_digest"] = proof["previous_verifier_digest"]
                proof["digest"] = canonical_json_digest({k: v for k, v in proof.items() if k != "digest"}, allow_nan=True)
                altered["attempts"][0]["receipt"]["body_remeasurement_digest"] = proof["digest"]
                if field == "removed":
                    altered.pop("body_reuse_remeasurement")
                with pytest.raises(ValueError, match="body_remeasurement_unproven"):
                    require_body_remeasurement_witness(altered, owner=owner, index=0, parent=parent)
            # Re-signing both declaration copies cannot change the immutable
            # native publication. Isolate only the native projection boundary.
            from services.generation_core.drivers.svg import native_publish
            native_parent, native_payload = deepcopy(parent), deepcopy(result)
            native_receipt = native_parent["pages"][0]["content"]["svgReceipt"]
            native_receipt["nativeObjects"] = {"accepted": 1}
            native_parent["pages"][0]["content"]["nativeObjects"] = {"cell": "original"}
            native_proof = native_payload["body_reuse_remeasurement"]
            native_payload["nativeDeclarations"] = {"cell": "original"}
            native_proof["source_native_declarations"] = {"cell": "original"}
            native_proof["source_receipt_digest"] = canonical_json_digest(native_receipt, allow_nan=True)

            def reseal_native():
                native_proof["native_declarations_digest"] = canonical_json_digest(native_payload["nativeDeclarations"])
                native_proof["digest"] = canonical_json_digest(
                    {k: v for k, v in native_proof.items() if k != "digest"}, allow_nan=True)
                native_payload["attempts"][0]["receipt"]["body_remeasurement_digest"] = native_proof["digest"]

            def project_native(value, **kwargs):
                assert value["svg_content"] == wrapped_final
                return deepcopy(value["nativeDeclarations"]), {"accepted": 1}

            with monkeypatch.context() as native_patch:
                native_patch.setattr(native_publish, "page_native_objects", project_native)
                reseal_native()
                require_body_remeasurement_witness(native_payload, owner=owner, index=0, parent=native_parent)
                native_payload["nativeDeclarations"]["cell"] = "changed"
                native_proof["source_native_declarations"]["cell"] = "changed"
                reseal_native()
                with pytest.raises(ValueError, match="body_remeasurement_unproven"):
                    require_body_remeasurement_witness(native_payload, owner=owner, index=0, parent=native_parent)
            bad = deepcopy(row)
            bad.content["svgReceipt"]["quality"]["geometry_passed"] = False
            with pytest.raises(ValueError, match="body_remeasurement_unproven"):
                await remeasure_header_body(bad, owner=owner, ctx=ctx, request=request,
                    outline=outline, index=0, parent=parent, session_factory=None)
            assert len(captures) == 2
            async def failed_capture(svg, **kwargs):
                captures.append(svg)
                quality = deepcopy(current_quality)
                quality.update(passed=False, failures=[{"kind": "required_text_missing"}])
                return svg_renderer.SvgRenderResult(png, quality, "wrapper", source_digest(svg))
            monkeypatch.setattr(svg_renderer, "render_svg_with_metrics", failed_capture)
            with pytest.raises(ValueError, match="body_remeasurement_unproven"):
                await remeasure_header_body(row, owner=owner, ctx=ctx, request=request,
                    outline=outline, index=0, parent=parent, session_factory=None)
            assert len(captures) == 3 and row.content == before

        asyncio.run(verify_remeasurement())
    finally:
        reset_current_tenant_id(tenant_token)
        reset_current_owner_id(owner_token)


def test_header_v2_metadata_preserves_exact_values_provenance_and_admitted_input():
    from copy import deepcopy
    from models.common_header_design import CommonHeaderDesignInput
    from models.presentation_outline_model import PresentationOutlineModel, SlideOutlineModel
    from services.authored_canvas import request_canvas_metadata
    from services.presentation_generation_staging import _durable_request_payload, _request_from_durable_payload
    from services.svg_common_header_design_plan import require_header_design_plan
    from services.svg_common_header_v2 import prepare_common_header_v2
    from services.svg_common_header_values import prepare_header_page_values
    from services.workspace_svg_header_revision_inputs import merge_header_revision_values

    request = _v2_header_request()
    request.common_header_design = CommonHeaderDesignInput(document_values={"document_name": "  확정 문서명  "},
        page_values=[{"page_index": 0, "values": {"section": "연구 01"}}])
    outline = PresentationOutlineModel(slides=[SlideOutlineModel(
        page_id="metadata-page", content="원문 그대로", title="운영 현황")])
    values = prepare_header_page_values(request, outline,
        source_manifest=SimpleNamespace(document_title="원본 제목", manifest_id="source-one", manifest_digest="pinned"))
    assert values[0]["page_id"] == "metadata-page"
    document = values[0]["values"]["document_name"]
    assert document["value"] == "  확정 문서명  " and document["user_fixed"] is True
    assert document["source_ref"].startswith("request:#/common_header_design/document_values/document_name@sha256:")
    assert values[0]["values"]["section"]["source_ref"].startswith(
        "request:#/common_header_design/page_values/0/values/section@sha256:")

    def field(node, ref, x, y, width, size):
        return {"node_id": node, "role": ref, "value_ref": ref,
            "box": {"x": x, "y": y, "width": width, "height": 60 if size == 32 else 35},
            "font_family": "Noto Sans KR", "font_weight": 700 if size == 32 else 400,
            "font_size_px": size, "line_height_px": size * 1.4,
            "baseline_offset_px": size + 4, "max_lines": 1, "alignment": "left", "optional": False}

    candidate = {"candidate_id": "actual-metadata", "revision": 1,
        "svg": '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720"><text id="heading" fill="#111111"/><text id="document" fill="#111111"/><text id="section" fill="#111111"/></svg>',
        "body_box": {"x": 64, "y": 170, "width": 1152, "height": 500},
        "fields": [field("heading", "title", 64, 25, 1152, 32),
                   field("document", "document_name", 64, 110, 600, 16),
                   field("section", "section", 850, 110, 350, 16)]}
    _, bindings = prepare_common_header_v2(candidate, outline, request_canvas_metadata(request),
        snapshot={}, page_values=values)
    assert [item["value"] for item in bindings[0]["fields"]] == ["운영 현황", "  확정 문서명  ", "연구 01"]
    previous = deepcopy(outline)
    previous.slides[0].common_header = deepcopy(bindings[0])
    fixed = {key: values[0]["values"][key] for key in ("document_name", "section")}
    for raw, retained in (({}, {"document_name", "section"}),
                          ({"document_values": {}}, {"section"}),
                          ({"page_values": []}, {"document_name"}),
                          ({"document_values": {}, "page_values": []}, set())):
        prepared = prepare_header_page_values(SimpleNamespace(common_header_design=raw), previous)
        merged, carrier, lineage = merge_header_revision_values(raw, previous, prepared, parent_revision=1)
        actual_fixed = {key: bound for key, bound in merged[0]["values"].items() if bound["user_fixed"]}
        assert actual_fixed == {key: fixed[key] for key in retained}
        assert carrier == {
            "document_values": {"document_name": document["value"]} if "document_name" in retained else {},
            "page_values": [{"page_index": 0, "values": {"section": "연구 01"}}] if "section" in retained else [],
        }
        assert {item["value_ref"] for item in lineage} == retained
        assert all(item["page_id"] == "metadata-page" and item["parent_revision"] == 1
                   and item["source_ref"] == fixed[item["value_ref"]]["source_ref"] for item in lineage)
        if not raw:
            _, inherited = prepare_common_header_v2(candidate, previous, request_canvas_metadata(request),
                snapshot={}, page_values=merged)
            assert {item["value_ref"]: {key: item[key] for key in ("value", "source_ref", "user_fixed")}
                    for item in inherited[0]["fields"] if item["user_fixed"]} == fixed
    missing_fixed = deepcopy(candidate)
    missing_fixed["fields"] = [field for field in candidate["fields"] if field["value_ref"] != "document_name"]
    missing_fixed["svg"] = candidate["svg"].replace('<text id="document" fill="#111111"/>', "")
    with pytest.raises(ValueError, match="svg_common_header_fixed_values_missing"):
        prepare_common_header_v2(missing_fixed, outline, request_canvas_metadata(request),
            snapshot={}, page_values=values)
    restored = _request_from_durable_payload(_durable_request_payload(request))
    assert prepare_header_page_values(restored, outline) == values
    restored.common_header_design.document_values["document_name"] = "다른 고정 요구"
    with pytest.raises(ValueError, match="design_plan_conflict"):
        require_header_design_plan(restored)
    for design in ({"document_values": {"title": "덮어쓰기"}},
                   {"page_values": [{"page_index": 1, "values": {"section": "범위 밖"}}]},
                   {"document_values": {"section": "문서"},
                    "page_values": [{"page_index": 0, "values": {"section": "페이지"}}]}):
        with pytest.raises(ValueError):
            prepare_header_page_values(SimpleNamespace(common_header_design=design), outline)
    empty = CommonHeaderDesignInput().model_dump(mode="json")
    assert "document_values" not in empty and "page_values" not in empty
    explicit_empty = CommonHeaderDesignInput(document_values={}, page_values=[]).model_dump(mode="json")
    assert explicit_empty["document_values"] == {} and explicit_empty["page_values"] == []
    fallback = prepare_header_page_values(SimpleNamespace(common_header_design=None), deepcopy(outline),
        presentation=SimpleNamespace(id="current-deck", revision=7, title="  실제 현재 제목  "), expected_revision=7)
    assert fallback[0]["values"]["document_name"]["value"] == "  실제 현재 제목  "
    assert fallback[0]["values"]["document_name"]["user_fixed"] is False

    from services.ppt_writing_visible import assess_visible_nodes, literal_source_values
    fixed_symbols = "원문 문서명 — 상단 · 보조"
    symbol_values = prepare_header_page_values(SimpleNamespace(common_header_design={
        "document_values": {"document_name": fixed_symbols},
        "page_values": [{"page_index": 0, "values": {"section": "연구 01"}}]}), outline)
    symbol_contract, symbol_bindings = prepare_common_header_v2(candidate, outline,
        request_canvas_metadata(request), snapshot={}, page_values=symbol_values)
    symbols = literal_source_values(header_contract=symbol_contract, header_binding=symbol_bindings[0])
    assert fixed_symbols in symbols and "운영 현황" not in symbols
    assert assess_visible_nodes([fixed_symbols], source_values=symbols)["hard_violation_counts"] == {}
    assert assess_visible_nodes([fixed_symbols, "AI·추가 — 개선"], source_values=symbols)["hard_violation_counts"] == {
        "middle_dot": 1, "long_dash": 1}
    altered_binding = deepcopy(symbol_bindings[0])
    altered_binding["fields"][1]["value"] = "변조 — 문구"
    assert literal_source_values(header_contract=symbol_contract, header_binding=altered_binding) == ()
    wrapped_candidate = deepcopy(candidate)
    wrapped_candidate["fields"][1]["box"].update(width=120, height=60)
    wrapped_candidate["fields"][1]["max_lines"] = 2
    wrapped_contract, wrapped_bindings = prepare_common_header_v2(wrapped_candidate, outline,
        request_canvas_metadata(request), snapshot={}, page_values=symbol_values)
    wrapped_lines = wrapped_bindings[0]["fields"][1]["lines"]
    assert len(wrapped_lines) == 2
    wrapped_symbols = literal_source_values(header_contract=wrapped_contract, header_binding=wrapped_bindings[0])
    assert assess_visible_nodes([line.strip() for line in wrapped_lines], source_values=wrapped_symbols)["hard_violation_counts"] == {}
    assert assess_visible_nodes(["AI·추가 — 개선"], source_values=wrapped_symbols)["hard_violation_counts"] == {
        "middle_dot": 1, "long_dash": 1}
    from services.ppt_writing_visible import svg_text_nodes, svg_writing_repair_witnesses
    repair_svg = ('<svg xmlns="http://www.w3.org/2000/svg">'
        '<text id="protected">' + fixed_symbols + '</text>'
        '<text id="repair-target">AI·추가 — 개선</text>'
        '<metadata><text>숨김·문구</text></metadata></svg>')
    writing_before = assess_visible_nodes(svg_text_nodes(repair_svg), source_values=wrapped_symbols)
    witnesses = svg_writing_repair_witnesses(repair_svg, source_values=wrapped_symbols)
    assert len(witnesses) == 1
    assert witnesses[0]["qid"] == "svg-2" and witnesses[0]["node_id"] == "repair-target"
    assert witnesses[0]["text"] == "AI·추가 — 개선" and witnesses[0]["frozen_header"] is False
    assert witnesses[0]["symbols"] == [
        {"category": "middle_dot", "start": 2, "end": 3},
        {"category": "long_dash", "start": 6, "end": 7}]
    assert assess_visible_nodes(svg_text_nodes(repair_svg), source_values=wrapped_symbols) == writing_before
    long_witness = svg_writing_repair_witnesses('<svg xmlns="http://www.w3.org/2000/svg"><text>'
        + "가" * 300 + "·" + "나" * 300 + '</text></svg>', max_text_chars=100)[0]
    assert len(long_witness["text"]) == 100 and long_witness["text_start"] > 0
    assert long_witness["text"][long_witness["symbols"][0]["start"]] == "·"

    import json
    import struct
    from pathlib import Path
    from llmai.shared import SystemMessage, UserMessage
    from services.generation_core.authoring.author_slide_svg import SVG_AUTHOR_RULES
    from services.generation_core.quality.vision_advisory import _design_input_sizes
    from services.svg_common_header_generation import _design_input, _selection_input
    from utils.hashing import canonical_json_digest
    from utils.llm_calls.svg_common_header_design import (
        DESIGN_INPUT_PROJECTION_VERSION, DESIGN_RULES, HEADER_SVG_RULES, SELECTION_RULES,
        _design_prompt_payload, candidate_schema, selection_schema,
    )
    from jsonschema import Draft202012Validator, ValidationError
    response_example = {"candidates": []}
    for index in range(2):
        example = deepcopy(candidate)
        example.update(candidate_id=f"schema-{index}", design_intent="source hierarchy",
            shared_style={"body_typography": "readable", "spacing_and_hierarchy": "source-led", "colour_usage": "tokens"})
        for item in example["fields"]:
            item["dependent_node_ids"] = []
        response_example["candidates"].append(example)
    schema_validator = Draft202012Validator(candidate_schema({"available_value_refs": sorted(values[0]["values"])}))
    schema_validator.validate(response_example)
    for field_change in ({"role": "page_title"}, {"optional": True}):
        altered = deepcopy(response_example)
        altered["candidates"][0]["fields"][0].update(field_change)
        with pytest.raises(ValidationError):
            schema_validator.validate(altered)
    free_metadata = deepcopy(response_example)
    free_metadata["candidates"][0]["fields"][1]["role"] = "free_metadata_role"
    schema_validator.validate(free_metadata)
    assert 'role="title" and optional=false' in DESIGN_RULES
    assert "physically readable text" in DESIGN_RULES

    long_name = "정확한 장문 문서명과 공백을 보존하는 운영 계획 " * 4
    long_request = _v2_header_request()
    long_request.common_header_design = CommonHeaderDesignInput(document_values={"document_name": long_name})
    frozen_long = _request_from_durable_payload(_durable_request_payload(long_request))
    assert frozen_long.common_header_design.document_values["document_name"] == long_name
    frozen_long.common_header_design.document_values["document_name"] = long_name + "변조"
    with pytest.raises(ValueError, match="design_plan_conflict"):
        require_header_design_plan(frozen_long)

    batch_outline = PresentationOutlineModel(slides=[SlideOutlineModel(
        page_id=f"projection-{i}", title=f"운영 계획 {i}", content="원문 조건 보존 " * 100)
        for i in range(12)])
    batch_request = _v2_header_request()
    batch_request.common_header_design = CommonHeaderDesignInput(document_values={"document_name": long_name})
    batch_values = prepare_header_page_values(batch_request, batch_outline)
    batch_snapshot = {"tokens": {"primary": "#123456", "heading_font": "Noto Sans KR"},
        "art_direction": {"reference_guidance": "Preserve brand typography and source relationships. " * 140},
        "common_header_design_plan": {"execution": "reserved " * 80},
        "quality_recovery_plan": {"execution": "reserved " * 60}}
    batch_plan = {"plan_digest": "sha256:" + "b" * 64, "seed": "c" * 32,
        "capability_version": "svg-static/v2", "max_svg_bytes": 262144, "max_svg_nodes": 2048,
        "max_render_seconds": 60, "selection_image_max_pixels": 8388608,
        "selection_image_max_bytes": 8388608, "selection_image_max_count": 2}
    batch_index = 0
    # Local acceptance runs additionally replay the retained real twelve-page
    # input. CI keeps the equally dense in-memory source/value/style fixture.
    artifact = Path(__file__).resolve().parents[5] / "delivery-backups/svg-header-completion-20261017/baseline12-paired-failed-job.json"
    if artifact.is_file():
        saved = json.loads(artifact.read_text(encoding="utf-8-sig"))
        batch_request = SimpleNamespace(**saved["request_payload"])
        batch_outline = PresentationOutlineModel.model_validate(saved["authored_outline"])
        batch_snapshot = saved["svg_style_snapshot"]
        batch_plan = batch_snapshot["common_header_design_plan"]
        document = saved["svg_header_design_checkpoint"]["document"]
        batch_values = document["header_page_values"]
        batch_index = document["representative_index"]
    ctx = SimpleNamespace(snapshot=batch_snapshot, canvas=SimpleNamespace(width_px=1280, height_px=720),
        header_page_values=batch_values, count=len(batch_outline.slides), source_reflow=False,
        roles=["content"] * len(batch_outline.slides), manifests=[{} for _ in batch_outline.slides])
    original_payload = _design_input(ctx, batch_request, batch_outline, batch_plan, batch_index)
    original_payload.update(candidate_revision=1, reference_images=[])
    retained = deepcopy(original_payload)

    def context_bytes(payload, rules, schema, *, compact):
        encoded = json.dumps(payload, ensure_ascii=False, **({"separators": (",", ":")} if compact else {}))
        return _design_input_sizes([SystemMessage(content=rules), UserMessage(content=encoded)], schema)["serializedContextBytes"]

    assert context_bytes(original_payload, DESIGN_RULES, candidate_schema(original_payload), compact=False) + 1024 > 32768
    for defects in (None, [{"candidate_id": "first", "error": "svg_common_header_field_box_invalid"},
                            {"candidate_id": "second", "error": "svg_common_header_font_invalid"}]):
        raw = deepcopy(original_payload)
        if defects is not None:
            raw["known_defects"] = defects
        projected = _design_prompt_payload(raw)
        for key in ("available_values", "pages", "representative", "header_requirements"):
            assert projected[key] == raw[key]
        assert projected["style"] == {key: value for key, value in raw["style"].items()
                                      if key not in {"common_header_design_plan", "quality_recovery_plan"}}
        assert projected["capability"] == {**raw["capability"], "svg_rules": HEADER_SVG_RULES}
        assert projected["input_projection"] == {"version": DESIGN_INPUT_PROJECTION_VERSION,
                                                "source_context_digest": canonical_json_digest(raw)}
        assert context_bytes(projected, DESIGN_RULES, candidate_schema(raw), compact=True) + 1024 <= 32768
        if defects is not None:
            assert projected["known_defects"] == defects
    assert original_payload == retained and original_payload["capability"]["svg_rules"] == SVG_AUTHOR_RULES
    candidates = [deepcopy(candidate), {**deepcopy(candidate), "candidate_id": "second"}]
    png = b"\x89PNG\r\n\x1a\n" + b"\0\0\0\rIHDR" + struct.pack(">II", 1280, 720)
    pages = {item["candidate_id"]: {"eligible": True, "payload": {
        "quality": {"passed": True, "failures": []}, "svg_content": item["svg"],
        "input_digest": "unchanged-body", "source_preservation": {"rendering_verified": True}}}
        for item in candidates}
    selected_input, screens = _selection_input(ctx, batch_request, batch_outline, batch_plan,
        {"selector_capability": {"provider": "test", "model": "test"}}, candidates, pages, batch_index, lambda _: png)
    assert screens == [png, png] and selected_input["available_values"] == batch_values
    assert context_bytes(selected_input, SELECTION_RULES, selection_schema(), compact=True) + 1024 <= 32768
    rejected_artifact = artifact.with_name("baseline12-projected-failed-job.json")
    if rejected_artifact.is_file():
        from services.ppt_writing_visible import svg_text_nodes
        from services.svg_common_header import header_snapshot_base
        saved = json.loads(rejected_artifact.read_text(encoding="utf-8-sig"))
        retained_failure = deepcopy(saved["last_error"])
        rejected_outline = PresentationOutlineModel.model_validate(saved["authored_outline"])
        checkpoint = saved["svg_header_design_checkpoint"]
        document = checkpoint["document"]
        designs = {item["candidate_id"]: item for item in document["steps"]["design-1"]["response"]["result"]["candidates"]}
        outcomes = []
        for page in checkpoint["candidate_pages"].values():
            design = designs[page["header_author_attempt_identity"]["candidate_id"]]
            contract, bindings = prepare_common_header_v2(design, rejected_outline,
                request_canvas_metadata(request), snapshot=header_snapshot_base(saved["svg_style_snapshot"]),
                page_values=document["header_page_values"])
            source_values = literal_source_values(saved["request_payload"]["content"], page.get("nativeRequirements"),
                (page.get("source_preservation") or {}).get("bindings") or (), page.get("rasterContentLedger"),
                header_contract=contract, header_binding=bindings[document["representative_index"]])
            before = deepcopy(page["quality"])
            after = assess_visible_nodes(svg_text_nodes(page["svg_content"]), source_values=source_values)
            assert "long_dash" not in after["hard_violation_counts"]
            assert page["quality"] == before and before["writing_policy"]["hard_violation_counts"]["long_dash"] == 1
            outcomes.append(after["hard_violation_counts"])
        assert {} in outcomes and {"middle_dot": 11} in outcomes
        assert saved["last_error"] == retained_failure

    import uuid
    from services.svg_common_header_transport import (
        TRANSPORT_RULES, pack_header_transport, restore_header_transport,
    )
    for count in (12, 80):
        dense_outline = PresentationOutlineModel(slides=[SlideOutlineModel(
            page_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"transport-fixture-{index}")),
            title=batch_outline.slides[index % len(batch_outline.slides)].title + f" 합성 구분 {index + 1}",
            content=batch_outline.slides[index % len(batch_outline.slides)].content + f"\n합성 원문 구분 {index + 1}")
            for index in range(count)])
        dense_values = prepare_header_page_values(batch_request, dense_outline)
        dense_ctx = SimpleNamespace(**{**vars(ctx), "header_page_values": dense_values,
            "count": count, "roles": ["content"] * count, "manifests": [{}] * count})
        dense_raw = _design_input(dense_ctx, batch_request, dense_outline, batch_plan, 8)
        dense_raw.update(candidate_revision=1, reference_images=[])
        unchanged_digest = canonical_json_digest(dense_raw)
        for repair in (False, True):
            raw = deepcopy(dense_raw)
            if repair:
                raw["known_defects"] = [{"candidate_id": "first", "error": "svg_common_header_field_box_invalid"},
                                        {"candidate_id": "second", "error": "svg_common_header_font_invalid"}]
            scoped = _design_prompt_payload(raw)
            packed, proof = pack_header_transport(scoped)
            assert pack_header_transport(scoped) == (packed, proof)
            restored = restore_header_transport(packed, proof["source_mapping"],
                expected_context_digest=canonical_json_digest(scoped))
            assert restored == scoped
            assert restored["available_values"] == dense_values
            assert packed["style"] == scoped["style"]
            assert packed["representative"]["source"] == scoped["representative"]["source"]
            assert proof["projected_context_digest"] == canonical_json_digest(packed)
            assert proof["source_mapping_digest"] == canonical_json_digest(proof["source_mapping"])
            assert context_bytes(packed, DESIGN_RULES + TRANSPORT_RULES, candidate_schema(raw), compact=True) + 1024 <= 32768
            legacy = deepcopy(packed)
            legacy["transport_source_mapping"]["version"] = "svg-header-source-handles/v1"
            legacy["available_values"]["field_columns"][2] = "user_fixed"
            for fields in legacy["available_values"]["rows"]:
                for field in fields:
                    field[2] = bool(field[2])
            assert restore_header_transport(legacy, proof["source_mapping"],
                expected_context_digest=canonical_json_digest(scoped)) == scoped
            tampered_mapping = deepcopy(proof["source_mapping"])
            first_handle = next(iter(tampered_mapping["r"]))
            tampered_mapping["r"][first_handle] += "changed"
            for recompute_digest in (False, True):
                tampered = deepcopy(packed)
                if recompute_digest:
                    tampered["transport_source_mapping"]["digest"] = canonical_json_digest(tampered_mapping)
                with pytest.raises(ValueError, match="svg_common_header_transport_input_invalid"):
                    restore_header_transport(tampered, tampered_mapping,
                        expected_context_digest=canonical_json_digest(scoped))
            tampered = deepcopy(packed)
            tampered["shared_value_dictionary"][0] += "changed"
            with pytest.raises(ValueError, match="svg_common_header_transport_input_invalid"):
                restore_header_transport(tampered, proof["source_mapping"],
                    expected_context_digest=canonical_json_digest(scoped))
        assert canonical_json_digest(dense_raw) == unchanged_digest
        selection, screens = _selection_input(dense_ctx, batch_request, dense_outline, batch_plan,
            {"selector_capability": {"provider": "test", "model": "test"}}, candidates, pages, 8, lambda _: png)
        frozen_selection = deepcopy(selection)
        packed, proof = pack_header_transport(selection)
        assert restore_header_transport(packed, proof["source_mapping"],
            expected_context_digest=canonical_json_digest(selection)) == frozen_selection
        assert selection == frozen_selection and screens == [png, png]
        assert context_bytes(packed, SELECTION_RULES + TRANSPORT_RULES, selection_schema(), compact=True) + 1024 <= 32768
    source_handle_candidate = deepcopy(candidate)
    source_handle_candidate["fields"][0]["value_ref"] = first_handle
    with pytest.raises(ValueError, match="svg_common_header"):
        prepare_common_header_v2(source_handle_candidate, outline, request_canvas_metadata(request),
            snapshot={}, page_values=values)
    import asyncio
    from services.svg_common_header_generation import _paid_step
    prepared_proof = {**proof, "source_context_digest": canonical_json_digest(selection)}
    records, calls = [], []
    durable_document = {"steps": {}}

    async def save_input():
        records.append(deepcopy(durable_document))

    async def unknown_call():
        calls.append("entered")
        raise RuntimeError("unknown provider outcome")

    unknown_call.input_projection = deepcopy(prepared_proof)
    unknown_call.input_envelope = {"serializedContextBytes": 123}
    async def call_once():
        return await _paid_step(durable_document, key="selection-1", stage="selection", ordinal=1,
            payload=selection, images=screens, plan={"header_call_limit": 4},
            job=SimpleNamespace(id=uuid.uuid4(), attempt_number=1, public_only=False), save=save_input)

    with patch("services.svg_common_header_generation.build_header_call", return_value=unknown_call):
        with pytest.raises(RuntimeError, match="unknown provider outcome"):
            asyncio.run(call_once())
        assert records[0]["steps"]["selection-1"]["status"] == "started"
        assert records[0]["steps"]["selection-1"]["input_projection"] == prepared_proof
        assert records[-1]["steps"]["selection-1"]["status"] == "unknown"
        assert records[-1]["steps"]["selection-1"]["input_projection"] == prepared_proof
        with pytest.raises(ValueError, match="svg_common_header_provider_result_unknown"):
            asyncio.run(call_once())
        assert calls == ["entered"]


def test_header_v2_pending_owned_preparation_persists_titles_and_reuses_stable_page_ids(monkeypatch):
    import asyncio
    from copy import deepcopy
    from unittest.mock import AsyncMock, Mock
    from models.presentation_outline_model import PresentationOutlineModel, SlideOutlineModel
    from services.authored_canvas import request_canvas_metadata
    from services.presentation_generation_staging import _durable_request_payload, _request_from_durable_payload
    from services.svg_presentation_prepare import _pin_owned_svg_style

    outline = PresentationOutlineModel(slides=[
        SlideOutlineModel(content="# 운영 현황\n원본 운영 값을 유지한다."),
        SlideOutlineModel(content="# 원문 제목\n다음 계획을 설명한다.", title="사용자 고정 제목"),
    ])
    request = _v2_header_request()
    request.slides_markdown = [slide.content for slide in outline.slides]
    request.n_slides = len(outline.slides)
    payload = _durable_request_payload(request)
    frozen = deepcopy(payload["_internal_svg_style_snapshot"])
    current = SimpleNamespace(id=uuid.uuid4(), svg_style_snapshot=frozen, authored_outline=None,
        svg_header_design_checkpoint=None, request_payload=deepcopy(payload))
    stale_job = SimpleNamespace(id=current.id, attempt_token="live-preparation", _workspace_fencing_token=11,
        svg_style_snapshot={"digest": "stale-caller-object"})
    checkpoint_session = AsyncMock()
    checkpoint_session.add = Mock()
    checkpoint_session.__aenter__.return_value = checkpoint_session
    session_maker = Mock(return_value=checkpoint_session)
    owned_job = AsyncMock(return_value=current)
    pin_style = Mock(side_effect=lambda selected: selected._svg_style_snapshot)
    committed = []

    async def observe_commit():
        committed.append(deepcopy(current.authored_outline))

    checkpoint_session.commit.side_effect = observe_commit
    monkeypatch.setattr("services.svg_generation_admission.prepare_private_svg_recovery_authority",
        AsyncMock(return_value=[]))

    async def scenario():
        assert "common_header" not in frozen
        await _pin_owned_svg_style(request, stale_job, pin_style=pin_style, session_maker=session_maker,
            owned_job=owned_job, outline=outline, canvas=request_canvas_metadata(request))
        persisted = PresentationOutlineModel.model_validate(committed[0])
        ids = [slide.page_id for slide in persisted.slides]
        assert len(set(ids)) == len(ids) and all(uuid.UUID(page_id).version == 5 for page_id in ids)
        assert [slide.title for slide in persisted.slides] == ["운영 현황", "사용자 고정 제목"]
        assert [slide.page_id for slide in outline.slides] == ids
        assert [slide.content for slide in persisted.slides] == request.slides_markdown
        assert all(slide.common_header is None for slide in persisted.slides)

        restored = _request_from_durable_payload(deepcopy(payload))
        resume_outline = PresentationOutlineModel.model_validate(deepcopy(current.authored_outline))
        current.svg_header_design_checkpoint = {"version": "svg-common-header-design-checkpoint/v2",
                                                "document": {"status": "preparing"}}
        await _pin_owned_svg_style(restored, stale_job, pin_style=pin_style, session_maker=session_maker,
            owned_job=owned_job, outline=resume_outline, canvas=request_canvas_metadata(restored))
        assert committed[1] == committed[0]
        assert [slide.page_id for slide in resume_outline.slides] == ids
        assert current.svg_style_snapshot == frozen
        owned_job.assert_awaited_with(checkpoint_session, current.id, "live-preparation", fencing_token=11)
        assert checkpoint_session.commit.await_count == 2

    asyncio.run(scenario())


def test_partial_recovery_page_input_digest_matches_new_and_legacy_svg_inputs():
    from services.svg_partial_recovery import _expected_page_input_digest
    from services.svg_presentation_checkpoint import _digest
    from services.ppt_writing_policy import WRITING_POLICY_ID

    legacy = GeneratePresentationRequest(content="Quarterly update")
    current = legacy.model_copy(update={"writing_policy": WRITING_POLICY_ID})
    job = SimpleNamespace(
        authored_outline={"slides": [{"content": "Page one"}]},
        svg_style_snapshot={"digest": "frozen-style"},
        total_slides=1,
    )
    page_assets = {"asset-1": {"sha256": "frozen-asset"}}
    page_input = {
        "content": "Page one", "style": job.svg_style_snapshot, "index": 0,
        "count": 1, "instructions": None, "language": None, "preset": None,
    }
    legacy_digest = _digest({"page": _digest(page_input), "assets": page_assets})
    current_digest = _digest({
        "page": _digest({**page_input, "writing_policy": WRITING_POLICY_ID}),
        "assets": page_assets,
    })

    assert _expected_page_input_digest(legacy, job, 0, page_assets) == legacy_digest
    assert _expected_page_input_digest(current, job, 0, page_assets) == current_digest
    assert current_digest != legacy_digest

    # A frozen common header must bind the same title, content and source in
    # initial authoring and durable recovery; changing either is not a replay.
    from copy import deepcopy
    from models.presentation_outline_model import PresentationOutlineModel, SlideOutlineModel
    from services.authored_canvas import canvas_metadata_for_aspect_ratio
    from services.svg_common_header import prepare_common_header
    from services.svg_generation_inputs import svg_page_input_digest
    from services.svg_contract import finalize_svg

    outline = PresentationOutlineModel(slides=[
        SlideOutlineModel(content="원본 운영 현황", title="운영 현황"),
        SlideOutlineModel(content="승인 전 수정본은 비공개다.",
            title="승인 전 수정본은 공개하지 않고 게시본을 유지한다"),
    ])
    source_style = {"tokens": {}, "common_header": {"title_width_px": 470}}
    before = deepcopy(source_style)
    canvas = canvas_metadata_for_aspect_ratio("16:9")
    contract, bindings = prepare_common_header(source_style, outline, canvas)
    assert source_style == before
    assert len(bindings[0]["lines"]) == 1
    assert len(bindings[1]["lines"]) == 2
    assert contract["title_slot_lines"] == 2
    style = {"tokens": {}, "common_header": contract}
    frozen = SimpleNamespace(svg_style_snapshot=style, total_slides=2,
        authored_outline={"slides": [
            {"content": slide.content, "title": slide.title, "common_header": binding}
            for slide, binding in zip(outline.slides, bindings)
        ]})
    for index, binding in enumerate(bindings):
        initial = svg_page_input_digest(content=outline.slides[index].content, style=style,
            index=index, count=2, instructions=None, language=None, preset=None,
            writing_policy=WRITING_POLICY_ID, header_page=binding, assets=page_assets)
        assert initial == _expected_page_input_digest(current, frozen, index, page_assets)
    broken = deepcopy(frozen)
    broken.authored_outline["slides"][0]["content"] = "원본과 다른 내용"
    with pytest.raises(ValueError, match="svg_common_header"):
        _expected_page_input_digest(current, broken, 0, page_assets)
    body = '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720"><rect x="64" y="400" width="200" height="60" fill="#2563EB"/></svg>'
    first = finalize_svg(body, index=0, n=2, style_snapshot=style, header_page=bindings[0])
    with pytest.raises(ValueError, match="svg_common_header"):
        finalize_svg(first, index=0, n=2, style_snapshot=style, header_page=bindings[0])
    assert finalize_svg(first, index=0, n=2, style_snapshot=style,
        header_page=bindings[0], allow_existing_common_header=True) == first
    preserved = SimpleNamespace(content="원본 보존", strategy="svg",
        source_mode="raster_reconstruction", common_header_mode="uniform",
        full_page_preserve_pages=(0,))
    with pytest.raises(HTTPException) as blocked:
        require_svg_creation_admission(preserved)
    assert blocked.value.detail == "svg_common_header_source_preservation_conflict"


def test_common_header_browser_ink_preserves_hinted_bearing_without_accepting_overflow():
    from copy import deepcopy
    from services.generation_core.drivers.svg.render import _apply_common_header_quality
    from services.svg_common_header import TITLE_ID

    svg = f'<svg xmlns="http://www.w3.org/2000/svg"><text id="{TITLE_ID}-line-0">AWS operations</text></svg>'
    contract = {"digest": "header", "title_box": {"x": 64, "y": 32, "width": 1152, "height": 50},
                "body_box": {"x": 64, "y": 144, "width": 1152, "height": 500},
                "font": {"family": "Noto Sans KR", "size_px": 34}}
    binding = {"applied": True, "lines": ["AWS operations"], "title_digest": "title", "digest": "page"}
    item = {"qid": "svg-1", "text_fragments": [{"x": 64, "y": 32, "width": 200, "height": 50}],
            "text_ink_fragments": [{"x": 63, "y": 43, "width": 200, "height": 33, "cellClusters": 0}],
            "font_loaded": True, "resolved_font_family": "Noto Sans KR", "font_size": 34,
            "css_visible": True, "effective_opacity": 1, "glyph_clipped": False}

    def measured(candidate):
        quality = {"passed": True, "geometry_passed": True,
                   "browser_metrics": {"elements": [candidate]}}
        _apply_common_header_quality(quality, svg, contract, binding, None)
        return quality

    assert measured(item)["common_header"]["passed"] is True
    outside = deepcopy(item)
    outside["text_ink_fragments"][0]["x"] = 62.9
    assert measured(outside)["passed"] is False
    clipped = deepcopy(item)
    clipped["glyph_clipped"] = True
    assert measured(clipped)["passed"] is False
    wrong_font = deepcopy(item)
    wrong_font["font_size"] = 36
    assert measured(wrong_font)["passed"] is False

    contract["canvas"] = {"width": 1280, "height": 720}
    body_rect = '<rect x="64" y="201" width="826" height="387" stroke="#222" stroke-width="2"/>'
    body_box = {"x": 64, "y": 201, "width": 826, "height": 387}

    def measured_body(body_svg, box, *, owned_text=False):
        import xml.etree.ElementTree as ET

        page_svg = svg.replace("</svg>", body_svg + "</svg>")
        body_qid = f"svg-{len(list(ET.fromstring(page_svg).iter())) - 1}"
        body_item = {"qid": body_qid, "bbox": box, "css_visible": True,
                     "effective_opacity": 1, "has_owned_text": owned_text}
        if owned_text:
            body_item.update(text_fragments=[box], text_ink_fragments=[{**box, "cellClusters": 0}])
        quality = {"passed": True, "geometry_passed": True,
                   "browser_metrics": {"elements": [deepcopy(item), body_item]}}
        _apply_common_header_quality(quality, page_svg, contract, binding, None)
        return quality

    assert measured_body(body_rect, body_box)["passed"] is True
    top_intrusion = {**body_box, "y": contract["body_box"]["y"]}
    assert measured_body(body_rect.replace('y="201"', 'y="144"'), top_intrusion)["passed"] is False
    bottom_intrusion = {**body_box, "y": 257}
    assert measured_body(body_rect.replace('y="201"', 'y="257"'), bottom_intrusion)["passed"] is False
    canvas_overflow = '<rect x="64" y="300" width="826" height="20" stroke="#222" stroke-width="132"/>'
    assert measured_body(canvas_overflow, {**body_box, "y": 300, "height": 20})["passed"] is False
    assert measured_body(body_rect.replace("<rect ", '<rect transform="scale(1)" '), body_box)["passed"] is False
    ancestor_transform = '<g transform="translate(0,0)">' + body_rect + "</g>"
    transformed = measured_body(ancestor_transform, body_box)
    assert transformed["passed"] is False
    assert transformed["failures"] == [{"kind": "svg_common_header_body_intrusion", "qid": "svg-3"}]
    assert measured_body('<text x="63" y="220">본문</text>', {**body_box, "x": 63}, owned_text=True)["passed"] is False
    outside_fill = body_rect.replace('x="64"', 'x="63"').replace(' stroke="#222" stroke-width="2"', '')
    assert measured_body(outside_fill, {**body_box, "x": 63})["passed"] is False
    assert measured_body('<image x="63" y="201" width="826" height="387"/>', {**body_box, "x": 63})["passed"] is False
    contract["brand_reservations"] = [{"x": 62, "y": 210, "width": 1.5, "height": 20}]
    assert measured_body(body_rect, body_box)["passed"] is False


def test_external_header_title_plan_classifies_page_labels_and_restores_exact_budget():
    from services.presentation_generation_staging import (
        _durable_request_payload, _request_from_durable_payload,
    )
    from services.public_operation_admission_models import plan_provider_requests
    from services.public_v2_orchestrator import _staged_svg_provider_request_plan
    from services.svg_common_header_title_budget import (
        INTERNAL_HEADER_TITLE_PLAN_KEY, seal_new_external_header_title_plan,
        svg_header_title_admission_budget,
    )
    from utils.outline_utils import explicit_outline_title

    slides = [
        "# 원본 1페이지\n운영 계획은 검토 중이다.",
        "# 원본 21/88페이지\n서비스 흐름을 설명한다.",
        "# Original page 38\nThe table lists requirements.",
        "# source page 59\nThe screen shows the approval state.",
        "# 원본 88페이지\n차트와 구성도는 입력 자료를 따른다.",
        "# 원본 이미지 보존\n원본 이미지는 승인 전까지 보존한다.",
    ]
    assert all(explicit_outline_title(page) is None for page in slides[:5])
    assert explicit_outline_title(slides[5]) == "원본 이미지 보존"
    request = GeneratePresentationRequest(
        content="입력 문서 재구성", template="authored", strategy="svg",
        common_header_mode="uniform", slides_markdown=slides,
    )
    plan = seal_new_external_header_title_plan(request)
    assert plan["missing_indices"] == [0, 1, 2, 3, 4]
    assert svg_header_title_admission_budget(request, len(slides)) == 1

    payload = _durable_request_payload(request)
    restored = _request_from_durable_payload(payload)
    assert payload[INTERNAL_HEADER_TITLE_PLAN_KEY] == plan
    assert restored._svg_header_title_plan == plan
    assert svg_header_title_admission_budget(restored, len(slides)) == 1
    legacy = dict(payload)
    legacy.pop(INTERNAL_HEADER_TITLE_PLAN_KEY)
    assert _request_from_durable_payload(legacy)._svg_header_title_plan is None

    standard = plan_provider_requests(
        slide_count=len(slides), image_budget=0, repair_budget=0, vqa_budget=0,
    )
    funded = plan_provider_requests(
        slide_count=len(slides), image_budget=0, repair_budget=0, vqa_budget=0,
        header_title_budget=1,
    )
    assert funded.initial_request_limit == standard.initial_request_limit + 1
    staged = {"illustration_budget": 0, "partitions": []}
    assert _staged_svg_provider_request_plan(len(slides), staged, header_title_budget=1).initial_request_limit == (
        _staged_svg_provider_request_plan(len(slides), staged).initial_request_limit + 1
    )


def test_external_header_titles_checkpoint_one_call_resume_unknown_and_source_guard():
    import asyncio

    from models.presentation_outline_model import PresentationOutlineModel, SlideOutlineModel
    from services.svg_common_header_title_budget import (
        INTERNAL_HEADER_TITLE_PLAN_KEY, seal_new_external_header_title_plan,
    )
    from services.svg_common_header_titles import prepare_missing_header_titles
    from services.provider_usage_instrumentation import _ACTIVE_SCOPE

    slides = [
        "# 원본 1페이지\n운영 계획은 검토 중이다.",
        "# 서비스 구성\n서비스 구성은 승인 이후 확정한다.",
    ]
    request = GeneratePresentationRequest(
        content="입력 문서 재구성", template="authored", strategy="svg",
        common_header_mode="uniform", slides_markdown=slides,
    )
    plan = seal_new_external_header_title_plan(request)
    outline = PresentationOutlineModel(slides=[SlideOutlineModel(content=page) for page in slides])

    class MemorySession:
        def __init__(self):
            self.row = None
            self.commits = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def scalar(self, _statement):
            return self.row

        def add(self, row):
            from models.sql.public_admission import AuthoredDeckPlanCheckpoint
            assert len(row.input_digest) == AuthoredDeckPlanCheckpoint.__table__.c.input_digest.type.length
            assert len(row.output_digest) == AuthoredDeckPlanCheckpoint.__table__.c.output_digest.type.length
            self.row = row

        async def execute(self, statement):
            values = statement.compile().params
            self.row.payload = values["payload"]
            self.row.output_digest = values["output_digest"]
            return SimpleNamespace(rowcount=1)

        async def commit(self):
            self.commits += 1

    async def owned_job(_session, _id, _token, *, fencing_token=None):
        return job

    async def prepared_title(payload, *, plan):
        assert _ACTIVE_SCOPE.get().workspace_operation_id is None
        calls.append(payload)
        return {
            "source_digest": payload["source_digest"],
            "page_count": payload["page_count"],
            "titles": [{"page_index": 0, "source_digest": payload["pages"][0]["source_digest"],
                        "title": "운영 계획", "source_quote": "운영 계획"}],
        }

    session = MemorySession()
    calls = []
    job = SimpleNamespace(
        id=uuid.uuid4(), attempt_token="first", attempt_number=1,
        request_sha256="a" * 64, workspace_operation_id=uuid.uuid4(),
        request_payload={INTERNAL_HEADER_TITLE_PLAN_KEY: plan},
        _workspace_fencing_token=None, public_only=False,
    )
    run = lambda source, callback: asyncio.run(prepare_missing_header_titles(
        request, job, source, session_maker=lambda: session,
        owned_job=owned_job, provider_callback=callback,
    ))
    assert calls == [] and session.row is None
    prepared = run(outline, prepared_title)
    assert len(calls) == 1
    assert session.row.payload["state"] == "succeeded"
    assert prepared.slides[0].title == "운영 계획"
    assert prepared.slides[1].title == "서비스 구성"
    assert [slide.content for slide in prepared.slides] == slides
    assert [slide.content for slide in outline.slides] == slides

    job.attempt_token = "resumed"
    job.attempt_number = 2
    assert run(outline, prepared_title).slides[0].title == "운영 계획"
    assert len(calls) == 1
    changed = PresentationOutlineModel(slides=[
        SlideOutlineModel(content=slides[0] + "\n새로운 사실"),
        SlideOutlineModel(content=slides[1]),
    ])
    with pytest.raises(ValueError, match="svg_common_header_title_plan_conflict"):
        run(changed, prepared_title)
    assert len(calls) == 1

    session = MemorySession()
    job = SimpleNamespace(**{**vars(job), "id": uuid.uuid4(), "attempt_number": 1,
                             "attempt_token": "unknown", "request_sha256": "b" * 64})

    async def unknown_provider(_payload, *, plan):
        assert _ACTIVE_SCOPE.get().workspace_operation_id is None
        calls.append("attempted")
        raise RuntimeError("provider result unknown")

    with pytest.raises(RuntimeError, match="provider result unknown"):
        run(outline, unknown_provider)
    assert session.row.payload["state"] == "unknown"
    assert len(calls) == 2
    with pytest.raises(ValueError, match="svg_common_header_title_outcome_unresolved"):
        run(outline, prepared_title)
    assert len(calls) == 2

    from copy import deepcopy
    from jsonschema import Draft202012Validator, ValidationError
    from services.svg_common_header import common_header_source_digest
    from services.svg_common_header_titles import _validate_title, _validated_result
    from utils.llm_calls.prepare_svg_common_header_titles import header_title_response_schema

    batch = deepcopy(calls[0])
    batch["pages"].append({"page_index": 1, "content": slides[1],
                          "source_digest": common_header_source_digest(slides[1])})
    response = {"source_digest": batch["source_digest"], "page_count": batch["page_count"],
        "titles": [{"page_index": page["page_index"], "source_digest": page["source_digest"],
                    "title": title, "source_quote": title}
                   for page, title in zip(batch["pages"], ["운영 계획", "서비스 구성"])]}
    validator = Draft202012Validator(header_title_response_schema(batch))
    validator.validate(response)
    assert len(_validated_result(response, batch)["titles"]) == 2
    crossed = deepcopy(response)
    crossed["titles"][0]["source_digest"] = batch["pages"][1]["source_digest"]
    with pytest.raises(ValidationError):
        validator.validate(crossed)
    with pytest.raises(ValueError, match="svg_common_header_title_source_mismatch") as failure:
        _validated_result(crossed, batch)
    assert failure.value.diagnostic == {"page_index": 0, "validation_stage": "source_digest"}
    assert _validate_title("예산 12억원", "예산 12억원", "예산 12억원")[0] == "예산 12억원"
    assert _validate_title("운영 개선", "운영 개선", "운영 개선")[0] == "운영 개선"
    with pytest.raises(ValueError, match="svg_common_header_title_source_mismatch"):
        _validate_title("운영 개선", "운영 계획", "운영 계획")
    with pytest.raises(ValueError, match="svg_common_header_title_qualification_mismatch"):
        _validate_title("운영 개선", "운영 개선 검토 중", "운영 개선 검토 중")

    session = MemorySession()
    job = SimpleNamespace(**{**vars(job), "id": uuid.uuid4(), "attempt_number": 1,
                             "attempt_token": "rejected", "request_sha256": "c" * 64})

    async def unsupported_number(payload, *, plan):
        result = await prepared_title(payload, plan=plan)
        result["titles"][0]["title"] = "운영 계획 2"
        return result

    with pytest.raises(ValueError, match="svg_common_header_title_source_mismatch"):
        run(outline, unsupported_number)
    assert session.row.payload["state"] == "rejected"
    assert session.row.payload["diagnostic"] == {"page_index": 0, "validation_stage": "title_source_quote"}
    assert set(session.row.payload) == {"version", "identity", "state", "reason", "diagnostic"}
    with pytest.raises(ValueError, match="svg_common_header_title_outcome_unresolved"):
        run(outline, prepared_title)
    assert len(calls) == 3


def test_title_only_terminal_retry_consumes_known_financial_history(monkeypatch):
    import asyncio
    from copy import deepcopy
    from datetime import datetime, timedelta, timezone

    from models.presentation_outline_model import PresentationOutlineModel, SlideOutlineModel
    from models.sql.authored_slide_checkpoint import AuthoredSlideCheckpoint
    from models.sql.public_admission import (
        AuthoredDeckPlanCheckpoint, PublicOperationAdmission, PublicProviderLogicalCall,
    )
    from models.sql.workspace import WorkspaceOperation
    from services import presentation_generation_pipeline as pipeline
    from services.authored_canvas import canvas_metadata_for_aspect_ratio
    from services.authored_deck_plan_checkpoint import canonical_checkpoint_digest
    from services.public_operation_admission_models import _logical_call_ordinal
    from services.svg_common_header import header_snapshot_base, prepare_common_header
    from services.svg_common_header_title_budget import (
        INTERNAL_HEADER_TITLE_PLAN_KEY, seal_new_external_header_title_plan,
    )
    from services.svg_common_header_titles import _validated_result
    from utils.hashing import canonical_json_digest, sha256_hex

    source = "# 원본 1페이지\n운영 계획은 검토 중이다."
    request = GeneratePresentationRequest(
        content="입력 문서 재구성", template="authored", strategy="svg",
        common_header_mode="uniform", slides_markdown=[source],
    )
    plan = seal_new_external_header_title_plan(request)
    source_operation_id, child_operation_id = uuid.uuid4(), uuid.uuid4()
    presentation_id, owner_id, conversation_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    job_id, admission_id = uuid.uuid4(), uuid.uuid4()
    original = PresentationOutlineModel(slides=[SlideOutlineModel(content=source)])
    title_payload = {"source_digest": plan["source_digest"], "page_count": 1,
                     "pages": [{"page_index": 0, "source_digest":
                                canonical_json_digest(source), "content": source}]}
    result = _validated_result({"source_digest": plan["source_digest"], "page_count": 1,
        "titles": [{"page_index": 0, "source_digest": title_payload["pages"][0]["source_digest"],
                    "title": "운영 계획", "source_quote": "운영 계획"}]}, title_payload)
    original.slides[0].title = result["titles"][0]["title"]
    original.slides[0].title_source_digest = canonical_json_digest(result["titles"][0])
    base_style = {"tokens": {}}
    base_style["digest"] = canonical_json_digest({"tokens": {}}, allow_nan=True)
    contract, bindings = prepare_common_header(
        base_style, original, canvas_metadata_for_aspect_ratio("16:9"),
    )
    original.slides[0].common_header = bindings[0]
    frozen_style = {**base_style, "common_header": contract}
    frozen_style["digest"] = canonical_json_digest(
        {key: value for key, value in frozen_style.items() if key != "digest"},
        allow_nan=True,
    )
    generation_payload = {**request.model_dump(mode="json"), "strategy": "svg",
        INTERNAL_HEADER_TITLE_PLAN_KEY: plan,
        "_internal_svg_style_snapshot": header_snapshot_base(frozen_style)}
    # The submitted caller fingerprint predates admission/bootstrap payload
    # fields, so the persisted job hash intentionally differs from this payload.
    request_digest = "f" * 64
    assert request_digest != pipeline.request_sha256(generation_payload)
    checkpoint_identity = {"job_id": str(job_id), "request_digest": request_digest,
        "workspace_operation_id": str(source_operation_id), "plan_digest": plan["plan_digest"]}
    checkpoint_payload = {"version": "svg-common-header-title-batch/v1",
        "identity": checkpoint_identity, "state": "succeeded", "result": result}
    checkpoint = SimpleNamespace(
        input_digest=canonical_checkpoint_digest(checkpoint_identity),
        output_digest=canonical_checkpoint_digest(checkpoint_payload),
        payload=checkpoint_payload, produced_attempt_number=1,
    )
    job = SimpleNamespace(
        id=job_id, presentation_id=presentation_id, workspace_operation_id=source_operation_id,
        request_payload=generation_payload, request_sha256=request_digest,
        completed_slides=0, total_slides=1, attempt_number=1,
        authored_outline=original.model_dump(mode="json"), svg_style_snapshot=frozen_style,
    )
    now = datetime.now(timezone.utc)
    deadline = now + timedelta(hours=1)
    source_operation = SimpleNamespace(
        id=source_operation_id, owner_id=owner_id, owner_scope="owner", tenant_id="tenant",
        presentation_id=presentation_id, conversation_id=conversation_id,
        kind="presentation.generate", state="failed", fencing_token=1,
        attempt_number=1,
        contract_version="public-operation/v2", execution_profile_version="public-generation-export/v2",
        deadline_at=deadline, retry_of_operation_id=None,
        request_payload={"job_id": str(job_id), "generation_request": generation_payload},
    )
    child_operation = SimpleNamespace(
        id=child_operation_id, retry_of_operation_id=source_operation_id,
        owner_id=owner_id, owner_scope="owner", presentation_id=presentation_id,
        conversation_id=conversation_id, kind="presentation.generate",
        request_payload={"generation_request": generation_payload},
    )
    admission = SimpleNamespace(
        id=admission_id, owner_id=owner_id, tenant_id="tenant", operation_kind="generation",
        state="failed", fence=1, deadline_at=deadline, slide_count=1,
        initial_request_count=1, retry_request_count=0,
        estimated_reserved_microunits=0, actual_reserved_microunits=0,
        actual_cost_unavailable_count=0, estimated_consumed_microunits=20,
        actual_consumed_microunits=10, planned_request_limit=5, retry_request_limit=2,
        estimated_cost_limit_microunits=100, actual_cost_limit_microunits=100,
    )
    slot = "header-titles-" + plan["plan_digest"].removeprefix("sha256:") + ".attempt-1"
    ordinal = _logical_call_ordinal(
        operation_id=source_operation_id, stage="outline", slide_index=-1,
        logical_call_ordinal=None, logical_call_key=slot,
    )
    key_hash = sha256_hex(
        f"public-provider-call/v2:{source_operation_id}:outline:-1:{ordinal}:"
        f"{sha256_hex(slot.encode())}".encode()
    )
    call = SimpleNamespace(
        stage="outline", slide_index=-1, retry_class="initial", state="reported",
        outcome="succeeded", reconciled_at=now, actual_cost_microunits=10,
        estimated_cost_microunits=20, provider_id="openai", model_id="model",
        logical_call_ordinal=ordinal, idempotency_key_hash=key_hash,
    )

    class HistorySession:
        async def scalar(self, statement):
            entity = statement.column_descriptions[0]["entity"]
            return {WorkspaceOperation: source_operation,
                    PublicOperationAdmission: admission,
                    pipeline.PresentationGenerationJob: job,
                    AuthoredDeckPlanCheckpoint: checkpoint}[entity]

        async def scalars(self, statement):
            entity = statement.column_descriptions[0]["entity"]
            return {PublicProviderLogicalCall: [call],
                    AuthoredSlideCheckpoint: []}[entity]

    monkeypatch.setattr(pipeline, "current_principal_context",
                        lambda: SimpleNamespace(owner_id=owner_id, tenant_id="tenant"))
    session = HistorySession()

    def bounds():
        return asyncio.run(pipeline._unused_terminal_generation_retry_budget(
            session, source_operation=source_operation, operation=child_operation,
        ))

    assert bounds() == (4, 2, 80, 90)
    for state in ("unavailable", "reserved"):
        call.state = state
        with pytest.raises(HTTPException, match="generation_retry_provider_history_present"):
            bounds()
    call.state = "reported"
    call.stage = "author"
    with pytest.raises(HTTPException, match="generation_retry_provider_history_present"):
        bounds()
    call.stage = "outline"
    original_result = deepcopy(checkpoint.payload["result"])
    checkpoint.payload["result"]["titles"][0]["title"] = "다른 제목"
    with pytest.raises(HTTPException, match="generation_retry_provider_history_present"):
        bounds()
    checkpoint.payload["result"] = original_result
    job.request_payload["slides_markdown"][0] = source + "\n변경"
    with pytest.raises(HTTPException, match="generation_retry_provider_history_present"):
        bounds()
    job.request_payload["slides_markdown"][0] = source
    admission.planned_request_limit = 1
    with pytest.raises(HTTPException, match="generation_retry_provider_history_present"):
        bounds()


@pytest.mark.parametrize("slide_count", [1, 2, 5, 6, 12, 80])
def test_restoration_policy_reserves_final_review_without_changing_legacy(slide_count):
    from copy import deepcopy
    from services.svg_authoring_profile import (
        ART_DIRECTION_PROFILE_V8, read_quality_recovery_execution_plan,
        seal_new_quality_recovery_plan, svg_quality_review_admission_budget,
        validate_quality_recovery_agreement,
    )
    from utils.hashing import canonical_json_digest

    def frozen(request, plan):
        value = dict(authoring_profile=dict(ART_DIRECTION_PROFILE_V8),
                     quality_recovery_policy=dict(request._svg_quality_recovery_policy),
                     quality_recovery_plan=deepcopy(plan))
        value["digest"] = canonical_json_digest(value, allow_nan=True)
        return value

    current = SimpleNamespace(vision_qa=False)
    plan = seal_new_quality_recovery_plan(current, slide_count)
    assert current._svg_quality_recovery_policy["version"] == 2
    snapshot = frozen(current, plan)
    current._svg_style_snapshot = snapshot
    execution = read_quality_recovery_execution_plan(snapshot)
    final = execution.get("final_selected_review")
    if slide_count > 1:
        assert final["physical_offset"] == execution["review_call_limit"]
        assert final["run_limit"] == 1
        assert final["review_call_limit"] == (slide_count + 4) // 5 + 2
    else:
        assert final is None
    total = execution["review_call_limit"] + (final["review_call_limit"] if final else 0)
    assert svg_quality_review_admission_budget(current, slide_count) == total
    if slide_count == 80:
        assert total == 54

    from services.svg_partial_recovery import _recovery_review_allocation
    regular, format_control, recovered_final = _recovery_review_allocation(current, snapshot, slide_count)
    assert regular == execution["review_call_limit"]
    assert recovered_final == final
    assert regular + (recovered_final["review_call_limit"] if recovered_final else 0) == total
    manifest = {}
    if format_control is not None:
        manifest["global_format_retry_control"] = format_control
    if recovered_final is not None:
        manifest["final_selected_review_control"] = recovered_final
    assert read_quality_recovery_execution_plan(snapshot, manifest=manifest) == execution

    legacy = SimpleNamespace(vision_qa=False, _svg_quality_recovery_policy={"id": "svg-quality-first", "version": 1})
    old_plan = seal_new_quality_recovery_plan(legacy, slide_count)
    old_snapshot = frozen(legacy, old_plan)
    legacy._svg_style_snapshot = old_snapshot
    original = deepcopy(old_snapshot)
    assert "final_selected_review_control" not in old_plan
    assert svg_quality_review_admission_budget(legacy, slide_count) == old_plan["review_call_limit"]
    legacy_regular, legacy_format, legacy_final = _recovery_review_allocation(legacy, old_snapshot, slide_count)
    assert legacy_regular == (legacy_format["review_call_limit"] if legacy_format else old_plan["review_call_limit"])
    assert legacy_final["physical_offset"] == legacy_regular
    assert seal_new_quality_recovery_plan(legacy, slide_count) == old_plan
    assert old_snapshot == original
    with pytest.raises(ValueError, match="svg_quality_recovery_policy_conflict"):
        validate_quality_recovery_agreement(current, old_snapshot)


def test_restoration_final_review_control_rejects_tampering():
    from copy import deepcopy
    from services.svg_authoring_profile import (
        ART_DIRECTION_PROFILE_V8, global_format_retry_control,
        read_quality_recovery_execution_plan, read_quality_recovery_plan,
        seal_new_quality_recovery_plan,
    )
    from utils.hashing import canonical_json_digest

    request = SimpleNamespace(vision_qa=False)
    plan = seal_new_quality_recovery_plan(request, 12)
    snapshot = dict(authoring_profile=dict(ART_DIRECTION_PROFILE_V8),
                    quality_recovery_policy=dict(request._svg_quality_recovery_policy),
                    quality_recovery_plan=plan)
    snapshot["digest"] = canonical_json_digest(snapshot, allow_nan=True)
    for field, value in (("physical_offset", 999), ("run_limit", True), ("namespace", "unadmitted")):
        changed = deepcopy(snapshot)
        changed["quality_recovery_plan"]["final_selected_review_control"][field] = value
        changed["digest"] = canonical_json_digest({k: v for k, v in changed.items() if k != "digest"}, allow_nan=True)
        with pytest.raises(ValueError, match="svg_quality_recovery_plan_invalid"):
            read_quality_recovery_plan(changed)
    manifest = dict(global_format_retry_control=global_format_retry_control(snapshot))
    manifest["global_format_retry_control"]["global_retry_limit"] = True
    with pytest.raises(ValueError, match="svg_quality_recovery_execution_plan_invalid"):
        read_quality_recovery_execution_plan(snapshot, manifest=manifest)
    with pytest.raises(ValueError, match="svg_quality_recovery_execution_plan_invalid"):
        read_quality_recovery_execution_plan(snapshot, manifest={"final_selected_review_control": {}})


def _svg_request(**overrides):
    base = dict(
        strategy="svg", source_mode=None, design_pack_pin=None,
        generation_engine="authored", generation_mode="auto", master_pin=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_design_pack_pin_is_rejected_with_a_specific_structural_reason():
    request = _svg_request(design_pack_pin={"designPackId": "acme", "version": 1})
    with pytest.raises(HTTPException) as excinfo:
        require_svg_creation_admission(request)
    assert excinfo.value.status_code == 422
    assert excinfo.value.detail == "svg_design_pack_requires_source_native_patch_target"


def test_design_pack_projection_error_names_the_real_gap():
    from services.generation_core.style.design_pack_projection import assert_design_pack_projects

    with pytest.raises(DesignPackProjectionError):
        assert_design_pack_projects()


def test_master_pin_alone_no_longer_blocks_admission():
    request = _svg_request(
        master_pin=AuthoredMasterPinInput(master_id="master:acme-1", revision=1, digest="sha256:" + "1" * 64)
    )
    # No exception: master_pin admissibility is decided later, once resolved
    # and projected (see _resolve_master_rules), not by presence alone.
    require_svg_creation_admission(request)


def test_svg_design_reference_does_not_reinterpret_legacy_pack_execution():
    pin = {"designPackId": "acme", "version": 1, "digest": "sha256:" + "1" * 64}
    require_svg_creation_admission(_svg_request(svg_design_reference_pin=pin))
    with pytest.raises(HTTPException) as error:
        require_svg_creation_admission(_svg_request(svg_design_reference_pin=pin, master_pin=pin))
    assert error.value.detail == "svg_design_reference_conflict"


def test_svg_design_reference_requires_owner_and_pins_target_geometry():
    pin = {"designPackId": "acme", "version": 1, "digest": "sha256:" + "1" * 64}
    request = _svg_request(svg_design_reference_pin=pin)
    canvas = SimpleNamespace(width_px=720, height_px=1280)
    with patch("utils.owner_context.get_current_owner_id", return_value=None):
        with pytest.raises(HTTPException) as error:
            _resolve_design_reference(request, canvas)
    assert error.value.status_code == 401
    owner = uuid.uuid4()
    with patch("utils.owner_context.get_current_owner_id", return_value=owner), patch(
        "services.generation_core.style.design_pack_projection.resolve_design_pack_reference",
        return_value={"reference_semantics": "design-identity"},
    ) as resolve:
        assert _resolve_design_reference(request, canvas)["reference_semantics"] == "design-identity"
        resolve.assert_called_once_with(owner_id=owner, pin=pin, width=720, height=1280)


def test_source_svg_reference_capability_allows_company_design_and_selected_canvas():
    capability = source_svg_capability(
        strategy="svg", source_content=True, has_company_standard=True,
        reference_mode="exact_reconstruction", style_id="default",
    )
    assert capability["enabled"] is True
    assert capability["canvasSource"] == "selected-svg"


def _resolved_master(contract):
    digest = contract.digest or ("sha256:" + "0" * 64)
    pin = MasterDigestPin(
        master_id="master:acme-1", master_revision=1, master_digest=digest,
        layout_registry_digest="sha256:" + "0" * 64, font_manifest_digest="sha256:" + "0" * 64,
    )
    return ResolvedLegacyMasterPin(owner_id=uuid.uuid4(), pin=pin, contract=contract)


def test_resolve_master_rules_returns_none_without_a_pin():
    request = _svg_request()
    canvas = SimpleNamespace(width_px=1280, height_px=720)
    assert _resolve_master_rules(request, canvas) == (None, None)


def test_resolve_master_rules_projects_the_published_master():
    contract = DeckMasterContract.model_validate(master_contract_payload())
    resolved = _resolved_master(contract)
    request = _svg_request(
        master_pin=AuthoredMasterPinInput(master_id="master:acme-1", revision=1, digest="sha256:" + "1" * 64)
    )
    canvas = SimpleNamespace(width_px=1280, height_px=720)
    with patch("services.svg_generation_admission.is_authored_vnext_master_enabled", return_value=True), \
         patch("utils.owner_context.get_current_owner_id", return_value=uuid.uuid4()), \
         patch("services.authored_vnext.generation.legacy_pin.resolve_legacy_master_pin", return_value=resolved):
        rules, pin = _resolve_master_rules(request, canvas)
    assert rules["tokens"]["accent"] == "#1463FF"
    assert pin["masterId"] == "master:acme-1"


def test_resolve_master_rules_requires_an_owner():
    request = _svg_request(
        master_pin=AuthoredMasterPinInput(master_id="master:acme-1", revision=1, digest="sha256:" + "1" * 64)
    )
    canvas = SimpleNamespace(width_px=1280, height_px=720)
    with patch("services.svg_generation_admission.is_authored_vnext_master_enabled", return_value=True), \
         patch("utils.owner_context.get_current_owner_id", return_value=None):
        with pytest.raises(HTTPException) as excinfo:
            _resolve_master_rules(request, canvas)
    assert excinfo.value.status_code == 401


def test_resolve_master_rules_maps_pipeline_errors_to_409():
    request = _svg_request(
        master_pin=AuthoredMasterPinInput(master_id="master:acme-1", revision=1, digest="sha256:" + "1" * 64)
    )
    canvas = SimpleNamespace(width_px=1280, height_px=720)
    with patch("services.svg_generation_admission.is_authored_vnext_master_enabled", return_value=True), \
         patch("utils.owner_context.get_current_owner_id", return_value=uuid.uuid4()), \
         patch(
             "services.authored_vnext.generation.legacy_pin.resolve_legacy_master_pin",
             side_effect=PublishedMasterRequiredError(),
         ):
        with pytest.raises(HTTPException) as excinfo:
            _resolve_master_rules(request, canvas)
    assert excinfo.value.status_code == 409


@pytest.mark.parametrize("profile_id", [None, 'current-v4', 'art-direction-v1', 'art-direction-v1@2', 'art-direction-v1@3', 'art-direction-v1@4', 'art-direction-v1@5', 'art-direction-v1@6', 'art-direction-v1@7', 'art-direction-v1@8'])
def test_authoring_profile_staging_preserves_legacy_and_frozen_new_identity(monkeypatch, profile_id):
    from models.generate_presentation_request import GeneratePresentationRequest
    from services.presentation_generation_staging import _durable_request_payload, _request_from_durable_payload
    from services.svg_authoring_profile import (
        ART_DIRECTION_PROFILE, ART_DIRECTION_PROFILE_V2, ART_DIRECTION_PROFILE_V3, ART_DIRECTION_PROFILE_V4, ART_DIRECTION_PROFILE_V5, ART_DIRECTION_PROFILE_V6, ART_DIRECTION_PROFILE_V7, INTERNAL_SVG_AUTHORING_PROFILE_KEY, select_svg_authoring_profile,
    )
    from services.svg_generation_admission import pin_svg_style
    from utils.hashing import canonical_json_digest

    legacy_request = GeneratePresentationRequest(content='deck', strategy='svg')
    select_svg_authoring_profile(legacy_request, "current-v4", trusted_internal=True)
    baseline = _durable_request_payload(legacy_request)
    request = GeneratePresentationRequest(content="deck", strategy="svg")
    monkeypatch.setenv("CANVAS_BENCH_ENABLED", "true")
    if profile_id is not None:
        select_svg_authoring_profile(request, profile_id)
    payload = _durable_request_payload(request)
    monkeypatch.delenv("CANVAS_BENCH_ENABLED")
    restored = _request_from_durable_payload(payload)
    assert pin_svg_style(restored) == payload["_internal_svg_style_snapshot"]
    if profile_id == "current-v4":
        assert payload == baseline
        assert INTERNAL_SVG_AUTHORING_PROFILE_KEY not in payload
        return
    snapshot = payload["_internal_svg_style_snapshot"]
    expected = {'art-direction-v1': ART_DIRECTION_PROFILE, 'art-direction-v1@2': ART_DIRECTION_PROFILE_V2, 'art-direction-v1@3': ART_DIRECTION_PROFILE_V3, 'art-direction-v1@4': ART_DIRECTION_PROFILE_V4, 'art-direction-v1@5': ART_DIRECTION_PROFILE_V5, 'art-direction-v1@6': ART_DIRECTION_PROFILE_V6, 'art-direction-v1@7': ART_DIRECTION_PROFILE_V7, None: {'id': 'art-direction-v1', 'version': 8}, 'art-direction-v1@8': {'id': 'art-direction-v1', 'version': 8}}[profile_id]
    assert payload[INTERNAL_SVG_AUTHORING_PROFILE_KEY] == expected
    assert snapshot["authoring_profile"] == expected
    new_contract_keys = {"readability_policy", "common_header_design_plan"} if profile_id is None else set()
    if profile_id is None:
        from services.document_canvas_generation import validate_svg_readability_policy
        from services.svg_common_header_design_plan import validate_header_design_plan

        assert validate_svg_readability_policy(snapshot["readability_policy"])["minimum_font_pt"] == 9
        assert validate_header_design_plan(snapshot["common_header_design_plan"])["schema"] == "svg-common-header-design-plan/v2"
    else:
        assert "readability_policy" not in snapshot
    assert {key: value for key, value in snapshot.items() if key not in {"authoring_profile", "digest"} | new_contract_keys} == {
        key: value for key, value in baseline["_internal_svg_style_snapshot"].items() if key != "digest"
    }
    assert snapshot["digest"] == canonical_json_digest(
        {key: value for key, value in snapshot.items() if key != "digest"}, allow_nan=True,
    )
    assert snapshot["digest"] != baseline["_internal_svg_style_snapshot"]["digest"]
    expected_request = GeneratePresentationRequest(content="deck", strategy="svg",
        **({"common_header_mode": "uniform"} if profile_id is None else {}))
    assert request.model_dump(mode="json") == expected_request.model_dump(mode="json")


@pytest.mark.parametrize("gate", [None, "false", "TRUE", "1"])
@pytest.mark.parametrize("profile_id", ["art-direction-v1", "art-direction-v1@2", "art-direction-v1@3", "art-direction-v1@4", "art-direction-v1@5", "art-direction-v1@6", "art-direction-v1@7"])
def test_new_authoring_selection_requires_exact_bench_gate(monkeypatch, gate, profile_id):
    from services.svg_authoring_profile import select_svg_authoring_profile

    monkeypatch.delenv("CANVAS_BENCH_ENABLED", raising=False)
    if gate is not None:
        monkeypatch.setenv("CANVAS_BENCH_ENABLED", gate)
    with pytest.raises(ValueError, match="bench_field_disabled"):
        select_svg_authoring_profile(_svg_request(), profile_id)


@pytest.mark.parametrize("profile_id", ["art-direction-v1", "art-direction-v1@2", "art-direction-v1@3", "art-direction-v1@4", "art-direction-v1@5", "art-direction-v1@6", "art-direction-v1@7"])
def test_new_authoring_selection_cannot_rewrite_old_snapshot(monkeypatch, profile_id):
    from models.generate_presentation_request import GeneratePresentationRequest
    from services.presentation_generation_staging import _durable_request_payload, _request_from_durable_payload
    from services.svg_authoring_profile import INTERNAL_SVG_AUTHORING_PROFILE_KEY, select_svg_authoring_profile, validate_authoring_profile
    from services.svg_generation_admission import pin_svg_style

    request = GeneratePresentationRequest(content="deck", strategy="svg")
    legacy_snapshot = pin_svg_style(request)
    monkeypatch.setenv("CANVAS_BENCH_ENABLED", "true")
    select_svg_authoring_profile(request, profile_id)
    with pytest.raises(HTTPException) as error:
        pin_svg_style(request)
    assert error.value.detail == "svg_authoring_profile_conflict"
    assert request._svg_style_snapshot == legacy_snapshot
    payload = _durable_request_payload(GeneratePresentationRequest(content="deck", strategy="svg"))
    payload[INTERNAL_SVG_AUTHORING_PROFILE_KEY] = validate_authoring_profile(profile_id)
    with pytest.raises(ValueError, match="svg_authoring_profile_conflict"):
        _request_from_durable_payload(payload)


@pytest.mark.parametrize("target_profile", ["art-direction-v1@2", "art-direction-v1@3", "art-direction-v1@4", "art-direction-v1@5", "art-direction-v1@6", "art-direction-v1@7"])
def test_frozen_v1_authoring_cannot_be_replayed_as_v2(monkeypatch, target_profile):
    from models.generate_presentation_request import GeneratePresentationRequest
    from services.presentation_generation_staging import _durable_request_payload, _request_from_durable_payload
    from services.svg_authoring_profile import INTERNAL_SVG_AUTHORING_PROFILE_KEY, select_svg_authoring_profile, validate_authoring_profile
    from services.svg_generation_admission import pin_svg_style

    monkeypatch.setenv("CANVAS_BENCH_ENABLED", "true")
    request = GeneratePresentationRequest(content="deck", strategy="svg")
    select_svg_authoring_profile(request, "art-direction-v1")
    payload = _durable_request_payload(request)
    frozen_digest = request._svg_style_snapshot["digest"]
    select_svg_authoring_profile(request, target_profile)
    with pytest.raises(HTTPException) as error:
        pin_svg_style(request)
    assert error.value.detail == "svg_authoring_profile_conflict"
    assert request._svg_style_snapshot["digest"] == frozen_digest
    payload[INTERNAL_SVG_AUTHORING_PROFILE_KEY] = validate_authoring_profile(target_profile)
    monkeypatch.delenv("CANVAS_BENCH_ENABLED")
    with pytest.raises(ValueError, match="svg_authoring_profile_conflict"):
        _request_from_durable_payload(payload)


@pytest.mark.parametrize("profile", ["future-v2", "art-direction-v1@9", {"id": "art-direction-v1", "version": 9}, {"id": "art-direction-v1", "version": True}])
def test_unknown_durable_authoring_profile_fails_closed(profile):
    from services.presentation_generation_staging import _request_from_durable_payload
    from services.svg_authoring_profile import INTERNAL_SVG_AUTHORING_PROFILE_KEY

    with pytest.raises(ValueError, match="svg_authoring_profile_invalid"):
        _request_from_durable_payload({"content": "deck", "strategy": "svg", INTERNAL_SVG_AUTHORING_PROFILE_KEY: profile})


@pytest.mark.parametrize("profile_id", ["art-direction-v1", "art-direction-v1@2", "art-direction-v1@3", "art-direction-v1@4", "art-direction-v1@5", "art-direction-v1@6", "art-direction-v1@7"])
def test_frozen_authoring_profile_replay_checks_digest_and_independent_speed_arm(monkeypatch, profile_id):
    from models.generate_presentation_request import GeneratePresentationRequest
    from services.presentation_generation_staging import _durable_request_payload, _request_from_durable_payload
    from services.svg_authoring_profile import INTERNAL_SVG_AUTHORING_PROFILE_KEY, select_svg_authoring_profile, validate_authoring_profile

    monkeypatch.setenv("CANVAS_BENCH_ENABLED", "true")
    request = GeneratePresentationRequest(content="deck", strategy="svg", bench_speed_arm="brief_off")
    select_svg_authoring_profile(request, profile_id)
    payload = _durable_request_payload(request)
    payload.pop(INTERNAL_SVG_AUTHORING_PROFILE_KEY)
    monkeypatch.delenv("CANVAS_BENCH_ENABLED")
    restored = _request_from_durable_payload(payload)
    assert restored._svg_authoring_profile == validate_authoring_profile(profile_id)
    assert restored._bench_speed_arm == "brief_off"
    payload["_internal_svg_style_snapshot"]["tokens"]["primary"] = "#123456"
    with pytest.raises(ValueError, match="svg_authoring_profile_snapshot_digest_mismatch"):
        _request_from_durable_payload(payload)


@pytest.mark.parametrize("profile_id", ["art-direction-v1", "art-direction-v1@2", "art-direction-v1@3", "art-direction-v1@4", "art-direction-v1@5", "art-direction-v1@6", "art-direction-v1@7"])
def test_public_generation_hash_distinguishes_only_new_authoring_profile(profile_id):
    from models.public_v2_contracts import PresentationGenerationRequestV2
    from services.public_v2_orchestrator import _canonical_hash, _generation_request_hash_payload
    from services.svg_authoring_profile import INTERNAL_SVG_AUTHORING_PROFILE_KEY, validate_authoring_profile

    request = PresentationGenerationRequestV2(content="deck", strategy="svg")
    baseline = _generation_request_hash_payload(request)
    assert _generation_request_hash_payload(request, svg_authoring_profile="current-v4") == baseline
    selected = _generation_request_hash_payload(request, svg_authoring_profile=profile_id)
    assert selected[INTERNAL_SVG_AUTHORING_PROFILE_KEY] == validate_authoring_profile(profile_id)
    assert _canonical_hash(selected) != _canonical_hash(baseline)
    if profile_id in {"art-direction-v1@2", "art-direction-v1@3", "art-direction-v1@4", "art-direction-v1@5", "art-direction-v1@6", "art-direction-v1@7"}:
        assert _canonical_hash(selected) != _canonical_hash(_generation_request_hash_payload(request, svg_authoring_profile="art-direction-v1"))
    if profile_id in {"art-direction-v1@3", "art-direction-v1@4", "art-direction-v1@5", "art-direction-v1@6", "art-direction-v1@7"}:
        assert _canonical_hash(selected) != _canonical_hash(_generation_request_hash_payload(request, svg_authoring_profile="art-direction-v1@2"))
    if profile_id in {"art-direction-v1@4", "art-direction-v1@5", "art-direction-v1@6", "art-direction-v1@7"}:
        assert _canonical_hash(selected) != _canonical_hash(_generation_request_hash_payload(request, svg_authoring_profile="art-direction-v1@3"))
    if profile_id in {"art-direction-v1@5", "art-direction-v1@6", "art-direction-v1@7"}:
        assert _canonical_hash(selected) != _canonical_hash(_generation_request_hash_payload(request, svg_authoring_profile="art-direction-v1@4"))
    if profile_id in {"art-direction-v1@6", "art-direction-v1@7"}:
        assert _canonical_hash(selected) != _canonical_hash(_generation_request_hash_payload(request, svg_authoring_profile="art-direction-v1@5"))
    if profile_id == "art-direction-v1@7":
        assert _canonical_hash(selected) != _canonical_hash(_generation_request_hash_payload(request, svg_authoring_profile="art-direction-v1@6"))
    with pytest.raises(ValueError, match="svg_authoring_profile_invalid"):
        _generation_request_hash_payload(request, svg_authoring_profile="future-v2")


@pytest.mark.parametrize("profile_id", [None, "art-direction-v1", "art-direction-v1@2", "art-direction-v1@3", "art-direction-v1@4", "art-direction-v1@5", "art-direction-v1@6", "art-direction-v1@7"])
def test_public_generation_integrity_replay_keeps_authoring_selection(monkeypatch, profile_id):
    import asyncio
    from unittest.mock import AsyncMock

    from sqlalchemy.exc import IntegrityError
    from models.public_v2_contracts import PresentationGenerationRequestV2
    from services import public_v2_orchestrator
    from services.public_v2_orchestration_types import PublicRequestActor

    orchestrator = object.__new__(public_v2_orchestrator.SqlPublicV2Orchestrator)
    orchestrator._submit_generation_once = AsyncMock(side_effect=IntegrityError("test", {}, Exception()))
    receipt = object()
    orchestrator._replay_generation = AsyncMock(return_value=receipt)
    monkeypatch.setattr(public_v2_orchestrator, "wake_workspace_operation_runner", lambda: None)
    actor = PublicRequestActor(owner_id=uuid.uuid4(), tenant_id="test", correlation_id="profile", scopes=frozenset())
    request = PresentationGenerationRequestV2(content="deck", strategy="svg")
    kwargs = dict(actor=actor, idempotency_key="profile-test", request=request)
    if profile_id is not None:
        kwargs["svg_authoring_profile"] = profile_id
    assert asyncio.run(orchestrator.submit_generation(**kwargs)) is receipt
    orchestrator._submit_generation_once.assert_awaited_once_with(**kwargs)
    orchestrator._replay_generation.assert_awaited_once_with(**kwargs)


@pytest.mark.parametrize("conflict", [None, "profile", "snapshot"])
def test_prepare_uses_fenced_current_job_snapshot_before_live_adaptation(conflict, monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock, Mock

    from services.svg_presentation_prepare import _pin_owned_svg_style

    frozen = {"digest": "legacy-frozen", "tokens": {"primary": "#123456"}}
    stale_job = SimpleNamespace(id=uuid.uuid4(), attempt_token="attempt", _workspace_fencing_token=9,
                                svg_style_snapshot={"digest": "stale-job-object"})
    current = SimpleNamespace(svg_style_snapshot=frozen)
    request = _svg_request(_svg_style_snapshot=None)
    if conflict == "profile":
        request._svg_authoring_profile = "art-direction-v1"
    elif conflict == "snapshot":
        request._svg_style_snapshot = {"digest": "conflicting-staged"}
    checkpoint_session = AsyncMock()
    checkpoint_session.add = Mock()
    session_maker = Mock(return_value=checkpoint_session)
    checkpoint_session.__aenter__.return_value = checkpoint_session
    owned_job = AsyncMock(return_value=current)
    pin_style = Mock(side_effect=lambda selected: selected._svg_style_snapshot)
    operation = _pin_owned_svg_style(request, stale_job, pin_style=pin_style,
                                    session_maker=session_maker, owned_job=owned_job)
    if conflict is not None:
        with pytest.raises((ValueError, RuntimeError), match="svg_authoring_profile_conflict|svg_style_snapshot_conflict"):
            asyncio.run(operation)
        pin_style.assert_not_called()
        checkpoint_session.commit.assert_not_awaited()
    else:
        assert asyncio.run(operation) == frozen
        assert request._svg_style_snapshot is frozen
        pin_style.assert_called_once_with(request)
        checkpoint_session.commit.assert_awaited_once()
    owned_job.assert_awaited_once_with(checkpoint_session, stale_job.id, "attempt", fencing_token=9)
    if conflict is None:
        from copy import deepcopy
        import json
        from xml.etree import ElementTree as ET
        from services import svg_partial_recovery as recovery
        from services.svg_authoring_profile import ART_DIRECTION_PROFILE_V8, read_quality_recovery_policy
        from services.svg_generation_admission import pin_svg_style as official_pin_svg_style
        from services import artifact_store, svg_asset_persistence
        from models.generate_presentation_request import GeneratePresentationRequest
        from utils.hashing import sha256_hex
        owner, operation_id, job_id, presentation_id = (uuid.uuid4() for _ in range(4))
        tenant = "fixture-tenant"
        monkeypatch.setattr("utils.owner_context.get_current_owner_id", lambda: owner)
        monkeypatch.setattr("utils.owner_context.get_current_tenant_id", lambda: tenant)
        source_request = GeneratePresentationRequest(content="Fixture message", strategy="svg",
                                                     n_slides=1, language="en", common_header_mode="free")
        snapshot = official_pin_svg_style(source_request)
        assert read_quality_recovery_policy(snapshot) == {"id": "svg-quality-first", "version": 2}
        immutable_request = source_request.model_dump(mode="json")
        parent = SimpleNamespace(id=operation_id, owner_id=owner, error_code="svg_source_quality_failed",
            request_payload={"generation_request": immutable_request})
        source_job = SimpleNamespace(id=job_id, workspace_operation_id=operation_id,
            request_payload=immutable_request, svg_style_snapshot=snapshot, total_slides=1,
            authored_outline={"slides": [{"content": "Fixture message"}]})
        asset = SimpleNamespace(digest="sha256:" + "a" * 64, offered_pages=[], slots=[],
                                presentation_id=presentation_id, source_revision=1)
        source_pointer = {"ref": str(uuid.uuid4()), "sha256": "a" * 64}
        service = SimpleNamespace(resolve_ref=AsyncMock(return_value=SimpleNamespace(created_by_operation_id=operation_id)))
        monkeypatch.setattr(artifact_store, "configured_public_artifact_service", lambda: service)
        monkeypatch.setattr(svg_asset_persistence, "load_source_manifest_pointer", AsyncMock(return_value=asset))
        input_digest = recovery.digest(dict(content="Fixture message", style=snapshot, index=0, count=1,
            instructions=None, language="en", preset=None))
        input_digest = recovery.digest({"page": input_digest, "assets": {}})
        raw = '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720"><text x="80" y="100" font-family="Noto Sans KR" font-size="32" font-weight="400" fill="#123456" text-anchor="start">Fixture message</text></svg>'
        original = dict(svg_content=None, input_digest=input_digest, inflight=None,
            source_manifest_ref=source_pointer, source_manifest_digest=asset.digest,
            initial_quality={"passed": False, "failures": [{"code": "required_text_missing"}]})
        normalized, first_svg, author_digest, _, _, advisories = recovery._replay_sealed_author_transform(
            original, raw, request=source_request, job=source_job, asset=asset, index=0)
        author_receipt = dict(contract_version="svg-static/v2", provider="fixture", requested_model="fixture-model",
            style_digest=snapshot["digest"], authoring_profile=dict(ART_DIRECTION_PROFILE_V8),
            raw_digest="sha256:" + sha256_hex(raw.encode()), source_digest="sha256:" + author_digest,
            validation_error=None, native_declarations=None, vector_icons_used=[], attempt_ordinal=1)
        metrics = {"owned_text": [{"text": "Fixture message"}]}
        measured = dict(passed=False, measurement_complete=True,
            failures=[{"code": "required_text_missing"}], source_digest="sha256:" + sha256_hex(first_svg.encode()),
            browser_metrics=metrics, metrics_digest="sha256:" + sha256_hex(json.dumps(metrics,
                sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode()),
            png_digest="sha256:" + "b" * 64, wrapper_digest="sha256:" + "c" * 64)
        original.update(source_digest=author_digest, last_valid_svg="historical-invalid-layout",
            fontAdvisories=advisories, attempts=[dict(kind="provider", raw_response=raw, receipt=author_receipt,
                                                    quality=deepcopy(measured))], initial_quality=deepcopy(measured))
        terminal = dict(schema="svg-provider-terminal/v1", terminal_event="response.completed",
            operation_id=str(operation_id), job_id=str(job_id), owner_id=str(owner), tenant_id=tenant,
            input_digest=input_digest, raw_response=raw, output_digest=sha256_hex(raw.encode()), usage={})
        terminal["receipt_digest"] = recovery.digest(terminal)
        original["provider_terminal_responses"] = {"author/1": terminal}
        row = SimpleNamespace(slide_index=0, author_status="running", render_status="pending",
                              persist_status="pending", svg_checkpoint=original)
        calls = [SimpleNamespace(slide_index=0, stage="author", outcome="succeeded", state="reported",
            reconciled_at=object(), provider_id="fixture", model_id="fixture-model",
            model_dump=lambda **kwargs: {"stage": "author", "slide_index": 0, "outcome": "succeeded"})]
        kwargs = dict(job=source_job, operation=parent, episode_calls=calls, completed=terminal,
            session=object(), request=source_request, binding=None, vectors={}, vector_seed_witness=None)
        before = deepcopy(original)
        projected, witness = asyncio.run(recovery._completed_source_author_projection(original, row, **kwargs))
        assert original == before and projected["attempts"] == original["attempts"]
        assert projected["svg_content"] == normalized and witness["phase"] == "author-only"
        assert "quality" not in projected and "initial_quality" not in projected and "png_sha256" not in projected
        page = dict(slide_index=0, category="known", row_digest="fixture-row", phase="author-only",
            svg_sha256=witness["render_svg_sha256"], source_svg_sha256=witness["source_svg_sha256"],
            final_render_svg_sha256=witness["render_svg_sha256"], input_digest=input_digest,
            author_receipt_digest=recovery.author_witness(projected))
        manifest = {"pages": [page]}
        plan = SimpleNamespace(manifest=manifest, manifest_hash=recovery.digest(manifest),
                               baseline_payloads={0: projected})
        projected_before_stale_case = deepcopy(projected)
        plan.baseline_payloads[0] = deepcopy(plan.baseline_payloads[0])
        plan.archive = {"job": {"svg_style_snapshot": deepcopy(snapshot)}}
        plan.baseline_payloads[0]["final_selected_render_evidence"] = {"schemaVersion": "fixture-stale-final-review"}
        stale_source_before = deepcopy(plan.baseline_payloads[0])
        child = recovery.cached_page_payload(row, plan, uuid.uuid4())
        assert "final_selected_render_evidence" not in child
        assert plan.baseline_payloads[0] == stale_source_before
        assert projected == projected_before_stale_case
        assert not child.get("quality") and not child.get("png_sha256")
        invalid_final_child = deepcopy(child)
        invalid_final_child["final_selected_render_evidence"] = deepcopy(stale_source_before["final_selected_render_evidence"])
        with pytest.raises(ValueError, match="svg_final_selected_evidence_unproven"):
            recovery.verify_cached_page(invalid_final_child, plan.manifest, 0, input_digest)
        assert recovery.verify_cached_page(child, manifest, 0, input_digest) is page
        assert "quality" not in child and "png_sha256" not in child and original == before
        bad_latest = deepcopy(original)
        bad_raw = raw.replace('<text ', '<text xml:space="preserve" ')
        bad_author_digest = sha256_hex(ET.tostring(ET.fromstring(bad_raw), encoding="unicode").encode())
        bad_receipt = {**deepcopy(author_receipt), "attempt_ordinal": 2,
            "raw_digest": "sha256:" + sha256_hex(bad_raw.encode()), "source_digest": "sha256:" + bad_author_digest}
        bad_terminal = {**deepcopy(terminal), "raw_response": bad_raw, "output_digest": sha256_hex(bad_raw.encode())}
        bad_terminal["receipt_digest"] = recovery.digest({key: value for key, value in bad_terminal.items() if key != "receipt_digest"})
        bad_latest["attempts"].append(dict(kind="provider", raw_response=bad_raw, receipt=bad_receipt))
        bad_latest["provider_terminal_responses"]["author/2"] = bad_terminal
        bad_latest.update(source_digest=bad_author_digest, last_valid_svg=first_svg)
        current_raw = raw.replace('fill="#123456"', 'fill="#234567"')
        current_author_digest = sha256_hex(ET.tostring(ET.fromstring(current_raw), encoding="unicode").encode())
        current_receipt = {**deepcopy(author_receipt), "attempt_ordinal": 2,
            "raw_digest": "sha256:" + sha256_hex(current_raw.encode()),
            "source_digest": "sha256:" + current_author_digest}
        current_terminal = {**deepcopy(terminal), "raw_response": current_raw,
            "output_digest": sha256_hex(current_raw.encode())}
        current_normalized, _, _, _, _, current_advisories = recovery._replay_sealed_author_transform(
            original, current_raw, request=source_request, job=source_job, asset=asset, index=0)
        retained = deepcopy(original)
        retained["attempts"].append(dict(kind="provider", raw_response=current_raw, receipt=current_receipt))
        retained.update(svg_content=current_normalized,
            source_digest=sha256_hex(current_normalized.encode()), fontAdvisories=current_advisories,
            initial_quality={**deepcopy(measured), "stage": "first_response"}, last_valid_svg=first_svg)
        retained["provider_terminal_responses"] = {
            "1": {**deepcopy(terminal), "ordinal": 1},
            "2": {**current_terminal, "ordinal": 2},
        }
        for record in retained["provider_terminal_responses"].values():
            record["receipt_digest"] = recovery.digest({
                key: value for key, value in record.items() if key != "receipt_digest"})
        retained_row = SimpleNamespace(author_status="completed", render_status="pending",
            persist_status="pending", render_path=None, render_sha256=None)
        retained_before = deepcopy(retained)
        retained_chain = recovery._author_render_chain(retained, current_raw, current_receipt,
            request=source_request, job=source_job, asset=asset, index=0,
            row=retained_row, completed=retained["provider_terminal_responses"]["2"])
        assert retained_chain["phase"] == "author-only"
        assert retained_chain["source_svg_sha256"] == sha256_hex(current_normalized.encode())
        assert retained == retained_before
        for field, value in (("raw_response", raw + " "), ("quality", {
                **measured, "source_digest": "sha256:" + "0" * 64})):
            tampered = deepcopy(retained)
            tampered["attempts"][0][field] = value
            with pytest.raises(recovery.PartialRecoveryRejected):
                recovery._author_render_chain(tampered, current_raw, current_receipt,
                    request=source_request, job=source_job, asset=asset, index=0,
                    row=retained_row, completed=tampered["provider_terminal_responses"]["2"])
        bad_before = deepcopy(bad_latest)
        fallback, selected_witness = asyncio.run(recovery._completed_source_author_projection(
            bad_latest, row, **{**kwargs, "completed": bad_terminal}))
        assert bad_latest == bad_before and fallback["attempts"][:-1] == bad_latest["attempts"]
        assert fallback["svg_content"] == normalized and selected_witness["phase"] == "author-only"
        assert selected_witness["completed_receipt_digest"] == terminal["receipt_digest"]
        replay = fallback["attempts"][-1]
        assert replay["kind"] == "sealed_terminal_replay" and replay["raw_response"] == raw
        assert replay["receipt"]["terminal_recovery_witness"]["attempt_ordinal"] == 1
        assert "quality" not in fallback and "png_sha256" not in fallback
        fallback_page = {**page, "author_receipt_digest": recovery.author_witness(fallback)}
        fallback_manifest = {"pages": [fallback_page]}
        fallback_plan = SimpleNamespace(manifest=fallback_manifest, manifest_hash=recovery.digest(fallback_manifest),
                                        baseline_payloads={0: fallback})
        fallback_child = recovery.cached_page_payload(row, fallback_plan, uuid.uuid4())
        assert recovery.verify_cached_page(fallback_child, fallback_manifest, 0, input_digest) is fallback_page
        unproven = deepcopy(bad_latest)
        unproven["attempts"][0]["quality"]["source_digest"] = "sha256:" + "0" * 64
        with pytest.raises(recovery.PartialRecoveryRejected, match="svg_partial_recovery_local_output_requires_repair"):
            asyncio.run(recovery._completed_source_author_projection(unproven, row,
                **{**kwargs, "completed": bad_terminal}))
        all_bad = deepcopy(bad_latest)
        all_bad["attempts"] = [all_bad["attempts"][-1]]
        with pytest.raises(recovery.PartialRecoveryRejected, match="svg_partial_recovery_local_output_requires_repair"):
            asyncio.run(recovery._completed_source_author_projection(all_bad, row,
                **{**kwargs, "completed": bad_terminal}))
        for violation in ("unknown", "body", "receipt", "input", "owner"):
            candidate = deepcopy(original)
            if violation == "unknown":
                candidate["provider_failures"] = [{"code": "public_provider_result_unknown"}]
            elif violation == "body":
                candidate["attempts"][-1]["receipt"]["raw_digest"] = "sha256:" + "0" * 64
            elif violation == "receipt":
                candidate["attempts"][-1]["receipt"]["validation_error"] = "static-validation-failed"
            elif violation == "input":
                candidate["input_digest"] = "unbound-input"
            else:
                monkeypatch.setattr("utils.owner_context.get_current_owner_id", lambda: uuid.uuid4())
            with pytest.raises(recovery.PartialRecoveryRejected):
                asyncio.run(recovery._completed_source_author_projection(candidate, row, **kwargs))
            monkeypatch.setattr("utils.owner_context.get_current_owner_id", lambda: owner)
        legacy_request = GeneratePresentationRequest(content="Fixture message", strategy="svg",
                                                     n_slides=1, language="en", common_header_mode="free")
        legacy_request._svg_quality_recovery_policy = {"id": "svg-quality-first", "version": 1}
        source_job.svg_style_snapshot = official_pin_svg_style(legacy_request)
        legacy_cache_plan = deepcopy(plan)
        legacy_cache_plan.archive = {"job": {"svg_style_snapshot": deepcopy(source_job.svg_style_snapshot)}}
        legacy_cached_child = recovery.cached_page_payload(row, legacy_cache_plan, uuid.uuid4())
        assert legacy_cached_child["final_selected_render_evidence"] == stale_source_before["final_selected_render_evidence"]
        with pytest.raises(ValueError, match="svg_final_selected_evidence_unproven"):
            recovery.verify_cached_page(legacy_cached_child, legacy_cache_plan.manifest, 0, input_digest)
        from utils.hashing import canonical_json_digest
        from services.svg_authoring_profile import read_authoring_profile
        no_policy_snapshot = deepcopy(snapshot)
        no_policy_snapshot.pop("quality_recovery_policy", None)
        no_policy_snapshot.pop("quality_recovery_plan", None)
        no_policy_snapshot["digest"] = canonical_json_digest({k: v for k, v in no_policy_snapshot.items() if k != "digest"}, allow_nan=True)
        assert read_authoring_profile(no_policy_snapshot) == ART_DIRECTION_PROFILE_V8
        assert read_quality_recovery_policy(no_policy_snapshot) is None
        no_policy_cache_plan = deepcopy(plan)
        no_policy_cache_plan.archive = {"job": {"svg_style_snapshot": no_policy_snapshot}}
        no_policy_cached_child = recovery.cached_page_payload(row, no_policy_cache_plan, uuid.uuid4())
        assert no_policy_cached_child["final_selected_render_evidence"] == stale_source_before["final_selected_render_evidence"]
        assert no_policy_cache_plan.baseline_payloads[0] == stale_source_before
        with pytest.raises(ValueError, match="svg_final_selected_evidence_unproven"):
            recovery.verify_cached_page(no_policy_cached_child, no_policy_cache_plan.manifest, 0, input_digest)
        assert read_quality_recovery_policy(source_job.svg_style_snapshot) == {"id": "svg-quality-first", "version": 1}
        assert asyncio.run(recovery._completed_source_author_projection(original, row, **kwargs)) is None
        assert original == before
        from services.generation_core.drivers.svg import partial_quality_recovery, quality_recovery
        from services.generation_core.quality.advisory_record import advisory_record, content_digest
        source_job.svg_style_snapshot = snapshot
        source_job.presentation_id = presentation_id
        prior = {**deepcopy(projected), "svg_content": first_svg, "last_valid_svg": first_svg}
        source_hash = sha256_hex(first_svg.encode())
        source_page = dict(category="known", phase="finalized", svg_sha256=source_hash,
            input_digest=input_digest, author_receipt_digest=recovery.author_witness(prior), row_digest="original-source-row")
        source_manifest = {"known_indices": [0], "pages": [source_page]}
        prior["partial_recovery"] = dict(schema="svg-partial-recovery-page/v1", operation_id=str(operation_id),
            manifest_hash=recovery.digest(source_manifest), slide_index=0, source_row_digest=source_page["row_digest"], category="known")
        target = dict(axis="hierarchy_emphasis", evidenceRegion="Topic and claim",
            visibleEvidence="Generic topic dominates the source claim.", sourceEvidence="Source states the primary claim.",
            improvementGoal="Give the primary source claim visual priority.")
        runtime = "fixture-quality-runtime"
        gate_body = dict(schemaVersion="svg-quality-first-design-gate/v1", policy=snapshot["quality_recovery_policy"],
            snapshotDigest=content_digest(snapshot), runtimeBinding=runtime, pageCoverageComplete=True,
            pageDigests=[source_hash], pngDigests=["b" * 64],
            binding=dict(jobId=str(job_id), operationId=str(operation_id), presentationId=str(presentation_id),
                         ownerId=str(owner), tenantId=tenant),
            pages=[dict(index=0, evaluated=True, failedAxes=[],
                axes={"hierarchy_emphasis": {"verdict": "passed", "repairScope": "none"}}, designTargets=[target])])
        gate = {**gate_body, "digest": content_digest(gate_body)}
        current_review = advisory_record({"designGate": gate, "slides": []}, [source_hash])
        decision = dict(repair_scope="design_rewrite", target_axes=["hierarchy_emphasis"], issues=[], design_targets=[target])
        candidate = deepcopy(prior)
        candidate.update(svg_content=None, correction_mode="design_rewrite", quality_recovery=dict(status="repair_pending", decision=decision,
            prior_candidate={"svg_digest": source_hash}))
        repair_ctx = SimpleNamespace(snapshot=snapshot, count=1, partial_recovery_manifest=source_manifest,
            quality_recovery_runtime_binding=runtime, quality_recovery_input_binding=runtime)
        seed = {"quality_recovery_execution": {"binding": runtime,
            "rounds": [{"status": "completed", "advisory": current_review}]}}
        async def current_checkpoint(*args):
            return deepcopy(seed)
        monkeypatch.setattr(quality_recovery, "_restore_candidate", lambda *args: deepcopy(prior))
        assert asyncio.run(partial_quality_recovery.current_quality_repair_authorized(
            repair_ctx, candidate, 0, source_job, current_checkpoint)) is True
        assert partial_quality_recovery.current_episode_attempt_count(repair_ctx, candidate, 0) == 0
        from services.generation_core.quality.vision_advisory_prompt import DESIGN_AXES
        retained_selection = {"selected": "prior", "reason": "comparison_improvement_unjudged"}
        compared_gate = deepcopy(gate)
        compared_gate["pages"][0]["mandatoryEvidenceValid"] = True
        compared_gate["pageDigests"] = ["sha256:" + "c" * 64]
        compared_gate["pngDigests"] = ["sha256:" + "d" * 64]
        paired_body = {
            "index": 0, "evaluated": True, "priorPageDigest": gate["pageDigests"][0],
            "priorPngDigest": gate["pngDigests"][0], "candidatePageDigest": compared_gate["pageDigests"][0],
            "candidatePngDigest": compared_gate["pngDigests"][0], "targetAxes": decision["target_axes"],
            "targetGoals": deepcopy(decision["design_targets"]), "priorInputDigest": input_digest,
            "priorMandatoryEvidenceValid": True, "comparisonMode": "joint-targets-current-source-context/v1",
            "comparisonCohortDigest": "fixture-current-comparison-cohort", "deckContextDigest": compared_gate.get("deckContextDigest"),
            "priorAxes": {axis: {"verdict": "passed", "repairScope": "none"} for axis in DESIGN_AXES},
            "axisChanges": {axis: {"change": "unjudged" if axis == "information_structure" else "improved",
                                      "visibleEvidence": "Synthetic comparison evidence."} for axis in DESIGN_AXES},
            "blindedPresentation": {"candidateLabel": "B", "priorLabel": "A", "displayOrder": ["A", "B"],
                                    "chronologyExposed": False, "versionExposed": False},
        }
        compared_gate["candidateComparisons"] = [{**paired_body, "digest": content_digest(paired_body)}]
        compared_gate["digest"] = content_digest({k: v for k, v in compared_gate.items() if k != "digest"})
        assert recovery._v2_retained_comparison_matches(retained_selection, decision, gate, compared_gate, 0, input_digest)
        assert not recovery._v2_retained_comparison_matches({"selected": "prior", "reason": "target_goal_not_improved"}, decision, gate, compared_gate, 0, input_digest)
        for violation in ("source", "png", "goal", "blind", "unknown", "unmeasured", "missing_prior_axes", "invalid_prior_axes"):
            altered = deepcopy(compared_gate)
            proof = altered["candidateComparisons"][0]
            if violation == "source":
                proof["priorPageDigest"] = "sha256:" + "0" * 64
            elif violation == "png":
                proof["candidatePngDigest"] = "sha256:" + "0" * 64
            elif violation == "goal":
                proof["targetGoals"][0]["improvementGoal"] = "Different unauthorized goal."
            elif violation == "blind":
                proof["blindedPresentation"]["versionExposed"] = True
            elif violation == "missing_prior_axes":
                proof.pop("priorAxes")
            elif violation == "invalid_prior_axes":
                proof["priorAxes"] = []
            elif violation == "unmeasured":
                altered["pages"][0]["mandatoryEvidenceValid"] = False
            else:
                altered["pages"][0]["evaluated"] = False
            proof["digest"] = content_digest({k: v for k, v in proof.items() if k != "digest"})
            altered["digest"] = content_digest({k: v for k, v in altered.items() if k != "digest"})
            assert not recovery._v2_retained_comparison_matches(retained_selection, decision, gate, altered, 0, input_digest)
        assert not recovery._v2_retained_comparison_matches(retained_selection, decision, gate, compared_gate, 0, "different-input")
        not_improved = deepcopy(compared_gate)
        proof = not_improved["candidateComparisons"][0]
        for axis_change in proof["axisChanges"].values():
            axis_change["change"] = "equivalent"
        proof["digest"] = content_digest({k: v for k, v in proof.items() if k != "digest"})
        not_improved["digest"] = content_digest({k: v for k, v in not_improved.items() if k != "digest"})
        assert recovery._v2_retained_comparison_matches({"selected": "prior", "reason": "target_goal_not_improved"}, decision, gate, not_improved, 0, input_digest)
        assert not recovery._v2_retained_comparison_matches(retained_selection, decision, gate, not_improved, 0, input_digest)
        purchased = deepcopy(candidate)
        purchased["attempts"].append({"kind": "provider", "raw_response": "new-candidate", "receipt": {}})
        assert partial_quality_recovery.current_episode_attempt_count(repair_ctx, purchased, 0) == 1
        # Initial cached bytes remain strict; only this exact current repair is authorized.
        with pytest.raises(recovery.PartialRecoveryRejected, match="cached_source_changed"):
            recovery.verify_cached_page(candidate, source_manifest, 0, input_digest)
        for mismatch in ("decision", "scope", "archive", "input", "operation", "history", "review"):
            invalid = deepcopy(candidate)
            before_seed = deepcopy(seed)
            if mismatch == "decision":
                invalid["quality_recovery"]["decision"]["design_targets"][0]["improvementGoal"] = "Unapproved different goal."
            elif mismatch == "scope":
                invalid["correction_mode"] = "local_repair"
            elif mismatch == "archive":
                invalid["quality_recovery"]["prior_candidate"]["svg_digest"] = "0" * 64
            elif mismatch == "input":
                invalid["input_digest"] = "changed-input"
            elif mismatch == "operation":
                invalid["partial_recovery"]["operation_id"] = str(uuid.uuid4())
            elif mismatch == "history":
                invalid["attempts"][0]["raw_response"] = "changed-original-history"
            else:
                seed["quality_recovery_execution"]["rounds"][0]["status"] = "review_unknown"
            if mismatch == "history":
                with pytest.raises(ValueError, match="cached_source_changed"):
                    asyncio.run(partial_quality_recovery.current_quality_repair_authorized(
                        repair_ctx, invalid, 0, source_job, current_checkpoint))
            else:
                assert asyncio.run(partial_quality_recovery.current_quality_repair_authorized(
                    repair_ctx, invalid, 0, source_job, current_checkpoint)) is False
            seed.clear()
            seed.update(before_seed)
        legacy_gate = {**deepcopy(gate_body), "policy": {"id": "svg-quality-first", "version": 1},
                       "snapshotDigest": content_digest(source_job.svg_style_snapshot)}
        # A fully sealed v1 review with only design targets grants no new repair authority.
        source_job.svg_style_snapshot = official_pin_svg_style(legacy_request)
        repair_ctx.snapshot = source_job.svg_style_snapshot
        legacy_gate["snapshotDigest"] = content_digest(repair_ctx.snapshot)
        legacy_gate["digest"] = content_digest(legacy_gate)
        seed["quality_recovery_execution"]["rounds"][0]["advisory"] = advisory_record({"designGate": legacy_gate, "slides": []}, [source_hash])
        assert not recovery._v2_retained_comparison_matches(retained_selection, decision, legacy_gate, compared_gate, 0, input_digest)
        assert asyncio.run(partial_quality_recovery.current_quality_repair_authorized(
            repair_ctx, candidate, 0, source_job, current_checkpoint)) is False
        assert original == before
