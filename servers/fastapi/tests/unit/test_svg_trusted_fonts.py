"""The SVG renderer's trusted Pretendard faces match the exporter package."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

import services.svg_trusted_fonts as trusted_fonts
from services.svg_trusted_fonts import (
    BUNDLED_FACES,
    FONT_DIRECTORY,
    read_trusted_pretendard_faces,
)

_NEXTJS = Path(__file__).resolve().parents[3] / "nextjs"


def test_bundled_faces_match_the_exporter_package() -> None:
    manifest = (_NEXTJS / "lib" / "authored-hybrid" / "bundled-fonts-manifest.ts").read_text(
        encoding="utf-8"
    )
    for _weight, _face, file_name in BUNDLED_FACES:
        assert file_name in manifest
        assert (FONT_DIRECTORY / file_name).is_file()


def test_trusted_faces_are_regular_and_bold_with_their_own_digests() -> None:
    faces = read_trusted_pretendard_faces()

    assert [weight for weight, _digest, _payload in faces] == [400, 700]
    for _weight, digest, payload in faces:
        assert digest == "sha256:" + hashlib.sha256(payload).hexdigest()


def test_trusted_faces_never_come_from_a_windows_system_font() -> None:
    assert FONT_DIRECTORY == _NEXTJS / "public" / "fonts" / "pretendard"
    windows_fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    for _weight, _face, file_name in BUNDLED_FACES:
        assert windows_fonts not in (FONT_DIRECTORY / file_name).parents


def test_a_mislabelled_face_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    swapped = ((400, "Bold", "Pretendard-Regular.ttf"),)
    monkeypatch.setattr(trusted_fonts, "BUNDLED_FACES", swapped)

    with pytest.raises(ValueError, match="svg_trusted_font_identity_invalid"):
        read_trusted_pretendard_faces()
