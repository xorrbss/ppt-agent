from datetime import UTC, datetime
import uuid

import pytest
from pydantic import ValidationError

from models.presentation_with_slides import PresentationWithSlides


def _presentation(**overrides):
    payload = {
        "id": uuid.uuid4(),
        "content": "runtime projection",
        "n_slides": 0,
        "language": "en",
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
        "slides": [],
        "generation_mode": "standard",
        "mode": "adaptive",
        "version": "v1-standard",
        "deck_plan": {"slides": []},
        "revision": 7,
        "lifecycle_status": "published",
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize(
    ("overrides", "driver_id"),
    [
        ({}, "standard"),
        (
            {
                "generation_mode": "smart",
                "mode": "template",
                "deck_plan": None,
            },
            "smart",
        ),
        (
            {
                "mode": "template",
                "version": "v2-standard",
                "deck_plan": None,
            },
            "template-v2",
        ),
        (
            {
                "mode": "authored",
                "theme": {"mode": "authored"},
                "deck_plan": None,
            },
            "authored",
        ),
    ],
)
def test_response_projects_all_server_drivers(overrides, driver_id):
    response = PresentationWithSlides.model_validate(_presentation(**overrides))

    assert response.profile.driver_id == driver_id
    assert response.capabilities.driver_id == driver_id
    assert response.revision == 7


def test_response_ignores_spoofed_profile_and_capabilities():
    response = PresentationWithSlides.model_validate(
        _presentation(
            profile={"driver_id": "authored"},
            capabilities={"driver_id": "authored"},
        )
    )

    assert response.profile.driver_id == "standard"
    assert response.capabilities.driver_id == "standard"


def test_response_fails_closed_for_future_persisted_identity():
    with pytest.raises(ValidationError, match="unsupported_presentation_version"):
        PresentationWithSlides.model_validate(_presentation(version="v9-future"))
