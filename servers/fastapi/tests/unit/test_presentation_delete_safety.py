import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select, text
from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import SQLModel

from api.v1.ppt.endpoints.presentation_crud import (
    PRESENTATION_CRUD_ROUTER,
)
from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from models.sql.template_v2 import TemplateV2
from models.sql.template_v2_local_state import TemplateV2LocalState
from models.sql.template_v2_pptx_import import TemplateV2PptxImport
from services.database import get_async_session


def _test_app(database_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def session_dependency():
        async with session_factory() as session:
            yield session

    app = FastAPI()
    app.include_router(PRESENTATION_CRUD_ROUTER, prefix="/api/v1/ppt")
    app.dependency_overrides[get_async_session] = session_dependency
    return app, engine, session_factory


def _presentation() -> PresentationModel:
    return PresentationModel(
        content="Delete safety",
        n_slides=1,
        language="en",
        mode="template",
        version="v2-standard",
    )


def test_delete_api_removes_owned_template_and_preserves_import_audit(
    tmp_path,
):
    app, engine, session_factory = _test_app(
        tmp_path / "presentation-delete.db"
    )
    presentation = _presentation()
    slide = SlideModel(
        presentation=presentation.id,
        layout_group="default",
        layout="title",
        index=0,
        content={"title": "Delete safety"},
    )
    task = AsyncPresentationGenerationTaskModel(
        id="delete-safety-task",
        status="SUCCESS",
    )
    template = TemplateV2(
        id="delete-safety-template",
        presentation_id=presentation.id,
        name="Delete safety template",
    )
    local_state = TemplateV2LocalState(
        template_id=template.id,
        presentation_id=presentation.id,
    )
    import_record = TemplateV2PptxImport(
        task_id=task.id,
        requested_template_id=template.id,
        draft_template_id=template.id,
        state="ready",
        source_filename="source.pptx",
        source_media_type=(
            "application/vnd.openxmlformats-officedocument."
            "presentationml.presentation"
        ),
        source_size_bytes=4,
        source_sha256="0" * 64,
        source_storage_key="imports/delete-safety/source.pptx",
    )

    async def setup():
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda sync_connection: SQLModel.metadata.create_all(
                    sync_connection,
                    tables=[
                        PresentationModel.__table__,
                        SlideModel.__table__,
                        AsyncPresentationGenerationTaskModel.__table__,
                        TemplateV2.__table__,
                        TemplateV2LocalState.__table__,
                        TemplateV2PptxImport.__table__,
                    ],
                )
            )
        async with session_factory() as session:
            session.add_all(
                [
                    presentation,
                    slide,
                    task,
                    template,
                    local_state,
                    import_record,
                ]
            )
            await session.commit()

    async def verify():
        async with session_factory() as session:
            assert await session.get(PresentationModel, presentation.id) is None
            assert await session.get(SlideModel, slide.id) is None
            assert await session.get(TemplateV2, template.id) is None
            assert (
                await session.scalar(
                    select(func.count()).select_from(
                        TemplateV2LocalState
                    )
                )
                == 0
            )
            retained = await session.get(
                TemplateV2PptxImport,
                import_record.id,
            )
            assert retained is not None
            assert retained.draft_template_id is None
            assert retained.source_storage_key == (
                "imports/delete-safety/source.pptx"
            )

    try:
        asyncio.run(setup())
        with TestClient(app) as client:
            response = client.delete(
                f"/api/v1/ppt/presentation/{presentation.id}"
            )
        assert response.status_code == 204
        asyncio.run(verify())
    finally:
        asyncio.run(engine.dispose())


def test_delete_api_fails_closed_when_sidecar_ownership_is_missing(tmp_path):
    app, engine, session_factory = _test_app(
        tmp_path / "presentation-delete-conflict.db"
    )
    presentation = _presentation()
    template = TemplateV2(
        id="orphaned-ownership-template",
        presentation_id=presentation.id,
        name="Missing sidecar",
    )

    async def setup():
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda sync_connection: SQLModel.metadata.create_all(
                    sync_connection,
                    tables=[
                        PresentationModel.__table__,
                        TemplateV2.__table__,
                        TemplateV2LocalState.__table__,
                    ],
                )
            )
        async with session_factory() as session:
            session.add_all([presentation, template])
            await session.commit()

    async def verify():
        async with session_factory() as session:
            assert (
                await session.get(PresentationModel, presentation.id)
                is not None
            )
            assert await session.get(TemplateV2, template.id) is not None

    try:
        asyncio.run(setup())
        with TestClient(app) as client:
            response = client.delete(
                f"/api/v1/ppt/presentation/{presentation.id}"
            )
        assert response.status_code == 409
        assert response.json()["detail"] == {
            "code": "presentation_delete_dependency_conflict",
            "presentation_id": str(presentation.id),
        }
        asyncio.run(verify())
    finally:
        asyncio.run(engine.dispose())


