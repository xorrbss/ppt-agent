"""Env accessors for the in-render card fit knobs.

Design: ``docs/authored-in-render-fit-design-20260906.md`` section 8.
"""

from __future__ import annotations

from utils.get_env_programme import (
    get_authored_in_render_fit_verify_sample,
    is_authored_in_render_fit_enabled,
)


def test_fit_enabled_defaults_true_and_reads_the_env(monkeypatch):
    monkeypatch.delenv("AUTHORED_IN_RENDER_FIT_ENABLED", raising=False)
    assert is_authored_in_render_fit_enabled() is True

    monkeypatch.setenv("AUTHORED_IN_RENDER_FIT_ENABLED", "true")
    assert is_authored_in_render_fit_enabled() is True

    monkeypatch.setenv("AUTHORED_IN_RENDER_FIT_ENABLED", "false")
    assert is_authored_in_render_fit_enabled() is False


def test_verify_sample_defaults_to_the_rollout_sample_and_clamps(monkeypatch):
    monkeypatch.delenv("AUTHORED_IN_RENDER_FIT_VERIFY_SAMPLE", raising=False)
    assert get_authored_in_render_fit_verify_sample() == 0.05

    monkeypatch.setenv("AUTHORED_IN_RENDER_FIT_VERIFY_SAMPLE", "0.05")
    assert get_authored_in_render_fit_verify_sample() == 0.05

    monkeypatch.setenv("AUTHORED_IN_RENDER_FIT_VERIFY_SAMPLE", "1")
    assert get_authored_in_render_fit_verify_sample() == 1.0

    monkeypatch.setenv("AUTHORED_IN_RENDER_FIT_VERIFY_SAMPLE", "-1")
    assert get_authored_in_render_fit_verify_sample() == 0.0

    monkeypatch.setenv("AUTHORED_IN_RENDER_FIT_VERIFY_SAMPLE", "5")
    assert get_authored_in_render_fit_verify_sample() == 1.0

    monkeypatch.setenv("AUTHORED_IN_RENDER_FIT_VERIFY_SAMPLE", "0")
    assert get_authored_in_render_fit_verify_sample() == 0.0

    # Unparseable falls back to the rollout sample, never silently to no
    # verification at all: a typo must not switch the safety net off.
    monkeypatch.setenv("AUTHORED_IN_RENDER_FIT_VERIFY_SAMPLE", "not-a-number")
    assert get_authored_in_render_fit_verify_sample() == 0.05
