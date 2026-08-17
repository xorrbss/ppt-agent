"""Deterministic compiler for retained authored-template PowerPoint geometry.

The compiler reads the retained PPTX itself.  It never asks a provider to
describe or recreate the reference.  The resulting JSON snapshot is small,
content-addressed, and safe to persist in an authored composition pin.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import posixpath
import re
import unicodedata
import xml.etree.ElementTree as ET
from typing import Any
from zipfile import ZipFile

from services.authored_vnext.retained_reference_marker import (
    RETAINED_REFERENCE_MARKER_CODEC,
    canonical_reference_digest,
    decode_reference_layout_marker,
    encode_reference_layout_marker,
)


RETAINED_REFERENCE_LAYOUT_SCHEMA_VERSION = "retained-reference-layout/v1"
OSB_RETAINED_REFERENCE_ID = "artifact-template-ai/osb-proposal-1-29"
DEVAX_RETAINED_REFERENCE_ID = "artifact-template-devax/devax-proposal"
DEVAX_AI_RETAINED_REFERENCE_ID = (
    "artifact-template-devax-ai/ai-development-proposal"
)
BLUE_INFOGRAPHIC_RETAINED_REFERENCE_ID = (
    "artifact-template-ppt/blue-infographic"
)

# Product-selectable authored styles mapped to their immutable retained source.
# Unregistered styles intentionally remain on the existing v2 grammar.
RETAINED_REFERENCE_REGISTRY: dict[str, dict[str, Any]] = {
    "osb-smart-banking": {
        "retainedReferenceId": OSB_RETAINED_REFERENCE_ID,
        "archetypes": [
            "cover",
            "section",
            "content",
            "data",
            "timeline",
            "architecture",
            "process",
            "closing",
        ],
        "semanticRoles": [
            "cover-summary",
            "section-summary",
            "content-card",
            "metric",
            "timeline-step",
            "architecture-layer",
            "process-step",
            "closing-message",
        ],
    },
    "visual-discovery": {
        "retainedReferenceId": DEVAX_RETAINED_REFERENCE_ID,
        "archetypes": ["cover", "section", "content", "process", "closing"],
        "semanticRoles": [
            "cover-summary",
            "section-summary",
            "content-card",
            "process-step",
            "closing-message",
        ],
    },
    "project-launch": {
        "retainedReferenceId": DEVAX_AI_RETAINED_REFERENCE_ID,
        "archetypes": ["cover", "section", "content", "process", "closing"],
        "semanticRoles": [
            "cover-summary",
            "section-summary",
            "content-card",
            "process-step",
            "closing-message",
        ],
    },
    "tech-blueprint": {
        "retainedReferenceId": BLUE_INFOGRAPHIC_RETAINED_REFERENCE_ID,
        "archetypes": ["cover", "section", "content", "process", "closing"],
        "semanticRoles": [
            "cover-summary",
            "section-summary",
            "content-card",
            "process-step",
            "closing-message",
        ],
    },
}

_P = "http://schemas.openxmlformats.org/presentationml/2006/main"
_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PR = "http://schemas.openxmlformats.org/package/2006/relationships"
_NS = {"p": _P, "a": _A, "r": _R, "pr": _PR}

RETAINED_NATIVE_SCENE_SCHEMA_VERSION = "retained-native-scene/v1"

ET.register_namespace("a", _A)
ET.register_namespace("p", _P)
ET.register_namespace("r", _R)


def _canonical_value(value: Any) -> Any:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("retained reference contains non-finite geometry")
        if value.is_integer():
            return int(value)
        return float(format(value, ".15g"))
    if isinstance(value, list):
        return [_canonical_value(item) for item in value]
    if isinstance(value, dict):
        return {
            unicodedata.normalize("NFC", str(key)): _canonical_value(item)
            for key, item in value.items()
        }
    return value


def _reference_skill_name(retained_reference_id: str) -> str:
    return retained_reference_id.split("/", 1)[0]


def _reference_path(retained_reference_id: str) -> Path | None:
    configured = os.environ.get("AUTHORED_RETAINED_REFERENCE_ROOT")
    candidates: list[Path] = []
    skill_name = _reference_skill_name(retained_reference_id)
    if configured:
        root = Path(configured).expanduser()
        candidates.extend(
            (
                root / skill_name / "assets" / "reference.pptx",
                root / skill_name / "reference.pptx",
            )
        )
    else:
        repo_root = Path(__file__).resolve().parents[6]
        if retained_reference_id == OSB_RETAINED_REFERENCE_ID:
            candidates.append(repo_root / "osb-template-24-layouts.pptx")
        candidates.append(
            repo_root
            / "assets"
            / "retained-references"
            / skill_name
            / "reference.pptx"
        )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    if configured:
        raise ValueError(
            f"configured retained reference is missing: {retained_reference_id}"
        )
    return None


def _emu_rect(shape: ET.Element) -> dict[str, float] | None:
    transform = shape.find("./p:spPr/a:xfrm", _NS)
    if transform is None:
        transform = shape.find("./p:xfrm", _NS)
    if transform is None:
        return None
    offset = transform.find("a:off", _NS)
    extent = transform.find("a:ext", _NS)
    if offset is None or extent is None:
        return None
    return {
        "x": float(offset.get("x", "0")),
        "y": float(offset.get("y", "0")),
        "width": float(extent.get("cx", "0")),
        "height": float(extent.get("cy", "0")),
    }


def _source_shape_id(shape: ET.Element) -> str:
    properties = None
    for path in (
        "./p:nvSpPr/p:cNvPr",
        "./p:nvPicPr/p:cNvPr",
        "./p:nvGraphicFramePr/p:cNvPr",
        "./p:nvCxnSpPr/p:cNvPr",
        "./p:nvGrpSpPr/p:cNvPr",
    ):
        properties = shape.find(path, _NS)
        if properties is not None:
            break
    value = properties.get("id", "0") if properties is not None else "0"
    return f"pptx-shape-{value}"


def _source_kind(shape: ET.Element) -> str:
    if shape.tag == f"{{{_P}}}graphicFrame":
        return "rect"
    geometry = shape.find("./p:spPr/a:prstGeom", _NS)
    preset = geometry.get("prst", "rect") if geometry is not None else "rect"
    if preset in {"ellipse", "line"}:
        return preset
    if preset in {"roundRect", "round1Rect", "round2SameRect", "round2DiagRect"}:
        return "roundRect"
    return "rect"


def _shape_text(shape: ET.Element) -> str:
    return "\n".join(
        text.text or "" for text in shape.findall(".//a:t", _NS)
    ).strip()


def _direct_rgb(parent: ET.Element | None, fallback: str) -> str:
    if parent is None:
        return fallback
    rgb = parent.find(".//a:srgbClr", _NS)
    if rgb is None:
        return fallback
    value = rgb.get("val", "").upper()
    return f"#{value}" if len(value) == 6 else fallback


def _shape_fill(shape: ET.Element, fallback: str) -> str:
    return _direct_rgb(shape.find("./p:spPr/a:solidFill", _NS), fallback)


def _font_face(shape: ET.Element, fallback: str) -> str:
    for path in (".//a:rPr/a:ea", ".//a:defRPr/a:ea", ".//a:rPr/a:latin"):
        value = shape.find(path, _NS)
        if value is not None and value.get("typeface"):
            return str(value.get("typeface"))
    return fallback


def _font_pt(shape: ET.Element, fallback: float) -> float:
    for path in (".//a:rPr", ".//a:defRPr", ".//a:endParaRPr"):
        value = shape.find(path, _NS)
        if value is not None and value.get("sz"):
            return round(float(value.get("sz", "0")) / 100.0, 2)
    return fallback


def _rect(x: float, y: float, width: float, height: float) -> dict[str, float]:
    return {
        "x": round(max(0.0, x), 4),
        "y": round(max(0.0, y), 4),
        "width": round(max(1.0, width), 4),
        "height": round(max(1.0, height), 4),
    }


def _inset(rect: dict[str, float], horizontal: float, vertical: float) -> dict[str, float]:
    return _rect(
        rect["x"] + horizontal,
        rect["y"] + vertical,
        rect["width"] - horizontal * 2,
        rect["height"] - vertical * 2,
    )


def _reserve_title_marker(
    bounds: dict[str, float],
    *,
    slide_role: str,
) -> dict[str, float]:
    """Keep the retained title marker beside, never on top of, title text."""
    if slide_role in {"cover", "closing"}:
        return bounds
    marker_width = min(18.0, bounds["height"] * 0.36)
    left_clearance = marker_width + 8.0
    return _rect(
        bounds["x"] + left_clearance,
        bounds["y"],
        bounds["width"] - left_clearance,
        bounds["height"],
    )


def _block(
    block_id: str,
    semantic_role: str,
    bounds: dict[str, float],
    *,
    source_shape_id: str,
    font_family: str,
    font_pt: float,
    fill: str,
    accent: str,
    text_color: str = "#404040",
    shape: str = "rect",
    z_index: int = 10,
) -> dict[str, Any]:
    return {
        "id": block_id,
        "kind": semantic_role,
        "semanticRole": semantic_role,
        "bounds": bounds,
        "shape": shape,
        "fill": fill,
        "stroke": accent,
        "strokeWidth": 1.0,
        "textColor": text_color,
        "accent": accent,
        "cornerRadius": 8.0 if shape == "roundRect" else 0.0,
        "textInsets": {"top": 12.0, "right": 14.0, "bottom": 12.0, "left": 14.0},
        "textStyle": {
            "fontFamily": font_family,
            "fontPt": font_pt,
            "fontWeight": 400,
            "color": text_color,
            "alignment": "left",
            "lineHeight": 1.25,
            "verticalAlignment": "middle",
        },
        "zIndex": z_index,
        "sourceShapeId": source_shape_id,
    }


def _decoration(
    decoration_id: str,
    semantic_role: str,
    kind: str,
    bounds: dict[str, float],
    *,
    fill: str | None,
    stroke: str | None,
    z_index: int,
    source_shape_id: str,
) -> dict[str, Any]:
    return {
        "id": decoration_id,
        "semanticRole": semantic_role,
        "kind": kind,
        "bounds": bounds,
        "fill": fill,
        "stroke": stroke,
        "strokeWidth": 2.0 if kind == "line" else 1.0,
        "zIndex": z_index,
        "sourceShapeId": source_shape_id,
    }


def _separate_timeline_frame(
    archetype: str,
    blocks: list[dict[str, Any]],
    decorations: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Keep the retained timeline frame while reserving a non-overlapping text zone."""
    if archetype != "timeline" or len(blocks) < 2:
        return blocks, decorations
    frame = max(
        blocks,
        key=lambda item: item["bounds"]["width"] * item["bounds"]["height"],
    )
    frame_bounds = frame["bounds"]
    frame_right = frame_bounds["x"] + frame_bounds["width"]
    frame_bottom = frame_bounds["y"] + frame_bounds["height"]
    nested = [
        item
        for item in blocks
        if item is not frame
        and item["bounds"]["x"] >= frame_bounds["x"]
        and item["bounds"]["y"] >= frame_bounds["y"]
        and item["bounds"]["x"] + item["bounds"]["width"] <= frame_right
        and item["bounds"]["y"] + item["bounds"]["height"] <= frame_bottom
    ]
    if len(nested) < 2:
        return blocks, decorations
    nested_bottom = max(
        item["bounds"]["y"] + item["bounds"]["height"] for item in nested
    )
    safe_top = nested_bottom + 12.0
    safe_bottom = frame_bottom - 12.0
    if safe_bottom - safe_top < 72.0:
        return blocks, decorations
    safe_block = dict(frame)
    safe_block.update(
        {
            "id": "timeline-body-safe",
            "bounds": _rect(
                frame_bounds["x"] + 14.0,
                safe_top,
                frame_bounds["width"] - 28.0,
                safe_bottom - safe_top,
            ),
            "fill": "#F2F2F2",
            "zIndex": 10,
        }
    )
    frame_decoration = _decoration(
        "timeline-background-frame",
        "background-decoration",
        frame.get("shape") or "rect",
        dict(frame_bounds),
        fill=frame.get("fill"),
        stroke=frame.get("stroke"),
        z_index=1,
        source_shape_id=str(frame["sourceShapeId"]),
    )
    return [*nested, safe_block], [frame_decoration, *decorations]


