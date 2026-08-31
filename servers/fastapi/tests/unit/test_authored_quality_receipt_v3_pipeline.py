from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

import pytest

from services.authored_quality_receipt_v2 import (
    QualityReceiptLedger,
    create_quality_receipt_v2,
)
from services.authored_quality_receipt_v3_pipeline import (
    create_generation_quality_receipt_v3,
    project_persisted_authored_quality_receipt_v3,
)
from services.authored_vnext.capability_registry import capability_metadata
from models.authored_vnext.deck import (
    SceneEditability,
    SceneOwnerDecision,
    SceneTextElement,
    SlideHybridExportPlan,
    seal_deck_scene,
    seal_slide_scene,
)
from models.authored_vnext.master.contract import Rect
from services.authored_vnext.capability_registry import (
    EDITABLE_HYBRID_PROFILE_ID,
    scene_capabilities,
)
from services.authored_vnext.integration.export_service import _assert_worker_readback
from services.authored_vnext.ooxml.native_scene import materialize_scene_pptx


def _receipt_v2():
    return create_quality_receipt_v2(
        QualityReceiptLedger(),
        attempt_id="live-generation-v3",
        revision=2,
        ledger_digest="sha256:" + "1" * 64,
        deck_html_digest="sha256:" + "2" * 64,
        evidence={
            "safety": {"status": "passed", "residuals": []},
            "capacity": {
                "browserObserved": True,
                "checks": {
                    name: True
                    for name in ("text", "title", "body", "font", "qid", "image")
                },
            },
            "sourceReadback": {"required": False, "status": "not_applicable"},
        },
    )


@pytest.mark.anyio
async def test_live_generation_binds_v2_to_evidence_bound_v3() -> None:
    receipt_v2 = _receipt_v2()
    receipt_v3 = await create_generation_quality_receipt_v3(
        receipt_v2=receipt_v2,
        job_id=None,
        slide_roles=("cover", "content"),
        provider_attempt_counts=(1, 2),
        repaired_slide_indices=(1,),
        capacity_evidence={
            "browserObserved": True,
            "checks": {"font": True},
            "violations": [],
        },
        safety_evidence={"status": "passed", "residuals": []},
        browser_metrics=(
            {"capabilityRegistry": capability_metadata(consumer="chromium")},
            {"capabilityRegistry": capability_metadata(consumer="chromium")},
        ),
        visual_review_passed=True,
        source_manifest=None,
        source_readback=None,
    )

    assert receipt_v3.gate_passed is False
    assert receipt_v3.previous_schema_version == "quality-receipt/v2"
    assert receipt_v3.previous_version_receipt_digest == receipt_v2.receipt_digest
    assert receipt_v3.capability_consumer_statuses == (
        ("prompt", "unavailable"),
        ("validator", "unavailable"),
        ("font", "unavailable"),
        ("chromium", "passed"),
        ("scene", "unavailable"),
        ("ooxml", "not_required"),
        ("readback", "not_required"),
    )
    assert "deterministic.geometry.failed" in receipt_v3.blockers
    assert "visualReview.required_failed" not in receipt_v3.blockers
    assert "provider:slide:1:recovery" in receipt_v3.t1_triggers

    generation_path = (
        Path(__file__).parents[2] / "services" / "authored_presentation_generation.py"
    )
    tree = ast.parse(generation_path.read_text(encoding="utf-8"))
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "create_generation_quality_receipt_v3"
        for node in ast.walk(tree)
    )
    source = generation_path.read_text(encoding="utf-8")
    assert '"promotionReady": False' in source
    assert '"powerPointReadback": "unavailable"' in source
    assert '"libreOfficeReadback": "unavailable"' in source
    assert '"performance_checker_evidence_not_persisted"' in source
    assert '"visual_checker_evidence_not_persisted"' in source


