"""Tests for services.liteparse_pdf_input.pdf_input_for_liteparse.

Covers the single-point fix: extensions @llamaindex/liteparse's own
convertToPdf() would spawn soffice for must be pre-converted through
DocumentConversionService (isolated LibreOffice profile) so LiteParse
never spawns soffice itself; everything else must pass through untouched.
"""

from pathlib import Path

from services.document_conversion_service import DocumentConversionService
from services.liteparse_pdf_input import (
    LITEPARSE_SOFFICE_EXTENSIONS,
    pdf_input_for_liteparse,
)


def _fake_convert_office_to_pdf(monkeypatch, calls: list):
    def fake(self, file_path, output_dir, timeout_seconds=180):
        calls.append((file_path, output_dir, timeout_seconds))
        out = Path(output_dir) / f"{Path(file_path).stem}.pdf"
        out.write_bytes(b"%PDF-1.4")
        return str(out)

    monkeypatch.setattr(DocumentConversionService, "convert_office_to_pdf", fake)


def test_pdf_input_is_a_no_op_for_pdf(tmp_path):
    service = DocumentConversionService()
    pdf_path = tmp_path / "already.pdf"
    pdf_path.write_bytes(b"%PDF-1.4")

    with pdf_input_for_liteparse(
        str(pdf_path), document_conversion_service=service, timeout_seconds=30
    ) as resolved:
        assert resolved == str(pdf_path)


def test_office_extension_is_converted_to_pdf_and_cleaned_up(tmp_path, monkeypatch):
    calls: list = []
    _fake_convert_office_to_pdf(monkeypatch, calls)
    service = DocumentConversionService()

    pptx_path = tmp_path / "deck.pptx"
    pptx_path.write_bytes(b"fake-pptx")

    with pdf_input_for_liteparse(
        str(pptx_path), document_conversion_service=service, timeout_seconds=30
    ) as resolved:
        assert resolved.endswith(".pdf")
        assert resolved != str(pptx_path)
        assert Path(resolved).is_file()
        conversion_dir = Path(resolved).parent

    assert len(calls) == 1
    assert calls[0][0] == str(pptx_path)
    # The temp conversion directory is removed once the context exits.
    assert not conversion_dir.exists()


def test_every_liteparse_soffice_extension_routes_through_conversion(
    tmp_path, monkeypatch
):
    calls: list = []
    _fake_convert_office_to_pdf(monkeypatch, calls)
    service = DocumentConversionService()

    for ext in sorted(LITEPARSE_SOFFICE_EXTENSIONS):
        source = tmp_path / f"input{ext}"
        source.write_bytes(b"data")
        with pdf_input_for_liteparse(
            str(source), document_conversion_service=service, timeout_seconds=30
        ) as resolved:
            assert resolved.endswith(".pdf")

    assert len(calls) == len(LITEPARSE_SOFFICE_EXTENSIONS)


def test_non_soffice_extension_is_left_untouched(tmp_path, monkeypatch):
    calls: list = []
    _fake_convert_office_to_pdf(monkeypatch, calls)
    service = DocumentConversionService()

    image_path = tmp_path / "photo.png"
    image_path.write_bytes(b"fake-png")

    with pdf_input_for_liteparse(
        str(image_path), document_conversion_service=service, timeout_seconds=30
    ) as resolved:
        assert resolved == str(image_path)

    assert calls == []