def _grid(rect: dict[str, float], columns: int, rows: int, gap: float) -> list[dict[str, float]]:
    width = (rect["width"] - gap * (columns - 1)) / columns
    height = (rect["height"] - gap * (rows - 1)) / rows
    return [
        _rect(
            rect["x"] + column * (width + gap),
            rect["y"] + row * (height + gap),
            width,
            height,
        )
        for row in range(rows)
        for column in range(columns)
    ]


def _layout_library(
    *,
    canvas_width: float,
    canvas_height: float,
    anchors: dict[str, dict[str, dict[str, float]]],
    source_title_id: str,
    source_block_ids: list[str],
    source_accent_id: str,
    font_family: str,
    body_font_pt: float,
    title_font_pt: float,
    accent: str,
    dark_accent: str,
    panel_fill: str,
) -> list[dict[str, Any]]:
    def title(role: str) -> dict[str, Any]:
        anchor = anchors[role]["title"]
        marker_size = min(18.0, anchor["height"] * 0.42)
        return {
            "bounds": _rect(
                anchor["x"] + marker_size + 12.0,
                anchor["y"],
                anchor["width"] - marker_size - 12.0,
                anchor["height"],
            ),
            "textStyle": {
                "fontFamily": font_family,
                "fontPt": title_font_pt,
                "fontWeight": 800,
                "color": "#404040",
                "alignment": "center" if role in {"cover", "closing"} else "left",
                "lineHeight": 1.2,
                "verticalAlignment": "middle",
            },
            "accent": _decoration(
                f"{role}-title-marker",
                "template-accent",
                "ellipse",
                _rect(anchor["x"], anchor["y"] + (anchor["height"] - marker_size) / 2, marker_size, marker_size),
                fill="#FFFFFF",
                stroke=dark_accent,
                z_index=12,
                source_shape_id=source_title_id,
            ),
        }

    def make(
        layout_id: str,
        archetype: str,
        slide_role: str,
        blocks: list[dict[str, Any]],
        decorations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "id": layout_id,
            "archetype": archetype,
            "slideRole": slide_role,
            "title": title(slide_role),
            "contentRect": anchors[slide_role]["content"],
            "blocks": blocks,
            "decorations": decorations,
        }

    result: list[dict[str, Any]] = []
    cover = _inset(anchors["cover"]["content"], 44.0, 34.0)
    result.append(
        make(
            "osb-cover-statement",
            "cover",
            "cover",
            [
                _block(
                    "cover-summary",
                    "cover-summary",
                    cover,
                    source_shape_id=source_block_ids[0],
                    font_family=font_family,
                    font_pt=max(body_font_pt + 5.0, 20.0),
                    fill="#FFFFFF",
                    accent=accent,
                    shape="roundRect",
                )
            ],
            [
                _decoration(
                    "cover-accent-band",
                    "template-accent",
                    "roundRect",
                    _rect(cover["x"], cover["y"] + cover["height"] * 0.68, cover["width"], cover["height"] * 0.12),
                    fill=accent,
                    stroke=accent,
                    z_index=4,
                    source_shape_id=source_accent_id,
                )
            ],
        )
    )

    content = anchors["content"]["content"]
    gap = max(14.0, content["width"] * 0.018)
    left_width = content["width"] * 0.61
    right_width = content["width"] - left_width - gap
    content_bounds = [
        _rect(content["x"], content["y"], left_width, content["height"]),
        _rect(content["x"] + left_width + gap, content["y"], right_width, (content["height"] - gap) / 2),
        _rect(content["x"] + left_width + gap, content["y"] + (content["height"] + gap) / 2, right_width, (content["height"] - gap) / 2),
    ]
    result.append(
        make(
            "osb-content-asymmetric",
            "content",
            "content",
            [
                _block(
                    f"content-{index + 1}",
                    "content-card",
                    bounds,
                    source_shape_id=source_block_ids[index % len(source_block_ids)],
                    font_family=font_family,
                    font_pt=body_font_pt,
                    fill=panel_fill,
                    accent=accent,
                    shape="roundRect",
                )
                for index, bounds in enumerate(content_bounds)
            ],
            [],
        )
    )

    metrics = _grid(_inset(content, 8.0, 12.0), 3, 1, gap)
    result.append(
        make(
            "osb-data-metrics",
            "data",
            "content",
            [
                _block(
                    f"metric-{index + 1}",
                    "metric",
                    bounds,
                    source_shape_id=source_block_ids[index % len(source_block_ids)],
                    font_family=font_family,
                    font_pt=max(body_font_pt, 17.0),
                    fill="#FFFFFF",
                    accent=accent,
                    shape="roundRect",
                )
                for index, bounds in enumerate(metrics)
            ],
            [
                _decoration(
                    f"metric-accent-{index + 1}",
                    "template-accent",
                    "rect",
                    _rect(bounds["x"], bounds["y"], bounds["width"], 10.0),
                    fill=accent,
                    stroke=accent,
                    z_index=12,
                    source_shape_id=source_accent_id,
                )
                for index, bounds in enumerate(metrics)
            ],
        )
    )

    timeline_area = _inset(content, 4.0, 8.0)
    step_width = (timeline_area["width"] - gap * 3) / 4
    step_height = timeline_area["height"] * 0.38
    timeline_bounds = [
        _rect(
            timeline_area["x"] + index * (step_width + gap),
            timeline_area["y"] if index % 2 == 0 else timeline_area["y"] + timeline_area["height"] - step_height,
            step_width,
            step_height,
        )
        for index in range(4)
    ]
    mid_y = timeline_area["y"] + timeline_area["height"] / 2
    result.append(
        make(
            "osb-timeline-alternating",
            "timeline",
            "content",
            [
                _block(
                    f"timeline-{index + 1}",
                    "timeline-step",
                    bounds,
                    source_shape_id=source_block_ids[index % len(source_block_ids)],
                    font_family=font_family,
                    font_pt=body_font_pt,
                    fill=panel_fill,
                    accent=accent,
                    shape="roundRect",
                )
                for index, bounds in enumerate(timeline_bounds)
            ],
            [
                _decoration(
                    "timeline-axis",
                    "process-connector",
                    "line",
                    _rect(timeline_area["x"], mid_y, timeline_area["width"], 1.0),
                    fill=None,
                    stroke=dark_accent,
                    z_index=4,
                    source_shape_id=source_accent_id,
                )
            ],
        )
    )

    architecture = _grid(_inset(content, 48.0, 4.0), 1, 3, gap)
    result.append(
        make(
            "osb-architecture-layers",
            "architecture",
            "content",
            [
                _block(
                    f"architecture-{index + 1}",
                    "architecture-layer",
                    bounds,
                    source_shape_id=source_block_ids[index % len(source_block_ids)],
                    font_family=font_family,
                    font_pt=body_font_pt,
                    fill="#FFFFFF" if index == 1 else panel_fill,
                    accent=dark_accent if index == 1 else accent,
                    shape="roundRect",
                )
                for index, bounds in enumerate(architecture)
            ],
            [
                _decoration(
                    "architecture-spine",
                    "process-connector",
                    "line",
                    _rect(content["x"] + 24.0, content["y"], 1.0, content["height"]),
                    fill=None,
                    stroke=accent,
                    z_index=4,
                    source_shape_id=source_accent_id,
                )
            ],
        )
    )

    process_bounds = _grid(_inset(content, 2.0, 2.0), 2, 2, gap)
    core_size = min(content["width"], content["height"]) * 0.24
    result.append(
        make(
            "osb-process-cycle",
            "process",
            "content",
            [
                _block(
                    f"process-{index + 1}",
                    "process-step",
                    bounds,
                    source_shape_id=source_block_ids[index % len(source_block_ids)],
                    font_family=font_family,
                    font_pt=body_font_pt,
                    fill=panel_fill,
                    accent=accent,
                    shape="roundRect",
                )
                for index, bounds in enumerate(process_bounds)
            ],
            [
                _decoration(
                    "process-core",
                    "template-accent",
                    "ellipse",
                    _rect(
                        content["x"] + (content["width"] - core_size) / 2,
                        content["y"] + (content["height"] - core_size) / 2,
                        core_size,
                        core_size,
                    ),
                    fill="#FFFFFF",
                    stroke=accent,
                    z_index=14,
                    source_shape_id=source_accent_id,
                )
            ],
        )
    )

    closing = _inset(anchors["closing"]["content"], 60.0, 42.0)
    result.append(
        make(
            "osb-closing-message",
            "closing",
            "closing",
            [
                _block(
                    "closing-message",
                    "closing-message",
                    closing,
                    source_shape_id=source_block_ids[-1],
                    font_family=font_family,
                    font_pt=max(body_font_pt + 4.0, 19.0),
                    fill="#FFFFFF",
                    accent=dark_accent,
                    shape="roundRect",
                )
            ],
            [
                _decoration(
                    "closing-accent-band",
                    "template-accent",
                    "roundRect",
                    _rect(closing["x"], closing["y"] + closing["height"] - 14.0, closing["width"], 14.0),
                    fill=accent,
                    stroke=accent,
                    z_index=12,
                    source_shape_id=source_accent_id,
                )
            ],
        )
    )
    return result


def _plain_rect(value: Any) -> dict[str, float]:
    if isinstance(value, dict):
        read = lambda name: value.get(name)  # noqa: E731
    else:
        read = lambda name: getattr(value, name)  # noqa: E731
    return _rect(
        float(read("x")),
        float(read("y")),
        float(read("width")),
        float(read("height")),
    )


_OSB_SOURCE_SLIDES = {
    "cover": (1, "cover", "cover", "cover-summary", 1),
    "section": (3, "section", "section", "section-summary", 1),
    "content": (5, "content", "content", "content-card", 4),
    "data": (6, "data", "content", "metric", 3),
    "timeline": (13, "timeline", "content", "timeline-step", 4),
    "architecture": (15, "architecture", "content", "architecture-layer", 3),
    "process": (7, "process", "content", "process-step", 4),
    "closing": (24, "closing", "closing", "closing-message", 1),
}

