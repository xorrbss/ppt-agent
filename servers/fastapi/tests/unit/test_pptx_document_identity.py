"""Deck-title candidate cleaning from an uploaded PPTX's own file name.

``title_from_file_name`` is the file-name half of the ``deck_title()``
fallback chain (``services/composer/pipeline_inputs.py``): a user's own
upload is typically named after its subject, so a cleaned candidate from it
outranks the last-resort constant, but a generic or opaque name must not
become the title. These are direct unit tests of the pure cleaning
function; its wiring into ``extract_pptx_source_content`` (including the
docProps/core.xml document title) is covered by
``tests/unit/test_composer_manifest_enrichment.py``.
"""

from __future__ import annotations

import pytest

from services.source_content.pptx_document_identity import title_from_file_name


@pytest.mark.parametrize(
    ("original_name", "expected"),
    [
        ("Q3 Board Deck.pptx", "Q3 Board Deck"),
        # Trailing "(2)" copy marker and a trailing date stripped together,
        # underscores normalised to spaces.
        ("Q3_Board_Deck_2026-01-05 (2).pptx", "Q3 Board Deck"),
        ("Annual Report - Copy.pptx", "Annual Report"),
        ("Annual Report 사본.pptx", "Annual Report"),
        ("Annual Report 복사본.pptx", "Annual Report"),
        # Bare timestamp suffix with no separator word.
        ("Board Update 20260105.pptx", "Board Update"),
        # Generic / auto-generated names -- rejected outright.
        ("Presentation1.pptx", None),
        ("Presentation.pptx", None),
        ("document.pptx", None),
        ("Document (1).pptx", None),
        ("무제.pptx", None),
        ("새 프레젠테이션.pptx", None),
        # A bare uuid or content-hash-like name -- meaningless as a title.
        ("3fa85f64-5717-4562-b3fc-2c963f66afa6.pptx", None),
        ("9f8e7d6c5b4a3f2e1d0c9b8a7f6e5d4c1a2b3c4d.pptx", None),
        # Nothing left after cleaning, or nothing to clean at all.
        ("   .pptx", None),
        ("", None),
        (None, None),
    ],
)
def test_title_from_file_name(original_name: str | None, expected: str | None) -> None:
    assert title_from_file_name(original_name) == expected


def test_title_from_file_name_caps_length() -> None:
    long_name = "가" * 300 + ".pptx"
    result = title_from_file_name(long_name)
    assert result == "가" * 200
    assert len(result) == 200


def test_title_from_file_name_ignores_windows_path_separators() -> None:
    assert title_from_file_name("C:\\Users\\me\\Documents\\Q3 Board Deck.pptx") == "Q3 Board Deck"
