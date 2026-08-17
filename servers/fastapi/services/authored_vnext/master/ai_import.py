"""Deterministic, local-only PPTX analysis for authored master drafts.

The extractor intentionally reads OOXML directly.  Meaningful labels are
heuristics and every such result carries evidence; no document bytes leave the
process and the deterministic result remains usable without an AI provider.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
from typing import Callable, Iterable
from uuid import uuid4
import zipfile
from io import BytesIO
from xml.etree import ElementTree as ET

from models.authored_vnext.master.ai_import import (
    AI_MASTER_FEATURE_ALGORITHM_VERSION,
    AiAnalysisSource,
    AiAnalysisSummary,
    AiCandidateDecision,
    AiCandidateElement,
    AiColorEvidence,
    AiElementEvidence,
    AiFontEvidence,
    AiFeatureSidecar,
    AiElementFeature,
    AiLayoutAction,
    AiLayoutCandidate,
    AiLayoutSlotEvidence,
    AiMasterAnalysis,
    AiReapplyIssue,
    AiReapplyValidation,
    AiSourceAsset,
    AiSourceRelationship,
    AiSlideFeature,
)
from models.authored_vnext.master.contract import DeckMasterContract
from services.authored_vnext.master.catalog_seed import _base_contract


NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pr": "http://schemas.openxmlformats.org/package/2006/relationships",
}
EMU_PER_INCH = 914400
PX_PER_INCH = 96
MAX_PPTX_BYTES = 50_000_000
MAX_EXPANDED_BYTES = 300_000_000
MAX_ZIP_MEMBERS = 20_000
GEOMETRY_TOLERANCE_RATIO = 0.0075
ROTATION_TOLERANCE_DEGREES = 1.0
FONT_SIZE_TOLERANCE_PT = 1.0
LAYOUT_VECTOR_TOLERANCE = 0.18
REPEAT_MIN_COVERAGE = 0.45


class AiImportError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _digest(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _part_rels(part: str) -> str:
    path = PurePosixPath(part)
    return str(path.parent / "_rels" / f"{path.name}.rels")


def _target_part(source_part: str, target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    return posixpath.normpath(posixpath.join(posixpath.dirname(source_part), target))


def _rels(package: dict[str, bytes], part: str) -> dict[str, tuple[str, str]]:
    rel_part = _part_rels(part)
    if rel_part not in package:
        return {}
    root = ET.fromstring(package[rel_part])
    return {
        node.attrib["Id"]: (
            node.attrib.get("Type", "").rsplit("/", 1)[-1],
            _target_part(part, node.attrib.get("Target", "")),
        )
        for node in root
        if node.attrib.get("Id") and node.attrib.get("TargetMode") != "External"
    }


def _read_package(value: bytes) -> dict[str, bytes]:
    if not value or len(value) > MAX_PPTX_BYTES:
        raise AiImportError("authored_vnext_ai_master_file_size_invalid")
    if not value.startswith(b"PK"):
        raise AiImportError("authored_vnext_ai_master_pptx_required")
    try:
        with zipfile.ZipFile(BytesIO(value)) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ZIP_MEMBERS:
                raise AiImportError("authored_vnext_ai_master_package_too_large")
            if sum(item.file_size for item in infos) > MAX_EXPANDED_BYTES:
                raise AiImportError("authored_vnext_ai_master_package_too_large")
            package: dict[str, bytes] = {}
            for item in infos:
                normalized = posixpath.normpath(item.filename.replace("\\", "/"))
                if normalized.startswith("../") or normalized.startswith("/"):
                    raise AiImportError("authored_vnext_ai_master_package_path_invalid")
                if not item.is_dir():
                    package[normalized] = archive.read(item)
    except (zipfile.BadZipFile, ET.ParseError) as error:
        raise AiImportError("authored_vnext_ai_master_pptx_invalid") from error
    if "ppt/presentation.xml" not in package:
        raise AiImportError("authored_vnext_ai_master_pptx_invalid")
    return package


def _theme_values(package: dict[str, bytes], theme_part: str | None) -> tuple[list[str], list[str], dict[str, str]]:
    if not theme_part or theme_part not in package:
        return [], [], {}
    root = ET.fromstring(package[theme_part])
    fonts: list[str] = []
    for node in root.findall(".//a:latin", NS) + root.findall(".//a:ea", NS) + root.findall(".//a:cs", NS):
        value = node.attrib.get("typeface", "").strip()
        if value and not value.startswith("+"):
            fonts.append(value)
    colors: list[str] = []
    mapping: dict[str, str] = {}
    scheme = root.find(".//a:clrScheme", NS)
    if scheme is not None:
        for child in list(scheme):
            value_node = next(iter(list(child)), None)
            if value_node is None:
                continue
            value = value_node.attrib.get("val") or value_node.attrib.get("lastClr")
            if value and re.fullmatch(r"[0-9A-Fa-f]{6}", value):
                normalized = f"#{value.upper()}"
                colors.append(normalized)
                mapping[child.tag.rsplit("}", 1)[-1]] = normalized
    return fonts, colors, mapping


def _color(node: ET.Element, theme_colors: dict[str, str]) -> str | None:
    srgb = node.find(".//a:srgbClr", NS)
    if srgb is not None and re.fullmatch(r"[0-9A-Fa-f]{6}", srgb.attrib.get("val", "")):
        return f"#{srgb.attrib['val'].upper()}"
    scheme = node.find(".//a:schemeClr", NS)
    if scheme is not None:
        return theme_colors.get(scheme.attrib.get("val", ""))
    return None


def _rect(node: ET.Element, width_px: int, height_px: int) -> dict[str, float]:
    xfrm = node.find(".//a:xfrm", NS)
    if xfrm is None:
        xfrm = node.find(".//p:xfrm", NS)
    off = xfrm.find("a:off", NS) if xfrm is not None else None
    ext = xfrm.find("a:ext", NS) if xfrm is not None else None
    scale = PX_PER_INCH / EMU_PER_INCH
    x = float(off.attrib.get("x", 0)) * scale if off is not None else 0.0
    y = float(off.attrib.get("y", 0)) * scale if off is not None else 0.0
    width = float(ext.attrib.get("cx", EMU_PER_INCH)) * scale if ext is not None else 1.0
    height = float(ext.attrib.get("cy", EMU_PER_INCH)) * scale if ext is not None else 1.0
    x = max(0.0, min(x, width_px - 1))
    y = max(0.0, min(y, height_px - 1))
    return {
        "x": round(x, 3),
        "y": round(y, 3),
        "width": round(max(1.0, min(width, width_px - x)), 3),
        "height": round(max(1.0, min(height, height_px - y)), 3),
    }


def _rotation(node: ET.Element) -> float:
    xfrm = node.find(".//a:xfrm", NS)
    if xfrm is None:
        xfrm = node.find(".//p:xfrm", NS)
    return round(float(xfrm.attrib.get("rot", 0)) / 60000.0, 3) if xfrm is not None else 0.0


def _crop(node: ET.Element) -> dict[str, float] | None:
    source = node.find(".//a:srcRect", NS)
    if source is None:
        return None
    left = max(0.0, min(1.0, float(source.attrib.get("l", 0)) / 100000.0))
    top = max(0.0, min(1.0, float(source.attrib.get("t", 0)) / 100000.0))
    right = max(0.0, min(1.0, float(source.attrib.get("r", 0)) / 100000.0))
    bottom = max(0.0, min(1.0, float(source.attrib.get("b", 0)) / 100000.0))
    width = max(0.000001, 1.0 - left - right)
    height = max(0.000001, 1.0 - top - bottom)
    return {"x": left, "y": top, "width": width, "height": height}


def _placeholder(node: ET.Element) -> tuple[str | None, int | None]:
    ph = node.find(".//p:nvPr/p:ph", NS)
    if ph is None:
        return None, None
    index = ph.attrib.get("idx")
    return ph.attrib.get("type", "body"), int(index) if index and index.isdigit() else None


def _text_style(node: ET.Element, theme_colors: dict[str, str]) -> tuple[str | None, float | None, str | None]:
    font: str | None = None
    size: float | None = None
    color: str | None = None
    for props in node.findall(".//a:rPr", NS) + node.findall(".//a:defRPr", NS) + node.findall(".//a:endParaRPr", NS):
        latin = props.find("a:latin", NS)
        east_asian = props.find("a:ea", NS)
        font_node = east_asian if east_asian is not None else latin
        if font_node is not None and font_node.attrib.get("typeface"):
            candidate = font_node.attrib["typeface"]
            if not candidate.startswith("+"):
                font = candidate
        if props.attrib.get("sz", "").isdigit():
            size = int(props.attrib["sz"]) / 100.0
        color = _color(props, theme_colors) or color
    return font, size, color


def _slide_parts(package: dict[str, bytes]) -> list[str]:
    root = ET.fromstring(package["ppt/presentation.xml"])
    relationships = _rels(package, "ppt/presentation.xml")
    result: list[str] = []
    for slide in root.findall(".//p:sldId", NS):
        rel_id = slide.attrib.get(f"{{{NS['r']}}}id")
        relation = relationships.get(rel_id or "")
        if relation and relation[0] == "slide" and relation[1] in package:
            result.append(relation[1])
    return result


def _canvas(package: dict[str, bytes]) -> tuple[int, int]:
    root = ET.fromstring(package["ppt/presentation.xml"])
    size = root.find("p:sldSz", NS)
    if size is None:
        return 1280, 720
    scale = PX_PER_INCH / EMU_PER_INCH
    return max(320, round(int(size.attrib["cx"]) * scale)), max(180, round(int(size.attrib["cy"]) * scale))


def _media_type(part: str) -> str:
    suffix = PurePosixPath(part).suffix.lower()
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".svg": "image/svg+xml",
        ".gif": "image/gif",
        ".emf": "image/x-emf",
        ".wmf": "image/x-wmf",
    }.get(suffix, "application/octet-stream")


def _role(element: dict[str, object]) -> str:
    text = str(element.get("text") or "").strip()
    lowered = text.lower()
    if element["type"] == "image":
        return "logo"
    if element["type"] == "shape" and float(element["bounds"]["width"]) > 900:
        return "background"
    if re.fullmatch(r"(?:page\s*)?\d+", lowered):
        return "pageNumber"
    if re.search(r"\brfp\b", lowered):
        return "rfpNumber"
    if re.search(r"confidential|대외비|보안|security", lowered):
        return "securityLevel"
    if re.search(r"문서\s*(?:코드|번호)|document\s*(?:code|no)", lowered):
        return "documentCode"
    if re.search(r"고객|customer|client", lowered):
        return "customerName"
    return "repeatedText" if element["type"] == "text" else "decoration"


def _normalized_content(element: dict[str, object]) -> str:
    if element["type"] == "image":
        return str(element.get("digest") or "")
    text = " ".join(str(element.get("text") or "").split()).lower()
    if re.fullmatch(r"(?:page\s*)?\d+", text):
        return "<page-number>"
    return text


def _element_key(
    element: dict[str, object],
    width: int,
    height: int,
    geometry_tolerance_ratio: float = GEOMETRY_TOLERANCE_RATIO,
) -> tuple[object, ...]:
    bounds = element["bounds"]
    x_tol = max(1.0, width * geometry_tolerance_ratio)
    y_tol = max(1.0, height * geometry_tolerance_ratio)
    return (
        element["type"],
        _normalized_content(element),
        round(float(bounds["x"]) / x_tol),
        round(float(bounds["y"]) / y_tol),
        round(float(bounds["width"]) / x_tol),
        round(float(bounds["height"]) / y_tol),
        round(float(element.get("rotation") or 0) / ROTATION_TOLERANCE_DEGREES),
    )


def _slide_elements(
    package: dict[str, bytes],
    slide_part: str,
    slide_number: int,
    width: int,
    height: int,
    theme_colors: dict[str, str],
    published_assets: dict[str, dict[str, object]],
) -> tuple[list[dict[str, object]], list[AiSourceRelationship]]:
    root = ET.fromstring(package[slide_part])
    relationships = _rels(package, slide_part)
    layout_part = next((target for kind, target in relationships.values() if kind == "slideLayout"), None)
    layout_relationships = _rels(package, layout_part) if layout_part else {}
    master_part = next((target for kind, target in layout_relationships.values() if kind == "slideMaster"), None)
    master_relationships = _rels(package, master_part) if master_part else {}
    theme_part = next((target for kind, target in master_relationships.values() if kind == "theme"), None)
    tree = root.find(".//p:spTree", NS)
    elements: list[dict[str, object]] = []
    source_relationships: list[AiSourceRelationship] = []
    if tree is None:
        return elements, source_relationships
    z_index = 0
    for node in list(tree):
        kind = node.tag.rsplit("}", 1)[-1]
        if kind not in {"sp", "pic", "graphicFrame", "cxnSp"}:
            continue
        bounds = _rect(node, width, height)
        placeholder_type, placeholder_index = _placeholder(node)
        text = "".join(item.text or "" for item in node.findall(".//a:t", NS)).strip() or None
        font_family, font_size, text_color = _text_style(node, theme_colors)
        element_type = "text" if text is not None else "shape"
        asset_id = digest = media_type = None
        crop = None
        fit = None
        relationship_id = None
        if kind == "pic":
            element_type = "image"
            blip = node.find(".//a:blip", NS)
            relationship_id = blip.attrib.get(f"{{{NS['r']}}}embed") if blip is not None else None
            relation = relationships.get(relationship_id or "")
            if relation and relation[1] in package:
                media = package[relation[1]]
                digest = _digest(media)
                receipt = published_assets.get(digest)
                asset_id = str(receipt.get("assetId")) if receipt else f"asset:{digest[7:39]}"
                media_type = _media_type(relation[1])
                crop = _crop(node)
                fit = "cover" if crop else "contain"
                source_relationships.append(
                    AiSourceRelationship(
                        slide_number=slide_number,
                        relationship_id=relationship_id or "unknown",
                        asset_id=asset_id,
                        crop=crop,
                        fit=fit,
                    )
                )
        fill = _color(node.find(".//p:spPr", NS) or node, theme_colors)
        elements.append(
            {
                "type": element_type,
                "text": text,
                "assetId": asset_id,
                "digest": digest,
                "mediaType": media_type,
                "crop": crop,
                "fit": fit,
                "bounds": bounds,
                "rotation": _rotation(node),
                "zIndex": z_index,
                "fontFamily": font_family,
                "fontSize": font_size,
                "color": text_color,
                "fill": fill,
                "placeholderType": placeholder_type,
                "placeholderIndex": placeholder_index,
                "layoutPart": layout_part,
                "masterPart": master_part,
                "themePart": theme_part,
            }
        )
        z_index += 1
    return elements, source_relationships


def _design_family_key(slide: dict[str, object], width: int, height: int) -> str:
    backgrounds = [
        item
        for item in slide["elements"]
        if item["type"] == "shape"
        and float(item["bounds"]["width"])
        * float(item["bounds"]["height"])
        / (width * height)
        >= 0.6
    ]
    background = min(backgrounds, key=lambda item: int(item.get("zIndex") or 0), default=None)
    material = "|".join(
        [
            str(slide.get("masterPart") or "none"),
            str(slide.get("themePart") or "none"),
            str(slide.get("layoutPart") or "none"),
            str(background.get("fill") if background else "none"),
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def _furniture_eligible(
    element: dict[str, object], width: int, height: int
) -> bool:
    bounds = element["bounds"]
    area = float(bounds["width"]) * float(bounds["height"]) / (width * height)
    role = _role(element)
    if element["type"] == "image":
        return bool(element.get("digest"))
    if element["type"] == "shape":
        near_edge = (
            float(bounds["y"]) / height >= 0.88
            or float(bounds["y"]) / height <= 0.04
        )
        return area >= 0.6 or (near_edge and area <= 0.15)
    if role != "repeatedText":
        return True
    # Exact content alone must not turn a recurring title/body placement into
    # furniture.  Generic text qualifies only as small edge chrome.
    small = float(element.get("fontSize") or 0) <= 18
    edge = (
        float(bounds["y"]) / height >= 0.82
        or float(bounds["y"]) / height <= 0.08
    )
    return small and edge and bool(_normalized_content(element))


def _furniture_match_key(
    element: dict[str, object],
    width: int,
    height: int,
    geometry_tolerance_ratio: float,
) -> tuple[object, ...]:
    return (
        *_element_key(element, width, height, geometry_tolerance_ratio),
        _role(element),
        str(element.get("fontFamily") or "").lower(),
        round(float(element.get("fontSize") or 0) / FONT_SIZE_TOLERANCE_PT),
        str(element.get("color") or ""),
        str(element.get("fill") or ""),
    )


def _layout_furniture_keys(
    slides: list[dict[str, object]],
    width: int,
    height: int,
    geometry_tolerance_ratio: float,
) -> dict[int, set[tuple[object, ...]]]:
    """Identify global and design-family scoped exact furniture.

    Content/image digest, inferred role, geometry and style all participate in
    the match.  This protects variable title/body regions even when their
    placement is shared by every member of a layout family.
    """

    global_occurrences: dict[tuple[object, ...], set[int]] = defaultdict(set)
    family_occurrences: dict[
        tuple[str, tuple[object, ...]], set[int]
    ] = defaultdict(set)
    family_members: dict[str, set[int]] = defaultdict(set)
    element_keys: dict[
        tuple[int, tuple[object, ...]], tuple[object, ...]
    ] = {}
    for slide in slides:
        slide_number = int(slide["slideNumber"])
        family = _design_family_key(slide, width, height)
        family_members[family].add(slide_number)
        for element in slide["elements"]:
            if (
                element["type"] not in {"text", "image", "shape"}
                or not _furniture_eligible(element, width, height)
            ):
                continue
            match_key = _furniture_match_key(
                element, width, height, geometry_tolerance_ratio
            )
            global_occurrences[match_key].add(slide_number)
            family_occurrences[(family, match_key)].add(slide_number)
            element_keys[(slide_number, match_key)] = _element_key(
                element, width, height, geometry_tolerance_ratio
            )
    global_threshold = max(3, math.ceil(len(slides) * 0.55))
    global_repeats = {
        key
        for key, numbers in global_occurrences.items()
        if len(numbers) >= global_threshold
    }
    scoped: dict[int, set[tuple[object, ...]]] = {
        int(slide["slideNumber"]): set() for slide in slides
    }
    for (family, match_key), numbers in family_occurrences.items():
        family_threshold = max(2, math.ceil(len(family_members[family]) * 0.55))
        if match_key not in global_repeats and len(numbers) < family_threshold:
            continue
        target_numbers = global_occurrences[match_key] if match_key in global_repeats else numbers
        for slide_number in target_numbers:
            scoped[slide_number].add(element_keys[(slide_number, match_key)])
    return scoped


def _normalized_bounds(
    element: dict[str, object], width: int, height: int
) -> tuple[float, float, float, float]:
    bounds = element["bounds"]
    return (
        float(bounds["x"]) / width,
        float(bounds["y"]) / height,
        float(bounds["width"]) / width,
        float(bounds["height"]) / height,
    )


def _layout_feature(
    slide: dict[str, object],
    width: int,
    height: int,
    furniture_keys: set[tuple[object, ...]],
    geometry_tolerance_ratio: float,
    total_slides: int,
) -> dict[str, object]:
    all_elements = list(slide["elements"])
    content = [
        item
        for item in all_elements
        if _element_key(item, width, height, geometry_tolerance_ratio)
        not in furniture_keys
        and (
            item["type"] == "text"
            or (
                float(item["bounds"]["width"])
                * float(item["bounds"]["height"])
                / (width * height)
                < 0.9
            )
        )
    ]
    texts = [item for item in content if item["type"] == "text" and item.get("text")]
    images = [item for item in content if item["type"] == "image"]
    shapes = [item for item in content if item["type"] == "shape"]
    top_texts = [
        item
        for item in texts
        if float(item["bounds"]["y"]) / height < 0.36
    ] or texts
    headline = max(
        top_texts,
        key=lambda item: (
            float(item.get("fontSize") or 0)
            * math.sqrt(
                float(item["bounds"]["width"])
                * float(item["bounds"]["height"])
                / (width * height)
            ),
            float(item["bounds"]["width"]),
            -float(item["bounds"]["y"]),
        ),
        default=None,
    )
    headline_font = float(headline.get("fontSize") or 0) if headline else 0.0
    headline_bounds = (
        _normalized_bounds(headline, width, height)
        if headline is not None
        else (0.0, 0.0, 0.0, 0.0)
    )

    # Three type-specific occupancy maps retain card/flow/table/dashboard
    # arrangement without depending on the number of tiny drawing fragments.
    occupancy: list[float] = []
    for element_type in ("text", "image", "shape"):
        cells = [0.0] * 12
        for item in content:
            if item["type"] != element_type:
                continue
            x, y, item_width, item_height = _normalized_bounds(item, width, height)
            center_x = min(0.999, x + item_width / 2)
            center_y = min(0.999, y + item_height / 2)
            column = min(3, int(center_x * 4))
            row = min(2, int(center_y * 3))
            area_weight = min(1.0, math.sqrt(max(0.0, item_width * item_height)) * 2.4)
            cells[row * 4 + column] = min(
                1.0, cells[row * 4 + column] + area_weight
            )
        occupancy.extend(cells)

    area_histogram: list[float] = []
    for element_type in ("text", "image", "shape"):
        bins = [0, 0, 0, 0]
        for item in content:
            if item["type"] != element_type:
                continue
            _, _, item_width, item_height = _normalized_bounds(item, width, height)
            area = item_width * item_height
            bucket = 0 if area < 0.002 else 1 if area < 0.012 else 2 if area < 0.06 else 3
            bins[bucket] += 1
        denominator = max(1, sum(bins))
        area_histogram.extend(min(1.0, value / denominator) for value in bins)

    # The largest regions preserve relative size, aspect, reading order and
    # z-order. Quantization absorbs sub-pixel and authoring-tool drift.
    large_regions = sorted(
        content,
        key=lambda item: (
            -float(item["bounds"]["width"]) * float(item["bounds"]["height"]),
            float(item["bounds"]["y"]),
            float(item["bounds"]["x"]),
            int(item.get("zIndex") or 0),
        ),
    )[:8]
    region_vector: list[float] = []
    for index in range(8):
        if index >= len(large_regions):
            region_vector.extend([0.0] * 8)
            continue
        item = large_regions[index]
        x, y, item_width, item_height = _normalized_bounds(item, width, height)
        type_code = {"text": 0.25, "image": 0.6, "shape": 1.0}.get(
            str(item["type"]), 0.0
        )
        aspect = item_width / max(item_height, 0.001)
        region_vector.extend(
            [
                type_code,
                round((x + item_width / 2) / 0.02) * 0.02,
                round((y + item_height / 2) / 0.02) * 0.02,
                round(item_width / 0.025) * 0.025,
                round(item_height / 0.025) * 0.025,
                min(1.0, abs(math.log(max(aspect, 0.05))) / 4),
                min(1.0, int(item.get("zIndex") or 0) / max(1, len(all_elements))),
                min(1.0, float(item.get("fontSize") or 0) / 48),
            ]
        )

    all_text = " ".join(str(item.get("text") or "") for item in texts).lower()
    slide_number = int(slide["slideNumber"])
    cover_score = (
        (0.75 if headline_font >= 30 else 0.0)
        + (0.65 if slide_number == 1 else 0.0)
        + (0.25 if re.search(r"제안|proposal|presentation", all_text) else 0.0)
        + (
            0.75
            if slide_number == 1
            and (
                headline_bounds[1] >= 0.14
                or headline_font >= 26
                or len(texts) <= 6
            )
            else 0.0
        )
    )
    closing_score = (
        (
            1.7
            if re.search(
                r"감사합니다|thank\s*you|contact\s*us|문의처|마무리|closing",
                all_text,
            )
            else 0.0
        )
        + (
            0.85
            if slide_number >= math.ceil(total_slides * 0.85)
            and re.search(r"최종|보증|결론|완결", all_text)
            else 0.0
        )
        + (0.8 if slide_number == total_slides else 0.0)
    )
    sparse_score = (
        (1.0 if len(texts) <= 8 else 0.0)
        + (0.7 if headline_font >= 27 else 0.0)
        + (0.35 if headline_bounds[0] > 0.16 else 0.0)
    )
    if cover_score >= 1.5 and cover_score > closing_score:
        semantic_hint = "cover"
    elif closing_score >= 1.5:
        semantic_hint = "closing"
    elif sparse_score >= 1.35:
        semantic_hint = "section"
    else:
        semantic_hint = "body"

    vector = (
        round(headline_bounds[0] / 0.02) * 0.02,
        round(headline_bounds[1] / 0.02) * 0.02,
        round(headline_bounds[2] / 0.025) * 0.025,
        round(headline_bounds[3] / 0.025) * 0.025,
        min(1.0, headline_font / 48),
        min(1.0, math.log1p(len(texts)) / math.log(101)),
        min(1.0, math.log1p(len(images)) / math.log(9)),
        min(1.0, math.log1p(len(shapes)) / math.log(101)),
        *occupancy,
        *area_histogram,
        *region_vector,
    )
    return {
        "slide": slide,
        "vector": vector,
        "semanticHint": semantic_hint,
        "headline": headline,
        "content": content,
        "texts": texts,
        "images": images,
        "shapes": shapes,
        "headlineFont": headline_font,
        "headlineBounds": headline_bounds,
        "layoutPart": slide.get("layoutPart"),
        "canvasWidth": width,
        "canvasHeight": height,
    }


def _feature_distance(left: dict[str, object], right: dict[str, object]) -> float:
    left_vector = left["vector"]
    right_vector = right["vector"]
    base = math.sqrt(
        sum((a - b) ** 2 for a, b in zip(left_vector, right_vector))
        / len(left_vector)
    )
    left_hint = left["semanticHint"]
    right_hint = right["semanticHint"]
    if left_hint != right_hint and (
        left_hint in {"cover", "closing"}
        or right_hint in {"cover", "closing"}
    ):
        base += 1.0
    elif left_hint != right_hint and {left_hint, right_hint} != {"body", "section"}:
        base += 0.22
    elif left_hint != right_hint and {left_hint, right_hint} == {"body", "section"}:
        # A section hint is emitted only after the sparse/large-title threshold
        # is met. Keep that high-certainty boundary distinct from dense body
        # layouts; weak candidates remain `body` and can still co-cluster.
        base += 0.5
    elif left_hint != right_hint:
        base += 0.07
    # Native layout/master identity is supporting evidence, never a hard key.
    if left.get("layoutPart") == right.get("layoutPart"):
        base *= 0.96
    else:
        base *= 1.04
    return base


def _cluster_diameter(
    left: list[dict[str, object]], right: list[dict[str, object]]
) -> float:
    combined = [*left, *right]
    return max(
        (
            _feature_distance(first, second)
            for index, first in enumerate(combined)
            for second in combined[index + 1 :]
        ),
        default=0.0,
    )


def _medoid(features: list[dict[str, object]]) -> dict[str, object]:
    return min(
        features,
        key=lambda candidate: (
            sum(_feature_distance(candidate, other) for other in features),
            candidate["slide"]["slideNumber"],
        ),
    )


def _stable_layout_candidate_id(slide_numbers: Iterable[int]) -> str:
    membership = ",".join(str(number) for number in sorted(slide_numbers))
    return f"layout-candidate:{hashlib.sha256(membership.encode('ascii')).hexdigest()[:16]}"


def _slot_evidence(
    element: dict[str, object],
    candidate_id: str,
    index: int,
    explicit_role: str | None = None,
) -> AiLayoutSlotEvidence:
    placeholder = str(element.get("placeholderType") or "")
    if explicit_role is not None:
        role = explicit_role
    elif placeholder in {"title", "ctrTitle", "subTitle"}:
        role = "title"
    elif placeholder in {"body", "obj"} or element["type"] == "text":
        role = "body"
    elif element["type"] == "image":
        role = "image"
    else:
        role = "content"
    return AiLayoutSlotEvidence(
        id=f"slot-evidence:{candidate_id.removeprefix('layout-candidate:')}-{index}",
        role=role,
        bounds=element["bounds"],
        placeholder_type=element.get("placeholderType"),
        placeholder_index=element.get("placeholderIndex"),
        font_family=element.get("fontFamily"),
        font_size=element.get("fontSize"),
        color=element.get("color"),
    )


def _feature_slots(
    feature: dict[str, object], candidate_id: str
) -> list[AiLayoutSlotEvidence]:
    headline = feature.get("headline")
    content = [item for item in feature["content"] if item is not headline]
    slots: list[AiLayoutSlotEvidence] = []
    if headline is not None:
        slots.append(_slot_evidence(headline, candidate_id, 0, "title"))
    body_items = [item for item in content if item["type"] == "text"]
    if body_items:
        below = [
            item
            for item in body_items
            if headline is None
            or float(item["bounds"]["y"])
            >= float(headline["bounds"]["y"])
            + float(headline["bounds"]["height"]) * 0.75
        ] or body_items
        selected = sorted(
            below,
            key=lambda item: -float(item["bounds"]["width"])
            * float(item["bounds"]["height"]),
        )[:10]
        left = min(float(item["bounds"]["x"]) for item in selected)
        top = min(float(item["bounds"]["y"]) for item in selected)
        right = max(float(item["bounds"]["x"]) + float(item["bounds"]["width"]) for item in selected)
        bottom = max(float(item["bounds"]["y"]) + float(item["bounds"]["height"]) for item in selected)
        body = dict(selected[0])
        body["bounds"] = {
            "x": left,
            "y": top,
            "width": max(1.0, right - left),
            "height": max(1.0, bottom - top),
        }
        slots.append(_slot_evidence(body, candidate_id, len(slots), "body"))
    for image in sorted(
        feature["images"],
        key=lambda item: -float(item["bounds"]["width"])
        * float(item["bounds"]["height"]),
    )[:2]:
        slots.append(_slot_evidence(image, candidate_id, len(slots), "image"))
    return slots


def _content_signature(element: dict[str, object]) -> str | None:
    normalized = _normalized_content(element)
    return _digest(normalized.encode("utf-8")) if normalized else None


def _feature_sidecar_from_slides(
    *,
    source_digest: str,
    slides: list[dict[str, object]],
    width: int,
    height: int,
    geometry_tolerance_ratio: float,
) -> AiFeatureSidecar:
    scoped_furniture = _layout_furniture_keys(
        slides, width, height, geometry_tolerance_ratio
    )
    sanitized_slides: list[AiSlideFeature] = []
    for slide in slides:
        slide_number = int(slide["slideNumber"])
        furniture_keys = scoped_furniture.get(slide_number, set())
        feature = _layout_feature(
            slide,
            width,
            height,
            furniture_keys,
            geometry_tolerance_ratio,
            len(slides),
        )
        headline = feature.get("headline")
        elements: list[AiElementFeature] = []
        for element in slide["elements"]:
            if element["type"] not in {"text", "image", "shape"}:
                continue
            furniture = (
                _element_key(
                    element, width, height, geometry_tolerance_ratio
                )
                in furniture_keys
            )
            placeholder = str(element.get("placeholderType") or "")
            if furniture:
                inferred_role = "furniture"
            elif element is headline or placeholder in {"title", "ctrTitle", "subTitle"}:
                inferred_role = "title"
            elif element["type"] == "text":
                inferred_role = "body"
            elif element["type"] == "image":
                inferred_role = "image"
            else:
                inferred_role = "content"
            elements.append(
                AiElementFeature(
                    kind=element["type"],
                    bounds=element["bounds"],
                    rotation=float(element.get("rotation") or 0),
                    z_order=int(element.get("zIndex") or 0),
                    font_family=element.get("fontFamily"),
                    font_size=element.get("fontSize"),
                    color=element.get("color"),
                    fill=element.get("fill"),
                    image_digest=element.get("digest"),
                    crop=element.get("crop"),
                    fit=element.get("fit"),
                    placeholder_type=element.get("placeholderType"),
                    placeholder_index=element.get("placeholderIndex"),
                    inferred_role=inferred_role,
                    content_signature=_content_signature(element),
                    furniture=furniture,
                )
            )
        sanitized_slides.append(
            AiSlideFeature(
                slide_number=slide_number,
                layout_part=slide.get("layoutPart"),
                master_part=slide.get("masterPart"),
                theme_part=slide.get("themePart"),
                design_family=_design_family_key(slide, width, height),
                semantic_hint=feature["semanticHint"],
                clustering_vector=list(feature["vector"]),
                elements=elements,
            )
        )
    return AiFeatureSidecar(
        algorithm_version=AI_MASTER_FEATURE_ALGORITHM_VERSION,
        source_digest=source_digest,
        slide_count=len(sanitized_slides),
        width_px=width,
        height_px=height,
        slides=sanitized_slides,
    )


def _runtime_features(sidecar: AiFeatureSidecar) -> dict[int, dict[str, object]]:
    result: dict[int, dict[str, object]] = {}
    for slide_feature in sidecar.slides:
        all_elements: list[dict[str, object]] = []
        for stored in slide_feature.elements:
            element = {
                "type": stored.kind,
                # Raw text is intentionally unavailable in the cache; this
                # marker is only used for type filtering in slot aggregation.
                "text": "<sanitized>" if stored.kind == "text" else None,
                "digest": stored.image_digest,
                "crop": stored.crop.model_dump(mode="python") if stored.crop else None,
                "fit": stored.fit,
                "bounds": stored.bounds.model_dump(mode="python"),
                "rotation": stored.rotation,
                "zIndex": stored.z_order,
                "fontFamily": stored.font_family,
                "fontSize": stored.font_size,
                "color": stored.color,
                "fill": stored.fill,
                "placeholderType": stored.placeholder_type,
                "placeholderIndex": stored.placeholder_index,
                "inferredRole": stored.inferred_role,
                "contentSignature": stored.content_signature,
                "furniture": stored.furniture,
            }
            all_elements.append(element)
        content = [item for item in all_elements if not item["furniture"]]
        headline = next(
            (item for item in content if item["inferredRole"] == "title"), None
        )
        result[slide_feature.slide_number] = {
            "slide": {
                "slideNumber": slide_feature.slide_number,
                "layoutPart": slide_feature.layout_part,
                "masterPart": slide_feature.master_part,
                "themePart": slide_feature.theme_part,
            },
            "vector": tuple(slide_feature.clustering_vector),
            "semanticHint": slide_feature.semantic_hint,
            "headline": headline,
            "content": content,
            "allElements": all_elements,
            "texts": [item for item in content if item["type"] == "text"],
            "images": [item for item in content if item["type"] == "image"],
            "shapes": [item for item in content if item["type"] == "shape"],
            "layoutPart": slide_feature.layout_part,
            "canvasWidth": sidecar.width_px,
            "canvasHeight": sidecar.height_px,
        }
    return result


def _stable_feature_slots(
    features: list[dict[str, object]], candidate_id: str
) -> list[AiLayoutSlotEvidence]:
    if not features:
        return []
    width = int(features[0]["canvasWidth"])
    height = int(features[0]["canvasHeight"])
    grouped: dict[tuple[object, ...], list[tuple[int, dict[str, object]]]] = defaultdict(list)
    for feature in features:
        slide_number = int(feature["slide"]["slideNumber"])
        for element in feature["allElements"]:
            inferred = str(element.get("inferredRole") or "content")
            role = (
                "title"
                if inferred == "title"
                else "body"
                if inferred == "body"
                else "image"
                if inferred == "image"
                else "content"
            )
            bounds = element["bounds"]
            key = (
                role,
                element["type"],
                str(element.get("placeholderType") or ""),
                int(element["placeholderIndex"])
                if element.get("placeholderIndex") is not None
                else -1,
                round(float(bounds["x"]) / max(1.0, width * 0.06)),
                round(float(bounds["y"]) / max(1.0, height * 0.06)),
                round(float(bounds["width"]) / max(1.0, width * 0.06)),
                round(float(bounds["height"]) / max(1.0, height * 0.06)),
                bool(element.get("furniture")),
            )
            grouped[key].append((slide_number, element))
    threshold = max(1, math.ceil(len(features) * 0.55))
    stable = [
        (key, values)
        for key, values in grouped.items()
        if len({number for number, _ in values}) >= threshold
    ]
    stable.sort(key=lambda item: item[0])
    slots: list[AiLayoutSlotEvidence] = []
    for key, values in stable[:64]:
        role = str(key[0])
        observations = [element for _, element in values]
        families = Counter(
            str(item["fontFamily"]) for item in observations if item.get("fontFamily")
        )
        colors = Counter(
            str(item["color"]) for item in observations if item.get("color")
        )
        placeholder_types = Counter(
            str(item["placeholderType"])
            for item in observations
            if item.get("placeholderType")
        )
        indices = [
            int(item["placeholderIndex"])
            for item in observations
            if item.get("placeholderIndex") is not None
        ]
        slots.append(
            AiLayoutSlotEvidence(
                id=f"slot-evidence:{candidate_id.removeprefix('layout-candidate:')}-{len(slots)}",
                role=role,
                bounds={
                    "x": _median_value(float(item["bounds"]["x"]) for item in observations),
                    "y": _median_value(float(item["bounds"]["y"]) for item in observations),
                    "width": max(1.0, _median_value(float(item["bounds"]["width"]) for item in observations)),
                    "height": max(1.0, _median_value(float(item["bounds"]["height"]) for item in observations)),
                },
                placeholder_type=placeholder_types.most_common(1)[0][0] if placeholder_types else None,
                placeholder_index=round(_median_value(indices)) if indices else None,
                font_family=families.most_common(1)[0][0] if families else None,
                font_size=(
                    _median_value(float(item["fontSize"]) for item in observations if item.get("fontSize") is not None)
                    if any(item.get("fontSize") is not None for item in observations)
                    else None
                ),
                color=colors.most_common(1)[0][0] if colors else None,
            )
        )
    return slots


def _archetype_name(feature: dict[str, object]) -> str:
    headline_bounds = feature["headlineBounds"]
    if headline_bounds[3] >= 0.09:
        return "장문 제목형"
    large_regions = sum(
        _normalized_bounds(
            item,
            int(feature["canvasWidth"]),
            int(feature["canvasHeight"]),
        )[2]
        * _normalized_bounds(
            item,
            int(feature["canvasWidth"]),
            int(feature["canvasHeight"]),
        )[3]
        >= 0.025
        for item in feature["content"]
    )
    if large_regions >= 6:
        return "카드·대시보드형"
    if len(feature["images"]) >= 1:
        return "이미지·플로우형"
    return "구조화 본문형"


def _cluster_layouts(
    slides: list[dict[str, object]],
    width: int,
    height: int,
    vector_tolerance: float = LAYOUT_VECTOR_TOLERANCE,
    geometry_tolerance_ratio: float = GEOMETRY_TOLERANCE_RATIO,
) -> list[AiLayoutCandidate]:
    furniture_by_slide: dict[int, set[tuple[object, ...]]] = {}
    features: list[dict[str, object]] = []
    # At most two deterministic passes: coarse design-family repeat evidence is
    # resolved first, then features are rebuilt with that furniture excluded.
    for _ in range(2):
        resolved = _layout_furniture_keys(
            slides, width, height, geometry_tolerance_ratio
        )
        features = [
            _layout_feature(
                slide,
                width,
                height,
                resolved.get(int(slide["slideNumber"]), set()),
                geometry_tolerance_ratio,
                len(slides),
            )
            for slide in slides
        ]
        if resolved == furniture_by_slide:
            break
        furniture_by_slide = resolved
    clusters: list[list[dict[str, object]]] = [[feature] for feature in features]
    # Complete-link agglomeration is order-independent and bounds within-cluster
    # diameter, preventing one chain of small similarities from becoming a mega
    # layout. Membership is used as the deterministic tie breaker.
    while True:
        eligible: list[tuple[float, tuple[int, ...], int, int]] = []
        for left_index, left in enumerate(clusters):
            for right_index in range(left_index + 1, len(clusters)):
                right = clusters[right_index]
                left_hint = _medoid(left)["semanticHint"]
                right_hint = _medoid(right)["semanticHint"]
                if (
                    left_hint == right_hint == "body"
                    and _body_intent_signature(left)
                    != _body_intent_signature(right)
                ):
                    continue
                diameter = _cluster_diameter(left, right)
                if diameter <= vector_tolerance:
                    membership = tuple(
                        sorted(
                            feature["slide"]["slideNumber"]
                            for feature in [*left, *right]
                        )
                    )
                    eligible.append(
                        (diameter, membership, left_index, right_index)
                    )
        if not eligible:
            break
        _, _, left_index, right_index = min(eligible)
        merged = [*clusters[left_index], *clusters[right_index]]
        clusters = [
            cluster
            for index, cluster in enumerate(clusters)
            if index not in {left_index, right_index}
        ]
        clusters.append(merged)

    # Merge tiny non-semantic variants into their nearest compatible cluster
    # with a bounded relaxed diameter. Genuine cover/closing evidence remains
    # separate even when it is a singleton.
    changed = True
    while changed:
        changed = False
        for source_index, source in sorted(
            enumerate(clusters),
            key=lambda item: (
                len(item[1]),
                min(feature["slide"]["slideNumber"] for feature in item[1]),
            ),
        ):
            if len(source) > max(1, math.ceil(len(slides) * 0.06)):
                continue
            source_hint = _medoid(source)["semanticHint"]
            if source_hint in {"cover", "closing"}:
                continue
            choices = []
            for target_index, target in enumerate(clusters):
                if target_index == source_index:
                    continue
                target_hint = _medoid(target)["semanticHint"]
                if target_hint in {"cover", "closing"}:
                    continue
                if (
                    source_hint == target_hint == "body"
                    and _body_intent_signature(source)
                    != _body_intent_signature(target)
                ):
                    continue
                diameter = _cluster_diameter(source, target)
                if diameter <= vector_tolerance * 1.45:
                    target_membership = tuple(
                        sorted(
                            feature["slide"]["slideNumber"]
                            for feature in target
                        )
                    )
                    choices.append((diameter, target_membership, target_index))
            if not choices:
                continue
            _, _, target_index = min(choices)
            clusters[target_index] = [*clusters[target_index], *source]
            clusters.pop(source_index)
            changed = True
            break

    # A relaxed complete-link pass may choose a medoid whose intent hides one
    # minority member. Repartition once by each slide's semantic role/intent so
    # image-led, long-title, KPI, comparison and card layouts never leak into
    # one another before final candidate-level dedupe.
    intent_partitioned: list[list[dict[str, object]]] = []
    for grouped in clusters:
        by_intent: dict[tuple[object, ...], list[dict[str, object]]] = defaultdict(list)
        for feature in grouped:
            hint = str(feature["semanticHint"])
            key: tuple[object, ...] = (
                (hint, *_body_intent_signature([feature]))
                if hint == "body"
                else (hint,)
            )
            by_intent[key].append(feature)
        intent_partitioned.extend(
            by_intent[key] for key in sorted(by_intent, key=repr)
        )
    clusters = intent_partitioned
    clusters.sort(
        key=lambda grouped: min(
            feature["slide"]["slideNumber"] for feature in grouped
        )
    )
    candidates: list[AiLayoutCandidate] = []
    largest_body = max(
        (
            len(grouped)
            for grouped in clusters
            if _medoid(grouped)["semanticHint"] in {"body", "section"}
        ),
        default=0,
    )
    body_index = 0
    primary_assigned: set[str] = set()
    for grouped in clusters:
        representative_feature = _medoid(grouped)
        representative = representative_feature["slide"]
        number = representative["slideNumber"]
        hint_counts = Counter(feature["semanticHint"] for feature in grouped)
        semantic_hint = hint_counts.most_common(1)[0][0]
        if semantic_hint == "cover" and "cover" not in primary_assigned:
            kind, name = "cover", "표지"
            primary_assigned.add("cover")
        elif semantic_hint == "closing" and "closing" not in primary_assigned:
            kind, name = "closing", "마무리"
            primary_assigned.add("closing")
        elif semantic_hint == "section" and "section" not in primary_assigned:
            kind, name = "section", "섹션"
            primary_assigned.add("section")
        elif (
            semantic_hint == "body"
            and len(grouped) == largest_body
            and "standard" not in primary_assigned
        ):
            kind, name = "standard", "본문"
            primary_assigned.add("standard")
            body_index += 1
        else:
            body_index += 1
            kind = "custom"
            name = f"본문 변형 {body_index} · {_archetype_name(representative_feature)}"
        slide_numbers = sorted(
            feature["slide"]["slideNumber"] for feature in grouped
        )
        candidate_id = _stable_layout_candidate_id(slide_numbers)
        slots = _feature_slots(representative_feature, candidate_id)
        distances = [
            _feature_distance(representative_feature, feature)
            for feature in grouped
        ]
        average_distance = sum(distances) / max(1, len(distances))
        max_distance = max(distances, default=0.0)
        support = min(1.0, len(grouped) / max(2, len(slides) * 0.15))
        singleton_penalty = 0.1 if len(grouped) == 1 and semantic_hint not in {"cover", "closing"} else 0.0
        mega_penalty = max(0.0, len(grouped) / len(slides) - 0.72) * 0.7
        confidence = max(
            0.35,
            min(
                0.99,
                0.72
                + support * 0.22
                - average_distance * 1.1
                - max_distance * 0.35
                - singleton_penalty
                - mega_penalty,
            ),
        )
        differences = [
            f"clusterAverageDistance={average_distance:.4f}",
            f"clusterMaxDistance={max_distance:.4f}",
            f"clusterSupport={len(grouped)}/{len(slides)}",
        ]
        native_layouts = {
            feature["slide"].get("layoutPart") for feature in grouped
        }
        native_consistency = max(
            Counter(
                feature["slide"].get("layoutPart") for feature in grouped
            ).values(),
            default=0,
        ) / len(grouped)
        semantic_consistency = hint_counts[semantic_hint] / len(grouped)
        separation = min(
            (
                _feature_distance(representative_feature, _medoid(other))
                for other in clusters
                if other is not grouped
            ),
            default=vector_tolerance * 2,
        )
        confidence = min(
            0.99,
            confidence
            + min(0.08, separation * 0.2)
            + native_consistency * 0.025
            + semantic_consistency * 0.035,
        )
        if len(grouped) == 1 and kind == "custom":
            confidence = min(confidence, 0.65)
        differences.extend(
            [
                f"nearestClusterSeparation={separation:.4f}",
                f"nativeLayoutConsistency={native_consistency:.4f}",
                f"semanticKindConsistency={semantic_consistency:.4f}",
            ]
        )
        if len(native_layouts) == 1:
            differences.append("nativeLayoutShared=true")
        candidates.append(
            AiLayoutCandidate(
                id=candidate_id,
                name=name,
                kind=kind,
                representative_slide=number,
                slide_numbers=slide_numbers,
                confidence=round(confidence, 4),
                differences=differences,
                slots=slots,
                layout_part=representative.get("layoutPart"),
                master_part=representative.get("masterPart"),
                theme_part=representative.get("themePart"),
                provenance="analysis",
                source_layout_ids=[candidate_id],
                auto_dedupe_eligible=True,
            )
        )
    return candidates


def _repeat_candidates(
    slides: list[dict[str, object]],
    width: int,
    height: int,
    geometry_tolerance_ratio: float = GEOMETRY_TOLERANCE_RATIO,
    repeat_coverage: float = REPEAT_MIN_COVERAGE,
) -> list[AiCandidateElement]:
    grouped: dict[tuple[object, ...], list[tuple[int, dict[str, object]]]] = defaultdict(list)
    for slide in slides:
        for element in slide["elements"]:
            if element["type"] in {"text", "image", "shape"}:
                grouped[
                    _element_key(
                        element, width, height, geometry_tolerance_ratio
                    )
                ].append((slide["slideNumber"], element))
    threshold = max(2, math.ceil(len(slides) * repeat_coverage))
    results: list[AiCandidateElement] = []
    for values in grouped.values():
        unique_slides = sorted({number for number, _ in values})
        if len(unique_slides) < threshold:
            continue
        exemplar = values[0][1]
        role = _role(exemplar)
        evidence = [
            AiElementEvidence(
                slide_number=number,
                bounds=element["bounds"],
                rotation=element["rotation"],
                z_index=element["zIndex"],
                layout_part=element.get("layoutPart"),
                master_part=element.get("masterPart"),
                theme_part=element.get("themePart"),
            )
            for number, element in values
        ]
        xs = [item.bounds.x for item in evidence]
        ys = [item.bounds.y for item in evidence]
        position_delta = max(max(xs) - min(xs), max(ys) - min(ys))
        font_sizes = [float(element["fontSize"]) for _, element in values if element.get("fontSize")]
        differences: list[str] = []
        if position_delta:
            differences.append(f"maxPositionDeltaPx={position_delta:.3f}")
        if font_sizes and max(font_sizes) - min(font_sizes) > FONT_SIZE_TOLERANCE_PT:
            differences.append(f"fontSizeRangePt={min(font_sizes):.1f}-{max(font_sizes):.1f}")
        confidence = (len(unique_slides) / len(slides)) * max(0.5, 1 - position_delta / max(width, height))
        type_name = "pageNumber" if role == "pageNumber" else exemplar["type"]
        candidate_key = hashlib.sha256(repr(_element_key(exemplar, width, height, geometry_tolerance_ratio)).encode()).hexdigest()[:16]
        results.append(
            AiCandidateElement(
                id=f"candidate:{candidate_key}",
                type=type_name,
                role=role,
                text=exemplar.get("text"),
                asset_id=exemplar.get("assetId"),
                digest=exemplar.get("digest"),
                media_type=exemplar.get("mediaType"),
                crop=exemplar.get("crop"),
                fit=exemplar.get("fit"),
                confidence=round(min(0.99, confidence), 4),
                evidence=evidence,
                differences=differences,
                ai_zone_candidate=role in {"documentCode", "customerName", "rfpNumber", "securityLevel", "repeatedText"},
            )
        )
    return sorted(results, key=lambda item: (-item.confidence, item.id))


def _fallback_slot(kind: str, width: int, height: int, role: str) -> dict[str, float]:
    if role == "title":
        if kind == "longTitle":
            return {"x": width * 0.075, "y": height * 0.09, "width": width * 0.85, "height": height * 0.245}
        return {"x": width * 0.075, "y": height * (0.25 if kind in {"cover", "section"} else 0.11), "width": width * 0.85, "height": height * 0.18}
    return {"x": width * 0.075, "y": height * (0.405 if kind == "longTitle" else 0.53 if kind in {"cover", "section"} else 0.34), "width": width * 0.85, "height": height * (0.405 if kind == "longTitle" else 0.45)}


def _layout_payload(candidate: AiLayoutCandidate | None, key: str, name: str, kind: str, width: int, height: int) -> dict[str, object]:
    source_slots = [] if candidate is None else list(candidate.slots)
    title = next((slot for slot in source_slots if slot.role == "title"), None)
    body = next((slot for slot in source_slots if slot.role in {"body", "content", "image"}), None)
    slots = []
    title_bounds: dict[str, float] | None = None
    for index, (role, evidence) in enumerate((("title", title), ("body", body))):
        bounds = evidence.bounds.model_dump(mode="json", by_alias=True) if evidence else _fallback_slot(kind, width, height, role)
        minimum_y = height * 0.09
        maximum_bottom = height * 0.91
        bounds["x"] = max(0.0, min(float(bounds["x"]), width - 1.0))
        bounds["y"] = max(minimum_y, min(float(bounds["y"]), maximum_bottom - 1.0))
        bounds["width"] = max(1.0, min(float(bounds["width"]), width - bounds["x"]))
        bounds["height"] = max(1.0, min(float(bounds["height"]), maximum_bottom - bounds["y"]))
        if role == "title":
            title_bounds = bounds
        elif title_bounds is not None and bounds["y"] < title_bounds["y"] + title_bounds["height"] + 4:
            bounds = _fallback_slot(kind, width, height, role)
        slots.append({
            "id": f"slot:ai-{key}-{role}",
            "role": role,
            "placeholderIndex": index,
            "bounds": bounds,
            "ownership": {"locked": False, "modelWritable": True},
            "style": "title" if role == "title" else "body",
        })
    return {"id": f"layout:ai-{key}", "name": name, "kind": kind, "slots": slots}


def _long_title_candidate(candidates: list[AiLayoutCandidate]) -> AiLayoutCandidate | None:
    def evidence_score(candidate: AiLayoutCandidate) -> int:
        evidence = {value.strip().casefold() for value in candidate.differences}
        if "autodedupefamily=longtitle" in evidence:
            return 3
        if evidence & {"autointent=longtitle", "autodedupeintent=longtitle"}:
            return 2
        normalized_name = re.sub(r"[\s_-]+", "", candidate.name).casefold()
        return 1 if "longtitle" in normalized_name or "장문제목" in normalized_name else 0

    ranked = [
        (-evidence_score(candidate), min(candidate.slide_numbers), candidate.id, candidate)
        for candidate in candidates
        if evidence_score(candidate) > 0
    ]
    return min(ranked)[3] if ranked else None


def _layouts_geometrically_equivalent(
    left: dict[str, object],
    right: dict[str, object],
    width: int,
    height: int,
) -> bool:
    def normalized_slots(layout: dict[str, object]) -> dict[str, tuple[float, ...]]:
        result: dict[str, tuple[float, ...]] = {}
        for slot in layout.get("slots", []):
            role = str(slot.get("role", ""))
            if role not in {"title", "body"}:
                continue
            bounds = slot.get("bounds", {})
            result[role] = (
                float(bounds.get("x", 0.0)) / max(1, width),
                float(bounds.get("y", 0.0)) / max(1, height),
                float(bounds.get("width", 0.0)) / max(1, width),
                float(bounds.get("height", 0.0)) / max(1, height),
            )
        return result

    left_slots = normalized_slots(left)
    right_slots = normalized_slots(right)
    return bool(left_slots) and left_slots.keys() == right_slots.keys() and all(
        abs(left_value - right_value) <= GEOMETRY_TOLERANCE_RATIO
        for role in left_slots
        for left_value, right_value in zip(left_slots[role], right_slots[role])
    )


def _choose_color(colors: list[AiColorEvidence], index: int, fallback: str) -> str:
    return colors[index].hex if len(colors) > index else fallback


def build_contract_draft(analysis: AiMasterAnalysis) -> DeckMasterContract:
    width, height = analysis.source.width_px, analysis.source.height_px
    font = (
        "Malgun Gothic"
        if analysis.purpose == "rebrand"
        else analysis.fonts[0].family if analysis.fonts else "Malgun Gothic"
    )
    fallbacks = list(dict.fromkeys([font, "Malgun Gothic", "Arial", "sans-serif"]))[:12]
    colors = (
        {
            "background": "#FFFFFF",
            "surface": "#F5F8FC",
            "textPrimary": "#172033",
            "textSecondary": "#5F6B7C",
            "accent": "#356AE6",
            "divider": "#D7DCE5",
            "warning": "#D64550",
        }
        if analysis.purpose == "rebrand"
        else {
            "background": _choose_color(analysis.colors, 0, "#FFFFFF"),
            "surface": _choose_color(analysis.colors, 1, "#F5F8FC"),
            "textPrimary": _choose_color(analysis.colors, 2, "#172033"),
            "textSecondary": _choose_color(analysis.colors, 3, "#5F6B7C"),
            "accent": _choose_color(analysis.colors, 4, "#356AE6"),
            "divider": _choose_color(analysis.colors, 5, "#D7DCE5"),
            "warning": "#D64550",
        }
    )
    payload = _base_contract(
        master_id=analysis.contract_draft.master_id if getattr(analysis, "contract_draft", None) else f"master:ai-{analysis.analysis_id[:16]}",
        name=f"{Path(analysis.source.filename).stem} AI Master",
        colors=colors,
        header="",
        callout="",
    )
    payload["canvas"] = {
        "widthPx": width,
        "heightPx": height,
        "locale": "ko-KR",
        "safeArea": {"x": width * 0.047, "y": height * 0.07, "width": width * 0.906, "height": height * 0.84},
    }
    scale_x = width / 1280.0
    scale_y = height / 720.0
    for furniture in payload["furniture"].values():
        bounds = furniture["bounds"]
        furniture["bounds"] = {
            "x": float(bounds["x"]) * scale_x,
            "y": float(bounds["y"]) * scale_y,
            "width": float(bounds["width"]) * scale_x,
            "height": float(bounds["height"]) * scale_y,
        }
    payload["tokens"]["fonts"] = {"latin": font, "eastAsian": font, "complexScript": font, "fallbacks": fallbacks}
    payload["fontManifest"] = {
        "primary": font,
        "fallbacks": fallbacks,
        "windowsFallback": font,
        "themeScripts": {"latin": font, "hang": font, "jpan": font, "hans": font, "hant": font},
        "files": [],
    }
    active = [item for item in analysis.layout_candidates if not item.excluded]
    def pick(kind: str, fallback_index: int = 0) -> AiLayoutCandidate | None:
        return next((item for item in active if item.kind == kind), active[fallback_index] if active else None)
    cover_candidate = pick("cover")
    section_candidate = pick("section")
    standard_candidate = pick("standard")
    long_title_candidate = _long_title_candidate(active)
    standard_layout = _layout_payload(standard_candidate, "standard", "본문", "standard", width, height)
    long_title_layout = _layout_payload(long_title_candidate, "long-title", "긴 제목", "longTitle", width, height)
    if _layouts_geometrically_equivalent(standard_layout, long_title_layout, width, height):
        long_title_layout = _layout_payload(None, "long-title", "긴 제목", "longTitle", width, height)
    payload["layouts"] = {
        "cover": _layout_payload(cover_candidate, "cover", "표지", "cover", width, height),
        "section": _layout_payload(section_candidate, "section", "섹션", "section", width, height),
        "standard": standard_layout,
        "longTitle": long_title_layout,
    }
    used = {id(candidate) for candidate in (cover_candidate, section_candidate, standard_candidate, long_title_candidate) if candidate is not None}
    custom_layouts = []
    for index, candidate in enumerate((item for item in active if id(item) not in used), start=1):
        custom = _layout_payload(candidate, f"custom-{index}", candidate.name, "custom", width, height)
        custom["kind"] = "custom"
        if any(
            _layouts_geometrically_equivalent(custom, published, width, height)
            for published in [*payload["layouts"].values(), *custom_layouts]
        ):
            continue
        custom_layouts.append(custom)
    payload["customLayouts"] = custom_layouts
    payload["fitPolicy"]["promotionLayoutId"] = "layout:ai-long-title"
    decisions = {item.candidate_id: item for item in analysis.decisions}
    semantic_elements: list[dict[str, object]] = []
    for candidate in analysis.repeated_elements:
        decision = decisions.get(candidate.id)
        if decision is None or decision.action in {"pending", "remove"}:
            continue
        evidence = candidate.evidence[0]
        content: dict[str, object] = {"role": "fixed-text"}
        element_type = "text" if candidate.type in {"text", "pageNumber"} else candidate.type
        if element_type == "image":
            if candidate.media_type != "image/png" or not candidate.asset_id or not candidate.digest:
                continue
            content.update({"assetId": candidate.asset_id, "digest": candidate.digest, "mediaType": candidate.media_type, "fit": candidate.fit, "crop": candidate.crop.model_dump(mode="json", by_alias=True) if candidate.crop else None, "altText": "PPTX repeated image"})
        else:
            content["text"] = decision.override_text or candidate.text or ""
        editable_user = decision.action == "variable"
        editable_ai = decision.action == "aiZone"
        if editable_user:
            content["role"] = f"user-variable:{decision.variable_name or candidate.role}"
        if editable_ai:
            content["role"] = "ai-text-zone"
            content["aiBinding"] = {
                "instruction": decision.instruction,
                "exampleText": decision.example_text,
                "emptyBehavior": decision.empty_behavior,
                "fallbackText": decision.fallback_text,
                "maxChars": decision.max_chars,
            }
        semantic_elements.append({
            "id": f"element:{candidate.id.removeprefix('candidate:')}",
            "type": element_type,
            "scope": {"kind": "common"},
            "bounds": evidence.bounds.model_dump(mode="json", by_alias=True),
            "rotation": evidence.rotation,
            "zIndex": evidence.z_index,
            "visibility": "always",
            "editableByUser": editable_user,
            "editableByAI": editable_ai,
            "locked": not (editable_user or editable_ai),
            "style": {},
            "content": content,
        })
    payload["semanticElements"] = semantic_elements
    return DeckMasterContract.model_validate(payload)


def validate_reapply(analysis: AiMasterAnalysis) -> AiReapplyValidation:
    issues: list[AiReapplyIssue] = []
    for candidate in analysis.repeated_elements:
        if candidate.crop is not None:
            issues.append(AiReapplyIssue(code="imageCrop", candidate_id=candidate.id, message="원본 이미지 crop 관계를 유지해야 합니다."))
        if len({item.slide_number for item in candidate.evidence}) < analysis.source.slide_count:
            issues.append(AiReapplyIssue(code="missingRepeatedElement", candidate_id=candidate.id, message="일부 원본 슬라이드에 반복 요소가 없습니다."))
        if candidate.differences:
            issues.append(AiReapplyIssue(code="positionDifference", candidate_id=candidate.id, message="; ".join(candidate.differences)))
    for layout in analysis.layout_candidates:
        if layout.excluded or not layout.slide_numbers:
            issues.append(AiReapplyIssue(code="unusedLayout", layout_id=layout.id, message="게시 초안에서 사용되지 않는 레이아웃입니다."))
        for slot in layout.slots:
            if slot.role not in {"title", "body"} or slot.font_size is None:
                continue
            capacity = max(1, int((slot.bounds.width * slot.bounds.height) / max(1, slot.font_size ** 2 * 0.55)))
            representative_texts = [item.text for item in analysis.repeated_elements if item.text and any(ev.slide_number in layout.slide_numbers for ev in item.evidence)]
            if any(len(text) > capacity for text in representative_texts):
                issues.append(AiReapplyIssue(code="textOverflow", slide_number=layout.representative_slide, layout_id=layout.id, message="대표 텍스트가 생성 slot을 넘칠 가능성이 있습니다."))
    for layout in analysis.layout_candidates:
        for index, left in enumerate(layout.slots):
            for right in layout.slots[index + 1 :]:
                overlap_w = min(left.bounds.right, right.bounds.right) - max(left.bounds.x, right.bounds.x)
                overlap_h = min(left.bounds.bottom, right.bounds.bottom) - max(left.bounds.y, right.bounds.y)
                if overlap_w > 2 and overlap_h > 2:
                    issues.append(AiReapplyIssue(code="overlap", slide_number=layout.representative_slide, layout_id=layout.id, message=f"{left.id}와 {right.id} 영역이 겹칩니다."))
    return AiReapplyValidation(valid=not any(item.severity == "error" for item in issues), issues=issues, checked_slide_count=analysis.source.slide_count)


def _median_value(values: Iterable[float]) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return (float(ordered[middle - 1]) + float(ordered[middle])) / 2


def _membership_representative(
    slide_numbers: list[int], preferred: Iterable[int] = ()
) -> int:
    center = _median_value(slide_numbers)
    preferred_set = set(preferred)
    return min(
        slide_numbers,
        key=lambda number: (
            abs(number - center),
            0 if number in preferred_set else 1,
            number,
        ),
    )


def _aggregate_layout_slots(
    layouts: list[AiLayoutCandidate], candidate_id: str
) -> list[AiLayoutSlotEvidence]:
    by_role: dict[str, list[AiLayoutSlotEvidence]] = defaultdict(list)
    for layout in layouts:
        for slot in layout.slots:
            by_role[slot.role].append(slot)
    result: list[AiLayoutSlotEvidence] = []
    for role in ("title", "body", "content", "image"):
        slots = by_role.get(role, [])
        if not slots:
            continue
        families = Counter(
            slot.font_family for slot in slots if slot.font_family
        )
        colors = Counter(slot.color for slot in slots if slot.color)
        placeholder_types = Counter(
            slot.placeholder_type for slot in slots if slot.placeholder_type
        )
        placeholder_indices = [
            slot.placeholder_index
            for slot in slots
            if slot.placeholder_index is not None
        ]
        result.append(
            AiLayoutSlotEvidence(
                id=f"slot-evidence:{candidate_id.removeprefix('layout-candidate:')}-{len(result)}",
                role=role,
                bounds={
                    "x": _median_value(slot.bounds.x for slot in slots),
                    "y": _median_value(slot.bounds.y for slot in slots),
                    "width": max(
                        1.0,
                        _median_value(slot.bounds.width for slot in slots),
                    ),
                    "height": max(
                        1.0,
                        _median_value(slot.bounds.height for slot in slots),
                    ),
                },
                placeholder_type=(
                    placeholder_types.most_common(1)[0][0]
                    if placeholder_types
                    else None
                ),
                placeholder_index=(
                    round(_median_value(placeholder_indices))
                    if placeholder_indices
                    else None
                ),
                font_family=(families.most_common(1)[0][0] if families else None),
                font_size=(
                    _median_value(
                        slot.font_size
                        for slot in slots
                        if slot.font_size is not None
                    )
                    if any(slot.font_size is not None for slot in slots)
                    else None
                ),
                color=(colors.most_common(1)[0][0] if colors else None),
            )
        )
    return result


def _candidate_from_feature_members(
    features: list[dict[str, object]],
    *,
    name: str,
    total_slides: int,
    merge_threshold: float,
    evidence: str,
    provenance: str = "analysis",
    source_layout_ids: Iterable[str] = (),
    auto_dedupe_eligible: bool = True,
    forced_merge: bool | None = None,
) -> tuple[AiLayoutCandidate, bool]:
    if not features:
        raise AiImportError("authored_vnext_ai_master_feature_sidecar_corrupt")
    features = sorted(
        features, key=lambda item: int(item["slide"]["slideNumber"])
    )
    representative_feature = _medoid(features)
    representative = representative_feature["slide"]
    slide_numbers = [int(item["slide"]["slideNumber"]) for item in features]
    candidate_id = _stable_layout_candidate_id(slide_numbers)
    distances = [
        _feature_distance(representative_feature, item) for item in features
    ]
    average_distance = sum(distances) / len(distances)
    diameter = max(
        (
            _feature_distance(left, right)
            for index, left in enumerate(features)
            for right in features[index + 1 :]
        ),
        default=0.0,
    )
    hint_counts = Counter(str(item["semanticHint"]) for item in features)
    semantic_hint = min(
        hint_counts,
        key=lambda hint: (-hint_counts[hint], ("cover", "closing", "section", "body").index(hint)),
    )
    kind = {
        "cover": "cover",
        "closing": "closing",
        "section": "section",
        "body": "standard",
    }[semantic_hint]
    native_counts = Counter(item["slide"].get("layoutPart") for item in features)
    native_consistency = max(native_counts.values(), default=0) / len(features)
    semantic_consistency = hint_counts[semantic_hint] / len(features)
    support = min(1.0, len(features) / max(2, total_slides * 0.15))
    confidence = (
        0.72
        + support * 0.22
        - average_distance * 1.1
        - diameter * 0.35
        + native_consistency * 0.025
        + semantic_consistency * 0.035
    )
    if len(features) == 1 and semantic_hint not in {"cover", "closing"}:
        confidence -= 0.1
    forced = diameter > merge_threshold if forced_merge is None else forced_merge
    if forced:
        confidence -= min(0.32, 0.08 + (diameter - merge_threshold) * 0.75)
    confidence = max(0.25 if forced else 0.35, min(0.99, confidence))
    differences = [
        evidence,
        "featureEvidence=sidecar",
        f"clusterAverageDistance={average_distance:.4f}",
        f"clusterDiameter={diameter:.4f}",
        f"clusterSupport={len(features)}/{total_slides}",
        f"nativeLayoutConsistency={native_consistency:.4f}",
        f"semanticKindConsistency={semantic_consistency:.4f}",
    ]
    if len(native_counts) > 1:
        differences.append("nativeLayoutMismatch=true")
    else:
        differences.append("nativeLayoutShared=true")
    if forced:
        differences.extend(
            [
                "forcedMergeDiameterExceeded=true",
                f"forcedMergeDiameter={diameter:.4f}",
                f"mergeThreshold={merge_threshold:.4f}",
            ]
        )
    return (
        AiLayoutCandidate(
            id=candidate_id,
            name=name,
            kind=kind,
            representative_slide=int(representative["slideNumber"]),
            slide_numbers=slide_numbers,
            confidence=round(confidence, 4),
            differences=differences,
            slots=_stable_feature_slots(features, candidate_id),
            layout_part=representative.get("layoutPart"),
            master_part=representative.get("masterPart"),
            theme_part=representative.get("themePart"),
            provenance=provenance,
            source_layout_ids=sorted(set(source_layout_ids)),
            auto_dedupe_eligible=auto_dedupe_eligible,
        ),
        forced,
    )


def _coarse_bands(
    elements: list[dict[str, object]], width: int, gap: float = 0.20
) -> list[list[dict[str, object]]]:
    ordered = sorted(
        elements,
        key=lambda item: (
            float(item["bounds"]["x"])
            + float(item["bounds"]["width"]) / 2
        )
        / max(1, width),
    )
    bands: list[list[dict[str, object]]] = []
    for item in ordered:
        center = (
            float(item["bounds"]["x"])
            + float(item["bounds"]["width"]) / 2
        ) / max(1, width)
        if not bands:
            bands.append([item])
            continue
        previous_centers = [
            (
                float(value["bounds"]["x"])
                + float(value["bounds"]["width"]) / 2
            )
            / max(1, width)
            for value in bands[-1]
        ]
        if center - sum(previous_centers) / len(previous_centers) > gap:
            bands.append([item])
        else:
            bands[-1].append(item)
    return bands


def _single_body_intent_signature(
    representative: dict[str, object],
) -> tuple[object, ...]:
    """Classify one slide's reusable slots without drawing-fragment noise."""

    width = int(representative["canvasWidth"])
    height = int(representative["canvasHeight"])
    source_elements = representative.get("allElements", representative["content"])
    headline = representative.get("headline")
    content = [
        item
        for item in source_elements
        if not item.get("furniture")
        and item.get("inferredRole") != "title"
        and item is not headline
    ]
    text = [item for item in content if item["type"] == "text"]
    images = [item for item in content if item["type"] == "image"]
    shapes = [item for item in content if item["type"] == "shape"]

    def area(item: dict[str, object]) -> float:
        return (
            float(item["bounds"]["width"])
            * float(item["bounds"]["height"])
            / max(1, width * height)
        )

    image_area = sum(area(item) for item in images)
    significant_text = [
        item
        for item in text
        if area(item) >= 0.012
        and float(item["bounds"]["width"]) / max(1, width) < 0.80
    ]
    significant_shapes = [
        item
        for item in shapes
        if 0.02 <= area(item) <= 0.32
    ]
    prominent_text = [
        item
        for item in text
        if float(item.get("fontSize") or 0) >= 24
        and area(item) >= 0.01
        and float(item["bounds"]["width"]) / max(1, width) < 0.48
    ]
    columns = _coarse_bands(significant_text, width)
    headline_bounds = (
        representative["headline"]["bounds"]
        if representative.get("headline") is not None
        else None
    )
    headline_height = (
        float(headline_bounds["height"]) / max(1, height)
        if headline_bounds is not None
        else 0.0
    )
    headline_top = (
        float(headline_bounds["y"]) / max(1, height)
        if headline_bounds is not None
        else 1.0
    )
    large_regions = sum(area(item) >= 0.025 for item in content)

    # Resolve structural branches before the dense fallback. A comparison or
    # multi-column slide remains that intent even when it contains many labels.
    if images and (image_area >= 0.12 or max(area(item) for item in images) >= 0.10):
        intent = "imageLed"
    elif len(prominent_text) >= 2:
        intent = "kpiChart"
    elif headline_height >= 0.09 and headline_top <= 0.18:
        intent = "longTitle"
    elif (
        len(columns) >= 3
        or (len(significant_shapes) >= 3 and len(significant_text) >= 3)
    ):
        intent = "cards"
    elif len(columns) == 2:
        column_sizes = [len(column) for column in columns]
        intent = (
            "comparison"
            if min(column_sizes) >= 2
            and max(column_sizes) - min(column_sizes) <= 1
            else "twoColumn"
        )
    elif len(text) >= 20:
        intent = "cards" if large_regions >= 6 else "standard"
    else:
        intent = "standard"

    slots = _feature_slots(representative, "layout-candidate:intent")
    roles = tuple(sorted(Counter(slot.role for slot in slots).items()))
    column_sizes = tuple(sorted(min(4, len(column)) for column in columns))
    if intent == "cards":
        topology = ("cardBands", min(4, len(columns)))
    elif intent in {"comparison", "twoColumn"}:
        topology = ("columnBands", min(4, len(columns)), column_sizes)
    elif intent == "imageLed":
        topology = ("images", min(2, len(images)))
    elif intent == "kpiChart":
        topology = ("kpi", min(4, len(prominent_text)))
    elif intent == "longTitle":
        topology = (
            "longTitle",
            min(4, len(_coarse_bands(text, width, gap=0.18))),
        )
    else:
        topology = ("standard", min(4, len(columns)))
    return intent, roles, topology


