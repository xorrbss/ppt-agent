from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import io
from pathlib import Path
import uuid
import zipfile

from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import HTTPException, UploadFile
from sqlalchemy import Column, MetaData, String, Table, create_engine, inspect
from starlette.requests import Request
from starlette.datastructures import Headers

from api.v1.ppt.endpoints import structured_template_imports
from api.v1.ppt.endpoints.structured_template_imports import (
    create_structured_template_import,
)
from models.sql.template_v2_pptx_import import TemplateV2PptxImport
from services import template_v2_pptx_ingestion_service as ingestion
from services.template_v2_pptx_storage import (
    PPTX_MEDIA_TYPE,
    PptxUploadRejected,
    private_import_root,
    resolve_private_source,
    store_private_pptx,
    verify_private_source,
)
from templates.v2.generation import build_generated_slide
from templates.v2.pptx.assembler import assemble_template_v2_draft
from templates.v2.pptx.ooxml_parser import parse_presentation_candidates
from templates.v2.pptx.package_reader import PptxPackageReader, UnsafePptxPackage


PRESENTATION_XML = b"""\
<p:presentation
 xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
 <p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst>
 <p:sldSz cx="12192000" cy="6858000"/>
</p:presentation>"""
PRESENTATION_RELS = b"""\
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rId1"
  Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide"
  Target="slides/slide1.xml"/>
</Relationships>"""
SLIDE_XML = b"""\
<p:sld
 xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
 xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
 <p:cSld><p:spTree>
  <p:nvGrpSpPr/><p:grpSpPr/>
  <p:sp>
   <p:nvSpPr><p:cNvPr id="2" name="Title 1"/></p:nvSpPr>
   <p:spPr><a:xfrm><a:off x="1219200" y="685800"/>
    <a:ext cx="6096000" cy="914400"/></a:xfrm>
    <a:prstGeom prst="rect"/>
   </p:spPr>
   <p:txBody><a:p><a:r><a:t>Hello import</a:t></a:r></a:p></p:txBody>
  </p:sp>
  <p:graphicFrame/>
 </p:spTree></p:cSld>
</p:sld>"""


def _pptx_bytes(
    *,
    extra: dict[str, bytes] | None = None,
    slide_xml: bytes = SLIDE_XML,
) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", b"<Types/>")
        archive.writestr("ppt/presentation.xml", PRESENTATION_XML)
        archive.writestr(
            "ppt/_rels/presentation.xml.rels",
            PRESENTATION_RELS,
        )
        archive.writestr("ppt/slides/slide1.xml", slide_xml)
        for name, payload in (extra or {}).items():
            archive.writestr(name, payload)
    return stream.getvalue()


def _upload(payload: bytes, *, filename: str = "source.pptx") -> UploadFile:
    return UploadFile(
        file=io.BytesIO(payload),
        filename=filename,
        headers=Headers({"content-type": PPTX_MEDIA_TYPE}),
    )


def _request(username: str = "local-user") -> Request:
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
    request.state.auth_username = username
    return request


