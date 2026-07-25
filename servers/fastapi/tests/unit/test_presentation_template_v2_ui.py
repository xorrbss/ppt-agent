import asyncio
from copy import deepcopy
import json
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel, create_engine, select

from api.middlewares import SessionAuthMiddleware
from api.v1.ppt.endpoints.presentation import PRESENTATION_ROUTER
from models.sql.presentation import PresentationModel
from models.sql.presentation_version import PresentationVersionModel
from models.sql.slide import SlideModel
from services.database import get_async_session
from services.presentation_version_service import restore_version, snapshot_slides
from templates.v2.constants import (
    LEGACY_PRESENTATION_VERSION,
    TEMPLATE_V2_VERSION,
)
from templates.v2.models.elements import Text
from templates.v2.models.layouts import Component, SlideLayout
from templates.v2.persistence import canonicalize_slide_ui


def _native_ui(title: str = "Original") -> dict:
    return {
        "id": "title",
        "description": "Native editable title slide",
        "components": [
            {
                "id": "hero",
                "description": "Editable hero title component",
                "position": {"x": 0, "y": 0},
                "elements": [
                    {
                        "type": "text",
                        "position": {"x": 1, "y": 1},
                        "size": {"width": 8, "height": 1},
                        "runs": [
                            {
                                "text": title,
                                "font": {"size": 18, "bold": True},
                            }
                        ],
                        "decorative": False,
                        "name": "title",
                        "min_length": 1,
                        "max_length": 80,
                    }
                ],
            }
        ],
    }


def _slide(**changes) -> SlideModel:
    values = {
        "presentation": uuid.uuid4(),
        "layout_group": "native",
        "layout": "title",
        "index": 0,
        "content": {"title": "Original"},
        "html_content": None,
        "ui": _native_ui(),
        "properties": {"source": "template-v2"},
    }
    values.update(changes)
    return SlideModel.model_validate(values)


def test_slide_rejects_mixed_native_ui_and_authored_html():
    with pytest.raises(
        ValidationError,
        match="slide_ui_and_authored_html_cannot_coexist",
    ):
        _slide(html_content="<section>authored</section>")


def test_slide_canonicalizes_and_deep_copies_native_ui():
    native_ui = _native_ui()

    slide = _slide(ui=native_ui)
    native_ui["components"][0]["elements"][0]["runs"][0]["text"] = "mutated"

    assert (
        slide.ui["components"][0]["elements"][0]["runs"][0]["text"]
        == "Original"
    )


def test_slide_clone_preserves_lossless_ui_and_deep_copies_payloads():
    source = _slide()

    clone = source.get_new_slide(uuid.uuid4(), content={})

    assert clone.content == {}
    assert clone.ui == source.ui
    assert clone.ui is not source.ui
    assert clone.properties == source.properties
    clone.ui["components"][0]["elements"][0]["runs"][0]["text"] = "changed"
    assert (
        source.ui["components"][0]["elements"][0]["runs"][0]["text"]
        == "Original"
    )


def _malformed_native_ui_cases() -> list[dict]:
    missing_description = _native_ui()
    missing_description.pop("description")

    duplicate_component = _native_ui()
    duplicate_component["components"].append(
        deepcopy(duplicate_component["components"][0])
    )

    unknown_layout_field = _native_ui()
    unknown_layout_field["unexpected"] = True

    unknown_element_field = _native_ui()
    unknown_element_field["components"][0]["elements"][0]["unexpected"] = True

    unknown_element_type = _native_ui()
    unknown_element_type["components"][0]["elements"][0]["type"] = "mystery"

    return [
        missing_description,
        duplicate_component,
        unknown_layout_field,
        unknown_element_field,
        unknown_element_type,
    ]


@pytest.mark.parametrize("ui", _malformed_native_ui_cases())
def test_slide_rejects_malformed_duplicate_and_unknown_native_ui(ui):
    with pytest.raises(ValidationError):
        _slide(ui=ui)