def _body_intent_signature(
    features: list[dict[str, object]],
) -> tuple[object, ...]:
    """Aggregate every member's coarse slot topology deterministically."""

    signatures = tuple(
        sorted(
            {_single_body_intent_signature(feature) for feature in features},
            key=repr,
        )
    )
    if len(signatures) == 1:
        return signatures[0]
    return "mixed", (), signatures


def _body_macro_family_for_intent(intent: object) -> str:
    if intent == "imageLed":
        return "visual"
    if intent == "longTitle":
        return "longTitle"
    return "structured"


def _body_macro_family(features: list[dict[str, object]]) -> str:
    families = {
        _body_macro_family_for_intent(
            _single_body_intent_signature(feature)[0]
        )
        for feature in features
    }
    return next(iter(families)) if len(families) == 1 else "mixed"


def _logical_slot_distance(
    left: list[dict[str, object]],
    right: list[dict[str, object]],
    *,
    require_fine_topology: bool = True,
) -> float:
    return max(
        (
            _feature_logical_slot_distance(
                left_feature,
                right_feature,
                require_fine_topology=require_fine_topology,
            )
            for left_feature in left
            for right_feature in right
        ),
        default=0.0,
    )


def _feature_logical_slot_distance(
    left_feature: dict[str, object],
    right_feature: dict[str, object],
    *,
    require_fine_topology: bool = True,
) -> float:
    if (
        require_fine_topology
        and _single_body_intent_signature(left_feature)
        != _single_body_intent_signature(right_feature)
    ):
        return math.inf
    width = int(left_feature["canvasWidth"])
    height = int(left_feature["canvasHeight"])
    left_slots = _feature_slots(left_feature, "layout-candidate:left")
    right_slots = _feature_slots(right_feature, "layout-candidate:right")
    left_roles: dict[str, list[AiLayoutSlotEvidence]] = defaultdict(list)
    right_roles: dict[str, list[AiLayoutSlotEvidence]] = defaultdict(list)
    for slot in left_slots:
        left_roles[slot.role].append(slot)
    for slot in right_slots:
        right_roles[slot.role].append(slot)
    if {role: len(slots) for role, slots in left_roles.items()} != {
        role: len(slots) for role, slots in right_roles.items()
    }:
        return math.inf
    differences: list[float] = []
    for role in sorted(left_roles):
        left_ordered = sorted(
            left_roles[role], key=lambda slot: (slot.bounds.x, slot.bounds.y)
        )
        right_ordered = sorted(
            right_roles[role], key=lambda slot: (slot.bounds.x, slot.bounds.y)
        )
        for left_slot, right_slot in zip(left_ordered, right_ordered):
            differences.extend(
                [
                    abs(left_slot.bounds.x - right_slot.bounds.x) / max(1, width),
                    abs(left_slot.bounds.y - right_slot.bounds.y) / max(1, height),
                    abs(left_slot.bounds.width - right_slot.bounds.width)
                    / max(1, width),
                    abs(left_slot.bounds.height - right_slot.bounds.height)
                    / max(1, height),
                ]
            )
    return sum(differences) / max(1, len(differences))


