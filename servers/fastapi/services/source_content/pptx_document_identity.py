"""Document-level title signals for a PPTX source, independent of slide content.

``pptx.py`` extracts the slide/notes object tree; this module resolves the
two signals ``deck_title()`` (``services/composer/pipeline_inputs.py``) falls
back to when the source's cover page carries no title-role text block:

- the document title recorded in the OPC core-properties part
  (``docProps/core.xml`` ``dc:title``), and
- a title candidate cleaned from the uploaded file's own name.

Both are deterministic and network-free.
"""

from __future__ import annotations

from pathlib import PurePosixPath
import re
from zipfile import ZipFile

from services.source_content.security import PackageSecurityReport, secure_xml

CORE_PROPERTIES_PART = "docProps/core.xml"
_DC_NS = "http://purl.org/dc/elements/1.1/"
_DC_TITLE_TAG = f"{{{_DC_NS}}}title"


def extract_document_title(package: ZipFile, report: PackageSecurityReport) -> str | None:
    """``dc:title`` from ``docProps/core.xml``, or ``None`` when absent/blank.

    Real-world decks frequently carry this part with an empty ``<dc:title/>``
    (PowerPoint writes the element regardless of whether the author filled
    it in), so whitespace-only content is treated the same as a missing
    element rather than letting a blank string beat a real cover-page title.
    Membership in ``report.entries`` reuses the same security-admitted part
    list ``_ordered_slide_parts`` checks, rather than trusting the raw zip.
    """

    if CORE_PROPERTIES_PART not in report.entries:
        return None
    root = secure_xml(package.read(CORE_PROPERTIES_PART), part_name=CORE_PROPERTIES_PART)
    title_element = root.find(_DC_TITLE_TAG)
    if title_element is None or not title_element.text:
        return None
    collapsed = " ".join(title_element.text.split())
    return collapsed or None


#: Repeated trailing "(2)", "copy", "Copy 2", or the Korean equivalents a
#: file manager appends when duplicating a file.
_TRAILING_COPY_RE = re.compile(
    r"(?:\s*\(\d+\)|[\s_-]+(?:copy(?:\s*\d+)?|사본|복사본))+$",
    re.IGNORECASE,
)
#: A trailing date/timestamp suffix ("_2026-01-01", "-20260101",
#: "_20260101013000") a save-as or export step appended to the name.
_TRAILING_TIMESTAMP_RE = re.compile(r"[\s_-]+(?:\d{4}-\d{2}-\d{2}|\d{8,14})$")
#: Auto-generated names PowerPoint/Keynote/Google Slides assign, optionally
#: followed by a counter ("Presentation1", "Document (1)" already stripped
#: down to "Document").
_GENERIC_FILE_NAME_RE = re.compile(
    r"^(presentation|document|untitled|new presentation|slide|slides|"
    r"무제|새\s*프레젠테이션|프레젠테이션)\s*\d*$",
    re.IGNORECASE,
)
#: A bare uuid or content hash once separators are removed -- meaningless as
#: a title even though it survives the checks above.
_OPAQUE_ID_RE = re.compile(r"^[0-9a-f]{12,}$", re.IGNORECASE)


def title_from_file_name(original_name: str | None, *, max_length: int = 200) -> str | None:
    """A deck-title candidate cleaned from an uploaded file's own name.

    Strips the extension and any trailing copy/timestamp suffix, then
    rejects what remains when it is empty or a generic placeholder (a bare
    uuid/hash, or a literal like "presentation"/"document"/"무제") rather
    than something a reader would recognise as the deck's subject. A user's
    own upload name is otherwise typically named after its subject, which is
    why this candidate outranks the last-resort fallback constant.
    """

    stem = PurePosixPath(str(original_name or "").replace("\\", "/")).stem.strip()
    if not stem:
        return None
    previous = None
    while previous != stem:
        previous = stem
        stem = _TRAILING_COPY_RE.sub("", stem).strip()
        stem = _TRAILING_TIMESTAMP_RE.sub("", stem).strip()
    normalized = " ".join(re.split(r"[_\s]+", stem)).strip()
    if not normalized:
        return None
    if _GENERIC_FILE_NAME_RE.match(normalized):
        return None
    compact = re.sub(r"[\s_-]", "", normalized).lower()
    if _OPAQUE_ID_RE.match(compact):
        return None
    return normalized[:max_length]


__all__ = ["CORE_PROPERTIES_PART", "extract_document_title", "title_from_file_name"]
