"""Bounded text extraction and explicit corporate-normalize slide mapping."""

from __future__ import annotations

from io import BytesIO
import re
from typing import Any
from xml.etree import ElementTree
import zipfile


_SLIDE_PART = re.compile(r"^ppt/slides/slide([1-9][0-9]*)\.xml$")
_MAX_SLIDES = 500
_MAX_SLIDE_XML_BYTES = 2 * 1024 * 1024
_MAX_TEXT_NODES_PER_SLIDE = 4096
_MAX_TOTAL_TEXT_CHARS = 2_000_000


def _parse_slide_xml(content: bytes) -> ElementTree.Element:
    lowered = content[:4096].lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise ValueError("authored_vnext_ooxml_slide_xml_active_content")
    try:
        return ElementTree.fromstring(content)
    except ElementTree.ParseError as error:
        raise ValueError("authored_vnext_ooxml_slide_xml_invalid") from error


def extract_imported_slides(content: bytes) -> list[dict[str, str]]:
    """Extract inert text from an already security-inspected bounded PPTX."""

    with zipfile.ZipFile(BytesIO(content), "r") as archive:
        members = sorted(
            (
                (int(match.group(1)), name)
                for name in archive.namelist()
                if (match := _SLIDE_PART.fullmatch(name)) is not None
            ),
            key=lambda item: item[0],
        )
        if len(members) > _MAX_SLIDES:
            raise ValueError("authored_vnext_ooxml_slide_count_exceeded")
        result: list[dict[str, str]] = []
        total_text_chars = 0
        for index, name in members:
            member = archive.getinfo(name)
            if member.file_size > _MAX_SLIDE_XML_BYTES:
                raise ValueError("authored_vnext_ooxml_slide_xml_size_exceeded")
            root = _parse_slide_xml(archive.read(member))
            texts = [
                " ".join((element.text or "").split())
                for element in root.iter()
                if element.tag.endswith("}t") and (element.text or "").strip()
            ]
            if len(texts) > _MAX_TEXT_NODES_PER_SLIDE:
                raise ValueError("authored_vnext_ooxml_slide_text_count_exceeded")
            total_text_chars += sum(len(text) for text in texts)
            if total_text_chars > _MAX_TOTAL_TEXT_CHARS:
                raise ValueError("authored_vnext_ooxml_slide_text_size_exceeded")
            source_layout_id = next(
                (
                    element.attrib["layoutId"]
                    for element in root.iter()
                    if "layoutId" in element.attrib
                ),
                "layout:standard",
            )
            result.append(
                {
                    "slideId": f"slide:imported-{index:03d}",
                    "sourceLayoutId": source_layout_id,
                    "title": texts[0] if texts else f"Imported slide {index}",
                    "body": "\n".join(texts[1:]) if len(texts) > 1 else " ",
                }
            )
        return result


def map_imported_slides(
    extracted: list[dict[str, str]],
    master_contract: dict[str, Any],
) -> list[dict[str, Any]]:
    if not extracted:
        raise ValueError("authored_vnext_ooxml_normalize_source_slides_required")
    layouts = {layout["kind"]: layout for layout in master_contract["layouts"]}
    required = {"cover", "section", "standard", "long-title"}
    if not required.issubset(layouts):
        raise ValueError("authored_vnext_ooxml_normalize_layouts_incomplete")
    result = []
    for index, source in enumerate(extracted):
        source_layout = source["sourceLayoutId"].casefold()
        kind = (
            "cover"
            if index == 0 or "cover" in source_layout
            else "section"
            if "section" in source_layout
            else "long-title"
            if "long" in source_layout
            else "standard"
        )
        layout = layouts[kind]
        values: dict[str, str] = {}
        for placeholder in layout["placeholders"]:
            role = placeholder["id"].removeprefix("placeholder:")
            values[role] = (
                source["title"]
                if placeholder["type"] == "title"
                else source["body"]
            )
        result.append(
            {
                "slideId": source["slideId"],
                "layoutId": layout["id"],
                "values": values,
            }
        )
    return result


__all__ = ["extract_imported_slides", "map_imported_slides"]
