import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from api.v1.ppt.router import API_V1_PPT_ROUTER
from models.sql.presentation import PresentationModel
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


def test_browser_blank_presentation_path_rejects_legacy_creation_before_persistence():
    session = BlankPresentationSession()
    app = FastAPI()
    app.include_router(API_V1_PPT_ROUTER)

    async def override_session():
        yield session

    app.dependency_overrides[get_async_session] = override_session

    with TestClient(app) as client:
        response = client.post("/api/v1/ppt/presentation/create/blank")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "legacy_blank_presentation_admission_closed"
    assert session.added == [] and session.commit_count == 0


def test_blank_presentation_gate_precedes_a_failing_database_commit():
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

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "legacy_blank_presentation_admission_closed"
    assert session.commit_count == session.rollback_count == 0


def test_blank_presentation_gate_leaves_sqlite_empty(tmp_path):
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
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "legacy_blank_presentation_admission_closed"

        async def row_counts():
            async with sessions() as session:
                presentations = (await session.scalars(select(PresentationModel))).all()
                slides = (await session.scalars(select(SlideModel))).all()
                return len(presentations), len(slides)

        assert asyncio.run(row_counts()) == (0, 0)
    finally:
        asyncio.run(engine.dispose())
