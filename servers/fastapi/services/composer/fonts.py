"""The bundled typeface the Composer renders with, publishes and embeds.

The Composer's stylesheet names ``Pretendard`` as its Hangul-capable family
(``services.composer.render_css``), the authored-hybrid renderer injects the
bundled Pretendard faces as ``@font-face`` before it measures a slide, and the
exporter admits a Korean run as native text only when the run's own stack names
a family in ``HANGUL_CAPABLE_AUTHORED_FAMILIES`` *and* an exactly matching face
was embedded (``lib/authored-hybrid/font-policy.ts``).  Publishing the same
family closes that loop: the emission binding names Pretendard, so
``nativeEmbeddingFamilies`` requests it and ``prepareFontEmbedding`` finds the
bundled bytes.

Deck IR keeps its own resolver (``_publication_font_materials``), which reads
``C:\\Windows\\Fonts\\malgun.ttf``.  That path is deliberately not shared:
Malgun Gothic is a Windows system font, and embedding it into a deck a customer
receives is a licensing exposure.  Pretendard ships under the SIL Open Font
License in ``servers/nextjs/public/fonts/pretendard`` and is embeddable.

Only Regular (400) and Bold (700) are bundled, which is the whole weight
vocabulary the Composer renders; ``composer_font_weight`` is the single place
that maps a requested weight onto them.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Final

from models.authored_vnext.semantic.contracts import (
    ArtifactPointerV1,
    FontManifestV1,
    ResolvedFontV1,
)
from services.authored_vnext.semantic.canonical import bytes_digest, canonical_digest
from services.authored_vnext.semantic.typography import (
    NativeFontByteRegistry,
    NativeTypographyRegistryError,
)
from services.composer.receipt import ComposerPublicationError

#: Repository root: ``servers/fastapi/services/composer/fonts.py`` -> 4 parents.
_REPOSITORY_ROOT: Final[Path] = Path(__file__).resolve().parents[4]
FONT_DIRECTORY: Final[Path] = (
    _REPOSITORY_ROOT / "servers" / "nextjs" / "public" / "fonts" / "pretendard"
)
FONT_FAMILY: Final[str] = "Pretendard"
RESOLVER_VERSION: Final[str] = "composer-bundled-pretendard/v1"

#: The exact faces ``lib/authored-hybrid/bundled-fonts.ts`` ships, by weight.
BUNDLED_FACES: Final[tuple[tuple[int, str, str], ...]] = (
    (400, "Regular", "Pretendard-Regular.ttf"),
    (700, "Bold", "Pretendard-Bold.ttf"),
)
REGULAR_FACE: Final[str] = "Regular"
BOLD_WEIGHT_THRESHOLD: Final[int] = 600


def composer_font_weight(weight: int | None) -> int:
    """Snap a requested weight onto a bundled face.

    A run rendered at 500 or 600 has no exactly matching embedded face, so the
    exporter's typography preflight rasterizes it (``embedding-face-missing``).
    The renderer therefore only ever emits 400 or 700, and semi-bold emphasis
    is expressed as bold rather than as a weight the deck cannot embed.
    """

    if weight is None:
        return 400
    return 700 if int(weight) >= BOLD_WEIGHT_THRESHOLD else 400


def font_family_stack(*fallbacks: str) -> str:
    """The CSS stack, with the embeddable family first.

    A style's own face may follow as a secondary fallback -- it still serves
    any glyph Pretendard lacks -- but it must never lead, or Chromium resolves
    an installed system face while the exporter emits Pretendard, which is the
    ``font-substitution`` the typography preflight rejects.
    """

    stack: list[str] = [FONT_FAMILY]
    for fallback in fallbacks:
        name = (fallback or "").strip().strip("'\"")
        if not name or name.casefold() == FONT_FAMILY.casefold():
            continue
        quoted = f"'{name}'" if " " in name else name
        if quoted not in stack:
            stack.append(quoted)
    if "'Malgun Gothic'" not in stack:
        stack.append("'Malgun Gothic'")
    stack.append("sans-serif")
    return ", ".join(stack)


def _resolve_face(path: Path, expected_face: str) -> tuple[ResolvedFontV1, str, bytes]:
    content = path.read_bytes()
    registry = NativeFontByteRegistry()
    receipt = registry.register(content)
    if (
        receipt.family.casefold() != FONT_FAMILY.casefold()
        or receipt.face.casefold() != expected_face.casefold()
    ):
        raise ComposerPublicationError("composer_font_identity_mismatch")
    if not receipt.embedding_permitted:
        raise ComposerPublicationError("composer_font_embedding_not_permitted")
    digest_value = bytes_digest(content)
    resolved = ResolvedFontV1(
        face_id=f"font:pretendard-{expected_face.casefold()}",
        family=receipt.family,
        face=receipt.face,
        artifact=ArtifactPointerV1(
            artifact_ref="artifact://sha256/" + digest_value.removeprefix("sha256:"),
            digest=digest_value,
            byte_length=len(content),
            media_type="font/ttf",
        ),
        locator_digest=canonical_digest(
            {
                "family": receipt.family,
                "face": receipt.face,
                "fontByteDigest": receipt.font_byte_digest,
                "resolverVersion": RESOLVER_VERSION,
            }
        ),
        resolver_version=RESOLVER_VERSION,
        embedding_permitted=receipt.embedding_permitted,
        subsetting_permitted=receipt.subsetting_permitted,
        retention_class="active",
    )
    return resolved, digest_value, content


def _read_bundled_faces() -> tuple[FontManifestV1, dict[str, bytes], str, str]:
    fonts: list[ResolvedFontV1] = []
    artifacts: dict[str, bytes] = {}
    for _weight, face, file_name in BUNDLED_FACES:
        resolved, digest_value, content = _resolve_face(FONT_DIRECTORY / file_name, face)
        fonts.append(resolved)
        artifacts[digest_value] = content
    return FontManifestV1(fonts=fonts), artifacts, FONT_FAMILY, REGULAR_FACE


async def composer_publication_font_materials() -> tuple[
    FontManifestV1, dict[str, bytes], str, str
]:
    """Resolve the bundled Pretendard bytes the published deck declares.

    Returns the same shape the authored publication expects: the font manifest,
    the artifact bytes keyed by digest, and the family/face a semantic text run
    declares.
    """

    try:
        return await asyncio.to_thread(_read_bundled_faces)
    except ComposerPublicationError:
        raise
    except (OSError, NativeTypographyRegistryError) as error:
        raise ComposerPublicationError("composer_font_bytes_unavailable") from error


__all__ = [
    "BOLD_WEIGHT_THRESHOLD",
    "BUNDLED_FACES",
    "FONT_DIRECTORY",
    "FONT_FAMILY",
    "REGULAR_FACE",
    "RESOLVER_VERSION",
    "composer_font_weight",
    "composer_publication_font_materials",
    "font_family_stack",
]
