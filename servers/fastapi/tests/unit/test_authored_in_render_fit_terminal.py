"""The remake admission call site for the in-render card fit (D1b).

Design: ``docs/authored-in-render-fit-design-20260906.md`` sections 5 and 6.
Unit-level: ``_admit_remade_slide`` is exercised directly with the semantic
canonicalizer, capacity gate and safety gate stubbed permissive, isolating
the render-and-swap decision under test. ``services/authored_repair_round_order.py``
(``_apply_text_scale_rung``) calls this same function, so it needs no test of
its own here (task 2c).

This repo has no ``pytest-asyncio`` plugin; async code is driven with
``asyncio.run`` from an ordinary sync test function.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from services import authored_terminal_slide_fallback as terminal_mod


def _permissive(monkeypatch):
    monkeypatch.setattr(terminal_mod, "canonicalize_authored_roles", lambda html: html)
    monkeypatch.setattr(terminal_mod, "capacity_violations", lambda *a, **k: ())
    monkeypatch.setattr(terminal_mod, "_blocking_defects", lambda **k: ())


def _admit(monkeypatch, candidate_html: str):
    return asyncio.run(
        terminal_mod._admit_remade_slide(
            candidate_html=candidate_html,
            capacity={},
            profile=SimpleNamespace(profile_id="ordinary"),
            reference_mode="style_reference",
        )
    )


def test_knob_off_calls_with_no_fit_keyword_and_keeps_the_candidate(monkeypatch):
    monkeypatch.setenv("AUTHORED_IN_RENDER_FIT_ENABLED", "false")
    _permissive(monkeypatch)

    async def fake_capture(html):
        """A fake shaped like the pre-fit interface: one positional arg only."""
        return SimpleNamespace(png=b"remade-png", browser_metrics={"telemetry": "ok"})

    monkeypatch.setattr(terminal_mod, "render_html_to_png_with_metrics", fake_capture)

    admitted = _admit(monkeypatch, "<p>candidate</p>")

    assert admitted == ("<p>candidate</p>", b"remade-png", {"telemetry": "ok"})


def test_knob_on_adopts_the_fitted_html_as_the_string_of_record(monkeypatch):
    monkeypatch.setenv("AUTHORED_IN_RENDER_FIT_ENABLED", "true")
    _permissive(monkeypatch)

    async def fake_capture(html, *, fit=False, **_kwargs):
        assert fit is True
        return SimpleNamespace(
            png=b"remade-png",
            browser_metrics={"telemetry": "ok"},
            fitted_html="<p>fitted candidate</p>",
        )

    monkeypatch.setattr(terminal_mod, "render_html_to_png_with_metrics", fake_capture)

    admitted = _admit(monkeypatch, "<p>candidate</p>")

    assert admitted == ("<p>fitted candidate</p>", b"remade-png", {"telemetry": "ok"})


def test_knob_on_with_no_fitted_html_keeps_the_candidate(monkeypatch):
    monkeypatch.setenv("AUTHORED_IN_RENDER_FIT_ENABLED", "true")
    _permissive(monkeypatch)

    async def fake_capture(html, *, fit=False, **_kwargs):
        return SimpleNamespace(
            png=b"remade-png", browser_metrics={"telemetry": "ok"}, fitted_html=None
        )

    monkeypatch.setattr(terminal_mod, "render_html_to_png_with_metrics", fake_capture)

    admitted = _admit(monkeypatch, "<p>candidate</p>")

    assert admitted == ("<p>candidate</p>", b"remade-png", {"telemetry": "ok"})


def test_knob_on_against_a_capture_missing_the_new_fields_keeps_the_candidate(
    monkeypatch,
):
    """A ``RenderCapture`` shaped like the current worktree's, pre-merge."""

    monkeypatch.setenv("AUTHORED_IN_RENDER_FIT_ENABLED", "true")
    _permissive(monkeypatch)

    class OldCapture:
        def __init__(self):
            self.png = b"remade-png"
            self.browser_metrics = {"telemetry": "ok"}

    async def fake_capture(html, *, fit=False, **_kwargs):
        return OldCapture()

    monkeypatch.setattr(terminal_mod, "render_html_to_png_with_metrics", fake_capture)

    admitted = _admit(monkeypatch, "<p>candidate</p>")

    assert admitted == ("<p>candidate</p>", b"remade-png", {"telemetry": "ok"})


def test_a_missing_metrics_or_placeholder_render_still_refuses_admission(monkeypatch):
    """The refusal is the knob's business either way, so exercise it on the
    default (on) path -- a placeholder png must not be admitted just because
    the fit was asked for."""

    monkeypatch.delenv("AUTHORED_IN_RENDER_FIT_ENABLED", raising=False)
    _permissive(monkeypatch)

    async def fake_capture(html, *, fit=False, **_kwargs):
        return SimpleNamespace(png=terminal_mod._placeholder_png(), browser_metrics=None)

    monkeypatch.setattr(terminal_mod, "render_html_to_png_with_metrics", fake_capture)

    assert _admit(monkeypatch, "<p>candidate</p>") is None
