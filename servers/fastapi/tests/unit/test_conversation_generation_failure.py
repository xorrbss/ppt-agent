"""The conversation API carries the workspace's generation recovery decision."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import pytest

from models.sql.presentation import PresentationModel
from services.conversations import service


@pytest.mark.parametrize("failure_class", ["internal_defect", "provider_unstable", None])
def test_timeline_preserves_workspace_failure_class(monkeypatch, failure_class):
    conversation_id = uuid.uuid4()
    presentation = PresentationModel(
        id=uuid.uuid4(), content="Failure contract", n_slides=2, language="en",
        layout=None, mode="authored", theme={"mode": "authored"}, generation_mode="standard", revision=4,
    )
    operation_id = uuid.uuid4()
    monkeypatch.setattr(service, "_conversation_and_presentation", AsyncMock(return_value=(
        SimpleNamespace(id=conversation_id), presentation,
    )))
    monkeypatch.setattr(service, "workspace_timeline", AsyncMock(return_value={
        "revision": 4,
        "items": [{
            "anchor_type": "operation", "operation_id": operation_id, "position": 1,
            "operation_type": "presentation.generate", "status": "failed", "version": 1,
            "failure_class": failure_class,
        }],
    }))
    timeline = asyncio.run(service.conversation_timeline(object(), conversation_id=conversation_id))
    assert timeline.items[0].failure_class == failure_class
    assert timeline.model_dump(mode="json")["items"][0]["failure_class"] == failure_class
