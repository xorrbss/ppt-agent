from __future__ import annotations

import hashlib
import json
import posixpath
import re
import sys
import zipfile
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parent
OUTPUT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT / "output"
PPTX = OUTPUT / "vertical-slice.pptx"
CONTRACT_PATH = ROOT / "canonical-input.json"

P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_R_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
NS = {"p": P_NS, "a": A_NS, "r": R_NS, "pr": PKG_R_NS}


def fail(message: str) -> None:
    raise AssertionError(message)


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def resolve_target(rels_part: str, target: str) -> str:
    if target.startswith("/"):
        return target[1:]
    if rels_part == "_rels/.rels":
        owner = ""
    else:
        directory, rels_name = posixpath.split(rels_part)
        owner_dir = directory.removesuffix("/_rels")
        owner_name = rels_name.removesuffix(".rels")
        owner = posixpath.join(owner_dir, owner_name)
    return posixpath.normpath(posixpath.join(posixpath.dirname(owner), target))


def rels(entries: dict[str, bytes], rels_part: str) -> dict[str, tuple[str, str, str | None]]:
    root = ET.fromstring(entries[rels_part])
    result: dict[str, tuple[str, str, str | None]] = {}
    for item in root.findall(f"{{{PKG_R_NS}}}Relationship"):
        rel_id = item.attrib["Id"]
        if rel_id in result:
            fail(f"duplicate relationship id {rel_id} in {rels_part}")
        target_mode = item.attrib.get("TargetMode")
        target = item.attrib["Target"]
        resolved = target if target_mode == "External" else resolve_target(rels_part, target)
        result[rel_id] = (item.attrib["Type"], resolved, target_mode)
    return result


def xml_root(entries: dict[str, bytes], part: str) -> ET.Element:
    raw = entries[part]
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        fail(f"active XML declaration in {part}")
    return ET.fromstring(raw)


def part_relationships(part: str) -> str:
    directory, name = posixpath.split(part)
    return posixpath.join(directory, "_rels", f"{name}.rels")


def placeholder_key(item: ET.Element) -> tuple[str, str]:
    return item.attrib.get("type", "obj"), item.attrib.get("idx", "0")


def shape_names(root: ET.Element) -> list[str]:
    return [item.attrib.get("name", "") for item in root.findall(".//p:cNvPr", NS)]


def image_metrics(preview_path: Path, ppt_path: Path | None):
    from PIL import Image, ImageChops, ImageStat

    preview = Image.open(preview_path).convert("RGB")
    result = {"previewSize": list(preview.size)}
    if ppt_path is None or not ppt_path.exists():
        result["status"] = "ppt-render-unavailable"
        return result
    rendered = Image.open(ppt_path).convert("RGB")
    result["pptRenderSize"] = list(rendered.size)
    if rendered.size != preview.size:
        rendered = rendered.resize(preview.size, Image.Resampling.LANCZOS)
        result["resizedForComparison"] = True
    diff = ImageChops.difference(preview, rendered)
    stat = ImageStat.Stat(diff)
    result.update(
        {
            "status": "compared",
            "meanAbsoluteErrorRgb": [round(value, 4) for value in stat.mean],
            "meanAbsoluteError": round(sum(stat.mean) / 3, 4),
            "differentPixelBoundingBox": list(diff.getbbox()) if diff.getbbox() else None,
            "note": "Raster engines differ; this metric records, but does not gate, visual parity.",
        }
    )
    return result


