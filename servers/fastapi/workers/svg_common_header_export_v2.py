"""Read back every editable text, shape and asset of a v2 common header."""
from __future__ import annotations

from dataclasses import replace
import posixpath
from xml.etree import ElementTree as ET
import zipfile
import hashlib

from services.svg_common_header_export_evidence import common_header_verifier_digest, digest, header_scene
from services.svg_contract import source_digest

P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"


def signature(node):
    text = node.text or ""
    if node.tag != f"{{{A}}}t" and not text.strip():
        text = ""
    return [node.tag, sorted(node.attrib.items()), text, [signature(child) for child in node]]


class CommonHeaderV2Export:
    def __init__(self, sources, pt_per_px):
        self.sources = sources
        self.scenes = [header_scene(source) if source else None for source in sources]
        self.pt_per_px = pt_per_px
        self.originals = {}
        self.captured = {}
        self.converter = None

    @property
    def active(self):
        return any(self.scenes)

    def install(self, converter):
        if not self.active:
            return
        self.converter = converter
        ET.register_namespace("p", P)
        ET.register_namespace("a", A)
        ET.register_namespace("r", R)
        for tag, original in list(converter._CONVERTERS.items()):
            self.originals[tag] = original

            def convert(element, context, original=original):
                page, identity = context.slide_num - 1, element.get("id")
                scene = self.scenes[page]
                expected = (scene or {}).get("elements", {}).get(identity)
                result = original(element, context)
                if expected is None:
                    return result
                if (result is None or dict(element.attrib) != expected["attributes"]
                        or (element.text or "") != expected["text"]):
                    raise ValueError("svg_common_header_export_binding_invalid")
                wrapper = ET.fromstring(f'<root xmlns:p="{P}" xmlns:a="{A}" xmlns:r="{R}">' + result.xml + '</root>')
                if len(wrapper) != 1:
                    raise ValueError("svg_common_header_export_not_editable")
                shape = wrapper[0]
                kind = "pic" if expected["tag"] == "image" else "sp"
                nv = shape.find(f"{{{P}}}" + ("nvPicPr" if kind == "pic" else "nvSpPr") + f"/{{{P}}}cNvPr")
                if shape.tag != f"{{{P}}}{kind}" or nv is None:
                    raise ValueError("svg_common_header_export_not_editable")
                if expected["tag"] == "text":
                    attrs = expected["attributes"]
                    body = shape.find(f"{{{P}}}txBody/{{{A}}}bodyPr")
                    runs = shape.findall(f"{{{P}}}txBody/{{{A}}}p/{{{A}}}r")
                    if body is None or len(runs) != 1 or runs[0].find(f"{{{A}}}t") is None:
                        raise ValueError("svg_common_header_export_not_editable")
                    text = runs[0].find(f"{{{A}}}t")
                    if (text.text or "").strip() != expected["text"].strip():
                        raise ValueError("svg_common_header_export_title_mismatch")
                    text.text = expected["text"]
                    text.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
                    for child in list(body):
                        if child.tag in {f"{{{A}}}{name}" for name in ("spAutoFit", "normAutofit", "noAutofit")}:
                            body.remove(child)
                    ET.SubElement(body, f"{{{A}}}noAutofit")
                    properties = runs[0].find(f"{{{A}}}rPr")
                    if properties is None:
                        raise ValueError("svg_common_header_export_font_mismatch")
                    if abs(int(properties.get("sz", "0")) / 100 - float(attrs["font-size"]) * self.pt_per_px) > .051:
                        raise ValueError("svg_common_header_export_font_mismatch")
                    color = properties.find(f"{{{A}}}solidFill/{{{A}}}srgbClr")
                    paragraph = shape.find(f"{{{P}}}txBody/{{{A}}}p/{{{A}}}pPr")
                    if (color is None or color.get("val", "").lower() != attrs["fill"].lstrip("#").lower()
                            or (properties.get("b") == "1") != (attrs.get("font-weight") in {"bold", "600", "700", "800", "900"})
                            or paragraph is None or paragraph.get("algn", "l") != {"start": "l", "middle": "ctr", "end": "r"}[attrs.get("text-anchor", "start")]):
                        raise ValueError("svg_common_header_export_font_mismatch")
                    for name in ("latin", "ea", "cs"):
                        face = properties.find(f"{{{A}}}{name}")
                        if face is None:
                            face = ET.SubElement(properties, f"{{{A}}}{name}")
                        face.set("typeface", attrs["font-family"])
                    result = replace(result, xml=ET.tostring(shape, encoding="unicode"))
                key = page, identity
                if key in self.captured:
                    raise ValueError("svg_common_header_export_binding_invalid")
                self.captured[key] = {"shapeId": int(nv.get("id")), "signature": signature(shape),
                                      "boundsEmu": list(result.bounds_emu), "kind": kind}
                return result

            converter._CONVERTERS[tag] = convert

    def restore(self):
        if self.converter is not None:
            self.converter._CONVERTERS.update(self.originals)

    def verify(self, target, *, legacy_readback=None):
        if not self.active:
            return legacy_readback
        pages = []
        with zipfile.ZipFile(target) as archive:
            if len(archive.namelist()) != len(set(archive.namelist())):
                raise ValueError("svg_common_header_export_package_invalid")
            for index, scene in enumerate(self.scenes):
                if scene is None:
                    continue
                root = ET.fromstring(archive.read(f"ppt/slides/slide{index + 1}.xml"))
                shapes = {}
                for node in root.iter():
                    if node.tag not in {f"{{{P}}}sp", f"{{{P}}}pic"}:
                        continue
                    nv = next((child for child in node if child.tag.rsplit("}", 1)[-1].startswith("nv")), None)
                    identity = None if nv is None else nv.find(f"{{{P}}}cNvPr")
                    if identity is None or int(identity.get("id")) in shapes:
                        raise ValueError("svg_common_header_export_binding_invalid")
                    shapes[int(identity.get("id"))] = node
                relationships = {}
                rel_path = f"ppt/slides/_rels/slide{index + 1}.xml.rels"
                if rel_path in archive.namelist():
                    for rel in ET.fromstring(archive.read(rel_path)):
                        if rel.get("Id") in relationships:
                            raise ValueError("svg_common_header_export_asset_invalid")
                        relationships[rel.get("Id")] = rel
                records, ids = [], set()
                for identity, expected in scene["elements"].items():
                    captured = self.captured.get((index, identity))
                    if captured is None or captured["shapeId"] not in shapes or captured["shapeId"] in ids:
                        raise ValueError("svg_common_header_export_not_editable")
                    shape = shapes[captured["shapeId"]]
                    if signature(shape) != captured["signature"]:
                        raise ValueError("svg_common_header_export_ooxml_mismatch")
                    ids.add(captured["shapeId"])
                    record = {"elementId": identity, "tag": expected["tag"], "shapeId": captured["shapeId"],
                              "nativeKind": captured["kind"], "sourceElementDigest": digest(expected),
                              "ooxmlDigest": digest(captured["signature"]), "boundsEmu": captured["boundsEmu"], "editable": True}
                    if expected["tag"] == "image":
                        blips = shape.findall(f".//{{{A}}}blip")
                        if len(blips) != 1 or blips[0].get(f"{{{R}}}link") is not None:
                            raise ValueError("svg_common_header_export_asset_invalid")
                        rel = relationships.get(blips[0].get(f"{{{R}}}embed"))
                        if rel is None or rel.get("TargetMode") is not None or not rel.get("Type", "").endswith("/image"):
                            raise ValueError("svg_common_header_export_asset_invalid")
                        media = posixpath.normpath(posixpath.join("ppt/slides", rel.get("Target", "")))
                        if not media.startswith("ppt/media/"):
                            raise ValueError("svg_common_header_export_asset_invalid")
                        actual = "sha256:" + hashlib.sha256(archive.read(media)).hexdigest()
                        if actual != expected["assetDigest"]:
                            raise ValueError("svg_common_header_export_asset_mismatch")
                        record["assetDigest"] = actual
                    elif expected["tag"] == "text":
                        record["text"] = "".join(node.text or "" for node in shape.findall(f"{{{P}}}txBody/{{{A}}}p/{{{A}}}r/{{{A}}}t"))
                        if record["text"] != expected["text"]:
                            raise ValueError("svg_common_header_export_title_mismatch")
                    records.append(record)
                pages.append({"pageIndex": index, "svgDigest": source_digest(self.sources[index]),
                              "renderedGroupDigest": scene["groupDigest"], "elements": records})
        value = {"schemaVersion": "svg-common-header-ooxml/v2", "origin": "generated-common-header", "passed": True,
                 "pages": pages, "pointPerPx": self.pt_per_px, "officeRenderMeasured": False,
                 "verifierDigest": common_header_verifier_digest(), "legacyReadback": legacy_readback}
        return {**value, "digest": digest(value)}