# Frozen from C:\project\PPT-agent\osb-template-24-layouts.pptx.  This is the
# deployable geometry resource, not an archetype-name fallback.  When the PPTX
# is present the compiler verifies these source shapes against its XML first.
_PACKAGED_OSB_SOURCE: dict[str, Any] = {
    "sourceDigest": "sha256:747e16a419652b51caaff406be99471f3635c31ec367c47b0f783482057a93dd",
    "sourceWidth": 10691813.0,
    "sourceHeight": 6840538.0,
    "layouts": {
        "cover": {"sourceSlideNumber": 1, "title": {"id": "pptx-shape-2", "bounds": {"x": 663579.0, "y": 2048709.0, "width": 6294940.0, "height": 639289.0}, "kind": "rect", "fontFamily": "KoPub돋움체 Bold", "fontPt": 40.0, "fill": "#F2F2F2"}, "blocks": [{"id": "pptx-shape-3", "bounds": {"x": 682433.0, "y": 2711657.0, "width": 6276085.0, "height": 674687.0}, "kind": "rect", "fontFamily": "KoPub돋움체 Bold", "fontPt": 40.0, "fill": "#F2F2F2"}]},
        "section": {"sourceSlideNumber": 3, "title": {"id": "artifact-layout-shape-2", "bounds": {"x": 493871.0, "y": 1781270.0, "width": 4213479.0, "height": 717518.0}, "kind": "rect", "fontFamily": "나눔스퀘어 ExtraBold", "fontPt": 53.33, "fill": "#FFFFFF"}, "blocks": [{"id": "artifact-layout-shape-2", "bounds": {"x": 493871.0, "y": 1781270.0, "width": 4213479.0, "height": 717518.0}, "kind": "rect", "fontFamily": "나눔스퀘어 ExtraBold", "fontPt": 53.33, "fill": "#FFFFFF"}]},
        "content": {"sourceSlideNumber": 5, "title": {"id": "pptx-shape-13", "bounds": {"x": 620011.0, "y": 842380.0, "width": 4292457.0, "height": 226216.0}, "kind": "rect", "fontFamily": "+mj-ea", "fontPt": 13.0, "fill": "#F2F2F2"}, "blocks": [{"id": "pptx-shape-26", "bounds": {"x": 654007.0, "y": 1945025.0, "width": 4437484.0, "height": 991233.0}, "kind": "rect", "fontFamily": "나눔스퀘어 Bold", "fontPt": 11.0, "fill": "#F2F2F2"}, {"id": "pptx-shape-2", "bounds": {"x": 5644623.0, "y": 1958141.0, "width": 4437484.0, "height": 991233.0}, "kind": "rect", "fontFamily": "나눔스퀘어 Bold", "fontPt": 11.0, "fill": "#F2F2F2"}, {"id": "pptx-shape-4", "bounds": {"x": 5644623.0, "y": 4545220.0, "width": 4437484.0, "height": 991233.0}, "kind": "rect", "fontFamily": "나눔스퀘어 Bold", "fontPt": 11.0, "fill": "#F2F2F2"}, {"id": "pptx-shape-3", "bounds": {"x": 654007.0, "y": 4535999.0, "width": 4437484.0, "height": 737318.0}, "kind": "rect", "fontFamily": "나눔스퀘어 Bold", "fontPt": 11.0, "fill": "#F2F2F2"}]},
        "data": {"sourceSlideNumber": 6, "title": {"id": "pptx-shape-9", "bounds": {"x": 620011.0, "y": 842380.0, "width": 4292457.0, "height": 226216.0}, "kind": "rect", "fontFamily": "나눔스퀘어 ExtraBold", "fontPt": 13.0, "fill": "#F2F2F2"}, "blocks": [{"id": "pptx-shape-150", "bounds": {"x": 1084566.0, "y": 4727021.0, "width": 8656031.0, "height": 1283159.0}, "kind": "rect", "fontFamily": "나눔스퀘어 Bold", "fontPt": 10.0, "fill": "#F2F2F2"}, {"id": "pptx-shape-2", "bounds": {"x": 8296275.0, "y": 4428070.0, "width": 1444322.0, "height": 266869.0}, "kind": "roundRect", "fontFamily": "나눔스퀘어 Bold", "fontPt": 10.5, "fill": "#0E75BC"}, {"id": "pptx-shape-10", "bounds": {"x": 1021556.0, "y": 2475715.0, "width": 1285263.0, "height": 285301.0}, "kind": "roundRect", "fontFamily": "나눔스퀘어 Bold", "fontPt": 10.5, "fill": "#18A7E0"}]},
        "timeline": {"sourceSlideNumber": 13, "title": {"id": "pptx-shape-111", "bounds": {"x": 620011.0, "y": 842380.0, "width": 4292457.0, "height": 226216.0}, "kind": "rect", "fontFamily": "+mj-ea", "fontPt": 13.0, "fill": "#F2F2F2"}, "blocks": [{"id": "pptx-shape-219", "bounds": {"x": 401139.0, "y": 1369959.0, "width": 9958928.0, "height": 4957373.0}, "kind": "rect", "fontFamily": "나눔스퀘어 Bold", "fontPt": 9.0, "fill": "#F2F2F2"}, {"id": "pptx-shape-4", "bounds": {"x": 5045663.0, "y": 1761017.0, "width": 3978518.0, "height": 397150.0}, "kind": "rect", "fontFamily": "나눔스퀘어 Bold", "fontPt": 9.0, "fill": "#F2F2F2"}, {"id": "pptx-shape-10", "bounds": {"x": 3711229.0, "y": 2502399.0, "width": 6023074.0, "height": 199011.0}, "kind": "roundRect", "fontFamily": "나눔스퀘어 Bold", "fontPt": 7.0, "fill": "#CCECFF"}, {"id": "pptx-shape-24", "bounds": {"x": 3711229.0, "y": 3203427.0, "width": 4679850.0, "height": 245450.0}, "kind": "roundRect", "fontFamily": "나눔스퀘어 Bold", "fontPt": 7.0, "fill": "#CCECFF"}]},
        "architecture": {"sourceSlideNumber": 15, "title": {"id": "pptx-shape-9", "bounds": {"x": 620011.0, "y": 842380.0, "width": 4292457.0, "height": 226216.0}, "kind": "rect", "fontFamily": "+mj-ea", "fontPt": 13.0, "fill": "#F2F2F2"}, "blocks": [{"id": "pptx-shape-389", "bounds": {"x": 8523780.0, "y": 3105494.0, "width": 1602633.0, "height": 706280.0}, "kind": "rect", "fontFamily": "나눔스퀘어 Bold", "fontPt": 8.0, "fill": "#F2F2F2"}, {"id": "pptx-shape-201", "bounds": {"x": 4644428.0, "y": 4118613.0, "width": 1210936.0, "height": 301173.0}, "kind": "rect", "fontFamily": "나눔스퀘어 Bold", "fontPt": 12.0, "fill": "#F2F2F2"}, {"id": "pptx-shape-163", "bounds": {"x": 3207610.0, "y": 5054136.0, "width": 1222104.0, "height": 272535.0}, "kind": "rect", "fontFamily": "나눔스퀘어 Bold", "fontPt": 9.0, "fill": "#F2F2F2"}]},
        "process": {"sourceSlideNumber": 7, "title": {"id": "pptx-shape-43", "bounds": {"x": 620011.0, "y": 842380.0, "width": 4292457.0, "height": 226216.0}, "kind": "rect", "fontFamily": "나눔스퀘어 ExtraBold", "fontPt": 13.0, "fill": "#F2F2F2"}, "blocks": [{"id": "pptx-shape-13", "bounds": {"x": 950805.0, "y": 2061187.0, "width": 4226386.0, "height": 1165577.0}, "kind": "rect", "fontFamily": "나눔스퀘어", "fontPt": 11.0, "fill": "#F2F2F2"}, {"id": "pptx-shape-12", "bounds": {"x": 5629032.0, "y": 4664576.0, "width": 4226386.0, "height": 1165577.0}, "kind": "rect", "fontFamily": "나눔스퀘어", "fontPt": 11.0, "fill": "#F2F2F2"}, {"id": "pptx-shape-11", "bounds": {"x": 950805.0, "y": 4736493.0, "width": 4226386.0, "height": 1165577.0}, "kind": "rect", "fontFamily": "나눔스퀘어", "fontPt": 11.0, "fill": "#F2F2F2"}, {"id": "pptx-shape-10", "bounds": {"x": 5644810.0, "y": 2266331.0, "width": 4226386.0, "height": 909061.0}, "kind": "rect", "fontFamily": "나눔스퀘어", "fontPt": 11.0, "fill": "#F2F2F2"}]},
        "closing": {"sourceSlideNumber": 24, "title": {"id": "pptx-shape-4", "bounds": {"x": 3518539.0, "y": 1916629.0, "width": 4487541.0, "height": 639289.0}, "kind": "rect", "fontFamily": "+mj-ea", "fontPt": 70.0, "fill": "#F2F2F2"}, "blocks": [{"id": "pptx-shape-4", "bounds": {"x": 3518539.0, "y": 1916629.0, "width": 4487541.0, "height": 639289.0}, "kind": "rect", "fontFamily": "+mj-ea", "fontPt": 70.0, "fill": "#F2F2F2"}]},
    },
}