def refresh_analysis_derived(analysis: AiMasterAnalysis) -> AiMasterAnalysis:
    low_confidence = [
        f"layout:{item.id}"
        for item in analysis.layout_candidates
        if item.confidence < 0.65
    ] + [
        f"element:{item.id}"
        for item in analysis.repeated_elements
        if item.confidence < 0.65
    ]
    return analysis.model_copy(
        update={
            "summary": analysis.summary.model_copy(
                update={
                    "layout_candidates": len(analysis.layout_candidates),
                    "low_confidence_count": len(low_confidence),
                }
            ),
            "low_confidence": low_confidence,
            "status": (
                "partial" if analysis.warnings or low_confidence else "completed"
            ),
        }
    )


def dedupe_layout_candidates(
    analysis: AiMasterAnalysis,
    feature_sidecar: AiFeatureSidecar,
) -> AiMasterAnalysis:
    """Deterministically collapse only unreviewed near-duplicate body layouts."""

    original_partition = _layout_partition(analysis.layout_candidates)
    expected_partition = Counter(range(1, analysis.source.slide_count + 1))
    if original_partition != expected_partition:
        raise AiImportError("authored_vnext_ai_master_layout_partition_invalid")
    if feature_sidecar.algorithm_version != AI_MASTER_FEATURE_ALGORITHM_VERSION:
        raise AiImportError(
            "authored_vnext_ai_master_feature_sidecar_version_mismatch"
        )
    if feature_sidecar.source_digest != analysis.source.digest:
        raise AiImportError(
            "authored_vnext_ai_master_feature_sidecar_source_digest_mismatch"
        )
    runtime_features = _runtime_features(feature_sidecar)
    if set(runtime_features) != set(original_partition):
        raise AiImportError("authored_vnext_ai_master_feature_sidecar_corrupt")

    threshold = analysis.tolerances.get(
        "autoDedupeVector",
        min(0.16, analysis.tolerances.get("layoutVector", LAYOUT_VECTOR_TOLERANCE) * 0.5),
    )
    macro_threshold = max(
        threshold,
        min(
            0.42,
            analysis.tolerances.get("layoutVector", LAYOUT_VECTOR_TOLERANCE)
            * 1.5,
        ),
    )
    layouts: list[AiLayoutCandidate] = []
    intent_partitioned_ids: set[str] = set()
    for candidate in sorted(
        analysis.layout_candidates, key=lambda item: (min(item.slide_numbers), item.id)
    ):
        if (
            candidate.excluded
            or not candidate.auto_dedupe_eligible
            or candidate.kind not in {"standard", "custom"}
        ):
            layouts.append(candidate)
            continue
        by_intent: dict[tuple[object, ...], list[int]] = defaultdict(list)
        for number in candidate.slide_numbers:
            feature = runtime_features[number]
            hint = str(feature["semanticHint"])
            key: tuple[object, ...] = (
                (hint, _body_macro_family([feature]))
                if hint == "body"
                else (hint,)
            )
            by_intent[key].append(number)
        if len(by_intent) == 1:
            layouts.append(candidate)
            continue
        intent_partitioned_ids.add(candidate.id)
        primary_key = next(
            key
            for key, numbers in by_intent.items()
            if candidate.representative_slide in numbers
        )
        source_ids = sorted(
            set(candidate.source_layout_ids or [candidate.id]) | {candidate.id}
        )
        for key, numbers in sorted(
            by_intent.items(), key=lambda item: (min(item[1]), repr(item[0]))
        ):
            intent_label = str(key[1]) if len(key) > 1 else str(key[0])
            partitioned, _ = _candidate_from_feature_members(
                [runtime_features[number] for number in sorted(numbers)],
                name=(
                    candidate.name
                    if key == primary_key
                    else f"{candidate.name} · {intent_label}"
                ),
                total_slides=analysis.source.slide_count,
                merge_threshold=analysis.tolerances.get(
                    "layoutVector", LAYOUT_VECTOR_TOLERANCE
                ),
                evidence=f"autoIntentPartition={candidate.id}",
                provenance="analysis",
                source_layout_ids=source_ids,
                auto_dedupe_eligible=True,
                forced_merge=False,
            )
            partitioned = partitioned.model_copy(
                update={
                    "kind": candidate.kind if key == primary_key else "custom",
                    "excluded": candidate.excluded,
                    "differences": [
                        *partitioned.differences,
                        f"autoIntent={intent_label}",
                    ],
                }
            )
            layouts.append(partitioned)
    groups: list[list[AiLayoutCandidate]] = [[item] for item in layouts]

    def group_evidence(
        group: list[AiLayoutCandidate],
    ) -> tuple[tuple[object, ...], list[dict[str, object]]] | None:
        if any(
            item.excluded
            or not item.auto_dedupe_eligible
            or item.kind not in {"standard", "custom"}
            for item in group
        ):
            return None
        features = [
            runtime_features[number]
            for item in group
            for number in item.slide_numbers
        ]
        if any(item["semanticHint"] != "body" for item in features):
            return None
        family = _body_macro_family(features)
        if family == "mixed":
            return None
        return (family,), features

    while True:
        eligible: list[
            tuple[float, tuple[int, ...], int, int]
        ] = []
        for left_index, left in enumerate(groups):
            left_evidence = group_evidence(left)
            if left_evidence is None:
                continue
            left_signature, _ = left_evidence
            for right_index in range(left_index + 1, len(groups)):
                right_evidence = group_evidence(groups[right_index])
                if right_evidence is None or right_evidence[0] != left_signature:
                    continue
                combined = [*left, *groups[right_index]]
                feature_groups = [
                    [runtime_features[number] for number in item.slide_numbers]
                    for item in combined
                ]
                diameter = max(
                    (
                        _logical_slot_distance(
                            first,
                            second,
                            require_fine_topology=False,
                        )
                        for index, first in enumerate(feature_groups)
                        for second in feature_groups[index + 1 :]
                    ),
                    default=0.0,
                )
                # A macro family is the final product-level layout boundary.
                # Distance remains quality evidence, but compatible logical
                # role schemas must not leak near-duplicate members back into
                # the registered master as separate layouts.
                if math.isfinite(diameter):
                    membership = tuple(
                        sorted(
                            number
                            for item in combined
                            for number in item.slide_numbers
                        )
                    )
                    eligible.append(
                        (diameter, membership, left_index, right_index)
                    )
        if not eligible:
            break
        _, _, left_index, right_index = min(eligible)
        groups[left_index] = [*groups[left_index], *groups[right_index]]
        groups.pop(right_index)

    deduped: list[AiLayoutCandidate] = []
    removed_ids: set[str] = set(intent_partitioned_ids)
    macro_distance_warning_ids: set[str] = set()
    for group in groups:
        if len(group) == 1:
            deduped.append(group[0])
            continue
        slide_numbers = sorted(
            number for item in group for number in item.slide_numbers
        )
        features = [runtime_features[number] for number in slide_numbers]
        feature_groups = [
            [runtime_features[number] for number in item.slide_numbers]
            for item in group
        ]
        distance = max(
            (
                _logical_slot_distance(
                    first,
                    second,
                    require_fine_topology=False,
                )
                for index, first in enumerate(feature_groups)
                for second in feature_groups[index + 1 :]
            ),
            default=0.0,
        )
        family = _body_macro_family(features)
        fine_signatures = sorted(
            {_single_body_intent_signature(feature) for feature in features},
            key=repr,
        )
        fine_intents = sorted({str(signature[0]) for signature in fine_signatures})
        fine_topologies = sorted({repr(signature[2]) for signature in fine_signatures})
        primary = next((item for item in group if item.kind == "standard"), group[0])
        source_ids = sorted(
            {
                source_id
                for item in group
                for source_id in (item.source_layout_ids or [item.id])
            }
        )
        merged, _ = _candidate_from_feature_members(
            features,
            name=primary.name,
            total_slides=analysis.source.slide_count,
            merge_threshold=analysis.tolerances.get(
                "layoutVector", LAYOUT_VECTOR_TOLERANCE
            ),
            evidence=f"autoDedupe={','.join(source_ids)}",
            provenance="autoDedupe",
            source_layout_ids=source_ids,
            auto_dedupe_eligible=True,
            forced_merge=False,
        )
        distance_exceeded = distance > macro_threshold
        confidence_penalty = (
            min(0.18, 0.06 + (distance - macro_threshold) * 0.45)
            if distance_exceeded
            else 0.0
        )
        merged = merged.model_copy(
            update={
                "kind": "standard" if any(item.kind == "standard" for item in group) else "custom",
                "confidence": round(
                    max(0.35, merged.confidence - confidence_penalty), 4
                ),
                "differences": [
                    *merged.differences,
                    f"autoDedupeDistance={distance:.4f}",
                    f"autoDedupeThreshold={macro_threshold:.4f}",
                    f"autoDedupeDistanceExceeded={str(distance_exceeded).lower()}",
                    f"autoDedupeConfidencePenalty={confidence_penalty:.4f}",
                    f"autoDedupeFamily={family}",
                    *(f"autoDedupeIntent={intent}" for intent in fine_intents),
                    *(
                        f"autoDedupeFineTopology={topology}"
                        for topology in fine_topologies
                    ),
                    "autoDedupeMetric=logicalSlotMeanAbsolute",
                    "autoDedupeLinkage=completeMemberSlots",
                    "autoDedupeMacroGuard=matched",
                    "autoDedupeTopologyGuard=fineEvidencePreserved",
                ],
            }
        )
        if distance_exceeded:
            macro_distance_warning_ids.add(merged.id)
        deduped.append(merged)
        removed_ids.update(item.id for item in group)

    if not removed_ids:
        return refresh_analysis_derived(analysis)
    if _layout_partition(deduped) != expected_partition:
        raise AiImportError("authored_vnext_ai_master_layout_partition_invalid")
    warnings = [
        warning
        for warning in analysis.warnings
        if not (
            warning.startswith("forcedMergeLowConfidence:")
            and warning.removeprefix("forcedMergeLowConfidence:") in removed_ids
        )
    ]
    for candidate_id in sorted(macro_distance_warning_ids):
        warning = f"autoDedupeMacroDistanceExceeded:{candidate_id}"
        if warning not in warnings:
            warnings.append(warning)
    updated = analysis.model_copy(
        update={
            "layout_candidates": sorted(
                deduped, key=lambda item: (min(item.slide_numbers), item.id)
            ),
            "warnings": warnings,
        }
    )
    return refresh_analysis_derived(updated)


