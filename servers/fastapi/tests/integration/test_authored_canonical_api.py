"""Focused product-contract coverage for canonical Authored persistence."""

from __future__ import annotations

from tests.support.existing_authored_rows import add_existing_authored_presentation
import asyncio
import uuid

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel, select

from api.v1.ppt.endpoints.authored_operations import AUTHORED_OPERATIONS_ROUTER
from models.sql.authored_canonical_revision import AuthoredCanonicalRevision
from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from services import database


def test_canonical_editor_refuses_a_pre_existing_authored_deck(
    tmp_path,
) -> None:
    async def scenario() -> None:
        database_path = tmp_path / "authored-canonical.db"
        database_url = f"sqlite+aiosqlite:///{database_path}"
        presentation_id = uuid.uuid4()
        title_slide_id = uuid.uuid4()
        table_slide_id = uuid.uuid4()

        async def build_runtime():
            engine = create_async_engine(database_url)
            maker = async_sessionmaker(engine, expire_on_commit=False)

            async def session_override():
                async with maker() as session:
                    yield session

            app = FastAPI()
            app.dependency_overrides[database.get_async_session] = session_override
            app.include_router(AUTHORED_OPERATIONS_ROUTER, prefix="/api/v1/ppt")
            return engine, maker, app

        engine, maker, app = await build_runtime()
        async with engine.begin() as connection:
            await connection.exec_driver_sql("PRAGMA journal_mode=WAL")
            await connection.run_sync(
                SQLModel.metadata.create_all,
                tables=[
                    PresentationModel.__table__,
                    SlideModel.__table__,
                    AuthoredCanonicalRevision.__table__,
                ],
            )
        async with maker() as session:
            await add_existing_authored_presentation(session, 
                PresentationModel(
                    id=presentation_id,
                    content="Canonical Authored test",
                    n_slides=2,
                    language="ko",
                    title="Canonical",
                    mode="authored",
                    revision=0,
                )
            )
            session.add_all(
                [
                    SlideModel(
                        id=title_slide_id,
                        presentation=presentation_id,
                        layout_group="authored",
                        layout="authored:default",
                        index=0,
                        content={"__authored__": True},
                        html_content=(
                            "<!doctype html><html><head><style>.hero{color:#111}</style></head>"
                            "<body><main data-qid='slide-root'>"
                            "<h1 data-qid='title'>Old title</h1>"
                            "<div data-qid='complex' style='filter: blur(2px)'>"
                            "<span data-qid='complex-text'>Complex</span></div>"
                            "<img data-qid='hero' src='https://assets.example/old.png'>"
                            "</main></body></html>"
                        ),
                    ),
                    SlideModel(
                        id=table_slide_id,
                        presentation=presentation_id,
                        layout_group="authored",
                        layout="authored:default",
                        index=1,
                        content={"__authored__": True},
                        html_content=(
                            "<!doctype html><html><body>"
                            "<table data-qid='table'><tr data-qid='row'>"
                            "<td data-qid='cell'>10</td></tr></table>"
                            "</body></html>"
                        ),
                    ),
                ]
            )
            await session.commit()

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            base_path = f"/api/v1/ppt/presentation/{presentation_id}/canonical"
            # The authored driver was removed on 2026-11-10 (option A): the
            # canonical editor of an existing authored deck answers the same
            # 422 as its read and writes no canonical revision.
            for method, path, body in (
                ("GET", base_path, None),
                ("GET", f"{base_path}/history", None),
                ("POST", f"{base_path}/undo", {"expectedRevision": 0}),
                ("POST", f"{base_path}/redo", {"expectedRevision": 0}),
                ("POST", f"{base_path}/patches", {
                    "expectedRevision": 0,
                    "patches": [{
                        "slideId": str(table_slide_id),
                        "actions": [{"action": "setText", "qid": "cell", "text": "11"}],
                    }],
                }),
            ):
                response = await client.request(method, path, json=body)
                assert response.status_code == 422, (path, response.text)
                assert response.json()["detail"] == "unsupported_presentation_driver"
        async with maker() as session:
            assert (await session.execute(select(AuthoredCanonicalRevision))).first() is None
            slide = await session.get(SlideModel, table_slide_id)
            assert slide is not None and ">10<" in (slide.html_content or "")
        await engine.dispose()

    asyncio.run(scenario())