# These compact manifests are frozen directly from the retained PPTX files
# shipped by the corresponding artifact-template skills.  They contain the
# exact source slide, shape id, EMU bounds, typography and color evidence used
# by the compiler; runtime replay therefore never depends on a user profile.
_PACKAGED_ADDITIONAL_SOURCES: dict[str, dict[str, Any]] = {
    DEVAX_RETAINED_REFERENCE_ID: {
        "sourceDigest": "sha256:51f031e5ca5a126017687592f6c878495be9ded02c0de65c0e6fc3a5b68fa381",
        "sourceWidth": 12192000.0,
        "sourceHeight": 6858000.0,
        "layoutPrefix": "devax",
        "accent": "#2563EB",
        "darkAccent": "#155EEF",
        "layouts": {
            "cover": {
                "sourceSlideNumber": 1,
                "title": {"id": "pptx-shape-6", "bounds": {"x": 3041710.0, "y": 1195702.0, "width": 3054290.0, "height": 2575449.0}, "kind": "rect", "fontFamily": "+mj-ea", "fontPt": 161.36, "fill": "#FFFFFF", "textColor": "#2563EB"},
                "blocks": [{"id": "pptx-shape-35", "bounds": {"x": 4691266.0, "y": 4792159.0, "width": 3466860.0, "height": 1015663.0}, "kind": "rect", "fontFamily": "+mj-ea", "fontPt": 60.0, "fill": "#FFFFFF", "textColor": "#2563EB"}],
            },
            "section": {
                "sourceSlideNumber": 2,
                "title": {"id": "pptx-shape-57", "bounds": {"x": 6657974.0, "y": 3898437.0, "width": 4033840.0, "height": 1384995.0}, "kind": "rect", "fontFamily": "나눔스퀘어 ExtraBold", "fontPt": 20.0, "fill": "#FFFFFF", "textColor": "#155EEF"},
                "blocks": [{"id": "pptx-shape-44", "bounds": {"x": 6409630.0, "y": 3246285.0, "width": 3324868.0, "height": 523220.0}, "kind": "rect", "fontFamily": "나눔스퀘어 ExtraBold", "fontPt": 14.0, "fill": "#FFFFFF", "textColor": "#E7E6E6"}],
            },
            "content": {
                "sourceSlideNumber": 6,
                "title": {"id": "pptx-shape-3", "bounds": {"x": 768495.0, "y": 890976.0, "width": 6326412.0, "height": 384721.0}, "kind": "rect", "fontFamily": "나눔스퀘어 ExtraBold", "fontPt": 25.0, "fill": "#FFFFFF", "textColor": "#0070C0"},
                "blocks": [
                    {"id": "pptx-shape-5", "bounds": {"x": 720340.0, "y": 1275697.0, "width": 7515047.0, "height": 619721.0}, "kind": "rect", "fontFamily": "나눔스퀘어 Bold", "fontPt": 12.0, "fill": "#FFFFFF", "textColor": "#2F3650"},
                    {"id": "pptx-shape-27", "bounds": {"x": 867396.0, "y": 2519984.0, "width": 2811410.0, "height": 1153970.0}, "kind": "rect", "fontFamily": "나눔스퀘어 Bold", "fontPt": 10.5, "fill": "#FFFFFF", "textColor": "#2F3650"},
                    {"id": "pptx-shape-39", "bounds": {"x": 867396.0, "y": 4407773.0, "width": 2811410.0, "height": 1153970.0}, "kind": "rect", "fontFamily": "나눔스퀘어 Bold", "fontPt": 10.5, "fill": "#FFFFFF", "textColor": "#2F3650"},
                ],
            },
            "process": {
                "sourceSlideNumber": 7,
                "title": {"id": "pptx-shape-26", "bounds": {"x": 4021769.0, "y": 805535.0, "width": 7956089.0, "height": 369332.0}, "kind": "rect", "fontFamily": "나눔스퀘어 ExtraBold", "fontPt": 24.0, "fill": "#FFFFFF", "textColor": "#0070C0"},
                "blocks": [
                    {"id": "pptx-shape-145", "bounds": {"x": 8174515.0, "y": 4731063.0, "width": 1548000.0, "height": 702782.0}, "kind": "rect", "fontFamily": "나눔스퀘어 Bold", "fontPt": 11.0, "fill": "#FFFFFF", "textColor": "#2F3650"},
                    {"id": "pptx-shape-181", "bounds": {"x": 355009.0, "y": 1444316.0, "width": 3528000.0, "height": 292068.0}, "kind": "roundRect", "fontFamily": "나눔스퀘어 ExtraBold", "fontPt": 14.0, "fill": "#28475E", "textColor": "#FFFFFF"},
                    {"id": "pptx-shape-129", "bounds": {"x": 6196279.0, "y": 4731063.0, "width": 1548000.0, "height": 311691.0}, "kind": "rect", "fontFamily": "나눔스퀘어 Bold", "fontPt": 11.0, "fill": "#FFFFFF", "textColor": "#2F3650"},
                    {"id": "pptx-shape-97", "bounds": {"x": 6192932.0, "y": 1929460.0, "width": 1548000.0, "height": 311691.0}, "kind": "rect", "fontFamily": "나눔스퀘어 Bold", "fontPt": 11.0, "fill": "#FFFFFF", "textColor": "#2F3650"},
                ],
            },
            "closing": {
                "sourceSlideNumber": 10,
                "title": {"id": "pptx-shape-92", "bounds": {"x": 436027.0, "y": 618878.0, "width": 11421116.0, "height": 342723.0}, "kind": "rect", "fontFamily": "나눔스퀘어 Bold", "fontPt": 12.0, "fill": "#FFFFFF", "textColor": "#2F3650"},
                "blocks": [{"id": "pptx-shape-7", "bounds": {"x": 529355.0, "y": 4642548.0, "width": 3438963.0, "height": 1034177.0}, "kind": "roundRect", "fontFamily": "나눔스퀘어", "fontPt": 9.0, "fill": "#FFFFFF", "textColor": "#2F3650"}],
            },
        },
    },
    DEVAX_AI_RETAINED_REFERENCE_ID: {
        "sourceDigest": "sha256:df1c7bc05a36b3f8c6801af3c1422fb9201c0373cedff11bdefab917cc306157",
        "sourceWidth": 12192000.0,
        "sourceHeight": 6858000.0,
        "layoutPrefix": "devax-ai",
        "accent": "#FFD34E",
        "darkAccent": "#244FC7",
        "layouts": {
            "cover": {
                "sourceSlideNumber": 3,
                "title": {"id": "pptx-shape-3", "bounds": {"x": 476250.0, "y": 1190625.0, "width": 2828925.0, "height": 1977628.0}, "kind": "rect", "fontFamily": "Noto Sans KR", "fontPt": 33.0, "fill": "#244FC7", "textColor": "#FFFFFF"},
                "blocks": [{"id": "pptx-shape-26", "bounds": {"x": 4286250.0, "y": 3181350.0, "width": 3419475.0, "height": 342900.0}, "kind": "rect", "fontFamily": "Noto Sans KR", "fontPt": 9.0, "fill": "#FFFFFF", "textColor": "#4B5D88"}],
            },
            "section": {
                "sourceSlideNumber": 6,
                "title": {"id": "pptx-shape-5", "bounds": {"x": 323850.0, "y": 933450.0, "width": 2647950.0, "height": 1856184.0}, "kind": "rect", "fontFamily": "Noto Sans KR", "fontPt": 31.5, "fill": "#244FC7", "textColor": "#FFFFFF"},
                "blocks": [{"id": "pptx-shape-6", "bounds": {"x": 323850.0, "y": 3018234.0, "width": 2647950.0, "height": 959644.0}, "kind": "rect", "fontFamily": "Noto Sans KR", "fontPt": 11.25, "fill": "#244FC7", "textColor": "#DCE8FF"}],
            },
            "content": {
                "sourceSlideNumber": 2,
                "title": {"id": "pptx-shape-3", "bounds": {"x": 323850.0, "y": 1224409.0, "width": 7143750.0, "height": 423416.0}, "kind": "rect", "fontFamily": "Noto Sans KR", "fontPt": 29.25, "fill": "#244FC7", "textColor": "#FFFFFF"},
                "blocks": [
                    {"id": "pptx-shape-4", "bounds": {"x": 8029575.0, "y": 948779.0, "width": 3838575.0, "height": 679996.0}, "kind": "rect", "fontFamily": "Noto Sans KR", "fontPt": 10.5, "fill": "#244FC7", "textColor": "#DCE8FF"},
                    {"id": "pptx-shape-5", "bounds": {"x": 552450.0, "y": 2085975.0, "width": 2673846.0, "height": 205680.0}, "kind": "rect", "fontFamily": "Noto Sans KR", "fontPt": 13.5, "fill": "#FFFFFF", "textColor": "#071B4F"},
                    {"id": "pptx-shape-22", "bounds": {"x": 7277100.0, "y": 2019300.0, "width": 1306562.0, "height": 428625.0}, "kind": "rect", "fontFamily": "Noto Sans KR", "fontPt": 13.5, "fill": "#244FC7", "textColor": "#FFFFFF"},
                ],
            },
            "process": {
                "sourceSlideNumber": 5,
                "title": {"id": "pptx-shape-12", "bounds": {"x": 3695700.0, "y": 523875.0, "width": 5095875.0, "height": 565547.0}, "kind": "rect", "fontFamily": "Noto Sans KR", "fontPt": 16.5, "fill": "#FFFFFF", "textColor": "#071B4F"},
                "blocks": [
                    {"id": "pptx-shape-5", "bounds": {"x": 361950.0, "y": 2247007.0, "width": 2628900.0, "height": 917377.0}, "kind": "rect", "fontFamily": "Noto Sans KR", "fontPt": 10.5, "fill": "#244FC7", "textColor": "#DCE8FF"},
                    {"id": "pptx-shape-8", "bounds": {"x": 361950.0, "y": 5514975.0, "width": 2705100.0, "height": 685800.0}, "kind": "rect", "fontFamily": "Noto Sans KR", "fontPt": 12.0, "fill": "#244FC7", "textColor": "#FFFFFF"},
                    {"id": "pptx-shape-42", "bounds": {"x": 9439275.0, "y": 3760291.0, "width": 2095500.0, "height": 285750.0}, "kind": "rect", "fontFamily": "Noto Sans KR", "fontPt": 7.5, "fill": "#FFFFFF", "textColor": "#071B4F"},
                    {"id": "pptx-shape-43", "bounds": {"x": 3914775.0, "y": 5757416.0, "width": 1676400.0, "height": 185738.0}, "kind": "rect", "fontFamily": "Noto Sans KR", "fontPt": 11.25, "fill": "#FFFFFF", "textColor": "#071B4F"},
                ],
            },
            "closing": {
                "sourceSlideNumber": 18,
                "title": {"id": "pptx-shape-11", "bounds": {"x": 8848725.0, "y": 1209675.0, "width": 2733675.0, "height": 157907.0}, "kind": "rect", "fontFamily": "Noto Sans KR", "fontPt": 8.4, "fill": "#FFFFFF", "textColor": "#405174"},
                "blocks": [{"id": "pptx-shape-38", "bounds": {"x": 6105525.0, "y": 5715595.0, "width": 5629275.0, "height": 847130.0}, "kind": "rect", "fontFamily": "Noto Sans KR", "fontPt": 8.4, "fill": "#244FC7", "textColor": "#FFFFFF"}],
            },
        },
    },
    BLUE_INFOGRAPHIC_RETAINED_REFERENCE_ID: {
        "sourceDigest": "sha256:f39536d3c0eb31be5c16ab1bf436825a3ab427f972d2eae3b44a5d11395a61fc",
        "sourceWidth": 12192000.0,
        "sourceHeight": 6858000.0,
        "layoutPrefix": "blue-infographic",
        "accent": "#1E68D7",
        "darkAccent": "#073B83",
        "layouts": {
            "cover": {
                "sourceSlideNumber": 1,
                "title": {"id": "pptx-shape-5", "bounds": {"x": 552450.0, "y": 1752600.0, "width": 6572250.0, "height": 1809750.0}, "kind": "rect", "fontFamily": "Arial", "fontPt": 51.0, "fill": "#FFFFFF", "textColor": "#14233B"},
                "blocks": [{"id": "pptx-shape-6", "bounds": {"x": 590550.0, "y": 3733800.0, "width": 5810250.0, "height": 819150.0}, "kind": "rect", "fontFamily": "Arial", "fontPt": 18.0, "fill": "#FFFFFF", "textColor": "#667085"}],
            },
            "section": {
                "sourceSlideNumber": 2,
                "title": {"id": "pptx-shape-3", "bounds": {"x": 590550.0, "y": 1028700.0, "width": 3048000.0, "height": 1428750.0}, "kind": "rect", "fontFamily": "Arial", "fontPt": 82.5, "fill": "#FFFFFF", "textColor": "#6FA8FF"},
                "blocks": [{"id": "pptx-shape-6", "bounds": {"x": 5219700.0, "y": 1676400.0, "width": 5905500.0, "height": 1428750.0}, "kind": "rect", "fontFamily": "Arial", "fontPt": 40.5, "fill": "#FFFFFF", "textColor": "#14233B"}],
            },
            "content": {
                "sourceSlideNumber": 3,
                "title": {"id": "pptx-shape-6", "bounds": {"x": 495300.0, "y": 704850.0, "width": 9715500.0, "height": 514350.0}, "kind": "rect", "fontFamily": "Arial", "fontPt": 28.5, "fill": "#FFFFFF", "textColor": "#14233B"},
                "blocks": [
                    {"id": "pptx-shape-7", "bounds": {"x": 514350.0, "y": 1200150.0, "width": 9906000.0, "height": 323850.0}, "kind": "rect", "fontFamily": "Arial", "fontPt": 12.0, "fill": "#FFFFFF", "textColor": "#667085"},
                    {"id": "pptx-shape-11", "bounds": {"x": 1352550.0, "y": 1952625.0, "width": 1333500.0, "height": 514350.0}, "kind": "rect", "fontFamily": "Arial", "fontPt": 28.5, "fill": "#FFFFFF", "textColor": "#073B83"},
                    {"id": "pptx-shape-18", "bounds": {"x": 1352550.0, "y": 3152775.0, "width": 1333500.0, "height": 514350.0}, "kind": "rect", "fontFamily": "Arial", "fontPt": 28.5, "fill": "#FFFFFF", "textColor": "#073B83"},
                ],
            },
            "process": {
                "sourceSlideNumber": 4,
                "title": {"id": "pptx-shape-6", "bounds": {"x": 495300.0, "y": 704850.0, "width": 9715500.0, "height": 514350.0}, "kind": "rect", "fontFamily": "Arial", "fontPt": 28.5, "fill": "#FFFFFF", "textColor": "#14233B"},
                "blocks": [
                    {"id": "pptx-shape-34", "bounds": {"x": 2133600.0, "y": 5238750.0, "width": 8572500.0, "height": 419100.0}, "kind": "rect", "fontFamily": "Arial", "fontPt": 15.0, "fill": "#FFFFFF", "textColor": "#14233B"},
                    {"id": "pptx-shape-7", "bounds": {"x": 514350.0, "y": 1200150.0, "width": 9906000.0, "height": 323850.0}, "kind": "rect", "fontFamily": "Arial", "fontPt": 12.0, "fill": "#FFFFFF", "textColor": "#667085"},
                    {"id": "pptx-shape-8", "bounds": {"x": 514350.0, "y": 1981200.0, "width": 2324100.0, "height": 1066800.0}, "kind": "rect", "fontFamily": "Arial", "fontPt": 12.75, "fill": "#0A2A5C", "textColor": "#FFFFFF"},
                    {"id": "pptx-shape-9", "bounds": {"x": 2667000.0, "y": 1981200.0, "width": 2324100.0, "height": 1066800.0}, "kind": "rect", "fontFamily": "Arial", "fontPt": 12.75, "fill": "#073B83", "textColor": "#FFFFFF"},
                ],
            },
            "closing": {
                "sourceSlideNumber": 12,
                "title": {"id": "pptx-shape-4", "bounds": {"x": 552450.0, "y": 1447800.0, "width": 5905500.0, "height": 1428750.0}, "kind": "rect", "fontFamily": "Arial", "fontPt": 46.5, "fill": "#073B83", "textColor": "#FFFFFF"},
                "blocks": [{"id": "pptx-shape-5", "bounds": {"x": 590550.0, "y": 3143250.0, "width": 5619750.0, "height": 876300.0}, "kind": "rect", "fontFamily": "Arial", "fontPt": 17.25, "fill": "#073B83", "textColor": "#E9F2FF"}],
            },
        },
    },
}

