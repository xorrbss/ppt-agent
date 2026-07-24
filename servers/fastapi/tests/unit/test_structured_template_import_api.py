from __future__ import annotations

import asyncio
import io
from pathlib import Path
import zipfile

from fastapi import HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from starlette.datastructures import Headers
from starlette.requests import Request

from api.v1.ppt.endpoints import structured_template_imports as imports_api
from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from models.sql.template_v2 import TemplateV2
from models.sql.template_v2_local_state import TemplateV2LocalState
from models.sql.template_v2_pptx_import import TemplateV2PptxImport
from services.template_v2_pptx_storage import PPTX_MEDIA_TYPE


def _pptx_bytes() -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", b"<Types/>")
        archive.writestr(
            "ppt/presentation.xml",
            b"""
            <p:presentation
              xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
              xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
              <p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst>
              <p:sldSz cx="12192000" cy="6858000"/>
            </p:presentation>
            """,
        )
        archive.writestr(
            "ppt/_rels/presentation.xml.rels",
            b"""
            <Relationships
              xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
              <Relationship Id="rId1"
                Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide"
                Target="slides/slide1.xml"/>
            </Relationships>
            """,
        )
        archive.writestr(
            "ppt/slides/slide1.xml",
            b"""
            <p:sld
              xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
              xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
              <p:cSld><p:spTree><p:nvGrpSpPr/><p:grpSpPr/></p:spTree></p:cSld>
            </p:sld>
            """,
        )
    return stream.getvalue()


def _upload(payload: bytes) -> UploadFile:
    return UploadFile(
        file=io.BytesIO(payload),
        filename="source.pptx",
        headers=Headers({"content-type": PPTX_MEDIA_TYPE}),
    )


def _request(owner: str) -> Request:
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/ppt/structured-templates/imports",
            "headers": [],
            "query_string": b"",
            "scheme": "http",
            "server": ("test", 80),
            "client": ("test", 1),
        }
    )
    request.state.auth_username = owner
    return request


async def _database(path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: SQLModel.metadata.create_all(
                sync_connection,
                tables=[
                    PresentationModel.__table__,
                    SlideModel.__table__,
                    TemplateV2.__table__,
                    TemplateV2LocalState.__table__,
                    AsyncPresentationGenerationTaskModel.__table__,
                    TemplateV2PptxImport.__table__,
                ],
            )
        )
    return engine, maker


def test_create_is_idempotent_and_cross_owner_reads_are_hidden(
    tmp_path: Path,
    monkeypatch,
) -> None:
    async def scenario() -> None:
        engine, maker = await _database(tmp_path / "imports-api.sqlite")
        monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path / "app-data"))
        monkeypatch.setenv("ENABLE_TEMPLATE_V2", "true")
        monkeypatch.setenv(
            "TEMPLATE_V2_TEMPLATE_ALLOWLIST",
            "safe-template,other-template",
        )
        monkeypatch.setattr(
            imports_api,
            "get_request_owner_scope",
            lambda request: request.state.auth_username,
        )
        notifications: list[str] = []
        monkeypatch.setattr(
            imports_api,
            "notify_template_v2_pptx_dispatcher",
            lambda: notifications.append("queued"),
        )
        payload = _pptx_bytes()
        try:
            async with maker() as session:
                first = await imports_api.create_structured_template_import(
                    request=_request("owner-a"),
                    template_id="safe-template",
                    pptx_file=_upload(payload),
                    idempotency_key="stable-request-key",
                    sql_session=session,
                )
            async with maker() as session:
                repeated = await imports_api.create_structured_template_import(
                    request=_request("owner-a"),
                    template_id="safe-template",
                    pptx_file=_upload(payload),
                    idempotency_key="stable-request-key",
                    sql_session=session,
                )
            assert repeated.id == first.id
            assert notifications == ["queued"]

            async with maker() as session:
                try:
                    await imports_api.create_structured_template_import(
                        request=_request("owner-a"),
                        template_id="other-template",
                        pptx_file=_upload(payload),
                        idempotency_key="stable-request-key",
                        sql_session=session,
                    )
                except HTTPException as error:
                    assert error.status_code == 409
                    assert "different import" in error.detail
                else:
                    raise AssertionError("idempotency key mismatch was accepted")

            async with maker() as session:
                assert (
                    await session.scalar(
                        select(func.count()).select_from(
                            TemplateV2PptxImport
                        )
                    )
                    == 1
                )
                try:
                    await imports_api.get_structured_template_import(
                        first.id,
                        request=_request("owner-b"),
                        sql_session=session,
                    )
                except HTTPException as error:
                    assert error.status_code == 404
                else:
                    raise AssertionError("cross-owner import read was exposed")
        finally:
            await engine.dispose()

    asyncio.run(scenario())
