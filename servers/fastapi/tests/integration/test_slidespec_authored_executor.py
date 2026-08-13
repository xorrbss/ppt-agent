"""Production-path SlideSpec canary with real browser DOM and real SQLite CAS."""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel, select

from models.slidespec_v1 import (
    SLIDESPEC_SCHEMA,
    AssetManifest,
    Bounds,
    ContentNode,
    FontManifest,
    LayoutNode,
    Provenance,
    RepairPlan,
    RenderRequest,
    RendererManifest,
    SlideDigests,
    SlideSpec,
)
from models.sql.presentation import PresentationModel
from models.sql.presentation_version import PresentationVersionModel
from models.sql.slide import SlideModel
from models.sql.slidespec_artifact_bundle import SlideSpecArtifactBundle
from models.sql.slidespec_publication_attempt import SlideSpecPublicationAttempt
from services import slidespec_authored_executor as executor
from services.slidespec_artifact_store import (
    build_slidespec_artifact_bundle,
    load_active_slidespec_bundle,
    publish_slidespec_bundle_cas,
)
from utils.authored_ocr_quality import OcrQualityResult
from utils.llm_calls.critique_slide import (
    StyleComparisonCritique,
    StyleComparisonDimension,
)
from utils.slide_capture import find_chrome
from utils.slidespec_core import ZERO_DIGEST, seal_slidespec


