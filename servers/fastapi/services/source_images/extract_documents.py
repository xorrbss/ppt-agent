"""Bounded document raster collection, explicitly opted into by SVG callers.

DOCX raster images retain part/block locations. Complex DOCX objects and PDF
vectors use page appearances through the existing conversion/rendering services.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
import re
import zipfile

from defusedxml import ElementTree as ET
from PIL import Image

from services.source_images.extract import (
    MAX_EXPANDED_BYTES,
    MAX_PPTX_BYTES,
    MAX_ZIP_MEMBERS,
    NS,
    _content_group,
    _nearest_text,
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
        blocks = list(root.iter(f"{{{w}}}p"))
        content_groups = _docx_content_groups(root, blocks, w, digest, part)
        for block_index, block in enumerate(blocks):
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
                    "contentGroup": content_groups[block_index],
                }
                relation = relations.get(blip.get(f"{{{NS['r']}}}embed", ""))
                if relation is None or relation[1] not in package:
                    result.diagnostics.append(
                        {"code": "external_or_missing_image_relationship", "sourceLocator": locator}
                    )
                    continue
                locator["mediaPart"] = relation[1]
                drawing = next((node for node in block.iter() if node.tag == f"{{{w}}}drawing" and blip in set(node.iter())), None)
                if drawing is not None:
                    properties = next((node for node in drawing.iter() if node.tag.endswith("}docPr")), None)
                    if properties is not None and (properties.get("descr") or properties.get("title")):
                        locator["contentGroup"] = {
                            **locator["contentGroup"],
                            "captionText": (properties.get("descr") or properties.get("title"))[:600],
                        }
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
            text_regions = _pdf_text_regions(page)
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
                if page_appearance:
                    body_text = _pdf_page_body_context(page, text_regions)
                    locator["contentGroup"] = _content_group(
                        digest, f"page/{page_index}/appearance", "page_appearance", body_text, order=page_index
                    )
                    locator["contentGroup"]["contextStatus"] = "local_body" if body_text else "local_context_unavailable"
                    if not body_text:
                        locator["contentGroup"]["contextDiagnostics"] = ["page_appearance_has_no_local_explanation"]
                else:
                    nearest = _nearest_text(bbox, text_regions)
                    locator["contentGroup"] = _content_group(
                        digest,
                        f"page/{page_index}/text/{nearest[0]}" if nearest else locator["objectLocator"],
                        "local_explanation" if nearest else "unresolved",
                        nearest[2] if nearest else None,
                        order=index,
                    )
                try:
                    buffer = BytesIO()
                    page.crop(bbox).to_image(resolution=144).original.save(buffer, format="PNG")
                    result.add(buffer.getvalue(), locator, text)
                except Exception:
                    result.diagnostics.append({"code": "pdf_image_render_failed", "sourceLocator": locator})


def _docx_content_groups(root, blocks: list, w: str, digest: str, part: str) -> dict[int, dict]:
    """Bind contiguous captures only to adjacent local source explanations."""

    parents = {child: parent for parent in root.iter() for child in parent}
    metadata = []
    sections = {}
    for index, block in enumerate(blocks):
        text = " ".join("".join(t.text or "" for t in block.iter(f"{{{w}}}t")).split())
        style_node = block.find(f"./{{{w}}}pPr/{{{w}}}pStyle")
        style = style_node.get(f"{{{w}}}val", "").lower() if style_node is not None else ""
        caption = style == "caption" or bool(re.match(r"^(?:figure|fig\.?|그림|화면|캡처)\s*\d", text, re.I))
        heading = style.startswith(("heading", "title")) or style in {"제목", "제목1", "제목2"}
        container = root
        node = block
        while node in parents:
            node = parents[node]
            if node.tag in {f"{{{w}}}tc", f"{{{w}}}footnote", f"{{{w}}}endnote"}:
                container = node
                break
        if heading:
            sections[container] = index
        metadata.append((text, caption, heading, container, sections.get(container),
                         next(block.iter(f"{{{NS['a']}}}blip"), None) is not None))
    result = {}
    def adjacent(index: int, direction: int, container, section):
        while 0 <= index < len(metadata):
            item = metadata[index]
            if item[3] is not container or item[4] != section or item[2] or item[5]:
                return None
            if item[0]:
                return (index, item)
            index += direction
        return None

    index = 0
    while index < len(metadata):
        text, is_caption, is_heading, container, section, has_image = metadata[index]
        if not has_image:
            index += 1
            continue
        if text and not is_caption and not is_heading:
            result[index] = _content_group(digest, f"{part}/paragraph/{index}", "local_explanation", text, order=index)
            index += 1
            continue
        start = index
        members = []
        run_captions = []
        while index < len(metadata):
            item = metadata[index]
            if item[3] is not container or item[4] != section or item[2] or (item[0] and not item[1]):
                break
            if item[5]:
                members.append(index)
            if item[1] and item[0]:
                run_captions.append((index, item))
            index += 1
        if not members:
            # An image in a heading has no trustworthy explanatory context.
            result[start] = _content_group(digest, f"{part}/paragraph/{start}", "unresolved", None, order=start)
            index = start + 1
            continue
        surrounding = [item for item in (adjacent(start - 1, -1, container, section),
                                         adjacent(index, 1, container, section)) if item is not None]
        explicit = [item for item in surrounding if item[1][1]]
        unique_run_captions = {item[1][0] for item in run_captions}
        if not explicit and len(unique_run_captions) == 1:
            explicit = run_captions[:1]
        explanations = explicit or [item for item in surrounding if not item[1][1]]
        different = {item[1][0] for item in explanations}
        ambiguous = len(different) > 1
        group_text = next(iter(different)) if len(different) == 1 else ""
        kind = "caption" if explicit and group_text else "local_explanation" if group_text else "unresolved"
        anchor = f"{part}/image-run/{start}"
        caption_owners = {}
        if len(unique_run_captions) > 1 and not ambiguous:
            prose_texts = {item[1][0] for item in surrounding if not item[1][1]}
            if len(prose_texts) == 1:
                # Distinct figure captions can belong to one explicitly
                # explained procedure; preserve that shared explanation.
                group_text = next(iter(prose_texts))
                kind = "local_explanation"
            elif len(prose_texts) > 1:
                ambiguous = True
                group_text, kind = "", "unresolved"
            else:
                # A consistent before/after caption placement establishes
                # ownership. Without that evidence, adjacency alone cannot
                # make unrelated figures a common explanatory unit.
                captions_by_index = {item[0]: item for item in run_captions + explicit}
                following = {member: captions_by_index.get(member) or captions_by_index.get(member + 1)
                             for member in members}
                preceding = {member: captions_by_index.get(member) or captions_by_index.get(member - 1)
                             for member in members}
                all_following, all_preceding = all(following.values()), all(preceding.values())
                if all_following and (not all_preceding or following == preceding):
                    caption_owners = following
                elif all_preceding and not all_following:
                    caption_owners = preceding
                else:
                    ambiguous = True
                    group_text, kind = "", "unresolved"
        for member in members:
            if member in caption_owners:
                caption_index, caption_item = caption_owners[member]
                result[member] = _content_group(
                    digest, f"{part}/caption/{caption_index}", "caption", caption_item[0], caption_item[0], member
                )
                continue
            captions = [item for item in run_captions if abs(item[0] - member) <= 1]
            captions.extend(item for item in explicit if abs(item[0] - member) <= 1)
            caption_texts = {item[1][0] for item in captions}
            caption_text = next(iter(caption_texts)) if len(caption_texts) == 1 else ""
            group = _content_group(digest, anchor, kind, group_text, caption_text, member)
            if ambiguous:
                group["referenceStatus"] = "ambiguous"
                group["contextStatus"] = "adjacent_explanations_ambiguous"
            elif not group_text and not caption_text:
                group["contextStatus"] = "local_context_unavailable"
            result[member] = group
    return result


def _pdf_text_regions(page) -> list[tuple]:
    """Local paragraphs from already available text geometry; no OCR fallback."""

    try:
        words = page.extract_words() or []
    except Exception:
        return []
    lines = []
    for word in sorted(words, key=lambda item: (item["top"], item["x0"])):
        if not word.get("text"):
            continue
        line = next((line for line in reversed(lines) if abs(line["top"] - word["top"]) <= 3
                     and -3 <= word["x0"] - line["x1"] <= 25), None)
        if line is None:
            lines.append({**word, "lineCount": 1})
        else:
            line["text"] += " " + word["text"]
            line["x0"] = min(line["x0"], word["x0"])
            line["x1"] = max(line["x1"], word["x1"])
            line["bottom"] = max(line["bottom"], word["bottom"])
    paragraphs = []
    for line in sorted(lines, key=lambda item: (item["top"], item["x0"])):
        previous = next((paragraph for paragraph in reversed(paragraphs)
                         if 0 <= line["top"] - paragraph["bottom"] <= max(6, line["bottom"] - line["top"])
                         and abs(line["x0"] - paragraph["x0"]) <= 12), None)
        if previous is None:
            paragraphs.append(dict(line))
        else:
            previous["text"] += " " + line["text"]
            previous["x1"] = max(previous["x1"], line["x1"])
            previous["bottom"] = line["bottom"]
            previous["lineCount"] += 1
    return [
        (index, None, paragraph["text"], (paragraph["x0"], paragraph["top"], paragraph["x1"], paragraph["bottom"]),
         {"lineCount": paragraph["lineCount"]})
        for index, paragraph in enumerate(paragraphs)
    ]


def _pdf_page_body_context(page, regions: list[tuple]) -> str | None:
    """A page appearance needs body evidence beyond a heading or page label."""

    page_top = page.bbox[1]
    page_bottom = page.bbox[3]
    height = page_bottom - page_top
    local = []
    for region in regions:
        text, bounds = region[2], region[3]
        line_count = region[4]["lineCount"]
        if bounds[1] < page_top + height * 0.12 or bounds[3] > page_bottom - height * 0.06:
            continue
        # Short isolated labels and one-line headings provide no local body
        # evidence. Keep the full source text separately as provenance.
        if not (line_count >= 2 and len(text) >= 40 or len(text) >= 120):
            continue
        local.append(text)
    return " ".join(local)[:600] or None