@pytest.mark.parametrize(
    ("ui", "html_content"),
    [
        *[(ui, None) for ui in _malformed_native_ui_cases()],
        (_native_ui(), "<section>mixed</section>"),
    ],
    ids=[
        "missing-description",
        "duplicate-component",
        "unknown-layout-field",
        "unknown-element-field",
        "unknown-element-type",
        "mixed-ui-html",
    ],
)
def test_update_api_returns_422_for_invalid_native_ui_and_preserves_live_slide(
    tmp_path,
    monkeypatch,
    ui,
    html_content,
):
    monkeypatch.setattr("api.middlewares.is_disable_auth_enabled", lambda: False)
    monkeypatch.setattr(
        "api.middlewares.get_session_token_from_request",
        lambda request: request.headers.get("x-test-token"),
    )
    monkeypatch.setattr(
        "api.middlewares.get_auth_status",
        lambda token: {
            "configured": True,
            "authenticated": token == "valid",
            "username": "tester" if token == "valid" else None,
        },
    )

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'slide-ui-update-api.db'}",
        poolclass=NullPool,
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    presentation = PresentationModel(content="api boundary", n_slides=1, language="en")
    live = _slide(presentation=presentation.id)
    expected_ui = deepcopy(live.ui)

    async def setup_database() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda sync_connection: SQLModel.metadata.create_all(
                    sync_connection,
                    tables=[
                        PresentationModel.__table__,
                        PresentationVersionModel.__table__,
                        SlideModel.__table__,
                    ],
                )
            )
        async with session_factory() as session:
            session.add_all([presentation, live])
            await session.commit()

    async def get_test_session():
        async with session_factory() as session:
            yield session

    request_slide = live.model_dump(mode="json")
    asyncio.run(setup_database())
    app = FastAPI()
    app.add_middleware(SessionAuthMiddleware)
    app.include_router(PRESENTATION_ROUTER, prefix="/api/v1/ppt")
    app.dependency_overrides[get_async_session] = get_test_session

    request_slide["ui"] = ui
    request_slide["html_content"] = html_content
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.patch(
            "/api/v1/ppt/presentation/update",
            headers={"x-test-token": "valid"},
            json={
                "id": str(presentation.id),
                "slides": [request_slide],
            },
        )

    assert response.status_code == 422

    async def assert_unchanged() -> None:
        async with session_factory() as session:
            reopened = list(
                await session.scalars(
                    select(SlideModel).where(
                        SlideModel.presentation == presentation.id
                    )
                )
            )
            versions = list(
                await session.scalars(
                    select(PresentationVersionModel).where(
                        PresentationVersionModel.presentation_id == presentation.id
                    )
                )
            )
            assert len(reopened) == 1
            assert reopened[0].id == live.id
            assert reopened[0].ui == expected_ui
            assert versions == []

    asyncio.run(assert_unchanged())
    asyncio.run(engine.dispose())


def test_update_api_preserves_valid_nested_native_ui_in_response_and_sqlite(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr("api.middlewares.is_disable_auth_enabled", lambda: False)
    monkeypatch.setattr(
        "api.middlewares.get_session_token_from_request",
        lambda request: request.headers.get("x-test-token"),
    )
    monkeypatch.setattr(
        "api.middlewares.get_auth_status",
        lambda token: {
            "configured": True,
            "authenticated": token == "valid",
            "username": "tester" if token == "valid" else None,
        },
    )

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'slide-ui-valid-update-api.db'}",
        poolclass=NullPool,
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    presentation = PresentationModel(
        content="valid api boundary",
        n_slides=1,
        language="en",
    )
    live = _slide(presentation=presentation.id)
    updated_ui = _native_ui("Updated through ASGI")
    updated_ui["components"][0]["position"] = {"x": 0.25, "y": 0.5}
    expected_ui = canonicalize_slide_ui(updated_ui)
    assert expected_ui is not None

    async def setup_database() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda sync_connection: SQLModel.metadata.create_all(
                    sync_connection,
                    tables=[
                        PresentationModel.__table__,
                        PresentationVersionModel.__table__,
                        SlideModel.__table__,
                    ],
                )
            )
        async with session_factory() as session:
            session.add_all([presentation, live])
            await session.commit()

    async def get_test_session():
        async with session_factory() as session:
            yield session

    request_slide = live.model_dump(mode="json")
    asyncio.run(setup_database())
    app = FastAPI()
    app.add_middleware(SessionAuthMiddleware)
    app.include_router(PRESENTATION_ROUTER, prefix="/api/v1/ppt")
    app.dependency_overrides[get_async_session] = get_test_session

    request_slide["ui"] = deepcopy(updated_ui)
    with TestClient(app) as client:
        response = client.patch(
            "/api/v1/ppt/presentation/update",
            headers={"x-test-token": "valid"},
            json={
                "id": str(presentation.id),
                "slides": [request_slide],
            },
        )

    assert response.status_code == 200
    assert response.json()["slides"][0]["ui"] == expected_ui

    async def assert_persisted() -> None:
        async with session_factory() as session:
            reopened = list(
                await session.scalars(
                    select(SlideModel).where(
                        SlideModel.presentation == presentation.id
                    )
                )
            )
            versions = list(
                await session.scalars(
                    select(PresentationVersionModel).where(
                        PresentationVersionModel.presentation_id == presentation.id
                    )
                )
            )
            assert len(reopened) == 1
            assert reopened[0].ui == expected_ui
            assert len(versions) == 1
            assert versions[0].slides[0]["ui"] == expected_ui

    asyncio.run(assert_persisted())
    asyncio.run(engine.dispose())


