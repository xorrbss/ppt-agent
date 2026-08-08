from __future__ import annotations

import asyncio
import io
from pathlib import Path
import uuid
import zipfile

from fastapi import HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from starlette.datastructures import Headers
from starlette.requests import Request

from api.v1.ppt.endpoints import structured_template_imports as imports_api
from api.v1.ppt.router import API_V1_PPT_ROUTER
from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from models.sql.template_v2 import TemplateV2
from models.sql.template_v2_local_state import TemplateV2LocalState
from models.sql.template_v2_pptx_import import TemplateV2PptxImport
from services.template_v2_pptx_storage import (
    PPTX_MEDIA_TYPE,
    PRIVATE_ASSET_URL_PREFIX,
    private_asset_reference,
    resolve_private_asset,
)


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


def _asset_route():
    return next(
        route
        for route in API_V1_PPT_ROUTER.routes
        if getattr(route, "endpoint", None)
        is imports_api.get_structured_template_import_asset
    )


def _write_asset(import_id: uuid.UUID, asset_name: str, payload: bytes) -> None:
    target = resolve_private_asset(
        private_asset_reference(import_id, asset_name),
        expected_import_id=import_id,
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)


def test_private_asset_url_prefix_matches_the_mounted_asset_route() -> None:
    # A router prefix change must break loudly here instead of letting the
    # storage module persist dead asset URLs into template layouts.
    route_path = _asset_route().path
    import_id = uuid.uuid4()

    assert route_path.startswith(f"{PRIVATE_ASSET_URL_PREFIX}/")
    assert route_path.format(
        import_id=import_id,
        asset_name="relocated-media.png",
    ) == private_asset_reference(import_id, "relocated-media.png")


