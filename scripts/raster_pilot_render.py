"""OCR and SVG rendering helpers for the local raster pilot."""

from __future__ import annotations

import csv
import io
import re
import subprocess
import unicodedata
from pathlib import Path
from xml.sax.saxutils import escape

from PIL import Image

IMAGE_ROLES = {"screenshot", "compound"}
TEXT_ROLES = {"text", "table_cell"}
ROLES = IMAGE_ROLES | TEXT_ROLES | {"ignored_decoration"}
MIN_FONT_LOGICAL = 7 * 9525 / 9000


def ocr_tokens(image: Image.Image, *, command: Path, tessdata: Path, timeout: float, psm: int) -> list[dict]:
    source = io.BytesIO()
    image.convert("RGB").save(source, format="PNG")
    argv = [
        str(command),
        "stdin",
        "stdout",
        "--tessdata-dir",
        str(tessdata),
        "-l",
        "kor+eng",
        "--psm",
        str(psm),
        "-c",
        "tessedit_create_tsv=1",
        "-c",
        "tessedit_create_txt=0",
    ]
    completed = subprocess.run(argv, input=source.getvalue(), capture_output=True, timeout=timeout, check=False)
    if completed.returncode:
        raise RuntimeError(f"Tesseract failed (exit {completed.returncode})")
    decoded = completed.stdout.decode("utf-8-sig", errors="replace")
    result: list[dict] = []
    reader = csv.DictReader(io.StringIO(decoded), delimiter="\t")
    required = {"left", "top", "width", "height", "text"}
    if not reader.fieldnames or not required.issubset(reader.fieldnames):
        raise RuntimeError("Tesseract did not return TSV fields")
    for row in reader:
        content = (row.get("text") or "").strip()
        if not content:
            continue
        try:
            bounds = [int(row[key]) for key in ("left", "top", "width", "height")]
        except (KeyError, ValueError):
            continue
        if bounds[2] <= 0 or bounds[3] <= 0:
            continue
        result.append({"text": content, "bbox_px": bounds})
    return result


def classify_token(token: dict, regions: list[dict]) -> tuple[str, list[str]]:
    x, y, w, h = token["bbox_px"]
    matches = []
    for region in regions:
        rx, ry, rw, rh = region["bbox_px"]
        overlap = max(0, min(x + w, rx + rw) - max(x, rx)) * max(0, min(y + h, ry + rh) - max(y, ry))
        if overlap > 0:
            matches.append((region, overlap / (w * h)))
    if not matches:
        return "uncovered", []
    if len(matches) > 1:
        return "ambiguous", [region["id"] for region, _ in matches]
    if matches[0][1] < 0.98:
        return "partial_overlap", [matches[0][0]["id"]]
    roles = {region["role"] for region, _ in matches}
    if roles & TEXT_ROLES:
        category = "outside_text_region"
    elif roles & IMAGE_ROLES:
        category = "covered_image"
    else:
        category = "ignored_decoration"
    return category, [region["id"] for region, _ in matches]


def svg_number(value: float) -> str:
    return f"{value:g}"


def effective_font_size(region: dict) -> float:
    requested = region.get("font_size", 16)
    if isinstance(requested, bool) or not isinstance(requested, (int, float)) or requested <= 0 or requested > 96:
        raise ValueError("font_size must be a positive number no greater than 96 units")
    if requested < MIN_FONT_LOGICAL:
        raise ValueError("font_size is below the accepted 7 CSS pixel equivalent")
    return float(requested)


