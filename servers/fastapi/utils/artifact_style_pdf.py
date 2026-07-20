"""Offline PDF design-signal extraction using the locked pdfplumber dependency."""

from __future__ import annotations

from binascii import Error as BinasciiError
from collections import Counter, defaultdict
from hashlib import sha256
from io import BytesIO
import math
from pathlib import Path
import re
from typing import Any, Iterable
import zlib

import pdfplumber
from pdfminer.ascii85 import ascii85decode, asciihexdecode
from pdfminer.pdfdocument import PDFEncryptionError, PDFPasswordIncorrect
from pdfminer.pdftypes import PDFObjRef
from pdfminer.pdfparser import PDFParser, PDFSyntaxError
from pdfminer.psparser import PSEOF, PSSyntaxError, literal_name
from pdfplumber.utils.exceptions import PdfminerException

from utils.artifact_style_analysis import (
    MAX_ARTIFACT_BYTES,
    MAX_PAGES,
    ArtifactAnalysisError,
)


_SUBSET_FONT = re.compile(r"^[A-Z]{6}\+")
_PDF_NAME = re.compile(rb"/([^\x00\t\n\f\r ()<>\[\]{}/%]+)")
_PDF_NAME_ESCAPE = re.compile(rb"#([0-9A-Fa-f]{2})")
_STREAM_MARKER = re.compile(rb"\bstream(?:\r\n|\n|\r)")
_DICTIONARY_TOKEN = re.compile(rb"<<|>>")
MAX_PDF_EXPANDED_STREAM_BYTES = MAX_ARTIFACT_BYTES
MAX_PDF_TOTAL_EXPANDED_STREAM_BYTES = 4 * MAX_ARTIFACT_BYTES
MAX_PDF_STREAM_DICTIONARY_BYTES = 1024 * 1024