def _split_layout_candidate(
    source: AiLayoutCandidate,
    slide_numbers: list[int],
    *,
    name: str,
    total_slides: int,
) -> AiLayoutCandidate:
    candidate_id = _stable_layout_candidate_id(slide_numbers)
    representative = _membership_representative(
        slide_numbers, [source.representative_slide]
    )
    support_ratio = len(slide_numbers) / max(1, len(source.slide_numbers))
    confidence = max(
        0.35,
        min(
            0.99,
            source.confidence
            + (1 - support_ratio) * 0.035
            - (0.06 if len(slide_numbers) == 1 else 0.0),
        ),
    )
    # Candidate summaries only retain the source medoid's slots. Reuse them
    # only for the side that still owns that medoid; the other side deliberately
    # falls back to generated safe slots rather than claiming stale geometry.
    rekeyed_slots = (
        [
            slot.model_copy(
                update={
                    "id": f"slot-evidence:{candidate_id.removeprefix('layout-candidate:')}-{index}"
                }
            )
            for index, slot in enumerate(source.slots)
        ]
        if representative == source.representative_slide
        else []
    )
    differences = [
        value
        for value in source.differences
        if not value.startswith("clusterSupport=")
        and not value.startswith("userSplit=")
    ]
    differences.extend(
        [
            f"clusterSupport={len(slide_numbers)}/{total_slides}",
            f"userSplit={source.id}",
            *(
                ["slotEvidencePending=true"]
                if representative != source.representative_slide
                else []
            ),
        ]
    )
    return source.model_copy(
        update={
            "id": candidate_id,
            "name": name,
            "representative_slide": representative,
            "slide_numbers": slide_numbers,
            "confidence": round(confidence, 4),
            "slots": rekeyed_slots,
            "differences": differences,
            "provenance": "userSplit",
            "source_layout_ids": sorted(
                set(source.source_layout_ids or [source.id]) | {source.id}
            ),
            "auto_dedupe_eligible": False,
        }
    )


