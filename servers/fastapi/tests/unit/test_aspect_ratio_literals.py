"""Every closed aspect-ratio Literal must carry the new preset canvases.

docs/canvas-expansion-admission-spec-20260929.md §3 item 1: a site left on the
old closed set 422s (request models) or 500s (``/authored/styles``) as soon as
a gated canvas is enabled.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

FASTAPI_ROOT = Path(__file__).resolve().parents[2]
LITERAL_SITES = (
    "services/authored_canvas.py",
    "models/generate_presentation_request.py",
    "api/v1/ppt/endpoints/presentation_crud.py",
    "api/v2/contracts.py",
    "api/v1/ppt/endpoints/authored.py",
)
ASPECT_LITERAL = re.compile(r"Literal\[([^\[\]]*\"16:9\"[^\[\]]*)\]")


@pytest.mark.parametrize("relative_path", LITERAL_SITES)
def test_aspect_ratio_literals_include_new_presets(relative_path: str) -> None:
    source = (FASTAPI_ROOT / relative_path).read_text(encoding="utf-8")

    assert '"16:9", "9:16", "9:13"]' not in source
    assert 'Literal["16:9", "9:16"]' not in source
    literals = ASPECT_LITERAL.findall(source)
    assert literals, f"{relative_path} no longer declares an aspect-ratio Literal"
    for members in literals:
        assert '"1:1"' in members and '"21:9"' in members, (relative_path, members)
