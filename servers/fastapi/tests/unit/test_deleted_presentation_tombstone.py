"""Regression coverage for durable presentation delete tombstones."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
import uuid
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from api.v1.ppt.endpoints import outlines as outlines_endpoint
from api.v1.ppt.endpoints import presentation as presentation_endpoint
from api.v1.ppt.endpoints import presentation_crud
from api.v1.ppt.endpoints import presentation_versions
from models.sql.presentation import (
    PRESENTATION_LIFECYCLE_DELETED,
    PRESENTATION_LIFECYCLE_GENERATION_FAILED,
    PRESENTATION_LIFECYCLE_PUBLISHED,
    PRESENTATION_LIFECYCLE_STAGING,
    PresentationModel,
)
from services.presentation_access_service import require_live_presentation
from services.workspace_legacy_write_gate import CHAT_FIRST_COMMAND_REQUIRED


class _TombstoneSession:
    def __init__(self, presentation: PresentationModel) -> None:
        self.presentation = presentation
        self.reads = 0
        self.adds = 0
        self.commits = 0
        self.rollbacks = 0
        self.queries = 0

    async def get(self, model, entity_id):
        self.reads += 1
        assert model is PresentationModel
        assert entity_id == self.presentation.id
        return self.presentation

    def add(self, _value) -> None:
        self.adds += 1

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def execute(self, *_args, **_kwargs):
        self.queries += 1
        raise AssertionError("deleted presentation reached a persistence query")

    async def scalars(self, *_args, **_kwargs):
        self.queries += 1
        raise AssertionError("deleted presentation reached a persistence query")


def _deleted_presentation() -> PresentationModel:
    return PresentationModel(
        id=uuid.uuid4(),
        content="",
        n_slides=0,
        language="en",
        lifecycle_status=PRESENTATION_LIFECYCLE_DELETED,
        deleted_at=None,
        deletion_operation_id=uuid.uuid4(),
    )


async def _call_deleted_mutation(
    name: str,
    presentation: PresentationModel,
    session: _TombstoneSession,
):
    if name == "prepare":
        return await presentation_endpoint.prepare_presentation(
            presentation.id,
            [object()],
            object(),
            None,
            session,
        )
    if name == "quality-review":
        return await presentation_endpoint.quality_review_authored_presentation(
            presentation.id,
            object(),
            object(),
            session,
        )
    if name == "retemplate":
        return await presentation_endpoint.retemplate_authored_presentation(
            presentation.id,
            object(),
            object(),
            object(),
            session,
        )
    if name == "stream":
        return await presentation_endpoint.stream_presentation(
            presentation.id,
            object(),
            False,
            session,
        )
    if name == "update":
        return await presentation_endpoint.update_presentation(
            presentation.id,
            None,
            None,
            None,
            None,
            session,
        )
    if name == "edit":
        return await presentation_endpoint.edit_presentation_with_new_content(
            object(),
            SimpleNamespace(presentation_id=presentation.id),
            session,
        )
    if name == "derive":
        return await presentation_endpoint.derive_presentation_from_existing_one(
            object(),
            SimpleNamespace(presentation_id=presentation.id),
            session,
        )
    if name == "restore":
        return await presentation_versions.restore_presentation_version(
            presentation.id,
            uuid.uuid4(),
            session,
        )
    raise AssertionError(f"unknown mutation: {name}")


# The outline stream resolves the deck before deciding, because the staging
# bootstrap allowance cannot be evaluated without it - rejecting earlier made
# generating any new presentation impossible. A deleted deck is therefore hidden
# by `require_live_presentation` (404) instead of the scope preflight (409): one
# read replaces none, while the properties that matter here are unchanged - no
# write, no rehydration, and no generation work is reached.
def test_outline_writer_never_rehydrates_deleted_tombstone(monkeypatch):
    presentation = _deleted_presentation()
    session = _TombstoneSession(presentation)
    request = SimpleNamespace()

    temp_dir = Mock()
    content_brief = Mock()
    generate_outline = Mock()
    store_context = Mock()
    store_outlines = Mock()
    monkeypatch.setattr(
        outlines_endpoint.TEMP_FILE_SERVICE,
        "create_temp_dir",
        temp_dir,
    )
    monkeypatch.setattr(outlines_endpoint, "generate_content_brief", content_brief)
    monkeypatch.setattr(outlines_endpoint, "generate_ppt_outline", generate_outline)
    monkeypatch.setattr(
        outlines_endpoint.MEM0_PRESENTATION_MEMORY_SERVICE,
        "store_generation_context",
        store_context,
    )
    monkeypatch.setattr(
        outlines_endpoint.MEM0_PRESENTATION_MEMORY_SERVICE,
        "store_generated_outlines",
        store_outlines,
    )

    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            outlines_endpoint.stream_outlines(
                presentation.id,
                request,
                session,
            )
        )

    assert caught.value.status_code == 404
    assert caught.value.detail == "Presentation not found"
    assert session.reads == 1
    assert session.adds == session.commits == session.rollbacks == session.queries == 0
    temp_dir.assert_not_called()
    content_brief.assert_not_called()
    generate_outline.assert_not_called()
    store_context.assert_not_called()
    store_outlines.assert_not_called()


def test_legacy_delete_preserves_existing_durable_tombstone(monkeypatch):
    presentation = _deleted_presentation()
    session = _TombstoneSession(presentation)
    deletion_service = Mock()
    monkeypatch.setattr(
        presentation_crud,
        "PresentationDeletionService",
        deletion_service,
    )

    with pytest.raises(HTTPException) as caught:
        asyncio.run(presentation_crud.delete_presentation(presentation.id, session))

    assert caught.value.status_code == 409
    assert caught.value.detail == CHAT_FIRST_COMMAND_REQUIRED
    assert session.reads == 0
    assert session.adds == session.commits == session.rollbacks == session.queries == 0
    deletion_service.assert_not_called()


@pytest.mark.parametrize(
    "mutation",
    (
        "prepare",
        "quality-review",
        "retemplate",
        "stream",
        "update",
        "edit",
        "derive",
        "restore",
    ),
)
def test_legacy_mutations_cannot_reach_deleted_presentation_side_effects(mutation):
    presentation = _deleted_presentation()
    session = _TombstoneSession(presentation)

    with pytest.raises(HTTPException) as caught:
        asyncio.run(_call_deleted_mutation(mutation, presentation, session))

    # The runner-only gate answers before the tombstone is even read, so a
    # deleted deck can never be touched by a direct write.
    assert caught.value.status_code == 409
    assert caught.value.detail == CHAT_FIRST_COMMAND_REQUIRED
    assert session.reads == 0
    assert session.adds == session.commits == session.rollbacks == session.queries == 0


@pytest.mark.parametrize(
    "lifecycle_status",
    (
        PRESENTATION_LIFECYCLE_STAGING,
        PRESENTATION_LIFECYCLE_GENERATION_FAILED,
        PRESENTATION_LIFECYCLE_PUBLISHED,
    ),
)
def test_live_presentation_guard_keeps_non_deleted_lifecycles_available(
    lifecycle_status,
):
    presentation = PresentationModel(
        id=uuid.uuid4(),
        content="live",
        n_slides=1,
        language="en",
        lifecycle_status=lifecycle_status,
    )
    session = _TombstoneSession(presentation)

    result = asyncio.run(require_live_presentation(session, presentation.id))

    assert result is presentation
    assert session.reads == 1


def test_deleted_presentation_is_hidden_from_read_routes_before_child_queries():
    presentation = _deleted_presentation()
    session = _TombstoneSession(presentation)

    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            presentation_crud.get_presentation(
                presentation.id,
                session,
            )
        )

    assert caught.value.status_code == 404
    assert caught.value.detail == "Presentation not found"
    assert session.reads == 1
    assert session.queries == session.adds == session.commits == 0