def _layout_partition(layouts: Iterable[AiLayoutCandidate]) -> Counter[int]:
    return Counter(
        number for layout in layouts for number in layout.slide_numbers
    )


def apply_layout_actions(
    analysis: AiMasterAnalysis,
    actions: list[AiLayoutAction],
    feature_sidecar: AiFeatureSidecar | None = None,
) -> AiMasterAnalysis:
    layouts = list(analysis.layout_candidates)
    original_partition = _layout_partition(layouts)
    if original_partition != Counter(range(1, analysis.source.slide_count + 1)):
        raise AiImportError("authored_vnext_ai_master_layout_partition_invalid")
    runtime_features: dict[int, dict[str, object]] | None = None
    if feature_sidecar is not None:
        if feature_sidecar.algorithm_version != AI_MASTER_FEATURE_ALGORITHM_VERSION:
            raise AiImportError(
                "authored_vnext_ai_master_feature_sidecar_version_mismatch"
            )
        if feature_sidecar.source_digest != analysis.source.digest:
            raise AiImportError(
                "authored_vnext_ai_master_feature_sidecar_source_digest_mismatch"
            )
        runtime_features = _runtime_features(feature_sidecar)
        if set(runtime_features) != set(original_partition):
            raise AiImportError("authored_vnext_ai_master_feature_sidecar_corrupt")
    applied_actions: list[AiLayoutAction] = []
    warnings = list(analysis.warnings)
    for action in actions:
        selected = [item for item in layouts if item.id in action.layout_ids]
        if action.action in {"exclude", "include"}:
            excluded = action.action == "exclude"
            if (
                len(action.layout_ids) != len(set(action.layout_ids))
                or len(selected) != len(action.layout_ids)
                or not selected
            ):
                raise AiImportError("authored_vnext_ai_master_layout_action_invalid")
            if all(item.excluded == excluded for item in selected):
                raise AiImportError("authored_vnext_ai_master_layout_action_noop")
            layouts = [
                item.model_copy(
                    update={
                        "excluded": excluded,
                        "provenance": "userDecision",
                        "source_layout_ids": sorted(
                            set(item.source_layout_ids or [item.id]) | {item.id}
                        ),
                        "auto_dedupe_eligible": False,
                    }
                )
                if item.id in action.layout_ids
                else item
                for item in layouts
            ]
        elif action.action == "merge":
            if (
                len(action.layout_ids) != len(set(action.layout_ids))
                or len(selected) != len(action.layout_ids)
                or len(selected) < 2
            ):
                raise AiImportError("authored_vnext_ai_master_layout_action_invalid")
            slide_numbers = sorted(
                {number for item in selected for number in item.slide_numbers}
            )
            if runtime_features is not None:
                merged, forced = _candidate_from_feature_members(
                    [runtime_features[number] for number in slide_numbers],
                    name=action.name or selected[0].name,
                    total_slides=analysis.source.slide_count,
                    merge_threshold=analysis.tolerances.get(
                        "layoutVector", LAYOUT_VECTOR_TOLERANCE
                    ),
                    evidence=f"userMerged={','.join(sorted(item.id for item in selected))}",
                    provenance="userMerge",
                    source_layout_ids=(
                        source_id
                        for item in selected
                        for source_id in (item.source_layout_ids or [item.id])
                    ),
                    auto_dedupe_eligible=False,
                )
                merged = merged.model_copy(
                    update={"excluded": all(item.excluded for item in selected)}
                )
                if forced:
                    warning = f"forcedMergeLowConfidence:{merged.id}"
                    if warning not in warnings:
                        warnings.append(warning)
            else:
                candidate_id = _stable_layout_candidate_id(slide_numbers)
                representative = _membership_representative(
                    slide_numbers,
                    [item.representative_slide for item in selected],
                )
                weighted_confidence = sum(
                    item.confidence * len(item.slide_numbers) for item in selected
                ) / len(slide_numbers)
                spread = max(item.confidence for item in selected) - min(
                    item.confidence for item in selected
                )
                confidence = max(
                    0.35, min(0.99, weighted_confidence - spread * 0.2)
                )
                kinds = {item.kind for item in selected}
                kind = selected[0].kind if len(kinds) == 1 else "standard" if "standard" in kinds else "custom"
                layout_parts = {item.layout_part for item in selected}
                master_parts = {item.master_part for item in selected}
                theme_parts = {item.theme_part for item in selected}
                merged = selected[0].model_copy(update={
                    "id": candidate_id,
                    "name": action.name or selected[0].name,
                    "kind": kind,
                    "representative_slide": representative,
                    "slide_numbers": slide_numbers,
                    "confidence": round(confidence, 4),
                    "slots": _aggregate_layout_slots(selected, candidate_id),
                    "differences": [
                        f"userMerged={','.join(sorted(item.id for item in selected))}",
                        f"clusterSupport={len(slide_numbers)}/{analysis.source.slide_count}",
                        f"mergedEvidence={sum(len(item.slots) for item in selected)}slots",
                    ],
                    "layout_part": next(iter(layout_parts)) if len(layout_parts) == 1 else None,
                    "master_part": next(iter(master_parts)) if len(master_parts) == 1 else None,
                    "theme_part": next(iter(theme_parts)) if len(theme_parts) == 1 else None,
                    "provenance": "userMerge",
                    "source_layout_ids": sorted(
                        {
                            source_id
                            for item in selected
                            for source_id in (item.source_layout_ids or [item.id])
                        }
                    ),
                    "auto_dedupe_eligible": False,
                })
            layouts = [item for item in layouts if item.id not in action.layout_ids]
            layouts.append(merged)
        elif action.action == "split":
            if len(selected) != 1 or len(action.layout_ids) != 1:
                raise AiImportError("authored_vnext_ai_master_layout_action_invalid")
            source = selected[0]
            split_numbers = sorted(set(action.slide_numbers))
            if (
                not split_numbers
                or any(number not in source.slide_numbers for number in split_numbers)
                or len(split_numbers) == len(source.slide_numbers)
            ):
                raise AiImportError("authored_vnext_ai_master_layout_action_invalid")
            remaining = [number for number in source.slide_numbers if number not in split_numbers]
            layouts = [item for item in layouts if item.id != source.id]
            if runtime_features is not None:
                left, _ = _candidate_from_feature_members(
                    [runtime_features[number] for number in remaining],
                    name=source.name,
                    total_slides=analysis.source.slide_count,
                    merge_threshold=analysis.tolerances.get("layoutVector", LAYOUT_VECTOR_TOLERANCE),
                    evidence=f"userSplit={source.id}",
                    provenance="userSplit",
                    source_layout_ids=(source.source_layout_ids or [source.id]),
                    auto_dedupe_eligible=False,
                )
                right, _ = _candidate_from_feature_members(
                    [runtime_features[number] for number in split_numbers],
                    name=action.name or f"{source.name} 분리",
                    total_slides=analysis.source.slide_count,
                    merge_threshold=analysis.tolerances.get("layoutVector", LAYOUT_VECTOR_TOLERANCE),
                    evidence=f"userSplit={source.id}",
                    provenance="userSplit",
                    source_layout_ids=(source.source_layout_ids or [source.id]),
                    auto_dedupe_eligible=False,
                )
                layouts.extend(
                    [
                        left.model_copy(update={"excluded": source.excluded}),
                        right.model_copy(update={"excluded": source.excluded}),
                    ]
                )
            else:
                layouts.extend([
                    _split_layout_candidate(
                        source,
                        remaining,
                        name=source.name,
                        total_slides=analysis.source.slide_count,
                    ),
                    _split_layout_candidate(
                        source,
                        split_numbers,
                        name=action.name or f"{source.name} 분리",
                        total_slides=analysis.source.slide_count,
                    ),
                ])
        else:
            raise AiImportError("authored_vnext_ai_master_layout_action_invalid")
        if _layout_partition(layouts) != original_partition:
            raise AiImportError("authored_vnext_ai_master_layout_partition_invalid")
        applied_actions.append(action)
    if not applied_actions:
        return analysis
    layouts.sort(key=lambda item: (min(item.slide_numbers), item.id))
    return analysis.model_copy(update={"layout_candidates": layouts, "layout_actions": [*analysis.layout_actions, *applied_actions], "warnings": warnings})


