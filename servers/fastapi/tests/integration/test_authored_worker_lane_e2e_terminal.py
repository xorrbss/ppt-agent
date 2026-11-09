"""ARCH-06 worker-lane e2e: terminal fallback at the author stage.

Scenario 3 of the brief; harness in ``tests/support/authored_worker_lane_harness.py``.
"""

from __future__ import annotations

import asyncio

from tests.support.authored_worker_lane_harness import (
    assert_lane_refused,
    _FORBIDDEN_DECLARATION,
    PolicyClock,
    StubProvider,
    WaveRecorder,
    _default_contract,
    _run_worker_lane,
    _slide_document,
)

# --- 3: terminal fallback at the author stage --------------------------------------


def test_renderer_policy_violation_lane_is_refused_as_authored_strategy_removed(
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

    # Authored generation was removed on 2026-11-09: this scenario can no
    # longer start, so the lane now proves the refusal.
    assert_lane_refused(outcome, provider)


def test_flagged_placeholder_lane_is_refused_as_authored_strategy_removed(
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

    # Authored generation was removed on 2026-11-09: this scenario can no
    # longer start, so the lane now proves the refusal.
    assert_lane_refused(outcome, provider)

