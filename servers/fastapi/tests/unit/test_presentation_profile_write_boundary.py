from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
import pytest

# Import registers the canonical before-flush write boundary.
from services import database as _database  # noqa: F401
from models.sql.presentation import PresentationModel
from services.presentation_profiles import PresentationProfileError


def _presentation(**identity: object) -> PresentationModel:
    return PresentationModel(
        content="profile boundary",
        n_slides=1,
        language="en",
        **identity,
    )


@pytest.mark.parametrize(
    ("identity", "expected_driver"),
    [
        ({}, "standard"),
        ({"generation_mode": "smart"}, "smart"),
        ({"version": "v2-standard", "mode": "template"}, "template-v2"),
        ({"mode": "authored"}, "authored"),
    ],
)
def test_new_presentation_persists_server_derived_profile(
    identity: dict[str, object],
    expected_driver: str,
) -> None:
    engine = create_engine("sqlite://")
    PresentationModel.__table__.create(engine)
    try:
        with Session(engine) as session:
            presentation = _presentation(**identity)
            session.add(presentation)
            session.commit()
            assert presentation.profile_driver_id == expected_driver
            assert presentation.profile_schema_version == 1
            assert presentation.profile_backfilled_at is not None
            assert presentation.generation_mode == (
                "smart" if expected_driver == "smart" else "standard"
            )
    finally:
        engine.dispose()

def test_new_presentation_rejects_unknown_client_profile() -> None:
    engine = create_engine("sqlite://")
    PresentationModel.__table__.create(engine)
    try:
        with Session(engine) as session:
            session.add(
                _presentation(
                    profile_driver_id="future-driver",
                    profile_schema_version=1,
                )
            )
            with pytest.raises(
                PresentationProfileError,
                match="unsupported_presentation_driver",
            ):
                session.flush()
    finally:
        engine.dispose()


def test_server_owned_identity_transition_restamps_profile() -> None:
    engine = create_engine("sqlite://")
    PresentationModel.__table__.create(engine)
    try:
        with Session(engine) as session:
            presentation = _presentation()
            session.add(presentation)
            session.commit()
            assert presentation.profile_driver_id == "standard"

            presentation.version = "v2-standard"
            presentation.mode = "template"
            session.commit()
            assert presentation.profile_driver_id == "template-v2"
            assert presentation.profile_schema_version == 1
    finally:
        engine.dispose()
