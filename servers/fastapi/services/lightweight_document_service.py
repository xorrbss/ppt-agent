"""
Dependency-light native extraction for office documents.

The primary office path is LibreOffice -> PDF -> LiteParse
(``services/document_conversion_service.py``). That requires the ``soffice``
binary, which is absent on plain Windows installs of this fork. This service
extracts text directly for the modern (zip-based) formats we can handle without
any external binary, so office uploads still work when LibreOffice is missing.

``documents_loader`` wires this in as ``document_service`` (the optional fallback
converter). Formats it cannot handle natively (legacy OLE ``.doc``/``.ppt``/
``.xls``, OpenDocument, spreadsheets) raise :class:`DocumentConversionError` with
a clear message so the caller can surface an actionable error instead of a raw
500.
"""

import html
import re
import zipfile
from pathlib import Path

from services.document_conversion_service import DocumentConversionError

# python-pptx officially supports these; other extensions (.ppsx, legacy .ppt)
# are left to LibreOffice.
PPTX_EXTENSIONS = {".pptx", ".pptm"}
DOCX_EXTENSIONS = {".docx", ".docm"}
TEXT_EXTENSIONS = {".txt", ".csv", ".tsv"}


class DocumentService:
    def parse_to_markdown(self, file_path: str) -> str:
        ext = Path(file_path).suffix.lower()
        if ext in PPTX_EXTENSIONS:
            return _parse_pptx(file_path)
        if ext in DOCX_EXTENSIONS:
            return _parse_docx(file_path)
        if ext in TEXT_EXTENSIONS:
            return _read_text(file_path)
        raise DocumentConversionError(
            f"'{Path(file_path).name}' 형식은 LibreOffice 없이는 변환할 수 없습니다. "
            "PDF로 변환해 업로드하거나 LibreOffice를 설치해 주세요."
        )


def _parse_pptx(file_path: str) -> str:
    try:
        from pptx import Presentation

        prs = Presentation(file_path)
        blocks: list[str] = []
        for index, slide in enumerate(prs.slides, start=1):
            lines: list[str] = []
            for shape in slide.shapes:
                lines.extend(_shape_text_lines(shape))
            if slide.has_notes_slide:
                notes = slide.notes_slide.notes_text_frame
                if notes is not None and notes.text.strip():
                    lines.append(f"> {notes.text.strip()}")
            if lines:
                blocks.append(f"## Slide {index}\n" + "\n".join(lines))
        return "\n\n".join(blocks).strip()
    except DocumentConversionError:
        raise
    except Exception as exc:
        raise DocumentConversionError(
            f"'{Path(file_path).name}' PowerPoint 파일을 읽을 수 없습니다: {exc}"
        ) from exc


def _shape_text_lines(shape) -> list[str]:
    out: list[str] = []
    # Grouped shapes hold nested shapes (common in real decks) — recurse.
    if hasattr(shape, "shapes"):
        for child in shape.shapes:
            out.extend(_shape_text_lines(child))
        return out
    if getattr(shape, "has_text_frame", False):
        for para in shape.text_frame.paragraphs:
            text = "".join(run.text for run in para.runs).strip()
            if text:
                out.append(text)
    if getattr(shape, "has_table", False):
        for row in shape.table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            if any(cells):
                out.append("| " + " | ".join(cells) + " |")
    return out


def _parse_docx(file_path: str) -> str:
    try:
        with zipfile.ZipFile(file_path) as zf:
            xml = zf.read("word/document.xml").decode("utf-8", errors="replace")
    except KeyError as exc:
        raise DocumentConversionError(
            f"'{Path(file_path).name}'에서 본문(word/document.xml)을 찾을 수 없습니다."
        ) from exc
    except Exception as exc:
        raise DocumentConversionError(
            f"'{Path(file_path).name}' Word 파일을 읽을 수 없습니다: {exc}"
        ) from exc

    # Turn structural markers into whitespace, then strip the remaining tags so
    # only the text nodes (the <w:t> contents) plus our breaks are left.
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"<w:br\b[^>]*/?>", "\n", xml)
    xml = re.sub(r"<w:tab\b[^>]*/?>", "\t", xml)
    text = re.sub(r"<[^>]+>", "", xml)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _read_text(file_path: str) -> str:
    with open(file_path, "r", encoding="utf-8", errors="replace") as file:
        return file.read()
