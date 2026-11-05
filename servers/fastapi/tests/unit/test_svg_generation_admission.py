import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import sqlalchemy
from fastapi import HTTPException


def _json_plain(value):
    if isinstance(value, SimpleNamespace):
        return {key: _json_plain(item) for key, item in vars(value).items()}
    if isinstance(value, (list, tuple)):
        return [_json_plain(item) for item in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


class _SourceManifestAsset(SimpleNamespace):
    """Stub of a loaded source manifest: partial recovery seals it by ``model_dump``."""

    def model_dump(self, **_kwargs):
        return {key: _json_plain(value) for key, value in vars(self).items()}


def _source_manifest_asset(**fields):
    return _SourceManifestAsset(**fields)



def _source_manifest_reference(created_by_operation_id):
    """Stub of the artifact reference ``resolve_ref`` returns for a source manifest.

    Partial recovery binds the reference's kind and media type before it loads
    the manifest, so a stub must carry them.
    """

    return SimpleNamespace(
        kind="source-manifest",
        media_type="application/json",
        created_by_operation_id=created_by_operation_id,
    )


from models.authored_vnext.deck import MasterDigestPin
from models.authored_vnext.master.contract import DeckMasterContract
from models.generate_presentation_request import AuthoredMasterPinInput, GeneratePresentationRequest
from services.authored_vnext.generation.errors import PublishedMasterRequiredError
from services.authored_vnext.generation.legacy_pin import ResolvedLegacyMasterPin
from services.public_operation_admission import PUBLIC_DEFAULT_MAX_SLIDES, PUBLIC_DEFAULT_POLICY_VERSION
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
            policy = PublicPolicyProfile(version=PUBLIC_DEFAULT_POLICY_VERSION, max_slides=PUBLIC_DEFAULT_MAX_SLIDES, retry_request_limit=8,
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
                    local.setattr("services.svg_common_header_design_calls.preflight_header_selector", lambda: {
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
    from services.generation_core.drivers.svg.native_requirements import requested_native_data
    native_source = '| 처리 유형 | 설명 |\n| --- | --- |\n| 이벤트참여형 경품·혜택 중심 처리 | 보존 |'
    native_literals = literal_source_values(native=requested_native_data(native_source))
    assert "이벤트참여형 경품·혜택 중심 처리" in native_literals and "경품·혜택" in native_literals
    joined_display = "이벤트참여형경품·혜택 중심 처리"
    assert assess_visible_nodes([joined_display], source_values=native_literals)["hard_violation_counts"] == {}
    assert assess_visible_nodes([joined_display], source_values=native_literals)["protected_symbol_counts"] == {"middle_dot": 1}
    assert assess_visible_nodes([joined_display])["hard_violation_counts"] == {"middle_dot": 1}
    generic = "DevAX·AI는 동기화에 활용합니다."
    assert assess_visible_nodes([generic], source_values=literal_source_values(generic))["hard_violation_counts"] == {"middle_dot": 1}
    declared_only = {"objects": [{"kind": "table", "raw": {"rows": [["경품·혜택"]]}}]}
    assert literal_source_values(native=declared_only) == ()
    edge_literals = literal_source_values(native={"tables": [{"rows": [[
        "행 · 열", "행 ·앞 열", "행 뒤· 열", "행 — 열"]]}]})
    assert not {"·", "·앞", "뒤·", "—"} & set(edge_literals)
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
    many_violations = '<svg xmlns="http://www.w3.org/2000/svg">' + ''.join(
        f'<text id="node-{index}">' + '앞 문맥 ' * 30 + f'항목{index}·다음항목'
        + ' 뒤 문맥' * 30 + '</text>' for index in range(15)) + '</svg>'
    broad_witnesses = svg_writing_repair_witnesses(many_violations)
    assert len(broad_witnesses) == 12
    assert [item["qid"] for item in broad_witnesses] == [f"svg-{index}" for index in range(1, 13)]
    assert sum(len(item["text"]) for item in broad_witnesses) <= 720
    assert len(json.dumps(broad_witnesses, ensure_ascii=False, separators=(",", ":"))) <= 2200
    for witness in broad_witnesses:
        assert witness["text"][witness["symbols"][0]["start"]] == "·"
    assert len(svg_writing_repair_witnesses(many_violations, limit=2)) == 2
    assert svg_writing_repair_witnesses(many_violations, source_values=svg_text_nodes(many_violations)) == []

    import struct
    from pathlib import Path
    from llmai.shared import SystemMessage, UserMessage
    from services.generation_core.authoring.author_slide_svg import SVG_AUTHOR_RULES
    from services.generation_core.quality.vision_advisory import _design_input_sizes
    from services.svg_common_header_generation import _design_input, _selection_input
    from utils.hashing import canonical_json_digest
    from services.svg_common_header_design_calls import (
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
    assert all("body_checks" not in record for record in selected_input["candidates"])
    body_failures = [{"kind": "asset_icon_omitted"}, {"kind": "source_text_paint_occluded"}]
    scoped = {candidate_id: {**page, "eligibility": "header", "payload": {**page["payload"],
        "quality": {"passed": False, "failures": body_failures, "common_header": {"passed": True}},
        "terminal": {"remakeOutcome": "svg_fallback_page"}}} for candidate_id, page in pages.items()}
    scoped_input, _ = _selection_input(ctx, batch_request, batch_outline, batch_plan,
        {"selector_capability": {"provider": "test", "model": "test"}}, candidates, scoped, batch_index, lambda _: png)
    for record in scoped_input["candidates"]:
        assert record["required_checks"] == {"passed": True, "scope": "header", "failures": [], "error": None}
        assert record["body_checks"] == {"passed": False, "failures": body_failures,
            "page_outcome": "svg_fallback_page", "handling": "page_rescue_after_selection"}
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
            assert page["nativeRequirements"] == requested_native_data(
                rejected_outline.slides[document["representative_index"]].content)
            assert any("지역·팀" in cell.split() for table in page["nativeRequirements"]["tables"]
                       for row in table["rows"] for cell in row)
            assert "지역·팀" in source_values
            if after["hard_violation_counts"] == {"middle_dot": 10}:
                without_native_token = assess_visible_nodes(svg_text_nodes(page["svg_content"]),
                    source_values=tuple(value for value in source_values if value != "지역·팀"))
                assert without_native_token["hard_violation_counts"] == {"middle_dot": 11}
                assert after["protected_symbol_counts"]["middle_dot"] == (
                    without_native_token["protected_symbol_counts"]["middle_dot"] + 1)
            assert "long_dash" not in after["hard_violation_counts"]
            assert page["quality"] == before and before["writing_policy"]["hard_violation_counts"]["long_dash"] == 1
            outcomes.append(after["hard_violation_counts"])
        assert {} in outcomes and {"middle_dot": 10} in outcomes
        assert saved["last_error"] == retained_failure

    import uuid
    from services.svg_common_header_transport import (
        TRANSPORT_RULES, pack_header_transport, restore_header_transport,
    )
    alias_raw = deepcopy(original_payload)
    reference = {"identity": {"colors": ["#123456"], "fonts": ["Noto Sans KR"]},
        "guidance": "Preserve exact typography, spacing and reference identity. " * 80,
        "source_ref": "exact-reference-source", "metadata": {"document_name": "  원본 문서명  "}}
    alias_raw["style"]["design_pack_template"] = deepcopy(reference)
    alias_raw["header_requirements"]["reference_content"] = deepcopy(reference)
    alias_scoped = _design_prompt_payload(alias_raw)
    alias_before = deepcopy(alias_scoped)
    alias_packed, alias_proof = pack_header_transport(alias_scoped)
    assert alias_packed["header_requirements"]["reference_content"] == {
        "$header_context_alias": "/style/design_pack_template"}
    assert alias_packed["style"]["design_pack_template"]["metadata"]["document_name"] == "  원본 문서명  "
    assert restore_header_transport(alias_packed, alias_proof["source_mapping"],
        expected_context_digest=canonical_json_digest(alias_scoped)) == alias_scoped
    assert alias_scoped == alias_before
    assert "/style/design_pack_template" in TRANSPORT_RULES
    for version in ("svg-header-source-handles/v1", "svg-header-source-handles/v2"):
        prior = deepcopy(alias_packed)
        prior["transport_source_mapping"]["version"] = version
        with pytest.raises(ValueError, match="svg_common_header_transport_input_invalid"):
            restore_header_transport(prior, alias_proof["source_mapping"],
                expected_context_digest=canonical_json_digest(alias_scoped))
        prior["header_requirements"]["reference_content"] = deepcopy(prior["style"]["design_pack_template"])
        if version.endswith("/v1"):
            prior["available_values"]["field_columns"][2] = "user_fixed"
            for fields in prior["available_values"]["rows"]:
                for field in fields:
                    field[2] = bool(field[2])
        assert restore_header_transport(prior, alias_proof["source_mapping"],
            expected_context_digest=canonical_json_digest(alias_scoped)) == alias_scoped
    for mutation in ("path", "extra", "missing", "target", "unresolved"):
        tampered = deepcopy(alias_packed)
        if mutation == "path":
            tampered["header_requirements"]["reference_content"]["$header_context_alias"] = "/style/tokens"
        elif mutation == "extra":
            tampered["header_requirements"]["reference_content"]["extra"] = True
        elif mutation == "missing":
            del tampered["style"]["design_pack_template"]
        elif mutation == "target":
            tampered["style"]["design_pack_template"]["metadata"]["document_name"] += "changed"
        else:
            tampered["representative"]["unresolved"] = {"$header_context_alias": "/style/design_pack_template"}
        with pytest.raises(ValueError, match="svg_common_header_transport_input_invalid"):
            restore_header_transport(tampered, alias_proof["source_mapping"],
                expected_context_digest=canonical_json_digest(alias_scoped))
    unequal = deepcopy(alias_scoped)
    unequal["header_requirements"]["reference_content"]["metadata"]["document_name"] += "different"
    unequal_packed, unequal_proof = pack_header_transport(unequal)
    assert "$header_context_alias" not in unequal_packed["header_requirements"]["reference_content"]
    assert restore_header_transport(unequal_packed, unequal_proof["source_mapping"],
        expected_context_digest=canonical_json_digest(unequal)) == unequal
    different_types = deepcopy(alias_scoped)
    different_types["style"]["design_pack_template"]["flag"] = 1
    different_types["header_requirements"]["reference_content"]["flag"] = True
    types_packed, types_proof = pack_header_transport(different_types)
    assert "$header_context_alias" not in types_packed["header_requirements"]["reference_content"]
    assert restore_header_transport(types_packed, types_proof["source_mapping"],
        expected_context_digest=canonical_json_digest(different_types)) == different_types
    assert context_bytes(alias_packed, DESIGN_RULES + TRANSPORT_RULES, candidate_schema(alias_raw), compact=True) < context_bytes(
        unequal_packed, DESIGN_RULES + TRANSPORT_RULES, candidate_schema(unequal), compact=True)
    with pytest.raises(ValueError, match="svg_common_header_transport_input_invalid"):
        pack_header_transport({**alias_scoped, "unexpected": {"$header_context_alias": "/style/design_pack_template"}})
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
            if legacy.get("header_requirements", {}).get("reference_content") == {
                    "$header_context_alias": "/style/design_pack_template"}:
                legacy["header_requirements"]["reference_content"] = deepcopy(legacy["style"]["design_pack_template"])
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

    from contextlib import asynccontextmanager
    from utils.codex_stream_adapter import _ObservedResponses, _PROVIDER_EVENT_OBSERVER
    from utils.hashing import sha256_hex
    from utils.provider_overload import ProviderOutcomeError
    from services import svg_common_header_design_calls as header_calls

    raw = '{"selected_candidate_id":"candidate-a"}'
    event = SimpleNamespace(type="response.completed", response=SimpleNamespace(
        id="resp_headerfixture", status="completed", incomplete_details=None,
        usage=SimpleNamespace(input_tokens=12, output_tokens=7, total_tokens=19),
        output=[SimpleNamespace(type="message", content=[SimpleNamespace(type="output_text", text=raw)])]))
    bounded_plan = {"header_call_limit": 4, "call_timeout_seconds": 180,
        "input_token_limit": 32768, "output_token_limit": 4096,
        "provider": "codex", "model": "header-fixture"}

    for failure in (None, "usage", "admission", "terminal_save"):
        terminal_document, saved, dispatched, usage_calls = {"steps": {}}, [], [], []
        save_failed = False

        async def save_terminal_document(terminal_document=terminal_document, failure=failure, saved=saved):
            nonlocal save_failed
            step = terminal_document["steps"]["selection-1"]
            if failure == "terminal_save" and "terminal_evidence" in step and not save_failed:
                save_failed = True
                raise RuntimeError("private terminal save failure")
            saved.append(deepcopy(terminal_document))

        class HeaderClient:
            def __init__(self):
                self.observer = _PROVIDER_EVENT_OBSERVER.get()
                self._client = SimpleNamespace(with_options=lambda **kwargs: self._client)

        async def generate_header(client, dispatched=dispatched, **kwargs):
            assert client.observer is not None
            assert kwargs["call_timeout_seconds"] == 180
            dispatched.append("physical")
            await asyncio.to_thread(lambda: list(_ObservedResponses._events(
                iter([event]), observer=client.observer)))
            return SimpleNamespace(content={"selected_candidate_id": "candidate-a"},
                response=SimpleNamespace(content=raw))

        @asynccontextmanager
        async def admit_header(failure=failure, **kwargs):
            yield
            if failure == "admission":
                raise RuntimeError("private admission failure")

        async def record_header_usage(failure=failure, usage_calls=usage_calls, **kwargs):
            usage_calls.append(kwargs)
            if failure == "usage":
                raise RuntimeError("private usage failure")

        async def terminal_call(terminal_document=terminal_document, save_terminal_document=save_terminal_document):
            return await _paid_step(terminal_document, key="selection-1", stage="selection", ordinal=1,
                payload=selection, images=screens, plan=bounded_plan,
                job=SimpleNamespace(id=uuid.uuid4(), attempt_number=1, public_only=False),
                save=save_terminal_document)

        with patch.object(header_calls, "get_client", side_effect=lambda **kwargs: HeaderClient()), \
             patch.object(header_calls, "get_llm_config", return_value=None), \
             patch.object(header_calls, "get_model", return_value="header-fixture"), \
             patch.object(header_calls, "get_llm_provider", return_value=SimpleNamespace(value="codex")), \
             patch.object(header_calls, "enforce_llm_egress_policy"), \
             patch.object(header_calls, "preflight_header_selector"), \
             patch.object(header_calls, "get_generate_kwargs", side_effect=lambda **kwargs: kwargs), \
             patch.object(header_calls, "_generate_structured_content", side_effect=generate_header), \
             patch.object(header_calls, "provider_call_admission", side_effect=admit_header), \
             patch.object(header_calls, "record_provider_call", side_effect=record_header_usage), \
             patch("services.generation_core.quality.vision_advisory._check_design_input",
                return_value={"serializedContextBytes": 123}):
            if failure is None:
                response = asyncio.run(terminal_call())
                assert response["raw_response"] == raw
                assert asyncio.run(terminal_call()) == response
            else:
                with pytest.raises(ProviderOutcomeError if failure == "terminal_save" else RuntimeError):
                    asyncio.run(terminal_call())
                with pytest.raises(ValueError, match="svg_common_header_provider_result_unknown"):
                    asyncio.run(terminal_call())
        step = saved[-1]["steps"]["selection-1"]
        assert dispatched == ["physical"]
        assert step["status"] == ("succeeded" if failure is None else "unknown")
        assert step["terminal_evidence"]["raw_response"] == raw
        assert step["terminal_evidence"]["output_digest"] == sha256_hex(raw.encode("utf-8"))
        assert step["terminal_evidence"]["usage"] == {
            "input_tokens": 12, "output_tokens": 7, "total_tokens": 19}
        if failure != "terminal_save":
            assert saved[1]["steps"]["selection-1"]["status"] == "started"
            assert "terminal_evidence" in saved[1]["steps"]["selection-1"]
        if failure is not None:
            assert "private" not in str(step["failure_diagnostics"])
            assert "response" not in step
        assert len(usage_calls) == 1


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
        request_payload={},
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
        request_payload={"common_header_mode": "uniform"},
        authored_outline={"slides": [
            {"content": slide.content, "title": slide.title, "common_header": binding}
            for slide, binding in zip(outline.slides, bindings, strict=False)
        ]})
    for index, binding in enumerate(bindings):
        initial = svg_page_input_digest(content=outline.slides[index].content, style=style,
            index=index, count=2, instructions=None, language=None, preset=None,
            writing_policy=WRITING_POLICY_ID, header_page=binding, assets=page_assets)
        assert initial == _expected_page_input_digest(current, frozen, index, page_assets)
    broken = deepcopy(frozen)
    broken.authored_outline["slides"][0]["content"] = "원본과 다른 내용"
    with pytest.raises(ValueError, match="svg_partial_recovery_header_binding_invalid"):
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
    def run(source, callback):
        return asyncio.run(prepare_missing_header_titles(
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
                   for page, title in zip(batch["pages"], ["운영 계획", "서비스 구성"], strict=False)]}
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
        assert final["review_call_limit"] == ((slide_count + 4) // 5 + 1) * 2
        assert final["review_retry_limit"] == 1
    else:
        assert final is None
    total = execution["review_call_limit"] + (final["review_call_limit"] if final else 0)
    assert svg_quality_review_admission_budget(current, slide_count) == total
    if slide_count == 80:
        assert total == 102

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

    # Frozen restoration plans retain their previous physical allocation.
    frozen_rounds = 1 if slide_count == 1 else 2
    frozen_calls = frozen_rounds * ((slide_count + 4) // 5 + (slide_count > 5))
    frozen_plan = dict(schema="svg-quality-recovery-plan/v2" if slide_count == 1
                       else "svg-quality-recovery-plan/v1", slide_count=slide_count,
                       review_round_limit=frozen_rounds, page_attempt_limit=3,
                       review_group_size=5, review_call_limit=frozen_calls)
    if slide_count > 1:
        from services.svg_final_selected_continuation import final_review_control
        frozen_plan["final_selected_review_control"] = final_review_control(
            slide_count, physical_offset=frozen_calls + (frozen_rounds if slide_count > 5 else 0))
    restoration_snapshot = frozen(current, frozen_plan)
    frozen_copy = deepcopy(restoration_snapshot)
    restoration = SimpleNamespace(_svg_style_snapshot=restoration_snapshot)
    assert seal_new_quality_recovery_plan(restoration, slide_count) == frozen_plan
    assert restoration_snapshot == frozen_copy
    frozen_execution = read_quality_recovery_execution_plan(restoration_snapshot)
    frozen_final = frozen_execution.get("final_selected_review")
    assert svg_quality_review_admission_budget(restoration, slide_count) == (
        frozen_execution["review_call_limit"] + (frozen_final["review_call_limit"] if frozen_final else 0))

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
    for field, value in (("physical_offset", 999), ("run_limit", True),
                         ("namespace", "unadmitted"), ("review_retry_limit", True)):
        changed = deepcopy(snapshot)
        changed["quality_recovery_plan"]["final_selected_review_control"][field] = value
        changed["digest"] = canonical_json_digest({k: v for k, v in changed.items() if k != "digest"}, allow_nan=True)
        with pytest.raises(ValueError, match="svg_quality_recovery_plan_invalid"):
            read_quality_recovery_plan(changed)
    assert global_format_retry_control(snapshot) is None
    manifest = dict(global_format_retry_control={"global_retry_limit": True})
    with pytest.raises(ValueError, match="svg_quality_recovery_execution_plan_invalid"):
        read_quality_recovery_execution_plan(snapshot, manifest=manifest)
    with pytest.raises(ValueError, match="svg_quality_recovery_execution_plan_invalid"):
        read_quality_recovery_execution_plan(snapshot, manifest={"final_selected_review_control": {}})


def _svg_request(**overrides):
    base = dict(
        strategy="svg", source_mode=None,
        generation_engine="authored", generation_mode="auto", master_pin=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_master_pin_alone_no_longer_blocks_admission():
    request = _svg_request(
        master_pin=AuthoredMasterPinInput(master_id="master:acme-1", revision=1, digest="sha256:" + "1" * 64)
    )
    # No exception: master_pin admissibility is decided later, once resolved
    # and projected (see _resolve_master_rules), not by presence alone.
    require_svg_creation_admission(request)


def test_uploaded_design_reference_conflicts_with_a_master_pin():
    snapshot_id = uuid.uuid4()
    require_svg_creation_admission(_svg_request(svg_design_reference_snapshot_id=snapshot_id))
    pin = AuthoredMasterPinInput(master_id="master:acme-1", revision=1, digest="sha256:" + "1" * 64)
    with pytest.raises(HTTPException) as error:
        require_svg_creation_admission(_svg_request(svg_design_reference_snapshot_id=snapshot_id, master_pin=pin))
    assert error.value.detail == "svg_design_reference_conflict"


def test_uploaded_design_reference_requires_its_admitted_projection():
    request = _svg_request(svg_design_reference_snapshot_id=uuid.uuid4())
    canvas = SimpleNamespace(width_px=720, height_px=1280)
    with pytest.raises(HTTPException) as error:
        _resolve_design_reference(request, canvas)
    assert error.value.detail == "svg_uploaded_design_reference_admission_required"
    request._svg_uploaded_design_reference = {"version": "svg-uploaded-design-reference/v1"}
    assert _resolve_design_reference(request, canvas) == {"version": "svg-uploaded-design-reference/v1"}
    assert _resolve_design_reference(_svg_request(), canvas) is None


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


@pytest.mark.parametrize("profile_id", [None, 'current-v4', 'art-direction-v1', 'art-direction-v1@2', 'art-direction-v1@3', 'art-direction-v1@4', 'art-direction-v1@5', 'art-direction-v1@6', 'art-direction-v1@7', 'art-direction-v1@8', 'art-direction-v1@9'])
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
    expected = {'art-direction-v1': ART_DIRECTION_PROFILE, 'art-direction-v1@2': ART_DIRECTION_PROFILE_V2, 'art-direction-v1@3': ART_DIRECTION_PROFILE_V3, 'art-direction-v1@4': ART_DIRECTION_PROFILE_V4, 'art-direction-v1@5': ART_DIRECTION_PROFILE_V5, 'art-direction-v1@6': ART_DIRECTION_PROFILE_V6, 'art-direction-v1@7': ART_DIRECTION_PROFILE_V7, None: {'id': 'art-direction-v1', 'version': 9}, 'art-direction-v1@8': {'id': 'art-direction-v1', 'version': 8}, 'art-direction-v1@9': {'id': 'art-direction-v1', 'version': 9}}[profile_id]
    assert payload[INTERNAL_SVG_AUTHORING_PROFILE_KEY] == expected
    assert snapshot["authoring_profile"] == expected
    new_contract_keys = ({"readability_policy", "common_header_design_plan", "native_requirements_revision"}
                         if profile_id is None else set())
    if profile_id is None:
        from services.document_canvas_generation import validate_svg_readability_policy
        from services.svg_common_header_design_plan import validate_header_design_plan

        assert snapshot["native_requirements_revision"] == "svg-native-requirements/v3"

        assert validate_svg_readability_policy(snapshot["readability_policy"])["minimum_font_pt"] == 9
        assert validate_header_design_plan(snapshot["common_header_design_plan"])["schema"] == "svg-common-header-design-plan/v3"
    else:
        assert "readability_policy" not in snapshot
        assert "native_requirements_revision" not in snapshot
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


@pytest.mark.parametrize("profile", ["future-v2", "art-direction-v1@10", {"id": "art-direction-v1", "version": 10}, {"id": "art-direction-v1", "version": True}])
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


def test_partial_recovery_live_heartbeat_preserves_authority_and_history(monkeypatch, caplog):
    import asyncio
    import json
    from copy import deepcopy
    from datetime import datetime, timedelta, timezone
    from unittest.mock import AsyncMock
    from services import svg_partial_recovery as recovery
    from models.sql.workspace import WorkspaceOperation
    from models.sql.presentation_generation_job import PresentationGenerationJob
    from models.sql.public_admission import PublicOperationAdmission, PublicProviderLogicalCall, PublicResourceLease
    from models.sql.provider_runtime_reservation import ProviderRuntimeBudget, ProviderRuntimeReservation
    from models.sql.provider_usage_receipt import ProviderUsageReceipt
    from utils.hashing import sha256_hex

    class Row(SimpleNamespace):
        def model_dump(self, **kwargs):
            return json.loads(json.dumps(vars(self), default=lambda value:
                value.isoformat() if isinstance(value, datetime) else str(value)))

    now = datetime(2026, 10, 18, tzinfo=timezone.utc)
    # Match the final Core query boundary: standard JSON decode, plain typed
    # CP validation, then immediate seal without exposing the fresh row.
    from models.sql.authored_slide_checkpoint import AuthoredSlideCheckpoint
    db_json = json.loads('{"text":"원문 12.5%","nested":[{"number":3,"flag":true,"empty":null}],"negative_zero":-0.0}')
    fresh_cp = AuthoredSlideCheckpoint.model_validate(dict(id=uuid.uuid4(), job_id=uuid.uuid4(),
        slide_index=0, svg_checkpoint=db_json, author_started_at=now))
    original_cp_dump = AuthoredSlideCheckpoint.model_dump
    original_cp_json = json.dumps(fresh_cp.svg_checkpoint, ensure_ascii=False, sort_keys=True)
    original_cp_seal = recovery.digest(original_cp_dump(fresh_cp, mode="json"))
    dump_calls = []
    def tracked_fresh_cp_dump(self, **kwargs):
        dump_calls.append(kwargs)
        return original_cp_dump(self, **kwargs)
    with monkeypatch.context() as fresh_cp_scope:
        fresh_cp_scope.setattr(AuthoredSlideCheckpoint, "model_dump", tracked_fresh_cp_dump)
        fresh_cp_scope.setattr(recovery, "_same_json_value", lambda *args:
            (_ for _ in ()).throw(AssertionError("fresh DB seal must not rescan payload")))
        assert recovery._fresh_producer_checkpoint_digest(fresh_cp) == original_cp_seal
        assert dump_calls == [{"mode": "json", "exclude": {"svg_checkpoint"}}]
        assert json.dumps(fresh_cp.svg_checkpoint, ensure_ascii=False, sort_keys=True) == original_cp_json
        fresh_cp.svg_checkpoint["nested"][0]["number"] = 4
        changed_cp_seal = recovery._fresh_producer_checkpoint_digest(fresh_cp)
        assert changed_cp_seal == recovery.digest(original_cp_dump(fresh_cp, mode="json"))
        assert changed_cp_seal != original_cp_seal
        fresh_cp.svg_checkpoint = None
        dump_calls.clear()
        assert recovery._fresh_producer_checkpoint_digest(fresh_cp) == recovery.digest(original_cp_dump(fresh_cp, mode="json"))
        assert dump_calls == [{"mode": "json"}]
    fake_cp = Row(id=uuid.uuid4(), job_id=uuid.uuid4(), slide_index=0,
                  svg_checkpoint={"nested": {"value": 1}}, updated_at=now)
    assert recovery._fresh_producer_checkpoint_digest(fake_cp) == recovery.digest(recovery._dump(fake_cp))
    owner, operation_id, job_id, admission_id = (uuid.uuid4() for _ in range(4))
    operation = Row(id=operation_id, owner_id=owner, state="running", contract_version="public-operation/v2",
        attempt_token="live-attempt", attempt_number=1, fencing_token=9, deadline_at=now + timedelta(hours=1),
        heartbeat_at=now, lease_expires_at=now + timedelta(minutes=1), updated_at=now,
        version=7, next_event_sequence=8, progress={"stage": "outline", "percent": 8},
        cancel_requested_at=None, request_payload={"source": "frozen"})
    ancestor = Row(**{**vars(deepcopy(operation)), "id": uuid.uuid4(), "state": "failed"})
    job = Row(id=job_id, workspace_operation_id=operation_id, state="running", attempt_token="live-attempt",
        request_payload={"source": "frozen"}, svg_style_snapshot={"digest": "frozen-style"}, heartbeat_at=now,
        updated_at=now, total_slides=2, completed_slides=0, current_stage="outline")
    admission = Row(id=admission_id, operation_id=operation_id, owner_id=owner, tenant_id="fixture",
        state="active", fence=9, deadline_at=operation.deadline_at, actual_cost_limit_microunits=100,
        estimated_cost_limit_microunits=100, actual_consumed_microunits=0, updated_at=now,
        planned_request_limit=10, retry_request_limit=5, policy_digest="frozen-policy",
        initial_request_count=0, retry_request_count=0, estimated_consumed_microunits=0,
        estimated_reserved_microunits=0, actual_reserved_microunits=0, actual_cost_unavailable_count=0)
    leases = [Row(id=uuid.uuid4(), admission_id=admission_id, operation_id=operation_id,
        state="leased", attempt=1, fence=9, lease_owner_hash=sha256_hex(b"live-attempt"),
        deadline_at=operation.deadline_at, heartbeat_at=now, lease_expires_at=operation.lease_expires_at,
        version=1, scope_key=str(index)) for index in range(3)]
    budget = Row(id=uuid.uuid4(), job_id=None, workspace_operation_id=operation_id,
        tenant_id="fixture", owner_id=owner, state="active", cancellation_reason=None,
        estimated_limit=100, actual_limit=100, actual_consumed=0, estimated_consumed=0,
        estimated_reserved=0, request_count=0, version=0, updated_at=now)
    old_admission_id, old_budget_id, old_job_id = (uuid.uuid4() for _ in range(3))
    old_call = Row(id=uuid.uuid4(), admission_id=old_admission_id, state="reported")
    old_usage = Row(id=uuid.uuid4(), job_id=old_job_id, actual_cost=1)
    old_reservation = Row(id=uuid.uuid4(), budget_id=old_budget_id, state="reconciled")
    old_admission = Row(id=old_admission_id, operation_id=ancestor.id, state="failed")
    old_budget = Row(id=old_budget_id, workspace_operation_id=ancestor.id, job_id=None, actual_limit=100)
    database = {WorkspaceOperation: [operation, ancestor], PresentationGenerationJob: [job],
        PublicOperationAdmission: [admission, old_admission], PublicResourceLease: leases, ProviderRuntimeBudget: [old_budget],
        PublicProviderLogicalCall: [old_call], ProviderUsageReceipt: [old_usage],
        ProviderRuntimeReservation: [old_reservation]}
    transaction = object()
    session = SimpleNamespace(sync_session=SimpleNamespace(get_transaction=lambda: transaction))
    monkeypatch.setattr("utils.owner_context.get_current_owner_id", lambda: owner)
    monkeypatch.setattr("utils.owner_context.get_current_tenant_id", lambda: "fixture")
    monkeypatch.setattr(recovery, "get_current_utc_datetime", lambda: now + timedelta(seconds=10))
    async def fresh_rows(session, model, predicate, **kwargs):
        rows = database.get(model, [])
        if model is PublicResourceLease:
            bindings = predicate.compile().params
            admitted = bindings.get("admission_id_1", [])
            operation_binding = bindings.get("operation_id_1")
            rows = [row for row in rows if row.admission_id in admitted
                or operation_binding is not None and row.operation_id == operation_binding]
        return rows

    monkeypatch.setattr(recovery, "_fresh_recovery_model_rows", AsyncMock(side_effect=fresh_rows))

    # Retaining a seal memo cannot own the verifier or its hydrated archive graph.
    import gc
    import weakref
    gc_was_enabled = gc.isenabled()
    gc.disable()
    try:
        lifetime = recovery._EpisodeVerification(session)
        retained_memo = lifetime.seal_memo
        lifetime_ref = weakref.ref(lifetime)
        lifetime.checkpoint_archives[("lifetime",)] = ({"checkpoints": [{"payload": "memo"}]}, b"guard")
        assert type(retained_memo["verification"]) is weakref.ReferenceType
        assert retained_memo["verification"]() is lifetime
        assert recovery._seal_memo_verification(retained_memo, verification=lifetime) is lifetime
        del lifetime
        assert lifetime_ref() is None
        with pytest.raises(recovery.PartialRecoveryRejected,
            match="svg_partial_recovery_request_snapshot_changed"):
            recovery._seal_memo_verification(retained_memo)
        # Rejection tracebacks may retain locals; use a separate verifier after the lifetime assertion.
        lifetime = recovery._EpisodeVerification(session)
        retained_memo = lifetime.seal_memo
        lifetime_ref = weakref.ref(lifetime)
        other_lifetime = recovery._EpisodeVerification(session)
        with pytest.raises(recovery.PartialRecoveryRejected,
            match="svg_partial_recovery_request_snapshot_changed"):
            recovery._seal_memo_verification(retained_memo, verification=other_lifetime)
        with pytest.raises(recovery.PartialRecoveryRejected,
            match="svg_partial_recovery_request_snapshot_changed"):
            recovery._seal_memo_verification(dict(retained_memo))
        callable_calls = []
        retained_memo["verification"] = lambda: callable_calls.append(True)
        with pytest.raises(recovery.PartialRecoveryRejected,
            match="svg_partial_recovery_request_snapshot_changed"):
            recovery._seal_memo_verification(retained_memo)
        assert callable_calls == []
        retained_memo["verification"] = lifetime_ref
    finally:
        if gc_was_enabled:
            gc.enable()

    async def exercise():
        verification = recovery._EpisodeVerification(session)
        verification.bind_live(operation, job)
        verification.expect(WorkspaceOperation, recovery._dump(operation))
        verification.expect(WorkspaceOperation, recovery._dump(ancestor))
        verification.expect(PresentationGenerationJob, recovery._dump(job))
        await verification.ledger({str(operation_id)}, str(job_id))
        operation.heartbeat_at += timedelta(seconds=5)
        operation.lease_expires_at += timedelta(seconds=5)
        operation.updated_at += timedelta(seconds=5)
        operation.version += 1
        operation.next_event_sequence += 1
        operation.progress = {"stage": "author", "percent": 20, "current": 1, "total": 2}
        job.heartbeat_at += timedelta(seconds=5)
        job.updated_at += timedelta(seconds=5)
        job.current_stage = "author"
        job.completed_slides = 1
        admission.updated_at += timedelta(seconds=5)
        admission.initial_request_count = 2
        admission.retry_request_count = 1
        admission.estimated_consumed_microunits = 2
        admission.actual_consumed_microunits = 3
        admission.estimated_reserved_microunits = 4
        admission.actual_reserved_microunits = 4
        admission.actual_cost_unavailable_count = 1
        budget.updated_at += timedelta(seconds=5)
        budget.estimated_consumed = 2
        budget.actual_consumed = 3
        budget.estimated_reserved = 4
        budget.request_count = 3
        budget.version = 2
        database[ProviderRuntimeBudget].insert(0, budget)
        database[PublicProviderLogicalCall].append(Row(id=uuid.uuid4(), admission_id=admission_id, state="reserved"))
        database[ProviderUsageReceipt].append(Row(id=uuid.uuid4(), job_id=job_id, actual_cost=3))
        database[ProviderRuntimeReservation].append(Row(id=uuid.uuid4(), budget_id=budget.id, state="reserved"))
        for lease in leases:
            lease.heartbeat_at += timedelta(seconds=5)
            lease.lease_expires_at += timedelta(seconds=5)
            lease.version += 1
        await verification.finish()
        admission.estimated_reserved_microunits = 0
        admission.actual_reserved_microunits = 0
        admission.estimated_consumed_microunits += 4
        admission.actual_consumed_microunits = 2
        admission.actual_cost_unavailable_count = 0
        budget.estimated_reserved = 0
        budget.estimated_consumed += 4
        budget.actual_consumed += 4
        budget.version += 1
        database[PublicProviderLogicalCall][-1].state = "reported"
        database[ProviderRuntimeReservation][-1].state = "reconciled"
        await verification.finish()
        unchanged_authority = deepcopy(database)
        for violation in ("fence", "owner", "state", "token", "cancel", "progress", "op_version",
                "heartbeat_backwards", "lease_backwards", "expired", "deadline", "lease_owner",
                "lease_fence", "lease_state", "lease_version", "ancestor", "frozen", "job", "job_total",
                "job_state", "job_token", "job_request", "job_completed", "job_heartbeat", "budget",
                "budget_owner", "budget_binding", "budget_state", "budget_ghost", "budget_missing", "budget_unbound",
                "admission_cap", "admission_planned", "admission_retry", "admission_policy", "admission_ghost",
                "admission_negative", "admission_overcalls", "job_backwards", "event_backwards",
                "ancestor_call", "ancestor_usage", "ancestor_reservation", "ancestor_budget", "lease_ghost",
                "lease_foreign_admission", "membership"):
            database.clear()
            database.update(deepcopy(unchanged_authority))
            current = database[WorkspaceOperation][0]
            lease = database[PublicResourceLease][0]
            if violation == "fence":
                current.fencing_token += 1
            elif violation == "owner":
                current.owner_id = uuid.uuid4()
            elif violation == "state":
                current.state = "cancelled"
            elif violation == "token":
                current.attempt_token = "another-attempt"
            elif violation == "cancel":
                current.cancel_requested_at = now
            elif violation == "progress":
                current.progress = {"percent": 101}
            elif violation == "op_version":
                current.version -= 1
            elif violation == "event_backwards":
                current.next_event_sequence -= 1
            elif violation == "heartbeat_backwards":
                current.heartbeat_at = now + timedelta(seconds=4)
            elif violation == "lease_backwards":
                current.lease_expires_at = now + timedelta(seconds=64)
            elif violation == "expired":
                current.lease_expires_at = now + timedelta(seconds=9)
            elif violation == "deadline":
                lease.lease_expires_at = now + timedelta(hours=2)
            elif violation == "lease_owner":
                lease.lease_owner_hash = "0" * 64
            elif violation == "lease_fence":
                lease.fence += 1
            elif violation == "lease_state":
                lease.state = "released"
            elif violation == "lease_version":
                lease.version = 1
            elif violation == "ancestor":
                database[WorkspaceOperation][1].heartbeat_at += timedelta(seconds=1)
            elif violation == "frozen":
                current.request_payload = {"source": "changed"}
            elif violation == "job":
                database[PresentationGenerationJob][0].svg_style_snapshot = {"digest": "changed"}
            elif violation == "job_total":
                database[PresentationGenerationJob][0].total_slides += 1
            elif violation == "job_state":
                database[PresentationGenerationJob][0].state = "cancelled"
            elif violation == "job_token":
                database[PresentationGenerationJob][0].attempt_token = "another-attempt"
            elif violation == "job_request":
                database[PresentationGenerationJob][0].request_payload = {"source": "changed"}
            elif violation == "job_completed":
                database[PresentationGenerationJob][0].completed_slides = 3
            elif violation == "job_backwards":
                database[PresentationGenerationJob][0].completed_slides = 0
            elif violation == "job_heartbeat":
                database[PresentationGenerationJob][0].heartbeat_at = now
            elif violation == "budget":
                database[ProviderRuntimeBudget][0].actual_limit += 1
            elif violation == "budget_owner":
                database[ProviderRuntimeBudget][0].owner_id = uuid.uuid4()
            elif violation == "budget_binding":
                database[ProviderRuntimeBudget][0].job_id = job_id
            elif violation == "budget_state":
                database[ProviderRuntimeBudget][0].state = "cancelled"
            elif violation == "budget_ghost":
                database[ProviderRuntimeBudget].append(Row(**{**vars(deepcopy(budget)), "id": uuid.uuid4()}))
            elif violation == "budget_missing":
                database[ProviderRuntimeBudget].pop(0)
            elif violation == "budget_unbound":
                database[ProviderRuntimeBudget][0].workspace_operation_id = None
                database[ProviderRuntimeBudget][0].job_id = job_id
            elif violation.startswith("admission_"):
                target = database[PublicOperationAdmission][0]
                if violation == "admission_cap":
                    target.actual_cost_limit_microunits += 1
                elif violation == "admission_planned":
                    target.planned_request_limit += 1
                elif violation == "admission_retry":
                    target.retry_request_limit += 1
                elif violation == "admission_policy":
                    target.policy_digest = "changed"
                elif violation == "admission_negative":
                    target.actual_consumed_microunits = -1
                elif violation == "admission_overcalls":
                    target.initial_request_count = target.planned_request_limit + 1
                else:
                    database[PublicOperationAdmission].append(Row(**{**vars(deepcopy(target)), "id": uuid.uuid4()}))
            elif violation == "ancestor_call":
                database[PublicProviderLogicalCall][0].state = "failed"
            elif violation == "ancestor_usage":
                database[ProviderUsageReceipt][0].actual_cost += 1
            elif violation == "ancestor_reservation":
                database[ProviderRuntimeReservation][0].state = "cancelled"
            elif violation == "ancestor_budget":
                database[ProviderRuntimeBudget][1].actual_limit += 1
            elif violation == "lease_ghost":
                database[PublicResourceLease].append(Row(**{**vars(deepcopy(lease)), "id": uuid.uuid4()}))
            elif violation == "lease_foreign_admission":
                database[PublicResourceLease].append(Row(**{**vars(deepcopy(lease)),
                    "id": uuid.uuid4(), "admission_id": uuid.uuid4()}))
            else:
                database[PublicResourceLease].pop()
            with pytest.raises(recovery.PartialRecoveryRejected, match="svg_partial_recovery_request_snapshot_changed"):
                await verification.finish()
        for foreign_key in ("operation_id", "admission_id"):
            database.clear()
            database.update(deepcopy(unchanged_authority))
            ghost = deepcopy(database[PublicResourceLease][0])
            ghost.id = uuid.uuid4()
            setattr(ghost, foreign_key, uuid.uuid4())
            database[PublicResourceLease].append(ghost)
            initial = recovery._EpisodeVerification(session)
            initial.bind_live(database[WorkspaceOperation][0], database[PresentationGenerationJob][0])
            with pytest.raises(recovery.PartialRecoveryRejected, match="svg_partial_recovery_request_snapshot_changed"):
                await initial.ledger({str(operation_id)}, str(job_id))

    asyncio.run(exercise())

    async def exercise_slow_snapshot():
        initial_operation, initial_job = deepcopy(operation), deepcopy(job)
        current_time = [now + timedelta(seconds=10)]
        fresh_operation = deepcopy(initial_operation)

        async def fresh_live_rows(session, model, predicate, **kwargs):
            assert model is WorkspaceOperation
            return [fresh_operation]

        with monkeypatch.context() as scoped:
            scoped.setattr(recovery, "get_current_utc_datetime", lambda: current_time[0])
            scoped.setattr(recovery, "_fresh_recovery_model_rows", AsyncMock(side_effect=fresh_live_rows))
            proof = recovery._EpisodeVerification(session)
            proof.bind_live(initial_operation, initial_job)
            current_time[0] = now + timedelta(minutes=2)
            fresh_operation.heartbeat_at = fresh_operation.updated_at = current_time[0]
            fresh_operation.lease_expires_at = now + timedelta(minutes=5)
            assert initial_operation.lease_expires_at < current_time[0]
            proof.expect(WorkspaceOperation, recovery._dump(initial_operation))
            await proof.finish()

            expired = recovery._EpisodeVerification(session)
            expired.bind_live(fresh_operation, initial_job)
            current_time[0] = now + timedelta(minutes=6)
            expired.expect(WorkspaceOperation, recovery._dump(fresh_operation))
            with pytest.raises(recovery.PartialRecoveryRejected, match="request_snapshot_changed"):
                await expired.finish()
            with pytest.raises(recovery.PartialRecoveryRejected, match="request_snapshot_changed"):
                recovery._EpisodeVerification(session).bind_live(fresh_operation, initial_job)

    asyncio.run(exercise_slow_snapshot())

    def exercise_canonical_projection():
        import math
        import sys
        from utils import owner_context

        shared = ["immutable text", {"count": 1}]
        frozen = {"first": shared, "second": shared, "flag": True, "number": 1, "zero": 0.0}
        reference = deepcopy(frozen)
        assert reference is not frozen and reference["first"] is not shared
        assert reference["first"][0] is shared[0]
        assert recovery._same_json_value(reference, reference) is True
        encoded = recovery._marshal_json_bytes(reference)
        actual = deepcopy(frozen)
        actual["second"] = deepcopy(actual["second"])
        assert actual["first"] is not actual["second"]
        assert recovery._same_json_snapshot(reference, actual, encoded) is True

        real_same, real_digest = recovery._same_json_value, recovery.digest
        comparisons, hashes = [], []
        with monkeypatch.context() as scoped:
            scoped.setattr(recovery, "_same_json_value", lambda left, right:
                comparisons.append((left, right)) or real_same(left, right))
            if encoded is not None:
                assert recovery._same_json_snapshot(reference, actual, encoded) is True
                assert comparisons == []
            reordered = dict(reversed(list(actual.items())))
            assert recovery._same_json_snapshot(reference, reordered, encoded) is True
            assert comparisons
            for key, value in (("flag", 1), ("number", 1.0), ("zero", -0.0),
                               ("zero", float("nan")), ("zero", float("inf"))):
                changed = deepcopy(frozen)
                changed[key] = value
                expected_comparison = None if type(value) is float and not math.isfinite(value) else False
                assert recovery._same_json_snapshot(reference, changed, encoded) is expected_comparison
            changed = deepcopy(frozen)
            changed["first"][1]["count"] = 2
            assert recovery._same_json_snapshot(reference, changed, encoded) is False

        for base, value in ((dict, {"x": 1}), (list, [1]), (int, 1), (float, 1.0), (str, "x")):
            subclass = type("NonJsonSubclass", (base,), {})
            unsupported = subclass(value)
            baseline = deepcopy(value)
            assert recovery._marshal_json_bytes(unsupported) is None
            assert recovery._same_json_snapshot(baseline, unsupported,
                recovery._marshal_json_bytes(baseline)) is False
            assert recovery._same_json_snapshot({"value": baseline}, {"value": unsupported},
                recovery._marshal_json_bytes({"value": baseline})) is False
        for value in (float("nan"), float("inf"), (1,), object()):
            assert recovery._same_json_value(value, value) is None
        assert recovery._same_json_snapshot([1], (1,), recovery._marshal_json_bytes([1])) is False
        assert recovery._same_json_snapshot("x", b"x", recovery._marshal_json_bytes("x")) is False
        cyclic = {}
        cyclic["self"] = cyclic
        assert recovery._marshal_json_bytes(cyclic) is None
        assert recovery._same_json_value(cyclic, cyclic) is None
        assert recovery._same_json_snapshot({}, cyclic, recovery._marshal_json_bytes({})) is False
        with monkeypatch.context() as scoped:
            scoped.setattr(sys, "implementation", SimpleNamespace(name="pypy"))
            assert recovery._marshal_json_bytes(frozen) is None
            assert recovery._same_json_snapshot(reference, actual, encoded) is True
        with monkeypatch.context() as scoped:
            scoped.setattr(sys, "version_info", (3, 12, 0))
            assert recovery._marshal_json_bytes(frozen) is None
        import marshal
        with monkeypatch.context() as scoped:
            def unavailable(*args):
                raise ValueError("private encoding unavailable")
            scoped.setattr(marshal, "dumps", unavailable)
            assert recovery._same_json_snapshot(reference, actual, encoded) is True
        transaction = [object()]
        session = SimpleNamespace(sync_session=SimpleNamespace(get_transaction=lambda: transaction[0]))
        # Preserve real JSON-mode projection at this boundary, including nested mutation.
        projected_job = SimpleNamespace(id=uuid.uuid4())
        projection = {"id": str(projected_job.id), "request_payload": deepcopy(frozen)}
        projected_job.model_dump = lambda **kwargs: deepcopy(projection)
        with monkeypatch.context() as scoped:
            scoped.setattr(recovery, "digest", lambda value: hashes.append(deepcopy(value)) or real_digest(value))
            proof = recovery._EpisodeVerification(session)
            expected = proof.job_fingerprint(projected_job)
            assert proof.job_fingerprint(projected_job) == expected and len(hashes) == 1
            saved = proof.job_fingerprints[str(projected_job.id)][0]
            assert saved is not projection and saved["request_payload"] is not projection["request_payload"]
            projection["request_payload"]["first"][1]["count"] = 9
            assert proof.job_fingerprint(projected_job) != expected and len(hashes) == 2
            assert saved["request_payload"]["first"][1]["count"] == 1
            projection["request_payload"] = deepcopy(frozen)
            projection["request_payload"]["number"] = 1.0
            assert proof.job_fingerprint(projected_job) != expected and len(hashes) == 3
            projection["request_payload"] = deepcopy(frozen)
            proof.expect(PresentationGenerationJob, projection)
            before = len(hashes)
            proof.expect(PresentationGenerationJob, deepcopy(projection))
            assert len(hashes) == before
            projection["request_payload"]["flag"] = 1
            with pytest.raises(recovery.PartialRecoveryRejected):
                proof.expect(PresentationGenerationJob, projection)
            assert len(hashes) == before + 1
            transaction[0] = object()
            with pytest.raises(recovery.PartialRecoveryRejected):
                proof.job_fingerprint(projected_job)
            transaction[0] = proof.transaction
            with monkeypatch.context() as principal:
                principal.setattr(owner_context, "get_current_owner_id", lambda: uuid.uuid4())
                with pytest.raises(recovery.PartialRecoveryRejected):
                    proof.job_fingerprint(projected_job)
        transaction[0] = None
        detached = recovery._EpisodeVerification(session)
        detached.job_fingerprint(projected_job)
        detached.job_fingerprint(projected_job)
        assert detached.job_fingerprints == {}


        # Exercise the actual copy boundary and retain real comparator/hash code.
        import json as copy_json
        import sys as copy_sys
        real_copy = recovery.deepcopy
        copy_fallbacks = []

        def track_copy(value):
            copy_fallbacks.append(type(value))
            return real_copy(value)

        pure = {"nested": [{"value": 3}], "large": 2 ** 200,
            "negative_zero": -0.0, "flags": [True, 1, 1.0],
            "unicode": "한글🚀", "last": {"child": [7]}}
        legacy_fingerprint = real_digest(pure)
        with monkeypatch.context() as copy_scope:
            copy_scope.setattr(recovery, "deepcopy", track_copy)
            copied, copied_hash, copied_bytes = recovery._json_fingerprint_snapshot(
                pure, legacy_fingerprint)
            assert copy_fallbacks == []
            assert copied_hash == legacy_fingerprint == real_digest(copied)
            assert recovery._same_json_value(pure, copied) is True
            assert list(copied) == list(pure)
            assert copied_bytes == recovery._marshal_json_bytes(copied)
            assert copied["negative_zero"].hex() == (-0.0).hex()
            assert [type(v) for v in copied["flags"]] == [bool, int, float]
            copied["nested"][0]["value"] = 4
            copied["last"]["child"].append(8)
            assert pure["nested"][0]["value"] == 3
            assert pure["last"]["child"] == [7]

            shared_value = {"child": [1]}
            aliased = [shared_value, shared_value]
            detached_alias, _, _ = recovery._json_fingerprint_snapshot(aliased, "a" * 64)
            assert recovery._same_json_value(aliased, detached_alias) is True
            assert detached_alias[0] is not shared_value
            assert detached_alias[1] is not shared_value
            detached_alias[0]["child"].append(2)
            assert shared_value["child"] == [1]

            lone = {"text": "\ud800"}
            lone_copy, _, _ = recovery._json_fingerprint_snapshot(lone, "b" * 64)
            assert lone_copy == lone and lone_copy is not lone
            assert [ord(c) for c in lone_copy["text"]] == [0xD800]
            paired = {"text": "\ud83d\ude00"}
            before_fallback = len(copy_fallbacks)
            pair_copy, _, _ = recovery._json_fingerprint_snapshot(paired, "c" * 64)
            assert len(copy_fallbacks) == before_fallback + 1
            assert pair_copy == paired and pair_copy is not paired
            assert [ord(c) for c in pair_copy["text"]] == [0xD83D, 0xDE00]

            huge = {"integer": 10 ** 5000}
            before_fallback = len(copy_fallbacks)
            huge_copy, _, _ = recovery._json_fingerprint_snapshot(huge, "d" * 64)
            assert huge_copy == huge and huge_copy is not huge
            if 0 < copy_sys.get_int_max_str_digits() <= 5000:
                assert len(copy_fallbacks) == before_fallback + 1

            cyclic_copy_input = {}
            cyclic_copy_input["self"] = cyclic_copy_input
            before_fallback = len(copy_fallbacks)
            cyclic_copy, _, _ = recovery._json_fingerprint_snapshot(cyclic_copy_input, "e" * 64)
            assert len(copy_fallbacks) == before_fallback + 1
            assert cyclic_copy is not cyclic_copy_input
            assert cyclic_copy["self"] is cyclic_copy
            for unsupported in [("tuple", [1]), {"nonfinite": float("nan")},
                {"nonfinite": float("inf")}, {1: "non_string_key"}]:
                before_fallback = len(copy_fallbacks)
                fallback_copy, _, _ = recovery._json_fingerprint_snapshot(unsupported, "f" * 64)
                assert len(copy_fallbacks) == before_fallback + 1
                assert type(fallback_copy) is type(unsupported)
            for subclass in (type("CopyInt", (int,), {}), type("CopyList", (list,), {})):
                unsupported = subclass(1) if issubclass(subclass, int) else subclass([1])
                before_fallback = len(copy_fallbacks)
                fallback_copy, _, _ = recovery._json_fingerprint_snapshot(unsupported, "f" * 64)
                assert len(copy_fallbacks) == before_fallback + 1
                assert type(fallback_copy) is subclass

            with monkeypatch.context() as failing_encoder:
                failing_encoder.setattr(copy_json, "dumps",
                    lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("copy_encoder_unavailable")))
                before_fallback = len(copy_fallbacks)
                fallback_copy, fallback_hash, _ = recovery._json_fingerprint_snapshot(pure, legacy_fingerprint)
                assert len(copy_fallbacks) == before_fallback + 1
                assert fallback_hash == legacy_fingerprint
                assert recovery._same_json_value(pure, fallback_copy) is True
                assert fallback_copy["nested"] is not pure["nested"]


        async def exercise_finish_fingerprint():
            from models.sql.authored_slide_checkpoint import AuthoredSlideCheckpoint
            row_id, row_job_id = uuid.uuid4(), uuid.uuid4()
            fresh_projection = {"id": str(row_id), "job_id": str(row_job_id),
                "svg_checkpoint": {"nested": {"value": 1}, "negative_zero": -0.0}}
            fresh_row = SimpleNamespace(id=row_id, job_id=row_job_id,
                model_dump=lambda **kwargs: deepcopy(fresh_projection))
            stable_transaction = object()
            finish_session = SimpleNamespace(sync_session=SimpleNamespace(
                get_transaction=lambda: stable_transaction))
            canonical_hashes = []

            def track_finish_hash(value):
                canonical_hashes.append(type(value))
                return real_digest(value)

            with monkeypatch.context() as finish_scope:
                fresh_select = AsyncMock(return_value=[fresh_row])
                finish_scope.setattr(recovery, "_fresh_recovery_model_rows", fresh_select)
                finish_scope.setattr(recovery, "digest", track_finish_hash)
                proof = recovery._EpisodeVerification(finish_session)
                proof.expect(AuthoredSlideCheckpoint, deepcopy(fresh_projection))
                key = (AuthoredSlideCheckpoint, "id", str(row_id))
                assert key in proof.row_fingerprints
                initial_hashes = len(canonical_hashes)
                assert initial_hashes == 1
                await proof.finish()
                await proof.finish()
                assert len(canonical_hashes) == initial_hashes
                assert fresh_select.await_count == 2

                # Exact-value/type changes retain the old full canonical path.
                for changed in (True, 1.0, 2):
                    fresh_projection["svg_checkpoint"]["nested"]["value"] = changed
                    before_hashes = len(canonical_hashes)
                    with pytest.raises(recovery.PartialRecoveryRejected,
                        match="svg_partial_recovery_request_snapshot_changed"):
                        await proof.finish()
                    assert len(canonical_hashes) == before_hashes + 1
                fresh_projection["svg_checkpoint"]["nested"]["value"] = 1
                fresh_projection["svg_checkpoint"]["negative_zero"] = 0.0
                before_hashes = len(canonical_hashes)
                with pytest.raises(recovery.PartialRecoveryRejected,
                    match="svg_partial_recovery_request_snapshot_changed"):
                    await proof.finish()
                assert len(canonical_hashes) == before_hashes + 1
                fresh_projection["svg_checkpoint"]["negative_zero"] = -0.0

                fresh_select.return_value = []
                with pytest.raises(recovery.PartialRecoveryRejected,
                    match="svg_partial_recovery_request_snapshot_changed"):
                    await proof.finish()
                extra_id = uuid.uuid4()
                extra_projection = {**deepcopy(fresh_projection), "id": str(extra_id)}
                extra_row = SimpleNamespace(id=extra_id, job_id=row_job_id,
                    model_dump=lambda **kwargs: deepcopy(extra_projection))
                fresh_select.return_value = [fresh_row, extra_row]
                with pytest.raises(recovery.PartialRecoveryRejected,
                    match="svg_partial_recovery_request_snapshot_changed"):
                    await proof.finish()
                fresh_select.return_value = [fresh_row]

                # Missing memo is never a fresh PG validation exemption.
                registered = dict(proof.row_fingerprints)
                proof.row_fingerprints.clear()
                before_hashes = len(canonical_hashes)
                await proof.finish()
                assert len(canonical_hashes) == before_hashes + 1
                proof.row_fingerprints.update(registered)

                # One final fresh scan must prove both rows and producer membership.
                proof.producers[str(row_job_id)] = {str(row_id): real_digest(deepcopy(fresh_projection))}
                before_selects = fresh_select.await_count
                await proof.finish()
                await proof.finish()
                assert fresh_select.await_count == before_selects + 2
                for fresh_rows in ([], [fresh_row, extra_row]):
                    fresh_select.return_value = fresh_rows
                    with pytest.raises(recovery.PartialRecoveryRejected,
                        match="svg_partial_recovery_request_snapshot_changed"):
                        await proof.finish()
                fresh_select.return_value = [fresh_row]
                fresh_projection["svg_checkpoint"]["nested"]["value"] = 2
                with pytest.raises(recovery.PartialRecoveryRejected,
                    match="svg_partial_recovery_request_snapshot_changed"):
                    await proof.finish()
                fresh_projection["svg_checkpoint"]["nested"]["value"] = 1
                expected_row = proof.rows[key]
                proof.rows[key] = "0" * 64
                with pytest.raises(recovery.PartialRecoveryRejected,
                    match="svg_partial_recovery_request_snapshot_changed"):
                    await proof.finish()
                proof.rows[key] = expected_row
                await proof.finish()

                # Legacy tuple projections cannot enter the exact JSON memo.
                fresh_projection["svg_checkpoint"]["unsupported"] = ("tuple", [1])
                legacy = recovery._EpisodeVerification(finish_session)
                legacy.expect(AuthoredSlideCheckpoint, deepcopy(fresh_projection))
                assert key not in legacy.row_fingerprints
                before_hashes = len(canonical_hashes)
                await legacy.finish()
                assert len(canonical_hashes) == before_hashes + 1

        asyncio.run(exercise_finish_fingerprint())

    exercise_canonical_projection()
    async def exercise_real_historical_recursion():
        from collections import Counter
        from asyncpg.pgproto.pgproto import UUID as DriverUUID
        from models.sql.async_presentation_generation_status import AsyncPresentationGenerationTaskModel
        from models.sql.authored_slide_checkpoint import AuthoredSlideCheckpoint

        class HistoryRow(SimpleNamespace):
            def model_dump(self, **kwargs):
                return json.loads(json.dumps({key: value for key, value in vars(self).items()
                    if key not in kwargs.get("exclude", ())}, default=str))

        real_digest = recovery.digest
        operations, jobs, tasks, checkpoints, history_admissions = [], [], [], [], []
        style, outline = {"digest": "history-style"}, {"slides": [{"content": "A"}, {"content": "B"}]}
        seal_identity_kind = ["driver"]

        def storage_seal(row):
            payload = row.request_payload
            archive = payload.get(recovery.ARCHIVE_KEY)
            identity = (DriverUUID(str(row.id)) if seal_identity_kind[0] == "driver"
                else str(row.id) if seal_identity_kind[0] == "string" else row.id)
            return dict(id=identity, tenant_id="fixture", payload_sha256=real_digest(payload),
                archive_schema=archive.get("schema") if archive else None,
                archive_storage_sha256=real_digest(archive) if archive else None,
                archive_canonical_sha256=real_digest(archive) if archive else None)

        def reference(row):
            control = recovery._operation_control(row)
            return dict(schema=recovery.OPERATION_REFERENCE, id=str(row.id), control=control,
                control_digest=real_digest(control), payload_ref=dict(
                    algorithm=recovery.PAYLOAD_HASH_ALGORITHM, operation_id=str(row.id),
                    owner_id=str(owner), owner_scope=str(owner), request_hash=row.request_hash,
                    **{key: value for key, value in storage_seal(row).items() if key != "id"}))

        for ordinal in range(4):
            op_id, pid, jid = (uuid.uuid4() for _ in range(3))
            task = HistoryRow(id=f"history-task-{ordinal}", status="failed")
            payload = dict(strategy="svg", presentation_id=str(pid), common_header_mode="free",
                nested={"values": [1, 2]})
            historical_job = HistoryRow(id=jid, presentation_id=pid, workspace_operation_id=op_id,
                task_id=task.id, state="failed", total_slides=2, request_payload=payload,
                svg_style_snapshot=deepcopy(style), authored_outline=deepcopy(outline),
                completed_slides=0 if ordinal == 2 else 2,
                current_stage="outline" if ordinal == 2 else "author")
            historical_op = HistoryRow(id=op_id, owner_id=owner, owner_scope=str(owner),
                kind="presentation.generate", state="failed", presentation_id=pid, conversation_id=pid,
                retry_of_operation_id=operations[-1].id if operations else None,
                request_hash="f" * 64, request_payload={})
            if operations:
                parent, producer = operations[-1], jobs[-1]
                schema = (recovery.ARCHIVE_V1 if ordinal == 1 else
                    recovery.ARCHIVE_V2 if ordinal == 2 else recovery.ARCHIVE_V3)
                producer_checkpoints = [row.model_dump() for row in checkpoints if row.job_id == producer.id]
                archive = dict(schema=schema,
                    operation_refs=[reference(row) for row in reversed(operations)],
                    job=producer.model_dump(), task=tasks[-1].model_dump(),
                    checkpoints=producer_checkpoints,
                    admissions=[row.model_dump() for row in history_admissions], logical_calls=[],
                    usage=[], resource_leases=[], runtime_budgets=[], runtime_reservations=[],
                    deck_plans=[], source_record=None)
                if schema == recovery.ARCHIVE_V1:
                    archive["operations"] = [row.model_dump() for row in operations]
                    archive.pop("operation_refs")
                elif schema == recovery.ARCHIVE_V3:
                    archive["checkpoint_refs"] = [recovery._checkpoint_reference(row,
                        owner_id=str(owner), tenant_id="fixture") for row in archive.pop("checkpoints")]
                manifest = dict(append_only_recovery=True, archive_schema=schema,
                    archive_digest=real_digest(archive), operation_id=str(parent.id),
                    source_operation_id=str(operations[0].id), presentation_id=str(parent.presentation_id),
                    job_id=str(producer.id), owner_id=str(owner), tenant_id="fixture", expected_page_count=2,
                    known_indices=[0, 1], new_indices=[], unknown_indices=[],
                    frozen_request_digest=real_digest(recovery._frozen_payload(producer.request_payload)),
                    frozen_style_digest=real_digest(style),
                    source_request_routing=recovery._source_request_routing(producer.request_payload),
                    code_digest="historical-code")
                lineage = dict(schema="svg-partial-recovery-lineage/v2", operation_id=str(op_id),
                    action_operation_id=str(parent.id), source_operation_id=str(operations[0].id),
                    job_id=str(jid), source_job_id=str(producer.id),
                    presentation_id=str(pid), source_presentation_id=str(parent.presentation_id),
                    archive_schema=schema, manifest_hash=real_digest(manifest),
                    **{key: manifest[key] for key in ("archive_digest", "known_indices", "new_indices",
                        "unknown_indices", "frozen_request_digest", "frozen_style_digest")})
                historical_job.request_payload[recovery.LINEAGE_KEY] = deepcopy(lineage)
                historical_op.request_payload = dict(principal_tenant_id="fixture",
                    recovery_revision=recovery.REVISION, svg_partial_recovery_manifest=manifest,
                    svg_partial_recovery_manifest_hash=real_digest(manifest),
                    svg_partial_recovery_lineage=deepcopy(lineage), **{recovery.ARCHIVE_KEY: archive})
            historical_op.request_payload.update(generation_request=deepcopy(historical_job.request_payload),
                probe={"flag": True, "number": 1, "zero": 0.0})
            for index in range(2):
                saved_payload = {"immutable": "known-body", "nested": {"quality": [1, True, 0.0]},
                    "attempts": [{"kind": "author", "raw_response": "sealed-known-body",
                        "receipt": {"job_id": str(jid)}}]}
                if ordinal == 2:
                    prior = next(row for row in checkpoints if row.job_id == jobs[-1].id and row.slide_index == index)
                    saved_payload = deepcopy(prior.svg_checkpoint)
                    saved_payload["partial_recovery"] = dict(schema="svg-partial-recovery-page/v1",
                        operation_id=str(op_id), manifest_hash=real_digest(manifest), slide_index=index,
                        source_row_digest=real_digest(prior.model_dump()), category="known")
                checkpoint = HistoryRow(id=uuid.uuid4(), job_id=jid, slide_index=index,
                    svg_checkpoint=saved_payload, author_status="completed",
                    render_status="pending" if ordinal == 2 else "completed", persist_status="pending")
                checkpoints.append(checkpoint)
            history_admissions.append(HistoryRow(id=uuid.uuid4(), operation_id=op_id,
                owner_id=owner, tenant_id="fixture", state="released"))
            operations.append(historical_op)
            jobs.append(historical_job)
            tasks.append(task)

        database_history = {
            WorkspaceOperation: deepcopy(operations), PresentationGenerationJob: deepcopy(jobs),
            AsyncPresentationGenerationTaskModel: deepcopy(tasks), AuthoredSlideCheckpoint: deepcopy(checkpoints),
            PublicOperationAdmission: deepcopy(history_admissions)}
        identities = {WorkspaceOperation: operations, PresentationGenerationJob: jobs,
            AsyncPresentationGenerationTaskModel: tasks, AuthoredSlideCheckpoint: checkpoints,
            PublicOperationAdmission: history_admissions}
        transaction = [object()]
        canonical_calls, job_selects, payload_refreshes = Counter(), Counter(), Counter()
        mutate_payload = [None]
        forged_seal_identity = [None]

        def selected(rows, field, values):
            allowed = {str(value) for value in values}
            return [row for row in rows if str(getattr(row, field)) in allowed]

        class HistorySession:
            sync_session = SimpleNamespace(get_transaction=lambda: transaction[0])

            async def scalars(self, statement):
                model = statement.column_descriptions[0]["entity"]
                rows = identities.get(model, [])
                params = statement.compile().params
                if params:
                    field, value = next(iter(params.items()))
                    rows = selected(rows, field.rsplit("_", 1)[0], value if isinstance(value, list) else [value])
                return rows

            async def scalar(self, statement):
                model = statement.column_descriptions[0]["entity"]
                rows = identities.get(model, [])
                for key, value in statement.compile().params.items():
                    rows = selected(rows, key.rsplit("_", 1)[0], [value])
                result = rows[0] if rows else None
                if model is PresentationGenerationJob and result is not None:
                    job_selects[str(result.id)] += 1
                    if result.id == jobs[1].id and mutate_payload[0] is not None:
                        mutate_payload[0](operations[1].request_payload)
                if result is not None and statement.column_descriptions[0]["name"] == "id":
                    # ``select(Model.id)`` transfers only the identity; the
                    # session's ``get`` then supplies the row (edge verification).
                    return result.id
                return result

            async def get(self, model, identifier, **kwargs):
                return next((row for row in identities.get(model, []) if str(row.id) == str(identifier)), None)

            async def refresh(self, row, *, attribute_names):
                payload_refreshes[str(row.id)] += 1
                original = next(item for item in database_history[WorkspaceOperation] if item.id == row.id)
                for name in attribute_names:
                    setattr(row, name, deepcopy(getattr(original, name)))

        history_session = HistorySession()

        checkpoint_reads = []

        async def fresh_rows(session, model, predicate, **kwargs):
            rows = database_history.get(model, [])
            if not rows:
                return []
            field = predicate.left.key
            value = next(iter(predicate.compile().params.values()))
            matched = selected(rows, field, value if isinstance(value, list) else [value])
            if model is AuthoredSlideCheckpoint:
                checkpoint_reads.append(tuple(str(row.id) for row in matched))
            return deepcopy(matched)

        async def fresh_controls(session, ids):
            return {str(row.id): recovery._operation_control(row)
                for row in database_history[WorkspaceOperation] if str(row.id) in {str(value) for value in ids}}

        edge_seal_reads = []

        async def fresh_seals(session, ids):
            edge_seal_reads.append(tuple(str(value) for value in ids))
            seals = {str(row.id): storage_seal(row)
                for row in database_history[WorkspaceOperation] if str(row.id) in {str(value) for value in ids}}
            if forged_seal_identity[0] is not None and str(operations[1].id) in seals:
                seals[str(operations[1].id)]["id"] = forged_seal_identity[0]
            return seals

        def counted_digest(value):
            # The operation fingerprint is the digest of ``_operation_projection``:
            # the control fields plus ``request_payload`` in one mapping.
            if (isinstance(value, dict) and "request_payload" in value
                    and value.get("id") in {str(row.id) for row in operations[1:]}):
                canonical_calls[value["id"]] += 1
            return real_digest(value)

        original_operation_fingerprint = recovery._EpisodeVerification.operation_fingerprint
        original_fingerprint_snapshot = recovery._json_fingerprint_snapshot
        owned_snapshots, extra_owned_clones = {}, []

        def mutable_ids(value):
            pending, seen = [value], set()
            while pending:
                item = pending.pop()
                if type(item) not in {dict, list} or id(item) in seen:
                    continue
                seen.add(id(item))
                pending.extend(item.values() if type(item) is dict else item)
            return seen

        def capture_owned_snapshot(verifier, operation, *args, **kwargs):
            result = original_operation_fingerprint(verifier, operation, *args, **kwargs)
            if result[1] is not None:
                fingerprint_key, snapshot, *_marshalled = result[1]
                assert not mutable_ids(snapshot).intersection(mutable_ids(operation.request_payload))
                owned_snapshots[id(snapshot)] = (verifier, fingerprint_key, snapshot, deepcopy(snapshot))
            return result

        def count_owned_clone(value, fingerprint):
            if id(value) in owned_snapshots:
                extra_owned_clones.append(id(value))
            return original_fingerprint_snapshot(value, fingerprint)

        outer = HistoryRow(**{**vars(operations[-1]), "id": uuid.uuid4(), "retry_of_operation_id": operations[-1].id})
        outer_manifest = dict(append_only_recovery=True, presentation_id=str(operations[-1].presentation_id),
            source_operation_id=str(operations[0].id), owner_id=str(owner), tenant_id="fixture")
        outer_references = [reference(row) for row in reversed(operations)]
        with monkeypatch.context() as scoped:
            scoped.setattr(recovery, "_fresh_recovery_model_rows", fresh_rows)
            scoped.setattr(recovery, "_fresh_recovery_operation_controls", fresh_controls)
            scoped.setattr(recovery, "_fresh_payload_storage_seals", fresh_seals)
            scoped.setattr("models.sql.workspace.get_workspace_owner_scope", lambda: str(owner))
            scoped.setattr(recovery, "digest", counted_digest)

            real_inspect = sqlalchemy.inspect

            def inspect_fixture_rows(subject, *args, **kwargs):
                # Node proofs inspect the live ORM state of each proven row.
                # Fixture rows are plain namespaces that are never expired or
                # detached, so they report an empty, attached state.
                if isinstance(subject, HistoryRow):
                    return SimpleNamespace(expired_attributes=frozenset(), detached=False)
                return real_inspect(subject, *args, **kwargs)

            scoped.setattr(sqlalchemy, "inspect", inspect_fixture_rows)
            scoped.setattr(recovery._EpisodeVerification, "operation_fingerprint", capture_owned_snapshot)
            scoped.setattr(recovery, "_json_fingerprint_snapshot", count_owned_clone)
            proof = recovery._EpisodeVerification(history_session)

            async def walk():
                await recovery._verify_operation_references_once(history_session, outer, outer_manifest,
                    outer_references, seal_memo=proof.seal_memo, verification=proof,
                    reuse_historical_seals=True)

            assert not isinstance(recovery._verify_append_only_edge, AsyncMock)
            assert not isinstance(recovery.verify_episode, AsyncMock)
            await walk()
            assert owned_snapshots and extra_owned_clones == []
            for verifier, fingerprint_key, snapshot, unchanged in owned_snapshots.values():
                assert verifier is proof
                assert proof.operation_fingerprints[fingerprint_key][0] is snapshot
                assert snapshot == unchanged
            initial_refreshes = dict(payload_refreshes)
            assert initial_refreshes
            await walk()
            assert dict(payload_refreshes) == initial_refreshes
            assert canonical_calls == {str(row.id): 1 for row in operations[1:]}
            assert extra_owned_clones == []
            assert job_selects[str(jobs[1].id)] >= 3 and job_selects[str(jobs[2].id)] >= 2
            before_edges = len(edge_seal_reads)
            await walk()
            reused_edge_ids = sum(len(ids) for ids in edge_seal_reads[before_edges:])
            before_edges = len(edge_seal_reads)
            # Current-code callers do not opt into the historical edge shortcut.
            await recovery._verify_operation_references_once(history_session, outer, outer_manifest,
                outer_references, seal_memo=proof.seal_memo, verification=proof,
                reuse_historical_seals=False)
            fresh_edge_ids = sum(len(ids) for ids in edge_seal_reads[before_edges:])
            assert reused_edge_ids < fresh_edge_ids
            assert all(len(ids) == 2 for ids in edge_seal_reads[before_edges:])
            await proof.finish()

            # Direct-call fingerprints reuse only an already sealed exact snapshot.
            historical = operations[1]
            direct_payload = deepcopy(historical.request_payload)
            memo_keys = tuple(proof.operation_fingerprints)
            direct_expected = real_digest(recovery._operation_projection(historical))
            before_direct = canonical_calls[str(historical.id)]
            direct_value, direct_key = proof.operation_fingerprint(historical, None, require_current_code=False)
            assert direct_value == direct_expected and direct_key is None
            assert canonical_calls[str(historical.id)] == before_direct
            for field, changed in (("number", 999), ("number", True), ("flag", 1), ("zero", float("nan"))):
                historical.request_payload = deepcopy(direct_payload)
                historical.request_payload["probe"][field] = changed
                before_direct = canonical_calls[str(historical.id)]
                direct_value, direct_key = proof.operation_fingerprint(historical, None, require_current_code=False)
                assert direct_value == real_digest(recovery._operation_projection(historical))
                assert direct_key is None and canonical_calls[str(historical.id)] == before_direct + 1
            historical.request_payload = deepcopy(direct_payload)
            direct_state = historical.state
            try:
                historical.state = "cancelled"
                before_direct = canonical_calls[str(historical.id)]
                direct_value, direct_key = proof.operation_fingerprint(historical, None, require_current_code=False)
                assert direct_value == real_digest(recovery._operation_projection(historical))
                assert direct_key is None and canonical_calls[str(historical.id)] == before_direct + 1
            finally:
                historical.state = direct_state
            assert tuple(proof.operation_fingerprints) == memo_keys
            await proof.finish()
            # Missing loaded payload and unsupported exact comparisons still refresh.
            historical = operations[1]
            stored_payload = deepcopy(historical.request_payload)
            before_refreshes = payload_refreshes[str(historical.id)]
            del historical.request_payload
            await walk()
            assert payload_refreshes[str(historical.id)] == before_refreshes + 1
            historical.request_payload["probe"]["zero"] = float("nan")
            before_refreshes = payload_refreshes[str(historical.id)]
            await walk()
            assert payload_refreshes[str(historical.id)] == before_refreshes + 1
            assert historical.request_payload == stored_payload

            # Changed loaded values, including replacement dictionaries, never reuse.
            for replace_pointer in (False, True):
                historical.request_payload = deepcopy(stored_payload)
                if replace_pointer:
                    historical.request_payload = {**historical.request_payload,
                        "probe": {**historical.request_payload["probe"], "number": 999}}
                else:
                    historical.request_payload["probe"]["number"] = 999
                before_refreshes = payload_refreshes[str(historical.id)]
                with pytest.raises(recovery.PartialRecoveryRejected,
                    match="svg_partial_recovery_request_snapshot_changed"):
                    await walk()
                assert payload_refreshes[str(historical.id)] == before_refreshes
            historical.request_payload = deepcopy(stored_payload)
            await walk()

            # Edge reuse can defer storage-change detection, never past fresh finish.
            stored_operation = database_history[WorkspaceOperation][1]
            stored_operation.request_payload["probe"]["number"] = 999
            with pytest.raises(recovery.PartialRecoveryRejected,
                match="svg_partial_recovery_request_snapshot_changed"):
                await proof.finish()
            stored_operation.request_payload = deepcopy(stored_payload)
            await proof.finish()
            raw_v3 = operations[-1].request_payload[recovery.ARCHIVE_KEY]
            manifest_v3 = operations[-1].request_payload["svg_partial_recovery_manifest"]
            assert raw_v3["schema"] == recovery.ARCHIVE_V3 and "checkpoints" not in raw_v3
            unchanged_raw = deepcopy(raw_v3)
            hydration = recovery._EpisodeVerification(history_session)
            working = await recovery._hydrate_checkpoint_archive(history_session, manifest_v3, raw_v3,
                verification=hydration)
            assert working is not raw_v3 and raw_v3 == unchanged_raw and "checkpoints" not in raw_v3
            assert working["job"] is not raw_v3["job"]
            assert working["job"]["request_payload"] is not raw_v3["job"]["request_payload"]
            producer_snapshots = [row.model_dump() for row in checkpoints if row.job_id == jobs[-2].id]
            assert working["checkpoints"] == producer_snapshots
            assert working["job"]["completed_slides"] == 0 and working["job"]["current_stage"] == "outline"
            assert all(row["render_status"] == "pending" for row in working["checkpoints"])
            assert all(row["svg_checkpoint"]["attempts"][0]["receipt"]["job_id"] == str(jobs[-3].id)
                and row["job_id"] == str(jobs[-2].id) for row in working["checkpoints"])
            assert {entry[0] for entry in hydration.rows} >= {PresentationGenerationJob, AuthoredSlideCheckpoint}
            assert str(jobs[-2].id) in hydration.producers
            before_reuse = len(checkpoint_reads)
            assert await recovery._hydrate_checkpoint_archive(history_session, manifest_v3, raw_v3,
                verification=hydration) is working
            assert len(checkpoint_reads) == before_reuse
            await hydration.finish()
            assert len(checkpoint_reads) == before_reuse + 1
            # Producer-only cold proof avoids a working archive entirely.
            immediate = recovery._EpisodeVerification(history_session)
            immediate_materialization = {"copy": 0, "marshal": 0}
            immediate_deepcopy = recovery.deepcopy
            immediate_marshal = recovery._marshal_json_bytes
            immediate_snapshot = recovery._json_fingerprint_snapshot
            immediate_checkpoint_snapshots = []

            immediate_exact_snapshot = recovery._exact_json_snapshot

            def note_checkpoint_snapshot(value):
                if type(value) is dict and "svg_checkpoint" in value and "slide_index" in value:
                    immediate_checkpoint_snapshots.append(value["id"])

            def count_immediate_snapshot(value, fingerprint):
                note_checkpoint_snapshot(value)
                return immediate_snapshot(value, fingerprint)

            def count_immediate_exact_snapshot(value):
                # The exact-JSON fast path now takes the private snapshot first
                # and falls back to ``_json_fingerprint_snapshot`` only for
                # values it cannot prove; either one is "a snapshot taken".
                note_checkpoint_snapshot(value)
                return immediate_exact_snapshot(value)

            def count_immediate_copy(value):
                if value is raw_v3:
                    immediate_materialization["copy"] += 1
                return immediate_deepcopy(value)

            def count_immediate_marshal(value):
                if (type(value) is dict and value.get("schema") == recovery.ARCHIVE_V3
                        and "checkpoints" in value):
                    immediate_materialization["marshal"] += 1
                return immediate_marshal(value)

            with monkeypatch.context() as immediate_hooks:
                immediate_hooks.setattr(recovery, "deepcopy", count_immediate_copy)
                immediate_hooks.setattr(recovery, "_marshal_json_bytes", count_immediate_marshal)
                immediate_hooks.setattr(recovery, "_json_fingerprint_snapshot", count_immediate_snapshot)
                immediate_hooks.setattr(recovery, "_exact_json_snapshot", count_immediate_exact_snapshot)
                before_immediate = len(checkpoint_reads)
                immediate_producer = await recovery._verify_producer_rows(history_session, manifest_v3, raw_v3,
                    verification=immediate)
                assert immediate_producer.id == jobs[-2].id
                assert len(checkpoint_reads) == before_immediate + 1
                assert immediate_materialization == {"copy": 0, "marshal": 0}
                assert immediate.checkpoint_archives == {} and immediate.checkpoint_archive_bytes == 0
                assert immediate_checkpoint_snapshots == []
                assert not any(key[0] is AuthoredSlideCheckpoint for key in immediate.row_fingerprints)
                immediate_row_key = next(key for key in immediate.rows if key[0] is AuthoredSlideCheckpoint)
                immediate_row_digest = immediate.rows[immediate_row_key]
                immediate.rows[immediate_row_key] = "forged-existing-expectation"
                try:
                    with pytest.raises(recovery.PartialRecoveryRejected):
                        await recovery._verify_producer_rows(history_session, manifest_v3, raw_v3,
                            verification=immediate)
                finally:
                    immediate.rows[immediate_row_key] = immediate_row_digest
                with monkeypatch.context() as foreign_producer_owner:
                    foreign_producer_owner.setattr("utils.owner_context.get_current_owner_id", lambda: uuid.uuid4())
                    with pytest.raises(recovery.PartialRecoveryRejected):
                        await recovery._verify_producer_rows(history_session, manifest_v3, raw_v3,
                            verification=immediate)
                # A real page/seed consumer still receives the full independent view.
                immediate_working = await recovery._hydrate_checkpoint_archive(history_session, manifest_v3, raw_v3,
                    verification=immediate)
                assert immediate_working is not raw_v3 and raw_v3 == unchanged_raw
                assert immediate_working["checkpoints"] == working["checkpoints"]
                assert immediate_materialization == {"copy": 1, "marshal": 1}
                assert len(immediate_checkpoint_snapshots) == len(immediate_working["checkpoints"])
            # The same cold proof also exercises the production CP serializer.
            saved_source_rows = database_history[AuthoredSlideCheckpoint]
            fake_source = next(row for row in saved_source_rows if row.job_id == jobs[-2].id)
            real_source = AuthoredSlideCheckpoint.model_validate(fake_source.model_dump())
            real_source.svg_checkpoint["serializer_probe"] = {
                "integer": 1, "number": 1.0, "negative_zero": -0.0, "unicode": "한글"}
            full_source_json = real_source.model_dump(mode="json")
            full_source_digest = real_digest(full_source_json)
            assert recovery._fresh_producer_checkpoint_digest(real_source) == full_source_digest
            real_raw = deepcopy(raw_v3)
            next(ref for ref in real_raw["checkpoint_refs"]
                if ref["id"] == str(real_source.id))["row_digest"] = full_source_digest
            real_manifest = {**manifest_v3, "archive_digest": real_digest(real_raw)}
            database_history[AuthoredSlideCheckpoint] = [
                real_source if row.id == real_source.id else row for row in saved_source_rows]
            try:
                real_proof = recovery._EpisodeVerification(history_session)
                await recovery._verify_producer_rows(history_session, real_manifest, real_raw,
                    verification=real_proof)
                real_key = (AuthoredSlideCheckpoint, "id", str(real_source.id))
                assert real_proof.rows[real_key] == full_source_digest
                assert real_proof.producers[str(real_source.job_id)][str(real_source.id)] == full_source_digest
                assert real_proof.checkpoint_archives == {}
                await real_proof.finish()
                real_source.svg_checkpoint["serializer_probe"]["integer"] = True
                with pytest.raises(recovery.PartialRecoveryRejected,
                    match="svg_partial_recovery_checkpoint_reference_changed"):
                    await recovery._verify_producer_rows(history_session, real_manifest, real_raw,
                        verification=recovery._EpisodeVerification(history_session))
                with pytest.raises(recovery.PartialRecoveryRejected,
                    match="svg_partial_recovery_request_snapshot_changed"):
                    await real_proof.finish()
            finally:
                database_history[AuthoredSlideCheckpoint] = saved_source_rows
            # A hydration memo hit still performs the existing fresh producer read.
            before_hit = len(checkpoint_reads)
            await recovery._verify_producer_rows(history_session, manifest_v3, raw_v3,
                verification=immediate)
            assert len(checkpoint_reads) == before_hit + 1
            immediate_changed_producer = next(row for row in database_history[AuthoredSlideCheckpoint]
                if row.job_id == jobs[-2].id)
            immediate_original_checkpoint = deepcopy(immediate_changed_producer.svg_checkpoint)
            try:
                immediate_changed_producer.svg_checkpoint["immediate_handoff_mutation"] = True
                with pytest.raises(recovery.PartialRecoveryRejected):
                    await recovery._verify_producer_rows(history_session, manifest_v3, raw_v3,
                        verification=immediate)
                with pytest.raises(recovery.PartialRecoveryRejected):
                    await immediate.finish()
            finally:
                immediate_changed_producer.svg_checkpoint = immediate_original_checkpoint
            before_finish = len(checkpoint_reads)
            await immediate.finish()
            assert len(checkpoint_reads) == before_finish + 1
            # Without an owning verifier there is no handoff or final-proof trust.
            before_unowned = len(checkpoint_reads)
            await recovery._verify_producer_rows(history_session, manifest_v3, raw_v3)
            assert len(checkpoint_reads) == before_unowned + 2
            separate = recovery._EpisodeVerification(history_session)
            before_separate = len(checkpoint_reads)
            other_working = await recovery._hydrate_checkpoint_archive(history_session, manifest_v3, raw_v3,
                verification=separate)
            assert other_working == working and other_working is not working
            assert len(checkpoint_reads) == before_separate + 1
            assert recovery._CHECKPOINT_ARCHIVE_MEMO_MAX_BYTES == 512 * 1024 * 1024
            assert recovery._CHECKPOINT_ARCHIVE_MEMO_MAX_ENTRIES == 8
            assert 0 < hydration.checkpoint_archive_bytes <= recovery._CHECKPOINT_ARCHIVE_MEMO_MAX_BYTES
            assert all(type(guard) is bytes and len(guard) == 32
                for _, guard in hydration.checkpoint_archives.values())
            import hashlib
            expected_guard = hashlib.sha256(recovery._CHECKPOINT_ARCHIVE_MEMO_GUARD_DOMAIN)
            expected_guard.update(recovery._marshal_json_bytes(working))
            assert next(iter(hydration.checkpoint_archives.values()))[1] == expected_guard.digest()
            # Equal-looking JSON scalars still differ in the typed marshal guard.
            for changed in (False, 0.0):
                working["job"]["completed_slides"] = changed
                with pytest.raises(recovery.PartialRecoveryRejected):
                    await recovery._hydrate_checkpoint_archive(history_session, manifest_v3, raw_v3,
                        verification=hydration)
                with pytest.raises(recovery.PartialRecoveryRejected):
                    await hydration.finish()
                working["job"]["completed_slides"] = 0
            await hydration.finish()

            # An oversized entry is not registered; repeated calls remain fresh.
            with monkeypatch.context() as limits:
                limits.setattr(recovery, "_CHECKPOINT_ARCHIVE_MEMO_MAX_BYTES", 0)
                uncached = recovery._EpisodeVerification(history_session)
                before_uncached = len(checkpoint_reads)
                for _ in range(2):
                    fresh_working = await recovery._hydrate_checkpoint_archive(history_session,
                        manifest_v3, raw_v3, verification=uncached)
                    assert fresh_working == working and fresh_working is not working
                assert len(checkpoint_reads) == before_uncached + 2
                assert uncached.checkpoint_archives == {} and uncached.checkpoint_archive_bytes == 0
                await uncached.finish()

            # The byte budget is shared across all entries in this invocation.
            with monkeypatch.context() as limits:
                budget = 2 * hydration.checkpoint_archive_bytes
                limits.setattr(recovery, "_CHECKPOINT_ARCHIVE_MEMO_MAX_BYTES", budget)
                bounded = recovery._EpisodeVerification(history_session)
                retained_working = await recovery._hydrate_checkpoint_archive(history_session,
                    manifest_v3, raw_v3, verification=bounded)
                first_bytes = bounded.checkpoint_archive_bytes
                second_manifest = {**manifest_v3, "memo_probe": "second"}
                await recovery._hydrate_checkpoint_archive(history_session, second_manifest, raw_v3,
                    verification=bounded)
                assert len(bounded.checkpoint_archives) == 2
                assert first_bytes < bounded.checkpoint_archive_bytes <= budget
                retained_bytes = bounded.checkpoint_archive_bytes
                third_manifest = {**manifest_v3, "memo_probe": "third"}
                before_full = len(checkpoint_reads)
                for _ in range(2):
                    await recovery._hydrate_checkpoint_archive(history_session, third_manifest, raw_v3,
                        verification=bounded)
                assert len(checkpoint_reads) == before_full + 2
                assert len(bounded.checkpoint_archives) == 2
                assert bounded.checkpoint_archive_bytes == retained_bytes
                # Refusing another registration never evicts a watched working view.
                assert await recovery._hydrate_checkpoint_archive(history_session, manifest_v3, raw_v3,
                    verification=bounded) is retained_working
                retained_working["checkpoints"][0]["svg_checkpoint"]["nested"]["quality"][0] = 999
                with pytest.raises(recovery.PartialRecoveryRejected):
                    await recovery._hydrate_checkpoint_archive(history_session, manifest_v3, raw_v3,
                        verification=bounded)
                with pytest.raises(recovery.PartialRecoveryRejected):
                    await bounded.finish()

            # The entry ceiling also falls back without replacing earlier entries.
            with monkeypatch.context() as limits:
                limits.setattr(recovery, "_CHECKPOINT_ARCHIVE_MEMO_MAX_ENTRIES", 1)
                bounded = recovery._EpisodeVerification(history_session)
                retained_working = await recovery._hydrate_checkpoint_archive(history_session,
                    manifest_v3, raw_v3, verification=bounded)
                retained_bytes = bounded.checkpoint_archive_bytes
                before_full = len(checkpoint_reads)
                for _ in range(2):
                    await recovery._hydrate_checkpoint_archive(history_session,
                        {**manifest_v3, "memo_probe": "entry-overflow"}, raw_v3, verification=bounded)
                assert len(checkpoint_reads) == before_full + 2
                assert len(bounded.checkpoint_archives) == 1
                assert bounded.checkpoint_archive_bytes == retained_bytes
                assert await recovery._hydrate_checkpoint_archive(history_session, manifest_v3, raw_v3,
                    verification=bounded) is retained_working
                await bounded.finish()
            assert raw_v3 == unchanged_raw
            for replace_pointer in (False, True):
                saved_checkpoints = working["checkpoints"]
                if replace_pointer:
                    working["checkpoints"] = deepcopy(saved_checkpoints)
                working["checkpoints"][0]["svg_checkpoint"]["nested"]["quality"][0] = 999
                with pytest.raises(recovery.PartialRecoveryRejected):
                    await recovery._hydrate_checkpoint_archive(history_session, manifest_v3, raw_v3,
                        verification=hydration)
                with pytest.raises(recovery.PartialRecoveryRejected):
                    await hydration.finish()
                working["checkpoints"] = saved_checkpoints
                working["checkpoints"][0]["svg_checkpoint"]["nested"]["quality"][0] = producer_snapshots[0]["svg_checkpoint"]["nested"]["quality"][0]
            await hydration.finish()
            unchanged_database = deepcopy(database_history)
            for mode in ("missing", "extra", "tampered"):
                database_history.clear()
                database_history.update(deepcopy(unchanged_database))
                isolated = recovery._EpisodeVerification(history_session)
                await recovery._hydrate_checkpoint_archive(history_session, manifest_v3, raw_v3,
                    verification=isolated)
                producer = next(row for row in database_history[AuthoredSlideCheckpoint]
                    if row.job_id == jobs[-2].id)
                if mode == "missing":
                    database_history[AuthoredSlideCheckpoint].remove(producer)
                elif mode == "extra":
                    extra = deepcopy(producer)
                    extra.id = uuid.uuid4()
                    database_history[AuthoredSlideCheckpoint].append(extra)
                else:
                    producer.svg_checkpoint["nested"]["quality"][0] = 999
                before_reuse = len(checkpoint_reads)
                await recovery._hydrate_checkpoint_archive(history_session, manifest_v3, raw_v3,
                    verification=isolated)
                assert len(checkpoint_reads) == before_reuse
                with pytest.raises(recovery.PartialRecoveryRejected):
                    await isolated.finish()
            database_history.clear()
            database_history.update(deepcopy(unchanged_database))
            working["checkpoints"][0]["svg_checkpoint"]["nested"]["quality"][0] = 999
            with pytest.raises(recovery.PartialRecoveryRejected):
                hydration.expect(AuthoredSlideCheckpoint, working["checkpoints"][0])
            assert raw_v3 == unchanged_raw
            stored_before = deepcopy(database_history)
            producer_row = next(row for row in database_history[AuthoredSlideCheckpoint]
                if row.job_id == jobs[-2].id)
            producer_row.svg_checkpoint["nested"]["quality"][0] = 99
            with pytest.raises(recovery.PartialRecoveryRejected):
                await hydration.finish()
            database_history.clear()
            database_history.update(deepcopy(stored_before))
            for mode in ("duplicate_id", "duplicate_page", "missing", "foreign_job", "foreign_owner",
                         "foreign_tenant", "swapped_page", "swapped_id", "bad_digest", "extra_field",
                         "bool_page", "inline_rows", "raw_archive_digest", "missing_row", "extra_row", "row_tamper",
                         "parent_owner", "parent_scope", "parent_tenant", "parent_job", "parent_presentation"):
                database_history.clear()
                database_history.update(deepcopy(stored_before))
                candidate_archive, candidate_manifest = deepcopy(raw_v3), deepcopy(manifest_v3)
                refs = candidate_archive["checkpoint_refs"]
                if mode == "duplicate_id":
                    refs[1]["id"] = refs[0]["id"]
                elif mode == "duplicate_page":
                    refs[1]["slide_index"] = refs[0]["slide_index"]
                elif mode == "missing":
                    refs.pop()
                elif mode == "foreign_job":
                    refs[0]["job_id"] = str(uuid.uuid4())
                elif mode == "foreign_owner":
                    refs[0]["owner_id"] = str(uuid.uuid4())
                elif mode == "foreign_tenant":
                    refs[0]["tenant_id"] = "foreign"
                elif mode == "swapped_page":
                    refs[0]["slide_index"], refs[1]["slide_index"] = refs[1]["slide_index"], refs[0]["slide_index"]
                elif mode == "swapped_id":
                    refs[0]["id"], refs[1]["id"] = refs[1]["id"], refs[0]["id"]
                elif mode == "bad_digest":
                    refs[0]["row_digest"] = "0" * 64
                elif mode == "extra_field":
                    refs[0]["undeclared"] = "value"
                elif mode == "bool_page":
                    refs[0]["slide_index"] = False
                elif mode == "inline_rows":
                    candidate_archive["checkpoints"] = producer_snapshots
                elif mode == "raw_archive_digest":
                    candidate_archive["undeclared"] = "tamper"
                else:
                    parent = next(row for row in database_history[WorkspaceOperation] if row.id == operations[-2].id)
                    producer = next(row for row in database_history[PresentationGenerationJob] if row.id == jobs[-2].id)
                    admission = next(row for row in database_history[PublicOperationAdmission]
                        if row.operation_id == parent.id)
                    row = next(row for row in database_history[AuthoredSlideCheckpoint] if row.job_id == producer.id)
                    if mode == "missing_row":
                        database_history[AuthoredSlideCheckpoint].remove(row)
                    elif mode == "extra_row":
                        extra = deepcopy(row)
                        extra.id = uuid.uuid4()
                        database_history[AuthoredSlideCheckpoint].append(extra)
                    elif mode == "row_tamper":
                        row.svg_checkpoint["nested"]["quality"][0] = 9
                    elif mode == "parent_owner":
                        parent.owner_id = uuid.uuid4()
                    elif mode == "parent_scope":
                        parent.owner_scope = str(uuid.uuid4())
                    elif mode == "parent_tenant":
                        admission.tenant_id = "foreign"
                    elif mode == "parent_job":
                        producer.workspace_operation_id = uuid.uuid4()
                    else:
                        producer.presentation_id = uuid.uuid4()
                if mode != "raw_archive_digest":
                    candidate_manifest["archive_digest"] = real_digest(candidate_archive)
                with pytest.raises(recovery.PartialRecoveryRejected):
                    await recovery._hydrate_checkpoint_archive(history_session, candidate_manifest,
                        candidate_archive, verification=recovery._EpisodeVerification(history_session))
            database_history.clear()
            database_history.update(deepcopy(stored_before))
            for historical in operations[1:3]:
                legacy = historical.request_payload[recovery.ARCHIVE_KEY]
                assert await recovery._hydrate_checkpoint_archive(history_session,
                    historical.request_payload["svg_partial_recovery_manifest"], legacy) is legacy
            assert type(storage_seal(operations[1])["id"]) is DriverUUID
            assert isinstance(storage_seal(operations[1])["id"], uuid.UUID)
            for representation in ("standard", "string"):
                seal_identity_kind[0] = representation
                detached = recovery._EpisodeVerification(history_session)
                for _ in range(2):
                    await recovery._verify_operation_references_once(history_session, outer, outer_manifest,
                        outer_references, seal_memo=detached.seal_memo, verification=detached)
                await detached.finish()
            seal_identity_kind[0] = "driver"
            for invalid_identity in (uuid.uuid4(), DriverUUID(str(uuid.uuid4())), str(uuid.uuid4()), 1):
                forged_seal_identity[0] = invalid_identity
                detached = recovery._EpisodeVerification(history_session)
                with pytest.raises(recovery.PartialRecoveryRejected, match="svg_partial_recovery_history_changed"):
                    await recovery._verify_operation_references_once(history_session, outer, outer_manifest,
                        outer_references, seal_memo=detached.seal_memo, verification=detached)
            forged_seal_identity[0] = None
            jobs[1].request_payload["nested"]["values"][0] = 999
            with pytest.raises(recovery.PartialRecoveryRejected):
                await walk()
            jobs[1].request_payload = deepcopy(database_history[PresentationGenerationJob][1].request_payload)
            for value in (1.0, True, -0.0):
                jobs[1].request_payload["nested"]["values"][0] = value
                with pytest.raises(recovery.PartialRecoveryRejected):
                    await walk()
                jobs[1].request_payload = deepcopy(database_history[PresentationGenerationJob][1].request_payload)
            for key, value in (("flag", 1), ("number", 1.0), ("zero", -0.0)):
                mutate_payload[0] = lambda payload, key=key, value=value: payload["probe"].__setitem__(key, value)
                with pytest.raises(recovery.PartialRecoveryRejected, match="svg_partial_recovery_request_snapshot_changed"):
                    await walk()
            mutate_payload[0] = lambda payload: payload["probe"].__setitem__("zero", float("nan"))
            with pytest.raises(recovery.PartialRecoveryRejected):
                await walk()
            for invalid_kind in ("infinite", "non_json", "cycle"):
                def inject_invalid(payload, invalid_kind=invalid_kind):
                    if invalid_kind == "infinite":
                        payload["probe"]["zero"] = float("inf")
                    elif invalid_kind == "non_json":
                        payload["probe"]["zero"] = object()
                    else:
                        payload["probe"]["cycle"] = payload["probe"]
                mutate_payload[0] = inject_invalid
                with pytest.raises((recovery.PartialRecoveryRejected, TypeError, ValueError)):
                    await walk()
            mutate_payload[0] = None
            operations[1].request_payload = deepcopy(database_history[WorkspaceOperation][1].request_payload)
            operations[1].request_payload["probe"]["number"] = 5
            with pytest.raises(recovery.PartialRecoveryRejected):
                await recovery.verify_episode(history_session, operations[1], jobs[1],
                    require_current_code=False, verification=proof)
            operations[1].request_payload = deepcopy(database_history[WorkspaceOperation][1].request_payload)
            database_history[WorkspaceOperation][1].request_payload["changed_storage"] = True
            # A completed historical edge may defer this DB-only change.
            await walk()
            with pytest.raises(recovery.PartialRecoveryRejected,
                match="svg_partial_recovery_request_snapshot_changed"):
                await proof.finish()
            for _, _, snapshot, unchanged in owned_snapshots.values():
                assert snapshot == unchanged
            assert extra_owned_clones == []

        assert recovery._same_json_value({"x": True}, {"x": 1}) is False
        assert recovery._same_json_value({"x": 1}, {"x": 1.0}) is False
        assert recovery._same_json_value({"x": 0.0}, {"x": -0.0}) is False
        assert recovery._same_json_value(float("nan"), float("nan")) is None
        assert recovery._same_json_value(float("inf"), float("inf")) is None
        assert recovery._same_json_value(object(), object()) is None
        cyclic = {}
        cyclic["self"] = cyclic
        assert recovery._same_json_value(cyclic, cyclic) is None

    asyncio.run(exercise_real_historical_recursion())

    async def exercise_prepared_runtime():
        from sqlalchemy.dialects import postgresql
        from services import public_operation_admission as public
        from models.sql.authored_slide_checkpoint import AuthoredSlideCheckpoint

        live_operation = Row(**{**vars(operation), "state": "running", "kind": "presentation.generate",
            "owner_scope": "fixture-owner", "presentation_id": uuid.uuid4(),
            "retry_of_operation_id": ancestor.id, "execution_profile_version": "public-generation-export/v2",
            "contract_version": "public-operation/v2", "deadline_at": now + timedelta(hours=1),
            "phase": "active", "attempt_token": "prepared-attempt", "fencing_token": 9,
            "cancel_requested_at": None})
        source_presentation_id = uuid.uuid4()
        source_pointer = {"ref": str(uuid.uuid4()), "sha256": "a" * 64}
        manifest = {"new_indices": [1], "unknown_indices": [], "known_indices": [0],
            "proven_remaining_budget_microunits": 100,
            "prospective_initial_request_limit": 10, "prospective_retry_request_limit": 5,
            "owner_id": str(owner), "tenant_id": "fixture",
            "presentation_id": str(source_presentation_id), "presentation_revision": 7,
            "source_asset_binding": {"presentation_id": str(source_presentation_id),
                "source_revision": 7, "digest": "frozen-source", "pointer": source_pointer}}
        manifest_hash = recovery.digest(manifest)
        live_operation.request_payload = {
            "principal_tenant_id": "fixture", "recovery_revision": recovery.REVISION,
            "svg_partial_recovery_manifest": manifest,
            "svg_partial_recovery_manifest_hash": manifest_hash,
            "svg_partial_recovery_lineage": {"schema": "fixture-lineage"},
            "recovery_decision": {"manifest_hash": manifest_hash,
                "nonce": recovery.decision_nonce(manifest_hash), "acknowledged_unknown_indices": []},
            "job_id": str(job_id), recovery.ARCHIVE_KEY: {"historical": "verified once"}}
        live_job = Row(**{**vars(job), "state": "running", "attempt_token": "prepared-attempt",
            "workspace_operation_id": operation_id, "total_slides": 2,
            "presentation_id": live_operation.presentation_id, "authored_outline": {"slides": [0, 1]},
            "request_payload": {"source": "frozen", recovery.LINEAGE_KEY: {"manifest_hash": manifest_hash}}})
        live_admission = deepcopy(admission)
        live_admission.state = "active"
        live_admission.owner_id, live_admission.tenant_id = owner, "fixture"
        live_admission.operation_id, live_admission.fence = operation_id, 9
        live_admission.deadline_at = live_operation.deadline_at
        live_admission.policy_digest = "prepared-policy"
        live_admission.actual_cost_limit_microunits = live_admission.estimated_cost_limit_microunits = 100
        live_admission.planned_request_limit, live_admission.retry_request_limit = 10, 5
        queries, checkpoint_present = [], [True]

        class RuntimeSession:
            async def get(self, model, identifier):
                assert model is PresentationGenerationJob and identifier == job_id
                return live_job

            async def execute(self, statement):
                # No full operation JSON, archive, or historical checkpoint is selected.
                assert all(column is not WorkspaceOperation.__table__.c.request_payload
                           for column in statement.selected_columns)
                assert not any(recovery.ARCHIVE_KEY in column.name for column in statement.selected_columns)
                queries.append("current_operation_projection")
                values = {key: getattr(live_operation, key) for key in (
                    *recovery._RUNTIME_OPERATION_FIELDS, "state", "cancel_requested_at")}
                values.update({"payload_" + key: live_operation.request_payload.get(key)
                               for key in recovery._RUNTIME_PAYLOAD_FIELDS})
                return SimpleNamespace(mappings=lambda: SimpleNamespace(one_or_none=lambda: values))

            async def scalar(self, statement):
                entity = statement.column_descriptions[0]["entity"]
                if entity is PublicOperationAdmission:
                    return live_admission
                if entity is AuthoredSlideCheckpoint:
                    queries.append("target_page_id")
                    assert statement.column_descriptions[0]["name"] == "id"
                    return uuid.uuid4() if checkpoint_present[0] else None
                assert entity is WorkspaceOperation
                compiled = str(statement.compile(dialect=postgresql.dialect()))
                assert "workspace_operations.request_payload" not in compiled
                queries.append("live_phase2_authority")
                return live_operation

        session = RuntimeSession()
        verifier = SimpleNamespace(active=set(), episodes={}, bind_live=lambda *args: None,
            check=lambda *args: None, operation_fingerprint=lambda *args, **kwargs: ("operation", None),
            job_fingerprint=lambda *args: "job", expect=lambda *args: None, finish=AsyncMock())
        historical = AsyncMock(return_value=manifest)
        with monkeypatch.context() as scoped:
            scoped.setattr(recovery, "_PREPARED_EPISODES", recovery.OrderedDict())
            scoped.setattr(recovery, "_EpisodeVerification", lambda _: verifier)
            scoped.setattr(recovery, "_verify_episode_once", historical)
            scoped.setattr(recovery, "_code_digest", lambda: "prepared-code")
            scoped.setattr("utils.owner_context.get_current_owner_id", lambda: owner)
            scoped.setattr("utils.owner_context.get_current_tenant_id", lambda: "fixture")
            scoped.setattr("models.sql.workspace.get_workspace_owner_scope", lambda: "fixture-owner")
            scoped.setattr(public, "_resolved_phase2_authority", AsyncMock())
            scoped.setattr(public, "get_current_utc_datetime", lambda: now)

            async def admit(index=1, **kwargs):
                return await recovery.runtime_admission(session, job_id=job_id, operation_id=operation_id,
                    expected_attempt_token="prepared-attempt", expected_fencing_token=9,
                    slide_index=index, stage="author", **kwargs)

            with pytest.raises(recovery.PartialRecoveryRejected, match="runtime_not_prepared"):
                await admit()
            await recovery.verify_prepared_episode(session, live_operation, live_job)
            await recovery.verify_prepared_episode(session, live_operation, live_job)
            assert historical.await_count == verifier.finish.await_count == 1
            historical.side_effect = AssertionError("runtime must not revisit historical pages")

            from models.sql.presentation import PresentationModel
            from services.svg_generation_assets import _verify_inherited_source_manifest

            source_presentation = SimpleNamespace(id=source_presentation_id, owner_id=owner, revision=7)
            source_manifest = SimpleNamespace(presentation_id=source_presentation_id,
                                             source_revision=7, digest="frozen-source")
            asset_context = SimpleNamespace(owner_id=owner, tenant_id="fixture")

            class AssetSession:
                async def get(self, model, identifier):
                    if model is WorkspaceOperation:
                        assert identifier == live_operation.id
                        return live_operation
                    assert model is PresentationModel and identifier == source_presentation_id
                    return source_presentation

                async def scalar(self, statement):
                    assert statement.column_descriptions[0]["entity"] is PresentationGenerationJob
                    return live_job

            asset_session = AssetSession()

            async def verify_source(pointer=source_pointer, context=asset_context):
                await _verify_inherited_source_manifest(asset_session, source_manifest, pointer,
                    context=context, presentation_id=live_operation.presentation_id,
                    operation_id=live_operation.id)

            for state in ("running", "staging"):
                live_job.state = state
                await verify_source()
            live_job.state = "running"
            assert historical.await_count == 1
            with pytest.raises(ValueError, match="svg_asset_source_revision_conflict"):
                await verify_source({**source_pointer, "sha256": "b" * 64})
            with pytest.raises(ValueError, match="svg_asset_source_revision_conflict"):
                await verify_source(context=SimpleNamespace(owner_id=owner, tenant_id="foreign"))
            historical_verify = AsyncMock(return_value=manifest)
            with monkeypatch.context() as completed_scope:
                completed_scope.setattr(recovery, "verify_episode", historical_verify)
                live_job.state = "published"
                for state in ("completed", "succeeded"):
                    live_operation.state = state
                    await verify_source()
                    historical_verify.assert_awaited_with(asset_session, live_operation, live_job,
                                                         require_current_code=False)
            live_operation.state = live_job.state = "running"
            assert historical_verify.await_count == 2

            class UnreadableHistory:
                def __deepcopy__(self, memo):
                    raise AssertionError("runtime copied historical archive")

            live_operation.request_payload[recovery.ARCHIVE_KEY] = UnreadableHistory()
            for _ in range(3):
                assert await admit() is live_admission
            assert queries.count("target_page_id") == queries.count("live_phase2_authority") == 3
            assert historical.await_count == 1
            for invalid_index in (0, True, -1, 2):
                with pytest.raises(recovery.PartialRecoveryRejected, match="cached_provider_forbidden"):
                    await admit(invalid_index)
            checkpoint_present[0] = False
            with pytest.raises(recovery.PartialRecoveryRejected, match="page_unbound"):
                await admit()
            checkpoint_present[0] = True
            for target, field, changed in (
                (live_job.request_payload, "source", "changed"),
                (live_job.svg_style_snapshot, "digest", "changed"),
                (live_job.authored_outline, "slides", [1, 0]),
                (live_operation.request_payload, "principal_tenant_id", "foreign"),
            ):
                saved = target[field]
                target[field] = changed
                with pytest.raises(recovery.PartialRecoveryRejected, match="request_snapshot_changed"):
                    await admit()
                target[field] = saved
            live_operation.cancel_requested_at = now
            with pytest.raises(recovery.PartialRecoveryRejected, match="runtime_unbound"):
                await admit()
            live_operation.cancel_requested_at = None
            live_admission.actual_cost_limit_microunits = 101
            with pytest.raises(recovery.PartialRecoveryRejected, match="runtime_budget_unbound"):
                await admit()
            live_admission.actual_cost_limit_microunits = 100
            live_operation.fencing_token = 10
            with pytest.raises(recovery.PartialRecoveryRejected, match="runtime_not_prepared"):
                await admit()
            live_operation.fencing_token = 9
            scoped.setattr(public, "get_current_utc_datetime", lambda: live_operation.deadline_at + timedelta(seconds=1))
            with pytest.raises(public.PublicAdmissionRejected, match="public_deadline_insufficient"):
                await admit()
            scoped.setattr(public, "get_current_utc_datetime", lambda: now)
            scoped.setattr(recovery, "_code_digest", lambda: "changed-code")
            with pytest.raises(recovery.PartialRecoveryRejected, match="request_snapshot_changed"):
                await admit()
            scoped.setattr(recovery, "_code_digest", lambda: "prepared-code")
            scoped.setattr("utils.owner_context.get_current_owner_id", lambda: uuid.uuid4())
            with pytest.raises(recovery.PartialRecoveryRejected, match="runtime_not_prepared"):
                await admit()
            scoped.setattr("utils.owner_context.get_current_owner_id", lambda: owner)
            # A worker restart or new attempt cannot reuse the old preparation.
            recovery._PREPARED_EPISODES.clear()
            with pytest.raises(recovery.PartialRecoveryRejected, match="runtime_not_prepared"):
                await admit()
            historical.side_effect = None
            verifier.finish.side_effect = RuntimeError("preparation did not finish")
            with pytest.raises(RuntimeError, match="preparation did not finish"):
                await recovery.verify_prepared_episode(session, live_operation, live_job)
            assert not recovery._PREPARED_EPISODES

    asyncio.run(exercise_prepared_runtime())
    caplog.clear()

    def raise_snapshot_guard():
        recovery.reject("svg_partial_recovery_request_snapshot_changed")

    with pytest.raises(recovery.PartialRecoveryRejected, match="svg_partial_recovery_request_snapshot_changed"):
        raise_snapshot_guard()
    records = [record for record in caplog.records if record.name == recovery.__name__]
    assert len(records) == 1
    assert records[0].args == ("raise_snapshot_guard", raise_snapshot_guard.__code__.co_firstlineno + 1)
    caplog.clear()
    with pytest.raises(recovery.PartialRecoveryRejected, match="svg_partial_recovery_unproven"):
        recovery.reject()
    assert not [record for record in caplog.records if record.name == recovery.__name__]


@pytest.mark.parametrize("mode", ["descendant", "deadline_exceeded", "current_author", "ancestor_unknown", "superseded_invalid_unknown",
    "cancelled_unknown_completion", "mixed_unknown_invalid", "mixed_unmapped_call", "mixed_foreign_call",
    "mixed_excess_usage", "mixed_unmapped_usage", "mixed_own_unknown",
    "mixed_zero_usage", "mixed_noninteger_usage", "inherited_local_no_review", "inherited_local_partial_review"])
def test_partial_recovery_descendant_manifest_uses_current_episode_author_calls(mode, monkeypatch):
    import asyncio
    import json
    from unittest.mock import AsyncMock
    from copy import deepcopy
    from services import svg_partial_recovery as recovery, artifact_store, svg_asset_persistence
    from services import public_operation_admission as public_admission
    from models.sql.presentation import PresentationModel
    from models.sql.presentation_generation_job import PresentationGenerationJob
    from models.sql.public_admission import PublicOperationAdmission, PublicProviderLogicalCall
    from models.sql.authored_slide_checkpoint import AuthoredSlideCheckpoint
    from models.sql.source_content import SourceContentManifestRecord
    from models.sql.public_artifact import PublicArtifact
    from models.sql.provider_usage_receipt import ProviderUsageReceipt
    from services.source_content import manifest_store
    from services import svg_source_layout
    from utils.hashing import sha256_hex

    class Row(SimpleNamespace):
        def model_dump(self, **kwargs):
            return json.loads(json.dumps({key: value for key, value in vars(self).items()
                if key not in kwargs.get("exclude", ())}, default=str))

    owner, job_id, parent_id, child_id, parent_pid, child_pid = (uuid.uuid4() for _ in range(6))
    terminal_state = "deadline_exceeded" if mode == "deadline_exceeded" else "failed"
    mixed = mode.startswith("mixed_")
    unknown_mode = mixed or mode in {"superseded_invalid_unknown", "cancelled_unknown_completion"}
    usage_rows = []
    if mode == "ancestor_unknown":
        parent_pid = child_pid
    request = GeneratePresentationRequest(content="Fixture", strategy="svg", n_slides=3 if mixed else 2,
        language="en", common_header_mode="free")
    if unknown_mode:
        request = GeneratePresentationRequest(content="Fixture", strategy="svg", n_slides=3 if mixed else 2,
            source_mode="source_content_restyle", language="en", common_header_mode="free",
            template="authored", authored_style="fixture", content_policy={},
            source_content_manifest_pin={"manifest_id": "fixture-manifest", "digest": "sha256:" + "f" * 64})
    payload = request.model_dump(mode="json")
    if unknown_mode:
        payload["_internal_svg_source_binding"] = request.source_content_manifest_pin.model_dump(mode="json")
    if mode != "ancestor_unknown":
        payload[recovery.LINEAGE_KEY] = {"schema": "svg-partial-recovery-lineage/v2"}
    snapshot = {"digest": "fixture-style", "authoring_profile": {}}
    job = Row(id=job_id, task_id="fixture-task", presentation_id=child_pid,
        workspace_operation_id=child_id, state="failed", total_slides=request.n_slides, lease_expires_at=None,
        request_payload=payload, svg_style_snapshot=snapshot,
        authored_outline={"slides": [{"content": text} for text in (["A", "B", "C"] if mixed else ["A", "B"])]})
    parent = Row(id=parent_id, owner_id=owner, owner_scope="fixture", kind="presentation.generate",
        presentation_id=parent_pid, conversation_id=parent_pid, state=terminal_state, lease_expires_at=None,
        retry_of_operation_id=None, request_payload={})
    operation = Row(id=child_id, owner_id=owner, owner_scope="fixture", kind="presentation.generate",
        presentation_id=child_pid, conversation_id=child_pid,
        state=terminal_state if mode == "deadline_exceeded" else "dead_letter", lease_expires_at=None,
        retry_of_operation_id=parent_id, result_revision=None, version=4, request_payload={"generation_request": payload})
    presentation = PresentationModel(id=child_pid, owner_id=owner, revision=0)
    task = Row(id="fixture-task")
    admissions = [Row(id=uuid.uuid4(), operation_id=item.id, owner_id=owner, tenant_id="fixture",
        state=terminal_state, terminal_at="terminal", actual_cost_unavailable_count=0,
        estimated_consumed_microunits=0, estimated_reserved_microunits=0,
        actual_consumed_microunits=0, actual_reserved_microunits=0,
        estimated_cost_limit_microunits=100, actual_cost_limit_microunits=100)
        for item in (operation, parent)]
    parent_calls = [Row(id=uuid.uuid4(), admission_id=admissions[1].id, slide_index=index,
        stage="author", state="reported", outcome="succeeded", reconciled_at="reported",
        provider_id="fixture", model_id="fixture-model", estimated_cost_microunits=1,
        actual_cost_microunits=1) for index in (0, 1, 1)]
    if mode == "ancestor_unknown":
        parent_calls[-1].state, parent_calls[-1].outcome, parent_calls[-1].actual_cost_microunits = "failed", "unknown", None
    current_calls = []
    if mode == "current_author":
        current_calls = [Row(**{**vars(parent_calls[-1]), "id": uuid.uuid4(), "admission_id": admissions[0].id})]
    raw = "<svg/>"
    pointer = {"ref": str(uuid.uuid4()), "sha256": "a" * 64}
    receipt = dict(contract_version="svg-static/v2", provider="fixture", requested_model="fixture-model",
        style_digest=snapshot["digest"], authoring_profile={}, validation_error=None,
        raw_digest="sha256:" + sha256_hex(raw.encode()))
    known = dict(svg_content=raw, input_digest="frozen-page-0", source_manifest_ref=pointer,
        source_manifest_digest="fixture-asset", attempts=[dict(kind="provider", raw_response=raw, receipt=receipt)])
    rows = [Row(id=uuid.uuid4(), job_id=job_id, slide_index=0, author_status="completed", render_status="pending",
        persist_status="pending", svg_checkpoint=known), Row(id=uuid.uuid4(), job_id=job_id, slide_index=1,
        author_status="pending", render_status="pending", persist_status="pending",
        svg_checkpoint={"partial_recovery": {"category": "known_failed_output"}})]
    if unknown_mode:
        current_calls = [Row(**{**vars(parent_calls[-1]), "id": uuid.uuid4(),
            "admission_id": admissions[0].id}) for _ in range(2)]
        current_calls[-1].state = "unavailable"
        current_calls[-1].outcome = "failed"
        current_calls[-1].actual_cost_microunits = None
        admissions[0].actual_cost_unavailable_count = 1
        invalid = {**receipt, "attempt_ordinal": 1,
            "validation_error": "svg_source_receipt_text_mismatch"}
        terminal_identity = dict(schema="svg-provider-terminal/v1", job_id=str(job_id),
            operation_id=str(child_id), owner_id=str(owner), tenant_id="fixture",
            input_digest="frozen-page-1", attempt_token="fixture-attempt", fencing_token=1)
        terminals = {
            "1": {**terminal_identity, "ordinal": 1, "terminal_event": "response.completed",
                "response_id": "resp_fixture1", "raw_response": raw,
                "output_digest": sha256_hex(raw.encode()), "usage": {"input_tokens": 1}},
            "2": {**terminal_identity, "ordinal": 2, "terminal_event": "response.created",
                "response_id": "resp_fixture2"}}
        for terminal in terminals.values():
            terminal["receipt_digest"] = recovery.digest(terminal)
        rows[1].author_status = "running"
        rows[1].svg_checkpoint.update(svg_content=None, input_digest="frozen-page-1", inflight=2,
            attempts=[dict(kind="provider", raw_response=raw, receipt=invalid)],
            provider_terminal_responses=terminals,
            provider_diagnostics=[dict(ordinal=2, code="public_provider_result_unknown")])
        if mixed or mode == "cancelled_unknown_completion":
            rows[1].svg_checkpoint["inflight"] = None
            rows[1].svg_checkpoint["provider_terminal_responses"].pop("2")
    if mixed:
        from tests.unit.test_svg_source_layout import manifest_for
        from services.svg_source_layout import source_pages
        from services.generation_core.authoring.author_source_svg_layout import _whole_page_facts
        from utils.hashing import canonical_json_digest
        import xml.etree.ElementTree as ET

        source_data = manifest_for([("text", {"text": "A"})]).model_dump(mode="json", by_alias=True)
        for index in (1, 2):
            unit = deepcopy(source_data["units"][0])
            unit["sourceUnitId"], unit["sourceIndex"] = f"p{index}", index
            for block in unit["blocks"]:
                block["sourceBlockId"] = block["sourceBlockId"].replace("p0:", f"p{index}:")
            source_data["units"].append(unit)
        source_data["counts"].update(sourceUnits=3, textBlocks=3)
        source_data.pop("manifestDigest")
        source_data["manifestDigest"] = canonical_json_digest(source_data)
        source_data["ownerId"] = str(owner)
        source = type(manifest_for([])).model_validate(source_data)
        snapshot["source_authoring_contract"] = {"version": "svg-source-whole-page/v1"}
        known["source_font_profile"] = "fixture-font"
        monkeypatch.setattr("utils.svg_template_fonts.source_font_profile", lambda _: "fixture-font")
        page = source_pages(source)[2]
        fact = _whole_page_facts(page, {})[0]
        failed_raw = ('<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" '
            'viewBox="0 0 1280 720"><text id="' + fact["element_id"] + '" x="40" y="100" '
            'font-family="Noto Sans KR" font-size="40" font-weight="400" fill="#111111" '
            'text-anchor="start">B</text></svg>')
        failed_svg = ET.tostring(ET.fromstring(failed_raw), encoding="unicode")
        failed_input = recovery.digest({"page": recovery._expected_page_input_digest(request, job, 2, {}),
            "source_manifest": source.manifest_digest, "font_profile": "fixture-font",
            "source_page": page.model_dump(mode="json"), "source_layout_version": "svg-source-layout/v2"})
        failed_receipt = {**receipt, "source_authoring_contract": snapshot["source_authoring_contract"],
            "attempt_ordinal": 1, "validation_error": "svg_source_receipt_text_mismatch",
            "raw_digest": "sha256:" + sha256_hex(failed_raw.encode()),
            "source_digest": "sha256:" + sha256_hex(failed_svg.encode())}
        failed_terminal = {**terminal_identity, "input_digest": failed_input, "ordinal": 1,
            "terminal_event": "response.completed", "raw_response": failed_raw,
            "output_digest": sha256_hex(failed_raw.encode()), "usage": {"input_tokens": 1}}
        failed_terminal["receipt_digest"] = recovery.digest(failed_terminal)
        rows.append(Row(id=uuid.uuid4(), job_id=job_id, slide_index=2, author_status="running", render_status="pending",
            persist_status="pending", svg_checkpoint=dict(svg_content=failed_svg, input_digest=failed_input,
                inflight=None, source_manifest_ref=pointer, source_manifest_digest="fixture-asset",
                attempts=[dict(kind="provider", raw_response=failed_raw, receipt=failed_receipt)],
                provider_terminal_responses={"1": failed_terminal})))
        current_calls.append(Row(**{**vars(current_calls[0]), "id": uuid.uuid4(), "slide_index": 2}))
        usage_rows = [Row(id=uuid.uuid4(), job_id=job_id, stage="author", provider="fixture",
            model="fixture-model", usage_state="reported", outcome="succeeded", request_count=2),
            Row(id=uuid.uuid4(), job_id=job_id, stage="author", provider="fixture", model="fixture-model",
                usage_state="unavailable", outcome="cancelled", request_count=1)]
        if mode == "mixed_unmapped_call":
            current_calls[-1].slide_index = 99
        elif mode == "mixed_foreign_call":
            current_calls[-1].admission_id = admissions[1].id
        elif mode == "mixed_excess_usage":
            usage_rows.append(Row(**{**vars(usage_rows[-1]), "id": uuid.uuid4()}))
        elif mode == "mixed_unmapped_usage":
            usage_rows[-1].provider = "unmapped-provider"
        elif mode == "mixed_zero_usage":
            usage_rows[-1].request_count = 0
        elif mode == "mixed_noninteger_usage":
            usage_rows[-1].request_count = True
        elif mode == "mixed_own_unknown":
            current_calls.append(Row(**{**vars(current_calls[-1]), "id": uuid.uuid4(),
                "state": "uncertain", "outcome": "unknown", "actual_cost_microunits": None}))
            usage_rows.append(Row(**{**vars(usage_rows[-1]), "id": uuid.uuid4()}))
    if mode == "mixed_unmapped_call":
        # A first (non-lineage) operation whose page was stopped by a dispatch
        # freeze inside admission: the sealed before-stream stop proves the page
        # unstarted only when it accounts for every logical call of the page.
        from functools import partial

        stopped_diagnostics = {"streamStage": "before_stream", "failureKind": "dispatch_stopped"}
        stop = {**terminal_identity, "ordinal": 1, "terminal_event": "dispatch.stopped",
                "diagnostics": stopped_diagnostics}
        stop["receipt_digest"] = recovery.digest(stop)
        stopped = dict(input_digest="frozen-page-1", inflight=None, attempts=[], svg_content=None,
            provider_terminal_responses={"1": stop}, provider_diagnostics=[dict(ordinal=1,
                code="public_provider_dispatch_stopped", diagnostics=stopped_diagnostics)])
        first_job = Row(**{**vars(job), "request_payload": {
            key: value for key, value in payload.items() if key != recovery.LINEAGE_KEY}})
        stop_call = Row(**{**vars(current_calls[0]), "id": uuid.uuid4(), "slide_index": 1,
            "state": "unavailable", "outcome": "failed", "actual_cost_microunits": None})
        extra_call = Row(**{**vars(stop_call), "id": uuid.uuid4(), "state": "uncertain", "outcome": "unknown"})
        assert recovery._unresolved_provider_call(stop_call)
        undispatched = partial(recovery._undispatched_episode_page, stopped, first_job, operation, owner,
                               "fixture", attempt_token="fixture-attempt")
        assert undispatched(page_calls=[stop_call]) is True
        assert undispatched(page_calls=[stop_call, extra_call]) is False
        assert undispatched(page_calls=[extra_call]) is False
        assert recovery._undispatched_episode_page(stopped, first_job, operation, owner, "fixture") is False
        assert undispatched(page_calls=[stop_call], attempt_token="other-attempt") is False
        for malformed in (None, True, 0, "1", 1.0):
            bad = {**stopped, "provider_diagnostics": [{**stopped["provider_diagnostics"][0], "ordinal": malformed}]}
            assert recovery._undispatched_episode_page(bad, first_job, operation, owner, "fixture",
                page_calls=[stop_call], attempt_token="fixture-attempt") is False
        missing = {**stopped, "provider_diagnostics": [{key: value for key, value in
            stopped["provider_diagnostics"][0].items() if key != "ordinal"}]}
        assert recovery._undispatched_episode_page(missing, first_job, operation, owner, "fixture",
            page_calls=[stop_call], attempt_token="fixture-attempt") is False
        assert undispatched(page_calls=[Row(**{**vars(stop_call), "actual_cost_microunits": 1})]) is False
        # The lineage episode rule is unchanged.
        assert recovery._undispatched_episode_page(stopped, job, operation, owner, "fixture") is True

    class Session:
        sync_session = SimpleNamespace(get_transaction=lambda: "fixture-transaction")

        async def scalar(self, statement):
            model = statement.column_descriptions[0]["entity"]
            if model is PresentationModel:
                return presentation
            if model is PresentationGenerationJob:
                return job
            if model is PublicOperationAdmission:
                values = statement.compile().params.values()
                return next(item for item in admissions if item.operation_id in values)
            if model is SourceContentManifestRecord:
                return Row(manifest_id="fixture-manifest")
            if model is PublicArtifact:
                # The final metadata readback of a loaded source manifest: the
                # committed artifact row is still present.
                return Row(lifecycle_state="committed")
            raise AssertionError(model)

        async def scalars(self, statement):
            model = statement.column_descriptions[0]["entity"]
            if model is AuthoredSlideCheckpoint:
                return rows
            if model is ProviderUsageReceipt:
                return usage_rows
            if model is PublicProviderLogicalCall:
                values = statement.compile().params.values()
                return current_calls if admissions[0].id in values else parent_calls
            return []

        async def get(self, model, identifier, **kwargs):
            return parent if identifier == parent_id else task

    session = Session()
    controls = {str(item.id): recovery._operation_control(item) for item in (operation, parent)}
    ledger = recovery._recovery_ledger_snapshot(admissions, current_calls + parent_calls, [], usage_rows, [], [])
    verification = SimpleNamespace(seal_memo={"seals": {}, "transaction": None}, finish=AsyncMock())
    monkeypatch.setattr("utils.owner_context.get_current_owner_id", lambda: owner)
    monkeypatch.setattr("utils.owner_context.get_current_tenant_id", lambda: "fixture")
    monkeypatch.setattr(recovery, "_EpisodeVerification", lambda _: verification)
    monkeypatch.setattr(recovery, "_verify_append_only_edge", AsyncMock())
    monkeypatch.setattr(recovery, "verified_header_producer", AsyncMock(return_value=None))
    monkeypatch.setattr(recovery, "_frozen_vector_offer", lambda *args: ([{} for _ in range(request.n_slides)], None))
    for name in ("_pre_dispatch_repair_baseline", "_completed_repair_author_projection",
                 "_terminal_repair_author_projection", "_retained_quality_source_author"):
        monkeypatch.setattr(recovery, name, AsyncMock(return_value=None))
    monkeypatch.setattr(recovery, "_author_render_chain", lambda *args, **kwargs:
        {"render_svg_sha256": sha256_hex(raw.encode())})
    monkeypatch.setattr(recovery, "_payload_storage_seals", AsyncMock(return_value={}))
    monkeypatch.setattr(recovery, "_fresh_recovery_operation_controls", AsyncMock(return_value=controls))
    monkeypatch.setattr(recovery, "_fresh_recovery_ledger_snapshot", AsyncMock(return_value=ledger))
    monkeypatch.setattr(recovery, "_fresh_recovery_model_rows", AsyncMock(side_effect=
        lambda session, model, *args, **kwargs: [job] if model is PresentationGenerationJob else [presentation]))
    monkeypatch.setattr(recovery, "_operation_reference", lambda item, *args, **kwargs: {"id": str(item.id)})
    monkeypatch.setattr(recovery, "_recovery_review_allocation", lambda *args: (0, None, None))
    monkeypatch.setattr(recovery, "_code_digest", lambda: "fixture-code")
    if unknown_mode:
        monkeypatch.setattr(manifest_store, "admit_source_content_manifest_pin", AsyncMock(return_value=
            source if mixed else SimpleNamespace(units=[{}, {}], manifest_digest="fixture-source")))
        real_verify_source_receipt = svg_source_layout.verify_source_receipt

        def verify_fixture_source_receipt(svg, receipt, manifest, page_index, **kwargs):
            if page_index == 0:
                return None
            return real_verify_source_receipt(svg, receipt, manifest, page_index, **kwargs)

        monkeypatch.setattr(svg_source_layout, "verify_source_receipt", verify_fixture_source_receipt)
    if mode == "mixed_unknown_invalid":
        source_validation_counts = {"full": 0, "receipts": []}
        original_source_pages = svg_source_layout.source_pages
        original_source_receipt = svg_source_layout.verify_source_receipt
        original_build_manifest = recovery.build_manifest

        def count_source_pages(manifest, **kwargs):
            source_validation_counts["full"] += 1
            return original_source_pages(manifest, **kwargs)

        def count_source_receipt(svg, receipt, manifest, page_index, **kwargs):
            source_validation_counts["receipts"].append(page_index)
            assert kwargs.get("_validated_pages") is source.units
            return original_source_receipt(svg, receipt, manifest, page_index, **kwargs)

        async def count_build_manifest(*args, **kwargs):
            before_full = source_validation_counts["full"]
            before_receipts = len(source_validation_counts["receipts"])
            result = await original_build_manifest(*args, **kwargs)
            assert source_validation_counts["full"] == before_full + 1
            assert set(result.manifest["known_indices"]) <= set(source_validation_counts["receipts"][before_receipts:])
            return result

        monkeypatch.setattr(svg_source_layout, "source_pages", count_source_pages)
        monkeypatch.setattr(svg_source_layout, "verify_source_receipt", count_source_receipt)
        monkeypatch.setattr(recovery, "build_manifest", count_build_manifest)
    policy = Row(enabled=True, version=1, estimated_cost_limit_microunits=100, actual_cost_limit_microunits=100)
    monkeypatch.setattr(public_admission, "load_public_policy", AsyncMock(return_value=policy))
    monkeypatch.setattr(public_admission, "_policy_digest", lambda _: "fixture-policy")
    service = SimpleNamespace(resolve_ref=AsyncMock(return_value=_source_manifest_reference(parent_id)))
    monkeypatch.setattr(artifact_store, "configured_public_artifact_service", lambda: service)
    monkeypatch.setattr(svg_asset_persistence, "load_source_manifest_pointer", AsyncMock(return_value=
        _source_manifest_asset(digest="fixture-asset", presentation_id=parent_pid, source_revision=1,
            offered_pages=[SimpleNamespace(page_index=index, slot_ids=()) for index in range(request.n_slides)], slots=[])))
    if mode.startswith("inherited_local_"):
        # The parent manifest carries a source-local proof the recovery source job cannot continue.
        operation.request_payload["svg_partial_recovery_manifest"] = {"source_local_continuation": {"digest": "fixture"}}
    if mode == "inherited_local_partial_review":
        rows[1].svg_checkpoint["quality_recovery"] = {"schema": "fixture-partial-review"}
    original_rows = [operation, parent, job, *admissions, *rows, *parent_calls, *current_calls, *usage_rows]
    before = [recovery._dump(item) for item in original_rows]
    if mode == "inherited_local_partial_review":
        with pytest.raises(recovery.PartialRecoveryRejected, match="svg_final_selected_evidence_unproven"):
            asyncio.run(recovery.build_manifest(session, operation, presentation_revision=0))
    elif mode in {"mixed_unmapped_call", "mixed_foreign_call", "mixed_excess_usage", "mixed_unmapped_usage",
            "mixed_zero_usage", "mixed_noninteger_usage"}:
        with pytest.raises(recovery.PartialRecoveryRejected, match="svg_partial_recovery_invalid_source_unproven"):
            asyncio.run(recovery.build_manifest(session, operation, presentation_revision=0))
    elif mode == "current_author":
        with pytest.raises(recovery.PartialRecoveryRejected, match="svg_partial_recovery_local_output_requires_repair"):
            asyncio.run(recovery.build_manifest(session, operation, presentation_revision=0))
    else:
        plan = asyncio.run(recovery.build_manifest(session, operation, presentation_revision=0))
        expected_unknown = [1, 2] if mode == "mixed_own_unknown" else [1]
        expected_new = [2] if mixed and mode != "mixed_own_unknown" else []
        assert plan.manifest["known_indices"] == [0]
        assert len(plan.archive["logical_calls"]) == len(parent_calls) + len(current_calls)
        if mode == "inherited_local_no_review":
            assert "source_local_continuation" not in plan.manifest
            assert plan.manifest["prospective_initial_request_limit"] == plan.manifest["prospective_author_request_count"]
        if mode in {"descendant", "deadline_exceeded", "inherited_local_no_review"}:
            assert plan.manifest["new_indices"] == [1] and plan.manifest["unknown_indices"] == []
            assert plan.manifest["pages"][1]["category"] == "unstarted"
            assert recovery._historical_episode(operation, job)
        else:
            assert plan.manifest["new_indices"] == expected_new and plan.manifest["unknown_indices"] == expected_unknown
            assert plan.manifest["historical_actual_cost_unbounded"] is True
            decision = SimpleNamespace(manifest_hash=plan.manifest_hash, nonce=recovery.decision_nonce(plan.manifest_hash),
                acknowledged_unknown_indices=[], additional_budget_microunits=None)
            action = SimpleNamespace(action="retry", recovery_revision=recovery.REVISION,
                expected_operation_version=operation.version, expected_revision=0, recovery_decision=decision)
            with pytest.raises(recovery.PartialRecoveryRejected, match="svg_partial_recovery_unknown_ack_required"):
                asyncio.run(recovery.verify_action(session, operation, action, presentation_revision=0))
            if unknown_mode:
                assert plan.manifest["pages"][1]["category"] == "unknown"
                assert (recovery._completed_response(rows[1].svg_checkpoint, job, operation, owner, "fixture") is None) == (mode == "superseded_invalid_unknown")
                assert plan.manifest["proven_remaining_budget_microunits"] is None
                assert plan.manifest["prospective_author_request_count"] == (2 if mixed else 1)
                assert recovery.cached_page_payload(rows[1], plan, uuid.uuid4())["partial_recovery"]["category"] == "unknown"
                decision.acknowledged_unknown_indices = expected_unknown
                with pytest.raises(recovery.PartialRecoveryRejected, match="svg_partial_recovery_additional_budget_required"):
                    asyncio.run(recovery.verify_action(session, operation, action, presentation_revision=0))
                decision.additional_budget_microunits = plan.manifest["additional_budget_limit_microunits"] + 1
                with pytest.raises(recovery.PartialRecoveryRejected, match="svg_partial_recovery_budget_exceeds_policy"):
                    asyncio.run(recovery.verify_action(session, operation, action, presentation_revision=0))
                decision.additional_budget_microunits = 10
                approved = asyncio.run(recovery.verify_action(session, operation, action, presentation_revision=0))
                assert approved.approved_budget == 10
                assert approved.manifest["unknown_indices"] == expected_unknown and approved.manifest["new_indices"] == expected_new
                if mode == "mixed_own_unknown":
                    assert plan.manifest["pages"][2]["category"] == "unknown"
                elif mixed:
                    assert plan.manifest["pages"][2]["category"] == "known_failed_output"
                    assert plan.manifest["pages"][2]["completed_output_failure"]["validation_error"] == "svg_source_receipt_text_mismatch"
                    assert len(plan.archive["usage"]) == len(usage_rows)
                    assert recovery.cached_page_payload(rows[2], plan, uuid.uuid4())["partial_recovery"]["category"] == "known_failed_output"
    assert [recovery._dump(item) for item in original_rows] == before


@pytest.mark.parametrize("with_svg", [False, True, "historical_nonuniform", "text_mismatch", "native_table_failures"])
def test_partial_recovery_proves_completed_invalid_source_for_new_child(with_svg, monkeypatch):
    import asyncio
    from copy import deepcopy
    from unittest.mock import AsyncMock
    import xml.etree.ElementTree as ET
    from services import svg_partial_recovery as recovery, artifact_store, svg_asset_persistence
    from services.svg_source_layout import source_pages
    from services.generation_core.authoring.author_source_svg_layout import _whole_page_facts
    from tests.unit.test_svg_source_layout import manifest_for
    from utils.hashing import sha256_hex

    owner, operation_id, job_id, presentation_id = (uuid.uuid4() for _ in range(4))
    operation = SimpleNamespace(id=operation_id, owner_id=owner)
    historical = with_svg == "historical_nonuniform"
    text_mismatch = with_svg == "text_mismatch"
    native_structure = with_svg == "native_table_failures"
    contract = {"version": "svg-source-whole-page/v1"}
    snapshot = dict(digest="fixture-style", source_authoring_contract=contract, authoring_profile={})
    request = GeneratePresentationRequest(content="Fixture message", strategy="svg", n_slides=1,
        source_mode="source_content_restyle", language="en", common_header_mode="free",
        template="authored", authored_style="fixture", content_policy={},
        source_content_manifest_pin={"manifest_id": "fixture-manifest", "digest": "sha256:" + "f" * 64})
    job = SimpleNamespace(id=job_id, workspace_operation_id=operation_id,
        request_payload={}, svg_style_snapshot=snapshot, total_slides=1,
        authored_outline={"slides": [{"content": "Fixture message"}]})
    if historical:
        request.instructions = "body 7px"
    blocks = ([("table", {"rows": [[{"text": f"R{r}C{c}"} for c in range(6)] for r in range(24)]})]
        if historical else [("table", {"rows": [[{"text": "A"}, {"text": "B"}]]})]
        if with_svg and not text_mismatch else [("text", {"text": "A"})])
    source = manifest_for(blocks)
    pages = source_pages(source)
    facts = _whole_page_facts(pages[0], {})
    text_style = 'font-family="Noto Sans KR" font-size="40" font-weight="400" fill="#111111" text-anchor="start"'
    if historical:
        widths = [90, 205, 48, 505, 208, 52]
        xs = [40 + sum(widths[:c]) for c in range(6)]
        historical_text_style = text_style.replace('font-size="40"', 'font-size="14"')
        pieces = []
        for cell in facts[0]["cells"]:
            rr, cc = cell["row"], cell["column"]
            pieces.append(f'<rect x="{xs[cc]}" y="{40+rr*25}" width="{widths[cc]}" height="25" fill="#ffffff" />')
            pieces.append(f'<text id="{cell["element_id"]}" x="{xs[cc]+3}" y="{58+rr*25}" '
                f'{historical_text_style}>{cell["text"]}</text>')
        body = f'<g id="{facts[0]["element_id"]}">' + ''.join(pieces) + '</g>'
        validation_error = "svg_source_native_table_grid_mismatch"
    elif text_mismatch:
        body = f'<text id="{facts[0]["element_id"]}" x="40" y="100" {text_style}>B</text>'
        validation_error = "svg_source_receipt_text_mismatch"
    elif native_structure:
        cells = facts[0]["cells"]
        body = (f'<g id="{facts[0]["element_id"]}" fill="#ffffff">'
            + ''.join(f'<rect x="{40+i*200}" y="80" width="200" height="120" fill="#ffffff" />'
                      f'<text id="{cell["element_id"]}" x="{60+i*200}" y="140" {text_style}>{cell["text"]}</text>'
                      for i, cell in enumerate(cells)) + '</g>')
        validation_error = "svg_source_native_table_group_invalid"
    elif with_svg:
        cells = facts[0]["cells"]
        body = (f'<g id="{facts[0]["element_id"]}"><rect x="40" y="80" width="400" height="120" fill="#ffffff" />'
            + ''.join(f'<text id="{cell["element_id"]}" x="{60+i*200}" y="140" {text_style}>{cell["text"]}</text>'
                      for i, cell in enumerate(cells)) + '</g>')
        validation_error = "svg_source_native_table_grid_mismatch"
    else:
        body = f'<text x="40" y="100" {text_style}>A</text>'
        validation_error = "svg_source_receipt_binding_mismatch"
    raw = f'<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">{body}</svg>'
    svg = ET.tostring(ET.fromstring(raw), encoding="unicode")
    pointer = {"ref": str(uuid.uuid4()), "sha256": "a" * 64}
    asset = _source_manifest_asset(digest="fixture-asset", presentation_id=presentation_id,
        source_revision=1, offered_pages=[SimpleNamespace(page_index=0, slot_ids=())], slots=[])
    service = SimpleNamespace(resolve_ref=AsyncMock(return_value=_source_manifest_reference(operation_id)))
    monkeypatch.setattr(artifact_store, "configured_public_artifact_service", lambda: service)
    monkeypatch.setattr(svg_asset_persistence, "load_source_manifest_pointer", AsyncMock(return_value=asset))
    monkeypatch.setattr("utils.svg_template_fonts.source_font_profile", lambda _: "fixture-font")
    input_digest = recovery.digest({"page": recovery._expected_page_input_digest(request, job, 0, {}),
        "source_manifest": source.manifest_digest, "font_profile": "fixture-font",
        "source_page": pages[0].model_dump(mode="json"), "source_layout_version": "svg-source-layout/v2"})
    receipt = dict(contract_version="svg-static/v2", provider="fixture", requested_model="fixture-model",
        style_digest=snapshot["digest"], authoring_profile={}, source_authoring_contract=contract,
        attempt_ordinal=2, raw_digest="sha256:" + sha256_hex(raw.encode()),
        source_digest="sha256:" + sha256_hex(svg.encode()), validation_error=validation_error)
    terminal = dict(schema="svg-provider-terminal/v1", terminal_event="response.completed",
        operation_id=str(operation_id), job_id=str(job_id), owner_id=str(owner), tenant_id="fixture",
        input_digest=input_digest, ordinal=2, raw_response=raw, output_digest=sha256_hex(raw.encode()), usage={})
    terminal["receipt_digest"] = recovery.digest(terminal)
    previous_terminal = {**deepcopy(terminal), "ordinal": 1}
    previous_terminal["receipt_digest"] = recovery.digest({key: value for key, value in previous_terminal.items()
        if key != "receipt_digest"})
    payload = dict(svg_content=svg if with_svg else None, inflight=None, input_digest=input_digest,
        attempts=[dict(kind="provider", raw_response=raw, receipt={**deepcopy(receipt), "attempt_ordinal": 1}),
                  dict(kind="provider", raw_response=raw, receipt=receipt)],
        source_manifest_ref=pointer, source_manifest_digest=asset.digest,
        provider_terminal_responses={"1": previous_terminal, "2": terminal})
    row = SimpleNamespace(slide_index=0, author_status="completed" if with_svg else "failed",
        render_status="pending", persist_status="pending", svg_checkpoint=payload)
    calls = [SimpleNamespace(slide_index=0, stage="author", state="reported", outcome="succeeded",
        reconciled_at=object(), actual_cost_microunits=1,
        provider_id="fixture", model_id="fixture-model") for _ in range(2)]
    usage = [SimpleNamespace(job_id=job_id, stage="author", provider="fixture", model="fixture-model",
        usage_state="reported", outcome="succeeded", request_count=2)]
    kwargs = dict(completed=terminal, job=job, operation=operation, request=request, source=source,
        seed={"source_font_profile": "fixture-font"}, episode_calls=calls, usage=usage, session=object(),
        owner=owner, tenant="fixture", binding=None, vectors={}, header_producer=None)
    if historical:
        manifest_pin = {"operation_id": str(uuid.uuid4()), "code_digest": "a" * 64}
        lineage = {"schema": "svg-partial-recovery-lineage/v2", "operation_id": str(operation_id),
            "job_id": str(job_id), "manifest_hash": recovery.digest(manifest_pin)}
        job.request_payload = {recovery.LINEAGE_KEY: lineage}
        operation.retry_of_operation_id = uuid.UUID(manifest_pin["operation_id"])
        operation.request_payload = {"svg_partial_recovery_manifest": manifest_pin,
            "svg_partial_recovery_manifest_hash": recovery.digest(manifest_pin),
            "svg_partial_recovery_lineage": lineage, "generation_request": deepcopy(job.request_payload)}
    before = deepcopy(payload)
    proof = asyncio.run(recovery._completed_invalid_source_output(payload, row, **kwargs))
    assert proof["reason"] == "completed_source_invalid" and proof["validation_error"] == validation_error
    assert proof["completed_receipt_digest"] == terminal["receipt_digest"]
    assert proof["author_receipt_digest"] == recovery.author_witness(payload) and payload == before
    if historical:
        compatibility = proof["historical_uniform_grid_rejection"]
        assert "services/svg_source_native_tables.py" in recovery.CODE_FILES
        assert compatibility["reason"] == "historical_uniform_grid_rejected"
        table = compatibility["tables"][0]
        assert (table["rows"], table["columns"], table["cell_count"]) == (24, 6, 144)
        assert table["column_widths"] == widths and table["row_heights"] == [25] * 24
        assert table["first_uniform_mismatch"]["column"] == 0
        assert payload.get("source_preservation") is None
    page = dict(slide_index=0, category="known_failed_output", row_digest="immutable-source-row",
        completed_output_failure=proof)
    manifest = {"pages": [page]}
    plan = SimpleNamespace(manifest=manifest, manifest_hash=recovery.digest(manifest))
    child = recovery.cached_page_payload(row, plan, uuid.uuid4())
    assert set(child) == {"partial_recovery"} and child["partial_recovery"]["category"] == "known_failed_output"
    assert payload == before
    for violation in ("unknown", "inflight", "raw", "terminal", "style", "input", "frozen_input",
            "source", "call", "other_page_call", "usage", "owner", "validator"):
        candidate, changed_calls, changed_usage = deepcopy(payload), deepcopy(calls), deepcopy(usage)
        options = {**kwargs, "episode_calls": changed_calls, "usage": changed_usage}
        if violation == "unknown":
            candidate["provider_failures"] = [{"code": "public_provider_result_unknown"}]
        elif violation == "inflight":
            candidate["inflight"] = 3
        elif violation == "raw":
            candidate["attempts"][-1]["receipt"]["raw_digest"] = "sha256:" + "0" * 64
        elif violation == "terminal":
            candidate["provider_terminal_responses"]["2"]["receipt_digest"] = "0" * 64
        elif violation == "style":
            candidate["attempts"][-1]["receipt"]["style_digest"] = "another-style"
        elif violation == "input":
            candidate["input_digest"] = "another-input"
        elif violation == "frozen_input":
            candidate["input_digest"] = "another-input"
            changed_terminal = candidate["provider_terminal_responses"]["2"]
            changed_terminal["input_digest"] = "another-input"
            changed_terminal["receipt_digest"] = recovery.digest({key: value for key, value in changed_terminal.items()
                if key != "receipt_digest"})
            options["completed"] = changed_terminal
        elif violation == "source":
            candidate["source_manifest_digest"] = "another-source"
        elif violation == "call":
            changed_calls[-1].state = "reserved"
        elif violation == "other_page_call":
            other_page = deepcopy(changed_calls[-1])
            other_page.slide_index, other_page.state = 1, "reserved"
            changed_calls.append(other_page)
        elif violation == "usage":
            changed_usage[0].usage_state = "unavailable"
        elif violation == "owner":
            options["owner"] = uuid.uuid4()
        else:
            candidate["attempts"][-1]["receipt"]["validation_error"] = (
                "svg_source_receipt_binding_mismatch" if with_svg else "svg_source_native_table_grid_mismatch")
        with pytest.raises(recovery.PartialRecoveryRejected):
            asyncio.run(recovery._completed_invalid_source_output(candidate, row, **options))
    unsupported = deepcopy(payload)
    unsupported["attempts"][-1]["receipt"]["validation_error"] = "unproven-local-error"
    assert asyncio.run(recovery._completed_invalid_source_output(unsupported, row, **kwargs)) is None
    if native_structure:
        from services.svg_source_layout import bind_whole_page_source_svg

        def sealed_candidate(candidate_raw, expected_error):
            candidate = deepcopy(payload)
            candidate_svg = ET.tostring(ET.fromstring(candidate_raw), encoding="unicode")
            candidate["svg_content"] = candidate_svg
            last = candidate["attempts"][-1]
            last["raw_response"] = candidate_raw
            last["receipt"].update(validation_error=expected_error,
                raw_digest="sha256:" + sha256_hex(candidate_raw.encode()),
                source_digest="sha256:" + sha256_hex(candidate_svg.encode()))
            last_terminal = candidate["provider_terminal_responses"]["2"]
            last_terminal.update(raw_response=candidate_raw, output_digest=sha256_hex(candidate_raw.encode()))
            last_terminal["receipt_digest"] = recovery.digest({key: value for key, value in last_terminal.items()
                if key != "receipt_digest"})
            return candidate, last_terminal

        root = ET.fromstring(raw)
        group = next(node for node in root if node.tag.endswith("}g"))
        group.attrib.pop("fill")
        valid_raw = ET.tostring(root, encoding="unicode")
        # Establish that the corrected original source really binds, then
        # independently induce a membership failure without changing its text.
        valid_svg, valid_receipt = bind_whole_page_source_svg(valid_raw, source, 0, facts=facts,
            width=1280, height=720, style_snapshot=snapshot, minimum_font_px=7)
        assert valid_receipt["source_authoring_contract"] == contract
        moved_cell = next(node for node in group if node.tag.endswith("}text"))
        group.remove(moved_cell)
        root.append(moved_cell)
        membership_raw = ET.tostring(root, encoding="unicode")
        membership, membership_terminal = sealed_candidate(membership_raw,
            "svg_source_native_table_membership_mismatch")
        membership_before = deepcopy(membership)
        membership_proof = asyncio.run(recovery._completed_invalid_source_output(membership, row,
            **{**kwargs, "completed": membership_terminal}))
        assert membership_proof["validation_error"] == "svg_source_native_table_membership_mismatch"
        assert membership == membership_before and "historical_uniform_grid_rejection" not in membership_proof
        assert payload == before and "historical_uniform_grid_rejection" not in proof
        for base_payload, sealed, expected_proof, status in (
                (payload, terminal, proof, "failed"),
                (membership, membership_terminal, membership_proof, "running")):
            raw_only = deepcopy(base_payload)
            raw_only["svg_content"] = None
            raw_row = deepcopy(row)
            raw_row.author_status = status
            assert asyncio.run(recovery._completed_invalid_source_output(raw_only, raw_row,
                **{**kwargs, "completed": sealed})) == expected_proof
            assert raw_only["svg_content"] is None
        for base_payload, sealed in ((payload, terminal), (membership, membership_terminal)):
            for violation in ("own_actual_unknown", "ghost_usage", "zero_usage", "bool_usage", "float_usage",
                    "string_usage", "different_error", "raw_changed", "declarations_changed", "currently_valid"):
                candidate, current_calls, current_usage = deepcopy(base_payload), deepcopy(calls), deepcopy(usage)
                current_terminal = deepcopy(sealed)
                if violation == "own_actual_unknown":
                    current_calls[-1].actual_cost_microunits = None
                elif violation == "ghost_usage":
                    current_usage[0].request_count = 3
                elif violation in {"zero_usage", "bool_usage", "float_usage", "string_usage"}:
                    current_usage[0].request_count = {"zero_usage": 0, "bool_usage": True,
                        "float_usage": 2.0, "string_usage": "2"}[violation]
                elif violation == "different_error":
                    candidate["attempts"][-1]["receipt"]["validation_error"] = (
                        "svg_source_native_table_group_invalid" if base_payload is membership
                        else "svg_source_native_table_membership_mismatch")
                elif violation == "raw_changed":
                    candidate["attempts"][-1]["raw_response"] += " "
                elif violation == "declarations_changed":
                    candidate["attempts"][-1]["receipt"]["native_declarations"] = {"unproven": True}
                else:
                    candidate, current_terminal = sealed_candidate(valid_raw,
                        base_payload["attempts"][-1]["receipt"]["validation_error"])
                with pytest.raises(recovery.PartialRecoveryRejected, match="svg_partial_recovery_invalid_source_unproven"):
                    asyncio.run(recovery._completed_invalid_source_output(candidate, row,
                        **{**kwargs, "completed": current_terminal, "episode_calls": current_calls,
                           "usage": current_usage}))
        other_native_error = deepcopy(payload)
        other_native_error["attempts"][-1]["receipt"]["validation_error"] = "svg_source_native_table_frame_invalid"
        assert asyncio.run(recovery._completed_invalid_source_output(other_native_error, row, **kwargs)) is None
    if historical:
        from services.svg_source_layout import bind_whole_page_source_svg
        bound, preserved = bind_whole_page_source_svg(svg, source, 0, facts=facts,
            width=1280, height=720, style_snapshot=snapshot, minimum_font_px=7)
        for violation in ("missing_pin", "current_pin", "manifest_hash", "lineage", "parent"):
            changed_operation = deepcopy(operation)
            changed_job = deepcopy(job)
            if violation == "missing_pin":
                changed_operation.request_payload["svg_partial_recovery_manifest"].pop("code_digest")
            elif violation == "current_pin":
                changed_operation.request_payload["svg_partial_recovery_manifest"]["code_digest"] = recovery._code_digest()
            elif violation == "manifest_hash":
                changed_operation.request_payload["svg_partial_recovery_manifest_hash"] = "0" * 64
            elif violation == "lineage":
                changed_operation.request_payload["svg_partial_recovery_lineage"]["operation_id"] = str(uuid.uuid4())
            else:
                changed_operation.retry_of_operation_id = uuid.uuid4()
            if violation in {"missing_pin", "current_pin"}:
                changed_hash = recovery.digest(changed_operation.request_payload["svg_partial_recovery_manifest"])
                changed_operation.request_payload["svg_partial_recovery_manifest_hash"] = changed_hash
                changed_operation.request_payload["svg_partial_recovery_lineage"]["manifest_hash"] = changed_hash
                changed_job.request_payload[recovery.LINEAGE_KEY]["manifest_hash"] = changed_hash
                changed_operation.request_payload["generation_request"] = deepcopy(changed_job.request_payload)
            with pytest.raises(recovery.PartialRecoveryRejected):
                asyncio.run(recovery._completed_invalid_source_output(payload, row,
                    **{**kwargs, "operation": changed_operation, "job": changed_job}))
        root = ET.fromstring(bound)
        group = next(node for node in root if node.tag.rsplit('}', 1)[-1] == 'g')
        for index, rect in enumerate(node for node in group if node.tag.rsplit('}', 1)[-1] == 'rect'):
            rr, cc = divmod(index, 6)
            rect.set("x", str(40 + cc * (sum(widths) / 6)))
            rect.set("width", str(sum(widths) / 6))
        for index, text in enumerate(node for node in group if node.tag.rsplit('}', 1)[-1] == 'text'):
            rr, cc = divmod(index, 6)
            text.set("x", str(43 + cc * (sum(widths) / 6)))
        uniform = ET.tostring(root, encoding="unicode")
        uniform, uniform_receipt = bind_whole_page_source_svg(uniform, source, 0, facts=facts,
            width=1280, height=720, style_snapshot=snapshot, minimum_font_px=7)
        assert recovery._historical_uniform_grid_rejection(uniform, uniform_receipt, pages[0],
            operation=operation, job=job) is None
        assert payload == before
    if text_mismatch:
        from services.svg_source_layout import bind_whole_page_source_svg
        from services.svg_contract import source_digest
        lf_source = manifest_for([("text", {"text": "A\nB"})])
        lf_pages = source_pages(lf_source)
        lf_facts = _whole_page_facts(lf_pages[0], {})
        lf_raw = (f'<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">'
            f'<text id="{lf_facts[0]["element_id"]}" x="40" y="100" {text_style}>AB</text></svg>')
        serialized = ET.tostring(ET.fromstring(lf_raw), encoding="unicode")
        canonical, preservation = bind_whole_page_source_svg(serialized, lf_source, 0,
            facts=lf_facts, width=1280, height=720, style_snapshot=snapshot, minimum_font_px=14,
            restore_source_lf=True, raw_output_digest=source_digest(lf_raw))
        lf_receipt = {**deepcopy(receipt), "validation_error": None,
            "raw_digest": source_digest(lf_raw), "source_digest": source_digest(canonical),
            "source_preservation": deepcopy(preservation)}
        lf_payload = {"svg_content": canonical, "source_preservation": deepcopy(preservation)}
        lf_terminal = {**deepcopy(terminal), "raw_response": lf_raw,
            "output_digest": sha256_hex(lf_raw.encode())}
        lf_terminal["receipt_digest"] = recovery.digest({key: value for key, value in lf_terminal.items()
            if key != "receipt_digest"})
        lf_before = deepcopy((lf_payload, lf_receipt, lf_terminal))

        def replay(candidate, raw_output, author_receipt, sealed):
            return recovery._author_render_chain(candidate, raw_output, author_receipt,
                request=request, job=job, asset=asset, index=0, completed=sealed, source=lf_source)

        chain = replay(lf_payload, lf_raw, lf_receipt, lf_terminal)
        assert chain["transform"] == "source-preserved"
        assert chain["render_svg_sha256"] == sha256_hex(canonical.encode())
        assert preservation["source_lf_restoration"]["restored_lf_count"] == 1
        for violation in ("proof", "raw_sha", "canonical", "other_character", "missing_receipt_proof"):
            changed_payload, changed_receipt, changed_terminal = deepcopy(lf_before)
            changed_raw = lf_raw
            if violation == "proof":
                for carrier in (changed_payload["source_preservation"], changed_receipt["source_preservation"]):
                    carrier["source_lf_restoration"]["restored_lf_count"] = 2
            elif violation == "raw_sha":
                changed_receipt["raw_digest"] = "sha256:" + "0" * 64
            elif violation == "canonical":
                changed_payload["svg_content"] = canonical.replace("#111111", "#222222")
                changed_receipt["source_digest"] = source_digest(changed_payload["svg_content"])
            elif violation == "other_character":
                changed_raw = lf_raw.replace(">AB<", ">AC<")
                changed_receipt["raw_digest"] = source_digest(changed_raw)
                changed_terminal["raw_response"] = changed_raw
                changed_terminal["output_digest"] = sha256_hex(changed_raw.encode())
                changed_terminal["receipt_digest"] = recovery.digest({key: value for key, value in changed_terminal.items()
                    if key != "receipt_digest"})
            else:
                changed_receipt["source_preservation"].pop("source_lf_restoration")
            with pytest.raises(recovery.PartialRecoveryRejected, match="svg_partial_recovery_source_transform_unproven"):
                replay(changed_payload, changed_raw, changed_receipt, changed_terminal)
        legacy_raw = lf_raw.replace(">AB<", ">A\nB<")
        legacy_svg, legacy_preservation = bind_whole_page_source_svg(
            ET.tostring(ET.fromstring(legacy_raw), encoding="unicode"), lf_source, 0,
            facts=lf_facts, width=1280, height=720, style_snapshot=snapshot, minimum_font_px=14)
        legacy_receipt = {**deepcopy(lf_receipt), "source_preservation": legacy_preservation,
            "raw_digest": source_digest(legacy_raw), "source_digest": source_digest(legacy_svg)}
        assert replay({"svg_content": legacy_svg, "source_preservation": legacy_preservation},
            legacy_raw, legacy_receipt, None)["render_svg_sha256"] == sha256_hex(legacy_svg.encode())
        assert (lf_payload, lf_receipt, lf_terminal) == lf_before


def test_partial_recovery_replays_compact_output_view_raws_through_the_pinned_view(monkeypatch):
    """Every replay of a stored compact raw restores it through the job's pinned view."""
    import asyncio
    from copy import deepcopy
    from unittest.mock import AsyncMock
    import xml.etree.ElementTree as ET
    from services import svg_partial_recovery as recovery, artifact_store, svg_asset_persistence
    from services.generation_core.authoring.author_source_svg_layout import _whole_page_facts
    from services.generation_core.drivers.svg.native_declarations import (
        declaration_receipt, extract_native_declarations,
    )
    from services.svg_author_output_view import parse_author_response
    from services.svg_contract import source_digest
    from services.svg_source_layout import bind_whole_page_source_svg, source_pages
    from tests.unit.test_svg_source_layout import manifest_for
    from tests.unit.test_svg_output_view import V1, page_responses, source_page
    from utils.hashing import sha256_hex

    owner, operation_id, job_id, presentation_id = (uuid.uuid4() for _ in range(4))
    contract = {"version": "svg-source-whole-page/v1"}
    plain = dict(digest="fixture-style", source_authoring_contract=contract, authoring_profile={})
    pinned = {**plain, "source_output_view": deepcopy(V1)}
    future = {**plain, "source_output_view": {"version": "svg-source-output-view/v9", "stages": ["id_alias"]}}
    request = GeneratePresentationRequest(content="Fixture message", strategy="svg", n_slides=1,
        source_mode="source_content_restyle", language="en", common_header_mode="free",
        template="authored", authored_style="fixture", content_policy={},
        source_content_manifest_pin={"manifest_id": "fixture-manifest", "digest": "sha256:" + "f" * 64})

    def job_for(snapshot):
        return SimpleNamespace(id=job_id, workspace_operation_id=operation_id, request_payload={},
            svg_style_snapshot=snapshot, total_slides=1,
            authored_outline={"slides": [{"content": "Fixture message"}]})

    asset = _source_manifest_asset(digest="fixture-asset", presentation_id=presentation_id,
        source_revision=1, offered_pages=[SimpleNamespace(page_index=0, slot_ids=())], slots=[])
    text_style = 'font-weight="400" fill="#111111" text-anchor="start"'

    # 1. _author_render_chain: a compact raw whose stored svg_content is its bound expansion.
    lf_source = manifest_for([("text", {"text": "A\nB"})])
    lf_facts = _whole_page_facts(source_pages(lf_source)[0], {})
    compact_lf = ('<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720" '
                  'font-family="Noto Sans KR" font-size="40">'
                  f'<text id="f1" x="40" y="100" {text_style}>AB</text></svg>')
    expanded, _ = parse_author_response(compact_lf, view=V1, source_facts=lf_facts)
    stored, preservation = bind_whole_page_source_svg(ET.tostring(expanded, encoding="unicode"), lf_source, 0,
        facts=lf_facts, width=1280, height=720, style_snapshot=pinned, minimum_font_px=14,
        restore_source_lf=True, raw_output_digest=source_digest(compact_lf))
    assert f'id="{lf_facts[0]["element_id"]}"' in stored and 'font-family="Noto Sans KR"' in stored
    lf_receipt = dict(contract_version="svg-static/v2", provider="fixture", requested_model="fixture-model",
        style_digest="fixture-style", authoring_profile={}, source_authoring_contract=contract,
        attempt_ordinal=1, validation_error=None, raw_digest=source_digest(compact_lf),
        source_digest=source_digest(stored), source_preservation=deepcopy(preservation))
    lf_payload = {"svg_content": stored, "source_preservation": deepcopy(preservation)}

    def chain(snapshot):
        return recovery._author_render_chain(lf_payload, compact_lf, lf_receipt, request=request,
            job=job_for(snapshot), asset=asset, index=0, source=lf_source)

    assert chain(pinned)["render_svg_sha256"] == sha256_hex(stored.encode())
    with pytest.raises(recovery.PartialRecoveryRejected, match="svg_partial_recovery_source_transform_unproven"):
        chain(plain)  # without the pin the compact ids never bind: fail closed, never guessed
    with pytest.raises(recovery.PartialRecoveryRejected, match="svg_output_view_unsupported"):
        chain(future)

    # 2. _replay_sealed_author_transform: the rebuilt projection equals today's canonical run and
    #    carries the producer's output_view receipt entry.
    source = source_page()
    canonical, compact, facts = page_responses(source)

    def sealed(snapshot, raw):
        projection = {}
        result = recovery._replay_sealed_author_transform({}, raw, request=request, job=job_for(snapshot),
            asset=asset, index=0, source=source, source_projection=projection)
        return result, projection

    (today, _, _, today_declarations, _, _), today_projection = sealed(plain, canonical)
    (viewed, _, digest_value, declarations, _, _), projection = sealed(pinned, compact)
    assert viewed == today and digest_value == sha256_hex(today.encode())
    assert declarations == today_declarations and declarations["objects"][0]["kind"] == "table"
    assert "output_view" not in today_projection
    assert projection["output_view"] == parse_author_response(compact, view=V1, source_facts=facts)[1]
    assert projection["output_view"]["inbound"]["native_table_rows"]["expanded"] == 1
    assert {k: v for k, v in projection["source_preservation"].items() if k != "raw_output_digest"} == \
        {k: v for k, v in today_projection["source_preservation"].items() if k != "raw_output_digest"}
    with pytest.raises(recovery.PartialRecoveryRejected, match="svg_output_view_unsupported"):
        sealed(future, compact)

    # 3. _completed_invalid_source_output: a rejected compact raw reproduces its recorded error.
    cells = facts[1]["cells"]
    group =''.join(f'<text id="c{index + 1}" x="{60 + index * 150}" y="140" {text_style}>{cell["text"]}</text>'
                    for index, cell in enumerate(cells))
    invalid_compact = ('<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720" '
        'font-family="Noto Sans KR" font-size="40">'
        f'<text id="f1" x="40" y="60" {text_style}>{facts[0]["data"]["text"].replace("&", "&amp;")}</text>'
        "<g id=\"t1\" data-native-table='{\"headerRows\":1}'>"
        '<rect x="40" y="80" width="600" height="120" fill="#ffffff" />' + group + '</g></svg>')
    expanded_root, _ = parse_author_response(invalid_compact, view=V1, source_facts=facts)
    expanded_declarations = extract_native_declarations(expanded_root)
    expanded_svg = ET.tostring(expanded_root, encoding="unicode")
    pages = source_pages(source)
    monkeypatch.setattr(artifact_store, "configured_public_artifact_service", lambda: SimpleNamespace(
        resolve_ref=AsyncMock(return_value=_source_manifest_reference(operation_id))))
    monkeypatch.setattr(svg_asset_persistence, "load_source_manifest_pointer", AsyncMock(return_value=asset))
    monkeypatch.setattr("utils.svg_template_fonts.source_font_profile", lambda _: "fixture-font")

    def invalid_output(snapshot):
        job = job_for(snapshot)
        input_digest = recovery.digest({"page": recovery._expected_page_input_digest(request, job, 0, {}),
            "source_manifest": source.manifest_digest, "font_profile": "fixture-font",
            "source_page": pages[0].model_dump(mode="json"), "source_layout_version": "svg-source-layout/v2"})
        receipt = dict(contract_version="svg-static/v2", provider="fixture", requested_model="fixture-model",
            style_digest="fixture-style", authoring_profile={}, source_authoring_contract=contract,
            attempt_ordinal=1, raw_digest=source_digest(invalid_compact), source_digest=source_digest(expanded_svg),
            validation_error="svg_source_native_table_grid_mismatch",
            **declaration_receipt(expanded_declarations))
        terminal = dict(schema="svg-provider-terminal/v1", terminal_event="response.completed",
            operation_id=str(operation_id), job_id=str(job_id), owner_id=str(owner), tenant_id="fixture",
            input_digest=input_digest, ordinal=1, raw_response=invalid_compact,
            output_digest=sha256_hex(invalid_compact.encode()), usage={})
        terminal["receipt_digest"] = recovery.digest(terminal)
        payload = dict(svg_content=expanded_svg, inflight=None, input_digest=input_digest,
            attempts=[dict(kind="provider", raw_response=invalid_compact, receipt=receipt)],
            source_manifest_ref={"ref": str(uuid.uuid4()), "sha256": "a" * 64},
            source_manifest_digest=asset.digest, provider_terminal_responses={"1": terminal})
        row = SimpleNamespace(slide_index=0, author_status="completed", render_status="pending",
            persist_status="pending", svg_checkpoint=payload)
        calls = [SimpleNamespace(slide_index=0, stage="author", state="reported", outcome="succeeded",
            reconciled_at=object(), actual_cost_microunits=1, provider_id="fixture", model_id="fixture-model")]
        usage = [SimpleNamespace(job_id=job_id, stage="author", provider="fixture", model="fixture-model",
            usage_state="reported", outcome="succeeded", request_count=1)]
        return asyncio.run(recovery._completed_invalid_source_output(payload, row, completed=terminal, job=job,
            operation=SimpleNamespace(id=operation_id, owner_id=owner), request=request, source=source,
            seed={"source_font_profile": "fixture-font"}, episode_calls=calls, usage=usage, session=object(),
            owner=owner, tenant="fixture", binding=None, vectors={}, header_producer=None))

    proof = invalid_output(pinned)
    assert proof["reason"] == "completed_source_invalid"
    assert proof["validation_error"] == "svg_source_native_table_grid_mismatch"
    with pytest.raises(recovery.PartialRecoveryRejected):
        invalid_output(plain)
    with pytest.raises(recovery.PartialRecoveryRejected, match="svg_output_view_unsupported"):
        invalid_output(future)
    assert "services/svg_author_output_view.py" in recovery.CODE_FILES
    assert {"services/generation_core/authoring/source_id_alias.py",
            "services/svg_text_defaults.py",
            "services/generation_core/authoring/author_source_svg_layout.py"} <= set(recovery.CODE_FILES)


def test_partial_recovery_fingerprints_extracted_runtime_modules():
    import inspect

    from services import svg_partial_recovery as recovery
    from services.svg_recovery_source_evidence import verified_recovery_source_evidence
    from utils.ppt_writing_prompt import writing_policy_prompt
    from utils.svg_validation import (
        _numbers,
        normalize_svg_response,
        source_digest,
        validate_raster_data_url,
        validate_svg,
    )

    symbols = (
        _numbers,
        normalize_svg_response,
        source_digest,
        validate_raster_data_url,
        validate_svg,
        writing_policy_prompt,
        verified_recovery_source_evidence,
    )
    source_paths = {
        str(inspect.getmodule(symbol).__file__).replace("\\", "/").split("/servers/fastapi/")[-1]
        for symbol in symbols
    }

    assert source_paths <= set(recovery.CODE_FILES)


def test_restyle_admission_defaults_and_pins_explicit_output_view_opt_out(monkeypatch):
    from services.document_canvas import document_canvas_for_orientation
    from services.generation_core.authoring.author_slide_svg import svg_prompt_design
    from services.svg_generation_admission import pin_svg_style

    def restyle():
        request = GeneratePresentationRequest(content="Fixture message", strategy="svg", n_slides=1,
            source_mode="source_content_restyle", language="en", common_header_mode="free",
            template="authored", authored_style="academic-edge", content_policy={},
            source_content_manifest_pin={"manifest_id": "fixture-manifest", "digest": "sha256:" + "f" * 64})
        request._document_canvas = document_canvas_for_orientation("landscape")
        return request

    monkeypatch.delenv("SVG_SOURCE_OUTPUT_VIEW", raising=False)
    default_view = pin_svg_style(restyle())
    assert default_view["source_output_view"] == {"version": "svg-source-output-view/v1",
        "stages": ["id_alias", "native_table_rows", "text_defaults:family_size"]}
    monkeypatch.setenv("SVG_SOURCE_OUTPUT_VIEW", "off")
    off = pin_svg_style(restyle())
    assert "source_output_view" not in off
    monkeypatch.setenv("SVG_SOURCE_OUTPUT_VIEW", "v1")
    on = pin_svg_style(restyle())
    assert on["source_output_view"] == {"version": "svg-source-output-view/v1",
        "stages": ["id_alias", "native_table_rows", "text_defaults:family_size"]}
    assert on["digest"] != off["digest"]
    assert {k: v for k, v in on.items() if k not in {"source_output_view", "digest"}} == \
        {k: v for k, v in off.items() if k != "digest"}
    assert "source_output_view" not in svg_prompt_design(on)
    content = pin_svg_style(GeneratePresentationRequest(content="Fixture message", strategy="svg",
                                                        n_slides=1, language="en", common_header_mode="free"))
    assert "source_output_view" not in content
    monkeypatch.setenv("SVG_SOURCE_OUTPUT_VIEW", "ids")
    with pytest.raises(HTTPException) as invalid:
        pin_svg_style(restyle())
    assert (invalid.value.status_code, invalid.value.detail) == (422, "svg_output_view_knob_invalid")
    # A frozen snapshot is never re-read from the knob, and a pin this code cannot read is refused.
    from utils.hashing import canonical_json_digest

    frozen = restyle()
    future = {**off, "source_output_view": {"version": "svg-source-output-view/v9", "stages": ["id_alias"]}}
    future["digest"] = canonical_json_digest({k: v for k, v in future.items() if k != "digest"}, allow_nan=True)
    frozen._svg_style_snapshot = future
    with pytest.raises(HTTPException) as unsupported:
        pin_svg_style(frozen)
    assert (unsupported.value.status_code, unsupported.value.detail) == (409, "svg_output_view_unsupported")


def test_partial_recovery_failed_source_overlap_requires_bound_evidence(tmp_path, monkeypatch):
    import json
    from copy import deepcopy
    from services import svg_partial_recovery as recovery, svg_presentation_checkpoint as checkpoint
    from services.svg_source_layout import source_pages, bind_whole_page_source_svg
    from services.generation_core.authoring.author_source_svg_layout import _whole_page_facts
    from services.svg_text_overlap import text_overlaps
    from tests.unit.test_svg_source_layout import manifest_for
    from utils.hashing import sha256_hex
    from services.source_content.digests import digest_json

    owner, operation_id, job_id, presentation_id = (uuid.uuid4() for _ in range(4))
    operation = SimpleNamespace(id=operation_id, owner_id=owner, error_code="svg_source_quality_failed")
    contract = {"version": "svg-source-whole-page/v1"}
    snapshot = dict(digest="fixture-style", source_authoring_contract=contract, authoring_profile={})
    request = GeneratePresentationRequest(content="Fixture", strategy="svg", n_slides=2,
        source_mode="source_content_restyle", language="en", common_header_mode="free",
        template="authored", authored_style="fixture", content_policy={},
        source_content_manifest_pin={"manifest_id": "fixture-manifest", "digest": "sha256:" + "f" * 64})
    job = SimpleNamespace(id=job_id, workspace_operation_id=operation_id, request_payload={},
        svg_style_snapshot=snapshot, total_slides=2,
        authored_outline={"slides": [{"content": "Fixture"}, {"content": "Other page"}]})
    source = manifest_for([("text", {"text": "A"}), ("text", {"text": "B"})])
    packed = source.model_dump(mode="json", by_alias=True)
    another_unit = deepcopy(packed["units"][0])
    another_unit.update(sourceUnitId="p1", sourceIndex=1)
    for block in another_unit["blocks"]:
        block["sourceBlockId"] = block["sourceBlockId"].replace("p0:", "p1:", 1)
    packed["units"].append(another_unit)
    packed["counts"].update(sourceUnits=2, textBlocks=4)
    packed.pop("manifestDigest")
    packed["manifestDigest"] = digest_json(packed)
    source = type(source).model_validate({**packed, "ownerId": source.owner_id})
    pages = source_pages(source)
    facts = _whole_page_facts(pages[0], {})
    body = ''.join(f'<text id="{fact["element_id"]}" x="80" y="100" font-family="Noto Sans KR" '
        f'font-size="40" font-weight="400" fill="#111111" text-anchor="start">{text}</text>'
        for fact, text in zip(facts, ("A", "B"), strict=False))
    raw = f'<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">{body}</svg>'
    raw, preservation = bind_whole_page_source_svg(raw, source, 0, facts=facts, width=1280,
        height=720, style_snapshot=snapshot, minimum_font_px=12)
    asset = _source_manifest_asset(digest="fixture-asset", presentation_id=presentation_id,
        source_revision=1, offered_pages=[SimpleNamespace(page_index=0, slot_ids=())], slots=[])
    monkeypatch.setattr("utils.svg_template_fonts.source_font_profile", lambda _: "fixture-font")
    input_digest = recovery.digest({"page": recovery._expected_page_input_digest(request, job, 0, {}),
        "source_manifest": source.manifest_digest, "font_profile": "fixture-font",
        "source_page": pages[0].model_dump(mode="json"), "source_layout_version": "svg-source-layout/v2"})
    source_digest = sha256_hex(raw.encode())
    receipt = dict(contract_version="svg-static/v2", provider="fixture", requested_model="fixture-model",
        style_digest=snapshot["digest"], authoring_profile={}, source_authoring_contract=contract,
        attempt_ordinal=2, raw_digest="sha256:" + source_digest, source_digest="sha256:" + source_digest,
        validation_error=None)
    terminal = dict(schema="svg-provider-terminal/v1", terminal_event="response.completed",
        operation_id=str(operation_id), job_id=str(job_id), owner_id=str(owner), tenant_id="fixture",
        input_digest=input_digest, ordinal=2, raw_response=raw, output_digest=source_digest, usage={})
    terminal["receipt_digest"] = recovery.digest(terminal)
    box = dict(x=80, y=60, width=40, height=40)
    metrics = {"fonts_ready": True, "elements": [dict(qid=f"svg-{i}", parent_qid="svg-0",
        has_owned_text=True, text_fragments=[box], text_ink_fragments=[box]) for i in (1, 2)]}
    pairs, basis = text_overlaps(metrics["elements"])
    png = b"immutable-checkpoint-image"
    png_digest = sha256_hex(png)
    monkeypatch.setattr(checkpoint, "checkpoint_root", lambda: tmp_path)
    png_path = tmp_path / "svg" / png_digest[:2] / f"{png_digest}.png"
    png_path.parent.mkdir(parents=True)
    png_path.write_bytes(png)
    quality = dict(passed=False, geometry_passed=False, measurement_complete=True,
        failures=[{"kind": "text_overlap", "pairs": pairs}], overlap_basis=basis,
        physical_readability={"passed": True}, sourceImages=dict(used=[], offered=[], omitted=[]),
        contract_version="svg-static/v1", renderer_version="svg-chromium/v1",
        source_digest="sha256:" + source_digest, png_digest="sha256:" + png_digest,
        wrapper_digest="sha256:" + "a" * 64, browser_metrics=metrics,
        metrics_digest="sha256:" + sha256_hex(json.dumps(metrics, sort_keys=True,
            separators=(",", ":"), ensure_ascii=False, default=str).encode()))
    payload = dict(svg_content=raw, source_digest=source_digest, source_preservation=preservation,
        source_manifest_digest=asset.digest, inflight=None, input_digest=input_digest,
        png_sha256=png_digest, quality=quality,
        attempts=[dict(kind="provider", raw_response=raw, receipt={**deepcopy(receipt), "attempt_ordinal": 1}),
                  dict(kind="provider", raw_response=raw, receipt=receipt)],
        provider_terminal_responses={"2": terminal})
    row = SimpleNamespace(slide_index=0, author_status="completed", render_status="failed",
        persist_status="pending", svg_checkpoint=payload)
    calls = [SimpleNamespace(slide_index=0, stage="author", state="reported", outcome="succeeded",
        reconciled_at="reported", actual_cost_microunits=1, provider_id="fixture", model_id="fixture-model")
        for _ in range(2)]
    usage = [SimpleNamespace(job_id=job_id, stage="author", provider="fixture", model="fixture-model",
        usage_state="reported", outcome="succeeded", request_count=2)]
    kwargs = dict(completed=terminal, job=job, operation=operation, request=request, source=source,
        seed={"source_font_profile": "fixture-font"}, asset=asset, episode_calls=calls, usage=usage,
        owner=owner, tenant="fixture")
    before = deepcopy(payload)
    proof = recovery._completed_failed_source_quality(payload, row, **kwargs)
    assert proof["reason"] == "completed_source_quality_failed" and proof["failure_kind"] == "text_overlap"
    page = dict(slide_index=0, category="known_failed_output", row_digest="immutable-row", completed_output_failure=proof)
    manifest = {"pages": [page]}
    plan = SimpleNamespace(manifest=manifest, manifest_hash=recovery.digest(manifest))
    assert set(recovery.cached_page_payload(row, plan, uuid.uuid4())) == {"partial_recovery"}
    assert payload == before
    other_known = deepcopy(calls[0])
    other_known.slide_index, other_known.stage = 1, "repair"
    other_unknown = deepcopy(calls[0])
    other_unknown.slide_index, other_unknown.state, other_unknown.outcome = 1, "unavailable", "failed"
    other_unknown.actual_cost_microunits = None
    mixed_calls = [*deepcopy(calls), other_known, other_unknown]
    repair_usage = deepcopy(usage[0])
    repair_usage.stage, repair_usage.request_count = "repair", 1
    mixed_usage = [*deepcopy(usage), repair_usage]
    mixed_before = deepcopy((mixed_calls, mixed_usage, payload))
    mixed_options = {**kwargs, "episode_calls": mixed_calls, "usage": mixed_usage}
    assert recovery._completed_failed_source_quality(payload, row, **mixed_options) == proof
    assert recovery._unresolved_provider_call(other_unknown)
    assert (mixed_calls, mixed_usage, payload) == mixed_before
    unresolved_usage = deepcopy(usage[0])
    unresolved_usage.usage_state, unresolved_usage.outcome, unresolved_usage.request_count = "unavailable", "failed", 1
    mixed_usage.append(unresolved_usage)
    assert recovery._completed_failed_source_quality(payload, row,
        **{**mixed_options, "usage": mixed_usage}) == proof
    for violation in ("own_unknown", "missing_known_usage", "overreported", "overunsettled",
            "zero", "bool", "float", "string", "foreign_job", "wrong_stage", "wrong_provider",
            "wrong_model", "malformed_page", "malformed_model", "header_unknown", "unmapped_known_call"):
        altered_calls, altered_usage = deepcopy(mixed_calls), deepcopy(mixed_usage)
        if violation == "own_unknown":
            altered_calls[0].state, altered_calls[0].outcome = "unavailable", "failed"
            altered_calls[0].actual_cost_microunits = None
        elif violation == "missing_known_usage":
            altered_usage[0].request_count = 1
        elif violation == "overreported":
            altered_usage[0].request_count = 3
        elif violation == "overunsettled":
            altered_usage[-1].request_count = 2
        elif violation in {"zero", "bool", "float", "string"}:
            altered_usage[-1].request_count = {"zero": 0, "bool": True, "float": 1.0, "string": "1"}[violation]
        elif violation == "foreign_job":
            altered_usage[-1].job_id = uuid.uuid4()
        elif violation == "wrong_stage":
            altered_usage[-1].stage = "review"
        elif violation == "wrong_provider":
            altered_usage[-1].provider = "unmapped-provider"
        elif violation == "wrong_model":
            altered_usage[-1].model = "unmapped-model"
        elif violation == "malformed_page":
            altered_calls[-1].slide_index = 2
        elif violation == "malformed_model":
            altered_calls[-1].model_id = ""
        elif violation == "header_unknown":
            altered_calls[-1].stage = "review"
        else:
            unmatched = deepcopy(other_known)
            unmatched.stage = "repair_remake"
            altered_calls.append(unmatched)
        with pytest.raises(recovery.PartialRecoveryRejected, match="svg_partial_recovery_failed_source_quality_unproven"):
            recovery._completed_failed_source_quality(payload, row,
                **{**kwargs, "episode_calls": altered_calls, "usage": altered_usage})
    assert recovery._unresolved_provider_call(other_unknown)
    assert payload == before
    # A page whose earlier attempt sealed a ``dispatch.stopped`` at ordinal 2:
    # the same second attempt index answers for terminal ordinal 3, which the
    # receipt carries; receipts without the field (above) keep their ordinal.
    stop = dict(schema="svg-provider-terminal/v1", terminal_event="dispatch.stopped",
        operation_id=str(operation_id), job_id=str(job_id), input_digest=input_digest, ordinal=2,
        attempt_token="attempt-previous", diagnostics={"streamStage": "before_stream",
                                                       "failureKind": "dispatch_stopped"})
    stop["receipt_digest"] = recovery.digest(stop)
    moved = {key: value for key, value in terminal.items() if key != "receipt_digest"}
    moved["ordinal"] = 3
    moved["receipt_digest"] = recovery.digest(moved)
    stopped = deepcopy(payload)
    stopped["provider_terminal_responses"] = {"2": deepcopy(stop), "3": deepcopy(moved)}
    stopped["attempts"][-1]["receipt"]["terminal_ordinal"] = 3
    stopped_before = deepcopy(stopped)
    stopped_options = {**kwargs, "completed": moved}
    stopped_proof = recovery._completed_failed_source_quality(stopped, row, **stopped_options)
    assert stopped_proof["reason"] == "completed_source_quality_failed"
    assert stopped == stopped_before and stopped["provider_terminal_responses"]["2"] == stop
    for tampered in ("absent", "equal", "beyond", "bool", "string", "no_stop"):
        candidate = deepcopy(stopped)
        candidate_receipt = candidate["attempts"][-1]["receipt"]
        if tampered == "absent":
            candidate_receipt.pop("terminal_ordinal")
        elif tampered == "no_stop":
            candidate["provider_terminal_responses"].pop("2")
        else:
            candidate_receipt["terminal_ordinal"] = {"equal": 2, "beyond": 4, "bool": True, "string": "3"}[tampered]
        with pytest.raises(recovery.PartialRecoveryRejected, match="svg_partial_recovery_failed_source_quality_unproven"):
            recovery._completed_failed_source_quality(candidate, row, **stopped_options)
    for violation in ("unknown", "inflight", "owner", "terminal", "style", "input", "source", "preservation",
            "measurement", "physical", "metrics", "pairs", "basis", "png", "images", "call", "other_page",
            "actual", "usage", "usage_count", "render"):
        candidate, changed_row = deepcopy(payload), deepcopy(row)
        changed_calls, changed_usage = deepcopy(calls), deepcopy(usage)
        options = {**kwargs, "episode_calls": changed_calls, "usage": changed_usage}
        if violation == "unknown":
            candidate["provider_failures"] = [{"code": "public_provider_result_unknown"}]
        elif violation == "inflight":
            candidate["inflight"] = 3
        elif violation == "owner":
            options["owner"] = uuid.uuid4()
        elif violation == "terminal":
            candidate["provider_terminal_responses"]["2"]["receipt_digest"] = "0" * 64
        elif violation == "style":
            candidate["attempts"][-1]["receipt"]["style_digest"] = "changed"
        elif violation == "input":
            candidate["input_digest"] = "changed"
        elif violation == "source":
            candidate["source_digest"] = "changed"
        elif violation == "preservation":
            candidate["source_preservation"]["manifest_digest"] = "changed"
        elif violation == "measurement":
            candidate["quality"]["measurement_complete"] = False
        elif violation == "physical":
            candidate["quality"]["physical_readability"]["passed"] = False
        elif violation == "metrics":
            candidate["quality"]["metrics_digest"] = "0" * 64
        elif violation == "pairs":
            candidate["quality"]["failures"][0]["pairs"] = []
        elif violation == "basis":
            candidate["quality"]["overlap_basis"] = {}
        elif violation == "png":
            candidate["quality"]["png_digest"] = "0" * 64
        elif violation == "images":
            candidate["quality"]["sourceImages"]["omitted"] = ["missing"]
        elif violation == "call":
            changed_calls[-1].state = "reserved"
        elif violation == "other_page":
            other = deepcopy(changed_calls[-1])
            other.slide_index, other.outcome = 2, "unknown"
            changed_calls.append(other)
        elif violation == "actual":
            changed_calls[-1].actual_cost_microunits = None
        elif violation == "usage":
            changed_usage[0].usage_state = "unavailable"
        elif violation == "usage_count":
            changed_usage[0].request_count = 1
        elif violation == "render":
            changed_row.render_status = "completed"
        with pytest.raises(recovery.PartialRecoveryRejected):
            recovery._completed_failed_source_quality(candidate, changed_row, **options)
    unsupported = deepcopy(payload)
    unsupported["quality"]["failures"] = [{"kind": "text_clipped"}]
    assert recovery._completed_failed_source_quality(unsupported, row, **kwargs) is None
    png_path.unlink()
    with pytest.raises(recovery.PartialRecoveryRejected):
        recovery._completed_failed_source_quality(payload, row, **kwargs)
    assert payload == before


@pytest.mark.parametrize("conflict", [None, "profile", "snapshot"])
def test_prepare_uses_fenced_current_job_snapshot_before_live_adaptation(conflict, monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock, Mock

    from services.svg_presentation_prepare import _pin_owned_svg_style

    frozen = {"digest": "legacy-frozen", "tokens": {"primary": "#123456"}}
    stale_job = SimpleNamespace(id=uuid.uuid4(), attempt_token="attempt", _workspace_fencing_token=9,
                                svg_style_snapshot={"digest": "stale-job-object"})
    current = SimpleNamespace(svg_style_snapshot=frozen)
    request = _svg_request(_svg_style_snapshot=None, aspect_ratio="16:9")
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
        from services.svg_presentation_prepare import _verify_owned_partial_recovery
        from models.sql.presentation_generation_job import PresentationGenerationJob
        from models.sql.workspace import WorkspaceOperation
        from services.authored_canvas import request_canvas_metadata
        from services.authored_checkpoint_errors import AuthoredStageError
        from services import svg_partial_recovery as recovery

        owner, operation_id = uuid.uuid4(), uuid.uuid4()
        stale_job.workspace_operation_id = operation_id
        fresh = SimpleNamespace(id=stale_job.id, workspace_operation_id=operation_id,
            attempt_token="attempt", state="running", request_payload={"fresh": True})
        live_operation = SimpleNamespace(id=operation_id, attempt_token="attempt", fencing_token=9,
            state="running", cancel_requested_at=None, owner_id=owner)
        manifest = {"owner_id": str(owner), "tenant_id": "preparation-test",
            "expected_page_count": 1, "canvas_digest": recovery.digest(request_canvas_metadata(request))}

        async def verify_without_authority_locks(violation=None):
            events = []
            sessions = [AsyncMock() for _ in range(3)]
            authority, read, final_authority = sessions
            for index, selected in enumerate(sessions):
                selected.__aenter__.return_value = selected
                async def leave(*args, index=index):
                    events.append((index, "closed"))
                selected.__aexit__.side_effect = leave
            authority.commit.side_effect = lambda: events.append((0, "committed"))
            final_authority.commit.side_effect = lambda: events.append((2, "committed"))
            async def load(model, identity):
                assert events == [(0, "committed"), (0, "closed")]
                if model is PresentationGenerationJob:
                    assert identity == stale_job.id
                    return fresh
                assert model is WorkspaceOperation and identity == operation_id
                return live_operation
            read.get.side_effect = load
            verifier = AsyncMock()
            async def slow_verify(selected_session, selected_operation, selected_job):
                assert selected_session is read and selected_operation is live_operation
                assert selected_job is fresh and selected_job is not stale_job
                assert selected_operation.owner_id == owner
                assert events == [(0, "committed"), (0, "closed")]
                await asyncio.sleep(0)
                return {**manifest, **({"owner_id": str(uuid.uuid4())} if violation == "owner" else {})}
            verifier.side_effect = slow_verify
            async def fence(selected_session, identity, token, *, fencing_token=None):
                assert identity == stale_job.id and token == "attempt" and fencing_token == 9
                if selected_session is final_authority:
                    assert (1, "closed") in events
                if violation == "authority" or violation == "takeover" and selected_session is final_authority:
                    raise AuthoredStageError(slide_index=0, stage="persist",
                        code="authored_workspace_operation_fence_lost", retryable=True)
                return fresh
            fenced = AsyncMock(side_effect=fence)
            factory = Mock(side_effect=sessions)
            with monkeypatch.context() as scoped:
                scoped.setattr("utils.owner_context.get_current_owner_id", lambda: owner)
                scoped.setattr("utils.owner_context.get_current_tenant_id", lambda: "preparation-test")
                scoped.setattr(recovery, "verify_prepared_episode", verifier)
                call = _verify_owned_partial_recovery(request, stale_job, 1,
                    session_maker=factory, owned_job=fenced)
                if violation in {"authority", "takeover"}:
                    with pytest.raises(AuthoredStageError) as rejected:
                        await call
                    assert rejected.value.code == "authored_workspace_operation_fence_lost"
                elif violation:
                    with pytest.raises(ValueError, match="svg_partial_recovery_episode_unbound"):
                        await call
                else:
                    assert await call == manifest
                    assert events[-2:] == [(2, "committed"), (2, "closed")]
                    assert fenced.await_count == 2
                read.commit.assert_not_awaited()
                if violation == "authority":
                    assert factory.call_count == 1
                if violation in {"authority", "fence", "attempt", "cancelled"}:
                    verifier.assert_not_awaited()
                else:
                    verifier.assert_awaited_once()

        async def verify_transactions():
            await verify_without_authority_locks()
            for violation, field, value in (("fence", "fencing_token", 10),
                    ("attempt", "attempt_token", "new-attempt"), ("cancelled", "state", "cancelled")):
                saved = getattr(live_operation, field)
                setattr(live_operation, field, value)
                await verify_without_authority_locks(violation)
                setattr(live_operation, field, saved)
            for violation in ("owner", "authority", "takeover"):
                await verify_without_authority_locks(violation)

        asyncio.run(verify_transactions())
        from copy import deepcopy
        import json
        from xml.etree import ElementTree as ET
        from services import svg_partial_recovery as recovery
        from services.svg_authoring_profile import read_quality_recovery_policy
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
        asset = _source_manifest_asset(digest="sha256:" + "a" * 64, offered_pages=[], slots=[],
                                presentation_id=presentation_id, source_revision=1)
        source_pointer = {"ref": str(uuid.uuid4()), "sha256": "a" * 64}
        service = SimpleNamespace(resolve_ref=AsyncMock(return_value=_source_manifest_reference(operation_id)))
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
            style_digest=snapshot["digest"], authoring_profile=dict(snapshot["authoring_profile"]),
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
        bad_raw = raw.replace('<text ', '<text xml:base="https://example.invalid/" ')
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
        from services.svg_authoring_profile import CURRENT_ART_DIRECTION_PROFILE, read_authoring_profile
        no_policy_snapshot = deepcopy(snapshot)
        no_policy_snapshot.pop("quality_recovery_policy", None)
        no_policy_snapshot.pop("quality_recovery_plan", None)
        no_policy_snapshot["digest"] = canonical_json_digest({k: v for k, v in no_policy_snapshot.items() if k != "digest"}, allow_nan=True)
        assert read_authoring_profile(no_policy_snapshot) == CURRENT_ART_DIRECTION_PROFILE
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

        async def exercise_known_quality_runtime_admission():
            from datetime import datetime, timedelta, timezone
            from models.sql.authored_slide_checkpoint import AuthoredSlideCheckpoint
            from models.sql.presentation_generation_job import PresentationGenerationJob
            from models.sql.public_admission import PublicOperationAdmission
            from models.sql.workspace import WorkspaceOperation
            from services import public_operation_admission as public
            from services.public_operation_admission_models import PublicAdmissionRejected

            originals = deepcopy((source_job, source_manifest, candidate, prior, seed))
            admitted_manifest = {**deepcopy(source_manifest), "new_indices": [], "unknown_indices": [],
                "proven_remaining_budget_microunits": 100,
                "prospective_initial_request_limit": 10, "prospective_retry_request_limit": 5}
            manifest_hash = recovery.digest(admitted_manifest)
            admitted_prior, fresh_target = deepcopy(prior), deepcopy(candidate)
            for payload in (admitted_prior, fresh_target):
                payload["partial_recovery"]["manifest_hash"] = manifest_hash
            opaque_canvas = object()
            admitted_ctx = SimpleNamespace(**{**vars(repair_ctx), "partial_recovery_manifest": admitted_manifest,
                "physical_kwargs": {"physical_canvas": opaque_canvas}})
            live_job = SimpleNamespace(**{**deepcopy(vars(source_job)), "state": "running",
                "attempt_token": "known-repair-attempt", "request_payload": {
                    **deepcopy(source_job.request_payload), recovery.LINEAGE_KEY: {"manifest_hash": manifest_hash}}})
            now = datetime(2026, 1, 1, tzinfo=timezone.utc)
            live_operation = SimpleNamespace(**{**{key: None for key in recovery._RUNTIME_OPERATION_FIELDS},
                "id": operation_id, "owner_id": owner, "owner_scope": str(owner),
                "kind": "presentation.generate", "state": "running", "phase": "active",
                "presentation_id": presentation_id, "attempt_token": live_job.attempt_token,
                "fencing_token": 9, "deadline_at": now + timedelta(hours=1), "cancel_requested_at": None,
                "contract_version": "public-operation/v2", "execution_profile_version": "public-generation-export/v2",
                "request_payload": {"principal_tenant_id": tenant, "recovery_revision": recovery.REVISION,
                    "svg_partial_recovery_manifest": admitted_manifest,
                    "svg_partial_recovery_manifest_hash": manifest_hash,
                    "svg_partial_recovery_lineage": {"manifest_hash": manifest_hash},
                    "job_id": str(job_id), "recovery_decision": {"manifest_hash": manifest_hash,
                        "nonce": recovery.decision_nonce(manifest_hash), "acknowledged_unknown_indices": []}}})
            admission = SimpleNamespace(operation_id=operation_id, owner_id=owner, tenant_id=tenant,
                state="active", fence=9, deadline_at=live_operation.deadline_at, policy_digest="current-policy",
                estimated_cost_limit_microunits=100, actual_cost_limit_microunits=100,
                planned_request_limit=10, retry_request_limit=5)
            execution = deepcopy(seed["quality_recovery_execution"])
            queries = []
            target_fields = ("partial_recovery", "input_digest", "quality_recovery", "correction_mode", "attempts",
                "provider_failures", "inflight", "provider_terminal_responses",
                "provider_dispatch_stopped", "provider_diagnostics")

            class KnownRepairSession:
                async def get(self, model, identifier):
                    assert model is PresentationGenerationJob and identifier == job_id
                    return live_job

                async def execute(self, statement):
                    columns = list(statement.selected_columns)
                    if statement.column_descriptions[0]["entity"] is WorkspaceOperation:
                        assert all(column is not WorkspaceOperation.__table__.c.request_payload for column in columns)
                        queries.append("operation_projection")
                        values = {key: getattr(live_operation, key) for key in (
                            *recovery._RUNTIME_OPERATION_FIELDS, "state", "cancel_requested_at")}
                        values.update({"payload_" + key: live_operation.request_payload.get(key)
                            for key in recovery._RUNTIME_PAYLOAD_FIELDS})
                    else:
                        assert statement.column_descriptions[0]["entity"] is AuthoredSlideCheckpoint
                        assert tuple(column.name for column in columns) == target_fields
                        params = statement.compile().params.values()
                        assert job_id in params and 0 in params
                        queries.append("fresh_target_fields")
                        values = {key: deepcopy(fresh_target.get(key)) for key in target_fields}
                    return SimpleNamespace(mappings=lambda: SimpleNamespace(one_or_none=lambda: values))

                async def scalar(self, statement):
                    entity = statement.column_descriptions[0]["entity"]
                    if entity is PublicOperationAdmission:
                        return admission
                    if entity is WorkspaceOperation:
                        queries.append("fresh_phase2_authority")
                        return live_operation
                    assert entity is AuthoredSlideCheckpoint and len(statement.selected_columns) == 1
                    if statement.column_descriptions[0]["name"] == "id":
                        queries.append("target_checkpoint_id")
                        return uuid.uuid4()
                    assert "quality_recovery_execution" in statement.compile().params.values()
                    queries.append("seed_execution_only")
                    return deepcopy(execution)

            session = KnownRepairSession()
            with monkeypatch.context() as scoped:
                scoped.setattr(recovery, "_PREPARED_EPISODES", recovery.OrderedDict())
                scoped.setattr("models.sql.workspace.get_workspace_owner_scope", lambda: str(owner))
                scoped.setattr(public, "_resolved_phase2_authority", AsyncMock())
                scoped.setattr(public, "get_current_utc_datetime", lambda: now)
                scoped.setattr(quality_recovery, "_restore_candidate", lambda *args: deepcopy(admitted_prior))
                recovery._remember_prepared_episode(live_operation, live_job)

                async def admit(**changes):
                    options = dict(expected_attempt_token=live_job.attempt_token, expected_fencing_token=9,
                        slide_index=0, stage="repair", is_retry=True, quality_repair_context=admitted_ctx)
                    options.update(changes)
                    return await recovery.runtime_admission(session, job_id=job_id,
                        operation_id=operation_id, **options)

                assert await admit() is admission
                assert queries.count("fresh_target_fields") == queries.count("seed_execution_only") == 1
                assert queries.count("fresh_phase2_authority") == 1
                for stage in ("repair_remake", "repair_patch"):
                    assert await admit(stage=stage) is admission
                for options in ({"stage": "author"}, {"quality_repair_context": None}, {"is_retry": False}):
                    with pytest.raises(recovery.PartialRecoveryRejected, match="cached_provider_forbidden"):
                        await admit(**options)
                for violation in ("forged_target", "unfinished_gate", "binding", "no_retry_budget", "unknown", "inflight", "attempt_limit"):
                    saved_target, saved_execution = deepcopy(fresh_target), deepcopy(execution)
                    if violation == "forged_target":
                        fresh_target["quality_recovery"]["decision"]["design_targets"][0]["improvementGoal"] = "Unapproved goal."
                    elif violation == "unfinished_gate":
                        execution["rounds"][0]["status"] = "review_unknown"
                    elif violation == "binding":
                        admitted_ctx.quality_recovery_runtime_binding = admitted_ctx.quality_recovery_input_binding = "forged-runtime"
                    elif violation == "no_retry_budget":
                        admission.retry_request_limit = 0
                    elif violation == "unknown":
                        fresh_target["provider_failures"] = [{"code": "public_provider_result_unknown"}]
                    elif violation == "inflight":
                        fresh_target["inflight"] = 2
                    else:
                        fresh_target["attempts"].extend([{"kind": "provider"}, {"kind": "provider"}])
                    with pytest.raises(recovery.PartialRecoveryRejected, match="cached_provider_forbidden"):
                        await admit()
                    fresh_target.clear()
                    fresh_target.update(saved_target)
                    execution.clear()
                    execution.update(saved_execution)
                    admitted_ctx.quality_recovery_runtime_binding = admitted_ctx.quality_recovery_input_binding = runtime
                    admission.retry_request_limit = 5
                live_operation.owner_id = uuid.uuid4()
                with pytest.raises(recovery.PartialRecoveryRejected, match="runtime_unbound"):
                    await admit()
                live_operation.owner_id = owner
                for options in ({"expected_fencing_token": 10}, {"expected_attempt_token": "stale-attempt"}):
                    with pytest.raises(PublicAdmissionRejected) as rejected:
                        await admit(**options)
                    assert rejected.value.code == "public_provider_stale_attempt"
                assert await admit() is admission
                assert admitted_ctx.physical_kwargs["physical_canvas"] is opaque_canvas
            assert (source_job, source_manifest, candidate, prior, seed) == originals

        asyncio.run(exercise_known_quality_runtime_admission())
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
