"""Template-frame branch of the existing system-furniture attestation gate."""
from __future__ import annotations

from copy import deepcopy
import xml.etree.ElementTree as ET

from lxml import etree

from services.svg_templates.frame_composition import BACKGROUND_ID, FOREGROUND_ID, render_frame_groups, trusted_frame_images
from services.svg_templates.frame_fonts import artifact_digest


def _structure(node):
    return (node.tag, sorted(node.attrib.items()), node.text or "", tuple(_structure(child) for child in node))


def template_furniture_receipt(svg, snapshot, *, width, height, index, count, page_context):
    from services.svg_contract import source_digest
    from services.svg_templates.generation import page_frame
    from utils.hashing import canonical_json_digest
    if not page_context:
        raise ValueError("svg_template_page_context_missing")
    from services.svg_templates.chapters import chapter_from_context
    frame = page_frame(snapshot, index, count, role=page_context.get("role"),
                       chapter=chapter_from_context(page_context))
    if (page_context.get("frame_id") != frame.frame_id
            or page_context.get("frame_digest") != frame.digest
            or page_context.get("page_index") != index or page_context.get("page_count") != count
            or page_context.get("digest") != canonical_json_digest({k: v for k, v in page_context.items() if k != "digest"})
            or frame.source_canvas.width_px != width or frame.source_canvas.height_px != height):
        raise ValueError("svg_template_page_context_mismatch")
    expected = [ET.fromstring(etree.tostring(group)) for group in render_frame_groups(
        frame, dynamic_values=page_context.get("dynamic_values", {}))]
    root = ET.fromstring(svg)
    groups = []
    for candidate in expected:
        matches = [node for node in root.iter() if node.get("id") == candidate.get("id")]
        if len(matches) != 1 or matches[0] not in list(root) or _structure(matches[0]) != _structure(candidate):
            raise ValueError("svg_template_furniture_mismatch")
        groups.append(matches[0])
    binding = {"schema_version": "svg-template-furniture/v1", "frame_digest": frame.digest,
               "font_manifest_digest": frame.font_manifest.digest,
               "page_context": deepcopy(page_context),
               "groups": [ET.tostring(group, encoding="unicode") for group in expected]}
    if frame.schema_version in {"svg-template-frame/v2", "svg-template-frame/v3"}:
        from services.svg_templates.frame_native_text import native_frame_mapping, validate_mapping
        mapping = native_frame_mapping(frame)
        validate_mapping(root, mapping, frame.source_canvas, frame.font_manifest)
        binding.update(schema_version="svg-template-furniture/v2", compiler_version="ooxml-frame/2", native_fixed_text=mapping)
        if frame.schema_version.endswith("/v3"):
            from services.svg_templates.frame_glyphs import validate_frame_glyph_authority
            authority = validate_frame_glyph_authority(frame)
            binding.update(schema_version="svg-template-furniture/v3", compiler_version="ooxml-frame/3",
                           fixed_glyph_preview_digest=authority.authority_digest)
    binding["binding_digest"] = artifact_digest(binding)
    return {"version": "svg-system-furniture/v1", "svg_digest": source_digest(svg),
            "canvas": {"width": width, "height": height}, "digest": frame.digest,
            "master_digest": None, "generated_template_frame": binding,
            "element_ids": [node.get("id") for group in groups for node in group.iter()],
            "images": trusted_frame_images(frame)}


def validate_template_furniture_binding(root, receipt):
    binding = receipt.get("generated_template_frame", {})
    if (binding.get("schema_version") not in {"svg-template-furniture/v1", "svg-template-furniture/v2", "svg-template-furniture/v3"}
            or binding.get("frame_digest") != receipt.get("digest")
            or artifact_digest({k: v for k, v in binding.items() if k != "binding_digest"}) != binding.get("binding_digest")
            or not binding.get("page_context") or len(binding.get("groups", [])) != 2):
        raise ValueError("svg_template_furniture_binding_mismatch")
    expected_ids = [BACKGROUND_ID, FOREGROUND_ID]
    for xml, group_id in zip(binding["groups"], expected_ids, strict=True):
        from services.source_content.security import secure_xml
        expected = secure_xml(xml.encode(), part_name="template-furniture.svg")
        matches = [node for node in root.iter() if node.get("id") == group_id]
        if (expected.get("id") != group_id or len(matches) != 1 or matches[0] not in list(root)
                or _structure(matches[0]) != _structure(expected)):
            raise ValueError("svg_template_furniture_binding_mismatch")
    owned_text_ids = {node.get("id") for xml in binding["groups"] for node in ET.fromstring(xml).iter()
                      if node.tag == "{http://www.w3.org/2000/svg}text"}
    mapping = binding.get("native_fixed_text")
    if binding["schema_version"].endswith(("/v2", "/v3")):
        from models.svg_template_frame import SvgTemplateNativeFixedText
        from services.svg_templates.frame_native_text import _structure as native_structure
        version = binding["schema_version"].rsplit("/v", 1)[1]
        if (binding.get("compiler_version") != "ooxml-frame/" + version
                or not isinstance(mapping, dict) or not mapping or set(mapping) != owned_text_ids):
            raise ValueError("svg_template_native_layout_binding_mismatch")
        for identity, value in mapping.items():
            record = SvgTemplateNativeFixedText.model_validate(value)
            matches = [node for node in root.iter() if node.get("id") == identity]
            if (record.svg_text_id != identity or artifact_digest(record) != record.digest or len(matches) != 1
                    or artifact_digest(native_structure(matches[0])) != record.svg_text_digest):
                raise ValueError("svg_template_native_layout_binding_mismatch")
        glyph_digest = binding.get("fixed_glyph_preview_digest")
        if version == "3":
            import re
            if not isinstance(glyph_digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", glyph_digest):
                raise ValueError("svg_template_glyph_binding_mismatch")
        elif glyph_digest is not None:
            raise ValueError("svg_template_glyph_version_mismatch")
    elif mapping is not None:
        raise ValueError("svg_template_native_layout_version_mismatch")
    children = list(root)
    body_indices = [i for i, node in enumerate(children) if node.get("id") == "svg-system-template-body"]
    if (len(body_indices) != 1 or len(children) != 3 or children[0].get("id") != BACKGROUND_ID
            or body_indices[0] != 1 or children[2].get("id") != FOREGROUND_ID):
        raise ValueError("svg_template_furniture_layer_order_invalid")
    return expected_ids


def template_image_manifest(snapshot):
    from models.svg_template_frame import SvgTemplateFrameAdaptation
    from models.svg_template_codec import unpack_adaptation_fonts
    result = {}
    selection = (snapshot or {}).get("svg_template", {})
    if selection.get("mode") != "template-redesign":
        return result
    from services.svg_templates.selection_material import template_adaptations, template_package
    package = template_package(selection)
    for value in template_adaptations(selection).values():
        adaptation = SvgTemplateFrameAdaptation.model_validate(unpack_adaptation_fonts(value, package))
        if artifact_digest(adaptation) != adaptation.digest:
            raise ValueError("svg_template_adaptation_digest_mismatch")
        result.update(trusted_frame_images(adaptation.frame))
    return result
