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
from io import BytesIO
import logging
from pathlib import Path, PurePosixPath
import posixpath
from typing import Optional
from xml.etree import ElementTree as ET
import zipfile

from PIL import Image, UnidentifiedImageError
from utils.hashing import sha256_hex

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

#: The body text is a matching signal, not a transcript either. Enough to carry
#: a slide's subject, bounded so a dense table cannot dominate the comparison.
MAX_SOURCE_TEXT_CHARS = 600

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
    #: Everything the source slide says, condensed. Titles alone are often
    #: section markers ("02 · SOLUTION") that carry no subject, so the body text
    #: is what lets a consumer tell which new slide a picture belongs to.
    source_text: Optional[str] = None
    #: Share of the slide this picture was placed over, when the deck states
    #: both the slide size and the picture's extent. ``None`` means the deck
    #: did not say (a shape fill, or a picture without an explicit extent).
    slide_coverage: Optional[float] = None
    #: Local content relation, separate from slide-wide provenance text.
    content_group: Optional[dict] = None


def extract_pptx_images(pptx_path: Path) -> list[ExtractedSourceImage]:
    """Return every usable picture in ``pptx_path``, in slide order.

    Returns an empty list for anything that is not a readable presentation.
    """

    package = _read_package(pptx_path)
    if package is None:
        return []

    measured: dict[str, Optional[tuple[str, int, int, str]]] = {}
    try:
        document_digest = sha256_hex(pptx_path.read_bytes())
    except OSError:
        return []
    slide_area = _slide_area(package)
    extracted: list[ExtractedSourceImage] = []
    for slide_index, slide_part in enumerate(_slide_parts(package)):
        try:
            root = ET.fromstring(package[slide_part])
        except ET.ParseError:
            # One unreadable slide must not cost us the rest of the deck.
            continue
        heading = _slide_heading(root)
        slide_text = _slide_text(root)
        relationships = _relationships(package, slide_part)
        content_groups = _pptx_content_groups(root, document_digest, slide_index)
        for blip, media_part, coverage in _referenced_media_occurrences(
            root, relationships, package, slide_area
        ):
            if media_part not in measured:
                measured[media_part] = _measure(media_part, package[media_part])
            details = measured[media_part]
            if details is None:
                continue
            mime_type, width, height, digest = details
            extracted.append(
                ExtractedSourceImage(
                    payload=package[media_part],
                    mime_type=mime_type,
                    width=width,
                    height=height,
                    digest=digest,
                    source_slide_index=slide_index,
                    source_heading=heading,
                    source_text=slide_text,
                    slide_coverage=coverage,
                    content_group=content_groups.get(id(blip)),
                )
            )
    return extracted


def _content_group(
    document_digest: str,
    anchor: str,
    kind: str,
    text: Optional[str],
    caption: Optional[str] = None,
    order: int = 0,
) -> dict:
    """A deterministic relation to local source content, never a page-wide score."""

    return {
        "version": "source-content-group/v1",
        "groupId": sha256_hex(f"{document_digest}:{anchor}".encode()),
        "groupKind": kind,
        "groupText": " ".join((text or "").split())[:MAX_SOURCE_TEXT_CHARS],
        "captionText": " ".join((caption or "").split())[:MAX_SOURCE_TEXT_CHARS],
        "groupOrder": order,
    }


