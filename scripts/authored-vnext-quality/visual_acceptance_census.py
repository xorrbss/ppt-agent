"""Fail-closed visual acceptance census for legacy Authored vs authored-vNext.

The command consumes two *real exported PPTX files*, rendered slide PNGs, the
same-content manifest, and the authored-vNext Scene IR used for export.  It
does not create a deck or infer quality from a generation response.  A report
is written even when the gate fails so that missing native capabilities and
visual defects remain actionable evidence.

Example (after the official authored-vNext stack has produced both exports and
the PowerPoint render directories)::

    uv run --directory servers/fastapi python \
      scripts/authored-vnext-quality/visual_acceptance_census.py \
      --legacy-pptx artifacts/quality/legacy.pptx \
      --vnext-pptx artifacts/quality/vnext.pptx \
      --content-manifest scripts/authored-vnext-quality/representative_deck_manifest.json \
      --vnext-scene artifacts/quality/vnext-scene.json \
      --legacy-html-dir artifacts/quality/legacy-html \
      --legacy-render-dir artifacts/quality/legacy-render \
      --vnext-render-dir artifacts/quality/vnext-render \
      --output artifacts/quality/visual-acceptance-report.json

The exit code is non-zero for missing artifacts, invalid readback, clipping,
overlap, low contrast, content drift, or an undeclared/missing native
capability.  No fallback to raster/template-only evidence is permitted.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import hashlib
import html
import json
import math
from pathlib import Path
import re
import struct
import sys
from typing import Any, Iterable
from zipfile import BadZipFile, ZipFile
from xml.etree import ElementTree as ET


SCHEMA_VERSION = "authored-vnext-visual-acceptance/v1"
SCENE_SCHEMA_VERSION = "deck-scene-ir/v1"
REQUIRED_PATTERNS = frozenset(
    {"card", "process", "timeline", "comparison", "metric", "infographic"}
)
REQUIRED_LANGUAGES = frozenset({"ko", "zh", "ja"})
SUPPORTED_SCENE_KINDS = frozenset({"text", "shape", "image", "table", "chart"})
_NS = {
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
    "vt": "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes",
}
_EMU_RE = re.compile(r"^\d+$")
_CJK_RE = re.compile(r"[\u1100-\u11ff\u2e80-\u2fff\u3000-\u303f\u3040-\u30ff\u3130-\u318f\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_KO_RE = re.compile(r"[\uac00-\ud7af]")
_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")


def _identity_digest(value: Any) -> str:
    """Digest only stable identity evidence, never timestamps or file paths."""

    return _canonical_digest(value)


class CensusFailure(RuntimeError):
    """Raised only for command/setup failures; quality defects are reported."""


@dataclass(frozen=True)
class Box:
    slide: int
    part: str
    kind: str
    element_id: str | None
    description: str | None
    x: float
    y: float
    width: float
    height: float

    @property
    def area(self) -> float:
        return self.width * self.height


def _canonical_digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _read_json(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise CensusFailure(f"{label}_missing:{path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CensusFailure(f"{label}_invalid:{path}") from exc
    if not isinstance(value, dict):
        raise CensusFailure(f"{label}_object_required:{path}")
    return value


def _parse_color(value: str | None) -> str | None:
    if not value or not re.fullmatch(r"[0-9a-fA-F]{6}", value):
        return None
    return "#" + value.upper()


def _luminance(color: str) -> float:
    channels = [int(color[offset : offset + 2], 16) / 255 for offset in (1, 3, 5)]
    linear = [
        channel / 12.92
        if channel <= 0.04045
        else ((channel + 0.055) / 1.055) ** 2.4
        for channel in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(foreground: str, background: str) -> float:
    left, right = _luminance(foreground), _luminance(background)
    return (max(left, right) + 0.05) / (min(left, right) + 0.05)


def _attr_int(element: ET.Element | None, name: str) -> int | None:
    if element is None:
        return None
    value = element.attrib.get(name)
    if value is None or not _EMU_RE.fullmatch(value):
        return None
    return int(value)


def _shape_box(node: ET.Element, slide_number: int, part: str, kind: str) -> Box | None:
    xfrm = node.find(".//a:xfrm", _NS)
    if xfrm is None:
        xfrm = node.find("p:xfrm", _NS)
    off, ext = (xfrm.find("a:off", _NS), xfrm.find("a:ext", _NS)) if xfrm is not None else (None, None)
    x, y = (_attr_int(off, key) for key in ("x", "y"))
    width, height = (_attr_int(ext, key) for key in ("cx", "cy"))
    if None in {x, y, width, height} or width <= 0 or height <= 0:
        return None
    c_nv_pr = node.find("p:nvSpPr/p:cNvPr", _NS)
    if c_nv_pr is None:
        c_nv_pr = node.find("p:nvPicPr/p:cNvPr", _NS)
    if c_nv_pr is None:
        c_nv_pr = node.find("p:nvGraphicFramePr/p:cNvPr", _NS)
    if c_nv_pr is None:
        c_nv_pr = node.find("p:nvCxnSpPr/p:cNvPr", _NS)
    element_id = c_nv_pr.attrib.get("id") if c_nv_pr is not None else None
    description = c_nv_pr.attrib.get("descr") if c_nv_pr is not None else None
    return Box(slide_number, part, kind, element_id, description, x, y, width, height)


def _text(node: ET.Element) -> str:
    return "".join(item.text or "" for item in node.findall(".//a:t", _NS))


def _first_color(node: ET.Element, xpath: str) -> str | None:
    item = node.find(xpath, _NS)
    return _parse_color(item.attrib.get("val") if item is not None else None)


def _read_custom_properties(zf: ZipFile) -> dict[str, str]:
    try:
        root = ET.fromstring(zf.read("docProps/custom.xml"))
    except (KeyError, ET.ParseError) as exc:
        raise CensusFailure("vnext_custom_properties_missing_or_invalid") from exc
    result: dict[str, str] = {}
    for prop in root:
        name = prop.attrib.get("name")
        if not name or len(prop) != 1:
            continue
        result[name] = "".join(prop[0].itertext())
    return result


def _parse_scene_metadata(description: str | None) -> dict[str, Any] | None:
    if not description or not description.startswith("authored-vnext:"):
        return None
    try:
        value = json.loads(description.removeprefix("authored-vnext:"))
    except json.JSONDecodeError as exc:
        raise CensusFailure("vnext_scene_element_metadata_invalid") from exc
    if not isinstance(value, dict):
        raise CensusFailure("vnext_scene_element_metadata_object_required")
    return value


def _parse_slide(zf: ZipFile, part: str, number: int, canvas: tuple[int, int]) -> dict[str, Any]:
    try:
        root = ET.fromstring(zf.read(part))
    except (KeyError, ET.ParseError) as exc:
        raise CensusFailure(f"pptx_slide_invalid:{part}") from exc
    boxes: list[Box] = []
    texts: list[str] = []
    scene_elements: list[dict[str, Any]] = []
    native_counts: Counter[str] = Counter()
    visual_checks: list[dict[str, Any]] = []
    xml_order = {id(node): order for order, node in enumerate(root.iter())}
    ordered_scene_elements: list[tuple[int, dict[str, Any]]] = []
    for tag, kind in (("p:sp", "shape"), ("p:pic", "image"), ("p:graphicFrame", "graphicFrame"), ("p:cxnSp", "connector")):
        for node in root.findall(f".//{tag}", _NS):
            box = _shape_box(node, number, part, kind)
            if box is not None:
                boxes.append(box)
                if box.x < 0 or box.y < 0 or box.x + box.width > canvas[0] or box.y + box.height > canvas[1]:
                    visual_checks.append({"kind": "clipping", "part": part, "elementId": box.element_id})
            text = _text(node)
            if text:
                texts.append(text)
            metadata = _parse_scene_metadata(box.description if box else None)
            if metadata is not None:
                element_kind = metadata.get("kind")
                element_id = metadata.get("elementId")
                if not isinstance(element_id, str) or element_kind not in SUPPORTED_SCENE_KINDS:
                    raise CensusFailure("vnext_scene_element_identity_invalid")
                scene_elements.append({"elementId": element_id, "kind": element_kind, "text": text})
                # graphicFrame table/chart identity is counted from its native
                # graphicData URI below so one OOXML object is never counted
                # twice when it also carries Scene metadata.
                if element_kind not in {"table", "chart"}:
                    native_counts[element_kind] += 1
                ordered_scene_elements.append(
                    (xml_order.get(id(node), len(xml_order)), {"elementId": element_id, "kind": element_kind})
                )
            if tag == "p:graphicFrame":
                graphic = node.find(".//a:graphicData", _NS)
                uri = graphic.attrib.get("uri", "") if graphic is not None else ""
                if "table" in uri:
                    native_counts["table"] += 1
                elif "chart" in uri:
                    native_counts["chart"] += 1
    # A slide canvas/background is intentionally behind all body content and
    # is excluded from overlap census. Furniture is also excluded because the
    # body-vs-furniture boundary is checked through Scene metadata/readback.
    foreground = [
        box
        for box in boxes
        if box.description and not box.description.startswith("authored-furniture:")
        and not (box.x == 0 and box.y == 0 and box.width >= canvas[0] and box.height >= canvas[1])
        and box.kind != "connector"
    ]
    for index, left in enumerate(foreground):
        for right in foreground[index + 1 :]:
            overlap_width = max(0, min(left.x + left.width, right.x + right.width) - max(left.x, right.x))
            overlap_height = max(0, min(left.y + left.height, right.y + right.height) - max(left.y, right.y))
            overlap = overlap_width * overlap_height
            overlap_ratio = overlap / min(left.area, right.area) if overlap else 0
            left_metadata = _parse_scene_metadata(left.description)
            right_metadata = _parse_scene_metadata(right.description)
            metadata_kinds = {
                item.get("kind")
                for item in (left_metadata, right_metadata)
                if isinstance(item, dict)
            }
            semantic_roles = {
                str(item.get("semanticRole") or "").lower()
                for item in (left_metadata, right_metadata)
                if isinstance(item, dict)
            }
            if overlap_ratio >= 0.9 and metadata_kinds == {"shape", "text"}:
                # Native panel + contained copy is an intentional authored
                # composition, not a collision between peer content blocks.
                continue
            if metadata_kinds == {"shape"} and any(
                role.endswith(("accent", "axis", "marker", "hub", "spine"))
                for role in semantic_roles
            ):
                # Accents, process rails, and numbered markers are deliberately
                # layered with their owning native panel.
                continue
            if overlap_ratio >= 0.9 and "nativeobjectframe" in semantic_roles:
                # A verified image/table/chart deliberately occupies its
                # authored native-object frame.  The explicit Scene role keeps
                # this exemption narrower than generic shape containment.
                continue
            if overlap_ratio >= 0.2:
                visual_checks.append({"kind": "overlap", "part": part, "left": left.element_id, "right": right.element_id})
    background = _first_color(root, "p:cSld/p:bg//a:srgbClr") or "#FFFFFF"
    for node in root.findall(".//p:sp", _NS):
        text = _text(node)
        if not text:
            continue
        foreground_color = _first_color(node, ".//a:rPr/a:solidFill/a:srgbClr") or _first_color(node, ".//a:defRPr/a:solidFill/a:srgbClr")
        shape_background = _first_color(node, "p:spPr/a:solidFill/a:srgbClr") or background
        if foreground_color is None:
            visual_checks.append({"kind": "contrast-unknown", "part": part, "text": text[:80]})
        else:
            ratio = _contrast(foreground_color, shape_background)
            if ratio < 4.5:
                visual_checks.append({"kind": "low-contrast", "part": part, "ratio": round(ratio, 3), "text": text[:80]})
    return {
        "part": part,
        "texts": texts,
        "boxes": [box.__dict__ for box in boxes],
        "sceneElements": scene_elements,
        "sceneIdentityElements": [item for _, item in sorted(ordered_scene_elements, key=lambda pair: pair[0])],
        "nativeObjectCounts": dict(native_counts),
        "visualChecks": visual_checks,
    }


def inspect_pptx(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise CensusFailure(f"pptx_missing:{path}")
    try:
        zf = ZipFile(path)
        names = set(zf.namelist())
    except (OSError, BadZipFile) as exc:
        raise CensusFailure(f"pptx_invalid:{path}") from exc
    if "ppt/presentation.xml" not in names:
        raise CensusFailure(f"pptx_presentation_part_missing:{path}")
    presentation = ET.fromstring(zf.read("ppt/presentation.xml"))
    size = presentation.find("p:sldSz", _NS)
    canvas = (_attr_int(size, "cx"), _attr_int(size, "cy")) if size is not None else (None, None)
    if None in canvas or not all(value > 0 for value in canvas):
        raise CensusFailure(f"pptx_canvas_missing:{path}")
    slide_parts = sorted((name for name in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)), key=lambda item: int(re.search(r"(\d+)", item).group(1)))
    if not slide_parts:
        raise CensusFailure(f"pptx_slides_missing:{path}")
    slides = [_parse_slide(zf, part, index, canvas) for index, part in enumerate(slide_parts, start=1)]
    native_counts: Counter[str] = Counter()
    all_text: list[str] = []
    visual_checks: list[dict[str, Any]] = []
    scene_elements: list[dict[str, Any]] = []
    for slide in slides:
        native_counts.update(slide["nativeObjectCounts"])
        all_text.extend(slide["texts"])
        visual_checks.extend(slide["visualChecks"])
        scene_elements.extend(slide["sceneElements"])
    custom = _read_custom_properties(zf) if "docProps/custom.xml" in names else {}
    scene_identity = [
        {
            "slide": slide_number,
            "elements": [
                {"elementId": item["elementId"], "kind": item["kind"]}
                for item in slides[slide_number - 1]["sceneIdentityElements"]
            ],
        }
        for slide_number in range(1, len(slides) + 1)
    ]
    return {
        "path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "slideCount": len(slides),
        "canvasEmu": {"width": canvas[0], "height": canvas[1]},
        "slides": slides,
        "texts": all_text,
        "textCorpus": "\n".join(all_text),
        "nativeObjectCounts": dict(native_counts),
        "sceneElements": scene_elements,
        "sceneIdentityDigest": _identity_digest(scene_identity),
        "sceneIdentity": scene_identity,
        "customProperties": custom,
        "visualChecks": visual_checks,
    }


def inspect_rendered_slides(directory: Path, slide_count: int) -> dict[str, Any]:
    if not directory.is_dir():
        raise CensusFailure(f"render_directory_missing:{directory}")
    try:
        from PIL import Image, ImageStat
    except ImportError as exc:
        raise CensusFailure("pillow_required_for_rendered_visual_inspection") from exc
    # Chromium helper output uses slide-1.png; PowerPoint COM on a Korean
    # Windows install uses 슬라이드1.PNG.  Both are real raster evidence and
    # are ordered by the numeric suffix, while a mixed/duplicated set remains
    # fail-closed below rather than silently choosing one renderer's output.
    # Windows path matching is case-insensitive, so one glob covers both
    # .PNG and .png.  Keep both naming families in the candidate set: a mixed
    # Chromium/COM directory with the same numeric slide twice must fail
    # closed instead of silently preferring one renderer.
    files = list(directory.glob("slide-*.png")) + list(directory.glob("슬라이드*.png"))
    files = sorted(files, key=lambda item: int(re.search(r"(\d+)", item.stem).group(1)))
    indices = [int(re.search(r"(\d+)", item.stem).group(1)) for item in files]
    if len(indices) != len(set(indices)):
        raise CensusFailure(f"rendered_slide_duplicate_indices:{directory}")
    if len(files) != slide_count:
        raise CensusFailure(f"rendered_slide_count_mismatch:{directory}:{len(files)}!={slide_count}")
    evidence = []
    for index, path in enumerate(files, start=1):
        try:
            with Image.open(path) as image:
                image.load()
                rgb = image.convert("RGB")
                width, height = rgb.size
                variance = sum(ImageStat.Stat(rgb).var) / 3
                # Use a fixed thumbnail so the evidence is deterministic across
                # PowerPoint/Chrome render resolutions.  The dominant 16-level
                # color bucket is a conservative background estimate; ink and
                # edge ratios are descriptive evidence, never a quality score
                # on their own.
                thumb = rgb.resize((160, 90))
                pixels = list(thumb.getdata())
                buckets = Counter((r // 16, g // 16, b // 16) for r, g, b in pixels)
                dominant = buckets.most_common(1)[0][0]
                background = tuple(channel * 16 + 8 for channel in dominant)
                ink_ratio = sum(
                    1
                    for red, green, blue in pixels
                    if math.sqrt(
                        (red - background[0]) ** 2
                        + (green - background[1]) ** 2
                        + (blue - background[2]) ** 2
                    )
                    > 22
                ) / len(pixels)
                luminance = [
                    0.2126 * red + 0.7152 * green + 0.0722 * blue
                    for red, green, blue in pixels
                ]
                horizontal_edges = sum(
                    abs(luminance[row * 160 + column + 1] - luminance[row * 160 + column]) > 12
                    for row in range(90)
                    for column in range(159)
                )
                vertical_edges = sum(
                    abs(luminance[(row + 1) * 160 + column] - luminance[row * 160 + column]) > 12
                    for row in range(89)
                    for column in range(160)
                )
                edge_density = (horizontal_edges + vertical_edges) / (159 * 90 + 160 * 89)
        except Exception as exc:  # Pillow raises several format-specific errors.
            raise CensusFailure(f"rendered_slide_invalid:{path}") from exc
        if width < 640 or height < 360 or variance < 0.5:
            raise CensusFailure(f"rendered_slide_not_inspectable:{path}")
        evidence.append(
            {
                "slide": index,
                "path": str(path),
                "sha256": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
                "width": width,
                "height": height,
                "pixelVariance": round(variance, 3),
                "inkRatio": round(ink_ratio, 6),
                "whitespaceRatio": round(1 - ink_ratio, 6),
                "edgeDensity": round(edge_density, 6),
            }
        )
    return {"directory": str(directory), "slideCount": slide_count, "slides": evidence}


def inspect_legacy_html(directory: Path, slide_count: int) -> dict[str, Any]:
    """Bind a raster-only legacy PPTX to its exact Authored HTML source text."""

    paths = [directory / f"slide-{index}.html" for index in range(1, slide_count + 1)]
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise CensusFailure(f"legacy_html_missing:{missing[0]}")
    corpus: list[str] = []
    per_slide: list[str] = []
    for path in paths:
        value = path.read_text(encoding="utf-8")
        value = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", " ", value, flags=re.I | re.S)
        text = html.unescape(re.sub(r"<[^>]+>", " ", value))
        per_slide.append(text)
        corpus.append(text)
    return {
        "directory": str(directory),
        "slideCount": slide_count,
        "textCorpus": " ".join(corpus),
        "textCorpusBySlide": per_slide,
        "textIdentityDigest": _identity_digest(per_slide),
    }


def _manifest_checks(manifest: dict[str, Any], decks: Iterable[dict[str, Any]]) -> list[str]:
    issues: list[str] = []
    if manifest.get("schemaVersion") != SCHEMA_VERSION:
        issues.append("manifest_schema_unsupported")
    slides = manifest.get("slides")
    if not isinstance(slides, list) or not slides:
        issues.append("manifest_slides_required")
        return issues
    patterns: set[str] = set()
    languages: set[str] = set()
    required_text: list[str] = []
    for item in slides:
        if not isinstance(item, dict):
            issues.append("manifest_slide_invalid")
            continue
        patterns.update(value for value in item.get("patterns", []) if isinstance(value, str))
        language = item.get("language")
        if isinstance(language, str):
            languages.add(language)
        required_text.extend(value for value in item.get("requiredText", []) if isinstance(value, str) and value)
    issues.extend(f"manifest_pattern_missing:{pattern}" for pattern in sorted(REQUIRED_PATTERNS - patterns))
    issues.extend(f"manifest_language_missing:{language}" for language in sorted(REQUIRED_LANGUAGES - languages))
    if not required_text:
        issues.append("manifest_required_text_missing")
    for deck in decks:
        corpus = deck["textCorpus"]
        issues.extend(f"content_missing:{token}" for token in required_text if token not in corpus)
        if not _KO_RE.search(corpus) or len(_CJK_RE.findall(corpus)) < 12:
            issues.append(f"content_cjk_missing:{deck['path']}")
        if deck["slideCount"] != len(slides):
            issues.append(f"content_slide_count_mismatch:{deck['path']}")
    return issues


def _scene_identity(scene: dict[str, Any]) -> list[dict[str, Any]]:
    slides = scene.get("slides")
    if not isinstance(slides, list):
        return []
    return [
        {
            "slide": index,
            "slideId": slide.get("slideId"),
            "slideDigest": slide.get("slideDigest"),
            "elements": [
                {"elementId": element.get("elementId"), "kind": element.get("kind")}
                for element in slide.get("elements", [])
                if isinstance(element, dict)
            ],
        }
        for index, slide in enumerate(slides, start=1)
        if isinstance(slide, dict)
    ]


def _pattern_checks(
    manifest: dict[str, Any], scene: dict[str, Any]
) -> tuple[list[str], dict[str, Any]]:
    """Require a pattern's semantic structure, not merely its label.

    These are deliberately small lower bounds: they catch a process reduced to
    two cards or a timeline reduced to two milestones while leaving the
    compiler free to choose the actual composition.
    """

    issues: list[str] = []
    evidence: dict[str, Any] = {}
    scene_slides = scene.get("slides") if isinstance(scene.get("slides"), list) else []
    for index, manifest_slide in enumerate(manifest.get("slides", []), start=1):
        if index > len(scene_slides) or not isinstance(manifest_slide, dict):
            continue
        scene_slide = scene_slides[index - 1]
        elements = scene_slide.get("elements", []) if isinstance(scene_slide, dict) else []
        roles = Counter(
            str(element.get("semanticRole") or "")
            for element in elements
            if isinstance(element, dict)
        )
        patterns = [pattern for pattern in manifest_slide.get("patterns", []) if isinstance(pattern, str)]
        slide_id = str(manifest_slide.get("slideId") or index)
        slide_evidence = {"patterns": patterns, "roles": dict(roles)}
        evidence[slide_id] = slide_evidence
        for pattern in patterns:
            if pattern == "process" and roles["processStep"] < 3:
                issues.append(f"pattern_process_steps_insufficient:{slide_id}")
            elif pattern == "timeline" and roles["timelineStep"] < 4:
                issues.append(f"pattern_timeline_steps_insufficient:{slide_id}")
            elif pattern == "comparison" and roles["splitPanel"] < 2 and roles["comparisonPanel"] < 2:
                issues.append(f"pattern_comparison_panels_insufficient:{slide_id}")
            elif pattern == "card" and roles["cardSurface"] < 2:
                issues.append(f"pattern_card_surfaces_insufficient:{slide_id}")
            elif pattern == "metric" and roles["title"] < 1:
                issues.append(f"pattern_metric_title_missing:{slide_id}")
            elif pattern == "infographic":
                visual_role = any(
                    role.startswith("infographic")
                    or role in {"bodyImage", "bodyTable", "bodyChart"}
                    for role in roles
                )
                if not visual_role:
                    issues.append(f"pattern_infographic_visual_missing:{slide_id}")
            elif pattern == "image" and roles["bodyImage"] < 1:
                issues.append(f"pattern_native_image_missing:{slide_id}")
            elif pattern == "table" and roles["bodyTable"] < 1:
                issues.append(f"pattern_native_table_missing:{slide_id}")
            elif pattern == "chart" and roles["bodyChart"] < 1:
                issues.append(f"pattern_native_chart_missing:{slide_id}")
            elif pattern == "long-cjk" and (
                roles["longTitlePanel"] < 1 or roles["longTitleBody"] < 1
            ):
                issues.append(f"pattern_long_cjk_editorial_missing:{slide_id}")
    return issues, evidence


def _content_identity_evidence(
    manifest: dict[str, Any], legacy_source: dict[str, Any], scene: dict[str, Any]
) -> dict[str, Any]:
    """Bind the two decks to the same per-slide content contract.

    Full text is allowed to contain compiler furniture and accessibility labels;
    the manifest's ordered required text is the stable authored-content seam.
    """

    expected = [
        {
            "slideId": slide.get("slideId"),
            "requiredText": [
                token for token in slide.get("requiredText", [])
                if isinstance(token, str) and token
            ],
        }
        for slide in manifest.get("slides", [])
        if isinstance(slide, dict)
    ]
    legacy_slides = legacy_source.get("textCorpusBySlide", [])
    scene_slides = scene.get("slides", []) if isinstance(scene.get("slides"), list) else []
    legacy_match = []
    vnext_match = []
    for index, item in enumerate(expected):
        legacy_text = legacy_slides[index] if index < len(legacy_slides) else ""
        scene_slide = scene_slides[index] if index < len(scene_slides) else {}
        vnext_text = " ".join(
            str(element.get("text") or "")
            for element in scene_slide.get("elements", [])
            if isinstance(element, dict)
        ) if isinstance(scene_slide, dict) else ""
        legacy_match.append([token in legacy_text for token in item["requiredText"]])
        vnext_match.append([token in vnext_text for token in item["requiredText"]])
    expected_digest = _identity_digest(expected)
    legacy_digest = _identity_digest({"expected": expected, "matched": legacy_match})
    vnext_digest = _identity_digest({"expected": expected, "matched": vnext_match})
    return {
        "manifestDigest": expected_digest,
        "legacyMatched": legacy_match,
        "authoredVNextMatched": vnext_match,
        "legacyDigest": legacy_digest,
        "authoredVNextDigest": vnext_digest,
        "sameContent": all(all(row) for row in legacy_match + vnext_match),
    }


def _hierarchy_evidence(scene: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    issues: list[str] = []
    evidence: dict[str, Any] = {}
    for index, slide in enumerate(scene.get("slides", []), start=1):
        if not isinstance(slide, dict):
            continue
        title_sizes = []
        body_sizes = []
        sizes = []
        for element in slide.get("elements", []):
            if not isinstance(element, dict) or element.get("kind") != "text":
                continue
            size = (element.get("style") or {}).get("fontSize")
            if not isinstance(size, (int, float)) or size <= 0:
                continue
            sizes.append(float(size))
            role = str(element.get("semanticRole") or "")
            if role == "title":
                title_sizes.append(float(size))
            elif role not in {"caption", "label", "metadata"}:
                body_sizes.append(float(size))
        title_max = max(title_sizes, default=0)
        body_max = max(body_sizes, default=0)
        ratio = round(title_max / body_max, 3) if body_max else None
        evidence[str(index)] = {"titleMax": title_max, "bodyMax": body_max, "titleBodyRatio": ratio, "fontSizes": sorted(set(sizes))}
        if title_max <= 0 or (body_max > 0 and title_max < body_max * 1.15):
            issues.append(f"hierarchy_title_body_insufficient:{index}")
        if len(set(sizes)) < 2:
            issues.append(f"hierarchy_levels_insufficient:{index}")
    return issues, evidence


def _relative_visual_quality(
    legacy_render: dict[str, Any],
    vnext_render: dict[str, Any],
    scene: dict[str, Any],
) -> dict[str, Any]:
    """Compare rendered evidence while requiring a non-gameable evidence seam."""

    issues: list[str] = []
    legacy_slides = legacy_render.get("slides", [])
    vnext_slides = vnext_render.get("slides", [])
    if len(legacy_slides) != len(vnext_slides):
        issues.append("relative_render_slide_count_mismatch")
    paired = list(zip(legacy_slides, vnext_slides))
    per_slide = []
    for index, (legacy, vnext) in enumerate(paired, start=1):
        if not _DIGEST_RE.fullmatch(str(legacy.get("sha256") or "")) or not _DIGEST_RE.fullmatch(str(vnext.get("sha256") or "")):
            issues.append(f"relative_render_digest_missing:{index}")
        legacy_ink = float(legacy.get("inkRatio", 0))
        vnext_ink = float(vnext.get("inkRatio", 0))
        legacy_edge = float(legacy.get("edgeDensity", 0))
        vnext_edge = float(vnext.get("edgeDensity", 0))
        ink_ratio = vnext_ink / legacy_ink if legacy_ink else None
        edge_ratio = vnext_edge / legacy_edge if legacy_edge else None
        # Per-slide lower bound catches sparse/blank replacements.  Aggregate
        # bound prevents a few dense slides from masking several weak ones.
        if legacy_ink > 0 and vnext_ink < legacy_ink * 0.45:
            issues.append(f"relative_information_density_low:{index}")
        if legacy_edge > 0 and vnext_edge < legacy_edge * 0.35:
            issues.append(f"relative_visual_structure_low:{index}")
        per_slide.append({
            "slide": index,
            "legacyInkRatio": legacy_ink,
            "authoredVNextInkRatio": vnext_ink,
            "inkRatio": round(ink_ratio, 3) if ink_ratio is not None else None,
            "legacyEdgeDensity": legacy_edge,
            "authoredVNextEdgeDensity": vnext_edge,
            "edgeRatio": round(edge_ratio, 3) if edge_ratio is not None else None,
            "legacyRenderSha256": legacy.get("sha256"),
            "authoredVNextRenderSha256": vnext.get("sha256"),
        })
    legacy_average = sum(float(item.get("inkRatio", 0)) for item in legacy_slides) / len(legacy_slides) if legacy_slides else 0
    vnext_average = sum(float(item.get("inkRatio", 0)) for item in vnext_slides) / len(vnext_slides) if vnext_slides else 0
    if legacy_average > 0 and vnext_average < legacy_average * 0.70:
        issues.append("relative_information_density_average_low")
    return {
        "verified": not issues,
        "issues": issues,
        "legacyAverageInkRatio": round(legacy_average, 6),
        "authoredVNextAverageInkRatio": round(vnext_average, 6),
        "perSlide": per_slide,
        "thresholds": {"perSlideInkRatio": 0.45, "averageInkRatio": 0.70, "edgeRatio": 0.35},
        "renderEvidenceRequired": True,
        "sceneIdentityEvidenceRequired": True,
        "digestEvidenceRequired": True,
        "sceneSlideCount": len(scene.get("slides", [])) if isinstance(scene.get("slides"), list) else 0,
    }


def _vnext_scene_checks(scene: dict[str, Any], deck: dict[str, Any], manifest: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if scene.get("schemaVersion") != SCENE_SCHEMA_VERSION:
        issues.append("scene_schema_unsupported")
    scene_digest = scene.get("sceneDigest")
    if not isinstance(scene_digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", scene_digest):
        issues.append("scene_digest_invalid")
    elif deck["customProperties"].get("AuthoredSceneDigest") != scene_digest:
        issues.append("scene_digest_readback_mismatch")
    slides = scene.get("slides")
    if not isinstance(slides, list):
        return issues + ["scene_slides_required"]
    if len(slides) != len(manifest.get("slides", [])) or len(slides) != deck["slideCount"]:
        issues.append("scene_slide_count_identity_mismatch")
    slide_ids = [slide.get("slideId") for slide in slides if isinstance(slide, dict)]
    expected_slide_ids = [f"slide:{item.get('slideId')}" for item in manifest.get("slides", []) if isinstance(item, dict)]
    if slide_ids != expected_slide_ids:
        issues.append("scene_slide_identity_mismatch")
    slide_digests = [slide.get("slideDigest") for slide in slides if isinstance(slide, dict)]
    if any(not isinstance(value, str) or not _DIGEST_RE.fullmatch(value) for value in slide_digests):
        issues.append("scene_slide_digest_invalid")
    if len(set(slide_ids)) != len(slide_ids) or len(set(slide_digests)) != len(slide_digests):
        issues.append("scene_slide_identity_duplicate")
    expected_ids = {(item.get("elementId"), item.get("kind")) for slide in slides if isinstance(slide, dict) for item in slide.get("elements", []) if isinstance(item, dict)}
    actual_ids = {(item.get("elementId"), item.get("kind")) for item in deck["sceneElements"]}
    if expected_ids != actual_ids:
        issues.append("scene_element_stable_id_readback_mismatch")
    expected_identity = [
        {
            "slide": index,
            "elements": [
                {"elementId": element.get("elementId"), "kind": element.get("kind")}
                for element in slide.get("elements", [])
                if isinstance(element, dict)
            ],
        }
        for index, slide in enumerate(slides, start=1)
        if isinstance(slide, dict)
    ]
    if deck.get("sceneIdentityDigest") != _identity_digest(expected_identity):
        issues.append("scene_element_order_identity_mismatch")
    expected_kinds = {kind for _, kind in expected_ids}
    capabilities_raw = deck["customProperties"].get("AuthoredNativeCapabilities")
    try:
        capabilities = json.loads(capabilities_raw) if capabilities_raw else None
    except json.JSONDecodeError:
        capabilities = None
    if not isinstance(capabilities, dict) or not isinstance(capabilities.get("supported"), list) or not isinstance(capabilities.get("unsupported"), list):
        issues.append("native_capability_contract_missing")
        capabilities = {"supported": [], "unsupported": []}
    supported = set(capabilities["supported"])
    unsupported = set(capabilities["unsupported"])
    for kind in sorted(expected_kinds - supported):
        if kind not in unsupported:
            issues.append(f"native_capability_undeclared:{kind}")
        else:
            issues.append(f"native_capability_unsupported:{kind}")
    required_objects = set(manifest.get("requiredNativeObjects", []))
    counts = deck["nativeObjectCounts"]
    for kind in sorted(required_objects):
        if kind not in SUPPORTED_SCENE_KINDS:
            issues.append(f"manifest_native_object_invalid:{kind}")
        elif counts.get(kind, 0) < 1:
            issues.append(f"native_object_missing:{kind}")
    if "table" in required_objects and "table" not in supported:
        issues.append("native_capability_table_required_but_unsupported")
    if "chart" in required_objects and "chart" not in supported:
        issues.append("native_capability_chart_required_but_unsupported")
    return issues


def run_census(args: argparse.Namespace) -> dict[str, Any]:
    manifest = _read_json(Path(args.content_manifest), "content_manifest")
    legacy = inspect_pptx(Path(args.legacy_pptx))
    vnext = inspect_pptx(Path(args.vnext_pptx))
    legacy_source = inspect_legacy_html(
        Path(args.legacy_html_dir), legacy["slideCount"]
    )
    legacy["textCorpus"] = legacy_source["textCorpus"]
    legacy_render = inspect_rendered_slides(Path(args.legacy_render_dir), legacy["slideCount"])
    vnext_render = inspect_rendered_slides(Path(args.vnext_render_dir), vnext["slideCount"])
    scene = _read_json(Path(args.vnext_scene), "vnext_scene")
    content_identity = _content_identity_evidence(manifest, legacy_source, scene)
    pattern_issues, pattern_evidence = _pattern_checks(manifest, scene)
    hierarchy_issues, hierarchy_evidence = _hierarchy_evidence(scene)
    relative_quality = _relative_visual_quality(legacy_render, vnext_render, scene)
    issues = _manifest_checks(manifest, [legacy, vnext])
    issues.extend(_vnext_scene_checks(scene, vnext, manifest))
    issues.extend(pattern_issues)
    issues.extend(hierarchy_issues)
    issues.extend(relative_quality["issues"])
    if not content_identity["sameContent"]:
        issues.append("content_identity_digest_mismatch")
    for deck in (legacy, vnext):
        issues.extend({"deck": deck["path"], **check} for check in deck["visualChecks"])
    lifecycle = {
        "census_complete": False,
        "archive_ready": False,
        "active_jobs_drained": False,
        "checkpoint_replay_verified": False,
        "owner_isolation_verified": False,
        "checksum_verified": False,
    }
    receipt_path = getattr(args, "promotion_receipt", None)
    if receipt_path:
        receipt = _read_json(Path(receipt_path), "promotion_receipt")
        for key in lifecycle:
            lifecycle[key] = receipt.get(key) is True
    lifecycle["census_complete"] = not issues
    relative_verified = (
        content_identity["sameContent"]
        and relative_quality["verified"]
        and not pattern_issues
        and not hierarchy_issues
        and not _vnext_scene_checks(scene, vnext, manifest)
    )
    lifecycle["relative_visual_quality_verified"] = relative_verified
    promotion_ready = all(lifecycle.values())
    report = {
        "schemaVersion": SCHEMA_VERSION,
        "status": "accepted" if not issues else "rejected",
        "promotionReady": promotion_ready,
        "sameContent": content_identity["sameContent"],
        "promotionGates": lifecycle,
        "contentIdentity": content_identity,
        "relativeVisualQuality": relative_quality,
        "patternEvidence": pattern_evidence,
        "hierarchyEvidence": hierarchy_evidence,
        "manifest": {"path": str(Path(args.content_manifest)), "contentId": manifest.get("contentId"), "requiredPatterns": sorted(REQUIRED_PATTERNS), "requiredNativeObjects": manifest.get("requiredNativeObjects", [])},
        "legacy": {**legacy, "render": legacy_render, "source": legacy_source},
        "authoredVNext": {**vnext, "render": vnext_render, "sceneDigest": scene.get("sceneDigest")},
        "issues": issues,
        "capabilityPolicy": {"unsupportedIsFailure": True, "rasterFallbackAccepted": False, "templateOnlyFallbackAccepted": False},
    }
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy-pptx", required=True)
    parser.add_argument("--vnext-pptx", required=True)
    parser.add_argument("--content-manifest", required=True)
    parser.add_argument("--vnext-scene", required=True)
    parser.add_argument("--legacy-html-dir", required=True)
    parser.add_argument("--legacy-render-dir", required=True)
    parser.add_argument("--vnext-render-dir", required=True)
    parser.add_argument(
        "--promotion-receipt",
        help="Optional JSON receipt for archive/job/checkpoint/isolation/checksum gates; absent means fail closed.",
    )
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        report = run_census(args)
    except CensusFailure as exc:
        report = {
            "schemaVersion": SCHEMA_VERSION,
            "status": "blocked",
            "promotionReady": False,
            "promotionGates": {
                "census_complete": False,
                "archive_ready": False,
                "active_jobs_drained": False,
                "checkpoint_replay_verified": False,
                "owner_isolation_verified": False,
                "checksum_verified": False,
                "relative_visual_quality_verified": False,
            },
            "issues": [str(exc)],
            "capabilityPolicy": {"unsupportedIsFailure": True, "rasterFallbackAccepted": False, "templateOnlyFallbackAccepted": False},
        }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "output": str(output), "issueCount": len(report.get("issues", []))}, ensure_ascii=False))
    return 0 if report["status"] == "accepted" else 2


if __name__ == "__main__":
    raise SystemExit(main())
