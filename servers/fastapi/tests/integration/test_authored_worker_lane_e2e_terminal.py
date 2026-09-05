"""ARCH-06 worker-lane e2e: terminal fallback at the author stage.

Scenario 3 of the brief; harness in ``tests/support/authored_worker_lane_harness.py``.
"""

from __future__ import annotations

import asyncio

import pytest

from models.sql.presentation import (
    PRESENTATION_LIFECYCLE_PUBLISHED,
    PRESENTATION_LIFECYCLE_STAGING,
)
from tests.support.authored_worker_lane_harness import (
    _FORBIDDEN_DECLARATION,
    PolicyClock,
    StubProvider,
    WaveRecorder,
    _default_contract,
    _run_worker_lane,
    _slide_document,
)

# --- 3: terminal fallback at the author stage --------------------------------------


def test_persistent_renderer_policy_violation_fails_the_deck_when_flag_publishing_is_off(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("AUTHORED_PUBLISH_FLAGGED_SLIDES", "false")
    contract = _default_contract()
    slide_count = 6
    terminal_index = 3
    recorder = WaveRecorder(PolicyClock())
    recorder.install(monkeypatch)

    def html_for(index: int, _ordinal: int) -> str:
        if index == terminal_index:
            return _slide_document(contract, index, body_style_extra=_FORBIDDEN_DECLARATION)
        return _slide_document(contract, index)

    provider = StubProvider(html_for=html_for)
    outcome = asyncio.run(
        _run_worker_lane(
            tmp_path,
            monkeypatch,
            slide_count=slide_count,
            provider=provider,
            contract=contract,
        )
    )

    assert outcome.status["status"] == "error", outcome.status
    failure = outcome.status["failure"]
    assert failure["code"] == "authored_slide_author_failed"
    assert failure["stage"] == "author"
    assert failure["slide_number"] == terminal_index + 1
    # The per-slide call budget (initial + one recovery) was spent on that
    # slide only; every other slide was authored once and admitted.
    assert provider.calls_by_index[terminal_index] == 2
    assert all(
        count == 1 for index, count in provider.calls_by_index.items() if index != terminal_index
    )
    assert outcome.presentation is not None
    assert outcome.presentation.lifecycle_status == PRESENTATION_LIFECYCLE_STAGING
    assert outcome.slides == []
    assert not list((tmp_path / "app-data" / "exports").glob("*.pptx"))


@pytest.mark.xfail(
    strict=True,
    reason=(
        "pending: the author-stage rescue (services/authored_author_stage_rescue.py, "
        "reason author:attempts_exhausted) is wired into process_slide only; the "
        "batch wave path in _preauthor_missing_slides still fails the deck"
    ),
)
def test_persistent_author_failure_publishes_a_flagged_placeholder_when_enabled(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("AUTHORED_PUBLISH_FLAGGED_SLIDES", "true")
    contract = _default_contract()
    slide_count = 6
    terminal_index = 3
    terminal_html = _slide_document(contract, terminal_index, omit_header=True)

    def html_for(index: int, _ordinal: int) -> str:
        # The header band is mandatory for this role; dropping it on every
        # attempt is the 2026-09-05 production failure the rescue exists for.
        if index == terminal_index:
            return terminal_html
        return _slide_document(contract, index)

    provider = StubProvider(html_for=html_for)
    outcome = asyncio.run(
        _run_worker_lane(
            tmp_path,
            monkeypatch,
            slide_count=slide_count,
            provider=provider,
            contract=contract,
        )
    )

    assert outcome.status["status"] == "completed", outcome.status
    assert outcome.job.state == "published"
    assert outcome.presentation.lifecycle_status == PRESENTATION_LIFECYCLE_PUBLISHED
    gate = outcome.presentation.theme["authoredQualityGate"]
    flagged = gate["flaggedSlides"]
    assert [entry["index"] for entry in flagged] == [terminal_index]
    assert flagged[0]["reason"] == "author:attempts_exhausted"
    assert flagged[0]["remakeAttempted"] is True
    receipt = outcome.receipts[-1].to_dict()
    assert [entry["index"] for entry in receipt["flaggedSlides"]] == [terminal_index]
    # Two ordinary calls plus the one simplified rescue call, on that slide only.
    assert provider.calls_by_index[terminal_index] == 3
    assert all(
        count == 1 for index, count in provider.calls_by_index.items() if index != terminal_index
    )
    by_index = {slide.index: slide for slide in outcome.slides}
    assert sorted(by_index) == list(range(slide_count))
    assert by_index[terminal_index].html_content != terminal_html
    assert f"Slide {terminal_index + 1}" in by_index[terminal_index].html_content
    for index in range(slide_count):
        if index != terminal_index:
            assert 'data-header-part="body"' in by_index[index].html_content