def verify_final_common_header_readback(target, conversion_readback, canonical_sources):
    """Re-read the last saved package and prove its canonical header projection."""
    if not isinstance(conversion_readback, dict) or conversion_readback.get("schemaVersion") != "svg-common-header-ooxml/v2":
        return conversion_readback
    from copy import deepcopy

    parent = deepcopy(conversion_readback)
    unsigned = {key: item for key, item in parent.items() if key != "digest"}
    if parent.get("digest") != digest(unsigned) or parent.get("verifierDigest") != common_header_verifier_digest():
        raise ValueError("svg_common_header_export_binding_invalid")
    scenes = [(index, header_scene(source)) for index, source in enumerate(canonical_sources)]
    scenes = [(index, scene) for index, scene in scenes if scene is not None]
    if len(scenes) != len(parent.get("pages", [])):
        raise ValueError("svg_common_header_export_binding_invalid")
    pages = []
    with zipfile.ZipFile(target) as archive:
        if len(archive.namelist()) != len(set(archive.namelist())):
            raise ValueError("svg_common_header_export_package_invalid")
        for (index, scene), prior in zip(scenes, parent["pages"], strict=True):
            if (prior.get("pageIndex") != index or prior.get("renderedGroupDigest") != scene["groupDigest"]
                    or len(prior.get("elements", [])) != len(scene["elements"])):
                raise ValueError("svg_common_header_export_binding_invalid")
            root = ET.fromstring(archive.read(f"ppt/slides/slide{index + 1}.xml"))
            shapes = {}
            for node in root.iter():
                if node.tag not in {f"{{{P}}}sp", f"{{{P}}}pic"}:
                    continue
                nv = next((child for child in node if child.tag.rsplit("}", 1)[-1].startswith("nv")), None)
                identity = None if nv is None else nv.find(f"{{{P}}}cNvPr")
                if identity is None or int(identity.get("id")) in shapes:
                    raise ValueError("svg_common_header_export_binding_invalid")
                shapes[int(identity.get("id"))] = node
            rel_path = f"ppt/slides/_rels/slide{index + 1}.xml.rels"
            rels = {}
            if rel_path in archive.namelist():
                for rel in ET.fromstring(archive.read(rel_path)):
                    if rel.get("Id") in rels:
                        raise ValueError("svg_common_header_export_asset_invalid")
                    rels[rel.get("Id")] = rel
            for record, (identity, expected) in zip(prior["elements"], scene["elements"].items(), strict=True):
                shape = shapes.get(record.get("shapeId"))
                if (shape is None or record.get("elementId") != identity or record.get("sourceElementDigest") != digest(expected)
                        or digest(signature(shape)) != record.get("ooxmlDigest")):
                    raise ValueError("svg_common_header_export_ooxml_mismatch")
                if expected["tag"] == "image":
                    blips = shape.findall(f".//{{{A}}}blip")
                    if len(blips) != 1 or blips[0].get(f"{{{R}}}link") is not None:
                        raise ValueError("svg_common_header_export_asset_invalid")
                    rel = rels.get(blips[0].get(f"{{{R}}}embed"))
                    if rel is None or rel.get("TargetMode") is not None or not rel.get("Type", "").endswith("/image"):
                        raise ValueError("svg_common_header_export_asset_invalid")
                    media = posixpath.normpath(posixpath.join("ppt/slides", rel.get("Target", "")))
                    if not media.startswith("ppt/media/") or "sha256:" + hashlib.sha256(archive.read(media)).hexdigest() != expected["assetDigest"]:
                        raise ValueError("svg_common_header_export_asset_mismatch")
            pages.append({**prior, "conversionSvgDigest": prior["svgDigest"],
                          "svgDigest": source_digest(canonical_sources[index])})
    value = {**unsigned, "pages": pages, "finalPackageReverified": True,
             "conversionReadbackDigest": parent["digest"],
             "canonicalProjectionBinding": {"sameRenderedHeaderGroups": True,
                "canonicalSourceDigests": [source_digest(source) for source in canonical_sources],
                "conversionReadbackDigest": parent["digest"]}}
    return {**value, "digest": digest(value)}
