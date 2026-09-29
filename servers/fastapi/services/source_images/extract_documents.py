"""Bounded document raster collection, explicitly opted into by SVG callers.

DOCX raster images retain part/block locations. Complex DOCX objects and PDF
vectors use page appearances through the existing conversion/rendering services.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
import zipfile

from defusedxml import ElementTree as ET
from PIL import Image

from services.source_images.extract import (
    MAX_EXPANDED_BYTES,
    MAX_PPTX_BYTES,
    MAX_ZIP_MEMBERS,
    NS,
    _relationships,
)
from utils.hashing import sha256_hex

MAX_PIXELS = 40_000_000
MAX_PDF_PAGES = 200
MAX_OCCURRENCES = 2000


@dataclass(frozen=True)
class DocumentImage:
    payload: bytes
    mime_type: str
    width: int
    height: int
    digest: str
    occurrence_id: str
    locator: dict
    source_text: str | None = None


@dataclass
class DocumentImageCollection:
    assets: dict[str, DocumentImage] = field(default_factory=dict)
    occurrences: list[DocumentImage] = field(default_factory=list)
    diagnostics: list[dict] = field(default_factory=list)
    coverage: str = "complete"

    @property
    def status(self) -> str:
        if self.diagnostics:
            return "partial" if self.occurrences else "failed"
        return "complete" if self.occurrences else "no_images"

    def add(self, payload: bytes, locator: dict, text: str | None = None) -> None:
        if len(self.occurrences) >= MAX_OCCURRENCES:
            raise ValueError("image_occurrence_budget_exceeded")
        with Image.open(BytesIO(payload)) as image:
            if image.width * image.height > MAX_PIXELS:
                raise ValueError("image_pixel_budget_exceeded")
            image.load()
            mime = Image.MIME.get(image.format, "")
            if mime not in {"image/png", "image/jpeg", "image/webp"}:
                raise ValueError("unsupported_raster_format")
            width, height = image.size
        digest = sha256_hex(payload)
        identity = f"{locator['sourceDocumentSha256']}:{locator['objectLocator']}"
        item = DocumentImage(
            payload, mime, width, height, digest, sha256_hex(identity.encode()), locator, text
        )
        self.assets.setdefault(digest, item)
        self.occurrences.append(item)


def extract_document_images(path: Path) -> DocumentImageCollection:
    result = DocumentImageCollection()
    try:
        if path.stat().st_size > MAX_PPTX_BYTES:
            raise ValueError("document_byte_budget_exceeded")
        digest = sha256_hex(path.read_bytes())
        if path.suffix.lower() == ".docx":
            _docx(path, digest, result)
        elif path.suffix.lower() == ".pdf":
            _pdf(path, digest, result)
        else:
            raise ValueError("unsupported_document_format")
    except Exception as error:
        result.diagnostics.append({"code": str(error) if isinstance(error, ValueError) else "document_read_failed"})
    if result.diagnostics:
        result.coverage = "partial"
    return result


def _docx(path: Path, digest: str, result: DocumentImageCollection) -> None:
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if len(infos) > MAX_ZIP_MEMBERS or sum(i.file_size for i in infos) > MAX_EXPANDED_BYTES:
            raise ValueError("document_expansion_budget_exceeded")
        if any(".." in Path(i.filename).parts or i.filename.startswith(("/", "\\")) for i in infos):
            raise ValueError("document_package_path_invalid")
        package = {i.filename: archive.read(i) for i in infos if not i.is_dir()}
    if "word/document.xml" not in package:
        raise ValueError("docx_main_part_missing")
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    mc = "http://schemas.openxmlformats.org/markup-compatibility/2006"
    parts = ["word/document.xml"] + sorted(
        p
        for p in package
        if p.startswith(("word/header", "word/footer", "word/footnotes", "word/endnotes")) and p.endswith(".xml")
    )
    complex_parts = []
    for part in parts:
        root = ET.fromstring(package[part])
        # Select one AlternateContent branch; fallback copies must not double count.
        for alternate in root.iter(f"{{{mc}}}AlternateContent"):
            branches = list(alternate)
            selected = next((b for b in branches if b.find(f".//{{{NS['a']}}}blip") is not None), None)
            for branch in branches:
                if branch is not selected:
                    alternate.remove(branch)
        relations = _relationships(package, part)
        for block_index, block in enumerate(root.iter(f"{{{w}}}p")):
            text = " ".join(t.text or "" for t in block.iter(f"{{{w}}}t"))[:600] or None
            for index, blip in enumerate(block.iter(f"{{{NS['a']}}}blip")):
                locator = {
                    "sourceDocumentSha256": digest,
                    "sourceFormat": "docx",
                    "partName": part,
                    "blockPath": f"paragraph/{block_index}",
                    "pageIndex": None,
                    "coordinateSystem": "EMU",
                    "objectLocator": f"{part}/paragraph/{block_index}/image/{index}",
                    "extractionKind": "embedded_raster",
                }
                relation = relations.get(blip.get(f"{{{NS['r']}}}embed", ""))
                if relation is None or relation[1] not in package:
                    result.diagnostics.append(
                        {"code": "external_or_missing_image_relationship", "sourceLocator": locator}
                    )
                    continue
                locator["mediaPart"] = relation[1]
                # Keep crop/transform metadata even though appearance normalization is downstream.
                parent = next((node for node in block.iter() if blip in list(node)), None)
                if parent is not None:
                    crop = parent.find(f"{{{NS['a']}}}srcRect")
                    locator["crop"] = dict(crop.attrib) if crop is not None else None
                locator["transforms"] = [dict(node.attrib) for node in block.iter(f"{{{NS['a']}}}xfrm")]
                locator["extents"] = [dict(node.attrib) for node in block.iter() if node.tag.endswith("}extent")]
                try:
                    result.add(package[relation[1]], locator, text)
                except (ValueError, OSError):
                    result.diagnostics.append({"code": "unsupported_or_invalid_raster", "sourceLocator": locator})
        if any(node.tag.endswith(("}chart", "}relIds", "}OLEObject", "}imagedata")) for node in root.iter()):
            complex_parts.append(part)
    if complex_parts:
        # DOCX has no reliable page map for these objects. Reuse the document
        # converter and preserve complete page appearances instead of guessing
        # object geometry or silently omitting their content.
        import tempfile
        from services.document_conversion_service import DocumentConversionService

        try:
            with tempfile.TemporaryDirectory(prefix="svg-docx-appearance-") as temporary:
                converted = DocumentConversionService().convert_office_to_pdf(str(path), temporary)
                _pdf(Path(converted), digest, result, force_pages=True, source_format="docx")
        except Exception:
            result.diagnostics.append({"code": "docx_complex_object_render_failed"})


def _pdf(
    path: Path, digest: str, result: DocumentImageCollection, *, force_pages: bool = False, source_format: str = "pdf"
) -> None:
    import pdfplumber
    from pdfminer.pdfdocument import PDFPasswordIncorrect

    try:
        document = pdfplumber.open(path)
    except PDFPasswordIncorrect as error:
        raise ValueError("source_locked") from error
    with document:
        if len(document.pages) > MAX_PDF_PAGES:
            raise ValueError("pdf_page_budget_exceeded")
        for page_index, page in enumerate(document.pages):
            if page.width * page.height * 4 > MAX_PIXELS:
                raise ValueError("pdf_render_pixel_budget_exceeded")
            images = page.images
            text = (page.extract_text() or "")[:600] or None
            scanned = not text and any(
                (i["x1"] - i["x0"]) * (i["bottom"] - i["top"]) >= page.width * page.height * 0.8 for i in images
            )
            page_appearance = force_pages or scanned or bool(page.curves or page.lines or page.rects)
            regions = (
                [dict(x0=page.bbox[0], top=page.bbox[1], x1=page.bbox[2], bottom=page.bbox[3], name="page-appearance")]
                if page_appearance
                else images
            )
            if page_appearance:
                result.coverage = "page_only"
            for index, item in enumerate(regions):
                if item.get("imagemask"):
                    continue
                bbox = (
                    max(page.bbox[0], item["x0"]),
                    max(page.bbox[1], item["top"]),
                    min(page.bbox[2], item["x1"]),
                    min(page.bbox[3], item["bottom"]),
                )
                locator = {
                    "sourceDocumentSha256": digest,
                    "sourceFormat": source_format,
                    "pageIndex": page_index,
                    "objectLocator": f"page/{page_index}/image/{index}/{item.get('name', 'inline')}",
                    "bbox": list(bbox),
                    "coordinateSystem": "pdf_points_top_left",
                    "rotation": page.rotation,
                    "cropBox": list(page.cropbox),
                    "mediaBox": list(page.mediabox),
                    "extractionKind": "scanned_page"
                    if scanned
                    else "rendered_appearance"
                    if page_appearance
                    else "region_render",
                    "appearance": "source-appearance",
                    "originalDocumentRequired": True,
                }
                try:
                    buffer = BytesIO()
                    page.crop(bbox).to_image(resolution=144).original.save(buffer, format="PNG")
                    result.add(buffer.getvalue(), locator, text)
                except Exception:
                    result.diagnostics.append({"code": "pdf_image_render_failed", "sourceLocator": locator})
