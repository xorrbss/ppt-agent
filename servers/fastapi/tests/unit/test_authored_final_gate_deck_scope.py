"""The final authored quality gate's re-render must share the deck's render
worker lease, not fall back to a fresh single-use scope per slide.

``_final_authored_quality_gate`` re-renders every slide with
``ensure_browser_metrics`` when the caller has no cached browser metrics to
hand it. That gather previously ran with no ambient deck scope set, so each
slide got its own throwaway ``adhoc-<uuid>`` renderer lease instead of sharing
the deck's worker process the way the rest of the pipeline does.
"""

from types import SimpleNamespace
import asyncio
import uuid

from services import authored_presentation_generation as authored_generation_mod
from utils.slide_capture_deck_scope import current_render_deck_scope


class _FakeReport:
    telemetry_status = "available"
    fit_summary = None
    issues: list = []


def _fake_inspect(html, _metrics, **_kwargs):
    return _FakeReport()


def _fake_blocking(_report):
    return ()


def test_final_gate_rerender_shares_the_deck_render_scope(monkeypatch) -> None:
    presentation_id = uuid.uuid4()
    observed_scopes: list[str | None] = []

    async def fake_ensure_browser_metrics(_html):
        observed_scopes.append(current_render_deck_scope())
        return {"elements": []}

    monkeypatch.setattr(
        authored_generation_mod, "ensure_browser_metrics", fake_ensure_browser_metrics
    )
    monkeypatch.setattr(authored_generation_mod, "inspect_authored_dom", _fake_inspect)
    monkeypatch.setattr(authored_generation_mod, "blocking_safety_issues", _fake_blocking)

    residual = asyncio.run(
        authored_generation_mod._final_authored_quality_gate(
            presentation_id=presentation_id,
            htmls=["<html></html>", "<html></html>"],
            capacity_plan=None,
            profile=SimpleNamespace(profile_id="ordinary"),
            reference_mode="style_reference",
        )
    )

    assert residual == []
    assert observed_scopes == [str(presentation_id), str(presentation_id)]


def test_final_gate_skips_the_rerender_when_metrics_are_already_cached(monkeypatch) -> None:
    """No metrics gather means no scope is needed either."""

    calls: list[str] = []

    async def unexpected_ensure_browser_metrics(_html):
        calls.append("called")
        return {"elements": []}

    monkeypatch.setattr(
        authored_generation_mod,
        "ensure_browser_metrics",
        unexpected_ensure_browser_metrics,
    )
    monkeypatch.setattr(authored_generation_mod, "inspect_authored_dom", _fake_inspect)
    monkeypatch.setattr(authored_generation_mod, "blocking_safety_issues", _fake_blocking)

    residual = asyncio.run(
        authored_generation_mod._final_authored_quality_gate(
            presentation_id=uuid.uuid4(),
            htmls=["<html></html>"],
            capacity_plan=None,
            profile=SimpleNamespace(profile_id="ordinary"),
            reference_mode="style_reference",
            browser_metrics_by_slide=[{"elements": []}],
        )
    )

    assert residual == []
    assert calls == []