def test_proposal_llm_can_be_disabled_without_disabling_vqa(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SLIDESPEC_REPAIR_PROPOSAL_LLM_ENABLED", "0")
    assert executor._proposal_llm_enabled() is False

    monkeypatch.setenv("SLIDESPEC_REPAIR_PROPOSAL_LLM_ENABLED", "true")
    assert executor._proposal_llm_enabled() is True


def _request(slide_id: uuid.UUID) -> RenderRequest:
    spec = seal_slidespec(
        SlideSpec(
            schemaUri=SLIDESPEC_SCHEMA,
            specRevision=1,
            slideId=str(slide_id),
            content=(
                ContentNode(
                    semanticId=f"sem:{slide_id}/title",
                    kind="title",
                    text="PAGE 11 TOP CLIPPING CANARY",
                ),
            ),
            layout=LayoutNode(
                layoutId=f"lay:{slide_id}/root",
                kind="canvas",
                children=(
                    LayoutNode(
                        layoutId=f"lay:{slide_id}/title",
                        kind="content",
                        contentSemanticId=f"sem:{slide_id}/title",
                        bounds=Bounds(x=0.1, y=0.02, width=0.8, height=0.03),
                    ),
                ),
            ),
            provenance=Provenance(source="native"),
            digests=SlideDigests(
                content=ZERO_DIGEST,
                hierarchy=ZERO_DIGEST,
                importantTokens=ZERO_DIGEST,
                spec=ZERO_DIGEST,
            ),
        )
    )
    empty = "sha256:" + "0" * 64
    return RenderRequest(
        spec=spec,
        renderer=RendererManifest(
            name="slidespec-html", version="1", runtime="browser"
        ),
        fonts=FontManifest(digest=empty),
        assets=AssetManifest(digest=empty),
    )


async def _run_executor_canary(
    tmp_path, monkeypatch, *, fail_postcommit_verification: bool = False
) -> None:
    database = tmp_path / "executor.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database}")
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync: SQLModel.metadata.create_all(
                sync,
                tables=[
                    PresentationModel.__table__,
                    SlideModel.__table__,
                    PresentationVersionModel.__table__,
                    SlideSpecArtifactBundle.__table__,
                    SlideSpecPublicationAttempt.__table__,
                ],
            )
        )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    presentation_id = uuid.uuid4()
    target_id = uuid.uuid4()

    async with sessions() as session:
        session.add(
            PresentationModel(
                id=presentation_id,
                content="tracked synthetic page-11 executor canary",
                n_slides=11,
                language="en",
                mode="authored",
            )
        )
        slides = [
            SlideModel(
                id=target_id if index == 10 else uuid.uuid4(),
                presentation=presentation_id,
                layout_group="authored",
                layout="authored",
                index=index,
                content={"blocks": []},
                html_content=f"<section>legacy slide {index + 1}</section>",
                properties={"fixture": "synthetic-page-11"} if index == 10 else {},
            )
            for index in range(11)
        ]
        session.add_all(slides)
        await session.flush()
        request = _request(target_id)
        imported = build_slidespec_artifact_bundle(
            presentation_id=presentation_id,
            slide=slides[10],
            route="slidespec_active",
            status="imported",
            render_request=request,
            evidence={
                "fixture": "synthetic-page-11",
                "insertion_position": 10,
                "source": "tracked-integration-test",
            },
        )
        await publish_slidespec_bundle_cas(
            session, slide=slides[10], bundle=imported
        )
        await session.commit()

    ocr_calls = 0

    async def fake_ocr(_image: bytes, *, expected_text: str, **_kwargs):
        nonlocal ocr_calls
        ocr_calls += 1
        return OcrQualityResult(
            available=True,
            status="success",
            failure_reason=None,
            text=expected_text,
            text_digest="sha256:" + "1" * 64,
            metrics={},
            calls=1,
            cache_hits=0,
            elapsed_ms=0.1,
        )

    async def fake_vqa(*_args, **_kwargs):
        return [
            StyleComparisonCritique(
                needs_fix=False,
                issues=[],
                preference="candidate",
                target_improved=True,
                confidence=0.99,
                dimensions=[
                    StyleComparisonDimension(
                        dimension=dimension,
                        verdict="better" if dimension == "target-defect" else "same",
                        detail="candidate fixes the target without a protected regression",
                    )
                    for dimension in (
                        "target-defect",
                        "content",
                        "readability",
                        "hierarchy",
                        "alignment",
                        "spacing",
                        "style",
                    )
                ],
            )
        ]

    async def unexpected_llm(*_args, **_kwargs):
        raise AssertionError("deterministic page-11 repair must not call the LLM")

    monkeypatch.setenv("SLIDESPEC_REPAIR_CANARY", "100")
    monkeypatch.setenv("SLIDESPEC_REPAIR_APPLY", "true")
    monkeypatch.delenv("SLIDESPEC_KILL_SWITCH", raising=False)
    monkeypatch.delenv("SLIDESPEC_REPAIR_SHADOW", raising=False)
    monkeypatch.setattr(executor, "inspect_authored_ocr", fake_ocr)
    monkeypatch.setattr(executor, "critique_authored_style_comparison", fake_vqa)
    monkeypatch.setattr(executor, "propose_slidespec_repair", unexpected_llm)
    real_run_one = executor._run_one
    pass_controls: list[tuple[object, object]] = []

    async def capture_run_controls(**kwargs):
        pass_controls.append((kwargs["publish_gate"], kwargs["iteration_guard"]))
        return await real_run_one(**kwargs)

    monkeypatch.setattr(executor, "_run_one", capture_run_controls)
    cache_epoch_before = executor._BASELINE_INSPECTION_EPOCH.get(str(target_id), 0)
    if fail_postcommit_verification:
        real_load_active = executor.load_active_slidespec_bundle
        load_calls = 0

        async def fail_new_session_readback(session, slide):
            nonlocal load_calls
            load_calls += 1
            if load_calls == 2:
                raise executor.SlideSpecArtifactError(
                    "artifact.publish.readback_mismatch"
                )
            return await real_load_active(session, slide)

        monkeypatch.setattr(
            executor, "load_active_slidespec_bundle", fail_new_session_readback
        )

    async with sessions() as session:
        slides = list(
            (
                await session.execute(
                    select(SlideModel)
                    .where(SlideModel.presentation == presentation_id)
                    .order_by(SlideModel.index)
                )
            ).scalars()
        )
        result = await executor.execute_slidespec_authored_review(
            presentation_id=presentation_id,
            slides=slides,
            positions=tuple(range(11)),
            opt_in=True,
            owner_id=None,
            session=session,
        )

    assert result.claimed_positions == (10,)
    expected_fixed = 0 if fail_postcommit_verification else 1
    assert result.data["fixed_count"] == expected_fixed
    assert result.data["fixed_slide_indices"] == (
        [] if fail_postcommit_verification else [10]
    )
    assert result.data["metrics"]["publish_active"] == (
        0 if fail_postcommit_verification else 1
    )
    assert result.data["metrics"]["postverify_passed"] == (
        0 if fail_postcommit_verification else 1
    )
    assert result.data["metrics"]["rolled_back"] == (
        1 if fail_postcommit_verification else 0
    )
    assert result.data["metrics"]["deterministic_resolved"] == (
        0 if fail_postcommit_verification else 1
    )
    assert result.data["metrics"]["llm_calls"] == 0
    assert result.data["metrics"]["render_calls"] >= 1
    assert result.data["metrics"]["dom_calls"] >= 1
    assert result.data["metrics"]["ocr_calls"] >= 1
    assert result.data["metrics"]["vqa_calls"] >= 1
    assert result.data["metrics"]["total_ms"] > 0
    assert result.data["metrics"]["stage_timings_ms"]
    assert result.data["metrics"]["pattern_preinspection_count"] == 1
    assert result.data["metrics"]["pattern_preinspection_cache_misses"] == 1
    assert result.data["metrics"]["preinspection_render_count"] == 1
    assert result.data["detailed_issue_count"] >= 1
    assert result.data["resolved_issue_count"] == (
        0 if fail_postcommit_verification else result.data["detailed_issue_count"]
    )
    assert result.data["remaining_slide_count"] == int(
        fail_postcommit_verification
    )
    assert pass_controls
    assert len({id(lock) for lock, _guard in pass_controls}) == 1
    assert len({id(guard) for _lock, guard in pass_controls}) == 1
    assert all(isinstance(lock, asyncio.Lock) for lock, _guard in pass_controls)
    assert executor._BASELINE_INSPECTION_EPOCH.get(str(target_id), 0) == (
        cache_epoch_before + (0 if fail_postcommit_verification else 1)
    )
    assert result.data["slide_results"][0]["route"] == "slidespec_active"
    assert result.data["slide_results"][0]["path"] == "deterministic"
    assert result.data["slide_results"][0]["applied"] is (
        not fail_postcommit_verification
    )
    if fail_postcommit_verification:
        assert result.data["slide_results"][0]["publish_status"] == "active"
        assert result.data["slide_results"][0]["post_verify_status"] == "failed"
    else:
        slide_result = result.data["slide_results"][0]
        assert slide_result["accepted"] is True
        assert slide_result["apply_status"] == "committed"
        assert slide_result["publish_status"] == "active"
        assert slide_result["post_verify_status"] == "passed"
        assert slide_result["target_evidence"]
        assert all(
            item["qid"] and item["constraint_id"]
            for item in slide_result["target_evidence"]
        )
        assert slide_result["element_changes"]
        assert all(
            item["target"] and item["operation"]
            for item in slide_result["element_changes"]
        )
    assert result.data["slide_results"][0]["telemetry"]["llm_calls"] == 0
    assert result.data["slide_results"][0]["telemetry"]["repair_costs"]
    assert ocr_calls >= 2

    async with sessions() as session:
        slide = await session.get(SlideModel, target_id)
        assert slide is not None
        active = await load_active_slidespec_bundle(session, slide)
        assert active is not None
        if fail_postcommit_verification:
            assert active.id == imported.id
            assert active.status == "imported"
            assert active.spec_revision == 1
        else:
            assert active.status == "published"
            assert active.spec_revision == 2
            assert active.id != imported.id
        attempts = list(
            (
                await session.execute(
                    select(SlideSpecPublicationAttempt)
                    .where(SlideSpecPublicationAttempt.slide_id == target_id)
                    .order_by(SlideSpecPublicationAttempt.created_at)
                )
            ).scalars()
        )
        assert [attempt.state for attempt in attempts] == (
            ["staged", "activation_committed", "failed", "rolled_back"]
            if fail_postcommit_verification
            else ["staged", "activation_committed", "verified"]
        )
        assert len({attempt.attempt_id for attempt in attempts}) == 1
        if fail_postcommit_verification:
            assert attempts[-1].reason_code == "artifact.publish.readback_mismatch"
        versions = list(
            (
                await session.execute(
                    select(PresentationVersionModel).where(
                        PresentationVersionModel.presentation_id == presentation_id
                    )
                )
            ).scalars()
        )
        assert len(versions) == 1
        assert versions[0].slides[10]["html_content"] == imported.html

    await engine.dispose()


