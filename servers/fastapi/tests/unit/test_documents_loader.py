import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from services.documents_loader import (
    DocumentsLoader,
    _unwrap_liteparse_json_line_if_stored,
    clean_extracted_document_text,
)


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
