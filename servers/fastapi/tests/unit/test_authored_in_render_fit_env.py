"""Env accessors for the in-render card fit knobs.

Design: ``docs/authored-in-render-fit-design-20260906.md`` section 8.
"""

from __future__ import annotations

from utils.get_env_programme import (
    is_authored_in_render_fit_enabled,
)


def test_fit_enabled_defaults_false_and_reads_the_env(monkeypatch):
    # Off by default: local-fit is the active fitter.
    monkeypatch.delenv("AUTHORED_IN_RENDER_FIT_ENABLED", raising=False)
    assert is_authored_in_render_fit_enabled() is False

    monkeypatch.setenv("AUTHORED_IN_RENDER_FIT_ENABLED", "true")
    assert is_authored_in_render_fit_enabled() is True

    monkeypatch.setenv("AUTHORED_IN_RENDER_FIT_ENABLED", "false")
    assert is_authored_in_render_fit_enabled() is False

