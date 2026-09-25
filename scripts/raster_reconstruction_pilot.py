"""Local evidence-first raster-to-A4-SVG pilot; not a production ingestion route.

Annotations are private and use source-image pixel boxes. See --help for usage.
The pilot deliberately leaves agent transcription and OCR candidates under review.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import posixpath
import re
import subprocess
import sys
import unicodedata
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

from PIL import Image


SCHEMA = "raster-pilot/v1"
CANVASES = {"landscape": (1188, 840), "portrait": (840, 1188)}
IMAGE_ROLES = {"screenshot", "compound"}
TEXT_ROLES = {"text", "table_cell"}
ROLES = IMAGE_ROLES | TEXT_ROLES | {"ignored_decoration"}
DEFAULT_TESSERACT = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
DEFAULT_TESSDATA = Path(__file__).resolve().parents[1] / "tessdata"
MIN_FONT_LOGICAL = 7 * 9525 / 9000


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def pixel_digest(image: Image.Image) -> str:
    image = image.convert("RGBA")
    return digest(image.size[0].to_bytes(4, "big") + image.size[1].to_bytes(4, "big") + image.tobytes())


def json_file(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def box(value: object, name: str, limit: tuple[int, int], *, integer: bool) -> list[int | float]:
    if not isinstance(value, list) or len(value) != 4:
        raise ValueError(f"{name} must be [x,y,w,h]")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in value):
        raise ValueError(f"{name} must contain numbers")
    x, y, w, h = value
    if integer and any(not isinstance(v, int) for v in value):
        raise ValueError(f"{name} must use integer source pixels")
    if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > limit[0] or y + h > limit[1]:
        raise ValueError(f"{name} lies outside its canvas")
    return value


def media_inventory(pptx: Path) -> tuple[str, dict[str, bytes], dict[int, dict]]:
    pptx_bytes = pptx.read_bytes()
    media: dict[str, bytes] = {}
    full_slide_images: dict[int, dict] = {}
    with zipfile.ZipFile(io.BytesIO(pptx_bytes)) as archive:
        for name in archive.namelist():
            if name.startswith("ppt/media/") and not name.endswith("/"):
                media[name] = archive.read(name)
        slide_files = {name for name in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)}
        presentation = ET.fromstring(archive.read("ppt/presentation.xml"))
        slide_size = presentation.find("{http://schemas.openxmlformats.org/presentationml/2006/main}sldSz")
        if slide_size is None:
            raise ValueError("PPTX has no slide size")
        slide_w, slide_h = int(slide_size.attrib["cx"]), int(slide_size.attrib["cy"])
        ns_p = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
        ns_a = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
        ns_r = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
        presentation_rels = ET.fromstring(archive.read("ppt/_rels/presentation.xml.rels"))
        slide_targets = {}
        for relation in presentation_rels:
            target = relation.attrib.get("Target", "")
            resolved = posixpath.normpath(posixpath.join("ppt", target.lstrip("/")))
            if target.startswith("/"):
                resolved = target.lstrip("/")
            slide_targets[relation.attrib.get("Id")] = resolved
        slide_ids = presentation.find(f"{ns_p}sldIdLst")
        if slide_ids is None:
            raise ValueError("PPTX has no slide order")
        ordered_slides = [slide_targets.get(item.attrib.get(f"{ns_r}id")) for item in slide_ids]
        if not ordered_slides or any(name not in slide_files for name in ordered_slides):
            raise ValueError("PPTX slide order cannot be resolved")
        for page_index, slide_name in enumerate(ordered_slides, start=1):
            slide_file_number = int(re.search(r"slide(\d+)\.xml$", slide_name).group(1))
            slide = ET.fromstring(archive.read(slide_name))
            tree = slide.find(f".//{ns_p}spTree")
            if tree is None:
                continue
            visible = [child for child in tree if child.tag not in {f"{ns_p}nvGrpSpPr", f"{ns_p}grpSpPr"}]
            if len(visible) != 1 or visible[0].tag != f"{ns_p}pic":
                continue
            picture = visible[0]
            blip = picture.find(f"./{ns_p}blipFill/{ns_a}blip")
            src_rect = picture.find(f"./{ns_p}blipFill/{ns_a}srcRect")
            xfrm = picture.find(f"./{ns_p}spPr/{ns_a}xfrm")
            if blip is None or src_rect is not None or xfrm is None or xfrm.attrib:
                continue
            offset, extent = xfrm.find(f"{ns_a}off"), xfrm.find(f"{ns_a}ext")
            if offset is None or extent is None:
                continue
            off_x, off_y = int(offset.attrib["x"]), int(offset.attrib["y"])
            ext_w, ext_h = int(extent.attrib["cx"]), int(extent.attrib["cy"])
            rel_id = blip.attrib.get(f"{ns_r}embed")
            rel_name = f"ppt/slides/_rels/slide{slide_file_number}.xml.rels"
            if not rel_id or rel_name not in archive.namelist():
                continue
            relationships = ET.fromstring(archive.read(rel_name))
            for relation in relationships:
                if relation.attrib.get("Id") != rel_id or relation.attrib.get("TargetMode") == "External":
                    continue
                target = relation.attrib.get("Target", "")
                resolved = posixpath.normpath(posixpath.join("ppt/slides", target.lstrip("/")))
                if target.startswith("/"):
                    resolved = target.lstrip("/")
                if resolved in media:
                    try:
                        image_w, image_h = Image.open(io.BytesIO(media[resolved])).size
                    except (OSError, ValueError):
                        break
                    left_px = off_x * image_w / slide_w
                    right_px = (slide_w - off_x - ext_w) * image_w / slide_w
                    top_px = off_y * image_h / slide_h
                    bottom_px = (slide_h - off_y - ext_h) * image_h / slide_h
                    image_ratio = image_w / image_h
                    shape_ratio = ext_w / ext_h if ext_h > 0 else 0
                    if max(abs(value) for value in (left_px, right_px, top_px, bottom_px)) > 1:
                        break
                    if abs(image_ratio - shape_ratio) > 1 / image_h:
                        break
                    full_slide_images[page_index] = {
                        "entry": resolved,
                        "slide_size_emu": [slide_w, slide_h],
                        "source_shape_emu": [off_x, off_y, ext_w, ext_h],
                        "edge_offset_source_px": [left_px, top_px, right_px, bottom_px],
                        "geometry_policy": "single_uncropped_picture_full_slide_within_one_source_pixel",
                    }
                break
    if not slide_files:
        raise ValueError("PPTX has no slides")
    return digest(pptx_bytes), media, full_slide_images


def embedded_page_image(page: dict, media: dict[str, bytes], full_slide_images: dict[int, dict]) -> tuple[str, bytes, Image.Image, dict]:
    image_path = page.get("image_path")
    entry = page.get("image_entry")
    shape = full_slide_images.get(page.get("page"))
    if not shape:
        raise ValueError("Page is not one uncropped single-image slide within one source pixel of full bleed")
    expected_entry = shape["entry"]
    if entry is not None:
        if entry != expected_entry:
            raise ValueError("image_entry is not the selected slide's full-page image")
        blob = media[entry]
        image = Image.open(io.BytesIO(blob)).convert("RGBA")
        if image_path and pixel_digest(Image.open(image_path)) != pixel_digest(image):
            raise ValueError("image_path pixels differ from image_entry")
        return entry, blob, image, shape
    if not image_path:
        blob = media[expected_entry]
        return expected_entry, blob, Image.open(io.BytesIO(blob)).convert("RGBA"), shape
    external = Image.open(image_path).convert("RGBA")
    expected = pixel_digest(external)
    matches: list[tuple[str, bytes, Image.Image]] = []
    for name, blob in [(expected_entry, media[expected_entry])]:
        try:
            candidate = Image.open(io.BytesIO(blob))
            if candidate.size == external.size and pixel_digest(candidate) == expected:
                matches.append((name, blob, candidate.convert("RGBA")))
        except (OSError, ValueError):
            continue
    if not matches:
        raise ValueError("image_path pixels do not match any original PPTX media asset")
    if len(matches) > 1:
        raise ValueError("image_path matches multiple media assets; set image_entry")
    name, blob, image = matches[0]
    return name, blob, image, shape


def ocr_tokens(image: Image.Image, *, command: Path, tessdata: Path, timeout: float, psm: int) -> list[dict]:
    source = io.BytesIO()
    image.convert("RGB").save(source, format="PNG")
    argv = [str(command), "stdin", "stdout", "--tessdata-dir", str(tessdata), "-l", "kor+eng", "--psm", str(psm),
            "-c", "tessedit_create_tsv=1", "-c", "tessedit_create_txt=0"]
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


def svg_number(value: int | float) -> str:
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
    weight = region.get("font_weight", 700 if region.get("bold") or any(label in semantic for label in ("title", "heading", "header")) else 400)
    weight = {"bold": 700, "normal": 400, "700": 700, "400": 400}.get(weight, weight) if not isinstance(weight, bool) else weight
    if isinstance(weight, bool) or weight not in (400, 700):
        raise ValueError("font_weight must be 400 or 700")
    lines = content.splitlines() or [""]
    line_height = font_size * 1.28
    padding = min(8, tw * 0.04)
    left_padding = region.get("padding_left", padding)
    right_padding = region.get("padding_right", padding)
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0 for value in (left_padding, right_padding)) or left_padding + right_padding >= tw:
        raise ValueError("text padding must fit inside the text box")
    align = region.get("text_align", region.get("align", "left"))
    if align not in ("left", "center", "right"):
        raise ValueError("text alignment must be left, center, or right")
    vertical_align = region.get("vertical_align", region.get("valign", "middle" if region["role"] == "table_cell" else "top"))
    if vertical_align not in ("top", "middle"):
        raise ValueError("vertical alignment must be top or middle")
    left, right = tx + left_padding, tx + tw - right_padding
    anchor = {"left": "start", "center": "middle", "right": "end"}[align]
    text_x = {"left": left, "center": (left + right) / 2, "right": right}[align]
    first_baseline = ty + ((th - len(lines) * line_height) / 2 + font_size if vertical_align == "middle" else padding + font_size)
    result = []
    for index, line in enumerate(lines):
        baseline = first_baseline + index * line_height
        result.append(f'<text x="{svg_number(text_x)}" y="{svg_number(baseline)}" text-anchor="{anchor}" font-family="Malgun Gothic, Noto Sans CJK KR, sans-serif" font-size="{svg_number(font_size)}" font-weight="{weight}" fill="{color}">{escape(line)}</text>')
    return result


def svg_cell_background(region: dict) -> str:
    tx, ty, tw, th = region["target_box"]
    header = "header" in region["semantic_role"].lower()
    fill = region.get("fill_color", region.get("fill", "#142448" if header else "none"))
    if not isinstance(fill, str) or (fill != "none" and not re.fullmatch(r"#[0-9a-fA-F]{6}", fill)):
        raise ValueError("fill_color must be 'none' or a six-digit hex color")
    return f'<rect x="{svg_number(tx)}" y="{svg_number(ty)}" width="{svg_number(tw)}" height="{svg_number(th)}" fill="{fill}" stroke="#b7c5d2" stroke-width="1"/>'


def text_box_fit_estimate(region: dict, content: str) -> dict:
    tx, ty, width, height = region["target_box"]
    font_size = effective_font_size(region)
    padding = region.get("padding_left", min(8, width * 0.04))
    right_padding = region.get("padding_right", min(8, width * 0.04))
    top_padding = min(8, width * 0.04)
    lines = content.splitlines() or [""]
    vertical_align = region.get("vertical_align", region.get("valign", "middle" if region["role"] == "table_cell" else "top"))
    maximum = max(sum(1 if unicodedata.east_asian_width(char) in "WF" else 0.55 for char in line) for line in lines)
    return {
        "horizontal_overflow_estimate": maximum * font_size > width - padding - right_padding,
        "vertical_overflow_estimate": (len(lines) * font_size * 1.28 > height if vertical_align == "middle"
                                       else (len(lines) - 1) * font_size * 1.28 + font_size + top_padding > height),
        "vertical_align": vertical_align,
        "line_count": len(lines),
        "text_content_box": [tx + padding, ty + top_padding, width - padding - right_padding, height - top_padding],
        "requested_font_size": region.get("font_size", 16),
        "rendered_font_size": font_size,
        "font_floor_applied": False,
        "wrap_policy": "explicit_annotation_lines_only",
    }


def build_page(page: dict, source_image: Image.Image, source_entry: str, source_blob: bytes, source_sha: str, source_shape: dict,
               output: Path, *, orientation: str, command: Path, tessdata: Path,
               whole_timeout: float, crop_timeout: float) -> tuple[dict, list[dict]]:
    page_width, page_height = CANVASES[orientation]
    number = page.get("page")
    if not isinstance(number, int) or number <= 0:
        raise ValueError("page must be a positive slide number")
    regions = page.get("regions")
    if not isinstance(regions, list) or not regions:
        raise ValueError(f"page {number}: regions must be a nonempty list")
    seen: set[str] = set()
    validated: list[dict] = []
    for raw in regions:
        if not isinstance(raw, dict) or raw.get("role") not in ROLES:
            raise ValueError(f"page {number}: unsupported region role")
        name = raw.get("id")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name) or name in seen:
            raise ValueError(f"page {number}: region IDs must be unique and simple")
        seen.add(name)
        region = dict(raw)
        if not isinstance(region.get("semantic_role"), str) or not region["semantic_role"].strip():
            raise ValueError(f"page {number} {name}: semantic_role label is required")
        region["bbox_px"] = box(raw.get("bbox_px"), f"page {number} {name} bbox_px", source_image.size, integer=True)
        if region["role"] != "ignored_decoration":
            region["target_box"] = box(raw.get("target_box"), f"page {number} {name} target_box", (page_width, page_height), integer=False)
        if region["role"] in IMAGE_ROLES:
            area = region["bbox_px"][2] * region["bbox_px"][3]
            if area >= source_image.width * source_image.height * 0.90:
                raise ValueError(f"page {number} {name}: near-full-page raster is not a reconstructed region")
        validated.append(region)

    whole = ocr_tokens(source_image, command=command, tessdata=tessdata, timeout=whole_timeout, psm=11)
    inventory = []
    counts = {key: 0 for key in ("uncovered", "partial_overlap", "ambiguous", "outside_text_region", "covered_image", "ignored_decoration")}
    for token in whole:
        category, ids = classify_token(token, validated)
        counts[category] += 1
        inventory.append({**token, "category": category, "region_ids": ids})
    if not whole:
        raise RuntimeError(f"page {number}: whole-page OCR returned no tokens")

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{page_width}" height="{page_height}" viewBox="0 0 {page_width} {page_height}">',
             f'<rect x="0" y="0" width="{page_width}" height="{page_height}" fill="#ffffff"/>']
    receipts = []
    review = []
    background_parts = []
    image_parts = []
    text_parts = []
    for region in validated:
        rx, ry, rw, rh = region["bbox_px"]
        crop = source_image.crop((rx, ry, rx + rw, ry + rh))
        png = io.BytesIO()
        crop.save(png, format="PNG")
        crop_bytes = png.getvalue()
        receipt = {
            "id": region["id"], "role": region["role"], "semantic_role": region.get("semantic_role"),
            "source_bbox_px": region["bbox_px"], "target_box": region.get("target_box"),
            "source_image_entry": source_entry, "source_image_sha256": digest(source_blob),
            "source_page_pixel_sha256": source_sha, "crop_png_sha256": digest(crop_bytes),
            "crop_pixel_sha256": pixel_digest(crop),
        }
        if region["role"] in IMAGE_ROLES:
            tx, ty, tw, th = region["target_box"]
            slot_w, slot_h = max(1, int(round(tw))), max(1, int(round(th)))
            slot_x, slot_y = tx + (tw - slot_w) / 2, ty + (th - slot_h) / 2
            fit_scale = min(slot_w / rw, slot_h / rh)
            fit_w, fit_h = rw * fit_scale, rh * fit_scale
            fit_x, fit_y = slot_x + (slot_w - fit_w) / 2, slot_y + (slot_h - fit_h) / 2
            href = "data:image/png;base64," + base64.b64encode(crop_bytes).decode("ascii")
            image_parts.append(f'<image x="{svg_number(slot_x)}" y="{svg_number(slot_y)}" width="{slot_w}" height="{slot_h}" preserveAspectRatio="xMidYMid meet" href="{href}"/>')
            receipt["render_kind"] = "embedded_lossless_crop"
            receipt["svg_image_slot_box"] = [slot_x, slot_y, slot_w, slot_h]
            receipt["effective_render_box"] = [fit_x, fit_y, fit_w, fit_h]
            receipt["fit_policy"] = "aspect_preserving_contain_no_upstream_crop"
        elif region["role"] in TEXT_ROLES:
            if region["role"] == "table_cell":
                background_parts.append(svg_cell_background(region))
            crop_tokens = ocr_tokens(crop, command=command, tessdata=tessdata, timeout=crop_timeout, psm=6)
            candidate = " ".join(token["text"] for token in crop_tokens)
            authority = region.get("review_authority")
            reviewed = region.get("reviewed_text")
            reference = region.get("reference_text")
            if reviewed is not None and (not isinstance(reviewed, str) or authority != "user"):
                raise ValueError("reviewed_text requires review_authority=user")
            if reference is not None and (not isinstance(reference, str) or authority != "agent_visual"):
                raise ValueError("reference_text requires review_authority=agent_visual")
            rendered = reviewed if reviewed is not None else reference if reference is not None else candidate
            origin = "user_reviewed" if reviewed is not None else "agent_visual_reference" if reference is not None else "crop_ocr_candidate"
            needs_review = origin != "user_reviewed"
            text_parts.extend(svg_text(region, rendered))
            fit = text_box_fit_estimate(region, rendered)
            receipt.update({"render_kind": "editable_svg_text", "text_origin": origin, "needs_review": needs_review,
                            "crop_ocr_token_count": len(crop_tokens), "rendered_text_sha256": digest(rendered.encode("utf-8")),
                            **fit})
            review.append({"page": number, "region_id": region["id"], "role": region["role"],
                           "candidate_text": candidate, "reference_text": reference, "reviewed_text": reviewed,
                           "rendered_text": rendered, "text_origin": origin, "review_authority": authority,
                           "needs_review": needs_review, "crop_ocr_tokens": crop_tokens,
                           "source_bbox_px": region["bbox_px"], **fit})
        else:
            receipt["render_kind"] = "ignored_decoration_annotation"
            receipt["needs_review"] = True
        receipts.append(receipt)
    parts += background_parts + image_parts + text_parts + ["</svg>"]
    svg_path = output / f"page-{number:03d}.svg"
    svg_path.write_text("\n".join(parts), encoding="utf-8")
    page_receipt = {
        "version": SCHEMA, "page": number, "svg_file": svg_path.name,
        "svg_sha256": digest(svg_path.read_bytes()), "source_image_entry": source_entry,
        "source_image_sha256": digest(source_blob), "source_page_pixel_sha256": source_sha,
        "source_shape": source_shape,
        "source_size_px": list(source_image.size), "target_canvas": {"width": page_width, "height": page_height,
                                                                       "orientation": orientation, "unit": "9000_EMU"},
        "whole_page_ocr": {"engine": "tesseract_kor+eng_psm11", "independent_of_crop_ocr": True,
                           "same_engine_as_crop_ocr": True, "not_completeness_proof": True,
                           "tokens": inventory, "category_counts": counts},
        "regions": receipts, "review_required": not review or any(item["needs_review"] or item["vertical_overflow_estimate"] or item["horizontal_overflow_estimate"] for item in review)
                          or any(item.get("needs_review", False) for item in receipts)
                          or counts["uncovered"] > 0 or counts["partial_overlap"] > 0 or counts["ambiguous"] > 0 or counts["ignored_decoration"] > 0,
        "acceptance": "pilot_evidence_only",
    }
    json_file(output / f"page-{number:03d}.receipt.json", page_receipt)
    return page_receipt, review


def main() -> int:
    parser = argparse.ArgumentParser(description="Build local A4 SVG reconstruction evidence from annotated PPTX raster pages")
    parser.add_argument("--pptx", type=Path, required=True)
    parser.add_argument("--annotations", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--orientation", choices=CANVASES, default="landscape")
    parser.add_argument("--tesseract", type=Path, default=DEFAULT_TESSERACT)
    parser.add_argument("--tessdata", type=Path, default=DEFAULT_TESSDATA)
    parser.add_argument("--whole-ocr-timeout", type=float, default=90)
    parser.add_argument("--crop-ocr-timeout", type=float, default=25)
    args = parser.parse_args()
    if not args.tesseract.is_file() or not args.tessdata.is_dir():
        raise ValueError("Local Tesseract binary or tessdata is unavailable")
    annotations = json.loads(args.annotations.read_text(encoding="utf-8-sig"))
    if not isinstance(annotations, dict) or annotations.get("version") != SCHEMA:
        raise ValueError("Annotation version must be raster-pilot/v1")
    pages = annotations.get("pages")
    if not isinstance(pages, list) or not pages:
        raise ValueError("Annotation pages must be a nonempty list")
    source_sha, media, full_slide_images = media_inventory(args.pptx)
    if annotations.get("source_sha256") and annotations["source_sha256"] != source_sha:
        raise ValueError("PPTX digest does not match annotation source_sha256")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    receipts = []
    review = []
    seen_pages = set()
    for page in pages:
        if not isinstance(page, dict) or page.get("page") in seen_pages:
            raise ValueError("Page entries must be objects with unique page numbers")
        seen_pages.add(page["page"])
        entry, blob, image, shape = embedded_page_image(page, media, full_slide_images)
        receipt, items = build_page(page, image, entry, blob, pixel_digest(image), shape, args.output_dir,
                                    orientation=args.orientation, command=args.tesseract, tessdata=args.tessdata,
                                    whole_timeout=args.whole_ocr_timeout, crop_timeout=args.crop_ocr_timeout)
        receipts.append(receipt)
        review.extend(items)
    json_file(args.output_dir / "review.json", {"version": SCHEMA, "source_pptx_sha256": source_sha,
                                                  "items": review, "acceptance": "review_required"})
    json_file(args.output_dir / "manifest.json", {"version": SCHEMA, "source_pptx_sha256": source_sha,
                                                    "orientation": args.orientation,
                                                    "page_order": [item["page"] for item in receipts],
                                                    "page_receipts": [f"page-{item['page']:03d}.receipt.json" for item in receipts],
                                                    "acceptance": "pilot_evidence_only"})
    print(json.dumps({"pages": len(receipts), "svg": len(receipts), "review_items": len(review),
                      "uncovered_ocr_tokens": sum(item["whole_page_ocr"]["category_counts"]["uncovered"] for item in receipts),
                      "status": "pilot_evidence_only"}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, RuntimeError, subprocess.TimeoutExpired, zipfile.BadZipFile) as error:
        print(f"pilot failed: {type(error).__name__}: {error}", file=sys.stderr)
        raise SystemExit(1)