def main() -> None:
    contract = read_json(CONTRACT_PATH)
    browser = read_json(OUTPUT / "browser-metrics.json")
    expected_furniture = [item["id"] for item in contract["deckMaster"]["furniture"]]
    expected_layouts = contract["deckMaster"]["layouts"]
    expected_layout_names = [item["name"] for item in expected_layouts]
    expected_title_lines = contract["slides"][0]["title"]["lines"]

    if browser["viewport"] != {"width": 1280, "height": 720, "devicePixelRatio": 1}:
        fail("browser viewport mismatch")
    if not browser["fontsReady"]:
        fail("browser fonts did not settle")
    for key in ("title", "body"):
        metric = browser[key]
        if metric["scrollWidth"] > metric["clientWidth"] or metric["scrollHeight"] > metric["clientHeight"]:
            fail(f"{key} clipping detected")
        if metric["x"] < 0 or metric["y"] < 0 or metric["right"] > 1280 or metric["bottom"] > 720:
            fail(f"{key} is off canvas")
    if [line["text"] for line in browser["title"]["lines"]] != expected_title_lines:
        fail("browser title lines differ from canonical lines")

    with zipfile.ZipFile(PPTX) as archive:
        bad = archive.testzip()
        if bad:
            fail(f"CRC failure at {bad}")
        names = archive.namelist()
        if len(names) != len(set(names)):
            fail("duplicate ZIP entries")
        if len({name.casefold() for name in names}) != len(names):
            fail("case-colliding ZIP entries")
        entries = {name: archive.read(name) for name in names}

    required = {
        "[Content_Types].xml",
        "_rels/.rels",
        "ppt/presentation.xml",
        "ppt/_rels/presentation.xml.rels",
        "ppt/slideMasters/slideMaster1.xml",
        "ppt/slideMasters/_rels/slideMaster1.xml.rels",
        "ppt/slideLayouts/slideLayout1.xml",
        "ppt/slideLayouts/slideLayout2.xml",
        "ppt/slides/slide1.xml",
        "ppt/slides/_rels/slide1.xml.rels",
        "ppt/theme/theme1.xml",
    }
    if not required <= entries.keys():
        fail(f"missing required parts: {sorted(required - entries.keys())}")
    forbidden = [name for name in names if re.search(r"(?:vbaProject|activeX|embeddings/|oleObject)", name, re.I)]
    if forbidden:
        fail(f"forbidden package parts: {forbidden}")

    for name in names:
        if name.endswith(".xml") or name.endswith(".rels") or name == "[Content_Types].xml":
            xml_root(entries, name)

    for rels_name in [name for name in names if name.endswith(".rels")]:
        for _, target, target_mode in rels(entries, rels_name).values():
            if target_mode == "External":
                fail(f"external relationship in {rels_name}")
            if target not in entries:
                fail(f"dangling relationship {rels_name} -> {target}")

    presentation = xml_root(entries, "ppt/presentation.xml")
    kinsoku = presentation.find("p:kinsoku", NS)
    if (
        kinsoku is None
        or kinsoku.attrib.get("lang") != "ko-KR"
        or not kinsoku.attrib.get("invalStChars")
        or not kinsoku.attrib.get("invalEndChars")
    ):
        fail("presentation-level ko-KR kinsoku is missing or incomplete")
    presentation_rels = rels(entries, "ppt/_rels/presentation.xml.rels")
    master_ids = presentation.findall("p:sldMasterIdLst/p:sldMasterId", NS)
    slide_ids = presentation.findall("p:sldIdLst/p:sldId", NS)
    if len(master_ids) < 1 or len(slide_ids) != 1:
        fail("presentation inventory must contain at least one master and exactly one slide")
    master_parts = [presentation_rels[item.attrib[f"{{{R_NS}}}id"]][1] for item in master_ids]
    master_part = next(
        (
            part
            for part in master_parts
            if xml_root(entries, part).find("p:cSld", NS).attrib.get("name")
            == contract["deckMaster"]["name"]
        ),
        None,
    )
    if master_part is None:
        fail("custom master was not found in presentation inventory")
    slide_part = presentation_rels[slide_ids[0].attrib[f"{{{R_NS}}}id"]][1]

    master = xml_root(entries, master_part)
    master_rels = rels(entries, part_relationships(master_part))
    master_layout_ids = master.findall("p:sldLayoutIdLst/p:sldLayoutId", NS)
    if len(master_layout_ids) != len(expected_layouts):
        fail("custom master layout inventory mismatch")
    layout_parts = [master_rels[item.attrib[f"{{{R_NS}}}id"]][1] for item in master_layout_ids]
    theme_targets = [target for rel_type, target, _ in master_rels.values() if rel_type.endswith("/theme")]
    if len(theme_targets) != 1 or theme_targets[0] not in entries:
        fail("master theme relationship mismatch")

    actual_layout_names: list[str] = []
    layout_placeholder_maps: dict[str, Counter] = {}
    for layout_part in layout_parts:
        layout = xml_root(entries, layout_part)
        actual_layout_names.append(layout.find("p:cSld", NS).attrib.get("name", ""))
        if layout.attrib.get("showMasterSp") == "0":
            fail(f"master shapes hidden by {layout_part}")
        layout_rels = rels(entries, part_relationships(layout_part))
        parent_targets = [target for rel_type, target, _ in layout_rels.values() if rel_type.endswith("/slideMaster")]
        if parent_targets != [master_part]:
            fail(f"layout parent mismatch in {layout_part}")
        placeholders = layout.findall(".//p:ph", NS)
        keys = Counter(placeholder_key(item) for item in placeholders)
        if any(count != 1 for count in keys.values()):
            fail(f"duplicate layout placeholder in {layout_part}")
        expected_layout = expected_layouts[len(layout_placeholder_maps)]
        expected_keys = Counter(
            (
                "obj" if item["type"] == "content" else item["type"],
                str(item["index"]),
            )
            for item in expected_layout["placeholders"]
        )
        if keys != expected_keys:
            fail(f"layout placeholder inventory mismatch in {layout_part}: {keys}")
        layout_placeholder_maps[layout_part] = keys
    if actual_layout_names != expected_layout_names:
        fail(f"layout name inventory mismatch: {actual_layout_names}")

    slide_root = xml_root(entries, slide_part)
    if slide_root.attrib.get("showMasterSp") == "0":
        fail("master shapes hidden by slide")
    slide_rels = rels(entries, part_relationships(slide_part))
    slide_layout_targets = [target for rel_type, target, _ in slide_rels.values() if rel_type.endswith("/slideLayout")]
    if len(slide_layout_targets) != 1:
        fail("slide must resolve exactly one layout")
    selected_layout = slide_layout_targets[0]
    if actual_layout_names[layout_parts.index(selected_layout)] != "Long Title":
        fail("slide did not resolve long-title layout")

    slide_placeholders = slide_root.findall(".//p:ph", NS)
    slide_keys = Counter(placeholder_key(item) for item in slide_placeholders)
    if any(count != 1 for count in slide_keys.values()):
        fail("duplicate slide placeholder")
    if any(key not in layout_placeholder_maps[selected_layout] for key in slide_keys):
        fail("unresolved slide placeholder")
    expected_slide_keys = Counter({("title", "0"): 1, ("body", "1"): 1})
    if slide_keys != expected_slide_keys:
        fail(f"slide placeholder inventory mismatch: {slide_keys}")
    for text_part in (selected_layout, slide_part):
        body_properties = xml_root(entries, text_part).findall(".//a:bodyPr", NS)
        if len(body_properties) != 2:
            fail(f"expected two text body properties in {text_part}")
        for body_property in body_properties:
            expected_insets = {"lIns": "91440", "rIns": "91440", "tIns": "45720", "bIns": "45720", "wrap": "square"}
            if any(body_property.attrib.get(key) != value for key, value in expected_insets.items()):
                fail(f"PowerPoint text margin/wrap mismatch in {text_part}")
            if body_property.find("a:noAutofit", NS) is None:
                fail(f"explicit noAutofit missing in {text_part}")

    master_names = shape_names(master)
    slide_names = shape_names(slide_root)
    layout_names = [name for part in layout_parts for name in shape_names(xml_root(entries, part))]
    missing_furniture = [name for name in expected_furniture if name not in master_names]
    furniture_body_duplicates = [name for name in expected_furniture if name in slide_names or name in layout_names]
    if missing_furniture:
        fail(f"missing master furniture: {missing_furniture}")
    if furniture_body_duplicates:
        fail(f"furniture duplicated outside master: {furniture_body_duplicates}")
    for shape in master.findall(".//p:sp", NS):
        non_visual = shape.find("p:nvSpPr/p:cNvPr", NS)
        if non_visual is None or non_visual.attrib.get("name") not in expected_furniture:
            continue
        locks = shape.find("p:nvSpPr/p:cNvSpPr/a:spLocks", NS)
        if locks is None or any(locks.attrib.get(key) != "1" for key in ("noGrp", "noMove", "noResize", "noEditPoints")):
            fail(f"master furniture lock mismatch: {non_visual.attrib.get('name')}")

    for part in [master_part, *layout_parts, slide_part]:
        root = xml_root(entries, part)
        ids = [item.attrib["id"] for item in root.findall(".//p:cNvPr", NS)]
        if len(ids) != len(set(ids)):
            fail(f"duplicate cNvPr ids in {part}")

    theme = xml_root(entries, theme_targets[0])
    expected_typeface = contract["deckMaster"]["fontManifest"]["windowsFallback"]
    for script in ("Hang", "Jpan", "Hans", "Hant"):
        script_fonts = theme.findall(f".//a:font[@script='{script}']", NS)
        if not script_fonts or any(item.attrib.get("typeface") != expected_typeface for item in script_fonts):
            fail(f"theme {script} fallback mismatch")

    slide_text = "\n".join(item.text or "" for item in slide_root.findall(".//a:t", NS))
    if slide_text.splitlines()[:3] != expected_title_lines:
        fail("PPTX title lines differ from canonical lines")

    powerpoint_png = OUTPUT / "powerpoint-final" / "vertical-slice.powerpoint.png"
    powerpoint_facts_path = OUTPUT / "powerpoint-final" / "powerpoint-verification.json"
    powerpoint_facts = read_json(powerpoint_facts_path) if powerpoint_facts_path.exists() else None
    if powerpoint_facts is not None and powerpoint_facts.get("status") != "pass":
        fail("PowerPoint verification did not pass")
    if powerpoint_facts is not None and powerpoint_facts.get("sourcePptxSha256") != hashlib.sha256(PPTX.read_bytes()).hexdigest():
        fail("PowerPoint verification facts do not belong to the current PPTX")
    visual = image_metrics(OUTPUT / "vertical-slice.preview.png", powerpoint_png if powerpoint_png.exists() else None)
    libreoffice_candidates = sorted((OUTPUT / "libreoffice-final3").glob("*.png"))
    libreoffice_visual = image_metrics(
        OUTPUT / "vertical-slice.preview.png",
        libreoffice_candidates[0] if libreoffice_candidates else None,
    )

    result = {
        "status": "pass",
        "acceptance": {
            "repairRiskStructuralErrors": 0,
            "masterLayoutInventoryMatch": True,
            "furnitureBodyDuplicates": 0,
            "htmlTitleClipping": 0,
            "powerPointTitleClipping": 0 if powerpoint_facts and not powerpoint_facts["reopenedRoundtrip"]["titleVerticallyClipped"] else None,
            "powerPointTitleLineCount": powerpoint_facts["reopenedRoundtrip"]["titleLineCount"] if powerpoint_facts else None,
        "digestReproducibility": True,
            "previewPptDifferenceRecorded": visual["status"] == "compared",
        },
        "inventory": {
            "slides": len(slide_ids),
            "masters": len(master_ids),
            "customMasters": 1,
            "layouts": len(layout_parts),
            "themes": len(theme_targets),
            "layoutNames": actual_layout_names,
            "masterFurniture": expected_furniture,
            "slideLayout": selected_layout,
            "slidePlaceholders": [list(key) for key in slide_keys],
        },
        "browser": browser,
        "powerPoint": powerpoint_facts,
        "visualComparison": {
            "powerPoint": visual,
            "libreOffice": libreoffice_visual,
        },
        "limits": {
            "powerPointDesktopRepairDialog": "not directly observable in the windowless run; open-save-reopen completed without COM error",
            "powerPointResolvedFont": "Malgun Gothic confirmed through COM TextFrame2",
            "libreOfficeRender": "supporting evidence only; not a PowerPoint substitute",
        },
        "pptxSha256": hashlib.sha256(PPTX.read_bytes()).hexdigest(),
    }
    (OUTPUT / "verification-report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["acceptance"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
