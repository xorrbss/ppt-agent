import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from api.v1.ppt.router import API_V1_PPT_ROUTER
from models.sql.presentation import PresentationModel, PresentationVersion
from models.sql.slide import SlideModel
from services.database import get_async_session
from utils.datetime_utils import get_current_utc_datetime


class BlankPresentationSession:
    def __init__(self):
        self.added = []
        self.commit_count = 0
        self.rollback_count = 0

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        self.commit_count += 1

    async def rollback(self):
        self.rollback_count += 1

    async def refresh(self, value):
        if isinstance(value, PresentationModel):
            now = get_current_utc_datetime()
            value.created_at = value.created_at or now
            value.updated_at = value.updated_at or now


def test_browser_blank_presentation_path_reaches_fastapi_and_persists_editable_slide():
    session = BlankPresentationSession()
    app = FastAPI()
    app.include_router(API_V1_PPT_ROUTER)

    async def override_session():
        yield session

    app.dependency_overrides[get_async_session] = override_session

    with TestClient(app) as client:
        response = client.post("/api/v1/ppt/presentation/create/blank")

    assert response.status_code == 201
    payload = response.json()
    assert payload["version"] == PresentationVersion.V2_STANDARD
    assert payload["title"] == "Untitled Presentation"
    assert payload["n_slides"] == 1
    assert len(payload["slides"]) == 1
    assert payload["slides"][0]["layout_group"] == "blank"
    assert payload["slides"][0]["layout"] == "__blank_slide__"
    assert payload["slides"][0]["ui"] is None

    assert session.commit_count == 1
    presentation = next(
        value for value in session.added if isinstance(value, PresentationModel)
    )
    slide = next(value for value in session.added if isinstance(value, SlideModel))
    assert slide.presentation == presentation.id


def test_blank_presentation_transaction_rolls_back_on_commit_failure():
    class FailingSession(BlankPresentationSession):
        async def commit(self):
            self.commit_count += 1
            raise RuntimeError("database unavailable")

    session = FailingSession()
    app = FastAPI()
    app.include_router(API_V1_PPT_ROUTER)

    async def override_session():
        yield session

    app.dependency_overrides[get_async_session] = override_session

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/api/v1/ppt/presentation/create/blank")

    assert response.status_code == 500
    assert session.commit_count == 1
    assert session.rollback_count == 1


def test_blank_presentation_contract_persists_through_sqlite(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'blank-presentation.db'}"
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def prepare_database():
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda sync_connection: SQLModel.metadata.create_all(
                    sync_connection,
                    tables=[PresentationModel.__table__, SlideModel.__table__],
                )
            )

    asyncio.run(prepare_database())

    app = FastAPI()
    app.include_router(API_V1_PPT_ROUTER)

    async def override_session():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_async_session] = override_session
    try:
        with TestClient(app) as client:
            response = client.post("/api/v1/ppt/presentation/create/blank")
        assert response.status_code == 201
        payload = response.json()
        assert payload["slides"][0]["ui"] is None
        assert payload["slides"][0]["layout"] == "__blank_slide__"
    finally:
        asyncio.run(engine.dispose())