def test_ooxml_candidates_are_deterministic_and_manifest_review_is_explicit(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.pptx"
    payload = _pptx_bytes()
    source.write_bytes(payload)

    first = parse_presentation_candidates(
        PptxPackageReader(source),
        source_sha256=hashlib.sha256(payload).hexdigest(),
    )
    second = parse_presentation_candidates(
        PptxPackageReader(source),
        source_sha256=hashlib.sha256(payload).hexdigest(),
    )
    assert first == second
    assert [shape.kind for shape in first.slides[0].shapes] == [
        "text",
        "unsupported",
    ]
    draft = assemble_template_v2_draft(first)
    assert draft.raw_layouts.model_validate(
        draft.raw_layouts.model_dump(mode="json")
    )
    assert draft.layouts.model_validate(draft.layouts.model_dump(mode="json"))
    assert draft.contents[0]
    generated = build_generated_slide(draft.layouts.layouts[0], draft.contents[0])
    assert generated.layout_id == draft.layouts.layouts[0].id
    assert generated.content == draft.contents[0]
    assert generated.ui["components"]
    assert draft.manifest["parser"]["network_access"] is False
    assert draft.manifest["vision"] == {
        "available": False,
        "reason": "vision_provider_not_configured",
        "network_access": False,
    }
    assert draft.manifest["review"]["required"] is True
    assert draft.manifest["slides"][0]["unsupported"][0]["reason"] == (
        "unsupported_ooxml:graphicFrame"
    )
    assert draft.manifest["slides"][0]["fallback"]["kind"] == "manual_review"


def test_worker_analysis_binds_source_and_separates_inventory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app_data = tmp_path / "app-data"
    app_data.mkdir()
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))
    payload = _pptx_bytes()
    import_id = uuid.uuid4()
    stored = asyncio.run(
        store_private_pptx(
            _upload(payload),
            import_id=import_id,
        )
    )
    observations: list[dict] = []
    monkeypatch.setattr(
        ingestion,
        "log_pptx_analysis_observation",
        lambda **event: observations.append(event),
    )

    analysis, suggestions, inventory = ingestion._analyze_import_source(
        stored.storage_key,
        stored.sha256,
        import_id=import_id,
        source_filename=stored.display_filename,
        source_media_type=stored.media_type,
        source_size_bytes=stored.size_bytes,
    )

    assert analysis["provider"]["id"] == "deterministic-ooxml-static"
    assert analysis["provider"]["external_ai"] is False
    assert analysis["preview"]["status"] == "not_provided"
    assert analysis["render"]["status"] == "not_run"
    assert isinstance(suggestions, list)
    assert inventory["source"] == stored.secret_free_metadata().to_manifest()
    assert inventory["artifacts"]
    assert inventory["candidates"] == [
        {
            "identifier": "deterministic-ooxml-static-analysis-v1",
            "media_type": "application/json",
            "sha256": inventory["candidates"][0]["sha256"],
            "size_bytes": inventory["candidates"][0]["size_bytes"],
        }
    ]
    assert observations == [
        {
            "provider": "deterministic-ooxml-static",
            "status": "completed",
            "duration_ms": observations[0]["duration_ms"],
            "count": 1,
        }
    ]


def test_package_preflight_rejects_traversal_and_windows_paths(
    tmp_path: Path,
) -> None:
    for index, unsafe_name in enumerate(
        ("../escape.xml", r"ppt\..\escape.xml", "C:/escape.xml")
    ):
        source = tmp_path / f"unsafe-{index}.pptx"
        source.write_bytes(_pptx_bytes(extra={unsafe_name: b"x"}))
        try:
            PptxPackageReader(source).preflight()
        except UnsafePptxPackage as error:
            assert error.code == "unsafe_zip_member_path"
        else:
            raise AssertionError(f"unsafe member accepted: {unsafe_name}")


def test_package_preflight_rejects_casefold_duplicates_and_doctype(
    tmp_path: Path,
) -> None:
    duplicate = tmp_path / "duplicate.pptx"
    duplicate.write_bytes(
        _pptx_bytes(extra={"PPT/PRESENTATION.XML": b"<duplicate/>"})
    )
    try:
        PptxPackageReader(duplicate).preflight()
    except UnsafePptxPackage as error:
        assert error.code == "duplicate_zip_member"
    else:
        raise AssertionError("case-insensitive duplicate was accepted")

    unsafe_xml = tmp_path / "doctype.pptx"
    unsafe_xml.write_bytes(
        _pptx_bytes(
            slide_xml=(
                b'<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
                b"<foo/>"
            )
        )
    )
    reader = PptxPackageReader(unsafe_xml)
    reader.preflight()
    try:
        reader.read_xml("ppt/slides/slide1.xml")
    except UnsafePptxPackage as error:
        assert error.code == "unsafe_xml_declaration"
    else:
        raise AssertionError("DOCTYPE was accepted")