_REFERENCE_LAYOUT_SPECS: dict[str, dict[str, tuple[int, str, str, str, int]]] = {
    OSB_RETAINED_REFERENCE_ID: _OSB_SOURCE_SLIDES,
    DEVAX_RETAINED_REFERENCE_ID: {
        "cover": (1, "cover", "cover", "cover-summary", 1),
        "section": (2, "section", "section", "section-summary", 1),
        "content": (6, "content", "content", "content-card", 3),
        "process": (7, "process", "content", "process-step", 4),
        "closing": (10, "closing", "closing", "closing-message", 1),
    },
    DEVAX_AI_RETAINED_REFERENCE_ID: {
        "cover": (3, "cover", "cover", "cover-summary", 1),
        "section": (6, "section", "section", "section-summary", 1),
        "content": (2, "content", "content", "content-card", 3),
        "process": (5, "process", "content", "process-step", 4),
        "closing": (18, "closing", "closing", "closing-message", 1),
    },
    BLUE_INFOGRAPHIC_RETAINED_REFERENCE_ID: {
        "cover": (1, "cover", "cover", "cover-summary", 1),
        "section": (2, "section", "section", "section-summary", 1),
        "content": (3, "content", "content", "content-card", 3),
        "process": (4, "process", "content", "process-step", 4),
        "closing": (12, "closing", "closing", "closing-message", 1),
    },
}

# Text-bearing shapes in retained PPTX files frequently include terse labels,
# while generated semantic copy is materially longer.  The source shapes stay
# frozen as silhouette decorations; these per-layout flows define readable,
# template-specific text regions inside the retained content rectangle.  The
# flow identity is persisted in the resolved snapshot, so preview and OOXML do
# not independently infer geometry.
_PACKAGED_TEXT_FLOWS: dict[str, dict[str, str]] = {
    DEVAX_RETAINED_REFERENCE_ID: {
        "cover": "single",
        "section": "single",
        "content": "lead-top-two-column",
        "process": "feature-left-three-stack",
        "closing": "single",
    },
    DEVAX_AI_RETAINED_REFERENCE_ID: {
        "cover": "single",
        "section": "single",
        "content": "feature-left-two-stack",
        "process": "matrix-2x2",
        "closing": "single",
    },
    BLUE_INFOGRAPHIC_RETAINED_REFERENCE_ID: {
        "cover": "single",
        "section": "single",
        "content": "intro-top-two-column",
        "process": "header-pair-footer",
        "closing": "single",
    },
}

_PACKAGED_RETAINED_SOURCES: dict[str, dict[str, Any]] = {
    OSB_RETAINED_REFERENCE_ID: {
        **_PACKAGED_OSB_SOURCE,
        "layoutPrefix": "osb",
        "accent": "#0E75BC",
        "darkAccent": "#0F4A9C",
    },
    **_PACKAGED_ADDITIONAL_SOURCES,
}


def _slide_nodes(slide: ET.Element) -> list[ET.Element]:
    return [
        node
        for tag in ("sp", "graphicFrame", "pic")
        for node in slide.findall(f".//p:{tag}", _NS)
        if _emu_rect(node) is not None
    ]


def _source_title(nodes: list[ET.Element], source_height: float) -> ET.Element:
    text_nodes = [node for node in nodes if _shape_text(node)]
    header = [
        node
        for node in text_nodes
        if (_emu_rect(node) or {"y": source_height})["y"] <= source_height * 0.16
    ]
    pool = header or text_nodes
    if not pool:
        raise ValueError("retained OSB source slide has no title text geometry")
    return max(
        pool,
        key=lambda node: (
            _font_pt(node, 0.0),
            (_emu_rect(node) or {"width": 0.0})["width"],
            len(_shape_text(node)),
        ),
    )


def _source_body_nodes(
    nodes: list[ET.Element],
    *,
    title_node: ET.Element,
    archetype: str,
    desired: int,
    source_width: float,
    source_height: float,
) -> list[ET.Element]:
    candidates = []
    for node in nodes:
        if node is title_node:
            continue
        bounds = _emu_rect(node)
        if bounds is None:
            continue
        if bounds["y"] < source_height * 0.12 or bounds["y"] > source_height * 0.93:
            continue
        text = _shape_text(node)
        is_table = node.tag == f"{{{_P}}}graphicFrame"
        if not text and not is_table:
            continue
        area = bounds["width"] * bounds["height"]
        score = area
        if archetype in {"content", "process"} and len(text) >= 35:
            score *= 4.0
        if archetype == "data" and is_table:
            score *= 8.0
        if archetype == "timeline" and bounds["width"] >= source_width * 0.14:
            score *= 3.0
        if archetype == "architecture" and bounds["width"] >= source_width * 0.08:
            score *= 2.0
        candidates.append((score, bounds["y"], bounds["x"], node))
    candidates.sort(key=lambda item: (-item[0], item[1], item[2]))
    selected = [item[3] for item in candidates[:desired]]
    if not selected and archetype == "closing":
        selected = [title_node]
    if not selected:
        raise ValueError(f"retained OSB source slide has no {archetype} content geometry")
    return selected


def _union(nodes: list[ET.Element]) -> dict[str, float]:
    bounds = [_emu_rect(node) for node in nodes]
    values = [value for value in bounds if value is not None]
    left = min(value["x"] for value in values)
    top = min(value["y"] for value in values)
    right = max(value["x"] + value["width"] for value in values)
    bottom = max(value["y"] + value["height"] for value in values)
    return _rect(left, top, right - left, bottom - top)


def _map_source_bounds(
    source: dict[str, float],
    source_union: dict[str, float],
    target: dict[str, float],
) -> dict[str, float]:
    return _rect(
        target["x"]
        + (source["x"] - source_union["x"])
        / source_union["width"]
        * target["width"],
        target["y"]
        + (source["y"] - source_union["y"])
        / source_union["height"]
        * target["height"],
        source["width"] / source_union["width"] * target["width"],
        source["height"] / source_union["height"] * target["height"],
    )