def test_sqlite_bind_rejects_direct_constructor_schema_bypass(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'slide-ui-bind.db'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(
        engine,
        tables=[PresentationModel.__table__, SlideModel.__table__],
    )
    presentation = PresentationModel(content="contract", n_slides=1, language="en")
    bypassed = SlideModel(
        presentation=presentation.id,
        layout_group="native",
        layout="title",
        index=0,
        content={},
        ui={"id": "malformed"},
    )

    with Session(engine) as session:
        session.add_all([presentation, bypassed])
        with pytest.raises(StatementError):
            session.commit()


def test_canonicalization_and_sqlite_bind_reject_mutated_layout_model(tmp_path):
    mutated = SlideLayout.model_validate(_native_ui())
    mutated.components[0].description = "short"

    with pytest.raises(ValidationError):
        canonicalize_slide_ui(mutated)

    engine = create_engine(
        f"sqlite:///{tmp_path / 'slide-ui-mutated-model.db'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(
        engine,
        tables=[PresentationModel.__table__, SlideModel.__table__],
    )
    presentation = PresentationModel(content="contract", n_slides=1, language="en")
    bypassed = SlideModel(
        presentation=presentation.id,
        layout_group="native",
        layout="title",
        index=0,
        content={},
        ui=mutated,
    )

    with Session(engine) as session:
        session.add_all([presentation, bypassed])
        with pytest.raises(StatementError):
            session.commit()


def _ui_with_nested_mutated_component() -> dict:
    ui = _native_ui()
    component = Component.model_validate(ui["components"][0])
    component.description = "short"
    ui["components"] = [component]
    return ui


def _ui_with_nested_mutated_element() -> dict:
    ui = _native_ui()
    component = Component.model_validate(ui["components"][0])
    element = component.elements[0]
    assert isinstance(element, Text)
    element.name = {"invalid": True}
    component.elements = [element]
    ui["components"] = [component]
    return ui


@pytest.mark.parametrize(
    "ui_factory",
    [
        _ui_with_nested_mutated_component,
        _ui_with_nested_mutated_element,
    ],
    ids=["nested-mutated-component", "nested-mutated-element"],
)
def test_canonicalization_and_sqlite_bind_reject_nested_mutated_models(
    tmp_path,
    ui_factory,
):
    mutated_ui = ui_factory()

    with pytest.raises(ValidationError):
        canonicalize_slide_ui(mutated_ui)

    engine = create_engine(
        f"sqlite:///{tmp_path / f'slide-ui-{ui_factory.__name__}.db'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(
        engine,
        tables=[PresentationModel.__table__, SlideModel.__table__],
    )
    presentation = PresentationModel(
        content="nested model contract",
        n_slides=1,
        language="en",
    )
    bypassed = _slide(presentation=presentation.id, ui=None)
    # Assignment validation is deliberately bypassed to exercise the database
    # bind boundary against a nested Pydantic instance.
    bypassed.ui = mutated_ui

    with Session(engine) as session:
        session.add_all([presentation, bypassed])
        with pytest.raises(StatementError):
            session.commit()


def test_sqlite_load_rejects_corrupt_persisted_native_ui(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'slide-ui-corrupt.db'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(
        engine,
        tables=[PresentationModel.__table__, SlideModel.__table__],
    )
    presentation = PresentationModel(content="contract", n_slides=1, language="en")

    with Session(engine) as session:
        session.add(presentation)
        session.commit()
        session.exec(
            text(
                """
                INSERT INTO slides
                    (id, presentation, layout_group, layout, "index", content, ui)
                VALUES
                    (:id, :presentation, 'native', 'title', 0, '{}', :ui)
                """
            ),
            params={
                "id": uuid.uuid4().hex,
                "presentation": presentation.id.hex,
                "ui": json.dumps({"id": "corrupt"}),
            },
        )
        session.commit()
        session.expire_all()

        with pytest.raises(ValidationError):
            session.exec(select(SlideModel)).all()


def test_snapshot_restore_reopen_preserves_nested_native_ui_exactly(tmp_path):
    async def scenario() -> None:
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{tmp_path / 'slide-ui-roundtrip.db'}"
        )
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda sync_connection: SQLModel.metadata.create_all(
                    sync_connection,
                    tables=[
                        PresentationModel.__table__,
                        PresentationVersionModel.__table__,
                        SlideModel.__table__,
                    ],
                )
            )

        presentation = PresentationModel(
            content="round trip",
            n_slides=1,
            language="en",
        )
        original = _slide(presentation=presentation.id)
        expected_ui = deepcopy(original.ui)
        original_dump = original.model_dump(mode="json")

        async with session_factory() as session:
            session.add_all([presentation, original])
            await session.commit()
            version = await snapshot_slides(
                session,
                presentation.id,
                [original_dump],
                label="native snapshot",
                force=True,
            )
            assert version is not None
            await session.commit()
            version_id = version.id

        original_dump["ui"]["components"][0]["elements"][0]["runs"][0][
            "text"
        ] = "mutated after snapshot"

        async with session_factory() as session:
            restored = await restore_version(session, presentation.id, version_id)
            assert restored is not None
            await session.commit()

        async with session_factory() as session:
            reopened = list(
                await session.scalars(
                    select(SlideModel)
                    .where(SlideModel.presentation == presentation.id)
                    .order_by(SlideModel.index)
                )
            )
            assert len(reopened) == 1
            assert reopened[0].ui == expected_ui
            assert reopened[0].ui is not expected_ui

        await engine.dispose()

    asyncio.run(scenario())


