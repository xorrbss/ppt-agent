"""Embed the trusted Pretendard faces into a converted SVG pptx in place.

Neither python-pptx nor the vendored slide-master converter
(``vendor/slide_master/svg_to_pptx``) write OOXML embedded fonts. This module
adds exactly what PowerPoint itself would add (ECMA-376 §13.3,
``CT_Presentation.embeddedFontLst`` / ``CT_EmbeddedFont``) directly on the
already-converted zip archive, mirroring how authored embeds the same
Pretendard faces on the browser side
(``lib/authored-hybrid/pptx-font-embedding.ts``) but at the OOXML-part level
since the SVG pptx is assembled in Python, not in a browser.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from services.svg_trusted_fonts import FONT_FAMILY, read_trusted_pretendard_faces

NS_P = "http://schemas.openxmlformats.org/presentationml/2006/main"
NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_CT = "http://schemas.openxmlformats.org/package/2006/content-types"
NS_RELS = "http://schemas.openxmlformats.org/package/2006/relationships"
FONT_RELATIONSHIP_TYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/font"
)
FONT_CONTENT_TYPE = "application/x-fontdata"
_WEIGHT_SLOT = {400: "regular", 700: "bold"}

ET.register_namespace("p", NS_P)
ET.register_namespace("r", NS_R)


def _next_relationship_id(rels_root: ET.Element) -> int:
    existing = []
    for element in rels_root.findall(f"{{{NS_RELS}}}Relationship"):
        raw = (element.get("Id") or "").removeprefix("rId")
        if raw.isdigit():
            existing.append(int(raw))
    return (max(existing) + 1) if existing else 1


def _ensure_fntdata_content_type(content_types_root: ET.Element) -> None:
    for element in content_types_root.findall(f"{{{NS_CT}}}Default"):
        if element.get("Extension") == "fntdata":
            return
    default = ET.SubElement(content_types_root, f"{{{NS_CT}}}Default")
    default.set("Extension", "fntdata")
    default.set("ContentType", FONT_CONTENT_TYPE)


def _insert_embedded_font_list(presentation_root: ET.Element, embedded_font_lst: ET.Element) -> None:
    """Insert ``embeddedFontLst`` at its required schema position.

    ``CT_Presentation``'s sequence places it directly after ``notesSz`` (a
    required element) and before ``custShowLst``/``photoAlbum``/etc.
    """

    children = list(presentation_root)
    notes_sz_index = next(
        (index for index, child in enumerate(children) if child.tag == f"{{{NS_P}}}notesSz"),
        None,
    )
    if notes_sz_index is None:
        raise ValueError("svg_pptx_presentation_notes_size_missing")
    presentation_root.insert(notes_sz_index + 1, embedded_font_lst)
    presentation_root.set("embedTrueTypeFonts", "1")


def embed_trusted_fonts(target: Path) -> tuple[str, ...]:
    """Embed the bundled Pretendard faces into the pptx at ``target``, in place.

    Returns each face's ``"sha256:..."`` digest, in ``BUNDLED_FACES`` order,
    for the export evidence/receipt to bind to.
    """

    faces = read_trusted_pretendard_faces()
    with zipfile.ZipFile(target) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}

    presentation_root = ET.fromstring(entries["ppt/presentation.xml"])
    rels_path = "ppt/_rels/presentation.xml.rels"
    rels_root = ET.fromstring(entries[rels_path])
    content_types_root = ET.fromstring(entries["[Content_Types].xml"])

    embedded_font = ET.Element(f"{{{NS_P}}}embeddedFont")
    font_element = ET.SubElement(embedded_font, f"{{{NS_P}}}font")
    font_element.set("typeface", FONT_FAMILY)

    digests: list[str] = []
    next_id = _next_relationship_id(rels_root)
    for weight, digest, payload in faces:
        slot = _WEIGHT_SLOT[weight]
        part_name = f"font{next_id}.fntdata"
        relationship_id = f"rId{next_id}"
        next_id += 1
        entries[f"ppt/fonts/{part_name}"] = payload
        relationship = ET.SubElement(rels_root, f"{{{NS_RELS}}}Relationship")
        relationship.set("Id", relationship_id)
        relationship.set("Type", FONT_RELATIONSHIP_TYPE)
        relationship.set("Target", f"fonts/{part_name}")
        slot_element = ET.SubElement(embedded_font, f"{{{NS_P}}}{slot}")
        slot_element.set(f"{{{NS_R}}}id", relationship_id)
        digests.append(digest)

    embedded_font_lst = ET.Element(f"{{{NS_P}}}embeddedFontLst")
    embedded_font_lst.append(embedded_font)
    _insert_embedded_font_list(presentation_root, embedded_font_lst)
    _ensure_fntdata_content_type(content_types_root)

    entries["ppt/presentation.xml"] = ET.tostring(presentation_root, encoding="utf-8", xml_declaration=True)
    entries[rels_path] = ET.tostring(rels_root, encoding="utf-8", xml_declaration=True)
    entries["[Content_Types].xml"] = ET.tostring(content_types_root, encoding="utf-8", xml_declaration=True)

    target.unlink()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    return tuple(digests)


__all__ = ["embed_trusted_fonts", "FONT_CONTENT_TYPE", "FONT_RELATIONSHIP_TYPE"]
