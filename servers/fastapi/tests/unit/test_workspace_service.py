from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path
import uuid

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from models.sql.presentation import PresentationModel
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
from models.workspace import (
    WorkspaceAttachmentInput,
    WorkspaceAssetTransitionRequest,
    WorkspaceBootstrapRequest,
    WorkspaceCommandRequest,
    WorkspaceEventPageResponse,
    WorkspaceOperationActionRequest,
)
from services.workspace_service import (
    WorkspaceServiceError,
    _safe_payload,
    apply_workspace_action,
    bootstrap_workspace,
    create_workspace_command,
    workspace_events,
    workspace_timeline,
)
from services.public_operation_admission_models import PublicAdmissionRejected
from services.workspace_api_contract import project_workspace_timeline
from services.workspace_asset_service import transition_workspace_asset
from utils.datetime_utils import get_current_utc_datetime


WORKSPACE_SERVICE_TABLES = [
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
]


def test_event_page_contract_serializes_safe_retryable_flag():
    page = WorkspaceEventPageResponse.model_validate(
        {
            "schema_version": 1,
            "events": [
                {
                    "id": "00000000-0000-0000-0000-000000000001:1",
                    "operation_id": "00000000-0000-0000-0000-000000000001",
                    "operation_version": 3,
                    "sequence": 1,
                    "cursor": "42",
                    "type": "failed",
                    "status": "failed",
                    "error": "generation_admission_rejected",
                    "retryable": False,
                }
            ],
            "event_high_watermark": "42",
        }
    )

    wire = page.model_dump(mode="json")
    assert wire["events"][0]["retryable"] is False


def test_svg_timeout_repurchase_counter_preserves_consumed_purchase():
    from services.svg_presentation_checkpoint import _provider_attempt_count

    legacy = {"attempts": [{"kind": "local"}, {"kind": "provider"}],
              "retained_provider_attempt_fence": {"consumed_attempt_count": True}}
    assert _provider_attempt_count(legacy) == 1
    payload = {"recovery_revision": "svg-provider-timeout-repurchase/v1", "attempts": [],
               "retained_provider_attempt_fence": {"consumed_attempt_count": 1, "previous_ordinal": 1}}
    assert _provider_attempt_count(payload) == 1
    assert _provider_attempt_count(payload) + 1 == 2  # next provider ordinal
    payload["attempts"] = [{"kind": "provider"}]
    assert _provider_attempt_count(payload) == 2  # no automatic third purchase
    payload["attempts"] = []
    payload["provider_failures"] = [{"code": "public_provider_overload"}]
    assert _provider_attempt_count(payload) == 2
    for malformed in ({"consumed_attempt_count": True, "previous_ordinal": 1},
                      {"consumed_attempt_count": -1, "previous_ordinal": -1},
                      {"consumed_attempt_count": 1, "previous_ordinal": 2}):
        payload["retained_provider_attempt_fence"] = malformed
        with pytest.raises(ValueError, match="attempt_fence_invalid"):
            _provider_attempt_count(payload)


def test_generation_action_principal_binds_exact_owner_and_verified_local_tenant(tmp_path, monkeypatch):
    asyncio.run(_generation_action_principal_bridge(tmp_path, monkeypatch))


@pytest.mark.parametrize("kind",["slide.regenerate.preview","slide.regenerate","slide.regenerate.batch"])
def test_preview_principal_bridge_keeps_owner_and_refuses_normal_tenant_switch(tmp_path,monkeypatch,kind):
    asyncio.run(_preview_principal_bridge(tmp_path,monkeypatch,kind))


async def _preview_principal_bridge(tmp_path,monkeypatch,kind):
    from types import SimpleNamespace
    from models.sql.public_admission import PublicPolicyProfile,PublicOperationAdmission
    import services.workspace_service as service
    from utils.owner_context import get_current_owner_id,get_current_tenant_id
    from services.workspace_operation_principal import operation_principal
    monkeypatch.setattr("utils.env_settings.public_service.get_public_process_identity_settings",
        lambda:SimpleNamespace(service_tenant_id="service-default"))
    monkeypatch.setattr("models.principal.local_development_tenant_id",lambda:"local-development")
    engine,maker=await _database(tmp_path/"preview-principal.db")
    try:
        async with engine.begin() as c:
            await c.run_sync(lambda s:SQLModel.metadata.create_all(s,tables=[PublicPolicyProfile.__table__,PublicOperationAdmission.__table__]))
        async with maker() as session:
            owner=User(username="preview-owner",hashed_password="unused")
            session.add(owner)
            await session.commit()
        with operation_principal(owner.id,"service-default"):
            presentation=await _create_presentation(maker)
            async with maker() as session:
                _,_,source,_=await bootstrap_workspace(session,presentation_id=presentation.id,request=_bootstrap_request(key="preview-source"))
                source.kind,source.state="presentation.generate","succeeded"
                source.request_payload={"principal_tenant_id":"local-development"}
                values={c.name:100 for c in PublicPolicyProfile.__table__.columns if not c.nullable and c.default is None and c.server_default is None}
                values["version"]="preview-principal-policy"
                policy=PublicPolicyProfile(**values)
                session.add(policy)
                await session.flush()
                session.add(PublicOperationAdmission(operation_id=source.id,policy_version=policy.version,
                    owner_id=owner.id,tenant_id="local-development",trace_id_hash="a"*64,
                    operation_kind="generation",resource_units=1,policy_digest="b"*64,
                    deadline_at=get_current_utc_datetime(),state="succeeded",planned_request_limit=1,retry_request_limit=0,
                    estimated_cost_limit_microunits=100,actual_cost_limit_microunits=100))
                with operation_principal(owner.id,"local-development"):
                    await session.commit()
            observed=[]
            async def created(*args,**kwargs):
                observed.append((get_current_owner_id(),get_current_tenant_id()))
                return "preview-created"
            monkeypatch.setattr(service,"_create_workspace_command",created)
            request=WorkspaceCommandRequest(command="Preview",command_type=kind,idempotency_key="preview-bridge",expected_revision=0)
            async with maker() as session:
                with pytest.raises(WorkspaceServiceError,match="workspace_operation_principal_conflict"):
                    await service.create_workspace_command(session,presentation_id=presentation.id,request=request)
            async with maker() as session:
                assert await service.create_workspace_command(session,presentation_id=presentation.id,request=request,allow_local_development=True)=="preview-created"
            assert observed==[(owner.id,"local-development")]
            assert get_current_owner_id()==owner.id and get_current_tenant_id()=="service-default"
            with operation_principal(uuid.uuid4(),"service-default"):
                async with maker() as session:
                    with pytest.raises(WorkspaceServiceError,match="workspace_operation_principal_conflict"):
                        await service.create_workspace_command(session,presentation_id=presentation.id,
                            request=request,allow_local_development=True)
            assert observed==[(owner.id,"local-development")]
            assert get_current_owner_id()==owner.id and get_current_tenant_id()=="service-default"
    finally:
        await engine.dispose()


