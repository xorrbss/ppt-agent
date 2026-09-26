"""Verified raster replacements for hybrid PPTX pages."""

from __future__ import annotations

import hashlib
import json
import posixpath
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from workers.public_export_bundle import _OFFICE_REL, _REL_NS


def _slide_parts(archive: zipfile.ZipFile) -> list[str]:
    """Return presentation-ordered slide parts without trusting numeric names."""

    presentation = ET.fromstring(archive.read("ppt/presentation.xml"))
    relation_path = "ppt/_rels/presentation.xml.rels"
    relations = {
        item.get("Id"): (
            item.get("Target", "").lstrip("/")
            if item.get("Target", "").startswith("/")
            else posixpath.normpath(posixpath.join("ppt", item.get("Target", "")))
        )
        for item in ET.fromstring(archive.read(relation_path))
        if item.get("TargetMode") != "External"
    }
    return [
        relations[slide.get(_OFFICE_REL + "id")]
        for slide in presentation.findall(".//{http://schemas.openxmlformats.org/presentationml/2006/main}sldId")
    ]


def _slide_rel_path(part: str) -> str:
    folder, name = posixpath.split(part)
    return posixpath.join(folder, "_rels", name + ".rels")


def _replace_hybrid_pages_with_verified_raster(
    *,
    hybrid: Path,
    fidelity: Path,
    slide_indexes: tuple[int, ...],
    scratch: Path,
) -> Path:
    """Replace only refused slide parts with their identity-verified raster base.

    The source raster is admitted before this function is reached.  Healthy
    hybrid slide and package parts are copied byte-for-byte from ``hybrid``;
    each restored image gets a unique media target so it cannot overwrite a
    resource still referenced by a healthy native slide.
    """

    scratch.mkdir(parents=True, exist_ok=True)
    output = scratch / "hybrid-page-fallback.pptx"
    with zipfile.ZipFile(fidelity) as base, zipfile.ZipFile(hybrid) as current:
        if b'Extension="png"' not in base.read("[Content_Types].xml"):
            raise ValueError("hybrid_page_fallback_png_content_type_missing")
        base_presentation = ET.fromstring(base.read("ppt/presentation.xml"))
        current_presentation = ET.fromstring(current.read("ppt/presentation.xml"))
        if (
            base_presentation.find(".//{http://schemas.openxmlformats.org/presentationml/2006/main}sldSz").attrib
            != current_presentation.find(".//{http://schemas.openxmlformats.org/presentationml/2006/main}sldSz").attrib
        ):
            raise ValueError("hybrid_page_fallback_canvas_mismatch")
        base_parts, current_parts = _slide_parts(base), _slide_parts(current)
        if len(base_parts) != len(current_parts) or any(
            index < 0 or index >= len(base_parts) for index in slide_indexes
        ):
            raise ValueError("hybrid_page_fallback_slide_identity_mismatch")
        replacements: dict[str, bytes] = {}
        additions: dict[str, bytes] = {}
        for index in slide_indexes:
            source_part, target_part = base_parts[index], current_parts[index]
            replacements[target_part] = base.read(source_part)
            source_rel, target_rel = _slide_rel_path(source_part), _slide_rel_path(target_part)
            if source_rel not in base.namelist():
                raise ValueError("hybrid_page_fallback_relationship_missing")
            rel_root = ET.fromstring(base.read(source_rel))
            for relation in rel_root.findall(_REL_NS + "Relationship"):
                target = relation.get("Target", "")
                if relation.get("TargetMode") == "External" or not target:
                    continue
                source_resource = (
                    target.lstrip("/")
                    if target.startswith("/")
                    else posixpath.normpath(posixpath.join(posixpath.dirname(source_part), target))
                )
                if not source_resource.startswith("ppt/media/"):
                    if (
                        source_resource not in base.namelist()
                        or source_resource not in current.namelist()
                        or base.read(source_resource) != current.read(source_resource)
                    ):
                        raise ValueError("hybrid_page_fallback_dependency_mismatch")
                    relation.set("Target", posixpath.relpath(source_resource, posixpath.dirname(target_part)))
                    continue
                if source_resource not in base.namelist():
                    raise ValueError("hybrid_page_fallback_image_missing")
                media_name = posixpath.basename(source_resource)
                media_digest = hashlib.sha256(base.read(source_resource)).hexdigest()
                suffix = posixpath.splitext(media_name)[1] or ".png"
                replacement_resource = f"ppt/media/fallback-slide-{index + 1}-{media_digest}{suffix}"
                additions[replacement_resource] = base.read(source_resource)
                relation.set("Target", posixpath.relpath(replacement_resource, posixpath.dirname(target_part)))
            serialized_rel = ET.tostring(rel_root, encoding="utf-8", xml_declaration=True)
            if target_rel in current.namelist():
                replacements[target_rel] = serialized_rel
            else:
                additions[target_rel] = serialized_rel
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as repaired:
            for item in current.infolist():
                if item.filename in replacements:
                    repaired.writestr(item, replacements[item.filename])
                else:
                    repaired.writestr(item, current.read(item.filename))
            for name, payload in additions.items():
                if name not in current.namelist():
                    repaired.writestr(name, payload)
                elif current.read(name) != payload:
                    raise ValueError("hybrid_page_fallback_package_collision")
    sidecar = Path(str(hybrid) + ".local-fit.json")
    if sidecar.is_file():
        evidence = json.loads(sidecar.read_text(encoding="utf-8"))
        if isinstance(evidence, dict) and isinstance(evidence.get("slides"), list):
            refused = {index + 1 for index in slide_indexes}
            evidence["slides"] = [slide for slide in evidence["slides"] if slide.get("slideNumber") not in refused]
            evidence["pptxDigest"] = "sha256:" + hashlib.sha256(output.read_bytes()).hexdigest()
            Path(str(output) + ".local-fit.json").write_text(
                json.dumps(evidence, ensure_ascii=True, separators=(",", ":")), encoding="utf-8"
            )
    return output