class PptxMasterAnalyzer:
    def extract_feature_sidecar(
        self, pptx_bytes: bytes, analysis: AiMasterAnalysis
    ) -> AiFeatureSidecar:
        if _digest(pptx_bytes) != analysis.source.digest:
            raise AiImportError("authored_vnext_ai_master_source_digest_mismatch")
        package = _read_package(pptx_bytes)
        width, height = _canvas(package)
        slide_parts = _slide_parts(package)
        if (
            width != analysis.source.width_px
            or height != analysis.source.height_px
            or len(slide_parts) != analysis.source.slide_count
        ):
            raise AiImportError("authored_vnext_ai_master_source_digest_mismatch")
        slides: list[dict[str, object]] = []
        for number, part in enumerate(slide_parts, start=1):
            slide_rels = _rels(package, part)
            layout_part = next(
                (target for kind, target in slide_rels.values() if kind == "slideLayout"),
                None,
            )
            layout_rels = _rels(package, layout_part) if layout_part else {}
            master_part = next(
                (target for kind, target in layout_rels.values() if kind == "slideMaster"),
                None,
            )
            master_rels = _rels(package, master_part) if master_part else {}
            theme_part = next(
                (target for kind, target in master_rels.values() if kind == "theme"),
                None,
            )
            _, _, theme_map = _theme_values(package, theme_part)
            elements, _ = _slide_elements(
                package, part, number, width, height, theme_map, {}
            )
            slides.append(
                {
                    "slideNumber": number,
                    "part": part,
                    "layoutPart": layout_part,
                    "masterPart": master_part,
                    "themePart": theme_part,
                    "elements": elements,
                }
            )
        return _feature_sidecar_from_slides(
            source_digest=analysis.source.digest,
            slides=slides,
            width=width,
            height=height,
            geometry_tolerance_ratio=analysis.tolerances.get(
                "geometryRatio", GEOMETRY_TOLERANCE_RATIO
            ),
        )

    def analyze(
        self,
        pptx_bytes: bytes,
        filename: str,
        purpose: str,
        analysis_id: str | None = None,
        asset_publisher: Callable[[bytes], dict[str, object]] | None = None,
    ) -> AiMasterAnalysis:
        if purpose not in {"faithful", "cleanup", "rebrand"}:
            raise AiImportError("authored_vnext_ai_master_purpose_invalid")
        package = _read_package(pptx_bytes)
        width, height = _canvas(package)
        slide_parts = _slide_parts(package)
        if not slide_parts:
            raise AiImportError("authored_vnext_ai_master_slides_missing")
        source_assets: list[AiSourceAsset] = []
        published_assets: dict[str, dict[str, object]] = {}
        for part, value in sorted(package.items()):
            if not part.startswith("ppt/media/"):
                continue
            digest = _digest(value)
            media_type = _media_type(part)
            receipt = None
            if media_type == "image/png" and asset_publisher is not None:
                try:
                    receipt = asset_publisher(value)
                    published_assets[digest] = receipt
                except Exception:
                    receipt = None
            asset_id = str(receipt.get("assetId")) if receipt else f"asset:{digest[7:39]}"
            source_assets.append(AiSourceAsset(asset_id=asset_id, digest=digest, media_type=media_type, byte_length=len(value), part_name=part))
        slides: list[dict[str, object]] = []
        relationships: list[AiSourceRelationship] = []
        all_theme_fonts: list[str] = []
        all_theme_colors: list[str] = []
        for number, part in enumerate(slide_parts, start=1):
            slide_rels = _rels(package, part)
            layout_part = next((target for kind, target in slide_rels.values() if kind == "slideLayout"), None)
            layout_rels = _rels(package, layout_part) if layout_part else {}
            master_part = next((target for kind, target in layout_rels.values() if kind == "slideMaster"), None)
            master_rels = _rels(package, master_part) if master_part else {}
            theme_part = next((target for kind, target in master_rels.values() if kind == "theme"), None)
            theme_fonts, theme_colors, theme_map = _theme_values(package, theme_part)
            all_theme_fonts.extend(theme_fonts)
            all_theme_colors.extend(theme_colors)
            elements, element_relationships = _slide_elements(package, part, number, width, height, theme_map, published_assets)
            relationships.extend(element_relationships)
            slides.append({"slideNumber": number, "part": part, "layoutPart": layout_part, "masterPart": master_part, "themePart": theme_part, "elements": elements})
        font_counts = Counter(str(item.get("fontFamily")) for slide in slides for item in slide["elements"] if item.get("fontFamily"))
        font_counts.update(all_theme_fonts)
        color_counts = Counter(str(item.get("color") or item.get("fill")) for slide in slides for item in slide["elements"] if item.get("color") or item.get("fill"))
        color_counts.update(all_theme_colors)
        if not font_counts:
            font_counts["Malgun Gothic"] = 1
        if not color_counts:
            color_counts.update({"#FFFFFF": 1, "#172033": 1, "#356AE6": 1})
        fonts = [AiFontEvidence(family=value, count=count, source="slide" if any(item.get("fontFamily") == value for slide in slides for item in slide["elements"]) else "theme") for value, count in font_counts.most_common()]
        colors = [AiColorEvidence(hex=value.upper(), count=count, source="slide" if any((item.get("color") or item.get("fill")) == value for slide in slides for item in slide["elements"]) else "theme") for value, count in color_counts.most_common() if re.fullmatch(r"#[0-9A-Fa-f]{6}", value)]
        purpose_tolerances = {
            # Complete-link distances operate on the normalized spatial
            # signature above. Faithful retains more direct-placement
            # variants; cleanup deliberately absorbs near-duplicate cards;
            # rebrand sits between them while preserving layout geometry.
            "faithful": {
                "geometry": 0.004,
                "layout": 0.24,
                "autoDedupe": 0.11,
                "repeat": 0.50,
            },
            "cleanup": {
                "geometry": 0.0125,
                "layout": 0.32,
                "autoDedupe": 0.16,
                "repeat": 0.40,
            },
            "rebrand": {
                "geometry": 0.0075,
                "layout": 0.28,
                "autoDedupe": 0.14,
                "repeat": 0.45,
            },
        }[purpose]
        layouts = _cluster_layouts(
            slides,
            width,
            height,
            purpose_tolerances["layout"],
            purpose_tolerances["geometry"],
        )
        repeated = _repeat_candidates(
            slides,
            width,
            height,
            purpose_tolerances["geometry"],
            purpose_tolerances["repeat"],
        )
        decisions = [AiCandidateDecision(candidate_id=item.id) for item in repeated]
        warnings: list[str] = []
        low_confidence = [f"layout:{item.id}" for item in layouts if item.confidence < 0.65] + [f"element:{item.id}" for item in repeated if item.confidence < 0.65]
        unsupported = [item for item in source_assets if item.media_type not in {"image/png", "image/jpeg", "image/svg+xml"}]
        if unsupported:
            warnings.append("unsupportedMediaPreservedAsEvidence")
        if purpose == "rebrand":
            warnings.append("semanticBrandFallback")
        status = "partial" if warnings or low_confidence else "completed"
        identifier = analysis_id or uuid4().hex
        placeholder_contract = DeckMasterContract.model_validate(_base_contract(master_id=f"master:ai-{identifier[:16]}", name=f"{Path(filename).stem} AI Master", colors={"background": "#FFFFFF", "surface": "#F5F8FC", "textPrimary": "#172033", "textSecondary": "#5F6B7C", "accent": "#356AE6", "divider": "#D7DCE5", "warning": "#D64550"}, header="", callout=""))
        summary = AiAnalysisSummary(
            design_families=len(
                {_design_family_key(slide, width, height) for slide in slides}
            ),
            layout_candidates=len(layouts),
            font_count=len(fonts),
            color_count=len(colors),
            repeated_element_count=len(repeated),
            ai_zone_candidate_count=sum(item.ai_zone_candidate for item in repeated),
            low_confidence_count=len(low_confidence),
        )
        analysis = AiMasterAnalysis(
            analysis_id=identifier,
            status=status,
            purpose=purpose,
            algorithm_version=AI_MASTER_FEATURE_ALGORITHM_VERSION,
            feature_sidecar_status="ready",
            source=AiAnalysisSource(filename=Path(filename).name or "source.pptx", digest=_digest(pptx_bytes), slide_count=len(slides), width_px=width, height_px=height, assets=source_assets, asset_relationships=relationships),
            summary=summary,
            fonts=fonts,
            colors=colors,
            repeated_elements=repeated,
            layout_candidates=layouts,
            decisions=decisions,
            warnings=warnings,
            low_confidence=low_confidence,
            tolerances={"geometryRatio": purpose_tolerances["geometry"], "rotationDegrees": ROTATION_TOLERANCE_DEGREES, "fontSizePt": FONT_SIZE_TOLERANCE_PT, "layoutVector": purpose_tolerances["layout"], "autoDedupeVector": purpose_tolerances["autoDedupe"], "repeatCoverage": purpose_tolerances["repeat"]},
            contract_draft=placeholder_contract,
        )
        feature_sidecar = _feature_sidecar_from_slides(
            source_digest=analysis.source.digest,
            slides=slides,
            width=width,
            height=height,
            geometry_tolerance_ratio=purpose_tolerances["geometry"],
        )
        analysis = dedupe_layout_candidates(analysis, feature_sidecar)
        contract = build_contract_draft(analysis)
        return analysis.model_copy(update={"contract_draft": contract})


