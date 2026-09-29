from dataclasses import FrozenInstanceError

import pytest

from models.presentation_profiles import (
    GENERATION_MODE_COMPATIBILITY,
    PRESENTATION_DRIVER_REGISTRY,
    PresentationProfileError,
    resolve_presentation_driver,
    resolve_presentation_profile,
)


@pytest.mark.parametrize(
    ("identity", "driver_id", "content_model", "strategy", "style_source"),
    [
        (
            {
                "generation_mode": "standard",
                "mode": "authored",
                "version": "v1-standard",
                "theme": {"mode": "authored"},
            },
            "authored",
            "authored",
            "authored",
            "authored",
        ),
    ],
)
def test_supported_profiles_and_drivers(
    identity, driver_id, content_model, strategy, style_source
):
    profile, capabilities, driver = resolve_presentation_driver(identity)

    assert profile.driver_id == driver_id
    assert profile.content_model == content_model
    assert profile.generation_strategy == strategy
    assert profile.style_source == style_source
    assert capabilities.driver_id == driver_id
    assert driver.driver_id == driver_id
    assert driver.capabilities is capabilities


def test_registry_is_complete_immutable_and_exposes_supported_adapters():
    assert tuple(PRESENTATION_DRIVER_REGISTRY) == ("authored", "svg-v1")
    assert {
        driver.capabilities.renderer_adapter_id
        for driver in PRESENTATION_DRIVER_REGISTRY.values()
    } == {"authored", "svg-v1"}
    with pytest.raises(TypeError):
        PRESENTATION_DRIVER_REGISTRY["future"] = PRESENTATION_DRIVER_REGISTRY[  # type: ignore[index]
            "authored"
        ]
    with pytest.raises(FrozenInstanceError):
        PRESENTATION_DRIVER_REGISTRY["authored"].capabilities.export_adapter_id = (  # type: ignore[misc]
            "other"
        )


@pytest.mark.parametrize(
    ("identity", "code"),
    [
        ({"generation_mode": "smart"}, "unsupported_generation_mode"),
        ({"mode": "adaptive"}, "unsupported_presentation_mode"),
        ({"version": "v9-future"}, "unsupported_presentation_version"),
        (
            {"mode": "authored", "theme": {"mode": "template"}},
            "presentation_mode_theme_conflict",
        ),
        (
            {
                "profile_driver_id": "standard",
                "profile_schema_version": 1,
            },
            "unsupported_presentation_driver",
        ),
        # Template V2 was removed from the driver registry (2026-09-25); a row
        # carrying its old identity -- explicit or already-stamped -- can no
        # longer resolve to anything and fails closed like any other retired
        # driver, rather than resolving to "template-v2".
        (
            {
                "generation_mode": "standard",
                "mode": "template",
                "version": "v2-standard",
                "theme": {"mode": "template"},
            },
            "unsupported_presentation_driver",
        ),
        (
            {
                "generation_mode": "standard",
                "mode": None,
                "version": "v2-standard",
            },
            "unsupported_presentation_driver",
        ),
        (
            {
                "profile_driver_id": "template-v2",
                "profile_schema_version": 1,
            },
            "unsupported_presentation_driver",
        ),
    ],
)
def test_removed_and_conflicting_identity_fails_closed(identity, code):
    with pytest.raises(PresentationProfileError) as error:
        resolve_presentation_profile(identity)
    assert error.value.code == code


def test_client_profile_and_capability_spoofs_are_never_read():
    persisted = {
        "generation_mode": "standard",
        "mode": "authored",
        "version": "v1-standard",
        "profile": {"driver_id": "template-v2"},
        "capabilities": {"export_adapter_id": "template-v2-general"},
        "driver_id": "template-v2",
    }

    profile, capabilities, _ = resolve_presentation_driver(persisted)

    assert profile.driver_id == "authored"
    assert capabilities.export_adapter_id == "authored-hybrid"
    assert GENERATION_MODE_COMPATIBILITY.encode_profile(profile) == "standard"
