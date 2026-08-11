import asyncio
from contextlib import contextmanager
import os
from io import BytesIO
from pathlib import Path
import uuid

import pytest
from fastapi import HTTPException, UploadFile

from api.v1.ppt.endpoints import files as files_endpoint
from api.v1.ppt.endpoints.presentation_crud import create_presentation
from api.v1.ppt.endpoints.presentation_generate import (
    check_if_api_request_is_valid,
)
from constants.documents import UPLOAD_ACCEPTED_FILE_TYPES
from models.generate_presentation_request import GeneratePresentationRequest
from services.documents_loader import DocumentsLoader
from services.temp_file_service import TEMP_FILE_SERVICE
from utils.document_path_security import (
    validate_document_path,
    validate_document_paths,
    validated_upload_extension,
)
from utils.owner_context import (
    reset_current_owner_id,
    reset_current_owner_is_admin,
    set_current_owner_id,
    set_current_owner_is_admin,
)


@contextmanager
def _owner(owner_id: uuid.UUID, *, is_admin: bool = False):
    owner_token = set_current_owner_id(owner_id)
    admin_token = set_current_owner_is_admin(is_admin)
    try:
        yield
    finally:
        reset_current_owner_is_admin(admin_token)
        reset_current_owner_id(owner_token)


def _assert_generic_path_error(exc: HTTPException, secret_path: str) -> None:
    assert exc.status_code == 400
    assert exc.detail == "Invalid document file path."
    assert secret_path not in str(exc.detail)


def test_validator_accepts_only_regular_files_below_allowed_root(tmp_path):
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir()
    source = allowed_root / "source.md"
    source.write_text("# source", encoding="utf-8")

    assert validate_document_path(
        str(source), allowed_roots=[str(allowed_root)]
    ) == os.path.realpath(source)

    with pytest.raises(HTTPException) as captured:
        validate_document_path(
            str(allowed_root), allowed_roots=[str(allowed_root)]
        )
    _assert_generic_path_error(captured.value, str(allowed_root))


@pytest.mark.parametrize(
    "malicious_path",
    [
        "../outside.md",
        "C:\\Windows\\win.ini",
        "D:\\outside\\secret.md",
        "\x00outside.md",
    ],
)
def test_validator_rejects_traversal_absolute_drive_and_nul_without_leakage(
    tmp_path, malicious_path
):
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir()

    with pytest.raises(HTTPException) as captured:
        validate_document_path(
            malicious_path, allowed_roots=[str(allowed_root)]
        )

    _assert_generic_path_error(captured.value, malicious_path)


def test_validator_rejects_outside_root_and_duplicate_real_paths(tmp_path):
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("secret", encoding="utf-8")

    with pytest.raises(HTTPException) as captured:
        validate_document_path(str(outside), allowed_roots=[str(allowed_root)])
    _assert_generic_path_error(captured.value, str(outside))

    inside = allowed_root / "inside.md"
    inside.write_text("# inside", encoding="utf-8")
    duplicate = allowed_root / "." / "inside.md"
    with pytest.raises(HTTPException) as captured:
        validate_document_paths(
            [str(inside), str(duplicate)],
            allowed_roots=[str(allowed_root)],
        )
    assert captured.value.status_code == 400
    assert captured.value.detail == "Duplicate document file path."


def test_validator_rejects_symlink_escape(tmp_path):
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("secret", encoding="utf-8")
    link = allowed_root / "link.md"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"Symlinks are unavailable: {exc}")

    with pytest.raises(HTTPException) as captured:
        validate_document_path(str(link), allowed_roots=[str(allowed_root)])
    _assert_generic_path_error(captured.value, str(link))


def test_default_document_roots_deny_another_owners_temp_tree(
    tmp_path, monkeypatch
):
    owner_a = uuid.uuid4()
    owner_b = uuid.uuid4()
    own_root = tmp_path / str(owner_a)
    foreign_root = tmp_path / str(owner_b)
    own_root.mkdir()
    foreign_root.mkdir()
    own_file = own_root / "own.md"
    foreign_file = foreign_root / "foreign.md"
    own_file.write_text("own", encoding="utf-8")
    foreign_file.write_text("foreign", encoding="utf-8")
    monkeypatch.setattr(TEMP_FILE_SERVICE, "base_dir", str(tmp_path))

    with _owner(owner_a):
        assert validate_document_path(str(own_file)) == os.path.realpath(own_file)
        with pytest.raises(HTTPException) as captured:
            validate_document_path(str(foreign_file))

    _assert_generic_path_error(captured.value, str(foreign_file))


