"""The Composer's typeface contract with the authored-hybrid exporter.

A Korean run is admitted as native text only when the run's own CSS stack names
a family in ``HANGUL_CAPABLE_AUTHORED_FAMILIES`` (Pretendard) and an exactly
matching face was embedded.  Only Regular (400) and Bold (700) are bundled, so
the renderer may not emit any other weight, and the publication must declare the
bundled family rather than Deck IR's Windows Malgun Gothic.
"""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

import pytest

from services.composer.fonts import (
    BUNDLED_FACES,
    FONT_DIRECTORY,
    FONT_FAMILY,
    composer_font_weight,
    composer_publication_font_materials,
    font_family_stack,
)

_COMPOSER = Path(__file__).resolve().parents[2] / "services" / "composer"
_NEXTJS = Path(__file__).resolve().parents[3] / "nextjs"


def test_the_embeddable_family_always_leads() -> None:
    assert font_family_stack("").startswith(f"{FONT_FAMILY},")
    assert font_family_stack("Noto Sans KR").startswith(f"{FONT_FAMILY},")
    assert font_family_stack("Gowun Dodum") == (
        "Pretendard, 'Gowun Dodum', 'Malgun Gothic', sans-serif"
    )


def test_a_style_font_survives_as_a_secondary_fallback() -> None:
    """It still serves glyphs Pretendard lacks; it just cannot displace it."""

    stack = font_family_stack("Noto Sans KR")
    assert "'Noto Sans KR'" in stack
    assert stack.index("Pretendard") < stack.index("'Noto Sans KR'")


def test_the_family_is_never_repeated() -> None:
    assert font_family_stack("Pretendard") == "Pretendard, 'Malgun Gothic', sans-serif"
    assert font_family_stack("Malgun Gothic").count("Malgun Gothic") == 1


@pytest.mark.parametrize(
    ("requested", "expected"),
    [(None, 400), (100, 400), (400, 400), (500, 400), (600, 700), (700, 700), (900, 700)],
)
def test_weights_snap_onto_a_bundled_face(requested: int | None, expected: int) -> None:
    """600 is bold because ``embeddedFaceForStyle`` treats >= 600 as bold."""

    assert composer_font_weight(requested) == expected


def test_the_renderer_only_emits_bundled_weights() -> None:
    """A 500 or 600 weight would have no exact embedded face and rasterize."""

    emitted: set[str] = set()
    for module in sorted(_COMPOSER.glob("render*.py")):
        emitted.update(re.findall(r"font-weight:\s*([0-9]+)", module.read_text(encoding="utf-8")))
    assert emitted, "the renderer declares at least one explicit weight"
    assert emitted <= {"400", "700"}, f"unembeddable weights: {sorted(emitted - {'400', '700'})}"


def test_the_bundled_faces_match_the_exporter_package() -> None:
    """``lib/authored-hybrid/bundled-fonts.ts`` is the other half of this pair."""

    bundled = (_NEXTJS / "lib" / "authored-hybrid" / "bundled-fonts.ts").read_text(
        encoding="utf-8"
    )
    for _weight, _face, file_name in BUNDLED_FACES:
        assert file_name in bundled
        assert (FONT_DIRECTORY / file_name).is_file()


def test_publication_declares_the_bundled_pretendard_faces() -> None:
    manifest, artifacts, family, face = asyncio.run(composer_publication_font_materials())

    assert family == "Pretendard"
    assert face == "Regular"
    assert sorted(font.face for font in manifest.fonts) == ["Bold", "Regular"]
    assert {font.family for font in manifest.fonts} == {"Pretendard"}
    assert all(font.embedding_permitted for font in manifest.fonts)
    assert all(font.resolver_version == "composer-bundled-pretendard/v1" for font in manifest.fonts)
    assert sorted(artifacts) == sorted(font.artifact.digest for font in manifest.fonts)
    assert all(len(payload) > 0 for payload in artifacts.values())


def test_publication_never_reaches_for_a_windows_system_font() -> None:
    """Malgun Gothic stays on Deck IR's path; embedding it is a licence risk."""

    assert FONT_DIRECTORY == _NEXTJS / "public" / "fonts" / "pretendard"
    windows_fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    for _weight, _face, file_name in BUNDLED_FACES:
        assert windows_fonts not in (FONT_DIRECTORY / file_name).parents
    # Deck IR keeps its own Windows resolver; the Composer must not borrow it.
    publish = (_COMPOSER / "publish.py").read_text(encoding="utf-8")
    assert "deck_ir.product_adapter" not in publish
    assert "composer_publication_font_materials" in publish