@pytest.mark.skipif(find_chrome() is None, reason="headless Chrome is required")
def test_page_11_executor_publishes_with_cas_snapshot_and_readback(
    tmp_path, monkeypatch
) -> None:
    asyncio.run(_run_executor_canary(tmp_path, monkeypatch))


@pytest.mark.skipif(find_chrome() is None, reason="headless Chrome is required")
def test_page_11_executor_rolls_back_postcommit_verification_failure(
    tmp_path, monkeypatch
) -> None:
    asyncio.run(
        _run_executor_canary(
            tmp_path,
            monkeypatch,
            fail_postcommit_verification=True,
        )
    )


def test_plan_qualified_candidate_state_keys_do_not_alias_identical_renders() -> None:
    render_hash = "sha256:" + "a" * 64
    states = {}
    first = SimpleNamespace(
        plan=SimpleNamespace(plan_id="rp_rule_one"), decision="accepted"
    )
    second = SimpleNamespace(
        plan=SimpleNamespace(plan_id="rp_rule_two"), decision="rejected"
    )

    first_key = executor._store_candidate_state(states, render_hash, first)
    second_key = executor._store_candidate_state(states, render_hash, second)

    assert first_key != second_key
    assert len(states) == 2
    assert states[first_key].decision == "accepted"
    assert states[second_key].decision == "rejected"