def test_corrupt_snapshot_fails_before_live_slides_are_replaced(tmp_path):
    async def scenario() -> None:
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{tmp_path / 'slide-ui-restore-fail.db'}"
        )
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda sync_connection: SQLModel.metadata.create_all(
                    sync_connection,
                    tables=[
                        PresentationModel.__table__,
                        PresentationVersionModel.__table__,
                        SlideModel.__table__,
                    ],
                )
            )

        presentation = PresentationModel(
            content="fail closed",
            n_slides=1,
            language="en",
        )
        live = _slide(presentation=presentation.id)
        corrupt = PresentationVersionModel(
            presentation_id=presentation.id,
            slides=[
                {
                    "layout_group": "native",
                    "layout": "title",
                    "index": 0,
                    "content": {},
                    "html_content": None,
                    "ui": {"id": "corrupt"},
                }
            ],
        )
        expected_ui = deepcopy(live.ui)

        async with session_factory() as session:
            session.add_all([presentation, live, corrupt])
            await session.commit()
            corrupt_id = corrupt.id

        async with session_factory() as session:
            with pytest.raises(ValidationError):
                await restore_version(session, presentation.id, corrupt_id)
            await session.rollback()

        async with session_factory() as session:
            reopened = list(
                await session.scalars(
                    select(SlideModel).where(
                        SlideModel.presentation == presentation.id
                    )
                )
            )
            assert len(reopened) == 1
            assert reopened[0].id == live.id
            assert reopened[0].ui == expected_ui

        await engine.dispose()

    asyncio.run(scenario())


def test_presentation_version_defaults_legacy_and_clone_preserves_identity():
    legacy = PresentationModel(content="legacy", n_slides=1, language="en")
    native = PresentationModel(
        content="native",
        n_slides=1,
        language="en",
        mode="template",
        layout={"name": "native"},
        version=TEMPLATE_V2_VERSION,
    )

    assert legacy.version == LEGACY_PRESENTATION_VERSION
    assert native.get_new_presentation().version == TEMPLATE_V2_VERSION


def test_sqlite_constraint_accepts_each_native_or_authored_payload_but_not_both(
    tmp_path,
):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'slide-ui-invariant.db'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(
        engine,
        tables=[PresentationModel.__table__, SlideModel.__table__],
    )
    presentation = PresentationModel(content="contract", n_slides=3, language="en")
    authored = _slide(
        presentation=presentation.id,
        index=0,
        ui=None,
        html_content="<section>authored</section>",
    )
    native = _slide(presentation=presentation.id, index=1)
    mixed = _slide(presentation=presentation.id, index=2)
    # Simulate an invalid row from a bulk/legacy writer that bypasses Pydantic.
    mixed.html_content = "<section>mixed</section>"

    with Session(engine) as session:
        session.add_all([presentation, authored, native])
        session.commit()
        session.add(mixed)
        with pytest.raises(IntegrityError):
            session.commit()
