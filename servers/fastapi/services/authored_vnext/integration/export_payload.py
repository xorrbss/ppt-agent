"""Canonical bridge from authored Scene IR to the native OOXML worker."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from models.authored_vnext.master.contract import (
    DeckMasterContract,
    FurnitureElement,
    Ownership,
    Rect,
)
from models.authored_vnext.deck.operations import DeckGenerationSnapshot
from models.authored_vnext.master.slide import DeckSnapshot, SlideSpecV2
from services.authored_vnext.master.digests import canonical_json as _canonical_json


ExportableDeck = DeckSnapshot | DeckGenerationSnapshot
SCENE_SCHEMA_VERSION = "deck-scene-ir/v1"


def _model_payload(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        # Scene digests include explicit nulls from the sealed Pydantic wire
        # model, so export must not silently normalize them away.
        payload = value.model_dump(mode="json", by_alias=True)
    elif isinstance(value, dict):
        payload = dict(value)
    else:
        raise ValueError("authored_vnext_scene_required")
    if not isinstance(payload, dict):
        raise ValueError("authored_vnext_scene_required")
    return payload


def worker_scene(deck: ExportableDeck) -> dict[str, Any]:
    """Return the digest-sealed canonical Scene; never synthesize title/body."""

    scene_value = getattr(deck, "scene", None)
    if scene_value is None:
        raise ValueError("authored_vnext_scene_required")
    scene = _model_payload(scene_value)
    if scene.get("schemaVersion") != SCENE_SCHEMA_VERSION:
        raise ValueError("authored_vnext_scene_schema_unsupported")
    scene_digest = scene.get("sceneDigest") or scene.get("digest")
    if not isinstance(scene_digest, str) or not scene_digest.startswith("sha256:"):
        raise ValueError("authored_vnext_scene_digest_invalid")
    digest_payload = dict(scene)
    digest_payload.pop("sceneDigest", None)
    digest_payload.pop("digest", None)
    if prefixed_sha256(digest_payload) != scene_digest:
        raise ValueError("authored_vnext_scene_digest_mismatch")
    if scene.get("deckId") not in {None, deck.deck_id}:
        raise ValueError("authored_vnext_scene_deck_identity_mismatch")
    slides = scene.get("slides")
    if not isinstance(slides, list) or not slides:
        raise ValueError("authored_vnext_scene_slides_invalid")
    scene_slide_ids = [
        item.get("slideId") if isinstance(item, dict) else None for item in slides
    ]
    deck_slide_ids = [
        (item.slide if isinstance(deck, DeckGenerationSnapshot) else item).slide_id
        for item in deck.slides
    ]
    if scene_slide_ids != deck_slide_ids:
        raise ValueError("authored_vnext_scene_slide_identity_mismatch")
    composition = getattr(deck, "composition", None)
    composition_digest = getattr(composition, "digest", None)
    scene_composition_digest = scene.get("compositionDigest")
    if composition is not None and scene_composition_digest != composition_digest:
        raise ValueError("authored_vnext_scene_composition_mismatch")
    if composition is not None:
        scene["compositionPin"] = composition.model_dump(
            mode="json", by_alias=True, exclude_none=True
        )
    scene["sceneDigest"] = scene_digest
    return scene


def scene_asset_references(deck: ExportableDeck) -> dict[str, dict[str, Any]]:
    """Collect image references and optional inline bytes from canonical Scene IR."""

    scene = worker_scene(deck)
    declared: dict[str, dict[str, Any]] = {}
    for value in scene.get("assetManifest", scene.get("assets", [])):
        if not isinstance(value, dict):
            raise ValueError("authored_vnext_scene_assets_invalid")
        asset_id = value.get("assetId")
        digest = value.get("assetDigest") or value.get("digest")
        media_type = value.get("mediaType", "image/png")
        size_bytes = value.get("sizeBytes")
        if (
            not isinstance(asset_id, str)
            or not isinstance(digest, str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None
            or media_type not in {"image/png", "image/jpeg"}
            or isinstance(size_bytes, bool)
            or not isinstance(size_bytes, int)
            or size_bytes < 1
            or size_bytes > 100_000_000
            or asset_id in declared
        ):
            raise ValueError("authored_vnext_scene_assets_invalid")
        declared[asset_id] = {
            "assetId": asset_id,
            "digest": digest,
            "mediaType": media_type,
            "sizeBytes": size_bytes,
            **(
                {"storageReference": value["storageReference"]}
                if isinstance(value.get("storageReference"), str)
                else {}
            ),
            **(
                {"bytesBase64": value["bytesBase64"]}
                if isinstance(value.get("bytesBase64"), str)
                else {}
            ),
        }
    referenced: dict[str, dict[str, Any]] = {}
    for slide in scene["slides"]:
        elements = slide.get("elements") if isinstance(slide, dict) else None
        if not isinstance(elements, list):
            raise ValueError("authored_vnext_scene_elements_invalid")
        for element in elements:
            if not isinstance(element, dict):
                raise ValueError("authored_vnext_scene_element_invalid")
            kind = element.get("kind") or element.get("type")
            if kind != "image":
                continue
            asset_id = element.get("assetId")
            digest = element.get("assetDigest")
            if not isinstance(asset_id, str) or not isinstance(digest, str):
                raise ValueError("authored_vnext_scene_image_asset_invalid")
            declaration = declared.get(asset_id)
            if declaration is None or declaration["digest"] != digest:
                raise ValueError("authored_vnext_scene_image_asset_unavailable")
            previous = referenced.get(asset_id)
            if previous is not None and previous["digest"] != digest:
                raise ValueError("authored_vnext_scene_asset_digest_mismatch")
            referenced[asset_id] = declaration
        hybrid_export = slide.get("hybridExport")
        if hybrid_export is None:
            continue
        if not isinstance(hybrid_export, dict) or not isinstance(
            hybrid_export.get("rasterIslands"), list
        ):
            raise ValueError("authored_vnext_hybrid_export_plan_invalid")
        for island in hybrid_export["rasterIslands"]:
            if not isinstance(island, dict):
                raise ValueError("authored_vnext_hybrid_raster_island_invalid")
            asset_id = island.get("assetId")
            digest = island.get("assetDigest")
            if not isinstance(asset_id, str) or not isinstance(digest, str):
                raise ValueError("authored_vnext_hybrid_raster_island_invalid")
            declaration = declared.get(asset_id)
            if declaration is None or declaration["digest"] != digest:
                raise ValueError("authored_vnext_hybrid_raster_island_asset_mismatch")
            previous = referenced.get(asset_id)
            if previous is not None and previous["digest"] != digest:
                raise ValueError("authored_vnext_scene_asset_digest_mismatch")
            referenced[asset_id] = declaration
    return referenced


def semantic_asset_references(contract: DeckMasterContract) -> dict[str, str]:
    """Return digest-pinned WYSIWYG image assets without changing legacy brand bindings."""
    references: dict[str, str] = {}
    for element in contract.semantic_elements:
        if element.type != "image" or element.content.asset_id is None or element.content.digest is None:
            continue
        previous = references.get(element.content.asset_id)
        if previous is not None and previous != element.content.digest:
            raise ValueError("authored_vnext_master_asset_reference_mismatch")
        references[element.content.asset_id] = element.content.digest
    return references


def canonical_json(value: object) -> str:
    return _canonical_json(value)


def raw_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def prefixed_sha256(value: object) -> str:
    return f"sha256:{raw_sha256(value)}"


def _ownership(value: Ownership) -> dict[str, bool]:
    return {
        "locked": value.locked,
        "modelWritable": value.model_writable,
    }


def _bounds(value: Rect) -> dict[str, float]:
    return {
        "x": float(value.x),
        "y": float(value.y),
        "width": float(value.width),
        "height": float(value.height),
    }


def _furniture(
    value: FurnitureElement,
    asset_digests: dict[str, str],
) -> dict[str, Any] | None:
    result: dict[str, Any] = {
        "id": value.id,
        "kind": value.kind,
        "bounds": _bounds(value.bounds),
        "ownership": _ownership(value.ownership),
        "visibility": value.visibility,
    }
    if value.text is not None:
        result["text"] = value.text
    if value.fill is not None:
        result["fill"] = value.fill
    if value.stroke is not None:
        result["stroke"] = value.stroke
    if value.asset_id is not None:
        result["assetId"] = value.asset_id
        result["assetSha256"] = asset_digests[value.asset_id]
    return result


def worker_master_contract(
    contract: DeckMasterContract,
    asset_digests: dict[str, str] | None = None,
    authored_template: Any | None = None,
) -> dict[str, Any]:
    asset_digests = asset_digests or {}
    colors = contract.tokens.colors
    theme_colors = {
        "dk1": colors.text_primary,
        "lt1": colors.background,
        "dk2": colors.text_secondary,
        "lt2": colors.surface,
        "accent1": colors.accent,
        "accent2": colors.divider,
        "accent3": colors.warning,
        "accent4": colors.accent,
        "accent5": colors.text_secondary,
        "accent6": colors.divider,
        "hlink": colors.accent,
        "folHlink": colors.warning,
    }
    resolved = (
        getattr(authored_template, "resolved_reference_layout", None)
        if authored_template is not None
        else None
    )
    template_fonts: tuple[str, str] | None = None
    if resolved is not None:
        content_layout = next(
            (item for item in resolved.layouts if item.get("archetype") == "content"),
            resolved.layouts[0],
        )
        title_font = str(
            content_layout.get("title", {}).get("textStyle", {}).get("fontFamily")
            or "Noto Sans KR"
        )
        body_font = str(
            next(
                (
                    block.get("textStyle", {}).get("fontFamily")
                    for block in content_layout.get("blocks", [])
                    if block.get("textStyle", {}).get("fontFamily")
                ),
                title_font,
            )
        )
        template_fonts = (title_font, body_font)
    primary_font = template_fonts[1] if template_fonts else contract.font_manifest.primary
    fallback_fonts = (
        list(dict.fromkeys((template_fonts[1], template_fonts[0])))
        if template_fonts
        else list(contract.font_manifest.fallbacks)
    )
    if template_fonts:
        theme_scripts = {
            "latin": primary_font,
            "eastAsian": primary_font,
            "complexScript": primary_font,
            "Hang": primary_font,
            "Jpan": primary_font,
            "Hans": primary_font,
            "Hant": primary_font,
        }
    else:
        scripts = contract.font_manifest.theme_scripts
        theme_scripts = {
            "latin": scripts.latin,
            "eastAsian": scripts.hang,
            "complexScript": scripts.latin,
            "Hang": scripts.hang,
            "Jpan": scripts.jpan,
            "Hans": scripts.hans,
            "Hant": scripts.hant,
        }
    font_manifest: dict[str, Any] = {
        "primary": primary_font,
        "fallbacks": fallback_fonts,
        "windowsFallback": primary_font,
        "themeScripts": theme_scripts,
        "files": [] if template_fonts else [
            {
                "style": file.style,
                "path": file.path,
                "version": file.version,
                "sizeBytes": file.size_bytes,
                "sha256": file.sha256,
            }
            for file in contract.font_manifest.files
        ],
    }
    font_manifest["digest"] = (
        prefixed_sha256(font_manifest)
        if template_fonts
        else contract.font_manifest.digest
    )
    furniture = [
        mapped
        for mapped in (
            _furniture(contract.furniture.logo, asset_digests),
            _furniture(contract.furniture.header, asset_digests),
            _furniture(contract.furniture.footer, asset_digests),
            _furniture(contract.furniture.divider, asset_digests),
            _furniture(contract.furniture.page_number, asset_digests),
        )
        if mapped is not None
    ]
    layouts = []
    for layout in (
        contract.layouts.cover,
        contract.layouts.section,
        contract.layouts.standard,
        contract.layouts.long_title,
    ):
        placeholders = []
        for slot in layout.slots:
            style = getattr(
                contract.semantic_styles,
                slot.style.replace("rightCallout", "right_callout"),
            )
            margins = style.margins
            max_lines = style.max_lines
            if slot.role == "title" and layout.kind == "standard":
                max_lines = min(max_lines, 2)
            elif slot.role == "title" and layout.kind == "longTitle":
                max_lines = max(max_lines, 3)
            placeholders.append(
                {
                    "id": slot.id.replace("slot:", "placeholder:", 1),
                    "type": slot.role,
                    "index": slot.placeholder_index,
                    "bounds": _bounds(slot.bounds),
                    "ownership": _ownership(slot.ownership),
                    "textStyle": {
                        "fontPt": style.font_pt,
                        "lineHeight": style.line_height,
                        "maxLines": max_lines,
                        "marginsPx": {
                            "top": margins.top_pt * (4 / 3),
                            "right": margins.right_pt * (4 / 3),
                            "bottom": margins.bottom_pt * (4 / 3),
                            "left": margins.left_pt * (4 / 3),
                        },
                    },
                }
            )
        layouts.append(
            {
                "id": layout.id,
                "name": layout.name,
                "kind": {
                    "cover": "cover",
                    "section": "section",
                    "standard": "standard",
                    "longTitle": "long-title",
                }[layout.kind],
                "placeholders": placeholders,
            }
        )
    result = {
        "contractVersion": "deckmaster-contract/v1",
        "id": contract.master_id,
        "name": contract.name,
        "slideSizePx": {
            "x": 0,
            "y": 0,
            "width": contract.canvas.width_px,
            "height": contract.canvas.height_px,
        },
        "theme": {"name": contract.name, "colors": theme_colors},
        "tokens": {
            "color.background": colors.background,
            "color.text.primary": colors.text_primary,
            "color.text.secondary": colors.text_secondary,
            "font.body": template_fonts[1] if template_fonts else contract.font_manifest.primary,
            "font.title": template_fonts[0] if template_fonts else contract.font_manifest.primary,
        },
        "fontManifest": font_manifest,
        "furniture": furniture,
        "layouts": layouts,
        "digest": contract.digest,
    }
    if contract.semantic_elements or contract.custom_layouts:
        result["authoredManifest"] = contract.model_dump(
            mode="json", by_alias=True, exclude_none=True
        )
    return result


def worker_slides(
    deck: ExportableDeck, contract: DeckMasterContract
) -> list[dict[str, Any]]:
    del contract  # Scene owns authored body layout; master is furniture-only.
    scene = worker_scene(deck)
    result: list[dict[str, Any]] = []
    for scene_slide, item in zip(scene["slides"], deck.slides, strict=True):
        slide: SlideSpecV2 = (
            item.slide if isinstance(deck, DeckGenerationSnapshot) else item
        )
        layout_id = scene_slide.get("layoutId") or slide.layout_id
        if layout_id != slide.layout_id:
            raise ValueError("authored_vnext_scene_layout_identity_mismatch")
        result.append(
            {
                "slideId": scene_slide["slideId"],
                "layoutId": layout_id,
                # Content is never projected back to synthetic title/body values.
                # This list remains only as the durable worker/publication index.
                "values": {},
            }
        )
    return result


def build_worker_request(
    *,
    operation_id: str,
    fence_token: int,
    master: DeckMasterContract,
    deck: ExportableDeck,
    expected_package_digest: str | None,
    assets: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    assets = list(assets or [])
    configured_brand_assets = [
        asset
        for asset in (master.brand_assets.logo, master.brand_assets.wordmark)
        if asset is not None
    ]
    furniture_asset_ids = [
        element.asset_id
        for element in (
            master.furniture.logo,
            master.furniture.header,
            master.furniture.footer,
            master.furniture.divider,
            master.furniture.page_number,
        )
        if element.asset_id is not None
    ]
    brand_references = {asset.asset_id: asset.digest for asset in configured_brand_assets}
    if set(furniture_asset_ids) != set(brand_references):
        raise ValueError("authored_vnext_master_asset_reference_mismatch")
    references = dict(brand_references)
    for asset_id, digest in semantic_asset_references(master).items():
        if asset_id in references and references[asset_id] != digest:
            raise ValueError("authored_vnext_master_asset_reference_mismatch")
        references[asset_id] = digest
    scene_references = scene_asset_references(deck)
    for asset_id, reference in scene_references.items():
        digest = reference["digest"]
        if asset_id in references and references[asset_id] != digest:
            raise ValueError("authored_vnext_scene_asset_digest_mismatch")
        references[asset_id] = digest
    supplied = {asset.get("assetId"): asset for asset in assets}
    if len(supplied) != len(assets) or set(supplied) != set(references):
        raise ValueError("authored_vnext_master_asset_bytes_unavailable")
    for asset_id, digest in references.items():
        asset = supplied[asset_id]
        expected_media_type = (
            scene_references[asset_id]["mediaType"]
            if asset_id in scene_references
            else "image/png"
        )
        if (
            asset.get("mediaType") != expected_media_type
            or f"sha256:{asset.get('sha256', '')}" != digest
            or asset.get("sizeBytes")
            != (
                scene_references[asset_id]["sizeBytes"]
                if asset_id in scene_references
                else asset.get("sizeBytes")
            )
            or not isinstance(asset.get("bytesBase64"), str)
        ):
            raise ValueError("authored_vnext_scene_asset_digest_mismatch")
    raw_asset_digests = {
        asset_id: digest.removeprefix("sha256:")
        for asset_id, digest in references.items()
    }
    composition = getattr(deck, "composition", None)
    mapped_master = worker_master_contract(
        master,
        raw_asset_digests,
        composition.authored_template if composition is not None else None,
    )
    slides = worker_slides(deck, master)
    scene = worker_scene(deck)
    asset_manifest = [
        {
            "assetId": asset["assetId"],
            "mediaType": asset["mediaType"],
            "sha256": asset["sha256"],
            "sizeBytes": asset["sizeBytes"],
        }
        for asset in assets
    ]
    digests = {
        "master": f"sha256:{raw_sha256(mapped_master)}",
        # Scene is the canonical authored spec.  Worker slides are only an
        # ordered publication index and must never regain title/body ownership.
        "spec": scene["sceneDigest"],
        "assetManifest": f"sha256:{raw_sha256(asset_manifest)}",
        "fontManifest": f"sha256:{raw_sha256(mapped_master['fontManifest'])}",
    }
    payload = {
        "schemaVersion": "authored-vnext-ooxml-worker/v1",
        "operation": "EXPORT_PPTX",
        "operationId": operation_id,
        "fencingToken": fence_token,
        "master": mapped_master,
        "slides": slides,
        "scene": scene,
        "assets": assets,
        "digests": {
            "masterSha256": digests["master"].removeprefix("sha256:"),
            "specSha256": digests["spec"].removeprefix("sha256:"),
            "assetManifestSha256": digests["assetManifest"].removeprefix("sha256:"),
            "fontManifestSha256": digests["fontManifest"].removeprefix("sha256:"),
            # The public expected package digest fences the adapter's package
            # graph receipt. Worker packageSha256 instead means exact final
            # archive bytes, which are unknown before a first export.
            "packageSha256": None,
        },
    }
    return payload, digests
