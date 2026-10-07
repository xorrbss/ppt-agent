"""Draft analysis of an image-only PPTX without changing the native source route.

The embedded pixels are the source of truth. All OCR and geometric roles remain
review candidates; this module cannot issue an approval pin.
"""

from __future__ import annotations

import io
import posixpath
import re
import subprocess
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from utils.hashing import sha256_hex
from services.source_content.raster_contracts import RasterAnalysis, seal_analysis
from services.source_content.raster_regions import (
    RegionCandidate,
    full_page_ocr,
    ocr_density_rectangles,
    screenshot_rectangles,
    text_and_table_candidates,
)
from services.source_content.security import admit_pptx_package, secure_xml


P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
DEFAULT_TESSERACT = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
DEFAULT_TESSDATA = Path(__file__).resolve().parents[4] / "tessdata"
MAX_RASTER_PIXELS = 60_000_000


@dataclass(frozen=True)
class EmbeddedRasterPage:
    page_number: int
    media_part: str
    media_bytes: bytes
    image: Image.Image
    occurrence_id: str


@dataclass(frozen=True)
class RasterDraft:
    analysis: RasterAnalysis
    # Region crops are PNG-encoded from the embedded source image pixels.
    crop_png_by_region: dict[str, bytes]


def _sha256(value: bytes) -> str:
    return sha256_hex(value)


def _pixel_sha256(image: Image.Image) -> str:
    normalized = image.convert("RGBA")
    return _sha256(normalized.tobytes())


def _resolve(base: str, target: str) -> str:
    if target.startswith("/"):
        result = target.lstrip("/")
    else:
        result = posixpath.normpath(posixpath.join(base, target))
    if result.startswith("../") or result == ".." or not result.startswith("ppt/"):
        raise ValueError("raster_relationship_target_invalid")
    return result


def _relationship_map(archive: zipfile.ZipFile, part: str, base: str) -> dict[str, str]:
    root = secure_xml(archive.read(part), part_name=part)
    return {
        relation.get("Id", ""): _resolve(base, relation.get("Target", ""))
        for relation in root.findall(f"{PKG_REL}Relationship")
    }