def test_private_streaming_storage_hash_limit_and_path_safety(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app_data = tmp_path / "app-data"
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))
    payload = _pptx_bytes()

    stored = asyncio.run(
        store_private_pptx(
            _upload(payload, filename=r"C:\fakepath\Quarterly.pptx"),
            import_id=uuid.uuid4(),
        )
    )
    assert stored.display_filename == "Quarterly.pptx"
    assert stored.sha256 == hashlib.sha256(payload).hexdigest()
    source = verify_private_source(stored.storage_key, stored.sha256)
    assert source.is_relative_to(private_import_root())
    assert not source.is_relative_to(app_data)

    source.write_bytes(source.read_bytes() + b"tamper")
    try:
        verify_private_source(stored.storage_key, stored.sha256)
    except PptxUploadRejected as error:
        assert error.code == "private_source_integrity_mismatch"
    else:
        raise AssertionError("source hash mismatch was accepted")

    for key in ("../source.pptx", r"x\source.pptx", "C:/source.pptx"):
        try:
            resolve_private_source(key)
        except PptxUploadRejected as error:
            assert error.code == "invalid_private_storage_key"
        else:
            raise AssertionError(f"unsafe private key accepted: {key}")

    try:
        asyncio.run(
            store_private_pptx(
                _upload(payload),
                import_id=uuid.uuid4(),
                max_bytes=len(payload) - 1,
            )
        )
    except PptxUploadRejected as error:
        assert error.code == "pptx_upload_size_limit_exceeded"
    else:
        raise AssertionError("streaming byte limit was not enforced")


def test_import_api_queues_for_the_durable_dispatcher(
    tmp_path: Path,
    monkeypatch,
    fake_async_session,
) -> None:
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path / "app-data"))
    monkeypatch.setenv("DISABLE_AUTH", "true")
    monkeypatch.setenv("ENABLE_TEMPLATE_V2", "true")
    monkeypatch.setenv("TEMPLATE_V2_TEMPLATE_ALLOWLIST", "safe-template")
    notifications: list[str] = []
    monkeypatch.setattr(
        structured_template_imports,
        "notify_template_v2_pptx_dispatcher",
        lambda: notifications.append("queued"),
    )
    class EmptyResult:
        def scalar_one_or_none(self):
            return None

    async def execute_empty(*_args, **_kwargs):
        return EmptyResult()

    monkeypatch.setattr(fake_async_session, "execute", execute_empty)

    response = asyncio.run(
        create_structured_template_import(
            request=_request(),
            template_id="safe-template",
            pptx_file=_upload(_pptx_bytes()),
            idempotency_key="request-key-0001",
            sql_session=fake_async_session,
        )
    )

    assert response.state == "queued"
    assert response.requested_template_id == "safe-template"
    assert response.source_sha256
    assert response.source_inventory["source"]["sha256"] == response.source_sha256
    assert response.source_inventory["artifacts"] == []
    assert response.source_inventory["candidates"] == []
    assert fake_async_session.commit_count == 1
    jobs = [
        obj
        for obj in fake_async_session.added
        if isinstance(obj, TemplateV2PptxImport)
    ]
    assert jobs and "/" in jobs[0].source_storage_key
    assert notifications == ["queued"]
    assert "source_storage_key" not in response.model_dump()


def test_import_api_respects_default_off_policy(
    monkeypatch,
    fake_async_session,
) -> None:
    monkeypatch.delenv("ENABLE_TEMPLATE_V2", raising=False)
    monkeypatch.delenv("TEMPLATE_V2_TEMPLATE_ALLOWLIST", raising=False)
    try:
        asyncio.run(
            create_structured_template_import(
                request=_request(),
                template_id="blocked",
                pptx_file=_upload(_pptx_bytes()),
                idempotency_key="request-key-0002",
                sql_session=fake_async_session,
            )
        )
    except HTTPException as error:
        assert error.status_code == 403
        assert error.detail == "template_v2_creation_disabled"
    else:
        raise AssertionError("default-off import policy was bypassed")


