"""Deck-level icon selection and infographic plan calls carry an explicit call timeout.

Both calls fail open: a timed-out call must take the same None path a provider
failure takes, so the deck is produced without the plan or the icon picks.
"""

from __future__ import annotations

import asyncio

import pytest

from utils.llm_calls import author_infographic_plan as plan_module
from utils.llm_calls import select_deck_icons as icons_module


class _Capture:
    def __init__(self, error: BaseException | None = None, content=None):
        self.kwargs: dict = {}
        self._error = error
        self._content = content if content is not None else {"icons": []}

    async def __call__(self, client, model, **kwargs):
        self.kwargs = kwargs
        if self._error is not None:
            raise self._error
        return self._content


@pytest.fixture
def stub_llm(monkeypatch):
    def install(module, capture: _Capture):
        monkeypatch.setattr(module, "get_client", lambda **_: object())
        monkeypatch.setattr(module, "get_llm_config", lambda *a, **k: object())
        monkeypatch.setattr(module, "get_model", lambda *a, **k: "stub-model")
        monkeypatch.setattr(module, "generate_structured_with_schema_retries", capture)
        return capture

    return install


def test_icon_selection_passes_a_bounded_call_timeout(stub_llm):
    capture = stub_llm(icons_module, _Capture())
    picks = asyncio.run(icons_module.select_deck_icons(["Revenue", "Churn"], ["chart-bar", "user"]))
    assert picks == {}
    assert capture.kwargs["call_timeout_seconds"] == icons_module.DECK_ICON_SELECTION_CALL_TIMEOUT_SECONDS
    assert 0 < icons_module.DECK_ICON_SELECTION_CALL_TIMEOUT_SECONDS <= 90.0


def test_icon_selection_timeout_falls_back_to_none(stub_llm):
    stub_llm(icons_module, _Capture(error=TimeoutError("provider call timed out")))
    assert asyncio.run(icons_module.select_deck_icons(["Revenue"], ["chart-bar"])) is None


def test_infographic_plan_passes_a_bounded_call_timeout(stub_llm):
    capture = stub_llm(plan_module, _Capture(error=RuntimeError("stop after capture")))
    plan = asyncio.run(plan_module.generate_infographic_plan(
        topic="topic", language="ko", slide_contents=["one", "two"], roles=["CONTENT", "CONTENT"]))
    assert plan is None
    assert capture.kwargs["call_timeout_seconds"] == plan_module.INFOGRAPHIC_PLAN_CALL_TIMEOUT_SECONDS
    assert 0 < plan_module.INFOGRAPHIC_PLAN_CALL_TIMEOUT_SECONDS <= 180.0


def test_infographic_plan_timeout_falls_back_to_none(stub_llm):
    stub_llm(plan_module, _Capture(error=TimeoutError("provider call timed out")))
    plan = asyncio.run(plan_module.generate_infographic_plan(
        topic="topic", language="ko", slide_contents=["one"], roles=["CONTENT"]))
    assert plan is None
