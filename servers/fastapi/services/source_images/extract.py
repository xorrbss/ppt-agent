"""Pull reusable pictures out of a PPTX the user uploaded.

The reader walks the OOXML package directly instead of loading a slide model:
we only need each picture's bytes plus the slide it sat on, and a direct zip
walk keeps this path independent of the master-import subsystem.

Every failure mode here is a bad upload, not a bug, so nothing raises to the
caller: a truncated archive, a foreign file renamed to ``.pptx`` and a package
with no slides all yield an empty list. Extraction is deliberately mechanical --
it reports what the package contains and leaves relevance filtering (minimum
edge, furniture repeats) to the candidate builder that consumes it.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from io import BytesIO
import logging
from pathlib import Path, PurePosixPath
import posixpath
from typing import Optional
from xml.etree import ElementTree as ET
import zipfile

from PIL import Image, UnidentifiedImageError

logger = logging.getLogger(__name__)

NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}

#: Ceilings mirror the master import path: they bound the work a hostile or
#: merely broken upload can cause before anything is parsed.
MAX_PPTX_BYTES = 50_000_000
MAX_EXPANDED_BYTES = 300_000_000
MAX_ZIP_MEMBERS = 20_000

#: A heading is a matching hint for the slide author, not a transcript.
MAX_HEADING_CHARS = 120

#: Only the formats the authoring call can attach; anything else (SVG, EMF,
#: GIF, TIFF) is dropped rather than converted.
_EXTENSION_MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}

_TITLE_PLACEHOLDER_TYPES = {"title", "ctrTitle"}


@dataclass(frozen=True)
class ExtractedSourceImage:
    """One picture found on one slide of the uploaded deck.

    ``digest`` is the bare lowercase SHA256 hex of ``payload`` -- the same
    convention the rest of the authored pipeline uses, without the ``sha256:``
    prefix the vnext master contract carries.

    The same media part referenced from several slides yields one entry per
    slide, because the consumer counts those repeats to recognise furniture.
    """

    payload: bytes
    mime_type: str
    width: int
    height: int
    digest: str
    #: Zero-based position in the deck's slide order.
    source_slide_index: int
    source_heading: Optional[str] = None


def extract_pptx_images(pptx_path: Path) -> list[ExtractedSourceImage]:
    """Return every usable picture in ``pptx_path``, in slide order.

    Returns an empty list for anything that is not a readable presentation.
    """

    package = _read_package(pptx_path)
    if package is None:
        return []

    measured: dict[str, Optional[tuple[str, int, int, str]]] = {}
    extracted: list[ExtractedSourceImage] = []
    for slide_index, slide_part in enumerate(_slide_parts(package)):
        try:
            root = ET.fromstring(package[slide_part])
        except ET.ParseError:
            # One unreadable slide must not cost us the rest of the deck.
            continue
        heading = _slide_heading(root)
        relationships = _relationships(package, slide_part)
        seen_on_slide: set[str] = set()
        for media_part in _referenced_media_parts(root, relationships, package):
            if media_part not in measured:
                measured[media_part] = _measure(media_part, package[media_part])
            details = measured[media_part]
            if details is None:
                continue
            mime_type, width, height, digest = details
            if digest in seen_on_slide:
                # A picture placed twice on one slide is still one occurrence.
                continue
            seen_on_slide.add(digest)
            extracted.append(
                ExtractedSourceImage(
                    payload=package[media_part],
                    mime_type=mime_type,
                    width=width,
                    height=height,
                    digest=digest,
                    source_slide_index=slide_index,
                    source_heading=heading,
                )
            )
    return extracted


def _read_package(pptx_path: Path) -> Optional[dict[str, bytes]]:
    """Load the package parts, or ``None`` when the file is not a usable PPTX."""

    try:
        if not pptx_path.is_file() or pptx_path.stat().st_size > MAX_PPTX_BYTES:
            return None
        with zipfile.ZipFile(pptx_path) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ZIP_MEMBERS:
                return None
            if sum(item.file_size for item in infos) > MAX_EXPANDED_BYTES:
                return None
            package: dict[str, bytes] = {}
            for item in infos:
                if item.is_dir():
                    continue
                normalized = posixpath.normpath(item.filename.replace("\\", "/"))
                if normalized.startswith("../") or normalized.startswith("/"):
                    # A member escaping the package root is malformed by
                    # definition; refuse the whole archive rather than guess.
                    return None
                package[normalized] = archive.read(item)
    except (OSError, ValueError, zipfile.BadZipFile, zipfile.LargeZipFile):
        logger.debug("Source image extraction skipped an unreadable package")
        return None
    if "ppt/presentation.xml" not in package:
        return None
    return package


def _slide_parts(package: dict[str, bytes]) -> list[str]:
    """Slide part names in presentation order."""

    try:
        root = ET.fromstring(package["ppt/presentation.xml"])
    except ET.ParseError:
        return []
    relationships = _relationships(package, "ppt/presentation.xml")
    parts: list[str] = []
    for node in root.findall(".//p:sldId", NS):
        relation = relationships.get(node.attrib.get(f"{{{NS['r']}}}id") or "")
        if relation and relation[0] == "slide" and relation[1] in package:
            parts.append(relation[1])
    return parts


def _relationships(package: dict[str, bytes], part: str) -> dict[str, tuple[str, str]]:
    """Map relationship id -> (relationship kind, package-relative target)."""

    path = PurePosixPath(part)
    rel_part = str(path.parent / "_rels" / f"{path.name}.rels")
    if rel_part not in package:
        return {}
    try:
        root = ET.fromstring(package[rel_part])
    except ET.ParseError:
        return {}
    relations: dict[str, tuple[str, str]] = {}
    for node in root:
        identifier = node.attrib.get("Id")
        # External targets are URLs, not parts we can read bytes from.
        if not identifier or node.attrib.get("TargetMode") == "External":
            continue
        relations[identifier] = (
            node.attrib.get("Type", "").rsplit("/", 1)[-1],
            _resolve_target(part, node.attrib.get("Target", "")),
        )
    return relations


def _resolve_target(source_part: str, target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    return posixpath.normpath(posixpath.join(posixpath.dirname(source_part), target))


def _referenced_media_parts(
    root: ET.Element,
    relationships: dict[str, tuple[str, str]],
    package: dict[str, bytes],
) -> list[str]:
    """Media parts this slide embeds, in document order.

    Every ``a:blip`` counts, not only the ones inside ``p:pic``: a photo used as
    a shape fill is just as reusable as one dropped on the canvas. A target the
    package does not actually carry is a dangling relationship, not a picture.
    """

    parts: list[str] = []
    for blip in root.iter(f"{{{NS['a']}}}blip"):
        relation = relationships.get(blip.attrib.get(f"{{{NS['r']}}}embed") or "")
        if relation and relation[1] in package and relation[1] not in parts:
            parts.append(relation[1])
    return parts


def _measure(part: str, payload: bytes) -> Optional[tuple[str, int, int, str]]:
    """Return ``(mime_type, width, height, digest)`` for a usable picture."""

    declared = _EXTENSION_MIME_TYPES.get(PurePosixPath(part).suffix.lower())
    if declared is None:
        return None
    # A part whose bytes contradict its extension is either mislabelled or
    # disguised; either way we refuse to hand it to the authoring call.
    if _sniff_mime(payload) != declared:
        logger.debug("Source image extraction skipped a part whose bytes contradict its extension")
        return None
    try:
        with Image.open(BytesIO(payload)) as image:
            width, height = image.size
    except (OSError, UnidentifiedImageError, ValueError):
        logger.debug("Source image extraction skipped an undecodable picture")
        return None
    if width <= 0 or height <= 0:
        return None
    return declared, int(width), int(height), hashlib.sha256(payload).hexdigest()


def _sniff_mime(payload: bytes) -> Optional[str]:
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if payload.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if payload.startswith(b"RIFF") and payload[8:12] == b"WEBP":
        return "image/webp"
    return None


def _slide_heading(root: ET.Element) -> Optional[str]:
    """The slide's title, falling back to its first text.

    A deck whose titles live in a plain text box is common enough that the
    fallback is worth more than a strict placeholder read.
    """

    tree = root.find(".//p:spTree", NS)
    if tree is None:
        return None
    first_text: Optional[str] = None
    for shape in tree.iter(f"{{{NS['p']}}}sp"):
        text = _shape_text(shape)
        if not text:
            continue
        if _is_title_placeholder(shape):
            return _trim(text)
        if first_text is None:
            first_text = text
    return _trim(first_text) if first_text else None


def _is_title_placeholder(shape: ET.Element) -> bool:
    placeholder = shape.find(".//p:nvSpPr/p:nvPr/p:ph", NS)
    if placeholder is None:
        return False
    return placeholder.attrib.get("type", "") in _TITLE_PLACEHOLDER_TYPES


def _shape_text(shape: ET.Element) -> str:
    """Shape text with runs kept whole and paragraphs separated.

    PowerPoint splits a single word across runs whenever formatting changes, so
    runs join without a separator and only paragraphs get one.
    """

    paragraphs: list[str] = []
    for paragraph in shape.iter(f"{{{NS['a']}}}p"):
        runs = "".join(node.text or "" for node in paragraph.iter(f"{{{NS['a']}}}t"))
        if runs.strip():
            paragraphs.append(runs.strip())
    return " ".join(paragraphs).strip()


def _trim(text: str) -> Optional[str]:
    collapsed = " ".join(text.split())
    if not collapsed:
        return None
    return collapsed[:MAX_HEADING_CHARS].strip()
