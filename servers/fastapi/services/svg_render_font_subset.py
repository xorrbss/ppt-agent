"""Per-slide subsets of the bundled faces embedded into svg render documents.

Every default-profile svg render used to base64-inline the whole variable
Noto Sans KR (10.4 MB) plus each used Pretendard weight, so one wrapper
document weighed ~14 MB and Chromium spent ~2 s parsing it per page. The
renderer only needs the glyphs the page's text can reach, so this module
shrinks each face to that closure.

Measurement parity is the contract, so the subset keeps everything that can
change layout or paint for text made of the page's characters:

* every retained glyph keeps its outline, hinting, advance and side bearings,
  and the variable face keeps its full ``wght`` axis with its ``gvar``/``HVAR``
  deltas. Instancing the axis down to the used weights would rewrite the
  deltas and the interpolated outlines with them, so it is not done;
* every OpenType layout feature, script and language system is kept, and the
  glyph closure follows them (``locl`` for Korean, ``ljmo``/``vjmo``/``tjmo``
  jamo shaping, ``ccmp``, ``kern``, ``mark``/``mkmk`` and the rest), so text of
  the page's characters shapes exactly as with the full face;
* no table is dropped and no face-wide metric (``head``/``hhea``/``OS/2``) is
  recalculated, so ascent, descent and line gap stay the full face's.

The charset is the page's text, its NFC and NFD forms (HarfBuzz composes or
decomposes before glyph lookup) and U+0020, which the ink probe measures. A
subset is deterministic for its inputs and cached in-process by source digest,
face (weight/axes), subsetter identity and charset digest. Generation is
synchronous CPU work; async callers run it off the event loop.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from io import BytesIO
import threading
import unicodedata
import xml.etree.ElementTree as ET

from utils.hashing import canonical_json_digest

SUBSET_REVISION = "svg-render-font-subset/v1"
#: Characters every subset keeps: the ink probe measures a space advance.
BASELINE_CHARACTERS = " "
#: Shaping fallbacks HarfBuzz applies when a face lacks the first codepoint.
_SHAPING_FALLBACKS = {"‑": "‐"}
_FS_TYPE_NO_SUBSETTING = 0x0100
#: Options that keep every glyph-, layout- and metric-bearing table intact.
SUBSET_OPTIONS = {
    "layout_features": ["*"],
    "layout_scripts": ["*"],
    "layout_closure": True,
    "name_IDs": ["*"],
    "name_languages": ["*"],
    "name_legacy": True,
    "notdef_glyph": True,
    "notdef_outline": True,
    "recommended_glyphs": True,
    "glyph_names": False,
    "legacy_kern": True,
    "hinting": True,
    "drop_tables": [],
    "prune_unicode_ranges": False,
    "prune_codepage_ranges": False,
    "recalc_bounds": False,
    "recalc_timestamp": False,
    "recalc_average_width": False,
    "recalc_max_context": False,
}
_CACHE_MAX_ENTRIES = 32
_CACHE_MAX_BYTES = 32 * 1024 * 1024
_cache_lock = threading.Lock()
_cache: "OrderedDict[tuple[str, str, str, str], bytes]" = OrderedDict()


def subsetter_identity() -> str:
    """The subsetter's version and its option set, as one provenance string."""

    import fontTools

    return f"fontTools/{fontTools.version}|options:{canonical_json_digest(SUBSET_OPTIONS)}"


def render_charset(root: ET.Element) -> str:
    """Sorted unique characters the page's text can render."""

    text = "".join(root.itertext())
    characters = set(text) | set(unicodedata.normalize("NFC", text)) | set(unicodedata.normalize("NFD", text))
    characters |= set(BASELINE_CHARACTERS)
    characters |= {fallback for source, fallback in _SHAPING_FALLBACKS.items() if source in characters}
    return "".join(sorted(characters))


def charset_digest(charset: str) -> str:
    return canonical_json_digest([ord(character) for character in charset])


@dataclass(frozen=True, slots=True)
class SubsetFace:
    family: str
    weight: str
    #: ``wght:100-900`` for the variable face, ``static`` for a static one.
    axes: str
    source_digest: str


def _subset(payload: bytes, charset: str) -> bytes:
    from fontTools import subset
    from fontTools.ttLib import TTFont

    options = subset.Options()
    for name, value in SUBSET_OPTIONS.items():
        setattr(options, name, value)
    font = TTFont(BytesIO(payload), lazy=True, recalcBBoxes=False, recalcTimestamp=False)
    try:
        if "OS/2" in font and font["OS/2"].fsType & _FS_TYPE_NO_SUBSETTING:
            return payload
        subsetter = subset.Subsetter(options)
        subsetter.populate(unicodes=[ord(character) for character in charset])
        subsetter.subset(font)
        output = BytesIO()
        font.save(output)
        return output.getvalue()
    finally:
        font.close()


def subset_face(face: SubsetFace, payload: bytes, charset: str) -> bytes:
    """Return the cached subset of ``payload`` for ``charset``.

    ``payload`` must be the identity-checked bytes ``face.source_digest`` names;
    the callers read them from the digest-verified font registries.
    """

    key = (face.source_digest, f"{face.weight}|{face.axes}", subsetter_identity(), charset_digest(charset))
    with _cache_lock:
        cached = _cache.get(key)
        if cached is not None:
            _cache.move_to_end(key)
            return cached
    result = _subset(payload, charset)
    with _cache_lock:
        _cache[key] = result
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_MAX_ENTRIES or sum(map(len, _cache.values())) > _CACHE_MAX_BYTES:
            _cache.popitem(last=False)
    return result


def subset_provenance(faces: list[SubsetFace], charset: str) -> dict:
    """What a render embedded, recorded beside its measurement."""

    return {
        "revision": SUBSET_REVISION,
        "subsetter": subsetter_identity(),
        "charset_digest": charset_digest(charset),
        "codepoint_count": len(charset),
        "faces": [{"family": face.family, "weight": face.weight, "axes": face.axes,
                   "source_digest": face.source_digest} for face in faces],
    }


def clear_subset_cache() -> None:
    with _cache_lock:
        _cache.clear()


__all__ = [
    "BASELINE_CHARACTERS",
    "SUBSET_OPTIONS",
    "SUBSET_REVISION",
    "SubsetFace",
    "charset_digest",
    "clear_subset_cache",
    "render_charset",
    "subset_face",
    "subset_provenance",
    "subsetter_identity",
]
