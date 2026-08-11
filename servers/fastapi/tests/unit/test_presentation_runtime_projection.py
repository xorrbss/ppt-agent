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


def test_response_normalizes_the_legacy_authored_adaptive_conflict():
    presentation_id = uuid.uuid4()
    response = PresentationWithSlides.model_validate(
        _presentation(
            id=presentation_id,
            mode="adaptive",
            deck_plan=None,
            theme={"mode": "authored", "primary": "#2563EB"},
            slides=[
                {
                    "presentation": presentation_id,
                    "layout_group": "authored",
                    "layout": "authored:COVER",
                    "index": 0,
                    "content": {
                        "__authored__": True,
                        "image": "/static/cover.png",
                    },
                    "html_content": "<section>Authored cover</section>",
                }
            ],
        )
    )

    assert response.mode == "authored"
    assert response.profile.driver_id == "authored"
    assert response.capabilities.driver_id == "authored"


def test_response_keeps_failing_closed_without_authored_slide_evidence():
    presentation_id = uuid.uuid4()
    with pytest.raises(ValidationError, match="presentation_mode_theme_conflict"):
        PresentationWithSlides.model_validate(
            _presentation(
                id=presentation_id,
                mode="adaptive",
                deck_plan={"slides": []},
                theme={"mode": "authored"},
                slides=[
                    {
                        "presentation": presentation_id,
                        "layout_group": "native",
                        "layout": "title",
                        "index": 0,
                        "content": {"title": "Not authored"},
                    }
                ],
            )
        )


def test_response_fails_closed_for_future_persisted_identity():
    with pytest.raises(ValidationError, match="unsupported_presentation_version"):
        PresentationWithSlides.model_validate(_presentation(version="v9-future"))