def test_slide_execution_controls_are_isolated_per_slide_and_reused_by_pass() -> None:
    locks, guards = executor._slide_execution_controls(
        (3, 7), max_incremental_repairs=12
    )

    assert locks[3] is locks[3]
    assert guards[3] is guards[3]
    assert locks[3] is not locks[7]
    assert guards[3] is not guards[7]
    assert guards[3].max_incremental_applies == 12


def test_dry_run_removes_publication_authority_even_when_apply_is_enabled(
    monkeypatch,
) -> None:
    monkeypatch.setenv("SLIDESPEC_REPAIR_CANARY", "100")
    monkeypatch.setenv("SLIDESPEC_REPAIR_APPLY", "true")
    monkeypatch.delenv("SLIDESPEC_KILL_SWITCH", raising=False)

    rollout = executor._execution_rollout(
        uuid.uuid4(),
        position=10,
        opt_in=True,
        owner_id=None,
        dry_run=True,
    )

    assert rollout.should_run_repair is True
    assert rollout.shadow_mode is True
    assert rollout.publisher_allowed is False
    assert rollout.apply_enabled is False
    assert rollout.reason_code == "legacy_preserved.dry_run"


def test_successful_publication_epoch_changes_the_baseline_cache_key() -> None:
    slide_id = uuid.uuid4()
    request = _request(slide_id)
    epoch_key = str(slide_id)
    previous = executor._BASELINE_INSPECTION_EPOCH.get(epoch_key)
    try:
        before = executor._baseline_inspection_cache_key(
            slide_id=slide_id,
            request=request,
            html="<section>baseline</section>",
            adjacent_digest="adjacent:stable",
        )
        executor._invalidate_baseline_inspection(slide_id)
        after = executor._baseline_inspection_cache_key(
            slide_id=slide_id,
            request=request,
            html="<section>baseline</section>",
            adjacent_digest="adjacent:stable",
        )
    finally:
        if previous is None:
            executor._BASELINE_INSPECTION_EPOCH.pop(epoch_key, None)
        else:
            executor._BASELINE_INSPECTION_EPOCH[epoch_key] = previous

    assert before != after