def _source_layout_library(
    slides: dict[int, ET.Element],
    *,
    source_width: float,
    source_height: float,
    anchors: dict[str, dict[str, dict[str, float]]],
) -> list[dict[str, Any]]:
    layouts: list[dict[str, Any]] = []
    for archetype, (
        source_slide_number,
        layout_id,
        slide_role,
        semantic_role,
        desired,
    ) in _OSB_SOURCE_SLIDES.items():
        slide = slides.get(source_slide_number)
        if slide is None:
            raise ValueError(
                f"retained OSB reference is missing source slide {source_slide_number}"
            )
        nodes = _slide_nodes(slide)
        title_node = _source_title(nodes, source_height)
        body_nodes = _source_body_nodes(
            nodes,
            title_node=title_node,
            archetype=archetype,
            desired=desired,
            source_width=source_width,
            source_height=source_height,
        )
        body_union = _union(body_nodes)
        policy_title = anchors[slide_role]["title"]
        policy_content = anchors[slide_role]["content"]
        envelope_left = min(policy_title["x"], policy_content["x"])
        envelope_top = min(policy_title["y"], policy_content["y"])
        envelope_right = max(
            policy_title["x"] + policy_title["width"],
            policy_content["x"] + policy_content["width"],
        )
        envelope_bottom = max(
            policy_title["y"] + policy_title["height"],
            policy_content["y"] + policy_content["height"],
        )
        target_envelope = _rect(
            envelope_left,
            envelope_top,
            envelope_right - envelope_left,
            envelope_bottom - envelope_top,
        )
        source_layout_union = _union([title_node, *body_nodes])
        target_content = _map_source_bounds(
            body_union,
            source_layout_union,
            target_envelope,
        )
        source_title_bounds = _emu_rect(title_node)
        assert source_title_bounds is not None
        title_bounds = _map_source_bounds(
            source_title_bounds,
            source_layout_union,
            target_envelope,
        )
        title_marker_x = title_bounds["x"]
        title_bounds = _reserve_title_marker(title_bounds, slide_role=slide_role)
        title_style = {
            "fontFamily": _font_face(title_node, "나눔스퀘어 ExtraBold"),
            "fontPt": _font_pt(title_node, 21.33),
            "fontWeight": 800,
            "color": "#404040",
            "alignment": "center" if slide_role in {"cover", "closing"} else "left",
            "lineHeight": 1.2,
            "verticalAlignment": "middle",
        }
        title = {
            "bounds": title_bounds,
            "textStyle": title_style,
            "sourceShapeId": _source_shape_id(title_node),
            "sourceBounds": source_title_bounds,
            "accent": {
                "id": f"{layout_id}-title-marker",
                "semanticRole": "template-accent",
                "kind": "ellipse",
                "bounds": _rect(
                    title_marker_x,
                    title_bounds["y"] + title_bounds["height"] * 0.32,
                    min(18.0, title_bounds["height"] * 0.36),
                    min(18.0, title_bounds["height"] * 0.36),
                ),
                "fill": "#FFFFFF",
                "stroke": "#0F4A9C",
                "strokeWidth": 3.0,
                "zIndex": 12,
                "sourceShapeId": _source_shape_id(title_node),
            },
        }
        blocks = []
        for index, node in enumerate(
            sorted(
                body_nodes,
                key=lambda item: (
                    (_emu_rect(item) or {"y": 0})["y"],
                    (_emu_rect(item) or {"x": 0})["x"],
                ),
            )
        ):
            source_bounds = _emu_rect(node)
            assert source_bounds is not None
            mapped = _map_source_bounds(source_bounds, body_union, target_content)
            block = _block(
                f"{layout_id}-block-{index + 1}",
                semantic_role,
                mapped,
                source_shape_id=_source_shape_id(node),
                font_family=_font_face(node, "나눔스퀘어"),
                font_pt=_font_pt(node, 11.0),
                fill=_shape_fill(node, "#F2F2F2"),
                accent="#0E75BC",
                shape=_source_kind(node),
            )
            block["sourceBounds"] = source_bounds
            block["sourceSlideNumber"] = source_slide_number
            blocks.append(block)
        decoration_candidates = []
        union_right = body_union["x"] + body_union["width"]
        union_bottom = body_union["y"] + body_union["height"]
        for node in nodes:
            if node in body_nodes or node is title_node or _shape_text(node):
                continue
            bounds = _emu_rect(node)
            if bounds is None:
                continue
            right = bounds["x"] + bounds["width"]
            bottom = bounds["y"] + bounds["height"]
            if (
                right <= body_union["x"]
                or bounds["x"] >= union_right
                or bottom <= body_union["y"]
                or bounds["y"] >= union_bottom
            ):
                continue
            area = bounds["width"] * bounds["height"]
            if area >= source_width * source_height * 0.7:
                continue
            decoration_candidates.append((area, node))
        decoration_candidates.sort(key=lambda item: -item[0])
        decorations = []
        for index, (_, node) in enumerate(decoration_candidates[:3]):
            source_bounds = _emu_rect(node)
            assert source_bounds is not None
            decoration = _decoration(
                f"{layout_id}-decoration-{index + 1}",
                "template-accent",
                _source_kind(node),
                _map_source_bounds(source_bounds, body_union, target_content),
                fill=_shape_fill(node, "#0E75BC"),
                stroke="#0F4A9C",
                z_index=4 + index,
                source_shape_id=_source_shape_id(node),
            )
            decoration["sourceBounds"] = source_bounds
            decoration["sourceSlideNumber"] = source_slide_number
            decorations.append(decoration)
        blocks, decorations = _separate_timeline_frame(
            archetype, blocks, decorations
        )
        layouts.append(
            {
                "id": f"osb-{layout_id}",
                "archetype": archetype,
                "slideRole": slide_role,
                "sourceSlideNumber": source_slide_number,
                "title": title,
                "contentRect": target_content,
                "blocks": blocks,
                "decorations": decorations,
            }
        )
    return layouts


def _dict_union(values: list[dict[str, float]]) -> dict[str, float]:
    left = min(value["x"] for value in values)
    top = min(value["y"] for value in values)
    right = max(value["x"] + value["width"] for value in values)
    bottom = max(value["y"] + value["height"] for value in values)
    return _rect(left, top, right - left, bottom - top)