def _pptx_content_groups(root: ET.Element, digest: str, slide_index: int) -> dict[int, dict]:
    parents = {child: parent for parent in root.iter() for child in parent}
    shapes = list(root.iter(f"{{{NS['p']}}}sp"))
    nonempty_shapes = [shape for shape in shapes if _shape_text(shape)]
    plain_heading = (
        nonempty_shapes[0]
        if len(nonempty_shapes) > 1 and not any(_is_title_placeholder(shape) for shape in nonempty_shapes)
        else None
    )
    texts = [
        (index, shape, _shape_text(shape), _shape_bounds(shape, parents))
        for index, shape in enumerate(shapes)
        if _shape_text(shape) and not _is_title_placeholder(shape)
        and shape is not plain_heading
    ]
    groups = list(root.iter(f"{{{NS['p']}}}grpSp"))
    result = {}
    for order, blip in enumerate(root.iter(f"{{{NS['a']}}}blip")):
        ancestors = []
        node = blip
        while node in parents:
            node = parents[node]
            ancestors.append(node)
        shape = next((node for node in ancestors if node.tag in {f"{{{NS['p']}}}pic", f"{{{NS['p']}}}sp"}), None)
        group = next((node for node in ancestors if node.tag == f"{{{NS['p']}}}grpSp"), None)
        caption = ""
        if shape is not None:
            properties = shape.find(".//p:cNvPr", NS)
            if properties is not None:
                caption = properties.get("descr", "")
        group_members = set(group.iter()) if group is not None else set()
        grouped_texts = [item for item in texts if item[1] in group_members]
        if grouped_texts:
            anchor = f"slide/{slide_index}/group/{groups.index(group)}"
            text = " ".join(item[2] for item in grouped_texts)
            kind = "object_group"
        else:
            bounds = _shape_bounds(group if group is not None else shape, parents)
            nearest = _nearest_text(bounds, texts)
            if nearest is None:
                anchor, text, kind = f"slide/{slide_index}/image/{order}", "", "unresolved"
            else:
                anchor, text, kind = f"slide/{slide_index}/text/{nearest[0]}", nearest[2], "local_explanation"
        result[id(blip)] = _content_group(digest, anchor, kind, text, caption, order)
    return result


def _shape_bounds(shape: Optional[ET.Element], parents: dict) -> Optional[tuple[float, float, float, float]]:
    if shape is None:
        return None
    transform = shape.find("./p:spPr/a:xfrm", NS)
    if transform is None:
        transform = shape.find("./p:grpSpPr/a:xfrm", NS)
    if transform is None:
        return None
    if _unsupported_transform(transform):
        return None
    offset, extent = transform.find("a:off", NS), transform.find("a:ext", NS)
    if offset is None or extent is None:
        return None
    try:
        x, y = float(offset.attrib["x"]), float(offset.attrib["y"])
        width, height = float(extent.attrib["cx"]), float(extent.attrib["cy"])
        node = shape
        while node in parents:
            node = parents[node]
            if node.tag != f"{{{NS['p']}}}grpSp":
                continue
            group_transform = node.find("./p:grpSpPr/a:xfrm", NS)
            if group_transform is None:
                return None
            if _unsupported_transform(group_transform):
                return None
            off, ext = group_transform.find("a:off", NS), group_transform.find("a:ext", NS)
            child_off, child_ext = group_transform.find("a:chOff", NS), group_transform.find("a:chExt", NS)
            if off is None or ext is None or child_off is None or child_ext is None:
                return None
            scale_x = float(ext.attrib["cx"]) / float(child_ext.attrib["cx"])
            scale_y = float(ext.attrib["cy"]) / float(child_ext.attrib["cy"])
            x = float(off.attrib["x"]) + (x - float(child_off.attrib["x"])) * scale_x
            y = float(off.attrib["y"]) + (y - float(child_off.attrib["y"])) * scale_y
            width, height = width * scale_x, height * scale_y
        return x, y, x + width, y + height
    except (KeyError, ValueError, ZeroDivisionError):
        return None


def _unsupported_transform(transform: ET.Element) -> bool:
    try:
        if float(transform.get("rot", "0")) != 0:
            return True
    except ValueError:
        return True
    return any(transform.get(key, "false").lower() not in {"false", "0", "off"}
               for key in ("flipH", "flipV"))