@pytest.mark.anyio
async def test_live_source_generation_seals_occurrences_and_readback_registry() -> None:
    receipt_v3 = await create_generation_quality_receipt_v3(
        receipt_v2=_receipt_v2(),
        job_id=None,
        slide_roles=("content",),
        provider_attempt_counts=(1,),
        repaired_slide_indices=(),
        capacity_evidence={
            "browserObserved": True,
            "checks": {"font": True},
            "violations": [],
        },
        safety_evidence={"status": "passed", "residuals": []},
        browser_metrics=(
            {"capabilityRegistry": capability_metadata(consumer="chromium")},
        ),
        visual_review_passed=False,
        source_manifest={
            "units": [
                {
                    "blocks": [
                        {
                            "sourceBlockId": "text-1",
                            "kind": "text",
                            "contentDigest": "sha256:" + "3" * 64,
                        },
                        {
                            "sourceBlockId": "image-1",
                            "kind": "image",
                            "contentDigest": "sha256:" + "4" * 64,
                        },
                    ]
                }
            ]
        },
        source_readback={
            "verified": True,
            "capabilityRegistry": capability_metadata(consumer="readback"),
            "evidence": [
                {"sourceBlockId": "text-1"},
                {"sourceBlockId": "image-1"},
            ],
        },
    )

    assert receipt_v3.gate_passed is False
    assert dict(receipt_v3.capability_consumer_statuses)["readback"] == "passed"


@pytest.mark.anyio
async def test_t1_without_visual_review_is_unavailable_and_blocks_quality() -> None:
    receipt_v3 = await create_generation_quality_receipt_v3(
        receipt_v2=_receipt_v2(),
        job_id=None,
        slide_roles=("cover",),
        provider_attempt_counts=(1,),
        repaired_slide_indices=(),
        capacity_evidence={
            "browserObserved": True,
            "checks": {"font": True},
            "violations": [],
        },
        safety_evidence={"status": "passed", "residuals": []},
        browser_metrics=(
            {"capabilityRegistry": capability_metadata(consumer="chromium")},
        ),
        visual_review_passed=False,
        source_manifest=None,
        source_readback=None,
    )

    assert receipt_v3.gate_passed is False
    assert "visualReview.required_failed" in receipt_v3.blockers


@pytest.mark.anyio
async def test_generation_seals_observed_prompt_scene_and_capacity_evidence_to_port() -> None:
    appended: list[tuple[object, object]] = []

    class Port:
        async def append_quality_receipt_v3(self, *, receipt, evidence) -> None:
            appended.append((receipt, evidence))

    evidence = {
        "browserObserved": True,
        "checks": {
            name: True
            for name in ("text", "title", "body", "font", "qid", "image")
        },
        "violations": [],
    }
    receipt_v3 = await create_generation_quality_receipt_v3(
        receipt_v2=_receipt_v2(),
        job_id=None,
        slide_roles=("content",),
        provider_attempt_counts=(1,),
        repaired_slide_indices=(),
        capacity_evidence=evidence,
        safety_evidence={"status": "passed", "residuals": []},
        browser_metrics=(
            {"capabilityRegistry": capability_metadata(consumer="chromium")},
        ),
        visual_review_passed=False,
        source_manifest=None,
        source_readback=None,
        design_system_prompt=(
            "Capability contract: "
            + capability_metadata(consumer="prompt")["registryDigest"]
        ),
        scene_envelope={
            "schemaVersion": "authored-deck-scene-envelope/v1",
            "sceneDigest": "sha256:" + "1" * 64,
            "sourceDigest": "sha256:" + "2" * 64,
            "qidDigest": "sha256:" + "3" * 64,
        },
        persistence_port=Port(),
        durability_profile="public",
    )

    statuses = dict(receipt_v3.capability_consumer_statuses)
    assert statuses["prompt"] == "passed"
    assert statuses["validator"] == "passed"
    assert statuses["font"] == "passed"
    assert statuses["scene"] == "passed"
    assert "deterministic.geometry.failed" not in receipt_v3.blockers
    assert "deterministic.font.failed" not in receipt_v3.blockers
    assert "deterministic.content.failed" not in receipt_v3.blockers
    assert "persistence.durable_authority_required" not in receipt_v3.blockers
    assert receipt_v3.gate_passed is True
    assert appended and appended[0][0] == receipt_v3


