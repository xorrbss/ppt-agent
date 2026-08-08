"""Regression evidence for reversible chat-first rollout admission.

These tests call a real legacy mutation implementation.  A rollback decision
must restore the pre-existing write path, while an admitted cohort must stop
before the first persistence read.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi import HTTPException

from api.v1.ppt.endpoints.presentation_share import enable_share
from models.sql.presentation import (
    PRESENTATION_LIFECYCLE_PUBLISHED,
    PresentationModel,
)
from services.workspace_legacy_write_gate import CHAT_FIRST_COMMAND_REQUIRED


class _MutationSession:
    def __init__(self, presentation: PresentationModel) -> None:
        self.presentation = presentation
        self.reads = 0
        self.adds = 0
        self.commits = 0

    async def get(self, model, entity_id):
        self.reads += 1
        assert model is PresentationModel
        assert entity_id == self.presentation.id
        return self.presentation

    def add(self, value) -> None:
        assert value is self.presentation
        self.adds += 1

    async def commit(self) -> None:
        self.commits += 1


def _published_presentation() -> PresentationModel:
    return PresentationModel(
        id=uuid.uuid4(),
        content="rollout regression",
        n_slides=1,
        language="en",
        lifecycle_status=PRESENTATION_LIFECYCLE_PUBLISHED,
    )


def _enable_chat_first(monkeypatch) -> None:
    monkeypatch.setenv("CHAT_FIRST_WORKSPACE_ENABLED", "true")
    monkeypatch.setenv("CHAT_FIRST_WORKSPACE_RUNTIME_PROFILE", "sqlite-local")
    monkeypatch.delenv("CHAT_FIRST_WORKSPACE_KILL_SWITCH", raising=False)
    monkeypatch.delenv("CHAT_FIRST_WORKSPACE_ROLLOUT_PERCENT", raising=False)


@pytest.mark.parametrize(
    ("rollout_percent", "kill_switch"),
    (
        ("100", "true"),
        ("0", "false"),
    ),
    ids=("kill-switch-rollback", "cohort-exclusion-rollback"),
)
def test_rollback_controls_restore_an_actual_legacy_mutation(
    monkeypatch,
    rollout_percent,
    kill_switch,
):
    _enable_chat_first(monkeypatch)
    monkeypatch.setenv("CHAT_FIRST_WORKSPACE_ROLLOUT_PERCENT", rollout_percent)
    monkeypatch.setenv("CHAT_FIRST_WORKSPACE_KILL_SWITCH", kill_switch)
    presentation = _published_presentation()
    session = _MutationSession(presentation)

    response = asyncio.run(
        enable_share(
            presentation.id,
            regenerate=False,
            sql_session=session,
        )
    )

    assert response.shared is True
    assert response.share_token == presentation.share_token
    assert presentation.share_token
    assert session.reads == session.adds == session.commits == 1


def test_admitted_cohort_blocks_the_same_legacy_mutation_before_persistence(
    monkeypatch,
):
    _enable_chat_first(monkeypatch)
    monkeypatch.setenv("CHAT_FIRST_WORKSPACE_ROLLOUT_PERCENT", "100")
    presentation = _published_presentation()
    session = _MutationSession(presentation)

    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            enable_share(
                presentation.id,
                regenerate=False,
                sql_session=session,
            )
        )

    assert caught.value.status_code == 409
    assert caught.value.detail == CHAT_FIRST_COMMAND_REQUIRED
    assert session.reads == session.adds == session.commits == 0
    assert presentation.share_token is None
