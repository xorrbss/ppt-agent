import asyncio
import os
from io import BytesIO
from pathlib import Path

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
