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
import xml.etree.ElementTree as ET

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

#: Weights each trusted (embeddable) font family actually bundles. The
#: renderer normalizes any other requested weight to one of these (see
#: ``services.svg_renderer``); it never invents a weight this registry does
#: not declare. Keyed casefolded; a family absent here is not trusted at all
#: -- the renderer neither validates nor embeds it.
TRUSTED_FONT_WEIGHTS: Final[dict[str, frozenset[int]]] = {
    FONT_FAMILY.casefold(): frozenset(weight for weight, _face, _file in BUNDLED_FACES),
}


def declared_weights(family: str) -> frozenset[int] | None:
    """The weights ``family`` bundles, or ``None`` if it is not a trusted font."""

    return TRUSTED_FONT_WEIGHTS.get(family.strip("'\"").casefold())


def _nearest_declared_weight(declared: frozenset[int], requested: int) -> int:
    """Round up to the nearest heavier declared weight, clamped at the
    bundled range's ends. This is also what a browser's own CSS font
    matching already approximates to when only these exact faces are
    embedded, so the rendered glyphs match what gets embedded."""
    ordered = sorted(declared)
    if requested <= ordered[0]:
        return ordered[0]
    if requested >= ordered[-1]:
        return ordered[-1]
    return next(weight for weight in ordered if weight >= requested)


def trusted_font_weights_used(root: ET.Element) -> tuple[set[int], list[dict]]:
    """Weights the renderer needs to embed for every trusted-font node in
    ``root``, plus one advisory per node whose requested weight this
    registry does not bundle exactly (normalized instead of failing).

    A font-family this registry does not declare at all is skipped entirely:
    it is neither validated nor embedded here.
    """

    weights: set[int] = set()
    advisories: list[dict] = []
    for node in root.iter():
        family = node.get("font-family", "")
        declared = declared_weights(family)
        if declared is None:
            continue
        raw = node.get("font-weight", "400").casefold()
        if raw == "bold":
            requested = 700
        elif raw == "normal":
            requested = 400
        else:
            try:
                requested = int(raw)
            except ValueError as error:
                raise ValueError("svg_trusted_font_weight_invalid") from error
        normalized = requested if requested in declared else _nearest_declared_weight(declared, requested)
        if normalized != requested:
            advisories.append({
                "family": family, "requested_weight": requested, "normalized_weight": normalized,
            })
        weights.add(normalized)
    return weights, advisories


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
    "TRUSTED_FONT_WEIGHTS",
    "declared_weights",
    "read_trusted_pretendard_faces",
    "trusted_font_weights_used",
]
