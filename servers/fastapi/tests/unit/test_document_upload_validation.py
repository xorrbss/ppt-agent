from io import BytesIO

import pytest
from fastapi import UploadFile
from starlette.datastructures import Headers

from constants.documents import UPLOAD_ACCEPTED_FILE_TYPES
from utils.validators import _is_accepted_file_type


def _upload(filename: str, content_type: str | None) -> UploadFile:
    headers = Headers({"content-type": content_type}) if content_type else Headers()
    return UploadFile(
        filename=filename,
        file=BytesIO(b"content"),
        headers=headers,
    )


@pytest.mark.parametrize(
    ("filename", "content_type"),
    [
        ("notes.txt", "text/plain"),
        ("notes.md", "text/markdown"),
        ("notes.md", None),
        ("notes.markdown", "application/octet-stream"),
        ("notes.MD", "application/octet-stream"),
    ],
)
def test_text_upload_accepts_supported_extensions_regardless_of_generic_mime(
    filename, content_type
):
    assert _is_accepted_file_type(
        _upload(filename, content_type),
        UPLOAD_ACCEPTED_FILE_TYPES,
    )


@pytest.mark.parametrize(
    ("filename", "content_type"),
    [
        ("payload.exe", "text/plain"),
        ("payload.unknown", "text/markdown"),
        ("payload.text", "text/plain"),
        ("payload.exe", "text/custom"),
        ("payload", "application/octet-stream"),
    ],
)
def test_text_upload_rejects_unknown_extensions_and_arbitrary_mime(
    filename, content_type
):
    assert not _is_accepted_file_type(
        _upload(filename, content_type),
        UPLOAD_ACCEPTED_FILE_TYPES,
    )


def test_extensionless_file_can_use_an_exactly_allowed_mime():
    assert _is_accepted_file_type(
        _upload("README", "text/plain"),
        UPLOAD_ACCEPTED_FILE_TYPES,
    )