def test_delete_api_rolls_back_all_mutations_on_dependency_conflict(tmp_path):
    app, engine, session_factory = _test_app(
        tmp_path / "presentation-delete-rollback.db"
    )
    presentation = _presentation()
    task = AsyncPresentationGenerationTaskModel(
        id="delete-rollback-task",
        status="SUCCESS",
    )
    template = TemplateV2(
        id="delete-rollback-template",
        presentation_id=presentation.id,
        name="Rollback template",
    )
    local_state = TemplateV2LocalState(
        template_id=template.id,
        presentation_id=presentation.id,
    )
    import_record = TemplateV2PptxImport(
        task_id=task.id,
        requested_template_id=template.id,
        draft_template_id=template.id,
        state="ready",
        source_filename="rollback.pptx",
        source_media_type=(
            "application/vnd.openxmlformats-officedocument."
            "presentationml.presentation"
        ),
        source_size_bytes=4,
        source_sha256="1" * 64,
        source_storage_key="imports/delete-rollback/source.pptx",
    )

    async def setup():
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda sync_connection: SQLModel.metadata.create_all(
                    sync_connection,
                    tables=[
                        PresentationModel.__table__,
                        AsyncPresentationGenerationTaskModel.__table__,
                        TemplateV2.__table__,
                        TemplateV2LocalState.__table__,
                        TemplateV2PptxImport.__table__,
                    ],
                )
            )
            await connection.execute(
                text(
                    """
                    CREATE TABLE presentation_delete_blockers (
                        id INTEGER PRIMARY KEY,
                        presentation_id CHAR(32) NOT NULL,
                        CONSTRAINT fk_delete_blocker_presentation
                            FOREIGN KEY(presentation_id)
                            REFERENCES presentations(id)
                            ON DELETE RESTRICT
                    )
                    """
                )
            )
        async with session_factory() as session:
            session.add_all(
                [
                    presentation,
                    task,
                    template,
                    local_state,
                    import_record,
                ]
            )
            await session.commit()
            await session.execute(
                text(
                    """
                    INSERT INTO presentation_delete_blockers
                        (id, presentation_id)
                    VALUES (1, :presentation_id)
                    """
                ),
                {"presentation_id": presentation.id.hex},
            )
            await session.commit()

    async def verify_rollback_and_unblock():
        async with session_factory() as session:
            assert (
                await session.get(PresentationModel, presentation.id)
                is not None
            )
            assert await session.get(TemplateV2, template.id) is not None
            assert (
                await session.get(TemplateV2LocalState, template.id)
                is not None
            )
            retained = await session.get(
                TemplateV2PptxImport,
                import_record.id,
            )
            assert retained is not None
            assert retained.draft_template_id == template.id
            await session.execute(
                text("DELETE FROM presentation_delete_blockers WHERE id = 1")
            )
            await session.commit()

    async def verify_retry():
        async with session_factory() as session:
            assert await session.get(PresentationModel, presentation.id) is None
            assert await session.get(TemplateV2, template.id) is None
            assert (
                await session.get(TemplateV2LocalState, template.id)
                is None
            )
            retained = await session.get(
                TemplateV2PptxImport,
                import_record.id,
            )
            assert retained is not None
            assert retained.draft_template_id is None

    try:
        asyncio.run(setup())
        with TestClient(app) as client:
            conflict = client.delete(
                f"/api/v1/ppt/presentation/{presentation.id}"
            )
            assert conflict.status_code == 409
            assert conflict.json()["detail"]["code"] == (
                "presentation_delete_dependency_conflict"
            )
            asyncio.run(verify_rollback_and_unblock())
            retry = client.delete(
                f"/api/v1/ppt/presentation/{presentation.id}"
            )
            assert retry.status_code == 204
        asyncio.run(verify_retry())
    finally:
        asyncio.run(engine.dispose())