def test_import_assets_are_served_only_to_their_owner(
    tmp_path: Path,
    monkeypatch,
) -> None:
    async def scenario() -> None:
        engine, maker = await _database(tmp_path / "import-assets.sqlite")
        monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path / "app-data"))
        monkeypatch.setenv("ENABLE_TEMPLATE_V2", "true")
        monkeypatch.setenv("TEMPLATE_V2_TEMPLATE_ALLOWLIST", "safe-template")
        monkeypatch.setattr(
            imports_api,
            "get_request_owner_scope",
            lambda request: request.state.auth_username,
        )
        monkeypatch.setattr(
            imports_api,
            "notify_template_v2_pptx_dispatcher",
            lambda: None,
        )
        payload = b"\x89PNG\r\n\x1a\nrelocated-runtime-media"
        asset_name = "relocated-media.png"
        try:
            async with maker() as session:
                created = await imports_api.create_structured_template_import(
                    request=_request("owner-a"),
                    template_id="safe-template",
                    pptx_file=_upload(_pptx_bytes()),
                    idempotency_key="asset-request-key",
                    sql_session=session,
                )
            _write_asset(created.id, asset_name, payload)

            async with maker() as session:
                response = await imports_api.get_structured_template_import_asset(
                    created.id,
                    asset_name,
                    request=_request("owner-a"),
                    sql_session=session,
                )
            assert response.media_type == "image/png"
            assert Path(response.path).read_bytes() == payload

            async with maker() as session:
                try:
                    await imports_api.get_structured_template_import_asset(
                        created.id,
                        asset_name,
                        request=_request("owner-b"),
                        sql_session=session,
                    )
                except HTTPException as error:
                    assert error.status_code == 404
                else:
                    raise AssertionError("cross-owner asset read was exposed")

            for unsafe_name in (
                "..",
                "../source.pptx",
                "relocated-media.png/../../source.pptx",
                "relocated-media.pptx",
            ):
                async with maker() as session:
                    try:
                        await imports_api.get_structured_template_import_asset(
                            created.id,
                            unsafe_name,
                            request=_request("owner-a"),
                            sql_session=session,
                        )
                    except HTTPException as error:
                        assert error.status_code == 404
                    else:
                        raise AssertionError(
                            f"unsafe asset reference was served: {unsafe_name}"
                        )

            async with maker() as session:
                try:
                    await imports_api.get_structured_template_import_asset(
                        created.id,
                        "never-relocated.png",
                        request=_request("owner-a"),
                        sql_session=session,
                    )
                except HTTPException as error:
                    assert error.status_code == 404
                else:
                    raise AssertionError("missing asset was reported as served")
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_import_assets_stay_readable_when_the_rollout_flag_is_off(
    tmp_path: Path,
    monkeypatch,
) -> None:
    async def scenario() -> None:
        engine, maker = await _database(tmp_path / "import-assets-flag-off.sqlite")
        monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path / "app-data"))
        monkeypatch.setenv("ENABLE_TEMPLATE_V2", "true")
        monkeypatch.setenv("TEMPLATE_V2_TEMPLATE_ALLOWLIST", "safe-template")
        monkeypatch.setattr(
            imports_api,
            "get_request_owner_scope",
            lambda request: request.state.auth_username,
        )
        monkeypatch.setattr(
            imports_api,
            "notify_template_v2_pptx_dispatcher",
            lambda: None,
        )
        payload = b"\x89PNG\r\n\x1a\npersisted-after-rollback"
        asset_name = "relocated-media.png"
        try:
            async with maker() as session:
                created = await imports_api.create_structured_template_import(
                    request=_request("owner-a"),
                    template_id="safe-template",
                    pptx_file=_upload(_pptx_bytes()),
                    idempotency_key="asset-flag-off-key",
                    sql_session=session,
                )
            _write_asset(created.id, asset_name, payload)

            monkeypatch.setenv("ENABLE_TEMPLATE_V2", "false")
            monkeypatch.delenv("TEMPLATE_V2_TEMPLATE_ALLOWLIST", raising=False)

            async with maker() as session:
                # The write policy is now closed; confirm that only writes are gated.
                try:
                    await imports_api.cancel_structured_template_import(
                        created.id,
                        request=_request("owner-a"),
                        mutation=imports_api.ImportMutationRequest(
                            expected_revision=created.revision,
                        ),
                        sql_session=session,
                    )
                except HTTPException as error:
                    assert error.status_code == 403
                else:
                    raise AssertionError("write survived the disabled rollout flag")

            async with maker() as session:
                response = await imports_api.get_structured_template_import_asset(
                    created.id,
                    asset_name,
                    request=_request("owner-a"),
                    sql_session=session,
                )
            assert Path(response.path).read_bytes() == payload
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_confirmed_assets_survive_a_credential_rotation(tmp_path, monkeypatch) -> None:
    """`owner_scope` is an HMAC over AUTH_SECRET_KEY, which a password change rotates.

    Scoping the asset more tightly than the template that embeds it meant a rotation
    silently blanked every previously imported deck while export still returned 200.
    """

    async def scenario() -> None:
        engine, maker = await _database(tmp_path / "rotation.sqlite")
        monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path / "app-data"))
        try:
            import_id = uuid.uuid4()
            template_id = "canary-template-a"
            async with maker() as session:
                session.add(
                    TemplateV2PptxImport(
                        id=import_id,
                        task_id=f"task-{uuid.uuid4().hex}",
                        requested_template_id=template_id,
                        draft_template_id=template_id,
                        state="confirmed",
                        owner_scope="scope-before-rotation",
                        source_filename="source.pptx",
                        source_media_type=(
                            "application/vnd.openxmlformats-officedocument."
                            "presentationml.presentation"
                        ),
                        source_size_bytes=4,
                        source_sha256="a" * 64,
                        source_storage_key=f"{import_id}/source.pptx",
                        manifest={"schema_version": 1},
                    )
                )
                session.add(
                    TemplateV2(
                        id=template_id,
                        presentation_id=uuid.uuid4(),
                        name="canary",
                        layouts={"layouts": []},
                    )
                )
                await session.commit()

            async with maker() as session:
                assert await imports_api._may_read_import_asset(
                    import_id, session, "scope-before-rotation"
                ), "the owner must keep access"
                assert await imports_api._may_read_import_asset(
                    import_id, session, "scope-after-rotation"
                ), "a rotated scope must not blank a confirmed deck"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_unconfirmed_assets_stay_owner_scoped(tmp_path, monkeypatch) -> None:
    """Before confirmation only the owner-scoped import record references the asset."""

    async def scenario() -> None:
        engine, maker = await _database(tmp_path / "unconfirmed.sqlite")
        monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path / "app-data"))
        try:
            import_id = uuid.uuid4()
            async with maker() as session:
                session.add(
                    TemplateV2PptxImport(
                        id=import_id,
                        task_id=f"task-{uuid.uuid4().hex}",
                        requested_template_id="canary-template-a",
                        state="review_required",
                        owner_scope="owner",
                        source_filename="source.pptx",
                        source_media_type=(
                            "application/vnd.openxmlformats-officedocument."
                            "presentationml.presentation"
                        ),
                        source_size_bytes=4,
                        source_sha256="a" * 64,
                        source_storage_key=f"{import_id}/source.pptx",
                        manifest={"schema_version": 1},
                    )
                )
                await session.commit()

            async with maker() as session:
                assert await imports_api._may_read_import_asset(import_id, session, "owner")
                assert not await imports_api._may_read_import_asset(
                    import_id, session, "someone-else"
                )
                assert not await imports_api._may_read_import_asset(
                    uuid.uuid4(), session, "owner"
                )
        finally:
            await engine.dispose()

    asyncio.run(scenario())