def _relative_luminance(color: str) -> float:
    if not isinstance(color, str) or not re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
        raise ValueError(f"retained template color is not canonical hex: {color}")
    channels = [int(color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [
        value / 12.92
        if value <= 0.04045
        else ((value + 0.055) / 1.055) ** 2.4
        for value in channels
    ]
    return linear[0] * 0.2126 + linear[1] * 0.7152 + linear[2] * 0.0722


def _contrast_ratio(left: str, right: str) -> float:
    lighter, darker = sorted(
        (_relative_luminance(left), _relative_luminance(right)), reverse=True
    )
    return (lighter + 0.05) / (darker + 0.05)


def _readable_template_title_color(
    source_color: str,
    *,
    background_color: str,
    palette: tuple[str, ...],
) -> str:
    """Keep the source color when readable, otherwise use its own palette."""

    candidates = tuple(dict.fromkeys((source_color, *palette)))
    if _contrast_ratio(source_color, background_color) >= 4.5:
        return source_color
    readable = [
        color
        for color in candidates[1:]
        if _contrast_ratio(color, background_color) >= 4.5
    ]
    if readable:
        return max(
            readable,
            key=lambda color: (_contrast_ratio(color, background_color), -candidates.index(color)),
        )
    raise ValueError("retained template palette has no readable company-background title color")


def _retained_cover_body_region(
    envelope: dict[str, float],
    title_bounds: dict[str, float],
) -> dict[str, float]:
    """Choose the largest title-free cover region inside the policy envelope."""

    gap = 16.0
    envelope_right = envelope["x"] + envelope["width"]
    envelope_bottom = envelope["y"] + envelope["height"]
    title_right = title_bounds["x"] + title_bounds["width"]
    title_bottom = title_bounds["y"] + title_bounds["height"]
    candidates: list[dict[str, float]] = []
    right_x = title_right + gap
    if envelope_right - right_x >= envelope["width"] * 0.28:
        candidates.append(
            _rect(
                right_x,
                max(envelope["y"], title_bounds["y"]),
                envelope_right - right_x - 0.02,
                min(envelope_bottom, title_bottom) - max(envelope["y"], title_bounds["y"]) - 0.02,
            )
        )
    below_y = title_bottom + gap
    if envelope_bottom - below_y >= 48.0:
        candidates.append(
            _rect(
                envelope["x"],
                below_y,
                envelope["width"] - 0.02,
                envelope_bottom - below_y - 0.02,
            )
        )
    candidates = [
        item for item in candidates if item["width"] > 0 and item["height"] > 0
    ]
    if not candidates:
        raise ValueError("retained cover has no title-free semantic body region")
    return max(candidates, key=lambda item: item["width"] * item["height"])


def _packaged_layout_library(
    retained_reference_id: str,
    anchors: dict[str, dict[str, dict[str, float]]],
    *,
    background_color: str,
) -> list[dict[str, Any]]:
    packaged = _PACKAGED_RETAINED_SOURCES[retained_reference_id]
    layout_specs = _REFERENCE_LAYOUT_SPECS[retained_reference_id]
    layout_prefix = str(packaged["layoutPrefix"])
    accent = str(packaged["accent"])
    dark_accent = str(packaged["darkAccent"])
    layouts: list[dict[str, Any]] = []
    for archetype, (_, layout_id, slide_role, semantic_role, _) in layout_specs.items():
        source = packaged["layouts"][archetype]
        source_title = source["title"]
        source_blocks = source["blocks"]
        policy_title = anchors[slide_role]["title"]
        policy_content = anchors[slide_role]["content"]
        envelope = _dict_union([policy_title, policy_content])
        # The authored reference owns its whitespace and relative placement.
        # Scale the complete source canvas into the optional AI envelope instead
        # of stretching each layout's occupied shapes to master-owned slots.
        source_layout_union = _rect(
            0.0,
            0.0,
            float(packaged["sourceWidth"]),
            float(packaged["sourceHeight"]),
        )
        title_bounds = _map_source_bounds(
            source_title["bounds"], source_layout_union, envelope
        )
        body_union = _dict_union([item["bounds"] for item in source_blocks])
        source_content_rect = _map_source_bounds(
            body_union, source_layout_union, envelope
        )
        content_rect = source_content_rect
        if archetype in {"section", "closing"} and title_bounds == content_rect:
            title_bounds = _rect(
                envelope["x"], envelope["y"], envelope["width"], envelope["height"] * 0.42
            )
            content_rect = _rect(
                envelope["x"] + envelope["width"] * 0.12,
                envelope["y"] + envelope["height"] * 0.5,
                envelope["width"] * 0.76,
                envelope["height"] * 0.34,
            )
        title_marker_x = title_bounds["x"]
        title_bounds = _reserve_title_marker(title_bounds, slide_role=slide_role)
        if (
            retained_reference_id in _PACKAGED_TEXT_FLOWS
            and archetype == "cover"
        ):
            content_rect = _retained_cover_body_region(envelope, title_bounds)
        blocks: list[dict[str, Any]] = []
        for index, item in enumerate(
            sorted(source_blocks, key=lambda value: (value["bounds"]["y"], value["bounds"]["x"]))
        ):
            block = _block(
                f"{layout_id}-block-{index + 1}",
                semantic_role,
                _map_source_bounds(
                    item["bounds"], body_union, source_content_rect
                ),
                source_shape_id=item["id"],
                font_family=item["fontFamily"],
                font_pt=item["fontPt"],
                fill=item["fill"],
                accent=accent,
                text_color=item.get("textColor", "#404040"),
                shape=item["kind"],
            )
            block["sourceBounds"] = item["bounds"]
            block["sourceSlideNumber"] = source["sourceSlideNumber"]
            blocks.append(block)
        source_title_color = source_title.get("textColor", "#404040")
        title_color = (
            _readable_template_title_color(
                source_title_color,
                background_color=background_color,
                palette=(dark_accent, accent, source_title["fill"]),
            )
            if (
                retained_reference_id in _PACKAGED_TEXT_FLOWS
                and slide_role == "cover"
            )
            else source_title_color
        )
        title = {
            "bounds": title_bounds,
            "textStyle": {
                "fontFamily": source_title["fontFamily"],
                "fontPt": source_title["fontPt"],
                "fontWeight": 800,
                "color": title_color,
                "alignment": "center" if slide_role in {"cover", "closing"} else "left",
                "lineHeight": 1.2,
                "verticalAlignment": "middle",
            },
            "sourceShapeId": source_title["id"],
            "sourceBounds": source_title["bounds"],
            "accent": {
                "id": f"{layout_id}-title-marker",
                "semanticRole": "template-accent",
                "kind": "ellipse",
                "bounds": _rect(
                    title_marker_x,
                    title_bounds["y"] + title_bounds["height"] * 0.32,
                    min(18.0, title_bounds["height"] * 0.36),
                    min(18.0, title_bounds["height"] * 0.36),
                ),
                "fill": "#FFFFFF",
                "stroke": dark_accent,
                "strokeWidth": 3.0,
                "zIndex": 12,
                "sourceShapeId": source_title["id"],
            },
        }
        blocks, decorations = _separate_timeline_frame(archetype, blocks, [])
        layouts.append(
            {
                "id": f"{layout_prefix}-{layout_id}",
                "archetype": archetype,
                "slideRole": slide_role,
                "sourceSlideNumber": source["sourceSlideNumber"],
                "canvasBackground": background_color,
                **(
                    {"textFlow": _PACKAGED_TEXT_FLOWS[retained_reference_id][archetype]}
                    if retained_reference_id in _PACKAGED_TEXT_FLOWS
                    else {}
                ),
                "title": title,
                "contentRect": content_rect,
                "blocks": blocks,
                "decorations": decorations,
            }
        )
    return layouts


def _template_background_color(packaged: dict[str, Any]) -> str:
    """Infer a retained-template canvas color without consulting a master."""

    explicit = packaged.get("backgroundColor")
    if isinstance(explicit, str):
        return explicit
    fills = [
        str(item["fill"])
        for layout in packaged["layouts"].values()
        for item in [layout["title"], *layout["blocks"]]
        if isinstance(item.get("fill"), str)
    ]
    if not fills:
        return "#FFFFFF"
    counts = {color: fills.count(color) for color in dict.fromkeys(fills)}
    return max(counts, key=lambda color: (counts[color], -fills.index(color)))


def _default_role_envelopes(
    canvas_width: float,
    canvas_height: float,
) -> dict[str, dict[str, float]]:
    envelope = _rect(0.0, 0.0, float(canvas_width), float(canvas_height))
    return {
        role: dict(envelope)
        for role in ("cover", "section", "content", "closing")
    }


def _anchors_from_envelopes(
    envelopes: dict[str, Any],
    *,
    canvas_width: float,
    canvas_height: float,
) -> dict[str, dict[str, dict[str, float]]]:
    """Split only for the legacy compiler interface; union stays the envelope."""

    defaults = _default_role_envelopes(canvas_width, canvas_height)
    anchors: dict[str, dict[str, dict[str, float]]] = {}
    for role, fallback in defaults.items():
        envelope = _plain_rect(envelopes.get(role, fallback))
        split = envelope["y"] + envelope["height"] * 0.24
        anchors[role] = {
            "title": _rect(
                envelope["x"],
                envelope["y"],
                envelope["width"],
                max(1.0, split - envelope["y"]),
            ),
            "content": _rect(
                envelope["x"],
                split,
                envelope["width"],
                max(1.0, envelope["y"] + envelope["height"] - split),
            ),
        }
    return anchors


def _sha256_digest(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _relationship_part_name(source_part: str) -> str:
    directory, name = posixpath.split(source_part)
    return posixpath.join(directory, "_rels", f"{name}.rels")


def _resolve_relationship_target(source_part: str, target: str) -> str:
    if target.startswith("/"):
        return target.removeprefix("/")
    return posixpath.normpath(posixpath.join(posixpath.dirname(source_part), target))


def _part_media_type(part_name: str) -> str:
    extension = posixpath.splitext(part_name)[1].lower()
    return {
        ".emf": "image/x-emf",
        ".gif": "image/gif",
        ".jpeg": "image/jpeg",
        ".jpg": "image/jpeg",
        ".png": "image/png",
        ".svg": "image/svg+xml",
        ".wmf": "image/x-wmf",
        ".xml": "application/xml",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }.get(extension, "application/octet-stream")


def _part_relationships(
    archive: ZipFile,
    source_part: str,
    *,
    include_target_bytes: bool = True,
) -> list[dict[str, Any]]:
    relationship_part = _relationship_part_name(source_part)
    if relationship_part not in archive.namelist():
        return []
    root = ET.fromstring(archive.read(relationship_part))
    records: list[dict[str, Any]] = []
    for relationship in root.findall("pr:Relationship", _NS):
        relationship_id = relationship.get("Id")
        relationship_type = relationship.get("Type")
        target = relationship.get("Target")
        if not relationship_id or not relationship_type or not target:
            raise ValueError(f"retained relationship is malformed: {relationship_part}")
        external = relationship.get("TargetMode") == "External"
        target_part = None if external else _resolve_relationship_target(source_part, target)
        if target_part is not None and target_part not in archive.namelist():
            raise ValueError(
                f"retained relationship target is missing: {source_part}/{relationship_id}"
            )
        target_value = archive.read(target_part) if target_part is not None else None
        records.append(
            {
                "id": relationship_id,
                "type": relationship_type.rsplit("/", 1)[-1],
                "target": target if external else target_part,
                "external": external,
                **(
                    {
                        "targetDigest": _sha256_digest(target_value),
                        "targetMediaType": _part_media_type(target_part),
                        **(
                            {
                                "targetBytesBase64": base64.b64encode(
                                    target_value
                                ).decode("ascii")
                            }
                            if include_target_bytes
                            else {}
                        ),
                    }
                    if target_part is not None and target_value is not None
                    else {}
                ),
            }
        )
    return sorted(records, key=lambda item: item["id"])


def _related_part(
    relationships: list[dict[str, Any]],
    relationship_type: str,
) -> str | None:
    matching = [
        item["target"]
        for item in relationships
        if item["type"] == relationship_type and not item["external"]
    ]
    if len(matching) > 1:
        raise ValueError(f"retained source has multiple {relationship_type} relationships")
    return str(matching[0]) if matching else None


def _source_part_closure(
    archive: ZipFile,
    source_slide_part: str,
) -> list[dict[str, Any]]:
    pending = [source_slide_part]
    visited: set[str] = set()
    parts: list[dict[str, Any]] = []
    while pending:
        part_name = pending.pop()
        if part_name in visited:
            continue
        if part_name not in archive.namelist():
            raise ValueError(f"retained source part is missing: {part_name}")
        visited.add(part_name)
        value = archive.read(part_name)
        media_type = _part_media_type(part_name)
        is_element_media = posixpath.splitext(part_name)[1].lower() in {
            ".emf",
            ".gif",
            ".jpeg",
            ".jpg",
            ".pict",
            ".png",
            ".svg",
            ".wmf",
        }
        parts.append(
            {
                "partName": part_name,
                "digest": _sha256_digest(value),
                "sizeBytes": len(value),
                "mediaType": media_type,
                **(
                    {"partBytesBase64": base64.b64encode(value).decode("ascii")}
                    if not is_element_media
                    else {}
                ),
            }
        )
        relationship_part = _relationship_part_name(part_name)
        if relationship_part in archive.namelist():
            relationship_value = archive.read(relationship_part)
            parts.append(
                {
                    "partName": relationship_part,
                    "digest": _sha256_digest(relationship_value),
                    "sizeBytes": len(relationship_value),
                    "mediaType": "application/xml",
                    "partBytesBase64": base64.b64encode(relationship_value).decode(
                        "ascii"
                    ),
                }
            )
            for relationship in _part_relationships(
                archive,
                part_name,
                include_target_bytes=False,
            ):
                target = relationship["target"]
                if (
                    relationship["type"] not in {"comments", "commentAuthors", "notesSlide"}
                    and not relationship["external"]
                    and isinstance(target, str)
                ):
                    pending.append(target)
    return sorted(parts, key=lambda item: item["partName"])


def _native_element_bounds(element: ET.Element) -> dict[str, float] | None:
    bounds = _emu_rect(element)
    if bounds is not None:
        return bounds
    transform = element.find("./p:grpSpPr/a:xfrm", _NS)
    if transform is None:
        return None
    offset = transform.find("a:off", _NS)
    extent = transform.find("a:ext", _NS)
    if offset is None or extent is None:
        return None
    return {
        "x": float(offset.get("x", "0")),
        "y": float(offset.get("y", "0")),
        "width": float(extent.get("cx", "0")),
        "height": float(extent.get("cy", "0")),
    }


def _element_relationship_ids(element: ET.Element) -> list[str]:
    ids = {
        value
        for node in element.iter()
        for name, value in node.attrib.items()
        if name.startswith(f"{{{_R}}}") and value
    }
    return sorted(ids)


def _native_element_kind(
    element: ET.Element,
    relationships: list[dict[str, Any]],
) -> tuple[str, str, dict[str, Any] | None]:
    tag = element.tag.rsplit("}", 1)[-1]
    if tag == "sp":
        return ("text" if element.find("./p:txBody", _NS) is not None else "shape"), "native-ooxml", None
    if tag == "cxnSp":
        return "connector", "native-ooxml", None
    if tag == "grpSp":
        return "group", "native-ooxml", None
    if tag == "pic":
        relation_ids = set(_element_relationship_ids(element))
        targets = [
            item
            for item in relationships
            if item["id"] in relation_ids and not item["external"]
        ]
        supported = next(
            (
                item
                for item in targets
                if posixpath.splitext(str(item["target"]))[1].lower()
                in {".gif", ".jpeg", ".jpg", ".png", ".svg"}
            ),
            None,
        )
        if supported is not None:
            return "image", "native-ooxml", None
        unsupported = next(
            (
                item
                for item in targets
                if posixpath.splitext(str(item["target"]))[1].lower()
                in {".emf", ".wmf", ".eps", ".pict"}
            ),
            None,
        )
        if unsupported is not None:
            target = str(unsupported["target"])
            return (
                "image",
                "element-raster-fallback",
                {
                    "mode": "element-raster",
                    "reason": f"unsupported-image-format:{posixpath.splitext(target)[1].lower()}",
                    "fullSlide": False,
                    "sourceAssetDigest": unsupported["targetDigest"],
                },
            )
        return "image", "native-ooxml", None
    if tag == "graphicFrame":
        graphic_data = element.find(".//a:graphicData", _NS)
        uri = graphic_data.get("uri", "") if graphic_data is not None else ""
        if uri.endswith("/table"):
            return "table", "native-ooxml", None
        if uri.endswith("/chart"):
            return "chart", "native-ooxml", None
        return (
            "unsupported",
            "element-raster-fallback",
            {
                "mode": "element-raster",
                "reason": f"unsupported-graphic-frame:{uri or 'unknown'}",
                "fullSlide": False,
            },
        )
    return (
        "unsupported",
        "element-raster-fallback",
        {
            "mode": "element-raster",
            "reason": f"unsupported-presentation-element:{tag}",
            "fullSlide": False,
        },
    )


def _native_element_payload(element: ET.Element, fragment_xml: str) -> dict[str, Any]:
    transform = element.find("./p:spPr/a:xfrm", _NS)
    if transform is None:
        transform = element.find("./p:xfrm", _NS)
    if transform is None:
        transform = element.find("./p:grpSpPr/a:xfrm", _NS)
    rotation = (
        round(float(transform.get("rot", "0")) / 60_000.0, 4)
        if transform is not None
        else 0.0
    )
    line = element.find("./p:spPr/a:ln", _NS)
    stroke_width = (
        round(float(line.get("w", "0")) / 12_700.0, 4)
        if line is not None
        else 0.0
    )
    text_color = _direct_rgb(element.find(".//a:rPr/a:solidFill", _NS), "#404040")
    return {
        "fragmentXml": fragment_xml,
        "preset": _source_kind(element),
        "rotation": rotation,
        "fill": _shape_fill(element, "#FFFFFF"),
        "stroke": _direct_rgb(line, "#000000") if line is not None else None,
        "strokeWidth": stroke_width,
        "text": "",
        "textStyle": {
            "fontFamily": _font_face(element, "Noto Sans KR"),
            "fontPt": _font_pt(element, 11.0),
            "color": text_color,
        },
    }


def _native_layer(
    archive: ZipFile,
    *,
    kind: str,
    source_part: str,
    title_shape_id: str,
    body_shape_ids: list[str],
) -> dict[str, Any]:
    source_value = archive.read(source_part)
    root = ET.fromstring(source_value)
    shape_tree = root.find(".//p:spTree", _NS)
    if shape_tree is None:
        raise ValueError(f"retained source layer has no shape tree: {source_part}")
    relationships = _part_relationships(archive, source_part)
    relation_by_id = {item["id"]: item for item in relationships}
    background = root.find("./p:cSld/p:bg", _NS)
    background_value = (
        ET.tostring(background, encoding="utf-8") if background is not None else None
    )
    elements: list[dict[str, Any]] = []
    excluded_bindings: list[str] = []
    for source_index, element in enumerate(list(shape_tree)):
        tag = element.tag.rsplit("}", 1)[-1]
        if tag in {"nvGrpSpPr", "grpSpPr"}:
            continue
        source_shape_id = _source_shape_id(element)
        binding_role = None
        if kind == "slide" and source_shape_id == title_shape_id:
            binding_role = "title"
        elif kind == "slide" and source_shape_id in body_shape_ids:
            binding_role = f"body:{body_shape_ids.index(source_shape_id) + 1}"
        if binding_role is not None:
            excluded_bindings.append(source_shape_id)
            continue
        relationship_ids = _element_relationship_ids(element)
        if any(item not in relation_by_id for item in relationship_ids):
            raise ValueError(
                f"retained element relationship is missing: {source_part}/{source_shape_id}"
            )
        element_kind, materialization, fallback = _native_element_kind(
            element,
            relationships,
        )
        bounds = _native_element_bounds(element)
        if materialization == "element-raster-fallback" and bounds is None:
            raise ValueError(
                f"retained fallback element has no bounds: {source_part}/{source_shape_id}"
            )
        sanitized = copy.deepcopy(element)
        has_text = sanitized.find(".//a:t", _NS) is not None
        if kind == "slide":
            for text in sanitized.findall(".//a:t", _NS):
                text.text = ""
        text_policy = (
            "clear-template-copy"
            if kind == "slide" and has_text
            else "preserve-template-fixed"
        )
        element_value = ET.tostring(sanitized, encoding="utf-8")
        fragment_xml = element_value.decode("utf-8")
        fragment_digest = _sha256_digest(element_value)
        elements.append(
            {
                "sourceShapeId": source_shape_id,
                "sourceIndex": source_index,
                "zIndex": len(elements),
                "kind": element_kind,
                "materialization": materialization,
                "bounds": bounds,
                "sourceXmlDigest": fragment_digest,
                "fragmentDigest": fragment_digest,
                "fragmentXml": fragment_xml,
                "native": _native_element_payload(sanitized, fragment_xml),
                "relationshipIds": relationship_ids,
                "relationships": [relation_by_id[item] for item in relationship_ids],
                "bindingRole": None,
                "textPolicy": text_policy,
                **({"fallback": fallback} if fallback is not None else {}),
            }
        )
    return {
        "kind": kind,
        "sourcePart": source_part,
        "sourcePartDigest": _sha256_digest(source_value),
        "elements": elements,
        "elementCount": len(elements),
        "sourceElementCount": len(elements) + len(excluded_bindings),
        "excludedBindingShapeIds": excluded_bindings,
        "backgroundFragmentXml": (
            background_value.decode("utf-8") if background_value is not None else None
        ),
        "backgroundFragmentDigest": (
            _sha256_digest(background_value) if background_value is not None else None
        ),
    }


def _native_scene(
    archive: ZipFile,
    *,
    source_package_digest: str,
    source_slide_number: int,
    source_width: float,
    source_height: float,
    target_width: float,
    target_height: float,
    title_shape_id: str,
    body_shape_ids: list[str],
) -> dict[str, Any]:
    source_slide_part = f"ppt/slides/slide{source_slide_number}.xml"
    slide_relationships = _part_relationships(archive, source_slide_part)
    layout_part = _related_part(slide_relationships, "slideLayout")
    if layout_part is None:
        raise ValueError(f"retained source slide has no layout: {source_slide_part}")
    layout_relationships = _part_relationships(archive, layout_part)
    master_part = _related_part(layout_relationships, "slideMaster")
    if master_part is None:
        raise ValueError(f"retained source layout has no master: {layout_part}")
    normalized_title_id = title_shape_id.replace(
        "artifact-layout-shape-", "pptx-shape-"
    )
    normalized_body_ids = [
        value.replace("artifact-layout-shape-", "pptx-shape-")
        for value in body_shape_ids
    ]
    layers = [
        _native_layer(
            archive,
            kind=kind,
            source_part=part,
            title_shape_id=normalized_title_id,
            body_shape_ids=normalized_body_ids,
        )
        for kind, part in (
            ("master", master_part),
            ("layout", layout_part),
            ("slide", source_slide_part),
        )
    ]
    slide_binding_ids = {
        value
        for layer in layers
        if layer["kind"] == "slide"
        for value in layer["excludedBindingShapeIds"]
    }
    required_bindings = {normalized_title_id, *normalized_body_ids}
    if not required_bindings.issubset(slide_binding_ids):
        missing = sorted(required_bindings - slide_binding_ids)
        raise ValueError(
            f"retained native scene binding is missing: {source_slide_part}/{missing}"
        )
    scene: dict[str, Any] = {
        "schemaVersion": RETAINED_NATIVE_SCENE_SCHEMA_VERSION,
        "sourcePackageDigest": source_package_digest,
        "sourceSlideNumber": source_slide_number,
        "sourceSlidePart": source_slide_part,
        "sourceCanvas": {"width": source_width, "height": source_height},
        "targetCanvas": {
            "width": round(target_width, 4),
            "height": round(target_height, 4),
        },
        "transform": {
            "offsetX": 0,
            "offsetY": 0,
            "scaleX": float(format(target_width / source_width, ".15g")),
            "scaleY": float(format(target_height / source_height, ".15g")),
        },
        "layers": layers,
        "partClosure": _source_part_closure(archive, source_slide_part),
        "bindings": {
            "titleShapeId": normalized_title_id,
            "bodyShapeIds": normalized_body_ids,
        },
        "fullSlideRasterAllowed": False,
    }
    # JSON numbers have no integer/float distinction, while Python's encoder
    # emits ``620011.0`` and JavaScript emits ``620011`` for the same value.
    # Canonicalize the scene before sealing so both renderer adapters recompute
    # exactly the same digest.
    scene = _canonical_value(scene)
    scene["digest"] = canonical_reference_digest(scene)
    return scene


def _verify_packaged_source(
    retained_reference_id: str,
    reference_path: Path,
) -> None:
    packaged = _PACKAGED_RETAINED_SOURCES[retained_reference_id]
    source_bytes = reference_path.read_bytes()
    digest = f"sha256:{hashlib.sha256(source_bytes).hexdigest()}"
    if digest != packaged["sourceDigest"]:
        raise ValueError(
            f"retained PPTX digest differs from packaged geometry: {retained_reference_id}"
        )
    with ZipFile(reference_path) as archive:
        presentation = ET.fromstring(archive.read("ppt/presentation.xml"))
        slide_size = presentation.find("p:sldSz", _NS)
        if slide_size is None:
            raise ValueError("retained OSB PPTX has no slide size")
        if (
            float(slide_size.get("cx", "0")) != packaged["sourceWidth"]
            or float(slide_size.get("cy", "0")) != packaged["sourceHeight"]
        ):
            raise ValueError(
                f"retained PPTX canvas differs from packaged geometry: {retained_reference_id}"
            )
        for archetype, source in packaged["layouts"].items():
            slide = ET.fromstring(
                archive.read(f"ppt/slides/slide{source['sourceSlideNumber']}.xml")
            )
            indexed: dict[str, list[ET.Element]] = {}
            for node in _slide_nodes(slide):
                indexed.setdefault(_source_shape_id(node), []).append(node)
            for item in [source["title"], *source["blocks"]]:
                if item["id"].startswith("artifact-layout-"):
                    continue
                matching_nodes = indexed.get(item["id"], [])
                if not any(_emu_rect(node) == item["bounds"] for node in matching_nodes):
                    raise ValueError(
                        "retained source geometry differs from packaged snapshot: "
                        f"{retained_reference_id}/{archetype}"
                    )


def compile_retained_reference_layout(
    retained_reference_id: str,
    *,
    canvas_width: float,
    canvas_height: float,
    role_policies: list[Any] | None = None,
    role_envelopes: dict[str, Any] | None = None,
    background_color: str | None = None,
) -> dict[str, Any]:
    """Compile retained geometry independently of legacy master styling.

    ``role_policies`` remains accepted for stored-call compatibility but is not
    consumed: those records historically carried master title/body anchors.
    Only geometry-only ``role_envelopes`` may constrain the outer canvas.
    """

    packaged = _PACKAGED_RETAINED_SOURCES.get(retained_reference_id)
    if packaged is None:
        raise ValueError(f"unsupported retained reference: {retained_reference_id}")
    reference_path = _reference_path(retained_reference_id)
    if retained_reference_id == OSB_RETAINED_REFERENCE_ID and reference_path is None:
        raise ValueError("retained OSB native source PPTX is required")
    if reference_path is not None:
        _verify_packaged_source(retained_reference_id, reference_path)
    source_digest = str(packaged["sourceDigest"])

    del role_policies
    anchors = _anchors_from_envelopes(
        role_envelopes or {},
        canvas_width=canvas_width,
        canvas_height=canvas_height,
    )
    template_background = background_color or _template_background_color(packaged)

    layouts = _packaged_layout_library(
        retained_reference_id,
        anchors,
        background_color=template_background,
    )
    if retained_reference_id == OSB_RETAINED_REFERENCE_ID:
        assert reference_path is not None
        with ZipFile(reference_path) as archive:
            for layout in layouts:
                archetype = str(layout["archetype"])
                source = packaged["layouts"][archetype]
                layout["nativeScene"] = _native_scene(
                    archive,
                    source_package_digest=source_digest,
                    source_slide_number=int(source["sourceSlideNumber"]),
                    source_width=float(packaged["sourceWidth"]),
                    source_height=float(packaged["sourceHeight"]),
                    target_width=float(canvas_width),
                    target_height=float(canvas_height),
                    title_shape_id=str(source["title"]["id"]),
                    body_shape_ids=[str(item["id"]) for item in source["blocks"]],
                )
    payload: dict[str, Any] = {
        "schemaVersion": RETAINED_REFERENCE_LAYOUT_SCHEMA_VERSION,
        "retainedReferenceId": retained_reference_id,
        "sourceDigest": source_digest,
        "canvas": {
            "width": round(float(canvas_width), 4),
            "height": round(float(canvas_height), 4),
        },
        "layouts": layouts,
    }
    payload["digest"] = canonical_reference_digest(payload)
    return payload


__all__ = [
    "BLUE_INFOGRAPHIC_RETAINED_REFERENCE_ID",
    "DEVAX_AI_RETAINED_REFERENCE_ID",
    "DEVAX_RETAINED_REFERENCE_ID",
    "OSB_RETAINED_REFERENCE_ID",
    "RETAINED_REFERENCE_REGISTRY",
    "RETAINED_REFERENCE_LAYOUT_SCHEMA_VERSION",
    "RETAINED_REFERENCE_MARKER_CODEC",
    "canonical_reference_digest",
    "compile_retained_reference_layout",
    "decode_reference_layout_marker",
    "encode_reference_layout_marker",
]
