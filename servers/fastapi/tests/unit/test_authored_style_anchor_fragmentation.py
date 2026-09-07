"""Q0: the fragmentation gate applied to the deck's style anchor.

``docs/authored-sol-quality-first-design-20260908.md`` section Q0. The anchor
slide's stylesheet is handed verbatim to every later slide, so a Sol-like
anchor with 60+ text-owning elements would make every slide in the deck
inherit that fragmentation before the ordinary per-slide gate ever runs. This
module gives the anchor's own one remake a reason to fire on a ``text_owners``
violation, and drops the anchor -- rather than publishing a fragmented one --
if the remake does not clear it.

Fakes are reused from ``test_authored_style_anchor_stage``: ``_plan``,
``_run``, ``_STYLED``, ``_clean_gate`` and ``_no_render``.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import utils.slide_capture as slide_capture
from services import authored_style_anchor_stage as stage
from tests.unit.test_authored_style_anchor_stage import (
    _STYLED,
    _clean_gate,
    _no_render,
    _plan,
    _run,
)
from utils.authored_fragmentation_gate import TEXT_OWNERS_VIOLATION

GATE_KNOB = "AUTHORED_FRAGMENTATION_GATE_ENABLED"

# A second, distinct anchor draft: same shape as ``_STYLED`` but with its own
# marker, so the fake metrics table below can tell the first draft and the
# remade draft apart by HTML text alone -- exactly how the render cache keys
# real browser metrics.
_REMADE = _STYLED.replace("#0B1F3A", "#12A150")


def _metrics(owner_count: int, *, font_px: float = 18.0) -> dict:
    """Browser metrics for a slide with ``owner_count`` text-owning elements.

    18px keeps every owner above the 15px default floor, so only the
    ``text_owners`` count -- never ``type_floor`` -- can violate.
    """

    return {
        "viewport": {"width": 1280, "height": 720},
        "elements": [
            {
                "qid": f"anchor.owner{index}",
                "has_owned_text": True,
                "rendered_text": f"claim {index}",
                "semantic_role": "content",
                "font_size": font_px,
            }
            for index in range(owner_count)
        ],
    }


def _wire_metrics(monkeypatch, by_html: dict[str, dict]) -> None:
    """Stand in for the render cache: metrics keyed on the exact HTML text."""

    monkeypatch.setattr(
        slide_capture, "get_cached_browser_metrics", lambda html: by_html.get(html)
    )


def _gate_on(monkeypatch) -> None:
    monkeypatch.setenv(GATE_KNOB, "true")


def _gate_off(monkeypatch) -> None:
    monkeypatch.delenv(GATE_KNOB, raising=False)


# --- an anchor over the cap is remade with the merge hint ---------------------


def test_an_anchor_over_the_cap_is_remade_with_the_merge_hint(monkeypatch):
    _gate_on(monkeypatch)
    _wire_metrics(monkeypatch, {_STYLED: _metrics(60), _REMADE: _metrics(20)})
    hints = []

    async def author(plan, index, *, prior_failure_hint=None, **_kwargs):
        hints.append(prior_failure_hint)
        return _REMADE if prior_failure_hint else _STYLED

    anchor, html, index = _run(
        stage.resolve_style_anchor(
            _plan(5),
            author=author,
            render=_no_render,
            inspect_blocking=_clean_gate,
            enabled=True,
        )
    )

    assert len(hints) == 2
    assert hints[0] is None
    assert "60 text-owning elements" in hints[1]
    assert "Merge" in hints[1]
    assert "CONTENT" in hints[1]
    assert "do not shrink type" in hints[1]
    assert html == _REMADE
    assert index == 1
    assert anchor is not None
    assert anchor.slide_index == 1


def test_the_merge_hint_names_both_when_a_blocking_defect_also_fires(monkeypatch):
    """Precedence: blocking DOM defects first, then one hint naming both."""

    _gate_on(monkeypatch)
    _wire_metrics(monkeypatch, {_STYLED: _metrics(60), _REMADE: _metrics(20)})
    hints = []
    defect = SimpleNamespace(category="overflow", message="card leaves the canvas")

    def gate(html):
        return () if html == _REMADE else (defect,)

    async def author(plan, index, *, prior_failure_hint=None, **_kwargs):
        hints.append(prior_failure_hint)
        return _REMADE if prior_failure_hint else _STYLED

    _run(
        stage.resolve_style_anchor(
            _plan(5),
            author=author,
            render=_no_render,
            inspect_blocking=gate,
            enabled=True,
        )
    )

    assert len(hints) == 2
    assert hints[1] is not None
    assert "style anchor" in hints[1]
    assert "overflow" in hints[1]
    assert "60 text-owning elements" in hints[1]


# --- a compliant anchor is untouched -------------------------------------------


def test_a_compliant_anchor_is_untouched(monkeypatch, caplog):
    _gate_on(monkeypatch)
    _wire_metrics(monkeypatch, {_STYLED: _metrics(20)})
    authored = []

    async def author(plan, index, **_kwargs):
        authored.append(index)
        return _STYLED

    with caplog.at_level(logging.INFO, logger="services.authored_style_anchor_stage"):
        anchor, html, index = _run(
            stage.resolve_style_anchor(
                _plan(5),
                author=author,
                render=_no_render,
                inspect_blocking=_clean_gate,
                enabled=True,
            )
        )

    assert authored == [1]  # no remake spent
    assert html == _STYLED
    assert index == 1
    assert anchor is not None
    assert "anchor_ready slide=1 gated=True owners=20" in caplog.text


# --- still fragmented after the remake: the anchor is dropped -----------------


def test_an_anchor_still_fragmented_after_the_remake_is_dropped(monkeypatch, caplog):
    _gate_on(monkeypatch)
    _wire_metrics(monkeypatch, {_STYLED: _metrics(60), _REMADE: _metrics(55)})

    async def author(plan, index, *, prior_failure_hint=None, **_kwargs):
        return _REMADE if prior_failure_hint else _STYLED

    with caplog.at_level(logging.INFO, logger="services.authored_style_anchor_stage"):
        anchor, html, index = _run(
            stage.resolve_style_anchor(
                _plan(5),
                author=author,
                render=_no_render,
                inspect_blocking=_clean_gate,
                enabled=True,
            )
        )

    # Dropped, not published: the deck authors without an anchor rather than
    # inheriting the still-fragmented one.
    assert anchor is None
    assert html == _REMADE
    assert index == 1
    assert "anchor_fragmented slide=1 owners=55" in caplog.text


def test_a_second_remake_is_never_spent_on_fragmentation_alone(monkeypatch):
    """The anchor's provider budget is one remake, not one per violation kind."""

    _gate_on(monkeypatch)
    _wire_metrics(monkeypatch, {_STYLED: _metrics(60), _REMADE: _metrics(55)})
    calls = []

    async def author(plan, index, *, prior_failure_hint=None, **_kwargs):
        calls.append(prior_failure_hint)
        return _REMADE if prior_failure_hint else _STYLED

    _run(
        stage.resolve_style_anchor(
            _plan(5),
            author=author,
            render=_no_render,
            inspect_blocking=_clean_gate,
            remaining_calls=2,
            enabled=True,
        )
    )

    assert len(calls) == 2  # one first call, one remake -- never a third


