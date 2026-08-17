"""Canonical bridge from Python DeckMaster/SlideSpec to the OOXML worker."""

from __future__ import annotations

import hashlib
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
from services.authored_vnext.body_grammar import (
    BODY_GRAMMAR_VALUE_KEY,
    build_body_grammar_snapshot,
)


ExportableDeck = DeckSnapshot | DeckGenerationSnapshot
RESOLVED_SNAPSHOT_EVIDENCE_VALUE_KEY = "__resolvedSnapshotEvidence"


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
    layouts = {
        contract.layouts.cover.id: contract.layouts.cover,
        contract.layouts.section.id: contract.layouts.section,
        contract.layouts.standard.id: contract.layouts.standard,
        contract.layouts.long_title.id: contract.layouts.long_title,
    }
    result: list[dict[str, Any]] = []
    for page_index, item in enumerate(deck.slides, start=1):
        slide: SlideSpecV2 = item.slide if isinstance(deck, DeckGenerationSnapshot) else item
        layout = layouts.get(slide.layout_id)
        if layout is None:
            raise ValueError("authored_vnext_slide_layout_not_in_master")
        values: dict[str, str] = {}
        for slot in layout.slots:
            role = slot.id.removeprefix("slot:")
            if slot.role == "title":
                values[role] = "\n".join(slide.title.lines)
            elif slot.role in {"body", "content"}:
                values[role] = slide.body
            else:
                raise ValueError("authored_vnext_slide_slot_role_unsupported")
        body_grammar = build_body_grammar_snapshot(contract, slide)
        if body_grammar is not None:
            values[BODY_GRAMMAR_VALUE_KEY] = _canonical_json(body_grammar)
            if body_grammar.get("schemaVersion") == "authored-body-grammar/v3":
                composition = (
                    deck.composition
                    if isinstance(deck, DeckGenerationSnapshot)
                    else None
                )
                template = (
                    composition.authored_template
                    if composition is not None
                    else None
                )
                policy = (
                    composition.consistency_policy
                    if composition is not None
                    else None
                )
                resolved = (
                    template.resolved_reference_layout
                    if template is not None
                    else None
                )
                if (
                    composition is None
                    or template is None
                    or policy is None
                    or resolved is None
                    or template.resolved_reference_layout_digest != resolved.digest
                    or body_grammar.get("referenceLayoutDigest") != resolved.digest
                    or body_grammar.get("referenceSourceDigest")
                    != resolved.source_digest
                    or body_grammar.get("digest") is None
                ):
                    raise ValueError(
                        "authored_vnext_resolved_reference_layout_mismatch"
                    )
                semantic_roles = body_grammar.get("semanticRolesUsed")
                if not isinstance(semantic_roles, list) or not all(
                    isinstance(role, str) and role
                    for role in semantic_roles
                ):
                    raise ValueError(
                        "authored_vnext_resolved_reference_layout_roles_invalid"
                    )
                layout_roles = [
                    str(body_grammar[key])
                    for key in ("referenceLayoutId", "archetype", "slideRole")
                    if isinstance(body_grammar.get(key), str)
                ]
                evidence = {
                    "schemaVersion": "resolved-snapshot-evidence/v1",
                    "resolvedTemplateId": template.retained_reference_id,
                    "resolvedReferenceLayoutDigest": resolved.digest,
                    "resolvedCompositionDigest": composition.digest,
                    "resolvedRuleDigest": policy.digest,
                    "pageNumber": page_index,
                    "slideCount": len(deck.slides),
                    "semanticRolesUsed": list(dict.fromkeys(semantic_roles)),
                    "layoutRolesUsed": list(dict.fromkeys(layout_roles)),
                    "bodyGrammarDigest": body_grammar["digest"],
                }
                values[RESOLVED_SNAPSHOT_EVIDENCE_VALUE_KEY] = _canonical_json(
                    evidence
                )
        result.append(
            {
                "slideId": slide.slide_id,
                "layoutId": slide.layout_id,
                "values": values,
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
    supplied = {asset.get("assetId"): asset for asset in assets}
    if len(supplied) != len(assets) or set(supplied) != set(references):
        raise ValueError("authored_vnext_master_asset_bytes_unavailable")
    for asset_id, digest in references.items():
        asset = supplied[asset_id]
        if (
            asset.get("mediaType") != "image/png"
            or f"sha256:{asset.get('sha256', '')}" != digest
            or not isinstance(asset.get("bytesBase64"), str)
        ):
            raise ValueError("authored_vnext_master_asset_digest_mismatch")
    raw_asset_digests = {
        asset_id: digest.removeprefix("sha256:")
        for asset_id, digest in references.items()
    }
    composition = (
        deck.composition
        if isinstance(deck, DeckGenerationSnapshot)
        else None
    )
    mapped_master = worker_master_contract(
        master,
        raw_asset_digests,
        composition.authored_template if composition is not None else None,
    )
    slides = worker_slides(deck, master)
    asset_manifest = [
        {
            "assetId": asset["assetId"],
            "mediaType": asset["mediaType"],
            "sha256": asset["sha256"],
        }
        for asset in assets
    ]
    digests = {
        "master": f"sha256:{raw_sha256(mapped_master)}",
        "spec": f"sha256:{raw_sha256(slides)}",
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