def extract_embedded_raster_pages(path: Path) -> tuple[str, int, int, tuple[EmbeddedRasterPage, ...]]:
    """Admit only ordered slides with one untransformed, full-page picture."""

    report = admit_pptx_package(path)
    source_bytes = path.read_bytes()
    names = set(report.entries)
    with zipfile.ZipFile(io.BytesIO(source_bytes)) as archive:
        presentation = secure_xml(archive.read("ppt/presentation.xml"), part_name="ppt/presentation.xml")
        size = presentation.find(f"{P}sldSz")
        if size is None:
            raise ValueError("raster_slide_size_missing")
        width_emu, height_emu = int(size.get("cx", "0")), int(size.get("cy", "0"))
        if width_emu <= 0 or height_emu <= 0:
            raise ValueError("raster_slide_size_invalid")
        rels = _relationship_map(archive, "ppt/_rels/presentation.xml.rels", "ppt")
        slide_list = presentation.find(f"{P}sldIdLst")
        if slide_list is None:
            raise ValueError("raster_slide_order_missing")
        slide_names = [rels.get(node.get(f"{R}id", ""), "") for node in slide_list]
        if len(slide_names) != report.slide_count or any(name not in names for name in slide_names):
            raise ValueError("raster_slide_order_invalid")
        pages: list[EmbeddedRasterPage] = []
        for page_number, slide_name in enumerate(slide_names, 1):
            slide = secure_xml(archive.read(slide_name), part_name=slide_name)
            tree = slide.find(f".//{P}spTree")
            if tree is None:
                raise ValueError("raster_slide_shape_tree_missing")
            visible = [node for node in tree if node.tag not in {f"{P}nvGrpSpPr", f"{P}grpSpPr"}]
            if len(visible) != 1 or visible[0].tag != f"{P}pic":
                raise ValueError("raster_slide_not_single_picture")
            picture = visible[0]
            blip = picture.find(f"./{P}blipFill/{A}blip")
            transform = picture.find(f"./{P}spPr/{A}xfrm")
            if blip is None or transform is None or transform.attrib:
                raise ValueError("raster_picture_transform_unsupported")
            if picture.find(f"./{P}blipFill/{A}srcRect") is not None:
                raise ValueError("raster_picture_crop_unsupported")
            if any(picture.find(f"./{P}spPr/{A}{effect}") is not None for effect in ("effectLst", "effectDag")):
                raise ValueError("raster_picture_effect_unsupported")
            offset, extent = transform.find(f"{A}off"), transform.find(f"{A}ext")
            if offset is None or extent is None:
                raise ValueError("raster_picture_geometry_missing")
            x, y = int(offset.get("x", "0")), int(offset.get("y", "0"))
            w, h = int(extent.get("cx", "0")), int(extent.get("cy", "0"))
            if w <= 0 or h <= 0:
                raise ValueError("raster_picture_geometry_invalid")
            embed_id = blip.get(f"{R}embed")
            match = re.fullmatch(r"(.*/)?([^/]+\.xml)", slide_name)
            if not embed_id or match is None:
                raise ValueError("raster_picture_relationship_missing")
            rel_part = f"ppt/slides/_rels/{match.group(2)}.rels"
            if rel_part not in names:
                raise ValueError("raster_picture_relationship_missing")
            media_part = _relationship_map(archive, rel_part, "ppt/slides").get(embed_id)
            if not media_part or not media_part.startswith("ppt/media/") or media_part not in names:
                raise ValueError("raster_embedded_image_missing")
            media = archive.read(media_part)
            try:
                source_image = Image.open(io.BytesIO(media))
                if source_image.width * source_image.height > MAX_RASTER_PIXELS:
                    raise ValueError("raster_image_pixel_limit")
                image = source_image.convert("RGBA")
                if image.getchannel("A").getextrema()[0] < 255:
                    raise ValueError("raster_transparent_page_image_unsupported")
            except (OSError, Image.DecompressionBombError) as error:
                raise ValueError("raster_embedded_image_invalid") from error
            # A one-source-pixel tolerance handles integer EMU rounding only.
            left = x * image.width / width_emu
            right = (width_emu - x - w) * image.width / width_emu
            top = y * image.height / height_emu
            bottom = (height_emu - y - h) * image.height / height_emu
            if max(abs(v) for v in (left, right, top, bottom)) > 1:
                raise ValueError("raster_picture_not_full_page")
            # The same one-pixel rounding tolerance, measured in either dimension.
            if (
                abs(image.width - image.height * w / h) > 1 + 1e-6
                and abs(image.height - image.width * h / w) > 1 + 1e-6
            ):
                raise ValueError("raster_picture_aspect_changed")
            pages.append(EmbeddedRasterPage(page_number, media_part, media, image, f"slide-{page_number}-image-1"))
    return _sha256(source_bytes), width_emu, height_emu, tuple(pages)


def _emu_box(box: tuple[int, int, int, int], image: Image.Image, slide_size: tuple[int, int]) -> dict[str, float]:
    x, y, width, height = box
    slide_width, slide_height = slide_size
    return {
        "x": x * slide_width / image.width,
        "y": y * slide_height / image.height,
        "width": width * slide_width / image.width,
        "height": height * slide_height / image.height,
    }


