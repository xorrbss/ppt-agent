"""Whether a design pack's rules can project into an svg template snapshot.

A design pack (``services/authored_vnext/design_pack/models.py``) is not a
brand-token catalogue; every ``DesignFrame`` is defined as an executable patch
onto one already-existing document part -- ``EditableSlot.patch_handle`` names
a concrete ``pptx-shape``, ``authored-qid`` or ``scene-element`` selector into
that frame's own ``source_part``, and schema v2 requires every source-native
slot to carry one (``design_pack_source_native_slots_missing`` /
``design_pack_slot_not_executable`` in ``DesignPackStructure``). SVG's
per-slide authoring has no such source document to patch against outside its
own separate source-restyle/raster-reconstruction admission path (already
served by ``utils.svg_template_rules``, unrelated to design packs). That is a
structural property of every design pack the current schema can express, not
a per-instance gap, so this check needs no repository lookup: it names the
missing capability instead of returning a blanket "not supported".
"""
from __future__ import annotations

import base64
import io
import json

from services.authored_vnext.design_pack.registry import RegisteredDesignPack, _confined
from utils.hashing import sha256_hex, sha256_prefixed


def _digest(value: object) -> str:
    return sha256_prefixed(json.dumps(value, sort_keys=True, ensure_ascii=False).encode())


class DesignPackProjectionError(ValueError):
    """A design pack's rules cannot project into an svg template snapshot."""


def assert_design_pack_projects() -> None:
    """Raise with a specific, structural reason. Never returns a rule set today.

    Kept as a function (not a module-level constant) so a future design-pack
    schema revision that adds a geometry-only, patch-free frame kind has one
    place to add the corresponding accept path.
    """

    raise DesignPackProjectionError("svg_design_pack_requires_source_native_patch_target")


def project_design_pack_reference(package: RegisteredDesignPack, width: int, height: int) -> dict:
    """Read identity from a sealed package; source-native patch handles are unused."""
    if not (0 < width <= 4096 and 0 < height <= 4096):
        raise DesignPackProjectionError("svg_design_reference_canvas_unsupported")
    from services.svg_trusted_fonts import bundled_svg_font_family
    from services.svg_contract import validate_raster_data_url

    structure, manifest = package.structure, package.manifest
    palette = [color for color in structure.tokens.colors if color.startswith("#")][:16]
    family = bundled_svg_font_family(",".join(structure.tokens.typography))
    backgrounds = [frame.background.fill for frame in structure.frames if frame.background.fill and frame.background.fill.startswith("#")]
    background = backgrounds[0] if backgrounds else "#FFFFFF"
    accent = next((color for color in palette if color.lower() not in {background.lower(), "#ffffff", "#000000"}), "#2563EB")
    references: dict[str, list[dict]] = {}
    examples, diagnostics = [], []
    inventory = {item.path: item for item in manifest.inventory}
    for render in structure.representative_renders:
        item = inventory.get(render.asset_path)
        if item is None or item.role != "representative-render" or item.sha256 != render.sha256:
            raise DesignPackProjectionError("svg_design_reference_asset_binding_mismatch")
        payload = _confined(package.root.resolve(), render.asset_path).read_bytes()
        if len(payload) != item.size_bytes or sha256_hex(payload) != render.sha256:
            raise DesignPackProjectionError("svg_design_reference_asset_digest_mismatch")
        example = {"role": render.role, "digest": "sha256:" + render.sha256,
                   "evidence_level": render.evidence_level, "semantics": "art-direction"}
        examples.append(example)
        # Structural diagrams show slots, not visual identity. Their evidence
        # stays in provenance and never anchors the model to source topology.
        if render.evidence_level != "source-render" or render.media_type == "image/svg+xml":
            diagnostics.append({"digest": example["digest"], "reason": "reference_visual_not_raster_art_direction"})
            continue
        if len(references.get(render.role, [])) >= 2:
            continue
        if len(payload) > 1536 * 1024:
            raise DesignPackProjectionError("svg_style_reference_size_limit")
        mime = render.media_type
        if mime == "image/webp":
            from PIL import Image
            with Image.open(io.BytesIO(payload)) as image:
                if image.format != "WEBP" or max(image.size) > 8192 or image.width * image.height > 16_777_216:
                    raise DesignPackProjectionError("svg_design_reference_image_invalid")
                converted = io.BytesIO()
                image.save(converted, format="PNG")
                payload, mime = converted.getvalue(), "image/png"
        data_url = f"data:{mime};base64," + base64.b64encode(payload).decode("ascii")
        validate_raster_data_url(data_url)
        references.setdefault(render.role, []).append({"digest": sha256_prefixed(payload), "data_url": data_url})
    result = {
        "version": "svg-design-pack-reference/v1",
        "style_id": manifest.pack_id,
        "composition_mode": "free-svg",
        "reference_semantics": "design-identity",
        "canvas": {"width": width, "height": height},
        "tokens": {"accent": accent, "background": background, "palette": palette},
        "fonts": {"heading": {"family": family}, "body": {"family": family}},
        "identity": {"typography": list(structure.tokens.typography), "colors": palette,
                     "line_styles": list(structure.tokens.line_styles)},
        "references": references, "reference_examples": examples, "diagnostics": diagnostics,
        "composition_guidance": ["Use the source's design identity while freely composing content on the selected canvas."],
        "provenance": {"package_digest": manifest.package_digest, "structure_digest": structure.structure_digest,
                       "source_digest": structure.source_digest, "source_kind": structure.source_kind,
                       "pack_id": manifest.pack_id, "package_version": manifest.package_version,
                       "source_canvas": structure.canvas.model_dump(mode="json", by_alias=True)},
    }
    result["digest"] = _digest(result)
    return result


def resolve_design_pack_reference(*, owner_id: object, pin: object, width: int, height: int) -> dict:
    """Validate an owner-scoped immutable pin without native execution canvas limits."""
    from services.authored_vnext.design_pack.registry import DesignPackRegistry
    from services.authored_vnext.generation_route import design_pack_storage_root, normalize_design_pack_pin

    normalized = normalize_design_pack_pin(pin)
    packages = DesignPackRegistry(design_pack_storage_root(owner_id)).list()
    matches = [package for package in packages
               if package.manifest.pack_id == normalized["designPackId"]
               and int(package.manifest.package_version.split(".", 1)[0]) == normalized["version"]
               and package.manifest.package_digest == normalized["digest"]]
    if len(matches) != 1:
        raise DesignPackProjectionError("design_pack_pin_digest_mismatch")
    result = project_design_pack_reference(matches[0], width, height)
    result["pin"] = normalized
    result["digest"] = _digest({key: value for key, value in result.items() if key != "digest"})
    return result


__all__ = ["DesignPackProjectionError", "assert_design_pack_projects", "project_design_pack_reference", "resolve_design_pack_reference"]