def test_default_document_roots_accept_only_current_owners_workspace_assets(
    tmp_path, monkeypatch
):
    owner_a = uuid.uuid4()
    owner_b = uuid.uuid4()
    app_data = tmp_path / "app-data"
    own_root = app_data / "workspace_assets" / str(owner_a)
    foreign_root = app_data / "workspace_assets" / str(owner_b)
    own_root.mkdir(parents=True)
    foreign_root.mkdir(parents=True)
    own_file = own_root / "own.pdf"
    foreign_file = foreign_root / "foreign.pdf"
    own_file.write_bytes(b"own")
    foreign_file.write_bytes(b"foreign")
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))
    monkeypatch.setattr(TEMP_FILE_SERVICE, "base_dir", str(tmp_path / "temp"))

    with _owner(owner_a):
        assert validate_document_path(str(own_file)) == os.path.realpath(own_file)
        with pytest.raises(HTTPException) as captured:
            validate_document_path(str(foreign_file))

    _assert_generic_path_error(captured.value, str(foreign_file))


def test_global_legacy_document_root_requires_explicit_admin_migration(
    tmp_path, monkeypatch
):
    owner = uuid.uuid4()
    legacy_root = tmp_path / "audited-legacy"
    legacy_root.mkdir()
    legacy_file = legacy_root / "legacy.md"
    legacy_file.write_text("legacy", encoding="utf-8")
    monkeypatch.setattr(TEMP_FILE_SERVICE, "base_dir", str(tmp_path / "temp"))

    with _owner(owner):
        with pytest.raises(HTTPException) as denied:
            validate_document_path(
                str(legacy_file), admin_legacy_roots=[str(legacy_root)]
            )
        assert denied.value.status_code == 403
        assert denied.value.detail == "admin_migration_required"

    with _owner(owner, is_admin=True):
        assert validate_document_path(
            str(legacy_file), admin_legacy_roots=[str(legacy_root)]
        ) == os.path.realpath(legacy_file)


@pytest.mark.parametrize(
    "filename",
    [
        "../escape.md",
        "..\\escape.md",
        "/absolute/report.md",
        "C:\\absolute\\report.md",
        "D:relative-drive.md",
        "bad\x00name.md",
    ],
)
def test_upload_filename_rejects_path_syntax_and_nul(filename):
    with pytest.raises(HTTPException) as captured:
        validated_upload_extension(filename, UPLOAD_ACCEPTED_FILE_TYPES)

    assert captured.value.status_code == 400
    assert filename not in str(captured.value.detail)


def test_duplicate_original_upload_names_get_unique_server_paths(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(TEMP_FILE_SERVICE, "base_dir", str(tmp_path))
    uploads = [
        UploadFile(file=BytesIO(b"first"), filename="report.md"),
        UploadFile(file=BytesIO(b"second"), filename="report.md"),
    ]

    paths = asyncio.run(files_endpoint.upload_files(uploads))

    assert len(paths) == 2
    assert paths[0] != paths[1]
    assert os.path.basename(paths[0]) != "report.md"
    assert os.path.basename(paths[1]) != "report.md"
    assert paths[0].endswith(".md")
    assert paths[1].endswith(".md")
    assert Path(paths[0]).read_bytes() == b"first"
    assert Path(paths[1]).read_bytes() == b"second"


def test_documents_loader_rejects_outside_root_without_leaking_path(
    tmp_path, monkeypatch
):
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir()
    outside = tmp_path / "secret.md"
    outside.write_text("secret", encoding="utf-8")
    monkeypatch.setattr(TEMP_FILE_SERVICE, "base_dir", str(allowed_root))

    with pytest.raises(HTTPException) as captured:
        asyncio.run(DocumentsLoader([str(outside)]).load_documents())

    _assert_generic_path_error(captured.value, str(outside))


def test_create_and_generate_entrypoints_reject_outside_paths(
    tmp_path, monkeypatch
):
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir()
    outside = tmp_path / "secret.md"
    outside.write_text("secret", encoding="utf-8")
    monkeypatch.setattr(TEMP_FILE_SERVICE, "base_dir", str(allowed_root))

    with pytest.raises(HTTPException) as create_error:
        asyncio.run(
            create_presentation(
                content="deck",
                file_paths=[str(outside)],
                sql_session=None,
            )
        )
    _assert_generic_path_error(create_error.value, str(outside))

    request = GeneratePresentationRequest(content="", files=[str(outside)])
    with pytest.raises(HTTPException) as generate_error:
        asyncio.run(check_if_api_request_is_valid(request, sql_session=None))
    _assert_generic_path_error(generate_error.value, str(outside))