async def _generation_action_principal_bridge(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from models.sql.public_admission import PublicPolicyProfile, PublicOperationAdmission
    from services.workspace_operation_principal import (
        generation_action_principal, operation_principal, _export_admission_principal,
    )
    from services.workspace_operation_handler_registry import NonRetryableWorkspaceOperationError
    from utils.owner_context import get_current_tenant_id, get_current_owner_id
    import services.workspace_service as workspace_service

    monkeypatch.setattr("utils.env_settings.public_service.get_public_process_identity_settings",
                        lambda: SimpleNamespace(service_tenant_id="service-default"))
    monkeypatch.setattr("models.principal.local_development_tenant_id", lambda: "local-development")
    engine, maker = await _database(tmp_path / "generation-action-principal.db")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(lambda conn: SQLModel.metadata.create_all(conn,
                tables=[PublicPolicyProfile.__table__, PublicOperationAdmission.__table__]))
        async with maker() as session:
            owner = User(username="generation-action-owner", hashed_password="unused")
            session.add(owner)
            await session.commit()
            owner_id = owner.id
        with operation_principal(owner_id, "service-default"):
            presentation = await _create_presentation(maker)
            async with maker() as session:
                _, _, source, _ = await bootstrap_workspace(session, presentation_id=presentation.id,
                    request=_bootstrap_request(key="generation-action-source"))
                source.kind = "presentation.generate"
                source.execution_profile_version = "public-generation-export/v2"
                source.contract_version = "public-operation/v2"
                source.phase = "rendering"
                source.deadline_at = get_current_utc_datetime() + timedelta(minutes=10)
                source.state, source.version, source.fencing_token = "running", 4, 1
                source.request_payload = {"principal_tenant_id": "local-development",
                    "tenant_id": "local-development", "context": {"tenant_id": "local-development"},
                    "opaque_source_archive": {"full_payload_preserved": True}}
                policy_values = {column.name: 100 for column in PublicPolicyProfile.__table__.columns
                    if not column.nullable and column.default is None and column.server_default is None}
                policy_values["version"] = "generation-action-policy"
                policy = PublicPolicyProfile(**policy_values)
                session.add(policy)
                await session.flush()
                admission = PublicOperationAdmission(operation_id=source.id, policy_version=policy.version,
                    owner_id=owner_id, tenant_id="local-development", trace_id_hash="a" * 64,
                    operation_kind="generation", resource_units=1, policy_digest="b" * 64,
                    deadline_at=source.deadline_at, fence=1,
                    state="active", planned_request_limit=1, retry_request_limit=1,
                    estimated_cost_limit_microunits=100, actual_cost_limit_microunits=100)
                source.deadline_at = admission.deadline_at
                # The fixture models v2 submission in its declared tenant;
                # subsequent workspace actions arrive in the service tenant.
                with operation_principal(owner_id, "local-development"):
                    session.add(admission)
                    await session.commit()
                source_id = source.id
            request = WorkspaceOperationActionRequest(action="cancel", idempotency_key="generation-cancel",
                                                     expected_operation_version=4)
            async with maker() as session:
                with pytest.raises(WorkspaceServiceError) as forbidden:
                    await apply_workspace_action(session, operation_id=source_id, request=request)
                assert forbidden.value.code == "workspace_operation_principal_conflict"
                assert get_current_tenant_id() == "service-default"
            async with maker() as session:
                source = await session.get(WorkspaceOperation, source_id)
                for changes in ({"owner_id": uuid.uuid4()}, {"kind": "export.pptx"},
                                {"id": uuid.uuid4()}, {"request_payload": {"principal_tenant_id": "another-tenant"}},
                                {"request_payload": {}},
                                {"request_payload": {"principal_tenant_id": "local-development", "tenant_id": "another-tenant"}}):
                    candidate = source.model_copy(update=changes)
                    with pytest.raises(NonRetryableWorkspaceOperationError):
                        async with generation_action_principal(session, source=candidate, action="cancel",
                                                               allow_local_development=True):
                            pytest.fail("must not grant unrelated generation authority")
                    assert get_current_tenant_id() == "service-default"
                    assert get_current_owner_id() == owner_id
                with pytest.raises(NonRetryableWorkspaceOperationError):
                    async with generation_action_principal(session, source=source, action="approve",
                                                           allow_local_development=True):
                        pytest.fail("only cancel/retry may use this scope")
                with pytest.raises(NonRetryableWorkspaceOperationError):
                    async with _export_admission_principal(session, sources=[source], operation_kind="generation",
                        allow_local_development=True, legacy_ok=False):
                        pytest.fail("export default must still require succeeded admission")
            actual_apply = workspace_service._apply_workspace_action
            observed_tenants = []

            async def observe_apply(*args, **kwargs):
                observed_tenants.append(get_current_tenant_id())
                return await actual_apply(*args, **kwargs)

            monkeypatch.setattr(workspace_service, "_apply_workspace_action", observe_apply)
            async with maker() as session:
                with pytest.raises(WorkspaceServiceError) as stale:
                    await apply_workspace_action(session, operation_id=source_id,
                        request=request.model_copy(update={"expected_operation_version": 3}), allow_local_development=True)
                assert stale.value.code == "operation_state_conflict"
                assert get_current_tenant_id() == "service-default"
            async with maker() as session:
                from copy import deepcopy
                preloaded_source = await session.get(WorkspaceOperation, source_id)
                preloaded_payload = deepcopy(preloaded_source.request_payload)
                source_read_shapes = []
                original_execute = session.execute

                async def observe_source_reads(statement, *args, **kwargs):
                    if getattr(statement, "is_select", False):
                        keys = set(statement.selected_columns.keys())
                        if "source_principal_tenant" in keys:
                            assert "request_payload" not in keys
                            assert {"source_tenant", "source_context_tenant", "source_context_target"} <= keys
                            source_read_shapes.append("metadata")
                        elif "request_payload" in keys and "owner_scope" in keys:
                            source_read_shapes.append("full")
                    return await original_execute(statement, *args, **kwargs)

                with monkeypatch.context() as source_reads:
                    source_reads.setattr(session, "execute", observe_source_reads)
                    receipt, cancelled, duplicate = await apply_workspace_action(session, operation_id=source_id,
                        request=request, allow_local_development=True)
                assert source_read_shapes[:2] == ["metadata", "full"]
                assert cancelled is preloaded_source
                assert cancelled.request_payload == preloaded_payload
                assert not duplicate and cancelled.state == "cancelling" and cancelled.version == 5
                assert cancelled.cancel_requested_at is not None and receipt.operation_id == source_id
                assert get_current_tenant_id() == "service-default" and get_current_owner_id() == owner_id
            assert observed_tenants == ["local-development", "local-development"]
            async with maker() as session:
                with operation_principal(owner_id, "local-development"):
                    source = await session.get(WorkspaceOperation, source_id)
                    admission = await session.scalar(select(PublicOperationAdmission).where(
                        PublicOperationAdmission.operation_id == source_id))
                    source.state, admission.state = "cancelled", "cancelled"
                    await session.commit()
            async with maker() as session:
                replay_receipt, replay, duplicate = await apply_workspace_action(session, operation_id=source_id,
                    request=request, allow_local_development=True)
                assert duplicate and replay_receipt.id == receipt.id and replay.state == "cancelled"
                with pytest.raises(WorkspaceServiceError) as terminal_cancel:
                    await apply_workspace_action(session, operation_id=source_id,
                        request=request.model_copy(update={"idempotency_key": "new-terminal-cancel", "expected_operation_version": 5}),
                        allow_local_development=True)
                assert terminal_cancel.value.code == "operation_state_conflict"
                assert await session.scalar(select(func.count()).select_from(WorkspaceActionReceipt)) == 1
                assert get_current_tenant_id() == "service-default"
            async with maker() as session:
                with operation_principal(owner_id, "local-development"):
                    source = await session.get(WorkspaceOperation, source_id)
                    admission = await session.scalar(select(PublicOperationAdmission).where(
                        PublicOperationAdmission.operation_id == source_id))
                    source.state, admission.state = "failed", "failed"
                async with generation_action_principal(session, source=source, action="retry",
                                                       allow_local_development=True):
                    assert get_current_tenant_id() == "local-development"
                assert get_current_tenant_id() == "service-default"
                with operation_principal(owner_id, "local-development"):
                    source.state, admission.state = "blocked", "queued"
                async with generation_action_principal(session, source=source, action="retry",
                                                       allow_local_development=True):
                    assert get_current_tenant_id() == "local-development"
                assert get_current_tenant_id() == "service-default"
    finally:
        await engine.dispose()


@pytest.mark.parametrize("mixed", [False, True, 'append_only'])
def test_svg_timeout_repurchase_requires_bound_consent_and_preserves_completed_sources(tmp_path, monkeypatch, mixed):
    if mixed == "append_only":
        asyncio.run(_svg_append_only_recovery_contract(tmp_path, monkeypatch))
        for routing in ("uuid", "null", "missing"):
            case_dir = tmp_path / routing
            case_dir.mkdir()
            asyncio.run(_svg_partial_append_only_contract(case_dir, monkeypatch, source_routing=routing))
        return
    asyncio.run(_svg_timeout_repurchase_bound_consent(tmp_path, monkeypatch, mixed=mixed))


@pytest.mark.parametrize("page_count", [4, 2])
def test_svg_classification_remeasurement_archives_failure_and_protects_cached_bytes(tmp_path, monkeypatch, page_count):
    asyncio.run(_svg_classification_remeasurement_contract(tmp_path, monkeypatch, page_count))


async def _svg_classification_remeasurement_contract(tmp_path, monkeypatch, page_count):
    from copy import deepcopy
    from models.sql.async_presentation_generation_status import AsyncPresentationGenerationTaskModel
    from models.sql.authored_slide_checkpoint import AuthoredSlideCheckpoint
    from models.sql.presentation_generation_job import PresentationGenerationJob
    from models.sql.public_admission import PublicPolicyProfile, PublicOperationAdmission, PublicProviderLogicalCall, PublicResourceLease
    from models.sql.provider_runtime_reservation import ProviderRuntimeBudget, ProviderRuntimeReservation
    from services.svg_classification_remeasurement import (
        SVG_CLASSIFICATION_REMEASUREMENT, CLASSIFICATION_APPROVAL_PREFIX,
        SVG_SOURCE_REMEASUREMENT_DISPATCH, DISPATCH_APPROVAL_PREFIX,
        build_classification_remeasurement_manifest, remeasurement_digest, author_witness_digest,
    )
    from services.authored_vnext.deck_ir.strategy import (
        seal_generation_strategy_decision, validate_sealed_generation_strategy,
        rebind_sealed_generation_strategy, GenerationStrategyCapabilityError,
    )
    from utils.hashing import sha256_hex

    monkeypatch.setattr("services.svg_presentation_checkpoint._read_png", lambda p: b"cached" if p.get("png_sha256") else None)
    admissions = []

    async def admit_generation(session, **kwargs):
        admissions.append(kwargs["operation"].id)

    monkeypatch.setattr("services.presentation_generation_pipeline._admit_generation_operation_v2_compatible", admit_generation)
    engine, maker = await _database(tmp_path / "classification-remeasurement.db")
    extra_tables = [AsyncPresentationGenerationTaskModel.__table__, PresentationGenerationJob.__table__,
        AuthoredSlideCheckpoint.__table__, PublicPolicyProfile.__table__, PublicOperationAdmission.__table__,
        PublicProviderLogicalCall.__table__, PublicResourceLease.__table__, ProviderRuntimeBudget.__table__,
        ProviderRuntimeReservation.__table__]
    try:
        async with engine.begin() as connection:
            await connection.run_sync(lambda conn: SQLModel.metadata.create_all(conn, tables=extra_tables))
        presentation = await _create_presentation(maker)
        now = get_current_utc_datetime()
        async with maker() as session:
            user = User(username="classification-owner", hashed_password="unused")
            session.add(user)
            await session.flush()
            _, _, source, _ = await bootstrap_workspace(session, presentation_id=presentation.id,
                request=_bootstrap_request(key="classification-source"))
            source.owner_id, source.kind, source.state = user.id, "presentation.generate", "failed"
            source.version, source.fencing_token = 4, 2
            error_code = "svg_source_classification_missing" if page_count == 4 else "svg_source_quality_failed"
            source.error_code, source.error_detail = error_code, {"retryable": False}
            task = AsyncPresentationGenerationTaskModel(status="failed")
            session.add(task)
            await session.flush()
            job = PresentationGenerationJob(task_id=task.id, presentation_id=presentation.id,
                workspace_operation_id=source.id, request_sha256="a" * 64,
                request_payload={"strategy": "svg", "content": "classification fixture", "n_slides": page_count,
                    "_internal_svg_authoring_profile": {"id": "art-direction-v1", "version": 7}},
                svg_style_snapshot={"authoring_profile": {"id": "art-direction-v1", "version": 7}, "references": []},
                state="failed", total_slides=page_count, attempt_number=1, attempt_token="old-author-fence")
            session.add(job)
            await session.flush()
            source.request_payload = {**source.request_payload, "job_id": str(job.id)}
            original_strategy = seal_generation_strategy_decision(operation_id=str(source.id),
                requirement_graph_digest="retained-graph", requested_profile="balanced-quality-v1",
                preflight={"missingCapabilities": ["source-binding"], "preflightDigest": "retained-preflight"},
                force_strategy="legacy-authored")
            source.request_payload = {**source.request_payload, "generation_strategy": original_strategy}
            policy_values = {column.name: 100 for column in PublicPolicyProfile.__table__.columns
                if not column.nullable and column.default is None and column.server_default is None}
            policy_values["version"] = "classification-policy"
            session.add(PublicPolicyProfile(**policy_values))
            await session.flush()
            admission = PublicOperationAdmission(operation_id=source.id, policy_version="classification-policy",
                owner_id=user.id, tenant_id="test", trace_id_hash="b" * 64,
                operation_kind="generation", resource_units=1, policy_digest="c" * 64,
                deadline_at=now, state="failed", terminal_at=now, slide_count=page_count,
                planned_request_limit=4, retry_request_limit=4, estimated_cost_limit_microunits=100,
                actual_cost_limit_microunits=100)
            session.add(admission)
            await session.flush()
            rows = []
            for index in range(page_count):
                source_bytes = f"<svg>retained-{index}</svg>"
                png_digest = sha256_hex(f"png-{index}".encode())
                attempts = [{"kind": "provider", "raw_response": f"raw-{index}-{ordinal}",
                             "receipt": {"model": "gpt-6.1-sol", "author_prompt_digest": f"prompt-{index}",
                                "reference_image_digests": ["ref-hash"]}, "quality": {"passed": index != 1}}
                            for ordinal in range(2 if index in {1, 3} else 1)]
                payload = {"input_digest": f"input-{index}", "inflight": None, "attempts": attempts,
                    "svg_content": source_bytes, "png_sha256": png_digest,
                    "quality": {"source_digest": "sha256:" + sha256_hex(source_bytes.encode()),
                        "png_digest": "sha256:" + png_digest, "measurement_complete": True,
                        "geometry_passed": True, "browser_metrics": {"elements": [{"qid": f"q-{index}"}]},
                        "passed": index != 1,
                        "source_classification_association": {"status": "unproven" if index == 1 else "proven"},
                        "failures": [{"kind": "source_classification_unproven"}] if index == 1 else []}}
                if page_count == 2 and index == 1:
                    payload["quality"]["source_classification_association"] = {"status": "proven"}
                    payload["quality"]["failures"] = [{"kind": "native_requested_chart_missing", "id": "chart-1"}]
                    payload["quality"]["requestedNativeObjects"] = {"passed": False, "requiredCharts": 1,
                        "projection": {"dropped": [{"id": "native-chart-chart-1", "kind": "chart",
                                                  "reason": "native_chart_label_not_drawn"}]}}
                rows.append(AuthoredSlideCheckpoint(job_id=job.id, slide_index=index, svg_checkpoint=payload,
                    author_status="failed" if index == 1 else "completed", render_status="completed",
                    failed_stage="author" if index == 1 else None,
                    error={"code": error_code} if index == 1 else None,
                    attempt_token="original-checkpoint-fence"))
                for ordinal in range(len(attempts)):
                    session.add(PublicProviderLogicalCall(admission_id=admission.id,
                        stage="author" if ordinal == 0 else "repair", slide_index=index,
                        logical_call_ordinal=ordinal + 1, retry_class="initial" if ordinal == 0 else "retry",
                        idempotency_key_hash=f"{index}-{ordinal}".ljust(64, "0"), state="reported",
                        estimated_cost_microunits=0, outcome="succeeded", reconciled_at=now))
            session.add_all(rows)
            await session.commit()
            source_id, job_id = source.id, job.id
        async with maker() as session:
            source = await session.get(WorkspaceOperation, source_id)
            recovery = await build_classification_remeasurement_manifest(session, source, presentation_revision=0)
            assert len(recovery.rows) == 1 and recovery.rows[0].slide_index == 1
            assert recovery.manifest["max_additional_author_calls"] == 0
            assert recovery.manifest["mandatory_render_count"] == 1
            assert recovery.manifest["expected_page_count"] == page_count
            assert recovery.manifest["recovery_reason"] == ("classification_legend_paint" if page_count == 4
                                                           else "native_single_series_title_unit")
            action_id = CLASSIFICATION_APPROVAL_PREFIX + recovery.manifest_hash
            originals = {row.slide_index: deepcopy(row.model_dump(mode="json")) for row in await session.scalars(
                select(AuthoredSlideCheckpoint).where(AuthoredSlideCheckpoint.job_id == job_id))}
            original_calls = [row.model_dump(mode="json") for row in await session.scalars(select(
                PublicProviderLogicalCall).order_by(PublicProviderLogicalCall.slide_index, PublicProviderLogicalCall.stage))]
        base = dict(action="retry", recovery_revision=SVG_CLASSIFICATION_REMEASUREMENT,
                    expected_operation_version=4, expected_revision=0, action_id=action_id)
        async with maker() as session:
            target = await session.scalar(select(AuthoredSlideCheckpoint).where(AuthoredSlideCheckpoint.slide_index == 1))
            changed = deepcopy(target.svg_checkpoint)
            changed["quality"]["failures"].append({"kind": "overlap"})
            target.svg_checkpoint = changed
            with pytest.raises(WorkspaceServiceError) as arbitrary_failure:
                await apply_workspace_action(session, operation_id=source_id,
                    request=WorkspaceOperationActionRequest(idempotency_key="unapproved-quality-failure", **base))
            assert arbitrary_failure.value.code == "svg_classification_remeasurement_not_safe"
            await session.rollback()
        if page_count == 2:
            async with maker() as session:
                target = await session.scalar(select(AuthoredSlideCheckpoint).where(AuthoredSlideCheckpoint.slide_index == 1))
                changed = deepcopy(target.svg_checkpoint)
                changed["quality"]["requestedNativeObjects"]["projection"]["dropped"][0]["reason"] = "other-rejection"
                target.svg_checkpoint = changed
                with pytest.raises(WorkspaceServiceError) as other_native_failure:
                    await apply_workspace_action(session, operation_id=source_id,
                        request=WorkspaceOperationActionRequest(idempotency_key="unapproved-native-failure", **base))
                assert other_native_failure.value.code == "svg_classification_remeasurement_not_safe"
                await session.rollback()
        for i, (changes, code) in enumerate([
            ({"action_id": None}, "svg_classification_remeasurement_approval_required"),
            ({"expected_operation_version": None}, "svg_classification_remeasurement_approval_required"),
            ({"expected_revision": None}, "svg_classification_remeasurement_approval_required"),
            ({"expected_operation_version": 3}, "operation_state_conflict"),
            ({"expected_revision": 1}, "revision_conflict"),
            ({"action_id": CLASSIFICATION_APPROVAL_PREFIX + "0" * 64}, "svg_classification_remeasurement_manifest_conflict"),
        ]):
            async with maker() as session:
                with pytest.raises(WorkspaceServiceError) as rejected:
                    await apply_workspace_action(session, operation_id=source_id,
                        request=WorkspaceOperationActionRequest(idempotency_key=f"classification-rejected-{i}", **{**base, **changes}))
                assert rejected.value.code == code
        async with maker() as session:
            call = await session.scalar(select(PublicProviderLogicalCall).where(PublicProviderLogicalCall.slide_index == 0))
            call.state, call.outcome = "unavailable", "failed"
            with pytest.raises(WorkspaceServiceError) as unknown:
                await apply_workspace_action(session, operation_id=source_id,
                    request=WorkspaceOperationActionRequest(idempotency_key="classification-unknown-denied", **base))
            assert unknown.value.code == "svg_classification_remeasurement_provider_unresolved"
            await session.rollback()
        assert not admissions
        request = WorkspaceOperationActionRequest(idempotency_key="classification-approved", **base)
        async with maker() as session:
            receipt, retry, duplicate = await apply_workspace_action(session, operation_id=source_id, request=request)
            assert not duplicate
            receipt_id, retry_id = receipt.id, retry.id
        async with maker() as session:
            receipt, retry, duplicate = await apply_workspace_action(session, operation_id=source_id, request=request)
            assert duplicate and receipt.id == receipt_id and retry.id == retry_id and len(admissions) == 1
            assert (await session.get(PresentationGenerationJob, job_id)).workspace_operation_id == retry_id
            retry_strategy = retry.request_payload["generation_strategy"]
            assert validate_sealed_generation_strategy(retry_strategy, operation_id=str(retry_id)) == "legacy-authored"
            assert {k:v for k,v in retry_strategy.items() if k not in {"operationId", "receiptDigest"}} == {
                k:v for k,v in original_strategy.items() if k not in {"operationId", "receiptDigest"}}
            with pytest.raises(GenerationStrategyCapabilityError):
                rebind_sealed_generation_strategy({**original_strategy, "selectedStrategy": "deck-ir"},
                    source_operation_id=str(source_id), operation_id=str(retry_id))
            assert [row.model_dump(mode="json") for row in await session.scalars(select(
                PublicProviderLogicalCall).order_by(PublicProviderLogicalCall.slide_index, PublicProviderLogicalCall.stage))] == original_calls
            for row in await session.scalars(select(AuthoredSlideCheckpoint)):
                old = originals[row.slide_index]
                if row.slide_index != 1:
                    assert row.model_dump(mode="json") == old
                    continue
                payload = row.svg_checkpoint
                marker = payload["cache_only_remeasurement"]
                assert marker["status"] == "pending" and marker["operation_id"] == str(retry_id)
                assert marker["source_operation_id"] == str(source_id) and marker["slide_index"] == 1
                assert marker["expected_page_count"] == page_count
                assert marker["approval_manifest_hash"] == action_id[len(CLASSIFICATION_APPROVAL_PREFIX):]
                archive = payload["classification_remeasurement_archive"]
                assert archive["checkpoint_row"] == old
                assert remeasurement_digest(archive["approval_manifest"]) == marker["approval_manifest_hash"]
                assert author_witness_digest(payload) == marker["author_receipt_digest"]
                for key in ("svg_content", "png_sha256", "input_digest", "attempts", "quality", "inflight"):
                    assert payload[key] == old["svg_checkpoint"][key]
                assert payload["quality"]["passed"] is False  # renderer must produce the new pass
            # Reproduce the old pre-dispatch operation-ID mismatch without a
            # provider call. A distinct consent revision may retarget pending
            # consumption, while the original source approval stays archived.
            retry.state, retry.error_code = "failed", "generation_strategy_capability_missing"
            retry.error_detail = {"retryable": False}
            retry.request_payload = {**retry.request_payload, "generation_strategy": original_strategy}
            rebound_job = await session.get(PresentationGenerationJob, job_id)
            rebound_job.state = "failed"
            pending_before = deepcopy((await session.scalar(select(AuthoredSlideCheckpoint).where(
                AuthoredSlideCheckpoint.slide_index == 1))).svg_checkpoint)
            second_admission = PublicOperationAdmission(operation_id=retry_id, policy_version="classification-policy",
                owner_id=retry.owner_id, tenant_id="test", trace_id_hash="e" * 64,
                operation_kind="generation", resource_units=1, policy_digest="f" * 64,
                deadline_at=now, state="failed", terminal_at=now, slide_count=page_count,
                planned_request_limit=4, retry_request_limit=4, estimated_cost_limit_microunits=100,
                actual_cost_limit_microunits=100)
            session.add(second_admission)
            await session.commit()
        async with maker() as session:
            failed_retry = await session.get(WorkspaceOperation, retry_id)
            dispatch = await build_classification_remeasurement_manifest(session, failed_retry,
                presentation_revision=0, dispatch_only=True)
            dispatch_version = failed_retry.version
            assert dispatch.manifest["strategy_source_operation_id"] == str(source_id)
            dispatch_request = dict(action="retry", recovery_revision=SVG_SOURCE_REMEASUREMENT_DISPATCH,
                expected_operation_version=dispatch_version, expected_revision=0,
                action_id=DISPATCH_APPROVAL_PREFIX + dispatch.manifest_hash)
        async with maker() as session:
            with pytest.raises(WorkspaceServiceError) as missing_dispatch_consent:
                await apply_workspace_action(session, operation_id=retry_id,
                    request=WorkspaceOperationActionRequest(idempotency_key="dispatch-no-consent", **{
                        **dispatch_request, "action_id": None}))
            assert missing_dispatch_consent.value.code == "svg_classification_remeasurement_approval_required"
        async with maker() as session:
            with pytest.raises(WorkspaceServiceError) as stale_dispatch:
                await apply_workspace_action(session, operation_id=retry_id,
                    request=WorkspaceOperationActionRequest(idempotency_key="dispatch-stale-version", **{
                        **dispatch_request, "expected_operation_version": dispatch_version + 1}))
            assert stale_dispatch.value.code == "operation_state_conflict"
        async with maker() as session:
            target = await session.scalar(select(AuthoredSlideCheckpoint).where(AuthoredSlideCheckpoint.slide_index == 1))
            changed = deepcopy(target.svg_checkpoint)
            changed["cache_only_remeasurement"]["status"] = "completed"
            target.svg_checkpoint = changed
            with pytest.raises(WorkspaceServiceError) as dispatch_consumed:
                await apply_workspace_action(session, operation_id=retry_id,
                    request=WorkspaceOperationActionRequest(idempotency_key="dispatch-already-consumed", **dispatch_request))
            assert dispatch_consumed.value.code == "svg_source_remeasure_dispatch_not_safe"
            await session.rollback()
        async with maker() as session:
            session.add(PublicProviderLogicalCall(admission_id=second_admission.id,
                stage="author", slide_index=1, logical_call_ordinal=1, retry_class="initial",
                idempotency_key_hash="new-call-denied".ljust(64, "0"), state="reported",
                estimated_cost_microunits=0, outcome="succeeded", reconciled_at=now))
            with pytest.raises(WorkspaceServiceError) as dispatch_provider_call:
                await apply_workspace_action(session, operation_id=retry_id,
                    request=WorkspaceOperationActionRequest(idempotency_key="dispatch-provider-denied", **dispatch_request))
            assert dispatch_provider_call.value.code == "svg_classification_remeasurement_provider_unresolved"
            await session.rollback()
        request = WorkspaceOperationActionRequest(idempotency_key="dispatch-approved", **dispatch_request)
        async with maker() as session:
            receipt, next_retry, duplicate = await apply_workspace_action(session, operation_id=retry_id, request=request)
            assert not duplicate
            next_id, dispatch_receipt_id = next_retry.id, receipt.id
        async with maker() as session:
            receipt, next_retry, duplicate = await apply_workspace_action(session, operation_id=retry_id, request=request)
            assert duplicate and receipt.id == dispatch_receipt_id and next_retry.id == next_id and len(admissions) == 2
            assert validate_sealed_generation_strategy(next_retry.request_payload["generation_strategy"],
                operation_id=str(next_id)) == "legacy-authored"
            target = (await session.scalar(select(AuthoredSlideCheckpoint).where(
                AuthoredSlideCheckpoint.slide_index == 1))).svg_checkpoint
            assert target["classification_remeasurement_archive"] == pending_before["classification_remeasurement_archive"]
            assert target["cache_only_remeasurement"]["source_operation_id"] == str(source_id)
            assert target["cache_only_remeasurement"]["operation_id"] == str(next_id)
            assert target["source_remeasurement_dispatch_archive"]["checkpoint_row"]["svg_checkpoint"] == pending_before
            for key in ("svg_content", "png_sha256", "input_digest", "attempts", "quality", "inflight"):
                assert target[key] == pending_before[key]
            assert target["quality"]["passed"] is False
            assert [row.model_dump(mode="json") for row in await session.scalars(select(
                PublicProviderLogicalCall).order_by(PublicProviderLogicalCall.slide_index, PublicProviderLogicalCall.stage))] == original_calls
    finally:
        await engine.dispose()


async def _svg_timeout_repurchase_bound_consent(tmp_path, monkeypatch, *, mixed=False):
    from copy import deepcopy
    from fastapi import HTTPException
    from enums.llm_provider import LLMProvider
    from models.sql.async_presentation_generation_status import AsyncPresentationGenerationTaskModel
    from models.sql.authored_slide_checkpoint import AuthoredSlideCheckpoint
    from models.sql.presentation_generation_job import PresentationGenerationJob
    from models.sql.public_admission import (
        PublicPolicyProfile, PublicOperationAdmission, PublicProviderLogicalCall, PublicResourceLease,
    )
    from models.sql.provider_runtime_reservation import ProviderRuntimeBudget, ProviderRuntimeReservation
    from services.svg_timeout_repurchase import (
        APPROVAL_ACTION_PREFIX, SVG_TIMEOUT_REPURCHASE, SVG_TIMEOUT_REPURCHASE_V2,
        build_timeout_repurchase_manifest, verify_repurchase_provider_target, TimeoutRepurchaseRejected,
    )
    from services.provider_usage_idempotency import _provider_idempotency_key
    from services.provider_usage_instrumentation import PublicOperationFence, ProviderUsageScope
    from utils.hashing import canonical_json_sha256
    revision = SVG_TIMEOUT_REPURCHASE_V2 if mixed else SVG_TIMEOUT_REPURCHASE

    monkeypatch.setattr("utils.llm_provider.get_llm_provider", lambda: LLMProvider.CODEX)
    monkeypatch.setattr("utils.llm_provider.get_model", lambda: "gpt-6-sol")
    monkeypatch.setattr("utils.llm_calls.author_prompt_profile.authoring_model", lambda model: model)
    monkeypatch.setattr("services.svg_presentation_checkpoint._read_png",
                        lambda payload: b"cached PNG" if payload.get("png_sha256") else None)
    admissions = []
    deny_admission = True

    async def admit_generation(session, **kwargs):
        admissions.append(kwargs["operation"].id)
        if deny_admission:
            raise HTTPException(status_code=429, detail="public_owner_queue_full")

    monkeypatch.setattr("services.presentation_generation_pipeline._admit_generation_operation_v2_compatible",
                        admit_generation)
    engine, maker = await _database(tmp_path / "svg-timeout-repurchase.db")
    extra_tables = [AsyncPresentationGenerationTaskModel.__table__, PresentationGenerationJob.__table__,
                    AuthoredSlideCheckpoint.__table__, PublicPolicyProfile.__table__,
                    PublicOperationAdmission.__table__, PublicProviderLogicalCall.__table__,
                    PublicResourceLease.__table__, ProviderRuntimeBudget.__table__, ProviderRuntimeReservation.__table__]
    try:
        async with engine.begin() as connection:
            await connection.run_sync(lambda conn: SQLModel.metadata.create_all(conn, tables=extra_tables))
        presentation = await _create_presentation(maker)
        now = get_current_utc_datetime()
        async with maker() as session:
            user = User(username="svg-timeout-owner", hashed_password="unused")
            session.add(user)
            await session.flush()
            _, _, source, _ = await bootstrap_workspace(session, presentation_id=presentation.id,
                request=_bootstrap_request(key="svg-timeout-source"))
            source.owner_id = user.id
            source.kind = "presentation.generate"
            source.state = "failed"
            source.error_code = "svg_source_classification_missing" if mixed else "public_provider_call_timeout"
            source.error_detail = {"retryable": False}
            source.version = 4
            source.fencing_token = 2
            task = AsyncPresentationGenerationTaskModel(status="failed")
            session.add(task)
            await session.flush()
            job = PresentationGenerationJob(task_id=task.id, presentation_id=presentation.id,
                workspace_operation_id=source.id, request_sha256="a" * 64,
                request_payload={"strategy": "svg", "content": "SVG timeout recovery", "n_slides": 12},
                svg_style_snapshot={"authoring_profile": {"id": "art-direction-v1", "version": 7}},
                state="failed", total_slides=12, attempt_number=1, attempt_token="original-job-fence")
            session.add(job)
            await session.flush()
            source.request_payload = {**source.request_payload, "job_id": str(job.id)}
            policy_values = {column.name: 100 for column in PublicPolicyProfile.__table__.columns
                             if not column.nullable and column.default is None and column.server_default is None}
            policy_values["version"] = "timeout-test-policy"
            policy = PublicPolicyProfile(**policy_values)
            session.add(policy)
            await session.flush()
            admission = PublicOperationAdmission(operation_id=source.id, policy_version=policy.version,
                owner_id=user.id, tenant_id="test", trace_id_hash="b" * 64,
                operation_kind="generation", resource_units=1, policy_digest="c" * 64,
                deadline_at=now, state="failed", terminal_at=now, slide_count=12,
                planned_request_limit=12, retry_request_limit=12,
                estimated_cost_limit_microunits=100, actual_cost_limit_microunits=100)
            session.add(admission)
            await session.flush()
            leases = [PublicResourceLease(admission_id=admission.id, operation_id=source.id,
                policy_version=policy.version, scope_type=scope, scope_key="global" if scope == "global" else "test",
                operation_kind="generation", resource_units=1, deadline_at=now,
                state="released", released_at=now) for scope in ("global", "tenant", "owner")]
            session.add_all(leases)
            rows = []
            for index in range(12):
                unknown = index in {0, 10, 11}
                payload = {"schema_version": "svg-slide-checkpoint/v2", "input_digest": f"input-{index}",
                           "attempts": [], "inflight": 1 if unknown else None}
                if mixed and unknown:
                    payload.update(inflight=None, provider_failures=[{"ordinal": 2 if index == 0 else 1,
                        "code": "public_provider_overload"}])
                    if index == 0:
                        payload.update(svg_content="<svg>unproven source</svg>", png_sha256="failed-png",
                            repair_unavailable=True,
                            quality={"passed": False,"failures":[{"kind":"source_classification_unproven"},
                                {"kind":"provider_repair_unavailable"}]},
                            attempts=[{"kind":"provider","raw_response":"<svg>unproven source</svg>","receipt":{}}])
                        session.add(PublicProviderLogicalCall(admission_id=admission.id, stage="author",slide_index=0,
                            logical_call_ordinal=1,retry_class="initial",idempotency_key_hash="8"*64,
                            state="reported",estimated_cost_microunits=0,outcome="succeeded",reconciled_at=now))
                if not unknown:
                    payload.update(svg_content=f"<svg>source-{index}</svg>", png_sha256=f"png-{index}",
                                   quality={"passed": True}, attempts=[{"kind": "provider", "receipt": {"index": index}}])
                rows.append(AuthoredSlideCheckpoint(job_id=job.id, slide_index=index, svg_checkpoint=payload,
                    author_status="failed" if unknown else "completed", render_status="pending" if unknown else "completed",
                    error={"code": "public_provider_call_timeout"} if unknown else None,
                    attempt_token="original-checkpoint-fence"))
                session.add(PublicProviderLogicalCall(admission_id=admission.id,
                    stage="repair" if mixed and index == 0 else "author", slide_index=index,
                    logical_call_ordinal=2 if mixed and index == 0 else 1,
                    retry_class="initial", idempotency_key_hash=str(index).zfill(64),
                    state="unavailable" if unknown else "reported", estimated_cost_microunits=0,
                    outcome="failed" if index == 0 else "cancelled" if unknown else "succeeded", reconciled_at=now))
            session.add_all(rows)
            await session.commit()
            source_id, job_id, lease_id = source.id, job.id, leases[0].id

        async with maker() as session:
            source = await session.get(WorkspaceOperation, source_id)
            recovery = await build_timeout_repurchase_manifest(session, source, presentation_revision=0,
                recovery_revision=revision)
            assert len(recovery.rows) == 3
            assert len(recovery.manifest["completed_checkpoints"]) == 9
            assert recovery.manifest["max_additional_author_calls"] == 3
            assert recovery.manifest["duplicate_billing_possible"] is True
            assert recovery.manifest["remote_termination_confirmed"] is False
            action_id = APPROVAL_ACTION_PREFIX + recovery.manifest_hash
            original_rows = {row.slide_index: deepcopy(row.model_dump(mode="json")) for row in
                await session.scalars(select(AuthoredSlideCheckpoint).where(AuthoredSlideCheckpoint.job_id == job_id))}
            old_calls = [row.model_dump(mode="json") for row in await session.scalars(
                select(PublicProviderLogicalCall).order_by(PublicProviderLogicalCall.slide_index))]
            original_job = await session.get(PresentationGenerationJob, job_id)
            original_request = deepcopy(original_job.request_payload)
            original_style = deepcopy(original_job.svg_style_snapshot)

        base = dict(action="retry", recovery_revision=revision,
                    expected_operation_version=4, expected_revision=0, action_id=action_id)
        for index, (changes, code) in enumerate([
            ({"action_id": None}, "svg_timeout_repurchase_approval_required"),
            ({"expected_operation_version": None}, "svg_timeout_repurchase_approval_required"),
            ({"expected_revision": None}, "svg_timeout_repurchase_approval_required"),
            ({"expected_operation_version": 3}, "operation_state_conflict"),
            ({"expected_revision": 1}, "revision_conflict"),
            ({"action_id": APPROVAL_ACTION_PREFIX + "0" * 64}, "svg_timeout_repurchase_manifest_conflict"),
        ]):
            async with maker() as session:
                with pytest.raises(WorkspaceServiceError) as rejected:
                    await apply_workspace_action(session, operation_id=source_id,
                        request=WorkspaceOperationActionRequest(idempotency_key=f"rejected-{index}", **{**base, **changes}))
                assert rejected.value.code == code
        assert admissions == []
        async with maker() as session:
            lease = await session.get(PublicResourceLease, lease_id)
            lease.state = "leased"
            await session.commit()
        async with maker() as session:
            with pytest.raises(WorkspaceServiceError) as rejected:
                await apply_workspace_action(session, operation_id=source_id,
                    request=WorkspaceOperationActionRequest(idempotency_key="active-lease-rejected", **base))
            assert rejected.value.code == "svg_timeout_repurchase_active_lease"
        async with maker() as session:
            lease = await session.get(PublicResourceLease, lease_id)
            lease.state = "released"
            await session.commit()
        async with maker() as session:
            call = await session.scalar(select(PublicProviderLogicalCall).where(
                PublicProviderLogicalCall.slide_index == 0))
            call.state = "reserved"
            call.reconciled_at = None
            with pytest.raises(WorkspaceServiceError) as active_call:
                await apply_workspace_action(session, operation_id=source_id,
                    request=WorkspaceOperationActionRequest(idempotency_key="active-call-rejected", **base))
            assert active_call.value.code == "svg_timeout_repurchase_active_call"
            await session.rollback()
        async with maker() as session:
            budget = ProviderRuntimeBudget(tenant_id="test", owner_id=user.id, job_id=job_id)
            session.add(budget)
            await session.flush()
            session.add(ProviderRuntimeReservation(budget_id=budget.id,
                reservation_key_hash="e" * 64, state="uncertain", estimated_cost=0,
                lease_token_hash="f" * 64, lease_expires_at=now))
            with pytest.raises(WorkspaceServiceError) as active_call:
                await apply_workspace_action(session, operation_id=source_id,
                    request=WorkspaceOperationActionRequest(idempotency_key="uncertain-runtime-rejected", **base))
            assert active_call.value.code == "svg_timeout_repurchase_active_call"
            await session.rollback()
        async with maker() as session:
            row = await session.scalar(select(AuthoredSlideCheckpoint).where(
                AuthoredSlideCheckpoint.slide_index == 0))
            row.svg_checkpoint = {**row.svg_checkpoint, "inflight": 2}
            with pytest.raises(WorkspaceServiceError) as exhausted:
                await apply_workspace_action(session, operation_id=source_id,
                    request=WorkspaceOperationActionRequest(idempotency_key="exhausted-page-rejected", **base))
            assert exhausted.value.code == "svg_timeout_repurchase_attempt_budget_exhausted"
            await session.rollback()
        request = WorkspaceOperationActionRequest(idempotency_key="timeout-explicit-consent", **base)
        async with maker() as session:
            with pytest.raises(WorkspaceServiceError) as denied:
                await apply_workspace_action(session, operation_id=source_id, request=request)
            assert denied.value.code == "public_owner_queue_full"
        async with maker() as session:
            assert (await session.get(PresentationGenerationJob, job_id)).workspace_operation_id == source_id
            assert await session.scalar(select(func.count()).select_from(WorkspaceOperation)) == 1
            for row in await session.scalars(select(AuthoredSlideCheckpoint)):
                assert row.model_dump(mode="json") == original_rows[row.slide_index]
        deny_admission = False
        async with maker() as session:
            receipt, retry, duplicate = await apply_workspace_action(session, operation_id=source_id, request=request)
            assert duplicate is False
            assert retry.retry_of_operation_id == source_id
            assert retry.max_attempts == source.max_attempts
            assert retry.request_payload["svg_timeout_repurchase_manifest_hash"] == action_id[len(APPROVAL_ACTION_PREFIX):]
            retry_id, receipt_id = retry.id, receipt.id
        admission_count = len(admissions)
        async with maker() as session:
            receipt, retry, duplicate = await apply_workspace_action(session, operation_id=source_id, request=request)
            assert duplicate is True and receipt.id == receipt_id and retry.id == retry_id
            assert len(admissions) == admission_count
            job = await session.get(PresentationGenerationJob, job_id)
            assert job.workspace_operation_id == retry_id and job.request_payload == original_request
            assert job.svg_style_snapshot == original_style
            assert (await session.get(WorkspaceOperation, source_id)).state == "failed"
            assert [row.model_dump(mode="json") for row in await session.scalars(select(
                PublicProviderLogicalCall).order_by(PublicProviderLogicalCall.slide_index))] == old_calls
            for row in await session.scalars(select(AuthoredSlideCheckpoint)):
                if row.slide_index not in {0, 10, 11}:
                    assert row.model_dump(mode="json") == original_rows[row.slide_index]
                else:
                    payload = row.svg_checkpoint
                    assert payload["previous_checkpoint_row"] == original_rows[row.slide_index]
                    assert payload["previous_checkpoint"] == original_rows[row.slide_index]["svg_checkpoint"]
                    assert payload["inflight"] is None
                    assert payload["retained_provider_attempt_fence"]["previous_ordinal"] == (2 if mixed and row.slide_index == 0 else 1)
                    assert payload["retained_provider_attempt_fence"]["approval_manifest_hash"] == action_id[len(APPROVAL_ACTION_PREFIX):]
                    assert canonical_json_sha256(payload["timeout_repurchase_approval_manifest"]) == action_id[len(APPROVAL_ACTION_PREFIX):]
                    if mixed:
                        from services.svg_presentation_checkpoint import _provider_attempt_limit, _provider_attempt_count
                        assert _provider_attempt_limit(payload,index=row.slide_index,job=job) == (3 if row.slide_index == 0 else 2)
                        assert _provider_attempt_count(payload) == (2 if row.slide_index == 0 else 1)
                        paid = deepcopy(payload)
                        paid["provider_failures"] = [{"ordinal":3 if row.slide_index == 0 else 2,"code":"public_provider_overload"}]
                        assert _provider_attempt_count(paid) == _provider_attempt_limit(paid,index=row.slide_index,job=job)
                        with pytest.raises(ValueError,match="attempt_fence_invalid"):
                            _provider_attempt_limit(payload,index=1,job=job)
            if mixed:
                from types import SimpleNamespace
                preview_admission=SimpleNamespace(id=uuid.uuid4(),owner_id=user.id,tenant_id="test")
                suffix=f".resume-{job.attempt_number}" if job.attempt_number>1 else ""
                args=dict(stage="repair",slide_index=0,logical_call_key="svg-author-0-3"+suffix,
                    provider_id="codex",model_id="gpt-6-sol")
                await verify_repurchase_provider_target(session,retry,preview_admission,**args)
                for changes in ({"slide_index":1},{"logical_call_key":"svg-author-0-2"+suffix},
                                {"model_id":"unapproved-model"}):
                    with pytest.raises(TimeoutRepurchaseRejected,match="not_approved"):
                        await verify_repurchase_provider_target(session,retry,preview_admission,**{**args,**changes})
                preview_admission.tenant_id="different-tenant"
                with pytest.raises(TimeoutRepurchaseRejected,match="not_approved"):
                    await verify_repurchase_provider_target(session,retry,preview_admission,**args)
            scope = ProviderUsageScope(job_id=job_id, operation="authored_generation", stage="repair", attempt_number=1, slide_index=0)
            assert _provider_idempotency_key(PublicOperationFence(source_id, None, 2), scope, "svg-author-0-2") != _provider_idempotency_key(
                PublicOperationFence(retry_id, None, 0), scope, "svg-author-0-2")
    finally:
        await engine.dispose()


async def _database(path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")

    @event.listens_for(engine.sync_engine, "connect")
    def _configure_sqlite(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")

    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: SQLModel.metadata.create_all(
                sync_connection,
                tables=WORKSPACE_SERVICE_TABLES,
            )
        )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _create_presentation(maker) -> PresentationModel:
    async with maker() as session:
        presentation = PresentationModel(
            content="workspace service test",
            n_slides=1,
            language="en",
            layout={"name": "test"},
            # Every deck this fork creates is authored; a row with no
            # identity described a retired React-template format.
            mode="authored",
        )
        session.add(presentation)
        await session.commit()
        return presentation


def _bootstrap_request(
    *,
    key: str = "bootstrap-1",
    prompt: str = "Build a blue strategy deck",
    status: str = "ready",
) -> WorkspaceBootstrapRequest:
    return WorkspaceBootstrapRequest.from_internal_legacy_metadata(
        idempotency_key=key,
        prompt=prompt,
        generation_settings={
            "language": "ko",
            "tone": "executive",
            "n_slides": 8,
        },
        attachments=[
            WorkspaceAttachmentInput(
                file_name=r"C:\private\source\market.pdf",
                media_type="application/pdf",
                size_bytes=321,
                sha256="a" * 64,
                status=status,
            )
        ],
        expected_revision=0,
    )


@pytest.mark.parametrize(
    "secret_key",
    [
        "api_key",
        "api-key",
        "apiKey",
        "APIKey",
        "accessToken",
        "bearerToken",
    ],
)
def test_workspace_request_metadata_rejects_nested_credentials(secret_key: str):
    with pytest.raises(ValueError, match="must not contain credentials"):
        WorkspaceBootstrapRequest(
            idempotency_key="bootstrap-secret",
            prompt="Build a deck",
            generation_settings={"provider": [{secret_key: "do-not-store"}]},
        )

    with pytest.raises(ValueError, match="must not contain credentials"):
        WorkspaceCommandRequest(
            command="Shorten the title",
            idempotency_key="command-secret",
            context={"selection": [{secret_key: "do-not-store"}]},
        )


def test_timeline_payload_sanitizer_normalizes_private_camel_case_keys():
    assert _safe_payload(
        {
            "safe": "visible",
            "providerResponse": "private",
            "storageKey": "private",
            "stackTrace": "private",
            "nested": [{"attachmentUrl": "private", "title": "visible"}],
        }
    ) == {"safe": "visible", "nested": [{"title": "visible"}]}


def test_bootstrap_durably_projects_first_message_attachment_and_settings(tmp_path: Path):
    asyncio.run(_bootstrap_durably_projects_first_message_attachment_and_settings(tmp_path))


async def _bootstrap_durably_projects_first_message_attachment_and_settings(
    tmp_path: Path,
):
    engine, maker = await _database(tmp_path / "bootstrap-service.db")
    try:
        presentation = await _create_presentation(maker)
        request = _bootstrap_request()
        async with maker() as session:
            conversation, message, operation, duplicate = await bootstrap_workspace(
                session,
                presentation_id=presentation.id,
                request=request,
            )
            assert duplicate is False
            assert message.position == 1
            assert operation.anchor_position == 2
            assert operation.state == "queued"

        # Reopen the database to prove this is durable history, not an identity-map
        # projection assembled from the request.
        async with maker() as session:
            timeline = await workspace_timeline(
                session,
                presentation_id=presentation.id,
            )
            assert timeline["conversation_id"] == conversation.id
            assert [item["anchor_type"] for item in timeline["items"]] == [
                "message",
                "operation",
            ]
            first_message = timeline["items"][0]
            assert first_message["id"] == str(message.id)
            assert first_message["content"] == request.prompt
            assert first_message["generation_settings"] == request.generation_settings
            assert first_message["attachments"] == [
                {
                    "schema_version": 1,
                    "id": first_message["attachments"][0]["id"],
                    "name": "market.pdf",
                    "status": "ready",
                    "current_state": "ready",
                    "mime_type": "application/pdf",
                    "size_bytes": 321,
                    "sha256": "a" * 64,
                    "error": None,
                    "safe_failure": None,
                    "download_url": (
                        "/api/v1/ppt/workspace/assets/"
                        f"{first_message['attachments'][0]['id']}/download"
                    ),
                    "download_action": "download",
                    "preview_url": None,
                    "preview_action": None,
                    "unavailable_reason": None,
                }
            ]
            assert [
                event["type"] for event in timeline["items"][1]["events"][:2]
            ] == ["message.created", "queued"]
            assert timeline["items"][1]["events"][0]["operation_version"] == 0
            assert timeline["event_high_watermark"] is not None

            assert await session.scalar(select(func.count()).select_from(WorkspaceMessage)) == 1
            assert await session.scalar(select(func.count()).select_from(WorkspaceAsset)) == 1
            assert await session.scalar(select(func.count()).select_from(WorkspaceOperation)) == 1
            assert await session.scalar(select(func.count()).select_from(WorkspaceOutbox)) == 2
    finally:
        await engine.dispose()


def test_timeline_attachment_snapshot_survives_live_mutation_and_delete(
    tmp_path: Path,
):
    asyncio.run(_timeline_attachment_snapshot_survives_live_mutation_and_delete(tmp_path))


async def _timeline_attachment_snapshot_survives_live_mutation_and_delete(
    tmp_path: Path,
):
    engine, maker = await _database(tmp_path / "attachment-history.db")
    try:
        presentation = await _create_presentation(maker)
        async with maker() as session:
            await bootstrap_workspace(
                session,
                presentation_id=presentation.id,
                request=_bootstrap_request(key="attachment-history"),
            )

        async with maker() as session:
            asset = (await session.scalars(select(WorkspaceAsset))).one()
            immutable_asset_id = asset.id
            asset.state = "failed"
            asset.original_name = "mutated-secret-name.bin"
            asset.media_type = "application/x-private"
            asset.size_bytes = 999
            asset.sha256 = "b" * 64
            asset.failure_code = "provider_scanner_internal_42"
            asset.failure_detail = {
                "scanner": "private-vendor",
                "path": "C:/private/quarantine/file",
                "stack": "must-not-cross-wire",
            }
            session.add(asset)
            await session.commit()

        async with maker() as session:
            projected = project_workspace_timeline(
                await workspace_timeline(session, presentation_id=presentation.id),
                presentation_id=presentation.id,
            ).model_dump(mode="json")
            attachment = projected["items"][0]["attachments"][0]
            assert attachment == {
                "schema_version": 1,
                "id": str(immutable_asset_id),
                "name": "market.pdf",
                "status": "ready",
                "current_state": "failed",
                "mime_type": "application/pdf",
                "size_bytes": 321,
                "sha256": "a" * 64,
                "error": "workspace_asset_failed",
                "safe_failure": {
                    "failure_code": "workspace_asset_failed",
                    "safe_message": "An attachment could not be processed.",
                    "retryable": True,
                },
                "download_url": None,
                "download_action": None,
                "preview_url": None,
                "preview_action": None,
                "unavailable_reason": "workspace_asset_failed",
            }
            assert "private-vendor" not in str(projected)
            assert "must-not-cross-wire" not in str(projected)

        async with maker() as session:
            asset = await session.get(WorkspaceAsset, immutable_asset_id)
            assert asset is not None
            await session.delete(asset)
            await session.commit()

        async with maker() as session:
            durable_attachment = (
                await session.scalars(select(WorkspaceMessageAttachment))
            ).one()
            assert durable_attachment.asset_id is None
            assert durable_attachment.asset_id_snapshot == immutable_asset_id
            projected = project_workspace_timeline(
                await workspace_timeline(session, presentation_id=presentation.id),
                presentation_id=presentation.id,
            ).model_dump(mode="json")
            attachment = projected["items"][0]["attachments"][0]
            assert attachment["id"] == str(immutable_asset_id)
            assert attachment["name"] == "market.pdf"
            assert attachment["status"] == "ready"
            assert attachment["current_state"] is None
            assert attachment["mime_type"] == "application/pdf"
            assert attachment["size_bytes"] == 321
            assert attachment["sha256"] == "a" * 64
            assert attachment["download_url"] is None
            assert attachment["download_action"] is None
            assert attachment["unavailable_reason"] == "workspace_asset_deleted"
            assert attachment["safe_failure"] == {
                "failure_code": "workspace_asset_deleted",
                "safe_message": "An attachment was deleted and must be uploaded again.",
                "retryable": False,
            }
    finally:
        await engine.dispose()


def test_command_idempotency_is_scoped_by_owner_presentation_and_operation_type(
    tmp_path: Path,
):
    asyncio.run(_command_idempotency_scope(tmp_path))


async def _command_idempotency_scope(tmp_path: Path):
    engine, maker = await _database(tmp_path / "command-idempotency-scope.db")
    try:
        first_presentation = await _create_presentation(maker)
        second_presentation = await _create_presentation(maker)
        shared_key = "same-raw-key"

        async with maker() as session:
            _, _, first_bootstrap, first_duplicate = await bootstrap_workspace(
                session,
                presentation_id=first_presentation.id,
                request=_bootstrap_request(key=shared_key),
            )
        async with maker() as session:
            _, _, second_bootstrap, second_duplicate = await bootstrap_workspace(
                session,
                presentation_id=second_presentation.id,
                request=_bootstrap_request(key=shared_key),
            )
        assert first_duplicate is False
        assert second_duplicate is False
        assert first_bootstrap.id != second_bootstrap.id

        presentation_edit = WorkspaceCommandRequest(
            command="Tighten the executive summary",
            command_type="presentation.edit",
            idempotency_key=shared_key,
            expected_revision=0,
        )
        slide_edit = WorkspaceCommandRequest(
            command="Tighten the executive summary",
            command_type="slide.edit",
            idempotency_key=shared_key,
            expected_revision=0,
        )
        async with maker() as session:
            edit_operation, edit_duplicate = await create_workspace_command(
                session,
                presentation_id=first_presentation.id,
                request=presentation_edit,
            )
        async with maker() as session:
            slide_operation, slide_duplicate = await create_workspace_command(
                session,
                presentation_id=first_presentation.id,
                request=slide_edit,
            )
        assert edit_duplicate is False
        assert slide_duplicate is False
        assert edit_operation.id != slide_operation.id
        assert edit_operation.id != first_bootstrap.id

        async with maker() as session:
            replay_operation, replay_duplicate = await create_workspace_command(
                session,
                presentation_id=first_presentation.id,
                request=presentation_edit,
            )
        assert replay_duplicate is True
        assert replay_operation.id == edit_operation.id

        async with maker() as session:
            with pytest.raises(WorkspaceServiceError) as conflict:
                await create_workspace_command(
                    session,
                    presentation_id=first_presentation.id,
                    request=WorkspaceCommandRequest(
                        command="A genuinely different edit",
                        command_type="presentation.edit",
                        idempotency_key=shared_key,
                        expected_revision=0,
                    ),
                )
        assert conflict.value.code == "idempotency_key_payload_conflict"
    finally:
        await engine.dispose()


def test_command_asset_ids_are_claimed_snapshotted_and_atomic(tmp_path: Path):
    asyncio.run(_command_asset_ids_are_claimed_snapshotted_and_atomic(tmp_path))


async def _command_asset_ids_are_claimed_snapshotted_and_atomic(tmp_path: Path):
    engine, maker = await _database(tmp_path / "command-assets.db")
    try:
        presentation = await _create_presentation(maker)
        ready_asset = WorkspaceAsset(
            state="ready",
            original_name=r"C:\private\source\evidence.pdf",
            media_type="application/pdf",
            size_bytes=987,
            storage_key=f"command-assets/{uuid.uuid4()}",
            sha256="b" * 64,
        )
        invalid_hash_asset = WorkspaceAsset(
            state="ready",
            original_name="invalid-hash.pdf",
            media_type="application/pdf",
            size_bytes=123,
            storage_key=f"command-assets/{uuid.uuid4()}",
            sha256=None,
        )
        async with maker() as session:
            session.add_all((ready_asset, invalid_hash_asset))
            await session.commit()

        request = WorkspaceCommandRequest(
            command="Use the attached evidence on the opening slide",
            command_type="presentation.edit",
            idempotency_key="command-with-ready-asset",
            expected_revision=0,
            asset_ids=[ready_asset.id],
        )
        async with maker() as session:
            operation, duplicate = await create_workspace_command(
                session,
                presentation_id=presentation.id,
                request=request,
            )
        assert duplicate is False
        assert operation.state == "queued"
        assert operation.request_payload["asset_ids"] == [str(ready_asset.id)]
        assert operation.request_payload["initial_operation_state"] == "queued"
        message_id = uuid.UUID(operation.request_payload["user_message_id"])

        # Reopen the session: attachment metadata must come from immutable,
        # authoritative snapshots rather than the command request or live row.
        async with maker() as session:
            claimed = await session.get(WorkspaceAsset, ready_asset.id)
            assert claimed is not None
            assert claimed.conversation_id == operation.conversation_id
            snapshot = (
                await session.scalars(
                    select(WorkspaceMessageAttachment).where(
                        WorkspaceMessageAttachment.message_id == message_id
                    )
                )
            ).one()
            assert snapshot.asset_id == ready_asset.id
            assert snapshot.asset_id_snapshot == ready_asset.id
            assert snapshot.ordinal == 0
            assert snapshot.display_name == "evidence.pdf"
            assert snapshot.media_type == "application/pdf"
            assert snapshot.size_bytes == 987
            assert snapshot.asset_state == "ready"
            assert snapshot.asset_sha256 == "b" * 64

            timeline = project_workspace_timeline(
                await workspace_timeline(session, presentation_id=presentation.id),
                presentation_id=presentation.id,
            ).model_dump(mode="json")
            message_item = next(
                item for item in timeline["items"] if item["id"] == str(message_id)
            )
            assert message_item["attachments"][0]["id"] == str(ready_asset.id)
            assert message_item["attachments"][0]["name"] == "evidence.pdf"
            assert message_item["attachments"][0]["sha256"] == "b" * 64

        async with maker() as session:
            replay, duplicate = await create_workspace_command(
                session,
                presentation_id=presentation.id,
                request=request,
            )
        assert duplicate is True
        assert replay.id == operation.id

        async with maker() as session:
            with pytest.raises(WorkspaceServiceError) as changed_assets:
                await create_workspace_command(
                    session,
                    presentation_id=presentation.id,
                    request=request.model_copy(update={"asset_ids": []}),
                )
        assert changed_assets.value.code == "idempotency_key_payload_conflict"

        # Hash validation occurs after the orphan is tentatively claimed. A
        # rejected command must roll back the claim and every staged row even if
        # the caller catches the error and subsequently commits this session.
        async with maker() as session:
            with pytest.raises(WorkspaceServiceError) as invalid_hash:
                await create_workspace_command(
                    session,
                    presentation_id=presentation.id,
                    request=WorkspaceCommandRequest(
                        command="Use an invalid attachment",
                        idempotency_key="command-invalid-asset-hash",
                        expected_revision=0,
                        asset_ids=[invalid_hash_asset.id],
                    ),
                )
            assert invalid_hash.value.code == "workspace_asset_hash_invalid"
            await session.commit()

        async with maker() as session:
            rejected_asset = await session.get(WorkspaceAsset, invalid_hash_asset.id)
            assert rejected_asset is not None
            assert rejected_asset.conversation_id is None
            assert await session.scalar(
                select(func.count()).select_from(WorkspaceMessage)
            ) == 1
            assert await session.scalar(
                select(func.count()).select_from(WorkspaceOperation)
            ) == 1
            assert await session.scalar(
                select(func.count()).select_from(WorkspaceMessageAttachment)
            ) == 1
    finally:
        await engine.dispose()


def test_destructive_command_waits_for_assets_without_bypassing_approval(
    tmp_path: Path,
):
    asyncio.run(
        _destructive_command_waits_for_assets_without_bypassing_approval(tmp_path)
    )


async def _destructive_command_waits_for_assets_without_bypassing_approval(
    tmp_path: Path,
):
    engine, maker = await _database(tmp_path / "command-asset-approval.db")
    try:
        presentation = await _create_presentation(maker)
        asset = WorkspaceAsset(
            state="processing",
            original_name="delete-plan.pdf",
            media_type="application/pdf",
            size_bytes=456,
            storage_key=f"command-assets/{uuid.uuid4()}",
            sha256="c" * 64,
        )
        async with maker() as session:
            session.add(asset)
            await session.commit()

        async with maker() as session:
            operation, duplicate = await create_workspace_command(
                session,
                presentation_id=presentation.id,
                request=WorkspaceCommandRequest(
                    command="Delete this presentation after checking the attachment",
                    command_type="presentation.delete",
                    idempotency_key="destructive-command-waits-for-asset",
                    expected_revision=0,
                    asset_ids=[asset.id],
                ),
            )
        assert duplicate is False
        assert operation.state == "waiting_for_assets"
        assert operation.request_payload["initial_operation_state"] == (
            "awaiting_approval"
        )
        assert operation.request_payload["asset_ids"] == [str(asset.id)]

        async with maker() as session:
            claimed = await session.get(WorkspaceAsset, asset.id)
            assert claimed is not None
            transitioned = await transition_workspace_asset(
                session,
                asset_id=asset.id,
                request=WorkspaceAssetTransitionRequest(
                    state="ready",
                    expected_version=claimed.version,
                ),
            )
            assert transitioned.state == "ready"

        async with maker() as session:
            fresh_operation = await session.get(WorkspaceOperation, operation.id)
            assert fresh_operation is not None
            assert fresh_operation.state == "awaiting_approval"
            assert fresh_operation.next_attempt_at is None
            event_types = list(
                (
                    await session.scalars(
                        select(WorkspaceOperationEvent.event_type)
                        .where(
                            WorkspaceOperationEvent.operation_id == operation.id
                        )
                        .order_by(WorkspaceOperationEvent.sequence)
                    )
                ).all()
            )
            assert event_types == [
                "message.created",
                "waiting_for_assets",
                "asset_state_changed",
            ]
    finally:
        await engine.dispose()


def test_bootstrap_idempotent_duplicate_and_payload_conflict(tmp_path: Path):
    asyncio.run(_bootstrap_idempotent_duplicate_and_payload_conflict(tmp_path))


async def _bootstrap_idempotent_duplicate_and_payload_conflict(tmp_path: Path):
    engine, maker = await _database(tmp_path / "bootstrap-idempotency.db")
    try:
        presentation = await _create_presentation(maker)
        request = _bootstrap_request()
        async with maker() as session:
            first = await bootstrap_workspace(
                session,
                presentation_id=presentation.id,
                request=request,
            )
        async with maker() as session:
            second = await bootstrap_workspace(
                session,
                presentation_id=presentation.id,
                request=request,
            )
        assert second[3] is True
        assert second[1].id == first[1].id
        assert second[2].id == first[2].id

        async with maker() as session:
            with pytest.raises(WorkspaceServiceError) as conflict:
                await bootstrap_workspace(
                    session,
                    presentation_id=presentation.id,
                    request=_bootstrap_request(prompt="A different payload"),
                )
        assert conflict.value.code == "idempotency_key_payload_conflict"
        assert conflict.value.status_code == 409

        async with maker() as session:
            assert await session.scalar(select(func.count()).select_from(WorkspaceMessage)) == 1
            assert await session.scalar(select(func.count()).select_from(WorkspaceOperation)) == 1
            assert await session.scalar(select(func.count()).select_from(WorkspaceActionReceipt)) == 0
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    ("asset_state", "operation_state"),
    [
        ("uploaded", "waiting_for_assets"),
        ("scanning", "waiting_for_assets"),
        ("processing", "waiting_for_assets"),
        ("ready", "queued"),
        ("failed", "blocked"),
        ("quarantined", "blocked"),
        ("expired", "blocked"),
        ("deleted", "blocked"),
    ],
)
def test_bootstrap_asset_state_matrix(
    tmp_path: Path,
    asset_state: str,
    operation_state: str,
):
    asyncio.run(
        _bootstrap_asset_state_matrix(tmp_path, asset_state, operation_state)
    )


async def _bootstrap_asset_state_matrix(
    tmp_path: Path,
    asset_state: str,
    operation_state: str,
):
    engine, maker = await _database(tmp_path / f"asset-{asset_state}.db")
    try:
        presentation = await _create_presentation(maker)
        async with maker() as session:
            _, _, operation, _ = await bootstrap_workspace(
                session,
                presentation_id=presentation.id,
                request=_bootstrap_request(
                    key=f"asset-{asset_state}",
                    status=asset_state,
                ),
            )
            assert operation.state == operation_state
        async with maker() as session:
            timeline = await workspace_timeline(
                session,
                presentation_id=presentation.id,
            )
            assert timeline["items"][0]["attachments"][0]["status"] == asset_state
            assert timeline["items"][1]["status"] == operation_state
            assert [
                event["type"] for event in timeline["items"][1]["events"][:2]
            ] == ["message.created", operation_state]
    finally:
        await engine.dispose()


def test_concurrent_commands_reserve_unique_positions_and_deduplicate_key(tmp_path: Path):
    asyncio.run(_concurrent_commands_reserve_unique_positions_and_deduplicate_key(tmp_path))


async def _concurrent_commands_reserve_unique_positions_and_deduplicate_key(tmp_path: Path):
    engine, maker = await _database(tmp_path / "concurrent-commands.db")
    try:
        presentation = await _create_presentation(maker)
        async with maker() as session:
            await bootstrap_workspace(
                session,
                presentation_id=presentation.id,
                request=_bootstrap_request(),
            )

        async def send(command: str, key: str):
            async with maker() as session:
                return await create_workspace_command(
                    session,
                    presentation_id=presentation.id,
                    request=WorkspaceCommandRequest(
                        command=command,
                        idempotency_key=key,
                        expected_revision=0,
                    ),
                )

        independent = await asyncio.gather(
            send("Add risks", "tab-a"),
            send("Shorten conclusion", "tab-b"),
        )
        assert {result[0].id for result in independent}.__len__() == 2
        assert {result[1] for result in independent} == {False}

        duplicate_pair = await asyncio.gather(
            send("Use a darker blue", "same-command"),
            send("Use a darker blue", "same-command"),
        )
        assert len({result[0].id for result in duplicate_pair}) == 1
        assert sorted(result[1] for result in duplicate_pair) == [False, True]

        async with maker() as session:
            messages = list(
                (
                    await session.scalars(
                        select(WorkspaceMessage).order_by(WorkspaceMessage.position)
                    )
                ).all()
            )
            operations = list(
                (
                    await session.scalars(
                        select(WorkspaceOperation).order_by(
                            WorkspaceOperation.anchor_position
                        )
                    )
                ).all()
            )
            all_positions = [message.position for message in messages] + [
                operation.anchor_position for operation in operations
            ]
            assert len(all_positions) == len(set(all_positions))
            assert [message.content for message in messages].count("Use a darker blue") == 1
            assert len(operations) == 4  # bootstrap + two tabs + one deduplicated command
    finally:
        await engine.dispose()


def test_message_pagination_and_event_high_watermark_are_independent(tmp_path: Path):
    asyncio.run(_message_pagination_and_event_high_watermark_are_independent(tmp_path))


async def _message_pagination_and_event_high_watermark_are_independent(tmp_path: Path):
    engine, maker = await _database(tmp_path / "timeline-cursors.db")
    try:
        presentation = await _create_presentation(maker)
        async with maker() as session:
            await bootstrap_workspace(
                session,
                presentation_id=presentation.id,
                request=_bootstrap_request(),
            )
        async with maker() as session:
            await create_workspace_command(
                session,
                presentation_id=presentation.id,
                request=WorkspaceCommandRequest(
                    command="Add an appendix",
                    idempotency_key="appendix",
                    expected_revision=0,
                ),
            )

        async with maker() as session:
            latest = await workspace_timeline(
                session,
                presentation_id=presentation.id,
                limit=2,
            )
            assert [item["position"] for item in latest["items"]] == [3, 4]
            assert latest["next_before_position"] == 3
            assert latest["has_more"] is True
            event_hwm = latest["event_high_watermark"]
            assert event_hwm is not None

            older = await workspace_timeline(
                session,
                presentation_id=presentation.id,
                before_position=latest["next_before_position"],
                limit=2,
            )
            assert [item["position"] for item in older["items"]] == [1, 2]
            assert older["event_high_watermark"] == event_hwm

            all_events = await workspace_events(
                session,
                presentation_id=presentation.id,
            )
            assert len(all_events["events"]) == 4
            assert [
                event["operation_version"] for event in all_events["events"]
            ] == [0, 0, 0, 0]
            assert [
                event["operation_anchor_position"] for event in all_events["events"]
            ] == [2, 2, 4, 4]
            first_event_cursor = all_events["events"][0]["cursor"]
            backfill = await workspace_events(
                session,
                presentation_id=presentation.id,
                after=first_event_cursor,
            )
            assert len(backfill["events"]) == 3
            assert backfill["events"][-1]["cursor"] == event_hwm
            assert backfill["event_high_watermark"] == event_hwm
    finally:
        await engine.dispose()


def test_timeline_watermark_cannot_skip_operation_committed_after_anchor_snapshot(
    tmp_path: Path,
):
    asyncio.run(
        _timeline_watermark_cannot_skip_operation_committed_after_anchor_snapshot(
            tmp_path
        )
    )


async def _timeline_watermark_cannot_skip_operation_committed_after_anchor_snapshot(
    tmp_path: Path,
):
    """Model PostgreSQL READ COMMITTED's per-statement snapshots deterministically.

    The competing command commits immediately after the operation-anchor SELECT
    has returned its rows. A timeline watermark captured after that point would
    acknowledge the new event even though its card is absent from the response.
    """

    database_path = tmp_path / "timeline-watermark-race.db"
    engine, maker = await _database(database_path)

    class OperationAnchorRaceSession(AsyncSession):
        after_operation_snapshot = None
        race_injected = False

        async def scalars(self, statement, *args, **kwargs):
            rows = await super().scalars(statement, *args, **kwargs)
            descriptions = getattr(statement, "column_descriptions", ())
            reads_operation_anchors = any(
                description.get("entity") is WorkspaceOperation
                for description in descriptions
            )
            if (
                reads_operation_anchors
                and not self.race_injected
                and self.after_operation_snapshot is not None
            ):
                self.race_injected = True
                await self.after_operation_snapshot()
            return rows

    # AUTOCOMMIT gives the reader a fresh SQLite snapshot for every statement,
    # matching the visibility that exposes this race under PostgreSQL's default
    # READ COMMITTED isolation. The writer still uses the normal transactional
    # session maker, so the injected command/event is committed atomically.
    race_engine = create_async_engine(
        f"sqlite+aiosqlite:///{database_path}",
        isolation_level="AUTOCOMMIT",
    )
    try:
        presentation = await _create_presentation(maker)
        async with maker() as session:
            await bootstrap_workspace(
                session,
                presentation_id=presentation.id,
                request=_bootstrap_request(),
            )

        committed_operation_id = None

        async def commit_competing_command():
            nonlocal committed_operation_id
            async with maker() as writer:
                operation, duplicate = await create_workspace_command(
                    writer,
                    presentation_id=presentation.id,
                    request=WorkspaceCommandRequest(
                        command="Commit between anchor snapshot and response",
                        idempotency_key="timeline-read-committed-race",
                        expected_revision=0,
                    ),
                )
            assert duplicate is False
            committed_operation_id = operation.id

        async with OperationAnchorRaceSession(
            bind=race_engine,
            expire_on_commit=False,
        ) as timeline_session:
            timeline_session.after_operation_snapshot = commit_competing_command
            timeline = await workspace_timeline(
                timeline_session,
                presentation_id=presentation.id,
            )

        assert committed_operation_id is not None
        projected_operation_ids = {
            item["operation_id"]
            for item in timeline["items"]
            if item["anchor_type"] == "operation"
        }
        assert str(committed_operation_id) not in projected_operation_ids

        async with maker() as session:
            backfill = await workspace_events(
                session,
                presentation_id=presentation.id,
                after=timeline["event_high_watermark"],
            )
        assert [event["operation_id"] for event in backfill["events"]] == [
            str(committed_operation_id),
            str(committed_operation_id),
        ]
        assert [event["type"] for event in backfill["events"]] == [
            "message.created",
            "queued",
        ]
        assert int(backfill["event_high_watermark"]) > int(
            timeline["event_high_watermark"]
        )
    finally:
        await race_engine.dispose()
        await engine.dispose()


def test_timeline_anchor_snapshot_never_splits_atomic_message_operation_pair(
    tmp_path: Path,
):
    asyncio.run(
        _timeline_anchor_snapshot_never_splits_atomic_message_operation_pair(tmp_path)
    )


async def _timeline_anchor_snapshot_never_splits_atomic_message_operation_pair(
    tmp_path: Path,
):
    database_path = tmp_path / "timeline-anchor-snapshot-race.db"
    engine, maker = await _database(database_path)

    class MessageReadRaceSession(AsyncSession):
        after_message_read = None
        race_injected = False

        async def scalars(self, statement, *args, **kwargs):
            rows = await super().scalars(statement, *args, **kwargs)
            descriptions = getattr(statement, "column_descriptions", ())
            reads_messages = any(
                description.get("entity") is WorkspaceMessage
                for description in descriptions
            )
            if (
                reads_messages
                and not self.race_injected
                and self.after_message_read is not None
            ):
                self.race_injected = True
                await self.after_message_read()
            return rows

    race_engine = create_async_engine(
        f"sqlite+aiosqlite:///{database_path}",
        isolation_level="AUTOCOMMIT",
    )
    try:
        presentation = await _create_presentation(maker)
        async with maker() as session:
            await bootstrap_workspace(
                session,
                presentation_id=presentation.id,
                request=_bootstrap_request(),
            )

        committed_message_id = None
        committed_operation_id = None

        async def commit_pair_after_message_read():
            nonlocal committed_message_id, committed_operation_id
            async with maker() as writer:
                operation, duplicate = await create_workspace_command(
                    writer,
                    presentation_id=presentation.id,
                    request=WorkspaceCommandRequest(
                        command="Keep this message and operation together",
                        idempotency_key="timeline-anchor-pair-race",
                        expected_revision=0,
                    ),
                )
            assert duplicate is False
            committed_operation_id = operation.id
            committed_message_id = uuid.UUID(operation.request_payload["user_message_id"])

        async with MessageReadRaceSession(
            bind=race_engine,
            expire_on_commit=False,
        ) as timeline_session:
            timeline_session.after_message_read = commit_pair_after_message_read
            timeline = await workspace_timeline(
                timeline_session,
                presentation_id=presentation.id,
            )

        assert committed_message_id is not None
        assert committed_operation_id is not None
        projected_message_ids = {
            item["id"]
            for item in timeline["items"]
            if item["anchor_type"] == "message"
        }
        projected_operation_ids = {
            item["operation_id"]
            for item in timeline["items"]
            if item["anchor_type"] == "operation"
        }
        message_projected = str(committed_message_id) in projected_message_ids
        operation_projected = str(committed_operation_id) in projected_operation_ids
        assert message_projected is operation_projected
        assert message_projected is False
    finally:
        await race_engine.dispose()
        await engine.dispose()


def test_empty_workspace_validates_and_canonicalizes_event_cursor(tmp_path: Path):
    asyncio.run(_empty_workspace_validates_and_canonicalizes_event_cursor(tmp_path))


async def _empty_workspace_validates_and_canonicalizes_event_cursor(tmp_path: Path):
    engine, maker = await _database(tmp_path / "empty-event-cursor.db")
    try:
        presentation = await _create_presentation(maker)
        async with maker() as session:
            page = await workspace_events(
                session,
                presentation_id=presentation.id,
            )
            assert page == {
                "schema_version": 1,
                "events": [],
                "event_high_watermark": "0",
            }

        async with maker() as session:
            with pytest.raises(WorkspaceServiceError) as invalid:
                await workspace_events(
                    session,
                    presentation_id=presentation.id,
                    after="not-a-cursor",
                )
        assert invalid.value.code == "invalid_event_cursor"
        assert invalid.value.status_code == 400

        for malformed in ("01", "cursor:1", "+1", "１"):
            async with maker() as session:
                with pytest.raises(WorkspaceServiceError) as invalid:
                    await workspace_events(
                        session,
                        presentation_id=presentation.id,
                        after=malformed,
                    )
            assert invalid.value.code == "invalid_event_cursor"
            assert invalid.value.status_code == 400

        async with maker() as session:
            with pytest.raises(WorkspaceServiceError) as ahead:
                await workspace_events(
                    session,
                    presentation_id=presentation.id,
                    after="1",
                )
        assert ahead.value.code == "event_cursor_expired"
        assert ahead.value.status_code == 410
        assert ahead.value.retryable is True
    finally:
        await engine.dispose()


def test_event_cursor_expiry_is_conversation_scoped_and_initial_poll_is_allowed(
    tmp_path: Path,
):
    asyncio.run(_event_cursor_expiry_is_conversation_scoped(tmp_path))


async def _event_cursor_expiry_is_conversation_scoped(tmp_path: Path):
    engine, maker = await _database(tmp_path / "event-cursor-expiry.db")
    try:
        presentation_a = await _create_presentation(maker)
        presentation_b = await _create_presentation(maker)
        async with maker() as session:
            conversation_a, _message, _operation, _duplicate = (
                await bootstrap_workspace(
                    session,
                    presentation_id=presentation_a.id,
                    request=_bootstrap_request(key="cursor-floor-a"),
                )
            )
            await bootstrap_workspace(
                session,
                presentation_id=presentation_b.id,
                request=_bootstrap_request(key="cursor-floor-b"),
            )
            page_a = await workspace_events(
                session,
                presentation_id=presentation_a.id,
            )
            floor_a = int(page_a["event_high_watermark"])
            conversation_a.event_retention_floor_cursor = floor_a
            session.add(conversation_a)
            await session.commit()

        async with maker() as session:
            with pytest.raises(WorkspaceServiceError) as expired:
                await workspace_events(
                    session,
                    presentation_id=presentation_a.id,
                    after="0",
                )
            assert expired.value.code == "event_cursor_expired"
            assert expired.value.status_code == 410
            assert expired.value.retryable is True

            initial = await workspace_events(
                session,
                presentation_id=presentation_a.id,
            )
            assert initial["events"]
            assert int(initial["event_high_watermark"]) >= floor_a
            with pytest.raises(WorkspaceServiceError) as ahead:
                await workspace_events(
                    session,
                    presentation_id=presentation_a.id,
                    after=str(int(initial["event_high_watermark"]) + 1),
                )
            assert ahead.value.code == "event_cursor_expired"
            assert ahead.value.status_code == 410
            assert ahead.value.retryable is True

            rebased_timeline = await workspace_timeline(
                session,
                presentation_id=presentation_a.id,
            )
            assert int(rebased_timeline["event_high_watermark"]) >= floor_a

            # Conversation B's first cursor is above zero only because A consumed
            # the global sequence. Its own floor remains zero, so this is valid.
            other_conversation = await workspace_events(
                session,
                presentation_id=presentation_b.id,
                after="0",
            )
            assert other_conversation["events"]
    finally:
        await engine.dispose()


def test_destructive_command_requires_fresh_revision_bound_approval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("WORKSPACE_DESTRUCTIVE_APPROVAL_TTL_SECONDS", "3600")
    asyncio.run(_destructive_command_requires_fresh_revision_bound_approval(tmp_path))


async def _destructive_command_requires_fresh_revision_bound_approval(tmp_path: Path):
    engine, maker = await _database(tmp_path / "destructive-approval.db")
    try:
        presentation = await _create_presentation(maker)
        async with maker() as session:
            operation, duplicate = await create_workspace_command(
                session,
                presentation_id=presentation.id,
                request=WorkspaceCommandRequest(
                    command="Delete slide 1",
                    command_type="slide.delete",
                    idempotency_key="delete-slide-1",
                    expected_revision=0,
                    context={"slide_index": 0},
                ),
            )
            assert duplicate is False
            assert operation.state == "awaiting_approval"
            assert operation.approval_expires_at is not None
            assert operation.next_attempt_at is None

        async with maker() as session:
            timeline = await workspace_timeline(
                session,
                presentation_id=presentation.id,
            )
            card = timeline["items"][-1]
            assert card["status"] == "awaiting_approval"
            assert card["approval_expires_at"] is not None
            assert [event["type"] for event in card["events"][:2]] == [
                "message.created",
                "approval_required",
            ]

        async with maker() as session:
            _, approved, duplicate = await apply_workspace_action(
                session,
                operation_id=operation.id,
                request=WorkspaceOperationActionRequest(
                    action="approve",
                    idempotency_key="approve-delete-slide-1",
                    expected_operation_version=0,
                    expected_revision=0,
                ),
            )
            assert duplicate is False
            assert approved.state == "queued"
            assert approved.version == 1

        async with maker() as session:
            stale_operation, _ = await create_workspace_command(
                session,
                presentation_id=presentation.id,
                request=WorkspaceCommandRequest(
                    command="Delete slide 2",
                    command_type="slide.delete",
                    idempotency_key="delete-slide-2",
                    expected_revision=0,
                    context={"slide_index": 1},
                ),
            )
            stored_presentation = await session.get(PresentationModel, presentation.id)
            assert stored_presentation is not None
            stored_presentation.revision = 1
            await session.commit()

        # Approval is bound to the revision captured by the operation even when
        # an old client omits its optional expected_revision field.
        async with maker() as session:
            with pytest.raises(WorkspaceServiceError) as stale:
                await apply_workspace_action(
                    session,
                    operation_id=stale_operation.id,
                    request=WorkspaceOperationActionRequest(
                        action="approve",
                        idempotency_key="approve-stale-delete",
                        expected_operation_version=0,
                    ),
                )
        assert stale.value.code == "revision_conflict"

        async with maker() as session:
            expired_operation, _ = await create_workspace_command(
                session,
                presentation_id=presentation.id,
                request=WorkspaceCommandRequest(
                    command="Delete slide 3",
                    command_type="slide.delete",
                    idempotency_key="delete-slide-3",
                    expected_revision=1,
                    context={"slide_index": 2},
                ),
            )
            expired_operation.approval_expires_at = (
                get_current_utc_datetime() - timedelta(seconds=1)
            )
            await session.commit()

        async with maker() as session:
            with pytest.raises(WorkspaceServiceError) as expired:
                await apply_workspace_action(
                    session,
                    operation_id=expired_operation.id,
                    request=WorkspaceOperationActionRequest(
                        action="approve",
                        idempotency_key="approve-expired-delete",
                        expected_operation_version=0,
                        expected_revision=1,
                    ),
                )
        assert expired.value.code == "approval_expired"
        assert expired.value.status_code == 409
    finally:
        await engine.dispose()


def test_version_restore_command_and_approval_are_durably_idempotent(
    tmp_path: Path,
):
    asyncio.run(_version_restore_command_and_approval_are_durably_idempotent(tmp_path))


async def _version_restore_command_and_approval_are_durably_idempotent(
    tmp_path: Path,
):
    engine, maker = await _database(tmp_path / "version-restore-admission.db")
    try:
        presentation = await _create_presentation(maker)
        command = WorkspaceCommandRequest(
            command="어제 저장한 버전으로 복원해 줘",
            command_type="version.restore",
            idempotency_key="restore-command-once",
            expected_revision=0,
            context={
                "version_id": "11111111-1111-1111-1111-111111111111",
                "source": "version_history",
            },
        )
        async with maker() as session:
            operation, duplicate = await create_workspace_command(
                session,
                presentation_id=presentation.id,
                request=command,
            )
        assert duplicate is False
        assert operation.kind == "version.restore"
        assert operation.state == "awaiting_approval"
        assert operation.expected_revision == 0

        async with maker() as session:
            replayed, duplicate = await create_workspace_command(
                session,
                presentation_id=presentation.id,
                request=command,
            )
        assert duplicate is True
        assert replayed.id == operation.id

        approval = WorkspaceOperationActionRequest(
            action="approve",
            idempotency_key="restore-approval-once",
            expected_operation_version=0,
            expected_revision=0,
        )
        async with maker() as session:
            receipt, approved, duplicate = await apply_workspace_action(
                session,
                operation_id=operation.id,
                request=approval,
            )
        assert duplicate is False
        assert approved.state == "queued"
        assert approved.version == 1

        async with maker() as session:
            replay_receipt, replay_operation, duplicate = await apply_workspace_action(
                session,
                operation_id=operation.id,
                request=approval,
            )
        assert duplicate is True
        assert replay_receipt.id == receipt.id
        assert replay_operation.id == operation.id
        assert replay_operation.state == "queued"

        async with maker() as session:
            assert await session.scalar(
                select(func.count()).select_from(WorkspaceMessage)
            ) == 1
            assert await session.scalar(
                select(func.count()).select_from(WorkspaceOperation)
            ) == 1
            assert await session.scalar(
                select(func.count()).select_from(WorkspaceActionReceipt)
            ) == 1
            assert await session.scalar(
                select(func.count()).select_from(WorkspaceOperationEvent)
            ) == 3
            assert await session.scalar(
                select(func.count()).select_from(WorkspaceOutbox)
            ) == 3

        # A later approval is still bound to the revision captured by the card,
        # even if that client omits its own optional expected_revision.
        stale_command = WorkspaceCommandRequest(
            command="다른 저장 버전으로 복원해 줘",
            command_type="version.restore",
            idempotency_key="restore-command-stale",
            expected_revision=0,
            context={
                "version_id": "22222222-2222-2222-2222-222222222222",
                "source": "version_history",
            },
        )
        async with maker() as session:
            stale_operation, _ = await create_workspace_command(
                session,
                presentation_id=presentation.id,
                request=stale_command,
            )
            stored_presentation = await session.get(
                PresentationModel,
                presentation.id,
            )
            stored_presentation.revision = 1
            await session.commit()

        async with maker() as session:
            with pytest.raises(WorkspaceServiceError) as stale:
                await apply_workspace_action(
                    session,
                    operation_id=stale_operation.id,
                    request=WorkspaceOperationActionRequest(
                        action="approve",
                        idempotency_key="restore-stale-approval",
                        expected_operation_version=0,
                    ),
                )
        assert stale.value.code == "revision_conflict"
        async with maker() as session:
            stored = await session.get(WorkspaceOperation, stale_operation.id)
            assert stored.state == "awaiting_approval"
            assert await session.scalar(
                select(func.count()).select_from(WorkspaceActionReceipt)
            ) == 1
    finally:
        await engine.dispose()


def test_action_receipt_is_idempotent_and_stale_operation_version_conflicts(tmp_path: Path):
    asyncio.run(_action_receipt_is_idempotent_and_stale_operation_version_conflicts(tmp_path))


async def _action_receipt_is_idempotent_and_stale_operation_version_conflicts(
    tmp_path: Path,
):
    engine, maker = await _database(tmp_path / "action-receipts.db")
    try:
        presentation = await _create_presentation(maker)
        async with maker() as session:
            _, _, operation, _ = await bootstrap_workspace(
                session,
                presentation_id=presentation.id,
                request=_bootstrap_request(),
            )

        cancel = WorkspaceOperationActionRequest(
            action="cancel",
            idempotency_key="cancel-once",
            action_id="cancel-click-1",
            expected_operation_version=0,
            expected_revision=0,
        )
        async with maker() as session:
            first_receipt, first_operation, first_duplicate = await apply_workspace_action(
                session,
                operation_id=operation.id,
                request=cancel,
            )
            assert first_duplicate is False
            assert first_operation.state == "cancelled"
            assert first_operation.version == 1

        async with maker() as session:
            second_receipt, second_operation, second_duplicate = await apply_workspace_action(
                session,
                operation_id=operation.id,
                request=cancel,
            )
            assert second_duplicate is True
            assert second_receipt.id == first_receipt.id
            assert second_operation.version == 1

        async with maker() as session:
            with pytest.raises(WorkspaceServiceError) as stale:
                await apply_workspace_action(
                    session,
                    operation_id=operation.id,
                    request=WorkspaceOperationActionRequest(
                        action="retry",
                        idempotency_key="stale-retry",
                        expected_operation_version=0,
                        expected_revision=0,
                    ),
                )
        assert stale.value.code == "operation_state_conflict"
        assert stale.value.status_code == 409

        async with maker() as session:
            assert await session.scalar(
                select(func.count()).select_from(WorkspaceActionReceipt)
            ) == 1
            events = list(
                (
                    await session.scalars(
                        select(WorkspaceOperationEvent)
                        .where(WorkspaceOperationEvent.operation_id == operation.id)
                        .order_by(WorkspaceOperationEvent.sequence)
                    )
                ).all()
            )
            assert [event.operation_version for event in events] == [0, 0, 1]
    finally:
        await engine.dispose()


def test_action_idempotency_is_scoped_by_owner_operation_and_action(tmp_path: Path):
    asyncio.run(_action_idempotency_scope(tmp_path))


async def _action_idempotency_scope(tmp_path: Path):
    engine, maker = await _database(tmp_path / "action-idempotency-scope.db")
    try:
        first_presentation = await _create_presentation(maker)
        second_presentation = await _create_presentation(maker)
        async with maker() as session:
            _, _, first_operation, _ = await bootstrap_workspace(
                session,
                presentation_id=first_presentation.id,
                request=_bootstrap_request(key="action-scope-first"),
                initial_operation_state="awaiting_approval",
            )
        async with maker() as session:
            _, _, second_operation, _ = await bootstrap_workspace(
                session,
                presentation_id=second_presentation.id,
                request=_bootstrap_request(key="action-scope-second"),
            )

        shared_key = "same-action-raw-key"
        approve = WorkspaceOperationActionRequest(
            action="approve",
            idempotency_key=shared_key,
            expected_operation_version=0,
            expected_revision=0,
        )
        cancel_first = WorkspaceOperationActionRequest(
            action="cancel",
            idempotency_key=shared_key,
            expected_operation_version=1,
            expected_revision=0,
        )
        cancel_second = WorkspaceOperationActionRequest(
            action="cancel",
            idempotency_key=shared_key,
            expected_operation_version=0,
            expected_revision=0,
        )

        async with maker() as session:
            approve_receipt, approved, approve_duplicate = await apply_workspace_action(
                session,
                operation_id=first_operation.id,
                request=approve,
            )
        assert approve_duplicate is False
        assert approved.version == 1

        # The same raw key is independent for a different action on the same
        # operation, even though the default action_id is also derived from it.
        async with maker() as session:
            cancel_receipt, cancelled, cancel_duplicate = await apply_workspace_action(
                session,
                operation_id=first_operation.id,
                request=cancel_first,
            )
        assert cancel_duplicate is False
        assert cancelled.version == 2
        assert cancel_receipt.id != approve_receipt.id

        # The same action/key is also independent on another operation.
        async with maker() as session:
            second_receipt, second_cancelled, second_duplicate = await apply_workspace_action(
                session,
                operation_id=second_operation.id,
                request=cancel_second,
            )
        assert second_duplicate is False
        assert second_cancelled.version == 1
        assert second_receipt.id != cancel_receipt.id

        async with maker() as session:
            replay_receipt, replay_operation, replay_duplicate = await apply_workspace_action(
                session,
                operation_id=second_operation.id,
                request=cancel_second,
            )
        assert replay_duplicate is True
        assert replay_receipt.id == second_receipt.id
        assert replay_operation.id == second_operation.id

        async with maker() as session:
            receipts = list((await session.scalars(select(WorkspaceActionReceipt))).all())
            assert len(receipts) == 3
            assert {
                (receipt.operation_id, receipt.action_type, receipt.idempotency_key_hash)
                for receipt in receipts
            }.__len__() == 3
    finally:
        await engine.dispose()


def test_partial_svg_deadline_retry_enters_verified_append_only_fork(monkeypatch):
    from copy import deepcopy
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from services import workspace_service as service, svg_checkpoint_recovery

    class ForkReached(Exception):
        pass

    async def exercise(kind, recovery_revision, *, fork_expected):
        source = WorkspaceOperation(
            owner_id=uuid.uuid4(), owner_scope="deadline-retry-fixture",
            conversation_id=uuid.uuid4(), presentation_id=uuid.uuid4(),
            kind=kind, state="deadline_exceeded", version=4,
            lease_expires_at=None, request_payload={"immutable": "source"},
            error_detail={"retryable": False},
        )
        before = deepcopy(source.model_dump(mode="json"))
        presentation = SimpleNamespace(id=source.presentation_id, revision=0)
        recovery = SimpleNamespace(manifest={"append_only_recovery": True})
        verification = AsyncMock(return_value=recovery)

        class Session:
            def __init__(self):
                self.reads = 0
                self.rollback = AsyncMock()

            async def execute(self, statement):
                if statement.is_update:
                    return SimpleNamespace(scalar_one_or_none=lambda: None)
                self.reads += 1
                return SimpleNamespace(scalar_one_or_none=lambda: source if self.reads == 1 else None)

        session = Session()

        async def fork(current_session, current_source, current_presentation, proven_recovery):
            assert current_session is session and current_source is source
            assert current_presentation is presentation and proven_recovery is recovery
            verification.assert_awaited_once()
            raise ForkReached()

        fork_spy = AsyncMock(side_effect=fork)
        monkeypatch.setattr(service, "_existing_action_receipt", AsyncMock(return_value=None))
        monkeypatch.setattr(service, "_lock_workspace_mutation_admission",
                            AsyncMock(return_value=(presentation, 0)))
        monkeypatch.setattr(service, "_verified_export_retry_v2_authority", AsyncMock(return_value=None))
        monkeypatch.setattr(service, "_verified_authored_local_fit_checkpoint_recovery", AsyncMock(return_value=None))
        monkeypatch.setattr(service, "_is_explicit_authored_capacity_recovery", lambda *args: False)
        monkeypatch.setattr(service, "_is_retryable_raster_tenant_handoff", lambda *args: False)
        monkeypatch.setattr(service, "get_workspace_owner_scope", lambda: source.owner_scope)
        monkeypatch.setattr(service, "_fork_append_only_svg_recovery_workspace", fork_spy)
        monkeypatch.setattr(svg_checkpoint_recovery, "verified_svg_recovery_checkpoints", verification)
        monkeypatch.setattr(service, "_replay_action_receipt_after_conflict",
                            AsyncMock(side_effect=WorkspaceServiceError("operation_state_conflict", status_code=409)))
        request = WorkspaceOperationActionRequest(
            action="retry", idempotency_key="deadline-retry-fixture",
            expected_operation_version=4, expected_revision=0,
            recovery_revision=recovery_revision,
        )
        expected_error = ForkReached if fork_expected else WorkspaceServiceError
        with pytest.raises(expected_error):
            await service._apply_workspace_action(session, operation_id=source.id, request=request)
        if fork_expected:
            fork_spy.assert_awaited_once()
        else:
            fork_spy.assert_not_awaited()
        assert source.model_dump(mode="json") == before

    async def run():
        await exercise("presentation.generate", "svg-partial-recovery/v1", fork_expected=True)
        await exercise("presentation.generate", None, fork_expected=False)
        await exercise("presentation.generate", "svg-cached-author-render/v1", fork_expected=False)
        await exercise("export.pptx", "svg-partial-recovery/v1", fork_expected=False)

    asyncio.run(run())


def test_terminal_retry_creates_one_new_operation_and_preserves_source(tmp_path: Path):
    asyncio.run(_terminal_retry_creates_one_new_operation_and_preserves_source(tmp_path))


def test_terminal_export_retry_creates_admission_before_queueing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    asyncio.run(_terminal_export_retry_creates_admission_before_queueing(tmp_path, monkeypatch))


async def _terminal_export_retry_creates_admission_before_queueing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    engine, maker = await _database(tmp_path / "terminal-export-retry.db")
    admissions: list[dict] = []

    async def admit_export(_session, **kwargs):
        admissions.append(kwargs)
        if len(admissions) == 2:
            raise PublicAdmissionRejected(
                "public_test_export_admission_denied", retry_after_seconds=4
            )
        return object()

    monkeypatch.setattr("services.workspace_service.admit_public_operation", admit_export)
    monkeypatch.setattr("services.workspace_service.current_principal_context", lambda: object())
    try:
        presentation = await _create_presentation(maker)
        async with maker() as session:
            _, _, source, _ = await bootstrap_workspace(
                session, presentation_id=presentation.id,
                request=_bootstrap_request(key="terminal-export-retry"),
            )
            source.kind = "export.pptx"
            source.state = "failed"
            source.error_code = "presentation_export_failed"
            source.error_detail = {"retryable": True}
            source.version = 4
            await session.commit()

        retry_request = WorkspaceOperationActionRequest(
            action="retry", idempotency_key="terminal-export-retry-1",
            expected_operation_version=4, expected_revision=0,
        )
        async with maker() as session:
            _, retry, duplicate = await apply_workspace_action(
                session, operation_id=source.id, request=retry_request,
            )
            assert duplicate is False
            assert retry.kind == "export.pptx"
            assert retry.state == "queued"
            assert len(admissions) == 1
            assert admissions[0]["operation_id"] == retry.id
            assert admissions[0]["operation_kind"] == "export"
            assert admissions[0]["request_plan"] is None
            assert admissions[0]["planned_slide_count"] == presentation.n_slides
            retry.state = "failed"
            retry.error_detail = {"retryable": True}
            await session.commit()

        async with maker() as session:
            with pytest.raises(WorkspaceServiceError) as denied:
                await apply_workspace_action(
                    session, operation_id=retry.id,
                    request=WorkspaceOperationActionRequest(
                        action="retry", idempotency_key="terminal-export-retry-2",
                        expected_operation_version=0, expected_revision=0,
                    ),
                )
            assert denied.value.code == "public_test_export_admission_denied"
            assert denied.value.retryable is True
        async with maker() as session:
            assert await session.scalar(
                select(func.count()).select_from(WorkspaceOperation)
            ) == 2
    finally:
        await engine.dispose()


async def _terminal_retry_creates_one_new_operation_and_preserves_source(
    tmp_path: Path,
):
    engine, maker = await _database(tmp_path / "terminal-retry.db")
    try:
        presentation = await _create_presentation(maker)
        async with maker() as session:
            conversation, _, source, _ = await bootstrap_workspace(
                session,
                presentation_id=presentation.id,
                request=_bootstrap_request(key="retry-source"),
            )
            source.state = "failed"
            source.error_code = "operation_dead_lettered"
            source.error_detail = {"safe": "original failure"}
            source.version = 4
            await session.commit()

        retry_request = WorkspaceOperationActionRequest(
            action="retry",
            idempotency_key="retry-source-click-1",
            action_id="retry-click-1",
            expected_operation_version=4,
            expected_revision=0,
        )
        async with maker() as session:
            receipt, retry, duplicate = await apply_workspace_action(
                session,
                operation_id=source.id,
                request=retry_request,
            )
            assert duplicate is False
            assert retry.id != source.id
            assert retry.retry_of_operation_id == source.id
            assert retry.state == "queued"
            assert retry.anchor_position == 3
            assert retry.expected_revision == 0
            assert retry.version == 0
            assert receipt.operation_id == source.id
            assert receipt.result_operation_id == retry.id
            assert receipt.result_payload == {
                "state": "queued",
                "operation_id": str(retry.id),
                "retry_of_operation_id": str(source.id),
            }

        async with maker() as session:
            replay_receipt, replay_operation, replay_duplicate = (
                await apply_workspace_action(
                    session,
                    operation_id=source.id,
                    request=retry_request,
                )
            )
            assert replay_duplicate is True
            assert replay_receipt.id == receipt.id
            assert replay_operation.id == retry.id

            persisted_source = await session.get(WorkspaceOperation, source.id)
            assert persisted_source is not None
            assert persisted_source.state == "failed"
            assert persisted_source.version == 4
            assert persisted_source.error_code == "operation_dead_lettered"
            assert await session.scalar(
                select(func.count()).select_from(WorkspaceOperation)
            ) == 2

            timeline = await workspace_timeline(
                session,
                presentation_id=presentation.id,
            )
            assert [item["position"] for item in timeline["items"]] == [1, 2, 3]
            assert timeline["items"][1]["retryable"] is True
            assert timeline["items"][2]["retry_of_operation_id"] == str(source.id)

        async with maker() as session:
            with pytest.raises(WorkspaceServiceError) as already_retried:
                await apply_workspace_action(
                    session,
                    operation_id=source.id,
                    request=WorkspaceOperationActionRequest(
                        action="retry",
                        idempotency_key="retry-source-click-2",
                        expected_operation_version=4,
                        expected_revision=0,
                    ),
                )
        assert already_retried.value.code == "operation_state_conflict"

        async with maker() as session:
            stored_conversation = await session.get(
                WorkspaceConversation, conversation.id
            )
            assert stored_conversation is not None
            assert stored_conversation.next_position == 4
    finally:
        await engine.dispose()


@pytest.mark.parametrize("state", ["failed", "dead_letter"])
def test_terminal_non_retryable_operation_rejects_retry_and_projects_safe_gate(
    tmp_path: Path,
    state: str,
):
    asyncio.run(
        _terminal_non_retryable_operation_rejects_retry_and_projects_safe_gate(
            tmp_path,
            state,
        )
    )


async def _terminal_non_retryable_operation_rejects_retry_and_projects_safe_gate(
    tmp_path: Path,
    state: str,
):
    engine, maker = await _database(tmp_path / f"non-retryable-{state}.db")
    try:
        presentation = await _create_presentation(maker)
        async with maker() as session:
            _, _, source, _ = await bootstrap_workspace(
                session,
                presentation_id=presentation.id,
                request=_bootstrap_request(key=f"non-retryable-{state}"),
            )
            source.state = state
            source.error_code = "generation_admission_rejected"
            source.error_detail = {
                "retryable": False,
                "private_detail": "must-not-leak",
            }
            source.version = 5
            await session.commit()

        async with maker() as session:
            timeline = await workspace_timeline(
                session,
                presentation_id=presentation.id,
            )
            anchor = next(
                item
                for item in timeline["items"]
                if item.get("operation_id") == str(source.id)
            )
            assert anchor["retryable"] is False
            assert "error_detail" not in anchor
            assert "must-not-leak" not in str(anchor)

        async with maker() as session:
            with pytest.raises(WorkspaceServiceError) as rejected:
                await apply_workspace_action(
                    session,
                    operation_id=source.id,
                    request=WorkspaceOperationActionRequest(
                        action="retry",
                        idempotency_key=f"retry-{state}",
                        expected_operation_version=5,
                        expected_revision=0,
                    ),
                )
        assert rejected.value.code == "operation_not_retryable"
        assert rejected.value.status_code == 409
        assert rejected.value.retryable is False
        assert rejected.value.safe_message == (
            "This operation cannot be retried. Submit a new command instead."
        )

        async with maker() as session:
            assert await session.scalar(
                select(func.count()).select_from(WorkspaceOperation)
            ) == 1
            assert await session.scalar(
                select(func.count()).select_from(WorkspaceActionReceipt)
            ) == 0
    finally:
        await engine.dispose()


def test_cancelled_operation_remains_retryable_with_legacy_error_detail(tmp_path: Path):
    asyncio.run(_cancelled_operation_remains_retryable_with_legacy_error_detail(tmp_path))


async def _cancelled_operation_remains_retryable_with_legacy_error_detail(
    tmp_path: Path,
):
    engine, maker = await _database(tmp_path / "cancelled-retryable.db")
    try:
        presentation = await _create_presentation(maker)
        async with maker() as session:
            _, _, source, _ = await bootstrap_workspace(
                session,
                presentation_id=presentation.id,
                request=_bootstrap_request(key="cancelled-retryable"),
            )
            source.state = "cancelled"
            source.error_detail = {"retryable": False}
            source.version = 2
            await session.commit()

        async with maker() as session:
            timeline = await workspace_timeline(
                session,
                presentation_id=presentation.id,
            )
            anchor = next(
                item
                for item in timeline["items"]
                if item.get("operation_id") == str(source.id)
            )
            assert anchor["retryable"] is True
            _, retry, duplicate = await apply_workspace_action(
                session,
                operation_id=source.id,
                request=WorkspaceOperationActionRequest(
                    action="retry",
                    idempotency_key="retry-cancelled",
                    expected_operation_version=2,
                    expected_revision=0,
                ),
            )
            assert duplicate is False
            assert retry.retry_of_operation_id == source.id
            assert retry.state == "queued"
    finally:
        await engine.dispose()


def test_blocked_retry_resumes_same_operation_after_assets_are_ready(tmp_path: Path):
    asyncio.run(_blocked_retry_resumes_same_operation_after_assets_are_ready(tmp_path))


async def _blocked_retry_resumes_same_operation_after_assets_are_ready(
    tmp_path: Path,
):
    engine, maker = await _database(tmp_path / "blocked-retry.db")
    try:
        presentation = await _create_presentation(maker)
        async with maker() as session:
            _, _, blocked, _ = await bootstrap_workspace(
                session,
                presentation_id=presentation.id,
                request=_bootstrap_request(
                    key="blocked-retry-source",
                    status="failed",
                ),
            )
            asset = (await session.scalars(select(WorkspaceAsset))).one()
            asset.state = "ready"
            await session.commit()

        async with maker() as session:
            _, resumed, duplicate = await apply_workspace_action(
                session,
                operation_id=blocked.id,
                request=WorkspaceOperationActionRequest(
                    action="retry",
                    idempotency_key="blocked-retry-click",
                    expected_operation_version=0,
                    expected_revision=0,
                ),
            )
            assert duplicate is False
            assert resumed.id == blocked.id
            assert resumed.retry_of_operation_id is None
            assert resumed.state == "queued"
            assert resumed.version == 1

        async with maker() as session:
            assert await session.scalar(
                select(func.count()).select_from(WorkspaceOperation)
            ) == 1
    finally:
        await engine.dispose()


async def _svg_partial_append_only_contract(tmp_path, monkeypatch, *, source_routing="uuid"):
    """Real isolated rows: fork, prepare, and fail closed without buying calls."""
    from copy import deepcopy
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from models.generate_presentation_request import GeneratePresentationRequest
    from models.presentation_outline_model import PresentationOutlineModel, SlideOutlineModel
    from models.sql.async_presentation_generation_status import AsyncPresentationGenerationTaskModel
    from models.sql.authored_slide_checkpoint import AuthoredSlideCheckpoint
    from models.sql.presentation_generation_job import PresentationGenerationJob
    from models.sql.public_admission import PublicPolicyProfile, PublicOperationAdmission, PublicProviderLogicalCall
    from models.sql.public_admission import PublicResourceLease
    from models.sql.provider_runtime_reservation import ProviderRuntimeBudget, ProviderRuntimeReservation
    from models.sql.provider_usage_receipt import ProviderUsageReceipt
    from services import svg_partial_recovery as recovery
    from services import svg_common_header_generation as headers
    from services.svg_common_header_design_checkpoint import _envelope
    from services.svg_common_header_v2 import prepare_common_header_v2
    from services.authored_canvas import request_canvas_metadata
    from services.presentation_generation_job_service import request_sha256
    from services.workspace_service import _fork_append_only_svg_recovery_workspace
    from utils.hashing import sha256_hex

    engine, maker = await _database(tmp_path / "svg-partial-append-only.db")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(lambda conn: SQLModel.metadata.create_all(conn, tables=[
                AsyncPresentationGenerationTaskModel.__table__, PresentationGenerationJob.__table__,
                AuthoredSlideCheckpoint.__table__, PublicPolicyProfile.__table__,
                PublicOperationAdmission.__table__, PublicProviderLogicalCall.__table__,
                PublicResourceLease.__table__, ProviderRuntimeBudget.__table__,
                ProviderRuntimeReservation.__table__, ProviderUsageReceipt.__table__]))
        presentation = await _create_presentation(maker)
        async with maker() as session:
            user = User(username="partial-owner", hashed_password="unused")
            session.add(user)
            await session.flush()
            _, _, source, _ = await bootstrap_workspace(session, presentation_id=presentation.id,
                request=_bootstrap_request(key="partial-immutable-source"))
            source.owner_id = user.id
            source.kind, source.state, source.fencing_token = "presentation.generate", "failed", 1
            source.request_payload = {**source.request_payload, "principal_tenant_id": "partial-test"}
            parent_presentation = await session.get(PresentationModel, presentation.id)
            parent_presentation.owner_id = user.id
            request = GeneratePresentationRequest(content="Three source pages", template="authored",
                strategy="svg", common_header_mode="uniform", n_slides=3)
            outline = PresentationOutlineModel(slides=[SlideOutlineModel(content=f"Source {i}",
                title=f"Title {i}", page_id=f"source-page-{i}") for i in range(3)])
            candidate = dict(candidate_id="source-header", revision=1,
                svg='<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720"><text id="heading" fill="#111111"/></svg>',
                body_box=dict(x=64, y=150, width=1152, height=520), fields=[dict(node_id="heading",
                    role="title", value_ref="title", box=dict(x=64,y=25,width=1152,height=70),
                    font_family="Noto Sans KR",font_weight=700,font_size_px=32,line_height_px=45,
                    baseline_offset_px=38,max_lines=2,alignment="left",optional=False)])
            contract, bindings = prepare_common_header_v2(candidate, outline,
                request_canvas_metadata(request), snapshot={})
            for slide, binding in zip(outline.slides, bindings, strict=True):
                slide.common_header = binding
            selection = dict(plan_digest="fixture-plan", selected_candidate_id="source-header",
                selected_revision=1, selection_input_digest="fixture-selection",representative_page_id="source-page-0")
            style = dict(common_header=contract, common_header_selection=selection,
                common_header_design_plan=dict(schema="svg-common-header-design-plan/v2", plan_digest="fixture-plan"))
            style["digest"] = recovery.digest(style)
            task = AsyncPresentationGenerationTaskModel(status="failed", owner_id=user.id)
            session.add(task)
            await session.flush()
            payload = dict(presentation_id=str(presentation.id), strategy="svg", common_header_mode="uniform",
                instructions="Keep every source condition", content="Three source pages")
            if source_routing == "null":
                payload["presentation_id"] = None
            elif source_routing == "missing":
                payload.pop("presentation_id")
            parent = PresentationGenerationJob(task_id=task.id,presentation_id=presentation.id,
                workspace_operation_id=source.id,idempotency_key_hash="1"*64,
                request_sha256=request_sha256(payload),request_payload=payload,
                svg_style_snapshot=style,authored_outline=outline.model_dump(mode="json"),
                state="failed",total_slides=3)
            envelope = _envelope(parent, None)
            envelope["document"] = dict(status="published", selection=selection,
                selection_input_digest="fixture-selection")
            envelope["published_inputs"] = dict(snapshot_digest=style["digest"],
                outline_digest=headers._digest(parent.authored_outline))
            parent.svg_header_design_checkpoint = envelope
            session.add(parent)
            await session.flush()
            source.request_payload = {**source.request_payload, "generation_request": payload,
                "job_id": str(parent.id), "task_id": task.id}
            policy_values = {c.name: 100 for c in PublicPolicyProfile.__table__.columns
                if not c.nullable and c.default is None and c.server_default is None}
            policy = PublicPolicyProfile(**{**policy_values, "version": "partial-scope-test"})
            session.add(policy)
            await session.flush()
            admission = PublicOperationAdmission(operation_id=source.id, policy_version=policy.version,
                owner_id=user.id, tenant_id="partial-test", trace_id_hash="a"*64,
                operation_kind="generation", resource_units=1, policy_digest="b"*64,
                deadline_at=get_current_utc_datetime(), state="failed", planned_request_limit=3,
                retry_request_limit=0, estimated_cost_limit_microunits=100,
                actual_cost_limit_microunits=100)
            session.add(admission)
            await session.flush()
            unknown_call = PublicProviderLogicalCall(admission_id=admission.id, stage="author",
                slide_index=1, logical_call_ordinal=1, retry_class="initial",
                idempotency_key_hash="c"*64, estimated_cost_microunits=100,
                state="reserved", outcome="unknown")
            session.add(unknown_call)
            # The confirmed page is stored in the current compact form: its
            # measured metrics live in the content-addressed sidecar and the
            # checkpoint row carries only the integrity-bound reference.
            from services import svg_presentation_checkpoint as compact_store
            monkeypatch.setattr(compact_store, "checkpoint_root", lambda: tmp_path / "checkpoints")
            compact_metrics = {"elements": [{"id": "body", "bbox": [64, 120, 1152, 480]}], "fonts": ["Pretendard"]}
            compact_quality = dict(passed=True, browser_metrics=deepcopy(compact_metrics))
            assert compact_store.compact_browser_metrics(compact_quality) == compact_metrics
            assert "browser_metrics" not in compact_quality and "browser_metrics_ref" in compact_quality
            for index in range(3):
                saved = dict(input_digest=f"source-input-{index}", inflight=None, attempts=[])
                if index == 0:
                    saved.update(svg_content="<svg>exact confirmed source</svg>",quality=deepcopy(compact_quality))
                elif index == 1:
                    saved["inflight"] = dict(status="unknown")
                session.add(AuthoredSlideCheckpoint(job_id=parent.id, slide_index=index,
                    author_status="completed" if index == 0 else "pending",svg_checkpoint=saved))
            await session.commit()
            parent_id, source_id = parent.id, source.id
            admission_id, unknown_call_id = admission.id, unknown_call.id

        async with maker() as session:
            parent = await session.get(PresentationGenerationJob, parent_id)
            source = await session.get(WorkspaceOperation, source_id)
            task = await session.get(AsyncPresentationGenerationTaskModel, parent.task_id)
            rows = list(await session.scalars(select(AuthoredSlideCheckpoint).where(
                AuthoredSlideCheckpoint.job_id == parent.id).order_by(AuthoredSlideCheckpoint.slide_index)))
            completed_record = dict(schema="svg-provider-terminal/v1",terminal_event="response.completed",
                operation_id=str(source.id),job_id=str(parent.id),owner_id=str(source.owner_id),tenant_id="partial-test",
                input_digest="ordinal-input",ordinal=1,raw_response="original author",usage={},
                output_digest=sha256_hex(b"original author"))
            completed_record["receipt_digest"] = recovery.digest(completed_record)
            ordinal_payload = dict(input_digest="ordinal-input",inflight=None,
                provider_terminal_responses={"1":completed_record})
            assert recovery._completed_response(ordinal_payload,parent,source,source.owner_id,"partial-test") == completed_record
            ordinal_payload["inflight"] = 2
            assert recovery._completed_response(ordinal_payload,parent,source,source.owner_id,"partial-test") is None
            ordinal_payload["provider_terminal_responses"]["2"] = dict(ordinal=2,terminal_event="response.created")
            ordinal_payload["inflight"] = None
            assert recovery._completed_response(ordinal_payload,parent,source,source.owner_id,"partial-test") is None
            next_record = {**completed_record,"ordinal":2,"raw_response":"completed repair",
                "output_digest":sha256_hex(b"completed repair")}
            next_record["receipt_digest"] = recovery.digest({k:v for k,v in next_record.items() if k!="receipt_digest"})
            ordinal_payload["provider_terminal_responses"]["2"] = next_record
            assert recovery._completed_response(ordinal_payload,parent,source,source.owner_id,"partial-test") == next_record
            from services.svg_common_header_design_checkpoint import _candidate_key
            from utils.hashing import canonical_json_digest
            candidate_identity = dict(candidate_revision="1",candidate_id="source-header",page_id="source-page-0")
            candidate_style = {k:v for k,v in parent.svg_style_snapshot.items() if k not in {"digest","common_header_selection"}}
            candidate_digest = canonical_json_digest(candidate_style)
            candidate_receipt = dict(style_digest=candidate_digest)
            representative = dict(common_header_candidate_lineage=candidate_identity,
                common_header_candidate_style_digest=candidate_digest,
                attempts=[dict(kind="provider",raw_response="exact candidate body",receipt=candidate_receipt)])
            proof_job = PresentationGenerationJob(**deepcopy(parent.model_dump()))
            proof_job.svg_header_design_checkpoint["candidate_pages"][_candidate_key(0,candidate_identity)] = {
                **deepcopy(representative),"header_author_attempt_identity":deepcopy(candidate_identity)}
            assert recovery._author_style_matches(representative,candidate_receipt,proof_job,0)
            copied_job = PresentationGenerationJob(**deepcopy(proof_job.model_dump()))
            copied_job.id = uuid.uuid4()
            copied_job.svg_header_design_checkpoint = None
            assert not recovery._author_style_matches(representative,candidate_receipt,copied_job,0)
            assert recovery._author_style_matches(representative,candidate_receipt,copied_job,0,header_producer=proof_job)
            assert not recovery._author_style_matches(representative,candidate_receipt,proof_job,1)
            for field in ("common_header_candidate_lineage","common_header_candidate_style_digest","attempts"):
                altered = deepcopy(representative)
                altered[field] = {} if field == "common_header_candidate_lineage" else [] if field == "attempts" else "different"
                assert not recovery._author_style_matches(altered,candidate_receipt,proof_job,0)
            seal = dict(tenant_id="partial-test",payload_sha256=recovery.digest(source.request_payload),
                archive_storage_sha256=None,archive_schema=None,archive_canonical_sha256=None)
            archive = dict(schema=recovery.ARCHIVE_V2, job=parent.model_dump(mode="json"),
                task=task.model_dump(mode="json"), checkpoints=[row.model_dump(mode="json") for row in rows],
                admissions=[(await session.get(PublicOperationAdmission, admission_id)).model_dump(mode="json")],
                logical_calls=[(await session.get(PublicProviderLogicalCall, unknown_call_id)).model_dump(mode="json")],
                operation_refs=[recovery._operation_reference(source,seal,tenant="partial-test")])
            pages = [dict(slide_index=row.slide_index,category=category,row_digest=recovery.digest(row.model_dump(mode="json")),
                svg_sha256=sha256_hex((row.svg_checkpoint.get("svg_content") or "").encode()),
                input_digest=row.svg_checkpoint["input_digest"],author_receipt_digest=recovery.author_witness(row.svg_checkpoint))
                for row,category in zip(rows,["known","unknown","unstarted"],strict=True)]
            manifest = dict(recovery_revision=recovery.REVISION,append_only_recovery=True,
                operation_id=str(source.id),source_operation_id=str(source.id),owner_id=str(source.owner_id),
                tenant_id="partial-test",presentation_id=str(source.presentation_id),job_id=str(parent.id),
                source_request_routing=recovery._source_request_routing(parent.request_payload),
                archive_schema=recovery.ARCHIVE_V2,archive_digest=recovery.digest(archive),expected_page_count=3,
                known_indices=[0],unknown_indices=[1],new_indices=[2],pages=pages,
                frozen_request_digest=recovery.digest(recovery._frozen_payload(parent.request_payload)),
                frozen_style_digest=recovery.digest(parent.svg_style_snapshot),code_digest=recovery._code_digest())
            plan = recovery.PartialRecovery(manifest,archive,rows,recovery.digest(manifest))
            child_presentation, child_conversation = await _fork_append_only_svg_recovery_workspace(
                session, source, await session.get(PresentationModel,source.presentation_id), plan)
            child = WorkspaceOperation(owner_id=source.owner_id,owner_scope=source.owner_scope,
                presentation_id=child_presentation.id,conversation_id=child_conversation.id,
                retry_of_operation_id=source.id,anchor_position=1,kind="presentation.generate",state="queued",
                request_payload=dict(recovery_revision=recovery.REVISION,principal_tenant_id="partial-test"),
                request_hash="2"*64,idempotency_key_hash="3"*64,expected_revision=0)
            session.add(child)
            await session.flush()
            consumer = await recovery.rebind_episode(session,source,child,plan,key_hash="4"*64)
            await session.commit()
            consumer_id, child_id = consumer.id, child.id

        with monkeypatch.context() as scoped:
            async def storage_seals(session, ids, **kwargs):
                # Only the PostgreSQL JSON storage-hash adapter is substituted;
                # producer rows, operation controls and all comparisons are real.
                seals = {}
                for operation_id in ids:
                    original_payload = await session.scalar(select(WorkspaceOperation.request_payload).where(
                        WorkspaceOperation.id == operation_id))
                    original_archive = original_payload.get(recovery.ARCHIVE_KEY)
                    seals[str(operation_id)] = dict(tenant_id="partial-test",
                        payload_sha256=recovery.digest(original_payload),
                        archive_storage_sha256=recovery.digest(original_archive) if original_archive else None,
                        archive_schema=original_archive.get("schema") if original_archive else None,
                        archive_canonical_sha256=recovery.digest(original_archive) if original_archive else None)
                return seals
            scoped.setattr(recovery,"_fresh_payload_storage_seals",storage_seals)
            scoped.setattr(headers,"preflight_header_selector",lambda: {})
            async with maker() as session:
                consumer = await session.get(PresentationGenerationJob,consumer_id)
                child = await session.get(WorkspaceOperation,child_id)
                assert consumer.id != parent_id and consumer.presentation_id != presentation.id
                assert consumer.idempotency_key_hash == "4"*64 and consumer.svg_header_design_checkpoint is None
                assert await recovery.verify_episode(session,child,consumer) == manifest
                assert recovery.frozen_episode_payload(consumer.request_payload,manifest) == archive["job"]["request_payload"]
                copied = list(await session.scalars(select(AuthoredSlideCheckpoint).where(
                    AuthoredSlideCheckpoint.job_id == consumer_id).order_by(AuthoredSlideCheckpoint.slide_index)))
                assert copied[0].svg_checkpoint["svg_content"] == rows[0].svg_checkpoint["svg_content"]
                assert copied[0].author_status == "completed"
                assert recovery.verify_cached_page(copied[0].svg_checkpoint,manifest,0,"source-input-0") == pages[0]
                # The authoritative verifier proved the producer row in its stored
                # compact form (the manifest binds that row's digest); the row keeps
                # the exact reference and its metrics rehydrate losslessly.
                producer_row = await session.scalar(select(AuthoredSlideCheckpoint).where(
                    AuthoredSlideCheckpoint.job_id == parent_id, AuthoredSlideCheckpoint.slide_index == 0))
                assert producer_row.svg_checkpoint["quality"] == compact_quality
                assert pages[0]["row_digest"] == recovery.digest(producer_row.model_dump(mode="json"))
                assert compact_store.quality_browser_metrics(producer_row.svg_checkpoint["quality"]) == compact_metrics
                assert copied[1].svg_checkpoint["partial_recovery"]["category"] == "unknown"
                assert "svg_content" not in copied[1].svg_checkpoint and copied[1].author_status == "pending"
                ctx = SimpleNamespace(snapshot=deepcopy(consumer.svg_style_snapshot),partial_recovery_manifest=manifest,
                    raster_reconstruction=False,source_reflow=False)
                runner = AsyncMock(side_effect=AssertionError("must not reauthor a published header"))
                assert await headers.prepare_document_header(ctx,request,consumer.presentation_id,
                    PresentationOutlineModel.model_validate(consumer.authored_outline),"en",consumer,
                    checkpoint=runner,async_session_maker=maker,read_png=runner,write_png=runner,
                    page_runner=runner,page_hooks_factory=runner,state_factory=runner) is ctx
                runner.assert_not_called()
                # Guard the actual quality consumer's producer/consumer
                # identity boundary; the existing tests cover gate integrity.
                from services.generation_core.drivers.svg import quality as deck_quality
                from services.svg_final_selected_continuation import (
                    _consume_local_producer, FinalSelectedEvidenceError, INITIAL_NAMESPACE)
                producer_binding = dict(jobId=str(parent_id),operationId=str(source_id),
                    presentationId=str(presentation.id),ownerId=manifest["owner_id"],tenantId=manifest["tenant_id"])
                consumer_binding = {**producer_binding,"jobId":str(consumer_id),"operationId":str(child_id),
                    "presentationId":str(consumer.presentation_id)}
                gate = dict(binding=deepcopy(producer_binding),runtimeBinding={})
                evidence = dict(proof=dict(owner_id=manifest["owner_id"],tenant_id=manifest["tenant_id"],
                    source_job_id=str(parent_id),source_operation_id=str(source_id)),
                    execution=dict(rounds=[dict(advisory={},groups={})]),consumer_runtime_equivalent=True,
                    consumer_binding=consumer_binding,producer_presentation_id=str(presentation.id))
                with scoped.context() as quality_scope:
                    quality_scope.setattr(deck_quality,"_gate_intact",lambda _: gate)
                    _consume_local_producer(dict(binding=consumer_binding),[],[],evidence,{},[],INITIAL_NAMESPACE)
                    for field in consumer_binding:
                        changed = {**consumer_binding,field:"different"}
                        with pytest.raises(FinalSelectedEvidenceError):
                            _consume_local_producer(dict(binding=changed),[],[],evidence,{},[],INITIAL_NAMESPACE)
                    with pytest.raises(FinalSelectedEvidenceError):
                        _consume_local_producer(dict(binding=producer_binding),[],[],evidence,{},[],INITIAL_NAMESPACE)
                    for field in ("jobId","operationId","presentationId"):
                        original_value = gate["binding"][field]
                        gate["binding"][field] = "different"
                        with pytest.raises(FinalSelectedEvidenceError):
                            _consume_local_producer(dict(binding=consumer_binding),[],[],evidence,{},[],INITIAL_NAMESPACE)
                        gate["binding"][field] = original_value
                assert await recovery._verify_producer_rows(session,manifest,archive) is not None
                assert (await session.get(PresentationGenerationJob,parent_id)).model_dump(mode="json") == archive["job"]
                for field in ("instructions","presentation_id"):
                    altered = deepcopy(consumer.request_payload)
                    altered[field] = "different"
                    if field == "presentation_id":
                        with pytest.raises(recovery.PartialRecoveryRejected):
                            recovery.frozen_episode_payload(altered,manifest)
                    else:
                        assert recovery.digest(recovery.frozen_episode_payload(altered,manifest)) != manifest["frozen_request_digest"]
                saved = consumer.request_payload
                altered = deepcopy(saved)
                altered[recovery.LINEAGE_KEY]["source_job_id"] = str(uuid.uuid4())
                consumer.request_payload = altered
                with pytest.raises(recovery.PartialRecoveryRejected):
                    await recovery.verify_episode(session,child,consumer)
                consumer.request_payload = saved
                await session.rollback()
            # A failed consumer can itself become a preserved producer. Its
            # original header envelope stays on the original producing job.
            async with maker() as session:
                previous = await session.get(WorkspaceOperation,child_id)
                previous.state = "failed"
                previous_job = await session.get(PresentationGenerationJob,consumer_id)
                previous_job.state = "failed"
                await session.commit()
                await session.refresh(previous)
                await session.refresh(previous_job)
                previous_rows = list(await session.scalars(select(AuthoredSlideCheckpoint).where(
                    AuthoredSlideCheckpoint.job_id == consumer_id).order_by(AuthoredSlideCheckpoint.slide_index)))
                previous_task = await session.get(AsyncPresentationGenerationTaskModel,previous_job.task_id)
                previous_seal = (await storage_seals(session,[child_id]))[str(child_id)]
                next_archive = dict(schema=recovery.ARCHIVE_V2,job=previous_job.model_dump(mode="json"),
                    task=previous_task.model_dump(mode="json"),checkpoints=[r.model_dump(mode="json") for r in previous_rows],
                    admissions=deepcopy(archive["admissions"]),logical_calls=deepcopy(archive["logical_calls"]),
                    operation_refs=[recovery._operation_reference(previous,previous_seal,tenant="partial-test"),
                        *archive["operation_refs"]])
                next_pages = [{**page,"row_digest":recovery.digest(row.model_dump(mode="json"))}
                              for page,row in zip(pages,previous_rows,strict=True)]
                next_manifest = {**manifest,"operation_id":str(child_id),"presentation_id":str(previous.presentation_id),
                    "job_id":str(consumer_id),"archive_digest":recovery.digest(next_archive),"pages":next_pages,
                    "source_request_routing":recovery._source_request_routing(previous_job.request_payload),
                    "frozen_request_digest":recovery.digest(recovery._frozen_payload(previous_job.request_payload))}
                next_plan = recovery.PartialRecovery(next_manifest,next_archive,previous_rows,recovery.digest(next_manifest))
                next_presentation,next_conversation = await _fork_append_only_svg_recovery_workspace(session,previous,
                    await session.get(PresentationModel,previous.presentation_id),next_plan)
                next_operation = WorkspaceOperation(owner_id=previous.owner_id,owner_scope=previous.owner_scope,
                    presentation_id=next_presentation.id,conversation_id=next_conversation.id,
                    retry_of_operation_id=previous.id,anchor_position=1,kind="presentation.generate",state="queued",
                    request_payload=dict(recovery_revision=recovery.REVISION,principal_tenant_id="partial-test"),
                    request_hash="5"*64,idempotency_key_hash="6"*64,expected_revision=0)
                session.add(next_operation)
                await session.flush()
                next_job = await recovery.rebind_episode(session,previous,next_operation,next_plan,key_hash="7"*64)
                await session.commit()
                next_operation_id,next_job_id = next_operation.id,next_job.id
            async with maker() as session:
                next_job = await session.get(PresentationGenerationJob,next_job_id)
                next_operation = await session.get(WorkspaceOperation,next_operation_id)
                assert await recovery.verify_episode(session,next_operation,next_job) == next_manifest
                next_ctx = SimpleNamespace(snapshot=deepcopy(next_job.svg_style_snapshot),partial_recovery_manifest=next_manifest,
                    raster_reconstruction=False,source_reflow=False)
                assert await headers.prepare_document_header(next_ctx,request,next_job.presentation_id,
                    PresentationOutlineModel.model_validate(next_job.authored_outline),"en",next_job,
                    checkpoint=runner,async_session_maker=maker,read_png=runner,write_png=runner,
                    page_runner=runner,page_hooks_factory=runner,state_factory=runner) is next_ctx
                runner.assert_not_called()
                assert await recovery._verify_producer_rows(session,next_manifest,next_archive) is not None
                assert await recovery._verify_producer_rows(session,manifest,archive) is not None
                if source_routing == "uuid":
                    from sqlalchemy import update
                    from utils.owner_context import get_current_owner_id, get_current_tenant_id

                    # A separately fenced preview legitimately appends job-wide
                    # usage and a legacy job budget after an episode archive.
                    preview = WorkspaceOperation(owner_id=source.owner_id,owner_scope=source.owner_scope,
                        presentation_id=source.presentation_id,conversation_id=source.conversation_id,
                        anchor_position=1,kind="slide.regenerate.preview",state="succeeded",
                        request_payload={"principal_tenant_id":"partial-test"},
                        request_hash="8"*64,idempotency_key_hash="9"*64,expected_revision=0)
                    session.add(preview)
                    await session.flush()
                    preview_usage = ProviderUsageReceipt(job_id=parent_id,
                        operation=f"svg_page_preview_{preview.id.hex}",stage="author",
                        provider="unavailable",model="unavailable",receipt_key_hash="e"*64,
                        retry_class="initial",outcome="succeeded",latency_ms=0,usage_state="unavailable")
                    preview_budget = ProviderRuntimeBudget(job_id=parent_id,
                        owner_id=source.owner_id,tenant_id="partial-test")
                    session.add_all((preview_usage,preview_budget))
                    await session.flush()
                    session.add(ProviderRuntimeReservation(budget_id=preview_budget.id,
                        reservation_key_hash="f"*64,state="reconciled",estimated_cost=1,
                        actual_cost=1,outcome="succeeded",lease_token_hash="a"*64,
                        lease_expires_at=get_current_utc_datetime()))
                    await session.commit()
                    assert await recovery.verify_episode(session,next_operation,next_job) == next_manifest

                    # New UNKNOWN work owned by the archived source operation
                    # is still forbidden, even before the memo is populated.
                    mutation = await session.begin_nested()
                    try:
                        session.add(PublicProviderLogicalCall(admission_id=admission_id,stage="author",
                            slide_index=1,logical_call_ordinal=2,retry_class="initial",
                            idempotency_key_hash="f"*64,estimated_cost_microunits=100,
                            state="reserved",outcome="unknown"))
                        await session.flush()
                        with pytest.raises(recovery.PartialRecoveryRejected,match="history_changed"):
                            await recovery.verify_episode(session,next_operation,next_job)
                    finally:
                        await mutation.rollback()

                    checks = []
                    original_once = recovery._verify_episode_once
                    async def counted_once(session, operation, job, **kwargs):
                        checks.append(str(operation.id))
                        return await original_once(session, operation, job, **kwargs)
                    with scoped.context() as memo_scope:
                        memo_scope.setattr(recovery, "_verify_episode_once", counted_once)
                        verification = recovery._EpisodeVerification(session)
                        # Review already proved the immutable source in this transaction.
                        # Admission consumes that proof but freshly verifies its child.
                        await recovery.verify_episode(session,previous,previous_job,
                            require_current_code=False,verification=verification)
                        for final in (False, True):
                            assert await recovery.verify_episode(session,next_operation,next_job,
                                verification=verification,finish_verification=final) == next_manifest
                        # Current-code consumers always run in full; the shared
                        # historical producer succeeds only once per scope.
                        assert checks.count(str(next_operation_id)) == 2
                        assert checks.count(str(child_id)) == 1
                        await verification.finish()
                    # Context changes and a new transaction cannot reuse proofs.
                    original_owner, original_tenant = get_current_owner_id(), get_current_tenant_id()
                    for owner, tenant in ((uuid.uuid4(), original_tenant),
                                          (original_owner, "different-tenant")):
                        # The earlier contract in this parameterized node
                        # substitutes these getters; vary that same boundary.
                        with scoped.context() as principal_scope:
                            principal_scope.setattr("utils.owner_context.get_current_owner_id",lambda:owner)
                            principal_scope.setattr("utils.owner_context.get_current_tenant_id",lambda:tenant)
                            with pytest.raises(recovery.PartialRecoveryRejected,match="snapshot_changed"):
                                verification.check(session)
                    verification.active.add(str(next_operation_id))
                    with pytest.raises(recovery.PartialRecoveryRejected,match="lineage_conflict"):
                        await recovery.verify_episode(session,next_operation,next_job,
                            verification=verification)
                    verification.active.clear()
                    # Change persisted data behind the identity map after a
                    # successful memo population. Exit reads must be fresh.
                    changes = [
                        (WorkspaceOperation, next_operation_id, "state", "cancelled", next_operation.state),
                        (WorkspaceOperation, next_operation_id, "owner_id", None, next_operation.owner_id),
                        (WorkspaceOperation, next_operation_id, "lease_expires_at",
                         get_current_utc_datetime()+timedelta(minutes=1), next_operation.lease_expires_at),
                        (WorkspaceOperation, next_operation_id, "version", next_operation.version+1, next_operation.version),
                        (WorkspaceOperation, next_operation_id, "request_payload",
                         {**next_operation.request_payload,"tampered":True}, next_operation.request_payload),
                        (WorkspaceOperation, source_id, "request_payload",
                         {**source.request_payload,"tampered":True}, source.request_payload),
                        (WorkspaceOperation, child_id, "retry_of_operation_id", None, source_id),
                        (PublicProviderLogicalCall, unknown_call_id, "outcome", "succeeded", "unknown"),
                        (AuthoredSlideCheckpoint, rows[1].id, "svg_checkpoint",
                         {**rows[1].svg_checkpoint,"inflight":None}, rows[1].svg_checkpoint),
                    ]
                    for model, row_id, field, changed, original in changes:
                        verification = recovery._EpisodeVerification(session)
                        await recovery.verify_episode(session,next_operation,next_job,
                            verification=verification)
                        mutation = await session.begin_nested()
                        try:
                            await session.execute(update(model).where(model.id == row_id)
                                .values(**{field:changed}).execution_options(synchronize_session=False))
                            with pytest.raises(recovery.PartialRecoveryRejected,match="snapshot_changed"):
                                await verification.finish()
                        finally:
                            # Restore implicit onupdate columns along with the
                            # intended mutation before checking another guard.
                            await mutation.rollback()
                    # Retained recheck of a finished historical proof: whole-payload work stays
                    # per node (no new digest on recheck), unchanged rows pass (by storage hash on
                    # PostgreSQL), committed-equivalent changes and unflushed in-place edits of a
                    # proven ancestor still fall back to cold verification.
                    postgres = session.bind.dialect.name == "postgresql"
                    def retained_proof():
                        return recovery.verify_episode(session,previous,previous_job,
                            require_current_code=False,_retained_proofs=holder)
                    holder = {}
                    await retained_proof()
                    proof = holder[str(previous.id)]
                    retained_stats = proof[0].stats
                    digests = retained_stats["operation_digests"]
                    for _ in range(2):
                        assert await recovery._recheck_retained_episode(session,previous,previous_job,proof) is not None
                    assert retained_stats["operation_digests"] == digests
                    if postgres:
                        assert retained_stats["rows_storage_hits"] > 0
                        assert "storage" in proof[0].storage_modes["rows"].split(",")
                    else:
                        assert proof[0].storage_modes["rows"] == "exact:unsupported_dialect"
                        assert retained_stats["rows_storage_hits"] == 0
                    for model, row_id, field, changed in (
                            (WorkspaceOperation, source_id, "request_payload", {**source.request_payload,"tampered":True}),
                            (WorkspaceOperation, source_id, "owner_id", None),
                            (WorkspaceOperation, child_id, "retry_of_operation_id", None),
                            (PresentationGenerationJob, consumer_id, "request_payload", {**previous_job.request_payload,"tampered":True}),
                            (PublicProviderLogicalCall, unknown_call_id, "outcome", "succeeded"),
                            (AuthoredSlideCheckpoint, rows[1].id, "svg_checkpoint", {**rows[1].svg_checkpoint,"inflight":None})):
                        mutation = await session.begin_nested()
                        try:
                            await session.execute(update(model).where(model.id == row_id)
                                .values(**{field:changed}).execution_options(synchronize_session=False))
                            assert await recovery._recheck_retained_episode(session,previous,previous_job,proof) is None, (model.__name__, field)
                        finally:
                            await mutation.rollback()
                        holder = {}
                        await retained_proof()
                        proof = holder[str(previous.id)]
                    previous.request_payload["tampered"] = True
                    try:
                        assert previous not in session.dirty
                        assert await recovery._recheck_retained_episode(session,previous,previous_job,proof) is None
                    finally:
                        previous.request_payload.pop("tampered")
                    # A proven historical node (child, reached through the consumer edge):
                    # its unflushed in-place edit fails at the exit fence.
                    verification = recovery._EpisodeVerification(session)
                    await recovery.verify_episode(session,next_operation,next_job,verification=verification)
                    node = verification.node_proofs[str(child_id)]
                    node.operation.request_payload["tampered"] = True
                    try:
                        assert node.operation not in session.dirty
                        with pytest.raises(recovery.PartialRecoveryRejected,match="snapshot_changed"):
                            await verification.finish()
                    finally:
                        node.operation.request_payload.pop("tampered")
                    verification = recovery._EpisodeVerification(session)
                    await recovery.verify_episode(session,next_operation,next_job,verification=verification)
                    new_usage = ProviderUsageReceipt(job_id=parent_id, operation="generation",
                        stage="author", provider="unavailable", model="unavailable",
                        receipt_key_hash="d"*64, retry_class="initial", outcome="failed",
                        latency_ms=0, usage_state="unavailable")
                    session.add(new_usage)
                    await session.flush()
                    with pytest.raises(recovery.PartialRecoveryRejected,match="snapshot_changed"):
                        await verification.finish()
                    await session.rollback()
                    await session.execute(select(WorkspaceOperation.id).limit(1))
                    with pytest.raises(recovery.PartialRecoveryRejected,match="snapshot_changed"):
                        verification.check(session)
            async with maker() as session:
                original = await session.get(AuthoredSlideCheckpoint,rows[0].id)
                original.svg_checkpoint = {**original.svg_checkpoint,"input_digest":"tampered"}
                await session.commit()
            async with maker() as session:
                with pytest.raises(recovery.PartialRecoveryRejected,match="history_changed"):
                    await recovery._verify_producer_rows(session,manifest,archive)
    finally:
        await engine.dispose()


async def _svg_append_only_recovery_contract(tmp_path, monkeypatch):
    from copy import deepcopy
    from types import SimpleNamespace
    from enums.llm_provider import LLMProvider
    from models.sql.async_presentation_generation_status import AsyncPresentationGenerationTaskModel
    from models.sql.authored_slide_checkpoint import AuthoredSlideCheckpoint
    from models.sql.presentation_generation_job import PresentationGenerationJob
    from models.sql.public_admission import PublicPolicyProfile, PublicOperationAdmission, PublicProviderLogicalCall, PublicResourceLease
    from models.sql.provider_runtime_reservation import ProviderRuntimeBudget, ProviderRuntimeReservation
    from services.svg_timeout_repurchase import build_timeout_repurchase_manifest, SVG_TIMEOUT_REPURCHASE_V3, APPROVAL_ACTION_PREFIX, verify_repurchase_provider_target, TimeoutRepurchaseRejected, reset_timeout_repurchase_checkpoints
    from services.generation_core.drivers.svg.collect import _provider_attempt_count, _provider_attempt_limit
    from utils.hashing import canonical_json_sha256, canonical_json_digest
    engine, maker = await _database(tmp_path / "svg-append-only.db")
    tables = [m.__table__ for m in [AsyncPresentationGenerationTaskModel, PresentationGenerationJob, AuthoredSlideCheckpoint,
        PublicPolicyProfile, PublicOperationAdmission, PublicProviderLogicalCall, PublicResourceLease, ProviderRuntimeBudget, ProviderRuntimeReservation]]
    try:
        async with engine.begin() as c:
            await c.run_sync(lambda c: SQLModel.metadata.create_all(c, tables=tables))
        presentation = await _create_presentation(maker)
        now = get_current_utc_datetime()
        async with maker() as s:
            user = User(username="append-only-owner", hashed_password="unused")
            s.add(user); await s.flush()
            _, _, source, _ = await bootstrap_workspace(s, presentation_id=presentation.id, request=_bootstrap_request(key="append-only-source"))
            (await s.get(PresentationModel,presentation.id)).owner_id=user.id
            source.owner_id=user.id;source.kind="presentation.generate";source.state="failed"
            source.error_code="svg_source_quality_failed";source.error_detail={"retryable":False};source.version=4;source.fencing_token=2
            source.request_payload={**source.request_payload,"principal_tenant_id":"test"}
            task=AsyncPresentationGenerationTaskModel(status="failed");s.add(task);await s.flush()
            frozen_style={"authoring_profile":{"id":"art-direction-v1","version":8}}
            frozen_style["digest"]=canonical_json_digest(frozen_style,allow_nan=True)
            job=PresentationGenerationJob(task_id=task.id,presentation_id=presentation.id,workspace_operation_id=source.id,
                request_sha256="a"*64,request_payload={"strategy":"svg","content":"append-only","n_slides":12,
                "_internal_svg_stage_plan":{"schema":"svg-stage-plan/v1","partitions":[[0,5,8,11],[1,2,3,4,6,7,9,10]]}},
                svg_style_snapshot=frozen_style,
                authored_outline={"slides":[{"content":str(i)} for i in range(12)]},state="failed",total_slides=12)
            s.add(job);await s.flush();source.request_payload={**source.request_payload,"job_id":str(job.id)}
            values={c.name:100 for c in PublicPolicyProfile.__table__.columns if not c.nullable and c.default is None and c.server_default is None}
            values['version']='append-only-policy';policy=PublicPolicyProfile(**values);s.add(policy);await s.flush()
            adm=PublicOperationAdmission(operation_id=source.id,policy_version=policy.version,owner_id=user.id,tenant_id="test",trace_id_hash="b"*64,
                operation_kind="generation",resource_units=1,policy_digest="c"*64,deadline_at=now,state="failed",terminal_at=now,slide_count=12,
                planned_request_limit=14,retry_request_limit=12,initial_request_count=4,retry_request_count=1,
                estimated_cost_limit_microunits=100,actual_cost_limit_microunits=100)
            s.add(adm);await s.flush()
            for i in range(12):
                payload={"input_digest":f"input-{i}","attempts":[],"inflight":1 if i==8 else None}
                cached=i in {0,5,11};n=2 if i==5 else 1
                if cached:
                    payload.update(svg_content=f"<svg>cache-{i}</svg>",attempts=[{"kind":"provider","receipt":{"attempt":j}} for j in range(n)],
                        quality={"passed":False,"failures":[{"kind":"required_text_missing"}]} if i==5 else {"passed":True})
                if i==0:payload.update(source_manifest_ref={"digest":"frozen-pointer"},source_manifest_digest="frozen-manifest")
                s.add(AuthoredSlideCheckpoint(job_id=job.id,slide_index=i,author_status="completed" if cached else "pending",svg_checkpoint=payload))
                if cached or i==8:
                    for j in range(n if cached else 1):
                        s.add(PublicProviderLogicalCall(admission_id=adm.id,stage="repair" if j else "author",slide_index=i,
                            logical_call_ordinal=j+1,retry_class="retry" if j else "initial",idempotency_key_hash=str(i*10+j).zfill(64),
                            state="reported" if cached else "unavailable",outcome="succeeded" if cached else "cancelled",
                            reconciled_at=now,estimated_cost_microunits=0,provider_id="codex",model_id="gpt-6.1-sol"))
            await s.commit();source_id=source.id;job_id=job.id;owner=user.id;scope=source.owner_scope;policy_version=policy.version
        monkeypatch.setattr("utils.owner_context.get_current_owner_id",lambda:owner)
        monkeypatch.setattr("utils.owner_context.get_current_tenant_id",lambda:"test")
        monkeypatch.setattr("services.workspace_service.get_workspace_owner_scope",lambda:scope)
        monkeypatch.setattr("utils.llm_provider.get_llm_provider",lambda:LLMProvider.CODEX)
        monkeypatch.setattr("utils.llm_provider.get_model",lambda:"gpt-6.1-sol")
        async def lock(s,presentation_id):return await s.get(PresentationModel,presentation_id),0
        monkeypatch.setattr("services.workspace_service._lock_workspace_mutation_admission",lock)
        deny=True
        async def admit(s,**kw):
            if deny:raise HTTPException(status_code=429,detail="public_owner_queue_full")
            s.add(PublicOperationAdmission(operation_id=kw['operation'].id,policy_version=policy_version,owner_id=owner,tenant_id="test",
                trace_id_hash="d"*64,operation_kind="generation",resource_units=1,policy_digest="e"*64,deadline_at=now+timedelta(minutes=10),
                state="active",slide_count=12,planned_request_limit=14,retry_request_limit=12,
                estimated_cost_limit_microunits=100,actual_cost_limit_microunits=100))
            await s.flush()
        from fastapi import HTTPException
        monkeypatch.setattr("services.presentation_generation_pipeline._admit_generation_operation_v2_compatible",admit)
        async with maker() as s:
            source=await s.get(WorkspaceOperation,source_id)
            recovery=await build_timeout_repurchase_manifest(s,source,presentation_revision=0,recovery_revision=SVG_TIMEOUT_REPURCHASE_V3)
            assert recovery.manifest['remaining_logical_call_limit']==21
            assert len(recovery.manifest['unknown_checkpoints'])==1 and len(recovery.manifest['completed_checkpoints'])==3
            before={m.__tablename__:[r.model_dump(mode='json') for r in await s.scalars(select(m))] for m in [WorkspaceOperation,PresentationGenerationJob,AuthoredSlideCheckpoint,PublicOperationAdmission,PublicProviderLogicalCall]}
            with pytest.raises(TimeoutRepurchaseRejected,match='original_checkpoint_immutable'):
                reset_timeout_repurchase_checkpoints(s,recovery,operation_id=source_id,now=now)
        request=WorkspaceOperationActionRequest(action="retry",idempotency_key="append-only-retry",expected_operation_version=4,
            expected_revision=0,recovery_revision=SVG_TIMEOUT_REPURCHASE_V3,action_id=APPROVAL_ACTION_PREFIX+recovery.manifest_hash)
        async with maker() as s:
            with pytest.raises(WorkspaceServiceError) as e:await apply_workspace_action(s,operation_id=source_id,request=request)
            assert e.value.code=='public_owner_queue_full'
        deny=False
        async with maker() as s:
            receipt,child,_=await apply_workspace_action(s,operation_id=source_id,request=request)
            assert child.retry_of_operation_id==source_id and child.presentation_id!=presentation.id
            child_id=child.id
            child_job=await s.scalar(select(PresentationGenerationJob).where(PresentationGenerationJob.workspace_operation_id==child.id))
            assert child_job.id!=job_id and child_job.request_payload['_svg_recovery_lineage']['parent_job_id']==str(job_id)
            child_adm=await s.scalar(select(PublicOperationAdmission).where(PublicOperationAdmission.operation_id==child.id))
            assert child_adm.planned_request_limit==10 and child_adm.retry_request_limit==11
            rows=list(await s.scalars(select(AuthoredSlideCheckpoint).where(AuthoredSlideCheckpoint.job_id==child_job.id)))
            replacement=next(r.svg_checkpoint for r in rows if r.slide_index==8)
            assert _provider_attempt_count(replacement)==1 and _provider_attempt_limit(replacement,index=8,job=child_job)==2
            assert next(r.svg_checkpoint for r in rows if r.slide_index==5)['quality']['passed'] is False
            await verify_repurchase_provider_target(s,child,child_adm,stage="repair",slide_index=8,
                logical_call_key="svg-author-8-2",provider_id="codex",model_id="gpt-6.1-sol")
            with pytest.raises(TimeoutRepurchaseRejected):await verify_repurchase_provider_target(s,child,child_adm,stage="repair",slide_index=5,
                logical_call_key="svg-author-5-3",provider_id="codex",model_id="gpt-6.1-sol")
            from services.svg_generation_assets import _verify_inherited_source_manifest
            context=SimpleNamespace(owner_id=owner,tenant_id="test")
            inherited=SimpleNamespace(presentation_id=presentation.id,source_revision=1,digest="frozen-manifest")
            await _verify_inherited_source_manifest(s,inherited,{"digest":"frozen-pointer"},context=context,
                presentation_id=child.presentation_id,operation_id=child.id)
            with pytest.raises(ValueError):await _verify_inherited_source_manifest(s,inherited,{"digest":"frozen-pointer"},
                context=SimpleNamespace(owner_id=owner,tenant_id="foreign"),presentation_id=child.presentation_id,operation_id=child.id)
            with pytest.raises(ValueError):await _verify_inherited_source_manifest(s,inherited,{"digest":"changed-pointer"},
                context=context,presentation_id=child.presentation_id,operation_id=child.id)
            from services.generation_core.drivers.svg import collect
            cached=next(r.svg_checkpoint for r in rows if r.slide_index==5)
            written=[]
            async def checkpoint(job,index,*,payload=None):
                if payload is not None:written.append(deepcopy(payload))
                return payload
            async def failed_render(**kw):
                kw["payload"]["attempts"][-1]["quality"] = {"passed":False,"remeasurement":True}
                kw["payload"]["quality"]={"passed":False,"failures":[{"kind":"required_text_missing"}]}
                return collect.render.RETRY
            monkeypatch.setattr(collect.render,"attempt_render",failed_render)
            hooks=SimpleNamespace(checkpoint=checkpoint,async_session_maker=None,write_png=None,read_png=lambda _:None)
            with pytest.raises(ValueError,match="svg_source_quality_failed"):
                await collect._remeasure_append_only_cache(SimpleNamespace(source_reflow=False),None,child.presentation_id,
                    5,"source",cached,"input-5",child_job,hooks)
            assert written[-1]['quality']['passed'] is False and written[-1]['svg_content']==cached['svg_content']
            s.add(PublicProviderLogicalCall(admission_id=child_adm.id,stage='repair',slide_index=8,logical_call_ordinal=2,retry_class='retry',
                idempotency_key_hash='f'*64,estimated_cost_microunits=0,state='unavailable',outcome='cancelled',reconciled_at=now));await s.flush()
            with pytest.raises(TimeoutRepurchaseRejected):await verify_repurchase_provider_target(s,child,child_adm,stage="repair",slide_index=8,
                logical_call_key="svg-author-8-2",provider_id="codex",model_id="gpt-6.1-sol")
            await s.rollback()
        async with maker() as s:
            _,replay,replayed=await apply_workspace_action(s,operation_id=source_id,request=request)
            assert replay.id==child_id and replayed is True
            for model in [WorkspaceOperation,PresentationGenerationJob,AuthoredSlideCheckpoint,PublicOperationAdmission,PublicProviderLogicalCall]:
                ids={r['id'] for r in before[model.__tablename__]}
                after=[r.model_dump(mode='json') for r in await s.scalars(select(model)) if str(r.id) in ids]
                assert canonical_json_sha256(after)==canonical_json_sha256(before[model.__tablename__])
        async with maker() as s:
            failed=await s.get(WorkspaceOperation,child_id)
            failed.state="dead_letter";failed.error_code="http_error_500";failed.version+=1
            failed_job=await s.scalar(select(PresentationGenerationJob).where(PresentationGenerationJob.workspace_operation_id==child_id))
            failed_job.state="failed"
            failed_adm=await s.scalar(select(PublicOperationAdmission).where(PublicOperationAdmission.operation_id==child_id))
            failed_adm.state="failed";failed_adm.terminal_at=now
            await s.commit()
        async with maker() as s:
            failed=await s.get(WorkspaceOperation,child_id)
            replay_manifest=await build_timeout_repurchase_manifest(s,failed,presentation_revision=0,recovery_revision=SVG_TIMEOUT_REPURCHASE_V3)
            assert replay_manifest.manifest['total_logical_call_limit']==26
            assert replay_manifest.manifest['remaining_logical_call_limit']==21
            assert replay_manifest.manifest['origin_operation_id']==str(source_id)
            assert replay_manifest.manifest['source_manifest_revision']==1
            assert replay_manifest.manifest['unknown_checkpoints'][0]['original_logical_call_id']==recovery.manifest['unknown_checkpoints'][0]['original_logical_call_id']
            from services.svg_timeout_repurchase import verified_append_only_consumption_baseline
            baseline = await verified_append_only_consumption_baseline(s, failed)
            assert baseline == (4, 1, 26, 12)
            from services.provider_runtime_control import ProviderRuntimeControl, JobKillConditions
            control = ProviderRuntimeControl()
            await control.register_job("append-only-free-contract", estimated_budget_microunits=None, actual_budget_microunits=None, lineage_initial_requests=baseline[0], lineage_retry_requests=baseline[1], kill_conditions=JobKillConditions(max_total_requests=baseline[2], max_retry_requests=baseline[3]))
            async with await control.admit("append-only-free-contract", "source38-replacement", estimated_cost_microunits=1, is_retry=True):
                pass
            status = await control.job_status("append-only-free-contract")
            assert status["initial_requests"] == 0 and status["retry_requests"] == 1

            import hashlib
            known_row = await s.scalar(select(AuthoredSlideCheckpoint).join(PresentationGenerationJob,AuthoredSlideCheckpoint.job_id==PresentationGenerationJob.id).where(PresentationGenerationJob.workspace_operation_id==failed.id,AuthoredSlideCheckpoint.slide_index==8))
            known_payload = deepcopy(known_row.svg_checkpoint)
            known_payload["input_digest"] = "confirmed-author-input"
            known_payload['svg_content'] = '<svg xmlns="http://www.w3.org/2000/svg"><text x="10" y="20">known</text></svg>'
            known_payload['attempts'] = [{'ordinal':2,'outcome':'succeeded'}]
            known_payload['quality'] = {'passed':False,'failures':[{'kind':'native_requested_table_missing'}]}
            known_row.svg_checkpoint = known_payload
            failed.error_code = 'svg_source_quality_failed'
            child_admission = await s.scalar(select(PublicOperationAdmission).where(PublicOperationAdmission.operation_id==failed.id))
            child_admission.retry_request_count = 1
            s.add(PublicProviderLogicalCall(admission_id=child_admission.id,stage='repair',slide_index=8,logical_call_ordinal=2,retry_class='retry',idempotency_key_hash=hashlib.sha256(f"public-provider-call/v2:{failed.id}:repair:8:2:{hashlib.sha256(b'svg-author-8-2').hexdigest()}".encode()).hexdigest(),state='reported',outcome='succeeded',reconciled_at=now,estimated_cost_microunits=0,provider_id='codex',model_id='gpt-6.1-sol'))
            s.add(known_row);s.add(failed);s.add(child_admission);await s.commit()
            known_manifest = await build_timeout_repurchase_manifest(s,failed,presentation_revision=0,recovery_revision=SVG_TIMEOUT_REPURCHASE_V3)
            assert known_manifest.manifest['previous_consumed_logical_calls'] == 6
            assert known_manifest.manifest['remaining_logical_call_limit'] == 20
            assert len(known_manifest.manifest['completed_checkpoints']) == 4
            assert known_manifest.manifest['unknown_checkpoints'] == []
            assert next(w for w in known_manifest.manifest['completed_checkpoints'] if w['slide_index']==8)['max_additional_author_calls'] == 0
            assert next(w for w in known_manifest.manifest['completed_checkpoints'] if w['slide_index']==5)['known_quality_repair_approved'] is True
    finally:await engine.dispose()
    # A confirmed author result becomes an immutable cache, retaining its old marker only as audit evidence.
    from services.svg_checkpoint_recovery import append_only_svg_checkpoint_payload
    from utils.hashing import canonical_json_sha256
    from types import SimpleNamespace
    import json
    author_original = json.loads(json.dumps(known_payload))
    author_original["append_only_unstarted"] = {"witness": {"slide_index": 8}, "lineage": {"child_job_id": "ancestor"}}
    author_lineage = {"child_job_id": str(failed_job.id), "child_operation_id": str(failed.id)}
    author_row = SimpleNamespace(svg_checkpoint=author_original, model_dump=lambda **kwargs: {"svg_checkpoint": author_original})
    author_witness = {"slide_index": 8, "consumed_attempt_count": 2, "max_additional_author_calls": 0, "row_digest": canonical_json_sha256(author_row.model_dump(mode="json"))}
    author_cache = append_only_svg_checkpoint_payload(author_row, author_witness, author_lineage, cached=True, replacement=False)
    assert "append_only_unstarted" not in author_cache
    assert author_cache["consumed_author_lineage"] == author_original["append_only_unstarted"]
    cached_job = SimpleNamespace(id=failed_job.id, workspace_operation_id=failed.id, request_payload={"_svg_recovery_lineage": author_lineage})
    assert _provider_attempt_limit(author_cache, index=8, job=cached_job) == 0
    assert next(entry for entry in known_manifest.manifest["completed_checkpoints"] if entry["slide_index"] == 8)["input_digest"] == "confirmed-author-input"
    from services.svg_timeout_repurchase import _validated_stage_review_input
    subjective_review = {"schema": "svg-stage-review-decision/v1", "reviewerKind": "agent", "reviewerRole": "authenticated_owner_agent", "model": "gpt-6.1-sol", "effort": "high", "userVisualReviewConfirmed": False, "vqaFlagsRemoved": False, "hardGatesRelaxed": False, "operationId": str(failed.id), "presentationId": str(failed.presentation_id), "vqaLogicalCallId": "00000000-0000-0000-0000-000000000001", "stageOrdinal": 1, "evidenceDigest": "a" * 64, "pages": [{"index": 8, "svgSha256": "b" * 64, "pngSha256": "c" * 64, "issues": ["illegible"], "decision": "accepted", "observedReadable": True, "majorClippingOrOverlapObserved": False}]}
    assert _validated_stage_review_input(subjective_review) == subjective_review
    assert WorkspaceOperationActionRequest(action="retry", idempotency_key="review-bound", stage_review_decision=subjective_review).stage_review_decision == subjective_review
    for key in ("userVisualReviewConfirmed", "vqaFlagsRemoved", "hardGatesRelaxed"):
        invalid_review = json.loads(json.dumps(subjective_review))
        invalid_review[key] = True
        with pytest.raises(TimeoutRepurchaseRejected):
            _validated_stage_review_input(invalid_review)
    invalid_review = json.loads(json.dumps(subjective_review))
    invalid_review["pages"][0]["issues"] = ["required_text_missing"]
    with pytest.raises(TimeoutRepurchaseRejected):
        _validated_stage_review_input(invalid_review)
