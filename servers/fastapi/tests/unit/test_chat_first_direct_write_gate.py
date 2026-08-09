import asyncio
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api.v1.ppt.endpoints import presentation as presentation_endpoints
from api.v1.ppt.endpoints import (
    presentation_crud,
    presentation_generate,
    presentation_versions,
    slide,
)
from services.workspace_legacy_write_gate import (
    CHAT_FIRST_COMMAND_REQUIRED,
    require_legacy_presentation_write_path,
)


_GUARDED_ENDPOINTS = (
    "prepare",
    "quality_review",
    "retemplate",
    "stream",
    "update",
    "edit",
    "derive",
    "slide_edit",
    "slide_edit_html",
    "restore",
    "delete",
    "generation_cancel",
)


class _LegacyPathReached(HTTPException):
    """Sentinel that also survives endpoints which rethrow HTTP errors."""

    def __init__(self):
        super().__init__(status_code=599, detail="legacy_path_reached")


class _ProbeSession:
    async def get(self, *_args, **_kwargs):
        raise _LegacyPathReached


async def _call_guarded_endpoint(name: str, session: _ProbeSession):
    entity_id = uuid.uuid4()
    edit_request = SimpleNamespace(presentation_id=entity_id)

    if name == "prepare":
        return await presentation_endpoints.prepare_presentation(
            entity_id,
            [object()],
            object(),
            None,
            session,
        )
    if name == "quality_review":
        return await presentation_endpoints.quality_review_authored_presentation(
            entity_id,
            object(),
            object(),
            session,
        )
    if name == "retemplate":
        return await presentation_endpoints.retemplate_authored_presentation(
            entity_id,
            object(),
            object(),
            object(),
            session,
        )
    if name == "stream":
        return await presentation_endpoints.stream_presentation(
            entity_id,
            object(),
            False,
            session,
        )
    if name == "update":
        return await presentation_endpoints.update_presentation(
            entity_id,
            None,
            None,
            None,
            None,
            session,
        )
    if name == "edit":
        return await presentation_endpoints.edit_presentation_with_new_content(
            object(),
            edit_request,
            session,
        )
    if name == "derive":
        return await presentation_endpoints.derive_presentation_from_existing_one(
            object(),
            edit_request,
            session,
        )
    if name == "slide_edit":
        return await slide.edit_slide(entity_id, "edit", session)
    if name == "slide_edit_html":
        return await slide.edit_slide_html(entity_id, "edit", None, session)
    if name == "restore":
        return await presentation_versions.restore_presentation_version(
            entity_id,
            uuid.uuid4(),
            session,
        )
    if name == "delete":
        return await presentation_crud.delete_presentation(entity_id, session)
    if name == "generation_cancel":
        return await presentation_generate.cancel_async_presentation_generation(
            "task-id"
        )
    raise AssertionError(f"Unknown guarded endpoint: {name}")


async def _legacy_quality_review(*_args, **_kwargs):
    raise _LegacyPathReached


async def _legacy_generation_path(*_args, **_kwargs):
    raise _LegacyPathReached


@pytest.mark.parametrize("endpoint_name", _GUARDED_ENDPOINTS)
def test_direct_presentation_writes_fail_closed_before_side_effects(
    monkeypatch,
    endpoint_name,
):
    monkeypatch.setattr(
        presentation_endpoints,
        "queue_authored_quality_review",
        _legacy_quality_review,
    )
    monkeypatch.setattr(
        presentation_generate,
        "check_if_api_request_is_valid",
        _legacy_generation_path,
    )
    monkeypatch.setattr(
        presentation_generate,
        "cancel_generation_job",
        _legacy_generation_path,
    )

    with pytest.raises(HTTPException) as caught:
        asyncio.run(_call_guarded_endpoint(endpoint_name, _ProbeSession()))

    assert caught.value.status_code == 409
    assert caught.value.detail == CHAT_FIRST_COMMAND_REQUIRED


def test_write_gate_has_no_opt_out():
    with pytest.raises(HTTPException) as caught:
        require_legacy_presentation_write_path()

    assert caught.value.status_code == 409
    assert caught.value.detail == "chat_first_command_required"


@pytest.mark.parametrize(
    "read_call",
    (
        lambda entity_id, session: presentation_crud.get_presentation(
            entity_id, session
        ),
        lambda entity_id, session: presentation_crud.get_presentation_generation_info(
            entity_id, session
        ),
        lambda entity_id, session: presentation_versions.list_presentation_versions(
            entity_id, session
        ),
    ),
)
def test_read_only_presentation_routes_stay_available(read_call):
    # Read routes intentionally do not use the legacy write gate.  The probe
    # proves dispatch reached their existing database read instead of a 409.
    with pytest.raises(_LegacyPathReached):
        asyncio.run(read_call(uuid.uuid4(), _ProbeSession()))
