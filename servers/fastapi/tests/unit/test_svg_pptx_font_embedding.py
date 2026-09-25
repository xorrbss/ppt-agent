"""OOXML embedded-font parts on a real pptx, exercised against a real python-pptx template.

Neither python-pptx nor the vendored slide-master converter write
``embeddedFontLst``; this exercises the raw zip/XML writer end to end instead
of trusting the ECMA-376 layout from memory.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from pptx import Presentation

from services.svg_pptx_font_embedding import (
    FONT_CONTENT_TYPE,
    FONT_RELATIONSHIP_TYPE,
    NS_CT,
    NS_P,
    NS_R,
    NS_RELS,
    embed_trusted_fonts,
)
from services.svg_trusted_fonts import FONT_FAMILY, read_trusted_pretendard_faces


def _blank_pptx(tmp_path: Path) -> Path:
    target = tmp_path / "result.pptx"
    Presentation().save(str(target))
    return target


def test_embed_trusted_fonts_adds_parts_relationships_and_content_type(tmp_path):
    target = _blank_pptx(tmp_path)
    expected = read_trusted_pretendard_faces()

    digests = embed_trusted_fonts(target)

    assert digests == tuple(digest for _weight, digest, _payload in expected)

    with zipfile.ZipFile(target) as archive:
        names = set(archive.namelist())
        font_parts = sorted(name for name in names if name.startswith("ppt/fonts/"))
        assert len(font_parts) == 2
        payload_by_digest = {
            "sha256:" + __import__("hashlib").sha256(archive.read(part)).hexdigest(): archive.read(part)
            for part in font_parts
        }
        for _weight, digest, payload in expected:
            assert payload_by_digest[digest] == payload

        presentation_root = ET.fromstring(archive.read("ppt/presentation.xml"))
        assert presentation_root.get("embedTrueTypeFonts") == "1"
        children = list(presentation_root)
        lst_index = next(i for i, c in enumerate(children) if c.tag == f"{{{NS_P}}}embeddedFontLst")
        notes_sz_index = next(i for i, c in enumerate(children) if c.tag == f"{{{NS_P}}}notesSz")
        assert lst_index == notes_sz_index + 1

        embedded_font_lst = children[lst_index]
        embedded_fonts = embedded_font_lst.findall(f"{{{NS_P}}}embeddedFont")
        assert len(embedded_fonts) == 1
        font_element = embedded_fonts[0].find(f"{{{NS_P}}}font")
        assert font_element.get("typeface") == FONT_FAMILY
        regular = embedded_fonts[0].find(f"{{{NS_P}}}regular")
        bold = embedded_fonts[0].find(f"{{{NS_P}}}bold")
        assert regular is not None and regular.get(f"{{{NS_R}}}id")
        assert bold is not None and bold.get(f"{{{NS_R}}}id")

        rels_root = ET.fromstring(archive.read("ppt/_rels/presentation.xml.rels"))
        font_relationships = [
            element for element in rels_root.findall(f"{{{NS_RELS}}}Relationship")
            if element.get("Type") == FONT_RELATIONSHIP_TYPE
        ]
        assert len(font_relationships) == 2
        relationship_ids = {element.get("Id") for element in font_relationships}
        assert relationship_ids == {regular.get(f"{{{NS_R}}}id"), bold.get(f"{{{NS_R}}}id")}

        content_types_root = ET.fromstring(archive.read("[Content_Types].xml"))
        fntdata_defaults = [
            element for element in content_types_root.findall(f"{{{NS_CT}}}Default")
            if element.get("Extension") == "fntdata"
        ]
        assert len(fntdata_defaults) == 1
        assert fntdata_defaults[0].get("ContentType") == FONT_CONTENT_TYPE

    # python-pptx's own oxml parser must still accept the file: proves the XML
    # this module wrote is well-formed and namespace-correct, not just that
    # ElementTree could read back what it itself wrote.
    reopened = Presentation(str(target))
    assert len(reopened.slides) == 0


def test_embed_trusted_fonts_does_not_collide_with_existing_relationship_ids(tmp_path):
    target = _blank_pptx(tmp_path)
    with zipfile.ZipFile(target) as archive:
        rels_root = ET.fromstring(archive.read("ppt/_rels/presentation.xml.rels"))
    existing_ids = {element.get("Id") for element in rels_root.findall(f"{{{NS_RELS}}}Relationship")}
    assert existing_ids, "a default python-pptx template always has at least one relationship"

    embed_trusted_fonts(target)

    with zipfile.ZipFile(target) as archive:
        rels_root = ET.fromstring(archive.read("ppt/_rels/presentation.xml.rels"))
    all_ids = [element.get("Id") for element in rels_root.findall(f"{{{NS_RELS}}}Relationship")]
    assert len(all_ids) == len(set(all_ids)), "relationship ids must stay unique after embedding"
    assert existing_ids <= set(all_ids)
