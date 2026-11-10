"""AUTHORED_RETAIN_CHECKPOINT_HTML keeps pre-repair slide HTML after publish."""

from __future__ import annotations


from utils.get_env import is_authored_retain_checkpoint_html_enabled


def test_retention_knob_defaults_off(monkeypatch) -> None:
    monkeypatch.delenv("AUTHORED_RETAIN_CHECKPOINT_HTML", raising=False)
    assert is_authored_retain_checkpoint_html_enabled() is False
    monkeypatch.setenv("AUTHORED_RETAIN_CHECKPOINT_HTML", "true")
    assert is_authored_retain_checkpoint_html_enabled() is True