@pytest.mark.anyio
async def test_public_generation_without_persistence_port_is_blocked() -> None:
    receipt_v3 = await create_generation_quality_receipt_v3(
        receipt_v2=_receipt_v2(),
        job_id=None,
        slide_roles=("content",),
        provider_attempt_counts=(1,),
        repaired_slide_indices=(),
        capacity_evidence={"browserObserved": False, "checks": {}},
        safety_evidence={"status": "passed", "residuals": []},
        browser_metrics=(),
        visual_review_passed=False,
        source_manifest=None,
        source_readback=None,
        durability_profile="public",
    )
    assert receipt_v3.gate_passed is False
    assert "persistence.durable_authority_required" in receipt_v3.blockers


@pytest.mark.anyio
async def test_persisted_quality_gate_projects_only_verified_internal_receipts(tmp_path) -> None:
    receipt_v2 = _receipt_v2()
    receipt_v3 = await create_generation_quality_receipt_v3(
        receipt_v2=receipt_v2,
        job_id=None,
        slide_roles=("content",),
        provider_attempt_counts=(1,),
        repaired_slide_indices=(),
        capacity_evidence={"browserObserved": True, "checks": {"font": True}},
        safety_evidence={"status": "passed", "residuals": []},
        browser_metrics=(
            {"capabilityRegistry": capability_metadata(consumer="chromium")},
        ),
        visual_review_passed=False,
        source_manifest=None,
        source_readback=None,
    )
    persisted_gate = {
        **receipt_v2.to_dict(),
        "qualityReceiptV3": receipt_v3.to_dict(),
    }

    projected = project_persisted_authored_quality_receipt_v3(
        authored_quality_gate=persisted_gate,
        quality_receipt_id="qrcpt_1",
        operation_id="op_generation_1",
        presentation_id="prs_1",
        artifact_digest="sha256:" + "3" * 64,
        source_revision=2,
        scene_digest="sha256:" + "4" * 64,
        exporter_version="authored-ooxml/1",
        evaluated_at=datetime(2026, 8, 30, tzinfo=timezone.utc),
    )

    assert projected.release_readiness == "blocked"
    assert projected.authoring.status == "failed"
    assert projected.geometry_font.status == "failed"
    assert projected.visual_review.status == "unavailable"
    assert projected.performance.status == "not_assessed"
    assert projected.native_coverage_ooxml.status == "pending"

    tampered = {
        **persisted_gate,
        "qualityReceiptV3": {
            **receipt_v3.to_dict(),
            "receiptDigest": "sha256:" + "0" * 64,
        },
    }
    with pytest.raises(ValueError, match="quality_receipt_v3_payload_digest_mismatch"):
        project_persisted_authored_quality_receipt_v3(
            authored_quality_gate=tampered,
            quality_receipt_id="qrcpt_1",
            operation_id="op_generation_1",
            presentation_id="prs_1",
            artifact_digest="sha256:" + "3" * 64,
            source_revision=2,
            scene_digest="sha256:" + "4" * 64,
            exporter_version="authored-ooxml/1",
            evaluated_at=datetime(2026, 8, 30, tzinfo=timezone.utc),
        )

    # Exercise the actual persistence adapter across sessions, not a dict-only
    # PASS projection. SQLite is the isolated transaction seam, not PG locking proof.
    from copy import deepcopy
    from types import SimpleNamespace
    import uuid
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlmodel import SQLModel
    from models.sql.workspace import WorkspaceOperation, WorkspaceOperationEvent
    from services.public_quality_receipt_v3_store import (
        PostgresOperationQualityReceiptV3Port, append_public_review_evidence,
        load_public_quality_evidence, quality_evidence_digest,
    )
    from services.workspace_quality_operation_handler import build_export_release_review_receipt
    from services.workspace_operation_runner import sanitize_workspace_event_payload

    owner, presentation, generation_id, review_id = (uuid.uuid4() for _ in range(4))
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'quality-evidence.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: SQLModel.metadata.create_all(
            sync, tables=[WorkspaceOperation.__table__, WorkspaceOperationEvent.__table__]))
    common = dict(owner_id=owner, owner_scope=str(owner), presentation_id=presentation,
                  conversation_id=uuid.uuid4(), anchor_position=1, state="running",
                  idempotency_key_hash="a" * 64, request_hash="b" * 64,
                  attempt_token="private-test-lease", fencing_token=1)
    async with factory() as session:
        session.add(WorkspaceOperation(id=generation_id, kind="presentation.generate",
                                      expected_revision=1, result_revision=2, **common))
        session.add(WorkspaceOperation(id=review_id, kind="quality.review",
                                      expected_revision=2, result_revision=3, **{**common, "anchor_position": 2}))
        await session.commit()
        port = PostgresOperationQualityReceiptV3Port(session, operation_id=generation_id,
                attempt_token="private-test-lease", fencing_token=1)
        receipt_v3 = await create_generation_quality_receipt_v3(
            receipt_v2=receipt_v2, job_id=None, slide_roles=("cover",),
            provider_attempt_counts=(1,), repaired_slide_indices=(),
            capacity_evidence={"browserObserved": True, "checks": {
                key: True for key in ("text", "title", "body", "font", "qid", "image")}, "violations": []},
            safety_evidence={"status": "passed", "residuals": []},
            browser_metrics=({"capabilityRegistry": capability_metadata(consumer="chromium")},),
            visual_review_passed=False, source_manifest=None, source_readback=None,
            design_system_prompt=capability_metadata(consumer="prompt")["registryDigest"],
            scene_envelope={"schemaVersion": "authored-deck-scene-envelope/v1",
                "sceneDigest": "sha256:" + "1" * 64, "sourceDigest": "sha256:" + "2" * 64,
                "qidDigest": "sha256:" + "3" * 64},
            persistence_port=port, durability_profile="public")
        (await session.get(WorkspaceOperation, generation_id)).state = "succeeded"
        await session.commit()
    gate = {**receipt_v2.to_dict(), "qualityReceiptV3": receipt_v3.to_dict()}
    lookup = dict(owner_id=owner, owner_scope=str(owner), presentation_id=presentation,
                  receipt_digest=receipt_v3.receipt_digest)
    async with factory() as session:
        generation = await load_public_quality_evidence(session, **lookup)
        assert generation and generation["operationId"] == str(generation_id)
        assert await load_public_quality_evidence(session, **{**lookup, "owner_id": uuid.uuid4()}) is None
        assert await load_public_quality_evidence(session, **{**lookup, "owner_scope": "other"}) is None
        assert await load_public_quality_evidence(session, **{**lookup, "presentation_id": uuid.uuid4()}) is None
    runtime, render, policy = ("sha256:" + ch * 64 for ch in "678")
    kwargs = dict(authored_quality_gate=gate, quality_receipt_id="quality:export",
        operation_id="export:1", presentation_id=str(presentation),
        artifact_digest="sha256:" + "3" * 64, source_revision=2,
        scene_digest=receipt_v2.deck_html_digest, exporter_version="public-worker-protocol/v1",
        evaluated_at=datetime(2026, 8, 30, tzinfo=timezone.utc),
        generation_evidence_record=generation, render_runtime_binding_digest=runtime,
        result_render_identity_digest=render, checker_policy_digest=policy)
    before = project_persisted_authored_quality_receipt_v3(**kwargs)
    assert before.authoring.status == before.geometry_font.status == "passed"
    assert before.visual_review.required and before.visual_review.status == "failed"
    assert before.release_readiness == "blocked"
    stale = project_persisted_authored_quality_receipt_v3(**{**kwargs,
        "source_revision": 3, "scene_digest": "sha256:" + "4" * 64})
    assert stale.authoring.status == stale.geometry_font.status == "failed"
    post = {"schemaVersion": "export-release-post-review-gate/v2", "profileId": "ordinary",
        "baseDeckHtmlDigest": receipt_v2.deck_html_digest, "resultDeckHtmlDigest": "sha256:" + "4" * 64,
        "renderRuntimeBindingDigest": runtime, "sourceRenderIdentityDigest": "sha256:" + "5" * 64,
        "resultRenderIdentityDigest": render, "checkerPolicyDigest": policy,
        "fixedCount": 1, "slideCount": 1, "issues": [], "gatePassed": True}
    post["evidenceDigest"] = quality_evidence_digest(post)
    checker_snapshot = {}
    review_receipt = build_export_release_review_receipt(
        operation_id=review_id, source_revision=2, result_revision=3, slide_count=1,
        review_result={"reviewed_count": 1, "remaining_issue_count": 0,
            "incomplete_measurement_count": 0, "unresolved_confirmed_issue_count": 0,
            "fixed_count": 1, "slide_results": [{"slide_index": 0}]},
        admission=SimpleNamespace(id=uuid.uuid4(), planned_request_limit=2, retry_request_limit=1,
            initial_request_count=1, retry_request_count=0, estimated_consumed_microunits=1,
            actual_consumed_microunits=1, actual_cost_unavailable_count=0),
        base_deck_html_digest=receipt_v2.deck_html_digest,
        base_quality_receipt_digest=receipt_v3.receipt_digest,
        result_deck_html_digest=post["resultDeckHtmlDigest"],
        render_runtime_binding_digest=runtime, source_render_identity_digest=post["sourceRenderIdentityDigest"],
        result_render_identity_digest=render, checker_policy_digest=policy,
        base_evidence_kind="generation-quality-v3", base_evidence_operation_id=None,
        post_review_gate=post, evidence_sink=checker_snapshot)
    async with factory() as session:
        append = dict(operation_id=review_id, attempt_token="private-test-lease",
                      fencing_token=1, receipt=review_receipt, evidence=checker_snapshot)
        with pytest.raises(RuntimeError, match="operation_lease_lost"):
            await append_public_review_evidence(session, **{**append, "fencing_token": 2})
        await append_public_review_evidence(session, **append)
        await append_public_review_evidence(session, **append)  # exact replay
        op = await session.get(WorkspaceOperation, review_id)
        op.state = "succeeded"
        op.result = sanitize_workspace_event_payload({"release_review_receipt": review_receipt},
                                                     operation_kind="quality.review")
        assert "checkerEvidence" not in str(op.result)
        await session.commit()
    async with factory() as session:
        reviewed = await load_public_quality_evidence(session, **{**lookup,
            "receipt_digest": review_receipt["receiptDigest"], "operation_id": review_id})
    kwargs.update(source_revision=3, scene_digest=post["resultDeckHtmlDigest"], review_evidence_record=reviewed)
    after = project_persisted_authored_quality_receipt_v3(**kwargs)
    assert after.release_readiness == "ready"
    assert after.authoring.status == after.geometry_font.status == after.visual_review.status == "passed"
    assert after.performance.status == "not_assessed" and after.native_coverage_ooxml.status == "pending"
    changed_artifact = project_persisted_authored_quality_receipt_v3(**{**kwargs, "artifact_digest": "sha256:" + "9" * 64})
    assert changed_artifact.authoring.evidence.input_digest != after.authoring.evidence.input_digest
    for bad_kwargs in ({"render_runtime_binding_digest": "sha256:" + "0" * 64},
                       {"source_revision": 4}, {"scene_digest": "sha256:" + "0" * 64}):
        with pytest.raises(ValueError):
            project_persisted_authored_quality_receipt_v3(**{**kwargs, **bad_kwargs})
    corrupt = deepcopy(reviewed)
    corrupt["evidence"]["postReviewGate"]["issues"] = [{"code": "overflow"}]
    with pytest.raises(ValueError, match="checker_digest_mismatch"):
        project_persisted_authored_quality_receipt_v3(**{**kwargs, "review_evidence_record": corrupt})
    bad_generation = deepcopy(generation)
    bad_generation["evidence"]["checkerPayloads"]["capacity"]["checks"]["font"] = False
    with pytest.raises(ValueError, match="generation_evidence_invalid"):
        project_persisted_authored_quality_receipt_v3(**{**kwargs, "generation_evidence_record": bad_generation})

    def successor(parent, *, operation_id, base_kind="release-review-transition"):
        unchanged_gate = {**post, "fixedCount": 0,
                          "baseDeckHtmlDigest": post["resultDeckHtmlDigest"],
                          "sourceRenderIdentityDigest": render}
        unchanged_gate.pop("evidenceDigest")
        unchanged_gate["evidenceDigest"] = quality_evidence_digest(unchanged_gate)
        snapshot = {}
        receipt = build_export_release_review_receipt(
            operation_id=operation_id, source_revision=3, result_revision=3, slide_count=1,
            review_result={"reviewed_count": 1, "remaining_issue_count": 0,
                "incomplete_measurement_count": 0, "unresolved_confirmed_issue_count": 0,
                "fixed_count": 0, "slide_results": [{"slide_index": 0}]},
            admission=SimpleNamespace(id=uuid.uuid4(), planned_request_limit=2, retry_request_limit=1,
                initial_request_count=1, retry_request_count=0, estimated_consumed_microunits=1,
                actual_consumed_microunits=1, actual_cost_unavailable_count=0),
            base_deck_html_digest=post["resultDeckHtmlDigest"],
            base_quality_receipt_digest=parent["receipt"]["receiptDigest"],
            result_deck_html_digest=post["resultDeckHtmlDigest"],
            render_runtime_binding_digest=runtime, source_render_identity_digest=render,
            result_render_identity_digest=render, checker_policy_digest=policy,
            base_evidence_kind=base_kind, base_evidence_operation_id=parent["operationId"],
            post_review_gate=unchanged_gate, evidence_sink=snapshot)
        return receipt, snapshot

    # A real H1 root followed by up to 31 unchanged, independently sealed
    # full-deck reviews is reusable. No ancestor header boolean is a checker.
    parent = reviewed
    successors = []
    async with factory() as session:
        for offset in range(32):
            child_id = uuid.uuid4()
            child_receipt, child_snapshot = successor(parent, operation_id=child_id)
            child_op = WorkspaceOperation(id=child_id, kind="quality.review",
                expected_revision=3, result_revision=3,
                **{**common, "anchor_position": offset + 3, "idempotency_key_hash": child_id.hex * 2})
            session.add(child_op)
            await session.flush()
            await append_public_review_evidence(session, operation_id=child_id,
                attempt_token="private-test-lease", fencing_token=1,
                receipt=child_receipt, evidence=child_snapshot)
            child_op.state = "succeeded"
            child_op.result = {"release_review_receipt": child_receipt}
            parent = {"operationId": str(child_id), "receipt": child_receipt}
            successors.append(parent)
        recheck_id = uuid.uuid4()
        recheck_receipt, recheck_snapshot = successor(reviewed, operation_id=recheck_id,
                                                     base_kind="release-recheck-transition")
        recheck_op = WorkspaceOperation(id=recheck_id, kind="quality.review", expected_revision=3,
            result_revision=3, **{**common, "anchor_position": 40, "idempotency_key_hash": recheck_id.hex * 2})
        session.add(recheck_op)
        await session.flush()
        await append_public_review_evidence(session, operation_id=recheck_id,
            attempt_token="private-test-lease", fencing_token=1, receipt=recheck_receipt, evidence=recheck_snapshot)
        recheck_op.state = "succeeded"
        recheck_op.result = {"release_review_receipt": recheck_receipt}
        await session.commit()
    async with factory() as session:
        async def load_review(item, **extra):
            return await load_public_quality_evidence(session, **{**lookup,
                "receipt_digest": item["receipt"]["receiptDigest"],
                "operation_id": uuid.UUID(item["operationId"]), **extra})
        child = await load_review(successors[0])
        chain = await load_review(successors[30])
        with pytest.raises(ValueError, match="chain_too_deep"):
            await load_review(successors[31])
        with pytest.raises(ValueError, match="chain_cycle"):
            await load_review(successors[0], _visited_operations=(uuid.UUID(successors[0]["operationId"]),))
        recheck = await load_review({"operationId": str(recheck_id), "receipt": recheck_receipt})
    followed = project_persisted_authored_quality_receipt_v3(**{**kwargs, "review_evidence_record": chain})
    assert followed.release_readiness == "ready"
    assert followed.authoring.evidence.output_digest == after.authoring.evidence.output_digest
    assert followed.visual_review.evidence.output_digest == chain["receipt"]["evidenceDigest"]
    rebound = project_persisted_authored_quality_receipt_v3(**{**kwargs, "operation_id": "export:successor",
        "artifact_digest": "sha256:" + "0" * 64, "review_evidence_record": chain})
    assert rebound.authoring.evidence.input_digest != followed.authoring.evidence.input_digest
    for missing in ({**child, "baseEvidenceRecord": None}, recheck):
        blocked = project_persisted_authored_quality_receipt_v3(**{**kwargs, "review_evidence_record": missing})
        assert blocked.authoring.status == blocked.geometry_font.status == "failed"
        assert blocked.visual_review.status == "passed" and blocked.release_readiness == "blocked"
    for key, invalid in (("ownerId", str(uuid.uuid4())), ("ownerScope", "other"),
                         ("presentationId", str(uuid.uuid4())), ("resultRevision", 2)):
        corrupt = deepcopy(child)
        corrupt["baseEvidenceRecord"][key] = invalid
        with pytest.raises(ValueError, match="chain_binding_mismatch"):
            project_persisted_authored_quality_receipt_v3(**{**kwargs, "review_evidence_record": corrupt})
    corrupt = deepcopy(child)
    corrupt["baseEvidenceRecord"]["evidence"]["postReviewGate"]["issues"] = [{"code": "clipped"}]
    with pytest.raises(ValueError, match="checker_digest_mismatch"):
        project_persisted_authored_quality_receipt_v3(**{**kwargs, "review_evidence_record": corrupt})
    async with factory() as session:
        (await session.get(WorkspaceOperation, review_id)).state = "failed"
        await session.commit()
        child_without_completed_root = await load_public_quality_evidence(session, **{**lookup,
            "receipt_digest": successors[0]["receipt"]["receiptDigest"],
            "operation_id": uuid.UUID(successors[0]["operationId"])})
        assert child_without_completed_root["baseEvidenceRecord"] is None
    await engine.dispose()