def _overlap(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return max(ax, bx) < min(ax + aw, bx + bw) and max(ay, by) < min(ay + ah, by + bh)


def _intersection_area(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> int:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return max(0, min(ax + aw, bx + bw) - max(ax, bx)) * max(0, min(ay + ah, by + bh) - max(ay, by))


def _page_analysis(
    page: EmbeddedRasterPage,
    slide_size: tuple[int, int],
    *,
    tesseract_path: Path,
    tessdata_path: Path,
) -> tuple[dict, dict[str, bytes], tuple[str, ...]]:
    image = page.image
    media_digest = _sha256(page.media_bytes)
    issues: set[str] = {f"page_{page.page_number}:human_review_required"}
    try:
        if not tesseract_path.is_file() or not tessdata_path.is_dir():
            raise RuntimeError("local_ocr_unavailable")
        words = full_page_ocr(image, executable=tesseract_path, tessdata=tessdata_path)
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as error:
        words = ()
        reason = str(error) if isinstance(error, RuntimeError) else "local_ocr_unavailable"
        issues.add(f"page_{page.page_number}:{reason}")
    # OCR inventory is complete; implausible low-score strings never become
    # editable source text or a capture boundary without independent review.
    promoted_words = tuple(
        word for word in words
        if not (len(word.text) > 60 and (word.confidence is None or word.confidence < 0.40))
    )
    if len(promoted_words) != len(words):
        issues.add(f"page_{page.page_number}:low_evidence_ocr_not_promoted")
    framed = screenshot_rectangles(image)
    density = ocr_density_rectangles(image, promoted_words)
    screenshots = list(framed)
    for candidate in density:
        area = candidate.bbox[2] * candidate.bbox[3]
        if any(
            _overlap(candidate.bbox, known.bbox)
            and min(area, known.bbox[2] * known.bbox[3]) * 0.3
            < _intersection_area(candidate.bbox, known.bbox)
            for known in screenshots
        ):
            continue
        screenshots.append(candidate)
    if density:
        issues.add(f"page_{page.page_number}:ocr_density_capture_boundary_unverified")
    screenshots = tuple(screenshots)
    outside, region_issues = text_and_table_candidates(promoted_words, screenshots)
    issues.update(f"page_{page.page_number}:{item}" for item in region_issues)
    if not screenshots:
        issues.add(f"page_{page.page_number}:screenshot_boundary_unresolved")
    if not words:
        issues.add(f"page_{page.page_number}:ocr_inventory_empty")
    candidates = [*screenshots, *outside]
    if not candidates:
        candidates = [RegionCandidate("unknown", (0, 0, image.width, image.height), reason="unclassified_full_page")]
    else:
        issues.add(f"page_{page.page_number}:unclassified_pixels_unverified")
    crop_png: dict[str, bytes] = {}
    ids = [f"p{page.page_number}-r{index}" for index in range(1, len(candidates) + 1)]
    regions: list[dict] = []
    for index, candidate in enumerate(candidates):
        region_id = ids[index]
        x, y, width, height = candidate.bbox
        crop = image.crop((x, y, x + width, y + height))
        output = io.BytesIO()
        crop.save(output, format="PNG")
        crop_png[region_id] = output.getvalue()
        overlaps = tuple(
            ids[j] for j, other in enumerate(candidates) if j != index and _overlap(candidate.bbox, other.bbox)
        )
        if overlaps:
            issues.add(f"page_{page.page_number}:overlapping_region_candidates")
        regions.append({
            "region_id": region_id,
            "page_number": page.page_number,
            "role": candidate.role,
            "bbox": _emu_box(candidate.bbox, image, slide_size),
            "z_order": index,
            "text": candidate.text,
            "source_occurrence_id": page.occurrence_id,
            "source_media_part": page.media_part,
            "source_pixel_bbox": candidate.bbox,
            "crop_source_sha256": media_digest,
            "pixel_sha256": _pixel_sha256(crop),
            "overlaps": overlaps,
            "review_required": True,
        })
    inventory = [
        {
            "text": word.text,
            "bbox": _emu_box(word.bbox, image, slide_size),
            "confidence": word.confidence,
            "engine": "tesseract-kor+eng-psm11",
            "source_sha256": media_digest,
            "review_required": True,
        }
        for word in words
    ]
    result = {"page_number": page.page_number, "regions": regions, "ocr_inventory": inventory}
    return result, crop_png, tuple(sorted(issues))


def analyze_raster_pptx(
    source_path: Path,
    *,
    orientation: str = "portrait",
    target_canvas=None,
    tesseract_path: Path = DEFAULT_TESSERACT,
    tessdata_path: Path = DEFAULT_TESSDATA,
) -> RasterDraft:
    """Produce a sealed, review-required A4 draft and byte-faithful crop assets."""

    if orientation not in {"portrait", "landscape"}:
        raise ValueError("raster_target_orientation_invalid")
    source_sha, width_emu, height_emu, embedded = extract_embedded_raster_pages(source_path)
    pages: list[dict] = []
    crops: dict[str, bytes] = {}
    issues: list[str] = []
    for page in embedded:
        page_result, page_crops, page_issues = _page_analysis(
            page, (width_emu, height_emu),
            tesseract_path=tesseract_path, tessdata_path=tessdata_path,
        )
        pages.append(page_result)
        crops.update(page_crops)
        issues.extend(page_issues)
    from services.source_content.raster_target import raster_target_from_selectors

    target = target_canvas or raster_target_from_selectors(orientation=orientation)
    analysis = seal_analysis({
        "analysis_id": uuid.uuid5(uuid.NAMESPACE_URL, f"raster-analysis:{source_sha}:{orientation}:v1"),
        "source": {
            "source_sha256": source_sha,
            "page_count": len(embedded),
            "canvas_width_emu": width_emu,
            "canvas_height_emu": height_emu,
        },
        "target_canvas": target.model_dump(mode="json"),
        "pages": pages,
        "unresolved": tuple(sorted(set(issues))),
    })
    from services.source_content.raster_svg_layout import candidate_layout_issue

    recommended_pages = []
    for page in analysis.pages:
        reason = candidate_layout_issue(analysis, page.page_number)
        recommended_pages.append(page.model_copy(update={
            "recommended_render_mode": "full_page_preserve" if reason else "reconstruct",
            "recommendation_reason": reason,
        }))
    analysis = seal_analysis(analysis.model_copy(update={"pages": tuple(recommended_pages)}))
    return RasterDraft(analysis=analysis, crop_png_by_region=crops)


def analyze_auto_preserve_pptx(
    source_path: Path, *, orientation: str = "portrait", target_canvas=None,
) -> RasterDraft:
    """Admit image-only pages and retain each complete source image without OCR."""

    if orientation not in {"portrait", "landscape"}:
        raise ValueError("raster_target_orientation_invalid")
    source_sha, width_emu, height_emu, embedded = extract_embedded_raster_pages(source_path)
    crops: dict[str, bytes] = {}
    pages: list[dict] = []
    full_box = {"x": 0, "y": 0, "width": width_emu, "height": height_emu}
    for page in embedded:
        region_id = f"p{page.page_number}-full-page-preserve"
        media_digest = _sha256(page.media_bytes)
        pixel_digest = _pixel_sha256(page.image)
        pixel_box = (0, 0, page.image.width, page.image.height)
        lineage = {
            "page_number": page.page_number,
            "bbox": full_box,
            "z_order": 0,
            "source_occurrence_id": page.occurrence_id,
            "source_media_part": page.media_part,
            "source_pixel_bbox": pixel_box,
            "crop_source_sha256": media_digest,
            "pixel_sha256": pixel_digest,
        }
        output = io.BytesIO()
        page.image.save(output, format="PNG")
        crops[region_id] = output.getvalue()
        pages.append({
            "page_number": page.page_number,
            "regions": [{
                **lineage, "region_id": f"p{page.page_number}-source-audit",
                "role": "unknown", "review_required": True,
            }],
            "ocr_inventory": (),
            "render_mode": "full_page_preserve",
            "approval_method": "automatic_preserve",
            "full_page_region": {
                **lineage, "region_id": region_id,
                "role": "full_page_preserve", "review_required": False,
            },
            "recommended_render_mode": "full_page_preserve",
            "recommendation_reason": "strict_image_only_source_preservation",
        })
    from services.source_content.raster_target import raster_target_from_selectors

    target = target_canvas or raster_target_from_selectors(orientation=orientation)
    analysis = seal_analysis({
        "analysis_id": uuid.uuid5(uuid.NAMESPACE_URL, f"raster-auto-preserve:{source_sha}:{orientation}:v1"),
        "source": {
            "source_sha256": source_sha,
            "page_count": len(embedded),
            "canvas_width_emu": width_emu,
            "canvas_height_emu": height_emu,
        },
        "target_canvas": target.model_dump(mode="json"),
        "pages": pages,
    })
    return RasterDraft(analysis=analysis, crop_png_by_region=crops)


def analyze_redesign_pptx(
    source_path: Path,
    *,
    orientation: str = "landscape",
    target_canvas=None,
    tesseract_path: Path = DEFAULT_TESSERACT,
    tessdata_path: Path = DEFAULT_TESSDATA,
    structurer: object = "auto",
) -> RasterDraft:
    """Redesign every image-only page into editable text, tables and captures.

    ``structurer`` is a vision-JSON callable used to structure each page;
    "auto" builds one from the runtime LLM settings (None when no
    vision-capable provider is configured), None disables it.

    Pages carry render_mode "redesign" / approval_method "automatic_redesign";
    only low-confidence regions are marked review_required. The analysis
    carries ``stage_timings`` of this run (outside its digest).
    """

    from services.source_content.raster_contracts import RasterStageTimings
    from services.source_content.raster_llm_client import VisionJsonClient
    from services.source_content.raster_redesign import analyze_redesign
    from services.source_content.raster_stage_timing import activate

    if structurer == "auto":
        structurer = VisionJsonClient.from_runtime()
    with activate() as run:
        draft = analyze_redesign(
            source_path, orientation=orientation, tesseract_path=tesseract_path, tessdata_path=tessdata_path,
            structurer=structurer, target_canvas=target_canvas,
        )
    timings = RasterStageTimings.model_validate(run.payload())
    return RasterDraft(
        analysis=draft.analysis.model_copy(update={"stage_timings": timings}),
        crop_png_by_region=draft.crop_png_by_region,
    )
