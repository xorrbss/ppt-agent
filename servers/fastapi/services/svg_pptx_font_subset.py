"""Deterministic per-deck font subsets for SVG PPTX font embedding.

Embedding the bundled faces whole added ~10 MB (variable Noto Sans KR) plus
~2.6 MB per Pretendard weight to every PPTX, which is why SVG PPTX exports
left embedding off by default and PowerPoint substituted a system face on
machines without Noto Sans KR. This module shrinks each face to the glyphs the
package actually uses:

* a variable face is first instantiated at the slot's weight (with names and
  weight class updated), so PowerPoint sees an ordinary static Regular/Bold;
* the static face is then subset to every character that appears in the
  package's XML text plus a small baseline set, so a user's small edits in
  ASCII and common punctuation still render in the embedded face.

Output is byte-deterministic for the same input (timestamps are not
recalculated), because the whole-page converter builds the expected faces and
compares them with what was embedded. A face whose OS/2 ``fsType`` forbids
subsetting (bit 8) is returned unchanged.

Instancing the variable Noto face takes 8-24 s, and every export runs in a
fresh isolated worker whose environment carries no writable cache root. So the
static instances of the pinned Noto source at 400 and 700 are built once, at
image build time, by ``scripts/build_svg_pptx_font_instances.py`` into
``PREBUILT_INSTANCE_DIRECTORY`` (read-only at run time). The digest of each
instance is pinned in ``PREBUILT_INSTANCE_DIGESTS`` and checked on every load;
the pinned bytes are exactly what ``instantiate_static_face`` produces for that
source and weight, so embedded faces (and their receipt digests) do not change.
A checkout that has not run the build script falls back to instancing at
export time, with the ``APP_DATA_DIRECTORY`` cache when one is set.
"""

from __future__ import annotations

from functools import lru_cache
from io import BytesIO
import logging
import os
from pathlib import Path
import re
import tempfile
import zipfile
from xml.etree import ElementTree as ET

from utils.hashing import sha256_prefixed
from utils.svg_template_fonts import FONT_DIGEST as NOTO_VARIABLE_FONT_DIGEST

#: Characters always kept so light edits stay in the embedded face.
BASELINE_TEXT = (
    "".join(chr(code) for code in range(0x20, 0x7F))
    + " ·–—‘’“”…•※、。"
    + "「」『』〈〉《》【】（），．：？！"
)
_FS_TYPE_NO_SUBSETTING = 0x0100
_XML_PART = re.compile(r"ppt/.+\.xml")
_INSTANCE_CACHE_SCHEMA = "svg-pptx-font-instance/v1"

#: Read-only directory of the static instances built at image build time.
PREBUILT_INSTANCE_DIRECTORY = (
    Path(__file__).resolve().parents[1] / "assets" / "font_instances"
    / _INSTANCE_CACHE_SCHEMA.replace("/", "-")
)

#: ``(variable source digest, weight) -> static instance digest``. The values
#: are what ``instantiate_static_face`` produces with the locked fontTools
#: (``uv.lock``); the build script refuses to write anything else, so a
#: dependency change that alters the bytes fails the image build instead of
#: silently changing exported PPTX bytes.
PREBUILT_INSTANCE_DIGESTS: dict[tuple[str, int], str] = {
    (NOTO_VARIABLE_FONT_DIGEST, 400):
        "sha256:4609a7b62a6da24cae3a8b73ecde7003581b8f60662d60cc8f55a3793de07763",
    (NOTO_VARIABLE_FONT_DIGEST, 700):
        "sha256:cf93c6acfa4c4adcb580d156aa46fc3614ed0310f1e2ef23b5954476e85c872e",
}

_LOGGER = logging.getLogger(__name__)


def package_text(target: Path) -> str:
    """Return every distinct character in the package's ``ppt/**/*.xml`` text."""

    characters: set[str] = set()
    with zipfile.ZipFile(target) as archive:
        for name in archive.namelist():
            if not _XML_PART.fullmatch(name):
                continue
            root = ET.fromstring(archive.read(name))
            for node in root.iter():
                if node.text:
                    characters.update(node.text)
    return "".join(sorted(characters))


def subset_text(target: Path) -> str:
    return "".join(sorted(set(package_text(target)) | set(BASELINE_TEXT)))


def subset_face(payload: bytes, *, weight: int, text: str) -> bytes:
    """Return ``payload`` instantiated at ``weight`` (if variable) and subset to ``text``."""

    from fontTools import subset
    from fontTools.ttLib import TTFont

    with TTFont(BytesIO(payload), lazy=False) as probe:
        if probe["OS/2"].fsType & _FS_TYPE_NO_SUBSETTING:
            return payload
        variable = "fvar" in probe
    static = _static_instance(payload, weight) if variable else payload
    font = TTFont(BytesIO(static))
    font.recalcTimestamp = False
    options = subset.Options()
    options.layout_features = ["*"]
    options.name_IDs = ["*"]
    options.name_languages = ["*"]
    options.notdef_outline = True
    subsetter = subset.Subsetter(options)
    subsetter.populate(text=text)
    subsetter.subset(font)
    output = BytesIO()
    font.save(output)
    font.close()
    return output.getvalue()


