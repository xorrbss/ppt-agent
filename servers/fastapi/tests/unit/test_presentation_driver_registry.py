from dataclasses import FrozenInstanceError

import pytest

from services.presentation_profiles import (
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
                "mode": "adaptive",
                "version": "v1-standard",
                "theme": {"mode": "adaptive", "user": "builtin"},
                "deck_plan": {"slides": []},
            },
            "standard",
            "legacy-elements",
            "standard",
            "builtin",
        ),
        (
            {
                "generation_mode": "smart",
                "mode": "template",
                "version": "v1-standard",
                "community_design_ids": [11, 12],
            },
            "smart",
            "smart-html",
            "smart",
            "community",
        ),
        (
            {
                "generation_mode": "standard",
                "mode": "template",
                "version": "v2-standard",
                "theme": {"mode": "template"},
            },
            "template-v2",
            "template-v2",
            "template",
            "imported",
        ),
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
def test_four_canonical_profiles_and_drivers(
    identity, driver_id, content_model, strategy, style_source
):
    profile, capabilities, driver = resolve_presentation_driver(identity)

    assert profile.driver_id == driver_id
    assert profile.content_model == content_model
    assert profile.generation_strategy == strategy
    assert profile.style_source == style_source
    assert capabilities.driver_id == driver_id
    assert driver.driver_id == driver_id
    assert driver.validate is not None
    assert driver.capabilities is capabilities


def test_registry_is_complete_immutable_and_exposes_every_adapter_family():
    assert tuple(PRESENTATION_DRIVER_REGISTRY) == (
        "standard",
        "smart",
        "template-v2",
        "authored",
    )
    assert {
        driver.capabilities.renderer_adapter_id
        for driver in PRESENTATION_DRIVER_REGISTRY.values()
    } == {"legacy-elements", "smart-html", "template-v2", "authored"}
    assert PRESENTATION_DRIVER_REGISTRY["authored"].capabilities.export_adapter_id == (
        "authored-hybrid"
    )
    with pytest.raises(TypeError):
        PRESENTATION_DRIVER_REGISTRY["future"] = PRESENTATION_DRIVER_REGISTRY[  # type: ignore[index]
            "standard"
        ]
    with pytest.raises(FrozenInstanceError):
        PRESENTATION_DRIVER_REGISTRY["standard"].capabilities.export_adapter_id = (  # type: ignore[misc]
            "other"
        )


@pytest.mark.parametrize(
    ("identity", "code"),
    [
        ({"generation_mode": "future"}, "unsupported_generation_mode"),
        ({"mode": "future"}, "unsupported_presentation_mode"),
        ({"version": "v9-future"}, "unsupported_presentation_version"),
        (
            {
                "mode": "adaptive",
                "theme": {"mode": "template"},
            },
            "presentation_mode_theme_conflict",
        ),
        (
            {
                "generation_mode": "smart",
                "mode": "authored",
                "theme": {"mode": "authored"},
            },
            "smart_authored_identity_conflict",
        ),
        (
            {
                "mode": "template",
                "version": "v2-standard",
                "deck_plan": {"slides": []},
            },
            "presentation_deck_plan_mode_conflict",
        ),
        (
            {
                "mode": "authored",
                "theme": {"mode": "authored"},
                "version": "v2-standard",
            },
            "authored_version_conflict",
        ),
        (
            {
                "generation_mode": "standard",
                "mode": "adaptive",
                "version": "v2-standard",
            },
            "template_v2_identity_conflict",
        ),
        (
            {
                "generation_mode": "smart",
                "mode": "template",
                "version": "v2-standard",
            },
            "template_v2_generation_mode_conflict",
        ),
    ],
)
def test_unknown_future_and_conflicting_persisted_identity_fails_closed(
    identity, code
):
    with pytest.raises(PresentationProfileError) as error:
        resolve_presentation_profile(identity)
    assert error.value.code == code


def test_legacy_null_mode_v2_row_resolves_through_additive_compatibility():
    profile = resolve_presentation_profile(
        {
            "generation_mode": "standard",
            "mode": None,
            "version": "v2-standard",
        }
    )
    assert profile.driver_id == "template-v2"


def test_client_profile_and_capability_spoofs_are_never_read():
    persisted = {
        "generation_mode": "standard",
        "mode": "adaptive",
        "version": "v1-standard",
        "deck_plan": {"slides": []},
        "profile": {
            "driver_id": "authored",
            "content_model": "authored",
        },
        "capabilities": {
            "smart_selection": True,
            "export_adapter_id": "authored-hybrid",
        },
        "driver_id": "smart",
    }

    profile, capabilities, _ = resolve_presentation_driver(persisted)

    assert profile.driver_id == "standard"
    assert capabilities.editor.smart_selection is False
    assert capabilities.export_adapter_id == "legacy-general"


def test_generation_mode_is_a_codec_not_an_independent_profile_selector():
    assert GENERATION_MODE_COMPATIBILITY.source_of_truth == (
        "derived-presentation-profile"
    )
    assert "Remove only after" in GENERATION_MODE_COMPATIBILITY.removal_condition
    standard = resolve_presentation_profile({})
    smart = resolve_presentation_profile({"generation_mode": "smart"})
    assert GENERATION_MODE_COMPATIBILITY.encode_profile(standard) == "standard"
    assert GENERATION_MODE_COMPATIBILITY.encode_profile(smart) == "smart"


def test_canonical_profile_is_source_of_truth_over_stale_legacy_codec():
    profile = resolve_presentation_profile(
        {
            "profile_driver_id": "smart",
            "profile_schema_version": 1,
            "generation_mode": "standard",
        }
    )

    assert profile.driver_id == "smart"
    assert profile.generation_mode == "smart"