def test_import_migration_upgrade_and_empty_downgrade(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'migration.sqlite'}")
    metadata = MetaData()
    Table(
        "async_presentation_generation_tasks",
        metadata,
        Column("id", String, primary_key=True),
    )
    Table("template_v2", metadata, Column("id", String, primary_key=True))
    metadata.create_all(engine)
    migration_path = (
        Path(__file__).parents[2]
        / "alembic"
        / "versions"
        / "b5c6d7e8f9a0_add_template_v2_pptx_imports.py"
    )
    spec = importlib.util.spec_from_file_location("pptx_import_migration", migration_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    lease_migration_path = (
        Path(__file__).parents[2]
        / "alembic"
        / "versions"
        / "d7e8f9a0b1c2_add_template_v2_import_attempt_lease.py"
    )
    lease_spec = importlib.util.spec_from_file_location(
        "pptx_import_lease_migration",
        lease_migration_path,
    )
    assert lease_spec is not None and lease_spec.loader is not None
    lease_migration = importlib.util.module_from_spec(lease_spec)
    lease_spec.loader.exec_module(lease_migration)
    retention_migration_path = (
        Path(__file__).parents[2]
        / "alembic"
        / "versions"
        / "e8f9a0b1c2d3_add_template_v2_source_retention.py"
    )
    retention_spec = importlib.util.spec_from_file_location(
        "pptx_import_retention_migration",
        retention_migration_path,
    )
    assert retention_spec is not None and retention_spec.loader is not None
    retention_migration = importlib.util.module_from_spec(retention_spec)
    retention_spec.loader.exec_module(retention_migration)
    review_migration_path = (
        Path(__file__).parents[2]
        / "alembic"
        / "versions"
        / "1b2c3d4e5f6a_add_template_v2_import_review_boundary.py"
    )
    review_spec = importlib.util.spec_from_file_location(
        "pptx_import_review_migration",
        review_migration_path,
    )
    assert review_spec is not None and review_spec.loader is not None
    review_migration = importlib.util.module_from_spec(review_spec)
    review_spec.loader.exec_module(review_migration)

    with engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        lease_migration.op = Operations(MigrationContext.configure(connection))
        lease_migration.upgrade()
        retention_migration.op = Operations(MigrationContext.configure(connection))
        retention_migration.upgrade()
        review_migration.op = Operations(MigrationContext.configure(connection))
        review_migration.upgrade()
        inspector = inspect(connection)
        assert "template_v2_pptx_imports" in inspector.get_table_names()
        columns = {
            column["name"]
            for column in inspector.get_columns("template_v2_pptx_imports")
        }
        assert {
            "task_id",
            "source_sha256",
            "source_storage_key",
            "manifest",
            "draft_template_id",
            "attempt_number",
            "attempt_token",
            "lease_expires_at",
            "heartbeat_at",
            "last_started_at",
            "source_retention_expires_at",
            "source_cleanup_token",
            "source_cleanup_lease_expires_at",
            "source_cleanup_attempted_at",
            "source_deleted_at",
            "owner_scope",
            "request_key_hash",
            "request_fingerprint",
            "revision",
            "analysis_result",
            "repeat_suggestions",
            "confirmed_at",
            "cancelled_at",
        }.issubset(columns)
        indexes = {
            index["name"]
            for index in inspector.get_indexes("template_v2_pptx_imports")
        }
        assert "ix_template_v2_pptx_imports_requested_template_id" in indexes
        assert "ix_template_v2_pptx_imports_dispatch" in indexes
        assert "ix_template_v2_pptx_imports_source_cleanup" in indexes
        assert "uq_template_v2_pptx_imports_owner_request_key" in indexes
        review_migration.downgrade()
        retention_migration.downgrade()
        lease_migration.downgrade()
        migration.downgrade()
        assert "template_v2_pptx_imports" not in inspect(connection).get_table_names()
