"""The bundled Pretendard faces the SVG renderer trusts and embeds.

Only Regular (400) and Bold (700) are bundled.  They are the same files the
authored-hybrid exporter ships (``lib/authored-hybrid/bundled-fonts-manifest.ts``)
and are licensed for embedding under the SIL Open Font License, unlike a
Windows system face such as Malgun Gothic.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Final

from services.authored_vnext.semantic.typography import NativeFontByteRegistry

#: Repository root: ``servers/fastapi/services/svg_trusted_fonts.py`` -> 3 parents.
_REPOSITORY_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
FONT_DIRECTORY: Final[Path] = (
    _REPOSITORY_ROOT / "servers" / "nextjs" / "public" / "fonts" / "pretendard"
)
FONT_FAMILY: Final[str] = "Pretendard"

#: The exact faces the exporter ships, by weight: (weight, face, file name).
BUNDLED_FACES: Final[tuple[tuple[int, str, str], ...]] = (
    (400, "Regular", "Pretendard-Regular.ttf"),
    (700, "Bold", "Pretendard-Bold.ttf"),
)


def read_trusted_pretendard_faces() -> tuple[tuple[int, str, bytes], ...]:
    """Return ``(weight, sha256 digest, bytes)`` for every bundled face.

    Each file must identify as the expected Pretendard face and permit
    embedding; anything else fails closed with a ``ValueError``.
    """

    result: list[tuple[int, str, bytes]] = []
    for weight, face, file_name in BUNDLED_FACES:
        try:
            payload = (FONT_DIRECTORY / file_name).read_bytes()
        except OSError as error:
            raise ValueError("svg_trusted_font_unavailable") from error
        receipt = NativeFontByteRegistry().register(payload)
        if (
            receipt.family.casefold() != FONT_FAMILY.casefold()
            or receipt.face.casefold() != face.casefold()
        ):
            raise ValueError("svg_trusted_font_identity_invalid")
        if not receipt.embedding_permitted:
            raise ValueError("svg_trusted_font_embedding_not_permitted")
        digest = "sha256:" + hashlib.sha256(payload).hexdigest()
        result.append((weight, digest, payload))
    return tuple(result)


__all__ = [
    "BUNDLED_FACES",
    "FONT_DIRECTORY",
    "FONT_FAMILY",
    "read_trusted_pretendard_faces",
]
