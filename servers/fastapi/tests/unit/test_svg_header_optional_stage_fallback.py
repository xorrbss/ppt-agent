"""A failure of the optional header stage reduces the header instead of failing the deck.

The header design stage is optional: a reduced (or free) header is proven for
every page before any of it is bought. A provider error or timeout there, or
an exception in a representative page, used to fail the whole deck. Under the
v2 fallback policy it takes the reduced header, while the failed provider step
keeps its unknown state so a resume never buys it again.
"""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services import svg_common_header_design_plan as policy
from services import svg_common_header_generation as generation
from services.svg_common_header_design_checkpoint import read_update_header_design

from test_svg_header_capacity_policy import _inputs
from test_svg_header_flow_body_defects import _PNG, _candidate, _page


def _selection(payload):
    return {"result": {"selection_input_digest": payload["selection_input_digest"],
        "candidate_selection": {"selected_id": "choice-1", "candidate_revision": 1, "reason": "clear"},
        "representative_visual_review": {"candidate_id": "choice-1", "passed": True,
                                         "harmony": "header and body agree", "known_defects": []}}}


def _run(tmp_path, monkeypatch, *, failure, fallback_policy=None, resumes=1):
    """Drive prepare_document_header on a real checkpoint database ``resumes`` times."""
    from test_authored_checkpoint_service import _checkpoint_database, _running_authored_job
    from models.sql.presentation_generation_job import PresentationGenerationJob
    from services.svg_canvas import svg_canvas_for_aspect_ratio

    async def scenario():
        engine, maker = await _checkpoint_database(tmp_path, monkeypatch)
        try:
            _, job = await _running_authored_job(maker, total_slides=1)
            request, _plan, snapshot, outline, _canvas = _inputs(fallback_policy=fallback_policy)
            async with maker() as session:
                row = await session.get(PresentationGenerationJob, job.id)
                row.svg_style_snapshot = deepcopy(snapshot)
                row.authored_outline = outline.model_dump(mode="json")
                session.add(row)
                await session.commit()
            ctx = SimpleNamespace(snapshot=snapshot, canvas=svg_canvas_for_aspect_ratio("16:9"),
                                  header_pages=None, count=1, source_reflow=False,
                                  raster_reconstruction=False, manifests=[{}], planned=[{}])
            monkeypatch.setattr(generation, "preflight_header_selector", lambda: {"model": "fixture"})
            monkeypatch.setattr(generation, "_design_input", lambda *args: {"context": "bound"})
            monkeypatch.setattr(generation, "_design_reference_images", lambda *args: [])
            purchases = []

            def build(payload, *, plan, stage, ordinal, **kwargs):
                async def execute():
                    purchases.append(f"{stage}-{ordinal}")
                    if failure == f"{stage}_timeout":
                        raise TimeoutError("fake provider timeout")
                    if stage == "design":
                        return {"result": {"candidates": [_candidate("first"), _candidate("second")]}}
                    return _selection(payload)
                return execute

            monkeypatch.setattr(generation, "build_header_call", build)
            pages = []

            async def page_runner(candidate_ctx, request_, presentation_id, outline_, language, job_, index,
                                  state, hooks):
                pages.append(candidate_ctx.header_author_attempt_identity["candidate_id"])
                if failure == "representative_exception":
                    raise RuntimeError("representative page failed")
                result = _page(True, body_failures=[])
                result["input_digest"] = generation._page_input_digest(candidate_ctx, request_, outline_,
                                                                       language, index)
                await hooks.checkpoint(job_, index, payload=result)
                return result, _PNG

            arguments = dict(checkpoint=AsyncMock(return_value={}), async_session_maker=maker,
                read_png=lambda payload: _PNG, write_png=None, page_runner=page_runner,
                page_hooks_factory=lambda checkpoint, **kwargs: SimpleNamespace(checkpoint=checkpoint),
                state_factory=lambda: None)
            results, errors = [], []
            for _ in range(resumes):
                try:
                    results.append(await generation.prepare_document_header(
                        ctx, request, job.presentation_id, outline, "en", job, **arguments))
                except Exception as error:  # the deck failure is what is observed
                    errors.append(f"{type(error).__name__}:{error}")
            document = await read_update_header_design(job, session_maker=maker)
            async with maker() as session:
                saved = (await session.get(PresentationGenerationJob, job.id)).svg_style_snapshot
            return results, errors, document, purchases, pages, saved
        finally:
            await engine.dispose()
    return asyncio.run(scenario())


def test_design_timeout_reduces_the_header_and_never_buys_the_design_again(tmp_path, monkeypatch):
    results, errors, document, purchases, pages, saved = _run(
        tmp_path, monkeypatch, failure="design_timeout", resumes=2)
    assert errors == []
    assert [result.snapshot["common_header_selection"]["mode"] for result in results] == ["reduced", "reduced"]
    assert results[0].header_pages[0]["title"] == "Required exact title"
    # One design purchase across the first run and the resume; no page or selection spend.
    assert purchases == ["design-1"] and pages == []
    assert document["steps"]["design-1"]["status"] == "unknown"
    assert document["fallback"]["reason"] == policy.PROVIDER_STEP_FAILED
    assert document["fallback_cause"] == {"exceptionType": "TimeoutError"}
    assert document["status"] == "published"
    assert saved["common_header_selection"]["mode"] == "reduced"
    assert saved["common_header"]["version"] == "svg-common-header/v1"