class AiMasterAnalysisStore:
    """Owner-bound, local durable storage for source bytes and review state."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _directory(self, analysis_id: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{32}", analysis_id):
            raise AiImportError("authored_vnext_ai_master_analysis_id_invalid")
        return self.root / analysis_id

    def save(
        self,
        analysis: AiMasterAnalysis,
        source: bytes | None = None,
        media: dict[str, bytes] | None = None,
        feature_sidecar: AiFeatureSidecar | None = None,
    ) -> None:
        directory = self._directory(analysis.analysis_id)
        directory.mkdir(parents=True, exist_ok=True)
        if source is not None:
            if _digest(source) != analysis.source.digest:
                raise AiImportError("authored_vnext_ai_master_source_digest_mismatch")
            self._atomic_bytes(directory / "source.pptx", source)
            if feature_sidecar is None:
                feature_sidecar = PptxMasterAnalyzer().extract_feature_sidecar(
                    source, analysis
                )
        if feature_sidecar is not None:
            self._validate_feature_sidecar(analysis, feature_sidecar)
            self._atomic_bytes(
                directory / "feature-sidecar.json",
                feature_sidecar.model_dump_json(
                    by_alias=True, exclude_none=True, indent=2
                ).encode("utf-8"),
            )
        if media:
            media_directory = directory / "media"
            media_directory.mkdir(parents=True, exist_ok=True)
            for digest, value in media.items():
                if _digest(value) != digest:
                    raise AiImportError("authored_vnext_ai_master_asset_digest_mismatch")
                self._atomic_bytes(media_directory / digest.removeprefix("sha256:"), value)
        payload = analysis.model_dump_json(by_alias=True, exclude_none=True, indent=2).encode("utf-8")
        self._atomic_bytes(directory / "analysis.json", payload)

    def get(self, analysis_id: str) -> AiMasterAnalysis:
        path = self._directory(analysis_id) / "analysis.json"
        if not path.is_file():
            raise AiImportError("authored_vnext_ai_master_analysis_not_found")
        try:
            analysis = AiMasterAnalysis.model_validate_json(path.read_bytes())
        except (OSError, ValueError) as error:
            raise AiImportError("authored_vnext_ai_master_analysis_corrupt") from error
        try:
            self.feature_sidecar(analysis_id, analysis=analysis)
        except AiImportError:
            return analysis.model_copy(
                update={"feature_sidecar_status": "missing"}
            )
        if analysis.algorithm_version != AI_MASTER_FEATURE_ALGORITHM_VERSION:
            return analysis.model_copy(
                update={
                    "algorithm_version": AI_MASTER_FEATURE_ALGORITHM_VERSION,
                    "feature_sidecar_status": "ready",
                }
            )
        return analysis

    @staticmethod
    def _validate_feature_sidecar(
        analysis: AiMasterAnalysis, sidecar: AiFeatureSidecar
    ) -> None:
        if sidecar.algorithm_version != AI_MASTER_FEATURE_ALGORITHM_VERSION:
            raise AiImportError(
                "authored_vnext_ai_master_feature_sidecar_version_mismatch"
            )
        if sidecar.source_digest != analysis.source.digest:
            raise AiImportError(
                "authored_vnext_ai_master_feature_sidecar_source_digest_mismatch"
            )
        if (
            sidecar.slide_count != analysis.source.slide_count
            or len(sidecar.slides) != analysis.source.slide_count
            or [item.slide_number for item in sidecar.slides]
            != list(range(1, analysis.source.slide_count + 1))
        ):
            raise AiImportError("authored_vnext_ai_master_feature_sidecar_corrupt")

    def feature_sidecar(
        self,
        analysis_id: str,
        *,
        analysis: AiMasterAnalysis | None = None,
    ) -> AiFeatureSidecar:
        current = analysis or self.get(analysis_id)
        path = self._directory(analysis_id) / "feature-sidecar.json"
        if not path.is_file():
            raise AiImportError("authored_vnext_ai_master_feature_sidecar_missing")
        try:
            sidecar = AiFeatureSidecar.model_validate_json(path.read_bytes())
        except (OSError, ValueError) as error:
            raise AiImportError(
                "authored_vnext_ai_master_feature_sidecar_corrupt"
            ) from error
        self._validate_feature_sidecar(current, sidecar)
        return sidecar

    def ensure_feature_sidecar(
        self, analysis_id: str
    ) -> tuple[AiFeatureSidecar, str | None]:
        analysis = self.get(analysis_id)
        try:
            return self.feature_sidecar(analysis_id, analysis=analysis), None
        except AiImportError as error:
            reasons = {
                "authored_vnext_ai_master_feature_sidecar_missing": "missing",
                "authored_vnext_ai_master_feature_sidecar_corrupt": "corrupt",
                "authored_vnext_ai_master_feature_sidecar_version_mismatch": "versionMismatch",
                "authored_vnext_ai_master_feature_sidecar_source_digest_mismatch": "sourceDigestMismatch",
            }
            reason = reasons.get(error.code)
            if reason is None:
                raise
        sidecar = PptxMasterAnalyzer().extract_feature_sidecar(
            self.source(analysis_id), analysis
        )
        self._atomic_bytes(
            self._directory(analysis_id) / "feature-sidecar.json",
            sidecar.model_dump_json(
                by_alias=True, exclude_none=True, indent=2
            ).encode("utf-8"),
        )
        return sidecar, reason

    def source(self, analysis_id: str) -> bytes:
        path = self._directory(analysis_id) / "source.pptx"
        if not path.is_file():
            raise AiImportError("authored_vnext_ai_master_source_not_found")
        value = path.read_bytes()
        if _digest(value) != self.get(analysis_id).source.digest:
            raise AiImportError("authored_vnext_ai_master_source_digest_mismatch")
        return value

    @staticmethod
    def _atomic_bytes(path: Path, value: bytes) -> None:
        temporary = path.parent / f".{path.name}.{uuid4().hex}.candidate"
        try:
            with temporary.open("xb") as handle:
                handle.write(value)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def package_media(pptx_bytes: bytes) -> dict[str, bytes]:
    return {_digest(value): value for part, value in _read_package(pptx_bytes).items() if part.startswith("ppt/media/")}


__all__ = [
    "AI_MASTER_FEATURE_ALGORITHM_VERSION",
    "AiImportError",
    "AiMasterAnalysisStore",
    "PptxMasterAnalyzer",
    "apply_layout_actions",
    "build_contract_draft",
    "dedupe_layout_candidates",
    "package_media",
    "refresh_analysis_derived",
    "validate_reapply",
]
