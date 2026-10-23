"""The staged SVG page loop publishes page-quality outcomes as page flags.

Drives ``collect._collect_stages`` with its plan preflight and the advisory
purchase isolated: a page that did not pass its gate, or that a stage review
names, must not stop the partition or the deck.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

from services.generation_core.drivers.svg import collect
from services.generation_core.drivers.svg import quality as deck_quality

PLAN = {"partitions": [[0, 1], [2]],
        "source_lineage": {"execution_authority": {"owner_id": "owner", "tenant_id": "tenant"}}}


def _page(index, *, passed=True, terminal=None):
    payload = {"svg_content": f"<svg data-index='{index}'/>", "png_sha256": f"png-{index}",
               "quality": {"passed": passed, "verified": False, "failures": []}}
    if terminal is not None:
        payload["terminal"] = terminal
    return payload, None


def _advisory(issues_by_local_index):
    slides = [{"index": i, "issues": list(issues)} for i, issues in issues_by_local_index.items()]
    return {"vision": {"slides": slides, "promptVersion": "v1", "model": "test-model"}}


def _run(monkeypatch, pages, advisories, review_stage=None):
    saved = {}

    async def checkpoint(_job, index, *, payload=None):
        if payload is not None:
            saved[index] = deepcopy(payload)
        return deepcopy(saved.get(index, {}))

    async def tracked_page(index):
        return pages[index]

    monkeypatch.setattr(collect, "_stage_preflight", AsyncMock(return_value={}))
    advisory_mock = AsyncMock(side_effect=list(advisories))
    monkeypatch.setattr(deck_quality, "deck_advisory", advisory_mock)
    ctx = SimpleNamespace(count=len(pages), semaphore=asyncio.Semaphore(len(pages)))
    job = SimpleNamespace(id=uuid.uuid4(), workspace_operation_id=uuid.uuid4(), request_payload={})
    result = asyncio.run(collect._collect_stages(ctx, PLAN, tracked_page, job, checkpoint, None, None,
                                                 review_stage, page_failures={}))
    return result, advisory_mock, saved


def test_a_rescued_page_that_did_not_pass_does_not_stop_its_partition(monkeypatch):
    terminal = {"index": 1, "reason": "author:placeholder", "remakeAttempted": False,
                "remakeOutcome": "svg_failure_placeholder"}
    pages = [_page(0), _page(1, passed=False, terminal=terminal), _page(2)]
    (published, record), advisory_mock, saved = _run(monkeypatch, pages, [_advisory({}), _advisory({})])

    assert [payload["svg_content"] for payload, _ in published] == [p["svg_content"] for p, _ in pages]
    assert published[1][0]["terminal"] == terminal
    assert advisory_mock.await_count == 2  # the second partition still ran
    assert [stage["status"] for stage in saved[0]["trusted_stage_execution"]["stages"]] == ["accepted"] * 2
    assert len(record["pageDigests"]) == 3


def test_a_measured_page_without_a_rescue_is_left_to_page_classification(monkeypatch):
    pages = [_page(0), _page(1), _page(2, passed=False)]
    (published, _record), _advisory_mock, _saved = _run(monkeypatch, pages, [_advisory({}), _advisory({})])

    assert published[2][0]["quality"]["passed"] is False
    assert deck_quality.REVIEW_FLAG_KEY not in published[2][0]


def test_a_page_the_stage_review_names_is_flagged_with_a_design_reason(monkeypatch):
    pages = [_page(0), _page(1), _page(2)]
    advisories = [_advisory({1: ["text_overlap", "illegible"]}), _advisory({0: ["broken_figure"]})]
    (published, record), advisory_mock, _saved = _run(monkeypatch, pages, advisories)

    assert advisory_mock.await_count == 2
    assert deck_quality.REVIEW_FLAG_KEY not in published[0][0]
    assert published[1][0][deck_quality.REVIEW_FLAG_KEY] == {
        "index": 1, "reason": "design:illegible,text_overlap", "remakeAttempted": False,
        "remakeOutcome": "not_attempted"}
    # Stage-local index 0 of the second partition is deck page 2.
    assert published[2][0][deck_quality.REVIEW_FLAG_KEY]["reason"] == "design:broken_figure"
    assert [slide["index"] for slide in record["vision"]["slides"]] == [1, 2]


def test_a_stage_review_flag_merges_into_an_existing_review_flag(monkeypatch):
    payload, png = _page(0)
    payload[deck_quality.REVIEW_FLAG_KEY] = {"index": 0, "reason": "source_visibility",
                                             "remakeAttempted": False, "remakeOutcome": "not_attempted"}
    pages = [(payload, png), _page(1), _page(2)]
    (published, _record), _advisory_mock, _saved = _run(
        monkeypatch, pages, [_advisory({0: ["illegible"]}), _advisory({})])

    flag = published[0][0][deck_quality.REVIEW_FLAG_KEY]
    assert flag["reason"] == "source_visibility"
    assert flag["reasons"] == ["source_visibility", "design:illegible"]


def test_a_stage_review_without_named_issues_flags_nothing(monkeypatch):
    pages = [_page(0), _page(1), _page(2)]
    (published, _record), _advisory_mock, _saved = _run(monkeypatch, pages, [_advisory({0: []}), _advisory({})])

    assert all(deck_quality.REVIEW_FLAG_KEY not in payload for payload, _ in published)


def test_an_explicit_stage_review_refusal_still_stops_the_deck(monkeypatch):
    from services.authored_checkpoint_errors import AuthoredStageError
    import pytest

    pages = [_page(0), _page(1), _page(2)]
    with pytest.raises(AuthoredStageError) as refused:
        _run(monkeypatch, pages, [_advisory({}), _advisory({})], review_stage=AsyncMock(return_value=False))
    assert refused.value.code == "svg_stage_review_pending"