def subset_faces(faces, *, text: str):
    """Map ``(family, slot, digest, payload)`` faces to their subset bytes and digests."""

    weights = {"regular": 400, "bold": 700}
    result = []
    for family, slot, _digest, payload in faces:
        subset_payload = subset_face(payload, weight=weights[slot], text=text)
        result.append((family, slot, sha256_prefixed(subset_payload), subset_payload))
    return tuple(result)


def _static_instance(payload: bytes, weight: int) -> bytes:
    source_digest = sha256_prefixed(payload)
    return _cached_static_instance(source_digest, weight, payload)


@lru_cache(maxsize=4)
def _cached_static_instance(source_digest: str, weight: int, payload: bytes) -> bytes:
    prebuilt = prebuilt_static_instance(source_digest, weight)
    if prebuilt is not None:
        return prebuilt
    # Without a prebuilt instance (a checkout that has not run the build
    # script), the instance is also kept on disk when a cache root is set.
    path = _instance_cache_path(source_digest, weight)
    if path is not None:
        cached = _read_cached_instance(path)
        if cached is not None:
            return cached
    instance = instantiate_static_face(payload, weight)
    if path is not None:
        _write_cached_instance(path, instance)
    return instance


def prebuilt_instance_path(source_digest: str, weight: int, directory: Path | None = None) -> Path:
    root = PREBUILT_INSTANCE_DIRECTORY if directory is None else directory
    return root / f"{source_digest.removeprefix('sha256:')}-{weight}.ttf"


def prebuilt_static_instance(source_digest: str, weight: int) -> bytes | None:
    """The pinned prebuilt instance; ``None`` if none is pinned or it is not built.

    A present file whose digest differs from the pin fails closed: it is a
    trusted asset, not a cache that may be stale.
    """

    expected = PREBUILT_INSTANCE_DIGESTS.get((source_digest, weight))
    if expected is None:
        return None
    path = prebuilt_instance_path(source_digest, weight)
    try:
        payload = path.read_bytes()
    except (FileNotFoundError, NotADirectoryError):
        _LOGGER.warning(
            "svg_pptx_font_instance_not_built: %s; instancing at export time "
            "(run scripts/build_svg_pptx_font_instances.py)", path.name,
        )
        return None
    except OSError as error:
        raise ValueError("svg_pptx_font_instance_unreadable") from error
    if sha256_prefixed(payload) != expected:
        raise ValueError("svg_pptx_font_instance_digest_mismatch")
    return payload


def instantiate_static_face(payload: bytes, weight: int) -> bytes:
    """Instantiate a variable face at ``weight`` as a static Regular/Bold face."""

    from fontTools.ttLib import TTFont
    from fontTools.varLib import instancer

    font = TTFont(BytesIO(payload))
    font.recalcTimestamp = False
    try:
        instance = instancer.instantiateVariableFont(font, {"wght": weight}, updateFontNames=True)
    except Exception:
        # Without a usable STAT table the names cannot be updated; the weight
        # class and style bits below still identify the slot.
        font = TTFont(BytesIO(payload))
        font.recalcTimestamp = False
        instance = instancer.instantiateVariableFont(font, {"wght": weight})
    bold = weight >= 700
    os2 = instance["OS/2"]
    os2.usWeightClass = weight
    os2.fsSelection = (os2.fsSelection & ~0b1100001) | (0b100000 if bold else 0b1000000)
    instance["head"].macStyle = (instance["head"].macStyle & ~0b1) | (0b1 if bold else 0)
    output = BytesIO()
    instance.save(output)
    return output.getvalue()


def _instance_cache_path(source_digest: str, weight: int) -> Path | None:
    from utils.get_env import get_app_data_directory_env

    root = get_app_data_directory_env()
    if not root:
        return None
    name = f"{source_digest.removeprefix('sha256:')}-{weight}.ttf"
    return Path(root) / "cache" / _INSTANCE_CACHE_SCHEMA.replace("/", "-") / name


def _read_cached_instance(path: Path) -> bytes | None:
    digest_path = path.with_suffix(".sha256")
    try:
        payload = path.read_bytes()
        expected = digest_path.read_text(encoding="ascii").strip()
    except OSError:
        return None
    return payload if sha256_prefixed(payload) == expected else None


def _write_cached_instance(path: Path, payload: bytes) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        for target, content in ((path, payload),
                                (path.with_suffix(".sha256"), sha256_prefixed(payload).encode("ascii"))):
            handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
            with os.fdopen(handle, "wb") as stream:
                stream.write(content)
            os.replace(temporary, target)
    except OSError:
        # A cache write failure only costs the next export the instancing time.
        return


__all__ = [
    "BASELINE_TEXT", "PREBUILT_INSTANCE_DIGESTS", "PREBUILT_INSTANCE_DIRECTORY",
    "instantiate_static_face", "package_text", "prebuilt_instance_path",
    "prebuilt_static_instance", "subset_face", "subset_faces", "subset_text",
]
