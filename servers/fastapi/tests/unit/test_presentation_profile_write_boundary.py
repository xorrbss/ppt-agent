from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
import pytest

# Import registers the canonical before-flush write boundary.
from services import database as _database  # noqa: F401
from models.sql.presentation import PresentationModel
from models.presentation_profiles import PresentationProfileError
from tests.support.existing_authored_rows import add_existing_authored_presentation_sync


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


def test_new_authored_presentation_is_refused() -> None:
    """The authored driver was removed on 2026-11-10 (authored-removal P2): a
    new authored row can no longer be written, by mode or by theme mode."""
    engine = create_engine("sqlite://")
    PresentationModel.__table__.create(engine)
    try:
        for identity in ({"mode": "authored"}, {"theme": {"mode": "authored"}}):
            with Session(engine) as session:
                session.add(_presentation(**identity))
                with pytest.raises(
                    PresentationProfileError,
                    match="unsupported_presentation_driver",
                ):
                    session.flush()
    finally:
        engine.dispose()


def test_identity_change_on_a_pre_existing_authored_row_is_refused() -> None:
    """An authored row written before the removal can still be read, but no
    identity write can restamp it -- not even a transition to svg, which would
    relabel authored HTML slides as an svg deck."""
    engine = create_engine("sqlite://")
    PresentationModel.__table__.create(engine)
    try:
        with Session(engine) as session:
            presentation = _presentation(mode="authored")
            add_existing_authored_presentation_sync(session, presentation)
            session.commit()
            stored = session.get(PresentationModel, presentation.id)
            assert stored.profile_driver_id == "authored"

            stored.mode = "svg"
            stored.theme = {"mode": "svg"}
            with pytest.raises(
                PresentationProfileError,
                match="unsupported_presentation_driver",
            ):
                session.flush()
    finally:
        engine.dispose()


def test_template_operation_staging_presentation_persists_svg_profile(monkeypatch) -> None:
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    import uuid

    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from services.svg_templates import registration

    owner = uuid.uuid4()
    operation = SimpleNamespace(max_attempts=3)
    conversation = SimpleNamespace(id=uuid.uuid4())
    bootstrap = AsyncMock(return_value=(conversation, None, operation, False))
    monkeypatch.setattr(registration, "require_new_template_admission", lambda: None)
    monkeypatch.setattr(registration, "principal", lambda: (owner, "default"))
    monkeypatch.setattr(registration, "bootstrap_workspace", bootstrap)

    async def exercise():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(PresentationModel.__table__.create)
            async with AsyncSession(engine) as session:
                returned = await registration.new_operation(
                    session, kind="svg.template.analyze",
                    payload={"registration_mode": "framed"}, name="template analysis",
                )
                assert returned == (conversation, operation)
                presentation_id = bootstrap.call_args.kwargs["presentation_id"]
                presentation = await session.get(PresentationModel, presentation_id)
                assert presentation.owner_id == owner
                assert presentation.lifecycle_status == "staging"
                assert presentation.n_slides == 0
                assert presentation.mode == "svg"
                assert presentation.profile_driver_id == "svg-v1"
                assert presentation.profile_schema_version == 1
                assert presentation.profile_backfilled_at is not None
                assert operation.max_attempts == 1
                assert bootstrap.call_args.kwargs["commit"] is False
        finally:
            await engine.dispose()

    asyncio.run(exercise())
