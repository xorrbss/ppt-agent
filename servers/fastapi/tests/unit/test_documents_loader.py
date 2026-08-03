import asyncio
import codecs
import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from services.document_conversion_service import DocumentConversionError
from services.documents_loader import (
    DocumentsLoader,
    _decode_text_bytes,
    _unwrap_liteparse_json_line_if_stored,
    clean_extracted_document_text,
)
from services.temp_file_service import TEMP_FILE_SERVICE


@pytest.fixture(autouse=True)
def _allow_test_temp_root(tmp_path, monkeypatch):
    monkeypatch.setattr(TEMP_FILE_SERVICE, "base_dir", str(tmp_path))


def test_unwrap_liteparse_json_line_extracts_text_field():
    inner_text = "Title\n\nBody with \"quotes\""
    payload = json.dumps({"ok": True, "filePath": "/tmp/test.pdf", "text": inner_text})

    assert _unwrap_liteparse_json_line_if_stored(payload) == inner_text
    assert _unwrap_liteparse_json_line_if_stored(f"  {payload}") == inner_text


def test_unwrap_liteparse_json_line_leaves_non_json_text():
    plain_text = "Not JSON, should stay as-is."
    assert _unwrap_liteparse_json_line_if_stored(plain_text) == plain_text


def test_clean_extracted_document_text_handles_malformed_json_body():
    malformed = (
        '{"ok": true, "filePath": "/tmp/test.pdf", "text": '
        '"hello\\nworld\\u0021 and trailing'
    )
    cleaned = clean_extracted_document_text(malformed)
    assert cleaned == "hello\nworld! and trailing"


def test_clean_extracted_document_text_unwraps_nested_liteparse_payloads():
    nested = json.dumps(
        {
            "ok": True,
            "filePath": "/tmp/outer.pdf",
            "text": json.dumps(
                {"ok": True, "filePath": "/tmp/inner.pdf", "text": "final body"}
            ),
        }
    )
    assert clean_extracted_document_text(nested) == "final body"


@pytest.mark.parametrize(
    ("extension", "encoded"),
    [
        (".txt", "제목\r\n본문\r끝".encode("utf-8")),
        (".md", codecs.BOM_UTF8 + "# 제목\r\n\r\n- 항목".encode("utf-8")),
        (".markdown", codecs.BOM_UTF16_LE + "# 제목\r\n본문".encode("utf-16-le")),
        (".txt", codecs.BOM_UTF16_BE + "제목\r본문".encode("utf-16-be")),
        (".md", "# 제목\r\n한글 본문".encode("cp949")),
    ],
)
def test_load_documents_decodes_supported_text_encodings_and_normalizes_newlines(
    tmp_path, extension, encoded
):
    source = tmp_path / f"source{extension}"
    source.write_bytes(encoded)
    loader = DocumentsLoader(file_paths=[str(source)])

    asyncio.run(loader.load_documents())

    assert "\r" not in loader.documents[0]
    assert "제목" in loader.documents[0]
    assert not loader.documents[0].startswith("\ufeff")


def test_load_documents_preserves_markdown_structure(tmp_path):
    markdown = (
        "# 제목\r\n\r\n- 첫 번째\r\n- 두 번째\r\n\r\n"
        "줄바꿈<br>표기\r\n\r\n```python\r\nprint('ok')\r\n```"
    )
    source = tmp_path / "source.md"
    source.write_bytes(markdown.encode("utf-8"))
    loader = DocumentsLoader(file_paths=[str(source)])

    asyncio.run(loader.load_documents())

    assert loader.documents == [
        "# 제목\n\n- 첫 번째\n- 두 번째\n\n"
        "줄바꿈<br>표기\n\n```python\nprint('ok')\n```"
    ]


def test_load_documents_does_not_unwrap_json_shaped_plain_text(tmp_path):
    text = json.dumps(
        {"ok": True, "filePath": "source.md", "text": "must not replace original"}
    )
    source = tmp_path / "source.md"
    source.write_text(text, encoding="utf-8")
    loader = DocumentsLoader(file_paths=[str(source)])

    asyncio.run(loader.load_documents())

    assert loader.documents == [text]


def test_decode_text_bytes_reports_actionable_error():
    with pytest.raises(HTTPException) as exc:
        _decode_text_bytes(b"\x81\x00", "broken.md")

    assert exc.value.status_code == 422
    assert "broken.md" in exc.value.detail
    assert "UTF-8" in exc.value.detail


def test_load_pdf_requires_temp_dir_when_images_are_requested():
    loader = DocumentsLoader(file_paths=[])

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            loader.load_pdf(
                file_path="/tmp/fake.pdf",
                load_text=False,
                load_images=True,
                temp_dir=None,
            )
        )

    assert exc.value.status_code == 400
    assert "temp_dir is required" in exc.value.detail


