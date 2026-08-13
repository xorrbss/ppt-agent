import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api.v1.ppt.endpoints.presentation_blank import create_blank_presentation
from api.v1.ppt.endpoints.presentation_generate import (
    check_if_api_request_is_valid,
)
from models.generate_presentation_request import GeneratePresentationRequest
from models.sql.presentation import PresentationModel
from services.compatibility_migration.admission import (
    requested_driver_from_generation,
    require_authored_generation_admission,
    require_legacy_creation_closed,
    stamp_new_authored_presentation,
)
from services.compatibility_migration.feature_manifest import (
    build_authored_only_capability_manifest,
)
from services.presentation_profiles import resolve_presentation_profile


@pytest.mark.parametrize(
    ("strategy", "template", "expected"),
    [
        ("legacy", "default", "standard"),
        ("adaptive", "adaptive", "smart"),
        (None, "adaptive", "smart"),
        ("template_v2", "adaptive", "template-v2"),
        ("authored", "adaptive", "authored"),
        (None, "authored", "authored"),
    ],
)
def test_requested_driver_uses_compatibility_fields(strategy, template, expected):
    request = SimpleNamespace(strategy=strategy, template=template)
    assert requested_driver_from_generation(request) == expected


@pytest.mark.parametrize("strategy", ["legacy", "adaptive", "template_v2"])
def test_legacy_generation_is_blocked_when_full_rollout_is_enabled(monkeypatch, strategy):
    monkeypatch.setenv("AUTHORED_ONLY_NEW_PRESENTATION_ADMISSION", "true")
    monkeypatch.setenv("AUTHORED_ONLY_CANARY_STAGE", "100")
    request = SimpleNamespace(strategy=strategy, template="adaptive")
    with pytest.raises(HTTPException) as raised:
        require_authored_generation_admission(request)
    assert raised.value.status_code == 409
    assert raised.value.detail["code"] == "legacy_generation_admission_closed"


def test_authored_generation_is_admitted(monkeypatch):
    monkeypatch.setenv("AUTHORED_ONLY_NEW_PRESENTATION_ADMISSION", "true")
    monkeypatch.setenv("AUTHORED_ONLY_CANARY_STAGE", "100")
    require_authored_generation_admission(
        SimpleNamespace(strategy="authored", template="authored")
    )


def test_malformed_flag_fails_closed(monkeypatch):
    monkeypatch.setenv("AUTHORED_ONLY_NEW_PRESENTATION_ADMISSION", "maybe")
    monkeypatch.setenv("AUTHORED_ONLY_CANARY_STAGE", "100")
    with pytest.raises(HTTPException):
        require_legacy_creation_closed(operation="blank")


def test_rollout_is_default_off_until_release_gates_pass(monkeypatch):
    monkeypatch.delenv("AUTHORED_ONLY_NEW_PRESENTATION_ADMISSION", raising=False)
    monkeypatch.delenv("AUTHORED_ONLY_CANARY_STAGE", raising=False)
    require_legacy_creation_closed(operation="blank")
    manifest = build_authored_only_capability_manifest()
    assert manifest["authored_only"]["canary_stage"] == "internal"
    assert manifest["ui"]["hide_legacy_create_actions"] is False


def test_internal_canary_only_admits_allowlisted_owner(monkeypatch):
    monkeypatch.setenv("AUTHORED_ONLY_NEW_PRESENTATION_ADMISSION", "true")
    monkeypatch.setenv("AUTHORED_ONLY_CANARY_STAGE", "internal")
    monkeypatch.setenv("AUTHORED_ONLY_INTERNAL_OWNER_IDS", "anonymous")
    with pytest.raises(HTTPException):
        require_legacy_creation_closed(operation="blank")

    monkeypatch.setenv("AUTHORED_ONLY_INTERNAL_OWNER_IDS", "different-owner")
    # Outside the canary cohort, existing create behavior remains available
    # so a rollback/hold changes configuration without a deployment.
    require_legacy_creation_closed(operation="blank")


def test_new_shell_is_canonical_authored(monkeypatch):
    monkeypatch.setenv("AUTHORED_ONLY_NEW_PRESENTATION_ADMISSION", "true")
    monkeypatch.setenv("AUTHORED_ONLY_CANARY_STAGE", "100")
    presentation = PresentationModel(content="x", n_slides=0, language="")
    stamp_new_authored_presentation(presentation)
    assert presentation.theme == {"mode": "authored"}
    assert presentation.profile_driver_id == "authored"
    assert resolve_presentation_profile(presentation).driver_id == "authored"


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


def test_generation_route_rejects_legacy_before_database_use(monkeypatch):
    monkeypatch.setenv("AUTHORED_ONLY_NEW_PRESENTATION_ADMISSION", "true")
    monkeypatch.setenv("AUTHORED_ONLY_CANARY_STAGE", "100")
    request = GeneratePresentationRequest(content="legacy", strategy="legacy")
    with pytest.raises(HTTPException) as raised:
        asyncio.run(check_if_api_request_is_valid(request, None))
    assert raised.value.detail["code"] == "legacy_generation_admission_closed"


def test_blank_route_rejects_before_database_use(monkeypatch):
    monkeypatch.setenv("AUTHORED_ONLY_NEW_PRESENTATION_ADMISSION", "true")
    monkeypatch.setenv("AUTHORED_ONLY_CANARY_STAGE", "100")
    with pytest.raises(HTTPException) as raised:
        asyncio.run(create_blank_presentation(None))
    assert raised.value.detail["code"] == "legacy_blank_presentation_admission_closed"
