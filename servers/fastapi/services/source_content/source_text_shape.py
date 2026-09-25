"""Glyph classes and whitespace normalization shared by source-run modules.

Split out of ``source_runs.py`` so both that module and
``source_run_segments.py`` can depend on these primitives without importing
each other -- kept free of any dependency on either so it cannot create a
cycle.
"""

from __future__ import annotations

import re

_MARKER_GLYPHS = "•·◦▪▫■□◆◇●○★☆✓✔✗☐☑▶►➤➜→⇒↓⇨‣⁃∙*-–—"
_ITEM_GLYPHS = "•□■◦▪✓✔☐☑▶►➤‣⁃"
_ENUMERATORS = "①-⑳⑴-⒇⒈-⒛❶-❿㉠-㉭㈀-㈜"


def _squash(value: object) -> str:
    """Drop whitespace entirely so layout cannot hide present source text."""

    return re.sub(r"\s+", "", str(value or ""))


def normalize_source_text(value: object) -> str:
    """Collapse layout whitespace exactly like the readback verifiers."""

    return re.sub(r"\s+", " ", str(value or "")).strip()
