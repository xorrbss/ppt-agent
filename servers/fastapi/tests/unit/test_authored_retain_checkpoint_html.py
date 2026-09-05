"""AUTHORED_RETAIN_CHECKPOINT_HTML keeps pre-repair slide HTML after publish."""

from __future__ import annotations

import asyncio
import uuid

import services.authored_presentation_artifacts as artifacts_mod
from utils.get_env import is_authored_retain_checkpoint_html_enabled


def test_retention_knob_defaults_off(monkeypatch) -> None:
    monkeypatch.delenv("AUTHORED_RETAIN_CHECKPOINT_HTML", raising=False)
    assert is_authored_retain_checkpoint_html_enabled() is False
    monkeypatch.setenv("AUTHORED_RETAIN_CHECKPOINT_HTML", "true")
    assert is_authored_retain_checkpoint_html_enabled() is True


def test_publish_cleanup_clears_html_by_default(monkeypatch) -> None:
    monkeypatch.delenv("AUTHORED_RETAIN_CHECKPOINT_HTML", raising=False)
    cleared: list[uuid.UUID] = []

    async def fake_clear(job_id: uuid.UUID) -> None:
        cleared.append(job_id)

    monkeypatch.setattr(artifacts_mod, "clear_checkpoint_html", fake_clear)
    job_id = uuid.uuid4()
    asyncio.run(artifacts_mod._cleanup_published_checkpoints(job_id))
    assert cleared == [job_id]


def test_publish_cleanup_keeps_html_when_retention_is_on(monkeypatch) -> None:
    monkeypatch.setenv("AUTHORED_RETAIN_CHECKPOINT_HTML", "true")
    cleared: list[uuid.UUID] = []

    async def fake_clear(job_id: uuid.UUID) -> None:
        cleared.append(job_id)

    monkeypatch.setattr(artifacts_mod, "clear_checkpoint_html", fake_clear)
    asyncio.run(artifacts_mod._cleanup_published_checkpoints(uuid.uuid4()))
    assert cleared == []
