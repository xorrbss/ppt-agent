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
        "mode": "svg",
        "theme": {"mode": "svg"},
        "version": "v1-standard",
        "deck_plan": None,
        "revision": 7,
        "lifecycle_status": "published",
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize(
    ("overrides", "driver_id"),
    [
        ({}, "svg-v1"),
    ],
)
def test_response_projects_all_server_drivers(overrides, driver_id):
    response = PresentationWithSlides.model_validate(_presentation(**overrides))

    assert response.profile.driver_id == driver_id
    assert response.capabilities.driver_id == driver_id
    assert response.revision == 7


def test_response_rejects_a_removed_template_v2_identity():
    """Template V2 was removed from the driver registry (2026-09-25).

    A row that used to describe it -- explicit mode/version or the bare
    default -- no longer resolves a profile at all, so the projection fails
    closed instead of naming a ``template-v2`` driver.
    """
    with pytest.raises(ValidationError, match="unsupported_presentation_driver"):
        PresentationWithSlides.model_validate(
            _presentation(mode="template", version="v2-standard", theme=None)
        )


def test_response_rejects_a_removed_authored_identity():
    """The authored driver was removed on 2026-11-10 (option A): an existing
    authored row fails closed like a Template V2 row."""
    with pytest.raises(ValidationError, match="unsupported_presentation_driver"):
        PresentationWithSlides.model_validate(
            _presentation(mode="authored", theme={"mode": "authored"})
        )


def test_response_ignores_spoofed_profile_and_capabilities():
    response = PresentationWithSlides.model_validate(
        _presentation(
            profile={"driver_id": "authored"},
            capabilities={"driver_id": "authored"},
        )
    )

    assert response.profile.driver_id == "svg-v1"
    assert response.capabilities.driver_id == "svg-v1"


def test_response_fails_closed_for_future_persisted_identity():
    with pytest.raises(ValidationError, match="unsupported_presentation_version"):
        PresentationWithSlides.model_validate(_presentation(version="v9-future"))