def _warning(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _component(value: Any) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    if number > 1:
        number /= 255
    return min(1.0, max(0.0, number))


def _pdf_color(value: Any) -> str | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        parts = [_component(value)]
    elif isinstance(value, (list, tuple)):
        parts = [_component(item) for item in value]
    else:
        return None
    if not parts or any(part is None for part in parts):
        return None
    channels = [float(part) for part in parts if part is not None]
    if len(channels) == 1:
        rgb = channels * 3
    elif len(channels) == 3:
        rgb = channels
    elif len(channels) == 4:
        cyan, magenta, yellow, black = channels
        rgb = [
            1 - min(1, cyan + black),
            1 - min(1, magenta + black),
            1 - min(1, yellow + black),
        ]
    else:
        return None
    return "#" + "".join(f"{round(channel * 255):02X}" for channel in rgb)


def _add_color(
    value: Any,
    role: str,
    colors: Counter[str],
    roles: dict[str, set[str]],
) -> None:
    candidate = _pdf_color(value)
    if candidate:
        colors[candidate] += 1
        roles[candidate].add(role)


def _decode_pdf_name(value: bytes) -> bytes:
    return _PDF_NAME_ESCAPE.sub(
        lambda match: bytes.fromhex(match.group(1).decode()), value
    )


def _pdf_name_counts(*payloads: bytes) -> Counter[bytes]:
    counts: Counter[bytes] = Counter()
    for payload in payloads:
        counts.update(
            _decode_pdf_name(match.group(1)) for match in _PDF_NAME.finditer(payload)
        )
    return counts


def _trailer_has_encryption(raw: bytes) -> bool:
    for trailer in re.finditer(rb"\btrailer\b", raw):
        end = raw.find(b"startxref", trailer.end())
        payload = raw[trailer.end() : end if end >= 0 else len(raw)]
        if _pdf_name_counts(payload)[b"Encrypt"]:
            return True
    return False


def _stream_dictionary(raw: bytes, marker_start: int) -> bytes | None:
    end = marker_start
    lower_bound = max(0, end - MAX_PDF_STREAM_DICTIONARY_BYTES)
    while end > lower_bound:
        while end > lower_bound and raw[end - 1] in b"\x00\t\n\f\r ":
            end -= 1
        line_start = (
            max(
                raw.rfind(b"\n", lower_bound, end),
                raw.rfind(b"\r", lower_bound, end),
            )
            + 1
        )
        comment = raw.rfind(b"%", line_start, end)
        if comment < 0:
            break
        end = comment
    if not raw[max(0, end - 2) : end] == b">>":
        return None
    lower_bound = max(0, end - MAX_PDF_STREAM_DICTIONARY_BYTES)
    depth = 0
    tokens = list(_DICTIONARY_TOKEN.finditer(raw, lower_bound, end))
    for token in reversed(tokens):
        if token.group() == b">>":
            depth += 1
        else:
            depth -= 1
            if depth == 0:
                return raw[token.start() : end]
    return None


def _parse_stream_dictionary(source: Path, dictionary: bytes) -> dict[str, Any]:
    parser = PDFParser(BytesIO(b"1 0 obj\n" + dictionary + b"\nendobj\n"))
    try:
        for _ in range(4):
            _, value = parser.nextobject()
            if isinstance(value, dict):
                return value
    except (PSEOF, PSSyntaxError, PDFSyntaxError, ValueError, TypeError) as exc:
        raise ArtifactAnalysisError(
            f"{source}: PDF stream dictionary cannot be safely parsed"
        ) from exc
    raise ArtifactAnalysisError(
        f"{source}: PDF stream dictionary cannot be safely parsed"
    )


def _stream_filters(source: Path, attributes: dict[str, Any]) -> tuple[bytes, ...]:
    value = attributes.get("Filter", attributes.get("F"))
    if value is None:
        return ()
    if isinstance(value, PDFObjRef):
        raise ArtifactAnalysisError(
            f"{source}: PDF stream uses an indirect filter declaration"
        )
    values = value if isinstance(value, list) else [value]
    filters: list[bytes] = []
    try:
        for item in values:
            if isinstance(item, PDFObjRef):
                raise ArtifactAnalysisError(
                    f"{source}: PDF stream uses an indirect filter declaration"
                )
            filters.append(literal_name(item).encode("latin-1"))
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise ArtifactAnalysisError(
            f"{source}: PDF stream uses a malformed filter declaration"
        ) from exc
    if not filters:
        raise ArtifactAnalysisError(
            f"{source}: PDF stream uses a malformed filter declaration"
        )
    return tuple(filters)


def _stream_length(source: Path, attributes: dict[str, Any]) -> int:
    value = attributes.get("Length", attributes.get("L"))
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ArtifactAnalysisError(
            f"{source}: PDF stream requires a direct non-negative integer length for safe preflight"
        )
    return value


def _bounded_flate(source: Path, payload: bytes) -> bytes:
    try:
        inflater = zlib.decompressobj()
        expanded = inflater.decompress(payload, MAX_PDF_EXPANDED_STREAM_BYTES + 1)
        if inflater.unconsumed_tail or len(expanded) > MAX_PDF_EXPANDED_STREAM_BYTES:
            raise ArtifactAnalysisError(
                f"{source}: PDF compressed stream exceeds the expanded-size safety limit"
            )
        expanded += inflater.flush(MAX_PDF_EXPANDED_STREAM_BYTES + 1 - len(expanded))
        if not inflater.eof:
            raise zlib.error("incomplete Flate stream")
        return expanded
    except zlib.error as exc:
        raise ArtifactAnalysisError(
            f"{source}: PDF compressed stream cannot be safely preflighted"
        ) from exc


def _bounded_run_length(source: Path, payload: bytes) -> bytes:
    expanded = bytearray()
    offset = 0
    while offset < len(payload):
        length = payload[offset]
        offset += 1
        if length == 128:
            return bytes(expanded)
        if length < 128:
            count = length + 1
            chunk = payload[offset : offset + count]
            if len(chunk) != count:
                break
            offset += count
        else:
            if offset >= len(payload):
                break
            count = 257 - length
            chunk = payload[offset : offset + 1] * count
            offset += 1
        if len(expanded) + len(chunk) > MAX_PDF_EXPANDED_STREAM_BYTES:
            raise ArtifactAnalysisError(
                f"{source}: PDF compressed stream exceeds the expanded-size safety limit"
            )
        expanded.extend(chunk)
    raise ArtifactAnalysisError(
        f"{source}: PDF RunLength stream cannot be safely preflighted"
    )


def _decode_filter(source: Path, name: bytes, payload: bytes) -> bytes:
    if name in {b"Fl", b"FlateDecode"}:
        return _bounded_flate(source, payload)
    if name in {b"A85", b"ASCII85Decode"}:
        return ascii85decode(payload)
    if name in {b"AHx", b"ASCIIHexDecode"}:
        return asciihexdecode(payload)
    if name in {b"RL", b"RunLengthDecode"}:
        return _bounded_run_length(source, payload)
    if name in {b"DCT", b"DCTDecode", b"JBIG2Decode", b"JPXDecode"}:
        return payload
    if name in {b"CCF", b"CCITTFaxDecode", b"LZW", b"LZWDecode"}:
        raise ArtifactAnalysisError(
            f"{source}: PDF filter /{name.decode()} is not supported by the bounded preflight"
        )
    raise ArtifactAnalysisError(
        f"{source}: PDF filter /{name.decode(errors='replace')} is unsupported"
    )


def _preflight_pdf_streams(source: Path, raw: bytes) -> Counter[bytes]:
    """Bound every directly declared stream pipeline before pdfminer opens it."""
    decoded_names: Counter[bytes] = Counter()
    total_expanded = 0
    position = 0
    while marker := _STREAM_MARKER.search(raw, position):
        dictionary = _stream_dictionary(raw, marker.start())
        if dictionary is None:
            raise ArtifactAnalysisError(
                f"{source}: PDF stream dictionary cannot be located within the safety limit"
            )
        if len(dictionary) >= MAX_PDF_STREAM_DICTIONARY_BYTES:
            raise ArtifactAnalysisError(
                f"{source}: PDF stream dictionary exceeds the safety limit"
            )
        attributes = _parse_stream_dictionary(source, dictionary)
        if "Encrypt" in attributes:
            raise ArtifactAnalysisError(
                f"{source}: encrypted PDF input is not supported"
            )
        filters = _stream_filters(source, attributes)
        length = _stream_length(source, attributes)
        payload_end = marker.end() + length
        if payload_end > len(raw):
            raise ArtifactAnalysisError(f"{source}: PDF stream length exceeds the file")
        payload = raw[marker.end() : payload_end]
        position = payload_end
        for filter_name in filters or ():
            try:
                payload = _decode_filter(source, filter_name, payload)
            except ArtifactAnalysisError:
                raise
            except (BinasciiError, ValueError) as exc:
                raise ArtifactAnalysisError(
                    f"{source}: PDF filter /{filter_name.decode(errors='replace')} cannot be safely preflighted"
                ) from exc
            if len(payload) > MAX_PDF_EXPANDED_STREAM_BYTES:
                raise ArtifactAnalysisError(
                    f"{source}: PDF compressed stream exceeds the expanded-size safety limit"
                )
            total_expanded += len(payload)
            if total_expanded > MAX_PDF_TOTAL_EXPANDED_STREAM_BYTES:
                raise ArtifactAnalysisError(
                    f"{source}: PDF compressed streams exceed the total expanded-size safety limit"
                )
        if filters:
            decoded_names.update(_pdf_name_counts(payload))
    return decoded_names


def _occupancy_cells(
    item: dict[str, Any], width: float, height: float, *, grid_size: int = 100
) -> set[int]:
    """Return deterministic canvas cells covered by one extracted bounding box."""
    if width <= 0 or height <= 0:
        return set()
    try:
        x0 = min(width, max(0.0, float(item.get("x0", 0))))
        x1 = min(width, max(x0, float(item.get("x1", x0))))
        top = min(height, max(0.0, float(item.get("top", 0))))
        bottom = min(height, max(top, float(item.get("bottom", top))))
    except (TypeError, ValueError):
        return set()
    if x1 <= x0 or bottom <= top:
        return set()
    left = min(grid_size - 1, int(x0 / width * grid_size))
    right = min(grid_size, max(left + 1, math.ceil(x1 / width * grid_size)))
    first_row = min(grid_size - 1, int(top / height * grid_size))
    last_row = min(
        grid_size, max(first_row + 1, math.ceil(bottom / height * grid_size))
    )
    return {
        row * grid_size + column
        for row in range(first_row, last_row)
        for column in range(left, right)
    }


def _region_key(item: dict[str, Any], width: float, height: float, kind: str) -> str:
    try:
        x = min(3, max(0, int((float(item.get("x0", 0)) / width) * 4)))
        y = min(3, max(0, int((float(item.get("top", 0)) / height) * 4)))
    except (TypeError, ValueError, ZeroDivisionError):
        x = y = 0
    return f"{kind}:{x},{y}"


def _layout_signature(
    words: Iterable[dict[str, Any]],
    images: Iterable[dict[str, Any]],
    vectors: Iterable[dict[str, Any]],
    width: float,
    height: float,
) -> str:
    regions: Counter[str] = Counter()
    for kind, items in (("text", words), ("image", images), ("shape", vectors)):
        for item in items:
            regions[_region_key(item, width, height, kind)] += 1
    payload = "|".join(
        f"{key}={min(count, 9)}" for key, count in sorted(regions.items())
    )
    return sha256(payload.encode("utf-8")).hexdigest()[:12]


def _signal(values: list[dict[str, Any]], confidence: str) -> dict[str, Any]:
    return {
        "confidence": confidence if values else "none",
        "status": "observed" if values else "unavailable",
        "values": values,
    }


def _open_pdf(source: Path, raw: bytes) -> Any:
    if not raw.startswith(b"%PDF-"):
        raise ArtifactAnalysisError(f"{source}: PDF header is missing or damaged")
    try:
        return pdfplumber.open(BytesIO(raw), password="")
    except PdfminerException as exc:
        cause = exc.args[0] if exc.args else exc
        if isinstance(cause, PDFPasswordIncorrect):
            raise ArtifactAnalysisError(
                f"{source}: encrypted PDF requires a password; password input is not supported"
            ) from exc
        if isinstance(cause, PDFEncryptionError):
            raise ArtifactAnalysisError(
                f"{source}: unsupported PDF encryption: {cause}"
            ) from exc
        raise ArtifactAnalysisError(
            f"{source}: damaged or unsupported PDF: {cause}"
        ) from exc
    except PDFPasswordIncorrect as exc:
        raise ArtifactAnalysisError(
            f"{source}: encrypted PDF requires a password; password input is not supported"
        ) from exc
    except PDFEncryptionError as exc:
        raise ArtifactAnalysisError(
            f"{source}: unsupported PDF encryption: {exc}"
        ) from exc
    except (PDFSyntaxError, ValueError, OSError) as exc:
        raise ArtifactAnalysisError(
            f"{source}: damaged or unsupported PDF: {exc}"
        ) from exc


def analyze_pdf(source: Path, raw: bytes) -> dict[str, Any]:
    if not raw.startswith(b"%PDF-"):
        raise ArtifactAnalysisError(f"{source}: PDF header is missing or damaged")
    serialized_names = _pdf_name_counts(raw)
    if _trailer_has_encryption(raw):
        raise ArtifactAnalysisError(f"{source}: encrypted PDF input is not supported")
    serialized_names.update(_preflight_pdf_streams(source, raw))
    active_content_counts = {
        "additional_actions": serialized_names[b"AA"],
        "javascript": serialized_names[b"JavaScript"] + serialized_names[b"JS"],
        "launch_actions": serialized_names[b"Launch"],
        "open_actions": serialized_names[b"OpenAction"],
        "rich_media": serialized_names[b"RichMedia"],
    }
    embedded_content_count = sum(
        serialized_names[name]
        for name in (b"EmbeddedFile", b"EmbeddedFiles", b"FileAttachment")
    )
    pdf = _open_pdf(source, raw)
    try:
        if getattr(pdf.doc, "encryption", None) is not None:
            raise ArtifactAnalysisError(
                f"{source}: encrypted PDF input is not supported"
            )
        if getattr(pdf.doc, "is_extractable", True) is False:
            raise ArtifactAnalysisError(
                f"{source}: PDF permissions prohibit extraction"
            )
        page_count = len(pdf.pages)
        if page_count == 0:
            raise ArtifactAnalysisError(f"{source}: PDF contains no pages")
        if page_count > MAX_PAGES:
            raise ArtifactAnalysisError(
                f"{source}: PDF exceeds the {MAX_PAGES}-page safety limit"
            )

        colors: Counter[str] = Counter()
        color_roles: dict[str, set[str]] = defaultdict(set)
        fonts: Counter[str] = Counter()
        sizes: Counter[float] = Counter()
        kind_counts: Counter[str] = Counter()
        occupied_cells: dict[str, set[tuple[int, int]]] = defaultdict(set)
        signatures: dict[str, list[int]] = defaultdict(list)
        page_sizes: Counter[tuple[float, float]] = Counter()
        text_character_count = 0
        external_link_count = 0
        total_page_area = 0.0

        for page_number, page in enumerate(pdf.pages, start=1):
            width = round(float(page.width), 3)
            height = round(float(page.height), 3)
            if (
                not math.isfinite(width)
                or not math.isfinite(height)
                or width <= 0
                or height <= 0
            ):
                raise ArtifactAnalysisError(
                    f"{source}: PDF page {page_number} has invalid dimensions"
                )
            page_sizes[(width, height)] += 1
            total_page_area += width * height
            chars = list(page.chars)
            images = list(page.images)
            rects = list(page.rects)
            curves = list(page.curves)
            lines = list(page.lines)
            vectors = [*rects, *curves, *lines]
            try:
                words = page.extract_words() or []
            except (TypeError, ValueError):
                words = []
            try:
                external_link_count += len(page.hyperlinks)
            except (TypeError, ValueError, KeyError):
                pass
            signature = _layout_signature(words, images, vectors, width, height)
            signatures[signature].append(page_number)

            text_character_count += len(chars)
            kind_counts["text"] += len(words)
            kind_counts["image"] += len(images)
            kind_counts["shape"] += len(vectors)
            for kind, items in (
                ("text", words),
                ("image", images),
                ("shape", vectors),
            ):
                for item in items:
                    occupied_cells[kind].update(
                        (page_number, cell)
                        for cell in _occupancy_cells(item, width, height)
                    )

            for char in chars:
                family = _SUBSET_FONT.sub("", str(char.get("fontname", "")).strip())
                if family:
                    fonts[family] += 1
                size = char.get("size")
                if isinstance(size, (int, float)) and math.isfinite(float(size)):
                    sizes[round(float(size), 1)] += 1
                _add_color(
                    char.get("non_stroking_color"), "text-fill", colors, color_roles
                )
                _add_color(
                    char.get("stroking_color"), "text-stroke", colors, color_roles
                )
            for item in rects:
                _add_color(
                    item.get("non_stroking_color"), "shape-fill", colors, color_roles
                )
                _add_color(
                    item.get("stroking_color"), "shape-stroke", colors, color_roles
                )
            for item in [*curves, *lines]:
                _add_color(
                    item.get("stroking_color"), "vector-stroke", colors, color_roles
                )

        color_values = [
            {"count": count, "roles": sorted(color_roles[value]), "value": value}
            for value, count in sorted(
                colors.items(), key=lambda item: (-item[1], item[0])
            )[:12]
        ]
        font_values = [
            {"count": count, "family": family, "sources": ["pdf-text"]}
            for family, count in sorted(
                fonts.items(), key=lambda item: (-item[1], item[0].casefold(), item[0])
            )[:12]
        ]
        hierarchy_values = [
            {"count": count, "role": "observed-size", "size_pt": size}
            for size, count in sorted(
                sizes.items(), key=lambda item: (-item[0], -item[1])
            )[:12]
        ]
        repeated_values = [
            {"count": len(pages), "signature": signature, "pages": pages}
            for signature, pages in sorted(
                signatures.items(), key=lambda item: (-len(item[1]), item[0])
            )
            if len(pages) >= 2
        ]
        total_occupied_cells = sum(len(cells) for cells in occupied_cells.values())
        area_share = {
            kind: round(len(cells) / total_occupied_cells, 4)
            for kind, cells in sorted(occupied_cells.items())
            if total_occupied_cells
        }
        canvas_coverage = {
            kind: round(len(cells) / (page_count * 10_000), 4)
            for kind, cells in sorted(occupied_cells.items())
        }
        primary_size, _ = sorted(
            page_sizes.items(), key=lambda item: (-item[1], item[0])
        )[0]
        warnings: list[dict[str, str]] = []
        if external_link_count:
            warnings.append(
                _warning(
                    "pdf_external_links_present",
                    f"{external_link_count} hyperlink annotation(s) were detected and not followed.",
                )
            )
        active_content_count = sum(active_content_counts.values())
        if active_content_count:
            warnings.append(
                _warning(
                    "pdf_active_content_present",
                    f"{active_content_count} serialized action/JavaScript/RichMedia marker(s) were detected and not executed.",
                )
            )
        if embedded_content_count:
            warnings.append(
                _warning(
                    "pdf_embedded_content_present",
                    f"{embedded_content_count} serialized attachment/embedded-file marker(s) were detected and not opened.",
                )
            )
        image_coverage = canvas_coverage.get("image", 0.0)
        if text_character_count == 0 and image_coverage >= 0.5:
            warnings.append(
                _warning(
                    "likely_scanned_pdf",
                    "No extractable text was found and images cover at least half of the page canvas; OCR is intentionally not performed.",
                )
            )
        elif text_character_count == 0 and kind_counts["image"]:
            warnings.append(
                _warning(
                    "pdf_text_unavailable_with_images",
                    "No extractable text was found and page images were present, but evidence is insufficient to classify the PDF as scanned.",
                )
            )
        elif text_character_count == 0:
            warnings.append(
                _warning(
                    "pdf_text_unavailable", "No extractable text was found in the PDF."
                )
            )
        if not color_values:
            warnings.append(
                _warning(
                    "colors_unavailable",
                    "No supported RGB, grayscale, or CMYK colors were extractable.",
                )
            )
        if not font_values:
            warnings.append(
                _warning(
                    "fonts_unavailable", "No embedded text font names were extractable."
                )
            )
        if not hierarchy_values:
            warnings.append(
                _warning(
                    "text_hierarchy_unavailable", "No text sizes were extractable."
                )
            )
        if not repeated_values:
            warnings.append(
                _warning(
                    "repeated_layouts_unavailable",
                    "No repeated coarse page layout was observed.",
                )
            )
        object_count = sum(kind_counts.values())
        if object_count == 0:
            warnings.append(
                _warning(
                    "pdf_design_objects_unavailable",
                    "No extractable text, image, or vector design objects were found.",
                )
            )
        if len(page_sizes) > 1:
            warnings.append(
                _warning(
                    "mixed_page_sizes",
                    "Multiple PDF page sizes were observed; the most common size is reported as primary.",
                )
            )

        return {
            "document": {
                "mixed_page_size_count": len(page_sizes),
                "page_count": page_count,
                "page_size": {
                    "aspect_ratio": round(primary_size[0] / primary_size[1], 6),
                    "height": primary_size[1],
                    "unit": "points",
                    "width": primary_size[0],
                },
                "page_sizes": [
                    {"count": count, "height": size[1], "width": size[0]}
                    for size, count in sorted(
                        page_sizes.items(), key=lambda item: (-item[1], item[0])
                    )
                ],
            },
            "evidence": [
                {
                    "method": "PDF graphics and text object color operands",
                    "observations": len(color_values),
                    "signal": "colors",
                },
                {
                    "method": "PDF character font names",
                    "observations": len(font_values),
                    "signal": "fonts",
                },
                {
                    "method": "PDF character sizes",
                    "observations": text_character_count,
                    "signal": "text_hierarchy",
                },
                {
                    "method": "coarse normalized word/image/vector geometry",
                    "observations": len(repeated_values),
                    "signal": "repeated_layouts",
                },
            ],
            "security": {
                "active_content_counts": active_content_counts,
                "encrypted": False,
                "embedded_content_marker_count": embedded_content_count,
                "external_link_count": external_link_count,
                "marker_detection": "Conservative PDF name-token scan across serialized bytes and bounded decoded streams; counts can include inactive references.",
                "policy": "JavaScript, actions, external links, attachments, and embedded content were not executed.",
            },
            "signals": {
                "colors": _signal(
                    color_values, "high" if len(color_values) >= 3 else "medium"
                ),
                "composition": {
                    "canvas_coverage": canvas_coverage,
                    "confidence": "medium" if object_count else "none",
                    "element_area_share": area_share,
                    "element_counts": {
                        key: count
                        for key, count in sorted(kind_counts.items())
                        if count
                    },
                    "note": "Shares use deterministic 100x100 per-kind canvas occupancy; cross-kind overlaps remain attributable to each kind.",
                    "status": "observed" if object_count else "unavailable",
                },
                "fonts": _signal(font_values, "high" if font_values else "none"),
                "repeated_layouts": _signal(repeated_values, "medium"),
                "text_hierarchy": _signal(
                    hierarchy_values, "high" if hierarchy_values else "none"
                ),
            },
            "warnings": warnings,
        }
    except ArtifactAnalysisError:
        raise
    except (
        PdfminerException,
        PDFEncryptionError,
        PDFPasswordIncorrect,
        PDFSyntaxError,
        ValueError,
        TypeError,
        KeyError,
        OSError,
        OverflowError,
        ZeroDivisionError,
    ) as exc:
        raise ArtifactAnalysisError(
            f"{source}: damaged or unsupported PDF: {exc}"
        ) from exc
    finally:
        pdf.close()
