"""Focused product-contract coverage for canonical Authored persistence."""

from __future__ import annotations

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


def test_canonical_save_cas_rebase_undo_redo_and_sqlite_restart_replay(
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
            session.add(
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
            initial_response = await client.get(base_path)
            assert initial_response.status_code == 200, initial_response.text
            initial = initial_response.json()
            assert initial["revision"] == 0
            assert initial["sourceDigest"].startswith("sha256:")
            assert initial["slides"][0]["assetManifest"][0]["qid"] == "hero"
            assert initial["slides"][0]["assetManifest"][0]["digestKind"] == "locator"
            initial_capabilities = {
                item["qid"]: item for item in initial["slides"][0]["capabilities"]
            }
            assert initial_capabilities["title"]["editable"] is True
            assert initial_capabilities["complex"]["editable"] is False
            assert initial_capabilities["complex"]["reason"] == "unsupported_css"
            table_capabilities = {
                item["qid"]: item for item in initial["slides"][1]["capabilities"]
            }
            assert table_capabilities["table"]["editable"] is False
            assert table_capabilities["cell"]["fields"] == ["text", "style"]

            first_save_response = await client.post(
                f"{base_path}/patches",
                json={
                    "expectedRevision": 0,
                    "expectedDigest": initial["sourceDigest"],
                    "patches": [
                        {
                            "slideId": str(title_slide_id),
                            "actions": [
                                {"action": "setText", "qid": "title", "text": "New title"},
                                {
                                    "action": "replaceImage",
                                    "qid": "hero",
                                    "src": "/app_data/images/users/owner/new.png",
                                    "alt": "Replacement",
                                },
                            ],
                        }
                    ],
                },
            )
            assert first_save_response.status_code == 200, first_save_response.text
            first_save = first_save_response.json()
            assert first_save["revision"] == 1
            assert first_save["slides"][0]["slideRevision"] == 1
            assert "New title" in first_save["slides"][0]["html"]
            assert first_save["slides"][0]["assetManifest"][0]["source"] == "/app_data/images/users/owner/new.png"

            identity_response = await client.get(base_path)
            assert identity_response.status_code == 200
            assert identity_response.json() == first_save

            stale_response = await client.post(
                f"{base_path}/patches",
                json={
                    "expectedRevision": 0,
                    "patches": [
                        {
                            "slideId": str(table_slide_id),
                            "actions": [
                                {"action": "setText", "qid": "cell", "text": "11"}
                            ],
                        }
                    ],
                },
            )
            assert stale_response.status_code == 409
            assert stale_response.json()["code"] == "authored_canonical_revision_conflict"
            assert stale_response.json()["currentRevision"] == 1

            rebased_response = await client.post(
                f"{base_path}/patches",
                json={
                    "expectedRevision": 0,
                    "rebaseOnConflict": True,
                    "patches": [
                        {
                            "slideId": str(table_slide_id),
                            "actions": [
                                {"action": "setText", "qid": "cell", "text": "11"}
                            ],
                        }
                    ],
                },
            )
            assert rebased_response.status_code == 200, rebased_response.text
            rebased = rebased_response.json()
            assert rebased["revision"] == 2
            assert rebased["rebasedFromRevision"] == 0
            assert rebased["slides"][1]["slideRevision"] == 1

            overlapping_rebase = await client.post(
                f"{base_path}/patches",
                json={
                    "expectedRevision": 0,
                    "rebaseOnConflict": True,
                    "patches": [
                        {
                            "slideId": str(title_slide_id),
                            "actions": [
                                {"action": "setText", "qid": "title", "text": "Conflict"}
                            ],
                        }
                    ],
                },
            )
            assert overlapping_rebase.status_code == 409
            assert overlapping_rebase.json()["code"] == "authored_canonical_rebase_conflict"

            undo_response = await client.post(
                f"{base_path}/undo",
                json={
                    "expectedRevision": 2,
                    "expectedDigest": rebased["sourceDigest"],
                },
            )
            assert undo_response.status_code == 200, undo_response.text
            undone = undo_response.json()
            assert undone["revision"] == 3
            assert ">10<" in undone["slides"][1]["html"]
            assert undone["history"]["canRedo"] is True

            redo_response = await client.post(
                f"{base_path}/redo",
                json={
                    "expectedRevision": 3,
                    "expectedDigest": undone["sourceDigest"],
                },
            )
            assert redo_response.status_code == 200, redo_response.text
            redone = redo_response.json()
            assert redone["revision"] == 4
            assert ">11<" in redone["slides"][1]["html"]
            assert redone["history"]["canRedo"] is False

            history_response = await client.get(f"{base_path}/history")
            assert history_response.status_code == 200, history_response.text
            assert [
                entry["operation"] for entry in history_response.json()["entries"]
            ] == ["initialize", "patch", "patch", "undo", "redo"]

            raw_html_response = await client.post(
                f"{base_path}/patches",
                json={
                    "expectedRevision": 4,
                    "html": "<script>alert(1)</script>",
                    "patches": [],
                },
            )
            assert raw_html_response.status_code == 422

        await engine.dispose()

        restarted_engine, restarted_maker, restarted_app = await build_runtime()
        restarted_transport = httpx.ASGITransport(app=restarted_app)
        async with httpx.AsyncClient(
            transport=restarted_transport, base_url="http://test"
        ) as restarted_client:
            replay_response = await restarted_client.get(
                f"/api/v1/ppt/presentation/{presentation_id}/canonical"
            )
            assert replay_response.status_code == 200, replay_response.text
            assert replay_response.json() == redone
        async with restarted_maker() as session:
            revisions = (
                await session.execute(
                    select(AuthoredCanonicalRevision)
                    .where(AuthoredCanonicalRevision.presentation_id == presentation_id)
                    .order_by(AuthoredCanonicalRevision.revision)
                )
            ).scalars().all()
            slide = await session.get(SlideModel, table_slide_id)
            assert [row.revision for row in revisions] == [0, 1, 2, 3, 4]
            assert slide is not None and ">11<" in (slide.html_content or "")
        await restarted_engine.dispose()

    asyncio.run(scenario())