# --- the knob off changes nothing ----------------------------------------------


def test_the_knob_off_never_evaluates_fragmentation_at_all(monkeypatch, caplog):
    _gate_off(monkeypatch)
    # Metrics say this anchor is badly fragmented; with the knob off that must
    # never be looked at.
    _wire_metrics(monkeypatch, {_STYLED: _metrics(90)})
    authored = []

    async def author(plan, index, **_kwargs):
        authored.append(index)
        return _STYLED

    with caplog.at_level(logging.INFO, logger="services.authored_style_anchor_stage"):
        anchor, html, index = _run(
            stage.resolve_style_anchor(
                _plan(5),
                author=author,
                render=_no_render,
                inspect_blocking=_clean_gate,
                enabled=True,
            )
        )

    assert authored == [1]  # no remake spent
    assert html == _STYLED
    assert anchor is not None
    assert "owners=" not in caplog.text
    assert "fragmented" not in caplog.text
    assert stage.anchor_fragmentation_report(_STYLED) is None


def test_anchor_fragmentation_report_helpers_off_and_on(monkeypatch):
    _gate_off(monkeypatch)
    assert stage.anchor_fragmentation_report(_STYLED) is None

    _gate_on(monkeypatch)
    assert stage.anchor_fragmentation_report(_STYLED) is None  # no metrics cached
    _wire_metrics(monkeypatch, {_STYLED: _metrics(60)})
    report = stage.anchor_fragmentation_report(_STYLED)
    assert report is not None
    assert report.text_owners == 60
    assert any(v.startswith(f"{TEXT_OWNERS_VIOLATION}:") for v in report.violations)
