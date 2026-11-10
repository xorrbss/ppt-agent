import asyncio

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from api.v1.ppt.endpoints.presentation_blank import create_blank_presentation
from models.generate_presentation_request import GeneratePresentationRequest
from models.sql.presentation import PresentationModel
from services.compatibility_migration.admission import (
    require_legacy_creation_closed,
    stamp_new_authored_presentation,
)
from services.compatibility_migration.feature_manifest import (
    build_authored_only_capability_manifest,
)
from models.presentation_profiles import PresentationProfileError, resolve_presentation_profile


def test_malformed_flag_fails_closed(monkeypatch):
    monkeypatch.setenv("AUTHORED_ONLY_NEW_PRESENTATION_ADMISSION", "maybe")
    monkeypatch.setenv("AUTHORED_ONLY_CANARY_STAGE", "100")
    with pytest.raises(HTTPException):
        require_legacy_creation_closed(operation="blank")


def test_authored_only_admission_is_fail_closed_by_default(monkeypatch):
    monkeypatch.delenv("AUTHORED_ONLY_NEW_PRESENTATION_ADMISSION", raising=False)
    monkeypatch.delenv("AUTHORED_ONLY_CANARY_STAGE", raising=False)
    with pytest.raises(HTTPException) as raised:
        require_legacy_creation_closed(operation="blank")
    assert raised.value.detail["code"] == "legacy_blank_presentation_admission_closed"
    manifest = build_authored_only_capability_manifest()
    assert manifest["authored_only"]["new_presentation_driver"] == "authored"
    assert manifest["ui"]["hide_legacy_create_actions"] is True


def test_internal_canary_only_admits_allowlisted_owner(monkeypatch):
    monkeypatch.setenv("AUTHORED_ONLY_NEW_PRESENTATION_ADMISSION", "true")
    monkeypatch.setenv("AUTHORED_ONLY_CANARY_STAGE", "internal")
    monkeypatch.setenv("AUTHORED_ONLY_INTERNAL_OWNER_IDS", "anonymous")
    with pytest.raises(HTTPException):
        require_legacy_creation_closed(operation="blank")

    monkeypatch.setenv("AUTHORED_ONLY_INTERNAL_OWNER_IDS", "different-owner")
    # A canary configuration must not reopen a non-authored creation path.
    with pytest.raises(HTTPException):
        require_legacy_creation_closed(operation="blank")


def test_new_authored_shell_no_longer_resolves(monkeypatch):
    monkeypatch.setenv("AUTHORED_ONLY_NEW_PRESENTATION_ADMISSION", "true")
    monkeypatch.setenv("AUTHORED_ONLY_CANARY_STAGE", "100")
    presentation = PresentationModel(content="x", n_slides=0, language="")
    stamp_new_authored_presentation(presentation)
    assert presentation.theme == {"mode": "authored"}
    assert presentation.profile_driver_id == "authored"
    # The authored driver was removed on 2026-11-10: the stamped shell no
    # longer resolves, so the write boundary refuses to persist it.
    with pytest.raises(PresentationProfileError, match="unsupported_presentation_driver"):
        resolve_presentation_profile(presentation)


def test_ui_manifest_hides_legacy_creation(monkeypatch):
    monkeypatch.setenv("AUTHORED_ONLY_NEW_PRESENTATION_ADMISSION", "true")
    monkeypatch.setenv("AUTHORED_ONLY_CANARY_STAGE", "100")
    manifest = build_authored_only_capability_manifest()
    assert manifest["authored_only"]["new_presentation_driver"] == "authored"
    assert manifest["authored_only"]["canary_stage"] == "100"
    assert manifest["ui"]["hide_legacy_create_actions"] is True
    assert manifest["legacy_boundary"]["allowed_actions"] == [
        "open",
        "preview",
        "export",
        "download",
        "delete",
        "upgrade-fork",
    ]


def test_generation_route_rejects_legacy_before_database_use():
    """A legacy strategy cannot be asked for, so it never reaches the route.

    The closure moved into the request contract itself: ``strategy`` accepts
    only the two drivers this fork still has renderers for, and the refusal
    therefore happens while parsing the body rather than anywhere near the
    database.
    """
    with pytest.raises(ValidationError) as raised:
        GeneratePresentationRequest(content="legacy", strategy="legacy")

    assert "strategy" in str(raised.value)


def test_blank_route_rejects_before_database_use(monkeypatch):
    monkeypatch.setenv("AUTHORED_ONLY_NEW_PRESENTATION_ADMISSION", "true")
    monkeypatch.setenv("AUTHORED_ONLY_CANARY_STAGE", "100")
    with pytest.raises(HTTPException) as raised:
        asyncio.run(create_blank_presentation(None))
    assert raised.value.detail["code"] == "legacy_blank_presentation_admission_closed"
