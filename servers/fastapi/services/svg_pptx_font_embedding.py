"""Embed byte-verified trusted faces into a converted SVG pptx in place.

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


def trusted_export_font_faces(font_usage=None, *, font_manifest=None):
    """Return only bounded trusted faces, keyed by actual OOXML family/slot."""
    if font_manifest is not None:
        from services.svg_templates.frame_fonts import resolve_font_manifest
        entries = resolve_font_manifest(font_manifest)
        if not entries:
            raise ValueError("svg_template_font_manifest_invalid")
        lookup = {}
        for asset, payload in entries:
            if asset.collection_index != 0 or asset.weight not in _WEIGHT_SLOT:
                raise ValueError("svg_template_export_font_face_unsupported")
            for name in (asset.family, *asset.family_aliases):
                key = (name.casefold(), asset.weight)
                if key in lookup and lookup[key][0].digest != asset.digest:
                    raise ValueError("svg_template_font_face_conflict")
                lookup[key] = (asset, payload)
        result = []
        seen = set()
        requested = ([(family, weight) for family, weights in font_usage.items() for weight in weights]
                     if font_usage is not None else
                     [(name, asset.weight) for asset, _ in entries for name in (asset.family, *asset.family_aliases)])
        for family, weight in sorted(requested):
            key = (family.casefold(), weight)
            if key in seen:
                continue
            if key not in lookup:
                raise ValueError("svg_template_font_unavailable")
            asset, payload = lookup[key]
            seen.add(key)
            result.append((family, _WEIGHT_SLOT[weight], asset.digest, payload))
        return tuple(result)
    if font_usage is None:
        return tuple((FONT_FAMILY, _WEIGHT_SLOT[weight], digest, payload)
            for weight, digest, payload in read_trusted_pretendard_faces())
    from utils.svg_template_fonts import FONT_FAMILY as NOTO, font_material, fallback_font_material

    result = []
    for family, weights in sorted(font_usage.items()):
        if not weights or not set(weights) <= {400, 700}:
            raise ValueError("svg_source_export_font_weight_unproven")
        if family == FONT_FAMILY:
            result.extend((family, _WEIGHT_SLOT[weight], digest, payload)
                for weight, digest, payload in read_trusted_pretendard_faces() if weight in weights)
        elif family == NOTO:
            for weight in sorted(weights):
                digest, payload = font_material(family, weight)
                result.append((family, _WEIGHT_SLOT[weight], digest, payload))
        else:
            fallback_family, weight, digest, payload = fallback_font_material()
            if family != fallback_family or weight != 400:
                raise ValueError("svg_source_export_font_family_unproven")
            # The real regular face is embedded; bold remains the original
            # OOXML requested weight and is synthesized as in Chromium.
            result.append((family, "regular", digest, payload))
    return tuple(result)


def embed_trusted_fonts(target: Path, *, font_usage=None, font_manifest=None) -> tuple[str, ...]:
    """Embed the bundled Pretendard faces into the pptx at ``target``, in place.

    The default preserves the historical Pretendard contract. Whole-page
    callers supply actual OOXML font usage for Noto and the trusted fallback.
    Returns each embedded face's digest for the export receipt to bind to.
    """

    faces = trusted_export_font_faces(font_usage, font_manifest=font_manifest)
    with zipfile.ZipFile(target) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}

    presentation_root = ET.fromstring(entries["ppt/presentation.xml"])
    rels_path = "ppt/_rels/presentation.xml.rels"
    rels_root = ET.fromstring(entries[rels_path])
    content_types_root = ET.fromstring(entries["[Content_Types].xml"])

    embedded_font_lst = ET.Element(f"{{{NS_P}}}embeddedFontLst")
    family_nodes = {}

    digests: list[str] = []
    next_id = _next_relationship_id(rels_root)
    for family, slot, digest, payload in faces:
        if family not in family_nodes:
            embedded_font = ET.SubElement(embedded_font_lst, f"{{{NS_P}}}embeddedFont")
            ET.SubElement(embedded_font, f"{{{NS_P}}}font", typeface=family)
            family_nodes[family] = embedded_font
        part_name = f"font{next_id}.fntdata"
        relationship_id = f"rId{next_id}"
        next_id += 1
        entries[f"ppt/fonts/{part_name}"] = payload
        relationship = ET.SubElement(rels_root, f"{{{NS_RELS}}}Relationship")
        relationship.set("Id", relationship_id)
        relationship.set("Type", FONT_RELATIONSHIP_TYPE)
        relationship.set("Target", f"fonts/{part_name}")
        slot_element = ET.SubElement(family_nodes[family], f"{{{NS_P}}}{slot}")
        slot_element.set(f"{{{NS_R}}}id", relationship_id)
        digests.append(digest)

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