def svg_text(region: dict, content: str) -> list[str]:
    tx, ty, tw, th = region["target_box"]
    font_size = effective_font_size(region)
    semantic = region["semantic_role"].lower()
    header = region["role"] == "table_cell" and "header" in semantic
    color = region.get("text_color", "#ffffff" if header else "#152b42")
    if not isinstance(color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        raise ValueError("text_color must be a six-digit hex color")
    weight = region.get(
        "font_weight",
        700 if region.get("bold") or any(label in semantic for label in ("title", "heading", "header")) else 400,
    )
    weight = (
        {"bold": 700, "normal": 400, "700": 700, "400": 400}.get(weight, weight)
        if not isinstance(weight, bool)
        else weight
    )
    if isinstance(weight, bool) or weight not in (400, 700):
        raise ValueError("font_weight must be 400 or 700")
    lines = content.splitlines() or [""]
    line_height = font_size * 1.28
    padding = min(8, tw * 0.04)
    left_padding = region.get("padding_left", padding)
    right_padding = region.get("padding_right", padding)
    if (
        any(
            isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0
            for value in (left_padding, right_padding)
        )
        or left_padding + right_padding >= tw
    ):
        raise ValueError("text padding must fit inside the text box")
    align = region.get("text_align", region.get("align", "left"))
    if align not in ("left", "center", "right"):
        raise ValueError("text alignment must be left, center, or right")
    vertical_align = region.get(
        "vertical_align",
        region.get("valign", "middle" if region["role"] == "table_cell" else "top"),
    )
    if vertical_align not in ("top", "middle"):
        raise ValueError("vertical alignment must be top or middle")
    left, right = tx + left_padding, tx + tw - right_padding
    anchor = {"left": "start", "center": "middle", "right": "end"}[align]
    text_x = {"left": left, "center": (left + right) / 2, "right": right}[align]
    first_baseline = ty + (
        (th - len(lines) * line_height) / 2 + font_size if vertical_align == "middle" else padding + font_size
    )
    result = []
    for index, line in enumerate(lines):
        baseline = first_baseline + index * line_height
        result.append(
            f'<text x="{svg_number(text_x)}" y="{svg_number(baseline)}" text-anchor="{anchor}" font-family="Malgun Gothic, Noto Sans CJK KR, sans-serif" font-size="{svg_number(font_size)}" font-weight="{weight}" fill="{color}">{escape(line)}</text>'  # noqa: E501
        )
    return result


def svg_cell_background(region: dict) -> str:
    tx, ty, tw, th = region["target_box"]
    header = "header" in region["semantic_role"].lower()
    fill = region.get("fill_color", region.get("fill", "#142448" if header else "none"))
    if not isinstance(fill, str) or (fill != "none" and not re.fullmatch(r"#[0-9a-fA-F]{6}", fill)):
        raise ValueError("fill_color must be 'none' or a six-digit hex color")
    return f'<rect x="{svg_number(tx)}" y="{svg_number(ty)}" width="{svg_number(tw)}" height="{svg_number(th)}" fill="{fill}" stroke="#b7c5d2" stroke-width="1"/>'  # noqa: E501


def text_box_fit_estimate(region: dict, content: str) -> dict:
    tx, ty, width, height = region["target_box"]
    font_size = effective_font_size(region)
    padding = region.get("padding_left", min(8, width * 0.04))
    right_padding = region.get("padding_right", min(8, width * 0.04))
    top_padding = min(8, width * 0.04)
    lines = content.splitlines() or [""]
    vertical_align = region.get(
        "vertical_align",
        region.get("valign", "middle" if region["role"] == "table_cell" else "top"),
    )
    maximum = max(sum(1 if unicodedata.east_asian_width(char) in "WF" else 0.55 for char in line) for line in lines)
    return {
        "horizontal_overflow_estimate": maximum * font_size > width - padding - right_padding,
        "vertical_overflow_estimate": (
            len(lines) * font_size * 1.28 > height
            if vertical_align == "middle"
            else (len(lines) - 1) * font_size * 1.28 + font_size + top_padding > height
        ),
        "vertical_align": vertical_align,
        "line_count": len(lines),
        "text_content_box": [
            tx + padding,
            ty + top_padding,
            width - padding - right_padding,
            height - top_padding,
        ],
        "requested_font_size": region.get("font_size", 16),
        "rendered_font_size": font_size,
        "font_floor_applied": False,
        "wrap_policy": "explicit_annotation_lines_only",
    }
