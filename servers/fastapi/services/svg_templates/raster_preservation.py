"""Fixed template decoration kept as an image cut from a source render.

A framed template's furniture is converted to vector SVG element by element
(``frame_ooxml``). Decoration the converter does not support (gradients,
cropped or effected pictures, preset shapes, rotated or group-only geometry,
non-solid backgrounds) is preserved instead as an image of exactly those
shapes, after the person confirms it in review
(docs/svg-template-real-deck-registration-design-20261103.md, D-1/D-2).
Text is never part of such an image: text inside a preserved shape is
emptied here and drawn as vector text by the compiler (D-5).

Each run of consecutive decoration becomes one render request. All requests
are placed on new slides of one render deck: every slide hides the template's
master and layout graphics and carries copies of only its run's shapes, at
their slide positions. The deck is rendered twice, on a white and on a black
slide background, through LibreOffice -> PDF -> pdfium; alpha and colour are
recovered per pixel (``alpha = 1 - (white - black)``, ``colour = black /
alpha``), so soft edges and translucent paint survive. A background request
keeps the source background and is opaque. The result is cropped to its
visible pixels at ``RASTER_SCALE`` times the logical canvas.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from copy import deepcopy
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path, PurePosixPath
from zipfile import ZIP_DEFLATED, ZipFile

from lxml import etree

from services.source_content.pptx_extraction_constants import NS, R_NS
from services.source_content.pptx_package_parts import _relationship_map

RASTER_SCALE = 2
#: A pixel counts as visible above this alpha (0-255); anti-aliasing below it is noise.
_VISIBLE_ALPHA = 3
_P = NS["p"]
_A = NS["a"]
_CT = "http://schemas.openxmlformats.org/package/2006/content-types"
_PR = "http://schemas.openxmlformats.org/package/2006/relationships"
_SLIDE_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide"
_LAYOUT_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout"
_SLIDE_CONTENT = "application/vnd.openxmlformats-officedocument.presentationml.slide+xml"
_TIMEOUT_SECONDS = 180


@dataclass(frozen=True)
class RasterShape:
    """One source shape of a run: its part, its XML and, when the shape's own
    transform is not its slide position (group leaf, inherited placeholder),
    its slide rectangle in EMU."""

    part: str
    element: object
    emu_rect: tuple[int, int, int, int] | None = None
    hide_text: bool = False


@dataclass(frozen=True)
class RasterRequest:
    key: str
    shapes: tuple[RasterShape, ...] = ()
    #: The part whose ``p:bg`` is the run (a background run has no shapes).
    background_part: str | None = None


@dataclass(frozen=True)
class RasterResult:
    data: bytes
    media_type: str
    x: float
    y: float
    width: float
    height: float
    renderer: str
    renderer_version: str
    approximate: bool


def libreoffice_raster_renderer():
    """The renderer for registration, or ``None`` when LibreOffice is absent."""
    from services.svg_templates.source_review import _libreoffice_executable
    return render_raster_requests if _libreoffice_executable() else None


def _local(node) -> str:
    return etree.QName(node).localname


def _relative_target(source_part: str, target_part: str) -> str:
    return os.path.relpath(target_part, str(PurePosixPath(source_part).parent)).replace(os.sep, "/")


def _copy_shape(shape: RasterShape, relationships: dict, new_part: str, rels: list) -> etree._Element:
    element = deepcopy(shape.element)
    if shape.emu_rect is not None:
        local = _local(element)
        container = element.find("p:grpSpPr" if local == "grpSp" else "p:spPr", NS)
        if container is None:
            container = etree.SubElement(element, f"{{{_P}}}{'grpSpPr' if local == 'grpSp' else 'spPr'}")
        xfrm = container.find("a:xfrm", NS)
        if xfrm is None:
            xfrm = etree.Element(f"{{{_A}}}xfrm")
            container.insert(0, xfrm)
        for child in list(xfrm):
            if _local(child) in {"off", "ext"}:
                xfrm.remove(child)
        x, y, cx, cy = shape.emu_rect
        xfrm.insert(0, etree.Element(f"{{{_A}}}ext", cx=str(cx), cy=str(cy)))
        xfrm.insert(0, etree.Element(f"{{{_A}}}off", x=str(x), y=str(y)))
    if shape.hide_text:
        for text in element.iter(f"{{{_A}}}t"):
            text.text = ""
    # Re-point every relationship the copy uses (images, fills) at the same
    # package part from the new slide.
    for node in element.iter():
        for attribute, value in list(node.attrib.items()):
            if not attribute.startswith(f"{{{R_NS}}}") or not value:
                continue
            relation = relationships.get(value)
            if relation is None:
                raise ValueError("svg_template_raster_relationship_missing")
            target, kind = relation
            new_id = f"rIdR{len(rels) + 1}"
            rels.append((new_id, kind, _relative_target(new_part, target)))
            node.set(attribute, new_id)
    return element


def _slide_xml(shapes: list, background) -> bytes:
    root = etree.Element(f"{{{_P}}}sld", nsmap={"p": _P, "a": _A, "r": R_NS}, showMasterSp="0")
    common = etree.SubElement(root, f"{{{_P}}}cSld")
    if background is not None:
        common.append(background)
    tree = etree.SubElement(common, f"{{{_P}}}spTree")
    group = etree.SubElement(tree, f"{{{_P}}}nvGrpSpPr")
    etree.SubElement(group, f"{{{_P}}}cNvPr", id="1", name="")
    etree.SubElement(group, f"{{{_P}}}cNvGrpSpPr")
    etree.SubElement(group, f"{{{_P}}}nvPr")
    etree.SubElement(tree, f"{{{_P}}}grpSpPr")
    for shape in shapes:
        tree.append(shape)
    overrides = etree.SubElement(root, f"{{{_P}}}clrMapOvr")
    etree.SubElement(overrides, f"{{{_A}}}masterClrMapping")
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _solid_background(color: str):
    background = etree.Element(f"{{{_P}}}bg")
    properties = etree.SubElement(background, f"{{{_P}}}bgPr")
    fill = etree.SubElement(properties, f"{{{_A}}}solidFill")
    etree.SubElement(fill, f"{{{_A}}}srgbClr", val=color)
    etree.SubElement(properties, f"{{{_A}}}effectLst")
    return background


def build_render_deck(source: bytes, requests: list[RasterRequest], color: str) -> bytes:
    """The source package with its slide list replaced by one slide per request."""
    with ZipFile(BytesIO(source)) as package:
        names = set(package.namelist())
        presentation = etree.fromstring(package.read("ppt/presentation.xml"))
        presentation_rels = etree.fromstring(package.read("ppt/_rels/presentation.xml.rels"))
        content_types = etree.fromstring(package.read("[Content_Types].xml"))
        layout = next(sorted(name for name in names if name.startswith("ppt/slideLayouts/")
                             and name.endswith(".xml") and "/_rels/" not in name).__iter__(), None)
        if layout is None:
            raise ValueError("svg_template_raster_layout_missing")
        relationship_cache = {}
        new_parts = {}
        slide_ids = []
        for relation in list(presentation_rels):
            if relation.get("Type") == _SLIDE_TYPE:
                presentation_rels.remove(relation)
        id_list = presentation.find("p:sldIdLst", NS)
        if id_list is None:
            raise ValueError("svg_template_raster_slide_list_missing")
        for child in list(id_list):
            id_list.remove(child)
        for index, request in enumerate(requests):
            part = f"ppt/slides/raster-render-{index + 1}.xml"
            rels = [("rIdLayout", _LAYOUT_TYPE, _relative_target(part, layout))]
            shapes = []
            for shape in request.shapes:
                if shape.part not in relationship_cache:
                    relationship_cache[shape.part] = _relationship_map(package, shape.part)
                shapes.append(_copy_shape(shape, relationship_cache[shape.part], part, rels))
            if request.background_part is not None:
                node = etree.fromstring(package.read(request.background_part)).find("p:cSld/p:bg", NS)
                if node is None:
                    raise ValueError("svg_template_raster_background_missing")
                if request.background_part not in relationship_cache:
                    relationship_cache[request.background_part] = _relationship_map(package, request.background_part)
                background = _copy_shape(RasterShape(part=request.background_part, element=node),
                                         relationship_cache[request.background_part], part, rels)
            else:
                background = _solid_background(color)
            new_parts[part] = _slide_xml(shapes, background)
            relations = etree.Element(f"{{{_PR}}}Relationships")
            for relation_id, kind, target in rels:
                etree.SubElement(relations, f"{{{_PR}}}Relationship", Id=relation_id, Type=kind, Target=target)
            new_parts[f"ppt/slides/_rels/raster-render-{index + 1}.xml.rels"] = etree.tostring(
                relations, xml_declaration=True, encoding="UTF-8", standalone=True)
            relation_id = f"rIdRender{index + 1}"
            etree.SubElement(presentation_rels, f"{{{_PR}}}Relationship", Id=relation_id, Type=_SLIDE_TYPE,
                             Target=_relative_target("ppt/presentation.xml", part))
            slide_ids.append((str(1000 + index), relation_id))
            etree.SubElement(content_types, f"{{{_CT}}}Override", PartName="/" + part, ContentType=_SLIDE_CONTENT)
        for slide_id, relation_id in slide_ids:
            etree.SubElement(id_list, f"{{{_P}}}sldId", id=slide_id, **{f"{{{R_NS}}}id": relation_id})
        output = BytesIO()
        with ZipFile(output, "w", ZIP_DEFLATED) as target:
            for name in package.namelist():
                if name in {"ppt/presentation.xml", "ppt/_rels/presentation.xml.rels", "[Content_Types].xml"}:
                    continue
                target.writestr(name, package.read(name))
            target.writestr("ppt/presentation.xml", etree.tostring(
                presentation, xml_declaration=True, encoding="UTF-8", standalone=True))
            target.writestr("ppt/_rels/presentation.xml.rels", etree.tostring(
                presentation_rels, xml_declaration=True, encoding="UTF-8", standalone=True))
            target.writestr("[Content_Types].xml", etree.tostring(
                content_types, xml_declaration=True, encoding="UTF-8", standalone=True))
            for name, data in new_parts.items():
                target.writestr(name, data)
        return output.getvalue()


def _convert(deck: bytes, directory: Path, name: str) -> bytes:
    from services.svg_templates.source_review import _libreoffice_executable
    executable = _libreoffice_executable()
    if not executable:
        raise ValueError("svg_template_raster_renderer_unavailable")
    source = directory / f"{name}.pptx"
    source.write_bytes(deck)
    completed = subprocess.run([executable, "--headless", "--norestore", "--nolockcheck",
                                f"-env:UserInstallation={(directory / ('profile-' + name)).as_uri()}",
                                "--convert-to", "pdf", "--outdir", str(directory), str(source)],
                               capture_output=True, timeout=_TIMEOUT_SECONDS,
                               env={**os.environ, "HOME": str(directory)}, check=False)
    output = directory / f"{name}.pdf"
    if completed.returncode != 0 or not output.is_file():
        raise ValueError("svg_template_raster_render_failed")
    return output.read_bytes()


def _pages(pdf: bytes, width_px: int):
    import numpy as np
    import pypdfium2 as pdfium
    document = pdfium.PdfDocument(pdf)
    try:
        for index in range(len(document)):
            page = document[index]
            try:
                bitmap = page.render(scale=width_px * RASTER_SCALE / page.get_width())
                yield np.asarray(bitmap.to_pil().convert("RGB")).astype("float64")
                bitmap.close()
            finally:
                page.close()
    finally:
        document.close()


def recover_alpha(white, black):
    """RGBA uint8 array from renders of the same content on white and on black."""
    import numpy as np
    alpha = np.clip(1.0 - (white - black).mean(axis=2) / 255.0, 0.0, 1.0)
    colour = np.where(alpha[..., None] > 1e-3, black / np.maximum(alpha[..., None], 1e-3), 0.0)
    return np.dstack([np.clip(colour, 0, 255), alpha * 255]).round().astype("uint8")


def _encode(rgba) -> tuple[bytes, str, tuple[int, int, int, int]] | None:
    import numpy as np
    from PIL import Image
    visible = rgba[..., 3] > _VISIBLE_ALPHA
    if not visible.any():
        return None
    rows, columns = np.where(visible.any(axis=1))[0], np.where(visible.any(axis=0))[0]
    top, bottom, left, right = rows[0], rows[-1] + 1, columns[0], columns[-1] + 1
    crop = rgba[top:bottom, left:right]
    output = BytesIO()
    if (crop[..., 3] == 255).all():
        Image.fromarray(crop[..., :3], "RGB").save(output, format="JPEG", quality=90)
        media_type = "image/jpeg"
    else:
        Image.fromarray(crop, "RGBA").save(output, format="PNG", optimize=True)
        media_type = "image/png"
    return output.getvalue(), media_type, (int(left), int(top), int(right - left), int(bottom - top))


def render_raster_requests(source_path, canvas, requests: list[RasterRequest]) -> dict[str, RasterResult | None]:
    """Render every request; ``None`` for a request that shows nothing."""
    import numpy as np
    from services.svg_templates.source_review import _libreoffice_executable, _libreoffice_version
    if not requests:
        return {}
    executable = _libreoffice_executable()
    if not executable:
        raise ValueError("svg_template_raster_renderer_unavailable")
    version = _libreoffice_version(executable)
    source = Path(source_path).read_bytes()
    with tempfile.TemporaryDirectory(prefix="svg-template-raster-") as raw:
        directory = Path(raw)
        white = list(_pages(_convert(build_render_deck(source, requests, "FFFFFF"), directory, "white"),
                            canvas.width_px))
        black = list(_pages(_convert(build_render_deck(source, requests, "000000"), directory, "black"),
                            canvas.width_px))
    if len(white) != len(requests) or len(black) != len(requests):
        raise ValueError("svg_template_raster_render_failed")
    results = {}
    for request, on_white, on_black in zip(requests, white, black, strict=True):
        if request.background_part is not None:
            rgba = np.dstack([on_white, np.full(on_white.shape[:2], 255.0)]).round().astype("uint8")
        else:
            rgba = recover_alpha(on_white, on_black)
        encoded = _encode(rgba)
        if encoded is None:
            results[request.key] = None
            continue
        data, media_type, (left, top, width, height) = encoded
        results[request.key] = RasterResult(data=data, media_type=media_type, x=left / RASTER_SCALE,
            y=top / RASTER_SCALE, width=width / RASTER_SCALE, height=height / RASTER_SCALE,
            renderer="libreoffice", renderer_version=version, approximate=True)
    return results


__all__ = ["RASTER_SCALE", "RasterRequest", "RasterResult", "RasterShape", "build_render_deck",
           "libreoffice_raster_renderer", "recover_alpha", "render_raster_requests"]