def test_canonical_terminal_prefers_selected_outcome() -> None:
    selected = {
        "plan_id": "rp_rule_selected",
        "selected": True,
        "validation_status": "accepted",
        "apply_status": "committed",
        "publish_status": "active",
        "post_verify_status": "passed",
        "diagnostics": {
            "target_evidence": [
                {"qid": "q-target", "constraint_id": "con:target"}
            ],
            "repair_operations": [
                {
                    "operation": "translateWithinParent",
                    "target": "lay:target",
                    "before": {"y": 0.9},
                    "after": {"y": 0.8},
                }
            ],
        },
    }
    rejected = {
        "plan_id": "rp_rule_rejected",
        "selected": False,
        "validation_status": "rejected",
        "publish_status": "not_started",
    }

    assert executor._canonical_terminal_outcome((selected, rejected)) is selected
    projection = executor._canonical_outcome_projection((selected, rejected))
    assert projection["accepted"] is True
    assert projection["apply_status"] == "committed"
    assert projection["publish_status"] == "active"
    assert projection["post_verify_status"] == "passed"
    assert projection["target_evidence"] == selected["diagnostics"][
        "target_evidence"
    ]
    assert projection["element_changes"] == selected["diagnostics"][
        "repair_operations"
    ]


def test_canonical_projection_unions_incremental_selected_evidence() -> None:
    first = {
        "selected": True,
        "validation_status": "accepted",
        "publish_status": "active",
        "post_verify_status": "passed",
        "diagnostics": {
            "target_evidence": [{"qid": "q-first", "constraint_id": "con:first"}],
            "repair_operations": [{"operation": "setBounds", "target": "lay:first"}],
        },
    }
    second = {
        "selected": True,
        "validation_status": "accepted",
        "apply_status": "committed",
        "publish_status": "active",
        "post_verify_status": "passed",
        "diagnostics": {
            "target_evidence": [{"qid": "q-second", "constraint_id": "con:second"}],
            "repair_operations": [{"operation": "setBounds", "target": "lay:second"}],
        },
    }

    projection = executor._canonical_outcome_projection((first, second))

    assert projection["terminal"] is second
    assert [item["qid"] for item in projection["target_evidence"]] == [
        "q-first",
        "q-second",
    ]
    assert [item["target"] for item in projection["element_changes"]] == [
        "lay:first",
        "lay:second",
    ]


def test_paired_vqa_context_includes_authoritative_chrome_geometry() -> None:
    request = _request(uuid.uuid4())
    plan = RepairPlan.model_validate(
        {
            "$schema": "urn:ppt-agent:repair-plan:1.0",
            "planId": "rp_rule_context",
            "slideId": request.spec.slideId,
            "baseSpecRevision": request.spec.specRevision,
            "baseSpecHash": request.spec.digests.spec,
            "expectedContentDigest": request.spec.digests.content,
            "strategy": "minimal_change",
            "operations": [
                {
                    "op": "translateWithinParent",
                    "target": request.spec.layout.children[0].layoutId,
                    "value": {"x": 0, "y": 0.01},
                }
            ],
            "proposedBy": {
                "kind": "rule_engine",
                "policyVersion": "test",
            },
        }
    )
    context, payload = executor._paired_vqa_context(
        request,
        plan,
        verified_geometry={
            "sourceProjectionRoundTrip": True,
            "remainingHighMediumGeometryIssueCount": 0,
        },
    )

    assert payload["verifiedGeometry"]["sourceProjectionRoundTrip"] is True
    assert "Chrome geometry facts are authoritative" in context


def test_executor_conflict_footprint_resolves_layout_ancestry_and_space_pool() -> None:
    request = _request(uuid.uuid4())
    target = request.spec.layout.children[0]
    plan = RepairPlan.model_validate(
        {
            "$schema": "urn:ppt-agent:repair-plan:1.0",
            "planId": "rp_rule_footprint",
            "slideId": request.spec.slideId,
            "baseSpecRevision": request.spec.specRevision,
            "baseSpecHash": request.spec.digests.spec,
            "expectedContentDigest": request.spec.digests.content,
            "strategy": "minimal_change",
            "operations": [
                {
                    "op": "translateWithinParent",
                    "target": target.contentSemanticId,
                    "value": {"x": 0, "y": 0.01},
                }
            ],
            "proposedBy": {"kind": "rule_engine", "policyVersion": "test"},
        }
    )

    footprint = executor._repair_conflict_footprint(request, plan)

    assert footprint.nodes == frozenset({target.layoutId})
    assert request.spec.layout.layoutId in footprint.ancestor_cones
    assert footprint.parent_space_pools == frozenset(
        {request.spec.layout.layoutId}
    )