def _nearest_text(bounds: Optional[tuple], texts: list) -> Optional[tuple]:
    if bounds is None:
        return None
    available = [item for item in texts if item[3] is not None]
    if not available:
        return None
    def distance(item: tuple) -> tuple:
        other = item[3]
        dx = max(other[0] - bounds[2], bounds[0] - other[2], 0)
        dy = max(other[1] - bounds[3], bounds[1] - other[3], 0)
        centers = abs(other[0] + other[2] - bounds[0] - bounds[2]) + abs(other[1] + other[3] - bounds[1] - bounds[3])
        return dx * dx + dy * dy, centers
    closest_distance = min(distance(item) for item in available)
    closest = [item for item in available if distance(item) == closest_distance]
    if len({item[2] for item in closest}) > 1:
        return None
    return closest[0]


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
    slide_area: Optional[int] = None,
) -> list[tuple[str, Optional[float]]]:
    """Media parts this slide embeds, in document order, with their coverage.

    Every ``a:blip`` counts, not only the ones inside ``p:pic``: a photo used as
    a shape fill is just as reusable as one dropped on the canvas. A target the
    package does not actually carry is a dangling relationship, not a picture.

    Coverage is how much of the slide the picture was placed over, which is what
    separates a backplate from an illustration. Only a ``p:pic`` states its own
    extent, so a blip found anywhere else reports ``None`` rather than a guess.
    """

    return [(part, coverage) for _, part, coverage in
            _referenced_media_occurrences(root, relationships, package, slide_area)]


def _referenced_media_occurrences(
    root: ET.Element,
    relationships: dict[str, tuple[str, str]],
    package: dict[str, bytes],
    slide_area: Optional[int] = None,
) -> list[tuple[ET.Element, str, Optional[float]]]:
    """Carry the original drawing with its media, preserving every occurrence."""

    coverage_by_blip = _picture_coverage(root, slide_area)
    parts: list[tuple[ET.Element, str, Optional[float]]] = []
    for blip in root.iter(f"{{{NS['a']}}}blip"):
        relation = relationships.get(blip.attrib.get(f"{{{NS['r']}}}embed") or "")
        if not relation or relation[1] not in package:
            continue
        # Keep one entry per drawing occurrence.  The ordinary authored registry
        # still deduplicates by digest, while source-content reflow needs two
        # shapes that reference the same media part to remain two placements.
        parts.append((blip, relation[1], coverage_by_blip.get(id(blip))))
    return parts


def _picture_coverage(
    root: ET.Element, slide_area: Optional[int]
) -> dict[int, float]:
    """Map each ``p:pic``'s blip to the share of the slide it was placed over."""

    if not slide_area:
        return {}
    coverage: dict[int, float] = {}
    for pic in root.iter(f"{{{NS['p']}}}pic"):
        blip = pic.find(f".//{{{NS['a']}}}blip")
        extent = pic.find(f".//{{{NS['a']}}}ext")
        if blip is None or extent is None:
            continue
        try:
            placed = int(extent.attrib["cx"]) * int(extent.attrib["cy"])
        except (KeyError, ValueError):
            continue
        coverage[id(blip)] = placed / slide_area
    return coverage


def _slide_area(package: dict[str, bytes]) -> Optional[int]:
    """Slide width x height in EMU, the denominator for picture coverage."""

    try:
        root = ET.fromstring(package["ppt/presentation.xml"])
    except (KeyError, ET.ParseError):
        return None
    size = root.find(f"{{{NS['p']}}}sldSz")
    if size is None:
        return None
    try:
        area = int(size.attrib["cx"]) * int(size.attrib["cy"])
    except (KeyError, ValueError):
        return None
    return area or None


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
    return declared, int(width), int(height), sha256_hex(payload)


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


def _slide_text(root: ET.Element) -> Optional[str]:
    """Everything the slide says, condensed into one matching hint.

    A deck's picture is identified by what its slide talks about. Titles are
    unreliable for that: proposal decks label whole sections ("02 · SOLUTION",
    "PROPOSAL MAP"), so every picture in a section would share one meaningless
    caption. Reading the body text costs nothing extra -- the XML is already
    parsed -- and it is the difference between a usable hint and none.
    """

    tree = root.find(".//p:spTree", NS)
    if tree is None:
        return None
    parts: list[str] = []
    for shape in tree.iter(f"{{{NS['p']}}}sp"):
        text = _shape_text(shape)
        if text:
            parts.append(text)
    joined = " ".join(" ".join(parts).split())
    if not joined:
        return None
    return joined[:MAX_SOURCE_TEXT_CHARS]


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
