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
        ({"mode": "authored"}, "authored"),
        ({"mode": "svg", "theme": {"mode": "svg"}}, "svg-v1"),
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
            assert presentation.generation_mode == "standard"
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
    # Template V2 was removed from the driver registry (2026-09-25), so a
    # transition to that identity can no longer restamp -- it fails to
    # resolve, like any other retired driver. svg-v1 is the other
    # currently-registered driver, so it is what exercises a real,
    # non-authored transition here.
    engine = create_engine("sqlite://")
    PresentationModel.__table__.create(engine)
    try:
        with Session(engine) as session:
            presentation = _presentation(mode="authored")
            session.add(presentation)
            session.commit()
            assert presentation.profile_driver_id == "authored"

            presentation.mode = "svg"
            presentation.theme = {"mode": "svg"}
            session.commit()
            assert presentation.profile_driver_id == "svg-v1"
            assert presentation.profile_schema_version == 1
    finally:
        engine.dispose()
