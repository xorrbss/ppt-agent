"""Media extraction and source geometry for the local raster pilot."""

from __future__ import annotations

import hashlib
import io
import json
import posixpath
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from PIL import Image


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


def embedded_page_image(
    page: dict, media: dict[str, bytes], full_slide_images: dict[int, dict]
) -> tuple[str, bytes, Image.Image, dict]:
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