def test_selection_timeout_reduces_the_header_and_keeps_the_unknown_step(tmp_path, monkeypatch):
    results, errors, document, purchases, pages, _saved = _run(
        tmp_path, monkeypatch, failure="selection_timeout", resumes=2)
    assert errors == []
    assert results[-1].snapshot["common_header_selection"]["mode"] == "reduced"
    assert purchases == ["design-1", "selection-1"] and sorted(pages) == ["first", "second"]
    assert document["steps"]["design-1"]["status"] == "succeeded"
    assert document["steps"]["selection-1"]["status"] == "unknown"
    assert document["fallback"]["reason"] == policy.PROVIDER_STEP_FAILED


def test_representative_page_exception_reduces_the_header(tmp_path, monkeypatch):
    results, errors, document, purchases, pages, _saved = _run(
        tmp_path, monkeypatch, failure="representative_exception", resumes=2)
    assert errors == []
    assert results[-1].snapshot["common_header_selection"]["mode"] == "reduced"
    # The resume publishes the stored fallback; no candidate page or call runs again.
    assert purchases == ["design-1"] and sorted(pages) == ["first", "second"]
    assert document["fallback"]["reason"] == policy.REPRESENTATIVE_PAGE_FAILED
    assert document["fallback_cause"] == {"exceptionType": "RuntimeError"}


def test_a_v1_policy_plan_still_fails_on_a_provider_timeout(tmp_path, monkeypatch):
    results, errors, document, purchases, _pages, _saved = _run(
        tmp_path, monkeypatch, failure="design_timeout", fallback_policy=policy._POLICY_V1, resumes=2)
    assert results == []
    assert errors == ["TimeoutError:fake provider timeout",
                      "ValueError:svg_common_header_provider_result_unknown"]
    assert purchases == ["design-1"]
    assert "fallback" not in document
    assert document["steps"]["design-1"]["status"] == "unknown"


def test_all_policies_validate_and_new_plans_seal_v3():
    request, plan, _snapshot, _outline, _canvas = _inputs()
    assert plan["policy"]["version"] == "svg-header-capacity-fallback/v3"
    assert policy.reduces_optional_stage_failures(plan)
    assert policy.design_input_without_asset_manifest(plan)
    _, v2, _, _, _ = _inputs(fallback_policy=policy._POLICY)
    assert policy.validate_header_design_plan(v2) == v2
    assert policy.reduces_optional_stage_failures(v2)
    assert not policy.design_input_without_asset_manifest(v2)
    _, legacy, _, _, _ = _inputs(fallback_policy=policy._POLICY_V1)
    assert policy.validate_header_design_plan(legacy) == legacy
    assert not policy.reduces_optional_stage_failures(legacy)
    tampered = {**plan, "policy": {**plan["policy"], "optional_stage_failures": []}}
    with pytest.raises(ValueError, match="design_plan_invalid"):
        policy.validate_header_design_plan(tampered)


@pytest.mark.parametrize("statuses, permitted", [
    ({"design-1": "unknown"}, True),
    ({"design-1": "started"}, True),
    ({"design-1": "succeeded", "selection-1": "unknown"}, True),
    ({"design-1": "succeeded"}, False),  # no failed provider step to account for
    ({}, False),
])
def test_provider_step_fallback_requires_the_recorded_unsettled_step(statuses, permitted):
    _, plan, _, _, _ = _inputs()
    document = {"steps": {key: {"status": status} for key, status in statuses.items()}}
    assert policy.permits_header_fallback(plan, policy.PROVIDER_STEP_FAILED, document) is permitted
    _, legacy, _, _, _ = _inputs(fallback_policy=policy._POLICY_V1)
    assert policy.permits_header_fallback(legacy, policy.PROVIDER_STEP_FAILED, document) is False


def test_representative_fallback_requires_settled_provider_steps():
    _, plan, _, _, _ = _inputs()
    assert policy.permits_header_fallback(plan, policy.REPRESENTATIVE_PAGE_FAILED,
                                          {"steps": {"design-1": {"status": "succeeded"}}})
    assert not policy.permits_header_fallback(plan, policy.REPRESENTATIVE_PAGE_FAILED,
                                              {"steps": {"design-1": {"status": "unknown"}}})


def test_an_integrity_conflict_in_a_provider_step_still_fails_the_deck():
    _, plan, _, _, _ = _inputs()
    document = {"steps": {"design-1": {"status": "unknown", "input_digest": "sha256:" + "0" * 64}}}

    async def call():
        return await generation._stage_paid_step(document, key="design-1", stage="design", ordinal=1,
            payload={"changed": True}, images=[], plan=plan, job=SimpleNamespace(), save=AsyncMock())

    with pytest.raises(ValueError, match="svg_common_header_checkpoint_input_conflict"):
        asyncio.run(call())