def test_load_office_falls_back_to_native_extraction_when_libreoffice_rejects_pptx(
    tmp_path, monkeypatch
):
    source = tmp_path / "source.pptx"
    source.write_bytes(b"pptx")
    loader = DocumentsLoader(file_paths=[str(source)])
    loader.document_service = MagicMock()
    loader.document_service.parse_to_markdown.return_value = "native slide text"
    monkeypatch.setattr(
        loader.document_conversion_service,
        "is_soffice_available",
        lambda: True,
    )
    monkeypatch.setattr(
        loader.document_conversion_service,
        "convert_office_to_pdf",
        MagicMock(
            side_effect=DocumentConversionError(
                "LibreOffice source file could not be loaded"
            )
        ),
    )

    result = loader.load_office_document(str(source), str(tmp_path))

    assert result == "native slide text"
    loader.document_service.parse_to_markdown.assert_called_once_with(str(source))


def test_load_office_keeps_conversion_error_when_native_fallback_also_fails(
    tmp_path, monkeypatch
):
    source = tmp_path / "source.pptx"
    source.write_bytes(b"invalid")
    loader = DocumentsLoader(file_paths=[str(source)])
    conversion_error = DocumentConversionError(
        "LibreOffice source file could not be loaded"
    )
    loader.document_service = MagicMock()
    loader.document_service.parse_to_markdown.side_effect = DocumentConversionError(
        "Native parser could not read the file"
    )
    monkeypatch.setattr(
        loader.document_conversion_service,
        "is_soffice_available",
        lambda: True,
    )
    monkeypatch.setattr(
        loader.document_conversion_service,
        "convert_office_to_pdf",
        MagicMock(side_effect=conversion_error),
    )

    with pytest.raises(DocumentConversionError) as exc:
        loader.load_office_document(str(source), str(tmp_path))

    assert exc.value is conversion_error


@pytest.mark.parametrize(
    ("suffix", "encoding"),
    [
        (".md", "utf-8"),
        (".markdown", "utf-16"),
        (".txt", "cp949"),
    ],
)
def test_load_text_documents_supports_markdown_and_korean_encodings(
    tmp_path, suffix, encoding
):
    source = "# 발표 개요\r\n\r\n- 첫 번째 슬라이드"
    file_path = tmp_path / f"brief{suffix}"
    file_path.write_bytes(source.encode(encoding))

    loader = DocumentsLoader(file_paths=[str(file_path)])
    asyncio.run(loader.load_documents())

    assert loader.documents == ["# 발표 개요\n\n- 첫 번째 슬라이드"]


def test_load_text_document_reports_unsupported_encoding(tmp_path):
    file_path = tmp_path / "brief.md"
    file_path.write_bytes(b"\x81")

    loader = DocumentsLoader(file_paths=[str(file_path)])
    with pytest.raises(HTTPException) as exc:
        asyncio.run(loader.load_documents())

    assert exc.value.status_code == 422
    assert "brief.md" in exc.value.detail


def _make_mock_page(text: str) -> MagicMock:
    page = MagicMock()
    page.extract_text.return_value = text
    return page


@patch("services.documents_loader.pdfplumber.open")
def test_is_scanned_pdf_returns_true_for_empty_pages(mock_open):
    mock_pdf = MagicMock()
    mock_pdf.pages = [_make_mock_page(""), _make_mock_page("")]
    mock_open.return_value.__enter__ = MagicMock(return_value=mock_pdf)
    mock_open.return_value.__exit__ = MagicMock(return_value=False)

    assert DocumentsLoader._is_scanned_pdf("/tmp/scanned.pdf") is True


@patch("services.documents_loader.pdfplumber.open")
def test_is_scanned_pdf_returns_false_for_text_pages(mock_open):
    mock_pdf = MagicMock()
    mock_pdf.pages = [
        _make_mock_page("Chapter 1: Introduction to calculus"),
        _make_mock_page("This chapter covers derivatives and integrals"),
    ]
    mock_open.return_value.__enter__ = MagicMock(return_value=mock_pdf)
    mock_open.return_value.__exit__ = MagicMock(return_value=False)

    assert DocumentsLoader._is_scanned_pdf("/tmp/text.pdf") is False


@patch("services.documents_loader.pdfplumber.open")
def test_is_scanned_pdf_threshold_edge_case(mock_open):
    mock_pdf = MagicMock()
    mock_pdf.pages = [_make_mock_page("x" * 49)]
    mock_open.return_value.__enter__ = MagicMock(return_value=mock_pdf)
    mock_open.return_value.__exit__ = MagicMock(return_value=False)

    assert DocumentsLoader._is_scanned_pdf("/tmp/edge.pdf", threshold=50) is True


@patch("services.documents_loader.pdfplumber.open")
def test_is_scanned_pdf_handles_exception_gracefully(mock_open):
    mock_open.side_effect = Exception("corrupt file")

    assert DocumentsLoader._is_scanned_pdf("/tmp/corrupt.pdf") is False