def test_export_adapter_accepts_registry_bound_hybrid_readback() -> None:
    element = SceneTextElement(
        kind="text",
        element_id="title.main",
        z_order=1,
        bounds=Rect(x=40, y=40, width=600, height=80),
        semantic_role="title",
        editability=SceneEditability(text=True, geometry=True, style=True),
        text="Registry-bound export",
    )
    scene = seal_deck_scene(
        deck_id="deck:registry-export",
        revision=1,
        composition_digest="sha256:" + "5" * 64,
        source_digest="sha256:" + "6" * 64,
        hybrid_profile_id=EDITABLE_HYBRID_PROFILE_ID,
        slides=[
            seal_slide_scene(
                slide_id="slide:registry-export",
                revision=1,
                elements=[element],
                hybrid_export=SlideHybridExportPlan(
                    owner_decisions=[
                        SceneOwnerDecision(
                            owner_id=element.element_id,
                            decision="nativeEditable",
                            capability_id="editable.text.ko-en.v1",
                        )
                    ]
                ),
            )
        ],
        capabilities=scene_capabilities(),
    )
    scene_payload = scene.model_dump(mode="json", by_alias=True)
    master_payload = {
        "slideSizePx": {"width": 960, "height": 540},
        "tokens": {"color.background": "#F7F9FC"},
        "furniture": [],
        "fontManifest": {"files": []},
    }
    _content, native_receipt = materialize_scene_pptx(
        scene_payload,
        [],
        master_payload,
    )
    # Keep this adapter test scoped to the registry/readback seam.  Native text
    # style/auto-fit parity has its own OOXML tests and is intentionally removed
    # from the synthetic worker receipt below.
    actual_element = native_receipt["scene"]["slides"][0]["elements"][0]
    native_receipt["scene"]["slides"][0]["elements"][0] = {
        key: actual_element[key]
        for key in ("elementId", "kind", "textDigest", "renderedBounds")
    }
    receipt = {
        **native_receipt,
        "validation": "candidate-reopened",
        "candidateDigestVerified": True,
        "package": {
            "macroDetected": False,
            "quarantineReasons": [],
            "slideCount": 1,
        },
        "preservation": {
            "silentLossCount": 0,
            "missingParts": [],
            "unexpectedChangedParts": [],
        },
        "master": {
            "furnitureCount": 0,
            "logoCount": 0,
            "pageNumberCount": 0,
            "fontFileCount": 0,
            "duplicateFurnitureCount": 0,
            "fontFilesMode": "REFERENCE_METADATA_VERIFIED",
        },
        "slides": [
            {
                "slideId": scene_payload["slides"][0]["slideId"],
                "slideDigest": scene_payload["slides"][0]["slideDigest"],
                "elementIds": ["title.main"],
                "rasterIslandIds": [],
                "rasterOwnerIds": [],
            }
        ],
    }
    _assert_worker_readback(
        receipt,
        {"master": master_payload, "slides": [{}], "scene": scene_payload},
    )
