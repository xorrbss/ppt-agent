"""Durable export orchestration across SQL, the OOXML worker, and artifact storage."""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import json
import re
from typing import Any
import uuid

from services.authored_vnext.integration.artifact_store import (
    ArtifactStoreError,
    BufferedArtifactStore,
)
from services.authored_vnext.integration.export_payload import (
    build_worker_request,
    prefixed_sha256,
    scene_asset_references,
    semantic_asset_references,
)
from services.authored_vnext.integration.sql_repository import (
    AuthoredVNextSqlRepository,
    ExportArtifactInput,
    OperationFenceError,
    PublicationConflictError,
    SourceDigestMismatchError,
)
from services.authored_vnext.integration.worker import OoxmlWorkerTransport, WorkerError
from services.authored_vnext.master.assets import MasterAssetError, OwnerMasterAssetStore
from services.authored_vnext.generation.ai_text_zones import (
    ResolvedAITextZoneSet,
    materialize_ai_text_zone_contract,
)
from services.authored_vnext.generation.effective_style import (
    materialize_authored_composition,
)


class ExportServiceError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


_HEX_COLOR = re.compile(r"^#?([0-9a-fA-F]{6})$")
_BACKGROUND_FURNITURE_ID = "company-standard:background"


def _hex_color(value: object, default: str) -> str:
    match = _HEX_COLOR.fullmatch(value) if isinstance(value, str) else None
    return f"#{(match.group(1) if match is not None else default).upper()}"


def _master_background_identity(master: dict[str, Any]) -> dict[str, Any]:
    tokens = master.get("tokens")
    if not isinstance(tokens, dict):
        raise ExportServiceError("authored_vnext_master_background_invalid")
    value = tokens.get("color.background")
    if not isinstance(value, str) or _HEX_COLOR.fullmatch(value) is None:
        raise ExportServiceError("authored_vnext_master_background_invalid")
    color = _hex_color(value, "FFFFFF")
    return {
        "furnitureId": _BACKGROUND_FURNITURE_ID,
        "kind": "background",
        "fill": color,
        "styleDigest": prefixed_sha256({"fill": color}),
    }


def _relative_luminance(color: str) -> float:
    match = _HEX_COLOR.fullmatch(color)
    if match is None:
        raise ExportServiceError("authored_vnext_master_furniture_color_invalid")
    hexadecimal = match.group(1)
    channels = [
        int(hexadecimal[index:index + 2], 16) / 255 for index in (0, 2, 4)
    ]
    linear = [
        value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
        for value in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast_ratio(foreground: str, background: str) -> float:
    left = _relative_luminance(foreground)
    right = _relative_luminance(background)
    lighter, darker = max(left, right), min(left, right)
    return (lighter + 0.05) / (darker + 0.05)


def _expected_furniture_text_style(
    item: dict[str, Any], master: dict[str, Any], background_color: str
) -> dict[str, Any]:
    requested = _hex_color(item.get("fill") or item.get("stroke"), "111827")
    tokens = master.get("tokens", {})
    tokens = tokens if isinstance(tokens, dict) else {}
    candidates = [
        requested,
        *(
            _hex_color(tokens[name], "111827")
            for name in (
                "color.text.primary",
                "color.text.secondary",
                "color.accent",
                "color.warning",
                "color.background",
            )
            if name in tokens
        ),
    ]
    effective = next(
        (
            candidate
            for candidate in dict.fromkeys(candidates)
            if _contrast_ratio(candidate, background_color) >= 4.5
        ),
        None,
    )
    if effective is None:
        raise ExportServiceError(
            "authored_vnext_master_furniture_contrast_unsupported"
        )
    font_manifest = master.get("fontManifest", {})
    font_manifest = font_manifest if isinstance(font_manifest, dict) else {}
    font_family = tokens.get("font.body") or font_manifest.get("primary") or "Aptos"
    if not isinstance(font_family, str) or not font_family:
        raise ExportServiceError("authored_vnext_master_furniture_font_invalid")
    return {
        "requestedColor": requested,
        "effectiveColor": effective,
        "fontFamily": font_family,
        "contrastRatio": round(_contrast_ratio(effective, background_color), 4),
        "contrastAdjusted": effective != requested,
        "styleDigest": prefixed_sha256(
            {"fontColor": effective, "fontFamily": font_family}
        ),
    }


def _assert_worker_readback(
    receipt: dict[str, Any], payload: dict[str, Any]
) -> None:
    """Reject a syntactically successful worker response with any silent loss."""
    package = receipt.get("package")
    preservation = receipt.get("preservation")
    master = receipt.get("master")
    if (
        receipt.get("validation") != "candidate-reopened"
        or receipt.get("candidateDigestVerified") is not True
        or not isinstance(package, dict)
        or not isinstance(preservation, dict)
        or not isinstance(master, dict)
        or not isinstance(receipt.get("slides"), list)
    ):
        raise ExportServiceError("authored_vnext_worker_read_back_invalid")
    furniture = payload["master"].get("furniture", [])
    font_files = payload["master"].get("fontManifest", {}).get("files", [])
    expected_master = {
        "furnitureCount": len(furniture),
        "logoCount": sum(item.get("kind") == "logo" for item in furniture),
        "pageNumberCount": sum(
            item.get("kind") == "pageNumber" for item in furniture
        ),
        "fontFileCount": len(font_files),
        "duplicateFurnitureCount": 0,
        "fontFilesMode": "REFERENCE_METADATA_VERIFIED",
    }
    if any(master.get(key) != value for key, value in expected_master.items()):
        raise ExportServiceError("authored_vnext_worker_master_read_back_mismatch")
    if (
        preservation.get("silentLossCount") != 0
        or preservation.get("missingParts") != []
        or preservation.get("unexpectedChangedParts") != []
        or package.get("macroDetected") is not False
        or package.get("quarantineReasons") != []
        or package.get("slideCount") != len(payload["slides"])
        or len(receipt["slides"]) != len(payload["slides"])
    ):
        raise ExportServiceError("authored_vnext_worker_preservation_mismatch")
    scene = payload.get("scene")
    actual_scene = receipt.get("scene")
    capabilities = receipt.get("capabilities")
    native_counts = receipt.get("nativeObjectCounts")
    hybrid_ownership = receipt.get("hybridOwnership")
    if not isinstance(scene, dict) or not isinstance(actual_scene, dict):
        raise ExportServiceError("authored_vnext_scene_read_back_invalid")
    expected_scene_slides: list[dict[str, Any]] = []
    expected_counts = {"chart": 0, "image": 0, "shape": 0, "table": 0, "text": 0}
    hybrid_profile_id = scene.get("hybridProfileId")
    hybrid_mode = hybrid_profile_id == "authored-editable-hybrid-ko-en-basic-v1"
    if hybrid_profile_id is not None and not hybrid_mode:
        raise ExportServiceError("authored_vnext_hybrid_profile_mismatch")
    expected_raster_island_count = 0
    expected_raster_owner_count = 0
    data_manifest = {
        item.get("dataId"): item
        for item in scene.get("dataManifest", [])
        if isinstance(item, dict) and isinstance(item.get("dataId"), str)
    }
    furniture = payload["master"].get("furniture", [])
    background_furniture = _master_background_identity(payload["master"])
    background_color = background_furniture["fill"]
    for page_number, slide in enumerate(scene.get("slides", []), start=1):
        if not isinstance(slide, dict) or not isinstance(slide.get("elements"), list):
            raise ExportServiceError("authored_vnext_scene_read_back_invalid")
        elements = sorted(
            slide["elements"],
            key=lambda item: (item.get("zOrder", -1), item.get("elementId", "")),
        )
        hybrid_export = slide.get("hybridExport")
        if hybrid_mode:
            if not isinstance(hybrid_export, dict) or not isinstance(
                hybrid_export.get("ownerDecisions"), list
            ) or not isinstance(hybrid_export.get("rasterIslands"), list):
                raise ExportServiceError("authored_vnext_hybrid_export_plan_invalid")
            decision_by_id = {
                item.get("ownerId"): item
                for item in hybrid_export["ownerDecisions"]
                if isinstance(item, dict) and isinstance(item.get("ownerId"), str)
            }
            if len(decision_by_id) != len(elements) or set(decision_by_id) != {
                item.get("elementId") for item in elements
            }:
                raise ExportServiceError(
                    "authored_vnext_hybrid_owner_inventory_mismatch"
                )
            native_ids = {
                owner_id
                for owner_id, decision in decision_by_id.items()
                if decision.get("decision") == "nativeEditable"
            }
        else:
            if hybrid_export is not None:
                raise ExportServiceError("authored_vnext_hybrid_profile_mismatch")
            decision_by_id = {}
            native_ids = {item.get("elementId") for item in elements}
        identities = []
        body_stacking: list[tuple[int, str, str]] = []
        for element in elements:
            if element.get("semanticRole") == "canvasBackground":
                raise ExportServiceError(
                    "authored_vnext_scene_body_background_forbidden"
                )
            kind = element.get("kind") or element.get("type")
            element_id = element.get("elementId") or element.get("id")
            if element_id not in native_ids:
                continue
            if kind not in expected_counts or not isinstance(element_id, str):
                raise ExportServiceError("authored_vnext_scene_read_back_invalid")
            z_order = element.get("zOrder")
            if isinstance(z_order, bool) or not isinstance(z_order, int):
                raise ExportServiceError("authored_vnext_scene_read_back_invalid")
            expected_counts[kind] += 1
            body_stacking.append((z_order, element_id, "scene"))
            identity: dict[str, Any] = {"elementId": element_id, "kind": kind}
            if kind == "text":
                text = element.get("text")
                if not isinstance(text, str):
                    raise ExportServiceError("authored_vnext_scene_read_back_invalid")
                identity["textDigest"] = prefixed_sha256({"text": text})
            elif kind == "image":
                identity["assetDigest"] = element.get("assetDigest")
            elif kind in {"table", "chart"}:
                manifest = data_manifest.get(element.get("dataId"))
                if (
                    not isinstance(manifest, dict)
                    or manifest.get("kind") != kind
                    or manifest.get("dataDigest") != element.get("dataDigest")
                    or not isinstance(manifest.get("payload"), dict)
                ):
                    raise ExportServiceError(
                        "authored_vnext_scene_data_manifest_invalid"
                    )
                identity["dataDigest"] = element.get("dataDigest")
                identity["nativeDataDigest"] = prefixed_sha256(
                    manifest["payload"]
                )
            else:
                identity["nativeKind"] = "shape"
            identities.append(identity)
        raster_identities: list[dict[str, Any]] = []
        if hybrid_mode:
            for island in sorted(
                hybrid_export["rasterIslands"],
                key=lambda item: (item.get("zBand", {}).get("minZ", -1), item.get("islandId", "")),
            ):
                if not isinstance(island, dict):
                    raise ExportServiceError(
                        "authored_vnext_hybrid_raster_island_invalid"
                    )
                raster_identities.append(
                    {
                        "islandId": island.get("islandId"),
                        "sourceOwnerIds": island.get("sourceOwnerIds"),
                        "assetDigest": island.get("assetDigest"),
                        "rasterPlaneDigest": island.get("rasterPlaneDigest"),
                        "zBand": island.get("zBand"),
                    }
                )
                min_z = island.get("zBand", {}).get("minZ")
                island_id = island.get("islandId")
                if isinstance(min_z, bool) or not isinstance(min_z, int) or not isinstance(island_id, str):
                    raise ExportServiceError(
                        "authored_vnext_hybrid_raster_island_invalid"
                    )
                body_stacking.append((min_z, island_id, "rasterIsland"))
                expected_raster_island_count += 1
                source_owner_ids = island.get("sourceOwnerIds")
                if not isinstance(source_owner_ids, list):
                    raise ExportServiceError(
                        "authored_vnext_hybrid_raster_island_invalid"
                    )
                expected_raster_owner_count += len(source_owner_ids)
        furniture_items: list[dict[str, Any]] = [background_furniture]
        for item in furniture:
            visibility = item.get("visibility", "always")
            if (
                visibility == "hidden"
                or visibility == "coverOnly" and page_number != 1
                or visibility == "exceptCover" and page_number == 1
            ):
                continue
            identity: dict[str, Any] = {
                "furnitureId": item["id"],
                "kind": item["kind"],
            }
            if item["kind"] in {"text", "pageNumber"}:
                text = (
                    str(page_number)
                    if item["kind"] == "pageNumber"
                    else str(item.get("text") or "")
                )
                identity["textDigest"] = prefixed_sha256({"text": text})
                identity["textStyle"] = _expected_furniture_text_style(
                    item, payload["master"], background_color
                )
            elif item["kind"] == "logo" and item.get("assetSha256"):
                identity["assetDigest"] = f"sha256:{item['assetSha256']}"
            elif item["kind"] == "logo":
                identity["unconfigured"] = True
            else:
                identity["nativeKind"] = "shape"
            furniture_items.append(identity)
        expected_scene_slides.append(
            {
                "slideId": slide.get("slideId"),
                "slideDigest": slide.get("slideDigest") or slide.get("digest"),
                "elements": identities,
                **(
                    {
                        "ownerDecisions": sorted(
                            hybrid_export["ownerDecisions"],
                            key=lambda item: item["ownerId"],
                        ),
                        "rasterIslands": raster_identities,
                    }
                    if hybrid_mode
                    else {}
                ),
                "furniture": furniture_items,
                "stackingOrder": [
                    {"layer": "furniture", "id": _BACKGROUND_FURNITURE_ID}
                ]
                + [
                    {"layer": layer, "id": owner_id}
                    for _z_order, owner_id, layer in sorted(body_stacking)
                ]
                + [
                    {"layer": "furniture", "id": item["furnitureId"]}
                    for item in furniture_items[1:]
                ],
            }
        )
    expected_scene = {
        "sceneDigest": scene.get("sceneDigest") or scene.get("digest"),
        "compositionDigest": scene.get("compositionDigest"),
        "compositionPin": scene.get("compositionPin"),
        **({"hybridProfileId": hybrid_profile_id} if hybrid_mode else {}),
        "slides": expected_scene_slides,
    }
    expected_capabilities = (
        {
            "profileId": "authored-editable-hybrid-ko-en-basic-v1",
            "nativeEditable": ["image", "shape", "table", "text"],
            "rasterIsland": [
                "chart",
                "connector",
                "group",
                "image",
                "shape",
                "table",
                "text",
            ],
        }
        if hybrid_mode
        else {
            "supported": ["chart", "image", "shape", "table", "text"],
            "unsupported": ["connector", "group"],
        }
    )
    expected_hybrid_ownership = {
        "profileId": hybrid_profile_id if hybrid_mode else None,
        "nativeOwnerCount": sum(expected_counts.values()),
        "rasterIslandCount": expected_raster_island_count,
        "rasterOwnerCount": expected_raster_owner_count,
        "nativeRasterDuplicateOwners": 0,
        "missingOrGhostQids": 0,
    }
    if (
        capabilities != expected_capabilities
        or native_counts != expected_counts
        or hybrid_ownership != expected_hybrid_ownership
    ):
        raise ExportServiceError("authored_vnext_scene_capability_read_back_mismatch")
    if (
        actual_scene.get("sceneDigest") != expected_scene["sceneDigest"]
        or actual_scene.get("compositionDigest")
        != expected_scene["compositionDigest"]
        or actual_scene.get("compositionPin") != expected_scene["compositionPin"]
        or actual_scene.get("hybridProfileId")
        != expected_scene.get("hybridProfileId")
        or not isinstance(actual_scene.get("slides"), list)
        or len(actual_scene["slides"]) != len(expected_scene_slides)
    ):
        raise ExportServiceError("authored_vnext_scene_read_back_mismatch")
    for expected_slide, reopened_slide in zip(
        expected_scene_slides, actual_scene["slides"], strict=True
    ):
        if (
            reopened_slide.get("slideId") != expected_slide["slideId"]
            or reopened_slide.get("slideDigest") != expected_slide["slideDigest"]
            or reopened_slide.get("furniture") != expected_slide["furniture"]
            or reopened_slide.get("stackingOrder") != expected_slide["stackingOrder"]
            or reopened_slide.get("ownerDecisions")
            != expected_slide.get("ownerDecisions")
            or not isinstance(reopened_slide.get("elements"), list)
            or len(reopened_slide["elements"]) != len(expected_slide["elements"])
            or not isinstance(reopened_slide.get("rasterIslands", []), list)
            or len(reopened_slide.get("rasterIslands", []))
            != len(expected_slide.get("rasterIslands", []))
        ):
            raise ExportServiceError("authored_vnext_scene_read_back_mismatch")
        for expected_element, reopened_element in zip(
            expected_slide["elements"], reopened_slide["elements"], strict=True
        ):
            bounds = reopened_element.get("renderedBounds")
            if (
                not isinstance(bounds, dict)
                or any(
                    isinstance(bounds.get(key), bool)
                    or not isinstance(bounds.get(key), (int, float))
                    for key in ("x", "y", "width", "height")
                )
                or bounds["x"] < 0
                or bounds["y"] < 0
                or bounds["width"] <= 0
                or bounds["height"] <= 0
            ):
                raise ExportServiceError(
                    "authored_vnext_scene_geometry_read_back_mismatch"
                )
            comparable = {
                key: value
                for key, value in reopened_element.items()
                if key not in {"renderedBounds", "cropGeometry"}
            }
            if comparable != expected_element:
                raise ExportServiceError(
                    "authored_vnext_scene_element_read_back_mismatch"
                )
            if expected_element["kind"] == "image":
                crop = reopened_element.get("cropGeometry")
                if (
                    not isinstance(crop, dict)
                    or any(
                        isinstance(crop.get(key), bool)
                        or not isinstance(crop.get(key), (int, float))
                        or crop[key] < 0
                        or crop[key] >= 1
                        for key in ("left", "top", "right", "bottom")
                    )
                    or crop["left"] + crop["right"] >= 1
                    or crop["top"] + crop["bottom"] >= 1
                ):
                    raise ExportServiceError(
                        "authored_vnext_scene_image_crop_read_back_mismatch"
                    )
        for expected_island, reopened_island in zip(
            expected_slide.get("rasterIslands", []),
            reopened_slide.get("rasterIslands", []),
            strict=True,
        ):
            bounds = reopened_island.get("renderedBounds")
            comparable = {
                key: value
                for key, value in reopened_island.items()
                if key != "renderedBounds"
            }
            if (
                comparable != expected_island
                or not isinstance(bounds, dict)
                or any(
                    isinstance(bounds.get(key), bool)
                    or not isinstance(bounds.get(key), (int, float))
                    for key in ("x", "y", "width", "height")
                )
                or bounds["x"] < 0
                or bounds["y"] < 0
                or bounds["width"] <= 0
                or bounds["height"] <= 0
            ):
                raise ExportServiceError(
                    "authored_vnext_hybrid_raster_island_read_back_mismatch"
                )
    for expected_slide, actual_slide in zip(
        expected_scene_slides, receipt["slides"], strict=True
    ):
        if (
            actual_slide.get("slideId") != expected_slide["slideId"]
            or actual_slide.get("slideDigest") != expected_slide["slideDigest"]
            or actual_slide.get("elementIds")
            != [item["elementId"] for item in expected_slide["elements"]]
            or actual_slide.get("rasterIslandIds", [])
            != [
                item["islandId"]
                for item in expected_slide.get("rasterIslands", [])
            ]
            or actual_slide.get("rasterOwnerIds", [])
            != sorted(
                owner_id
                for item in expected_slide.get("rasterIslands", [])
                for owner_id in item["sourceOwnerIds"]
            )
        ):
            raise ExportServiceError("authored_vnext_scene_slide_read_back_mismatch")
    for page_number, (expected_slide, actual_slide) in enumerate(
        zip(payload["slides"], receipt["slides"], strict=True),
        start=1,
    ):
        encoded = expected_slide.get("values", {}).get(
            "__resolvedSnapshotEvidence"
        )
        if encoded is None:
            continue
        try:
            expected = json.loads(encoded)
        except (TypeError, ValueError) as error:
            raise ExportServiceError(
                "authored_vnext_resolved_snapshot_evidence_invalid"
            ) from error
        checks = {
            "bodyGrammarDigest": expected.get("bodyGrammarDigest"),
            "referenceLayoutDigest": expected.get(
                "resolvedReferenceLayoutDigest"
            ),
            "resolvedTemplateId": expected.get("resolvedTemplateId"),
            "resolvedCompositionDigest": expected.get(
                "resolvedCompositionDigest"
            ),
            "resolvedRuleDigest": expected.get("resolvedRuleDigest"),
            "pageNumber": page_number,
            "slideCount": len(payload["slides"]),
            "semanticRolesUsed": expected.get("semanticRolesUsed"),
            "layoutRolesUsed": expected.get("layoutRolesUsed"),
        }
        if any(actual_slide.get(key) != value for key, value in checks.items()):
            raise ExportServiceError(
                "authored_vnext_resolved_snapshot_read_back_mismatch"
            )


@dataclass(frozen=True)
class CreateJobCommand:
    deck_id: str
    expected_deck_revision: int
    expected_deck_digest: str
    expected_master_revision: int
    expected_master_digest: str
    expected_active_artifact_revision: int
    expected_active_artifact_digest: str | None
    expected_source_package_digest: str | None
    expected_asset_digests: list[str]
    expected_font_digest: str
    idempotency_key: str
    timeout_ms: int
    max_attempts: int
    request_payload: dict[str, Any]
    operation_fence: int = 1


class AuthoredVNextExportService:
    def __init__(
        self,
        repository: AuthoredVNextSqlRepository,
        worker: OoxmlWorkerTransport,
        artifact_store: BufferedArtifactStore,
        master_asset_store: OwnerMasterAssetStore | None = None,
    ) -> None:
        self.repository = repository
        self.worker = worker
        self.artifact_store = artifact_store
        self.master_asset_store = master_asset_store

    def _materialize_ai_text_zones(
        self,
        contract,
        request_payload: dict[str, Any],
        *,
        expected_master_digest: str,
    ):
        payload = request_payload.get("aiTextZones")
        if payload is None:
            return contract
        # Legacy semantic AI text placeholders are compatibility-read only.
        # A furniture-only effective contract exposes geometry-only aiEditZones,
        # so persisted text-zone values cannot mutate template-owned content.
        if not any(
            element.content.role == "ai-text-zone"
            for element in contract.semantic_elements
        ):
            return contract
        try:
            resolved = ResolvedAITextZoneSet.model_validate(payload)
        except ValueError as error:
            raise ExportServiceError("authored_vnext_ai_text_zones_invalid") from error
        try:
            return materialize_ai_text_zone_contract(
                contract,
                resolved,
                expected_master_digest=expected_master_digest,
            )
        except ValueError as error:
            raise ExportServiceError("authored_vnext_ai_text_zones_mismatch") from error

    def _materialize_composition(
        self,
        contract,
        deck,
        request_payload: dict[str, Any],
        *,
        expected_master_digest: str,
    ):
        """Replay the persisted composition before any runtime-only AI text binding."""

        pinned = getattr(deck, "composition", None)
        payload = request_payload.get("compositionPin")
        if payload is not None:
            try:
                from models.authored_vnext.deck.composition import AuthoredCompositionPin

                requested = AuthoredCompositionPin.model_validate(payload)
            except ValueError as error:
                raise ExportServiceError("authored_vnext_composition_invalid") from error
            if pinned is None or requested != pinned:
                raise ExportServiceError("authored_vnext_composition_mismatch")
        if pinned is None:
            return contract
        if pinned.master_digest != expected_master_digest:
            raise ExportServiceError("authored_vnext_composition_master_mismatch")
        try:
            return materialize_authored_composition(contract, pinned)
        except ValueError as error:
            raise ExportServiceError("authored_vnext_composition_mismatch") from error

    def _worker_assets(self, master, deck) -> list[dict[str, Any]]:
        references = {
            asset.asset_id: asset.digest
            for asset in (master.brand_assets.logo, master.brand_assets.wordmark)
            if asset is not None
        }
        semantic_references = semantic_asset_references(master)
        furniture_ids = {
            item.asset_id
            for item in (
                master.furniture.logo,
                master.furniture.header,
                master.furniture.footer,
                master.furniture.divider,
                master.furniture.page_number,
            )
            if item.asset_id is not None
        }
        if references.keys() != furniture_ids:
            raise ExportServiceError("authored_vnext_master_asset_reference_mismatch")
        for asset_id, digest in semantic_references.items():
            if asset_id in references and references[asset_id] != digest:
                raise ExportServiceError("authored_vnext_master_asset_reference_mismatch")
            references[asset_id] = digest
        try:
            scene_references = scene_asset_references(deck)
        except ValueError as error:
            raise ExportServiceError(str(error)) from error
        for asset_id, reference in scene_references.items():
            digest = reference["digest"]
            if asset_id in references and references[asset_id] != digest:
                raise ExportServiceError("authored_vnext_scene_asset_digest_mismatch")
            references[asset_id] = digest
        if not references:
            return []
        assets = []
        try:
            for asset_id, digest in sorted(references.items()):
                scene_reference = scene_references.get(asset_id)
                encoded = (
                    scene_reference.get("bytesBase64")
                    if scene_reference is not None
                    else None
                )
                storage_reference = (
                    scene_reference.get("storageReference")
                    if scene_reference is not None
                    else f"asset-store:{asset_id}"
                )
                if (
                    encoded is None
                    and isinstance(storage_reference, str)
                    and storage_reference.startswith("data:")
                ):
                    expected_prefix = (
                        f"data:{scene_reference['mediaType']};base64,"
                    )
                    if not storage_reference.startswith(expected_prefix):
                        raise ExportServiceError(
                            "authored_vnext_scene_asset_storage_reference_invalid"
                        )
                    encoded = storage_reference.removeprefix(expected_prefix)
                if isinstance(encoded, str):
                    try:
                        value = base64.b64decode(encoded, validate=True)
                    except (ValueError, binascii.Error) as error:
                        raise ExportServiceError(
                            "authored_vnext_scene_asset_bytes_invalid"
                        ) from error
                    if self.artifact_store.digest_bytes(value) != digest:
                        raise ExportServiceError(
                            "authored_vnext_scene_asset_digest_mismatch"
                        )
                else:
                    if storage_reference != f"asset-store:{asset_id}":
                        raise ExportServiceError(
                            "authored_vnext_scene_asset_storage_reference_invalid"
                        )
                    if self.master_asset_store is None:
                        raise ExportServiceError(
                            "authored_vnext_scene_image_asset_unavailable"
                        )
                    value = self.master_asset_store.open_verified(asset_id, digest)
                if (
                    scene_reference is not None
                    and len(value) != scene_reference["sizeBytes"]
                ):
                    raise ExportServiceError(
                        "authored_vnext_scene_asset_size_mismatch"
                    )
                assets.append({
                    "assetId": asset_id,
                    "mediaType": (
                        scene_reference["mediaType"]
                        if scene_reference is not None
                        else "image/png"
                    ),
                    "sha256": digest.removeprefix("sha256:"),
                    "sizeBytes": len(value),
                    "bytesBase64": base64.b64encode(value).decode("ascii"),
                })
        except MasterAssetError as error:
            raise ExportServiceError(error.code) from error
        return assets

    def create_job(self, command: CreateJobCommand):
        deck = self.repository.get_deck(command.deck_id)
        if (deck.revision, deck.digest) != (
            command.expected_deck_revision,
            command.expected_deck_digest,
        ):
            raise ExportServiceError("authored_vnext_deck_digest_mismatch")
        scene = getattr(deck, "scene", None)
        scene_profile = getattr(scene, "hybrid_profile_id", None)
        requested_profile = command.request_payload.get("editableHybridProfileId")
        if scene_profile != requested_profile:
            raise ExportServiceError(
                "authored_vnext_editable_hybrid_profile_mismatch"
            )
        master_pin = getattr(deck, "master", None)
        deck_master_revision = (
            master_pin.master_revision
            if master_pin is not None
            else deck.master_revision
        )
        deck_master_digest = (
            master_pin.master_digest if master_pin is not None else deck.master_digest
        )
        deck_master_id = master_pin.master_id if master_pin is not None else deck.master_id
        if (deck_master_revision, deck_master_digest) != (
            command.expected_master_revision,
            command.expected_master_digest,
        ):
            raise ExportServiceError("authored_vnext_master_digest_mismatch")
        master = self.repository.get_master(deck_master_id, deck_master_revision)
        if master.status != "published" or master.digest != deck_master_digest:
            raise ExportServiceError("authored_vnext_master_not_published")
        composed_contract = self._materialize_composition(
            master.contract,
            deck,
            command.request_payload,
            expected_master_digest=master.digest,
        )
        export_contract = self._materialize_ai_text_zones(
            composed_contract,
            command.request_payload,
            expected_master_digest=master.digest,
        )
        try:
            assets = self._worker_assets(export_contract, deck)
            _, source_digests = build_worker_request(
                operation_id="digest-probe",
                fence_token=1,
                master=export_contract,
                deck=deck,
                expected_package_digest=command.expected_source_package_digest,
                assets=assets,
            )
        except ValueError as error:
            raise ExportServiceError(str(error)) from error
        # The public CAS pin is the sealed manifest digest exposed by the
        # published master.  The worker payload digest below intentionally
        # covers the serialized manifest including that seal and is retained
        # separately for worker/read-back integrity.
        if master.contract.font_manifest.digest != command.expected_font_digest:
            raise ExportServiceError("authored_vnext_font_digest_mismatch")
        expected_asset_manifest = source_digests["assetManifest"]
        actual_asset_digests = sorted(
            f"sha256:{asset['sha256']}" for asset in assets
        )
        if sorted(set(command.expected_asset_digests)) != actual_asset_digests:
            raise ExportServiceError("authored_vnext_asset_digest_mismatch")
        active = self.repository.get_active_artifact(deck.deck_id)
        actual_publication = (
            (active[0].revision, active[0].digest) if active is not None else (0, None)
        )
        if actual_publication != (
            command.expected_active_artifact_revision,
            command.expected_active_artifact_digest,
        ):
            raise ExportServiceError("authored_vnext_publication_conflict")
        expected_revision = command.expected_active_artifact_revision or None
        return self.repository.create_export_job(
            deck_id=deck.deck_id,
            deck_revision=deck.revision,
            deck_digest=deck.digest,
            master_revision=deck_master_revision,
            master_digest=source_digests["master"],
            master_id=deck_master_id,
            spec_digest=source_digests["spec"],
            asset_manifest_digest=expected_asset_manifest,
            font_manifest_digest=source_digests["fontManifest"],
            expected_package_digest=command.expected_source_package_digest,
            expected_publication_revision=expected_revision,
            expected_publication_digest=command.expected_active_artifact_digest,
            timeout_ms=command.timeout_ms,
            max_attempts=command.max_attempts,
            operation_fence=command.operation_fence,
            idempotency_key=command.idempotency_key,
            request_payload=command.request_payload,
        )

    async def run_job(self, job_id: uuid.UUID):
        claimed = self.repository.claim_export_job(
            job_id,
            worker_id=f"fastapi:{uuid.uuid4()}",
            lease_seconds=600,
        )
        try:
            deck = self.repository.get_deck(claimed.deck_id, claimed.deck_revision)
            master_pin = getattr(deck, "master", None)
            master = self.repository.get_master(
                master_pin.master_id if master_pin is not None else deck.master_id,
                master_pin.master_revision if master_pin is not None else deck.master_revision,
            )
            composed_contract = self._materialize_composition(
                master.contract,
                deck,
                claimed.request_payload,
                expected_master_digest=master.digest,
            )
            export_contract = self._materialize_ai_text_zones(
                composed_contract,
                claimed.request_payload,
                expected_master_digest=master.digest,
            )
            assets = self._worker_assets(export_contract, deck)
            payload, digests = build_worker_request(
                operation_id=str(claimed.id),
                fence_token=claimed.fence_token,
                master=export_contract,
                deck=deck,
                expected_package_digest=claimed.expected_package_digest,
                assets=assets,
            )
            actual = (
                digests["spec"],
                digests["assetManifest"],
                digests["fontManifest"],
            )
            expected = (
                claimed.spec_digest,
                claimed.asset_manifest_digest,
                claimed.font_manifest_digest,
            )
            if actual != expected or digests["master"] != claimed.master_digest:
                raise ExportServiceError("authored_vnext_source_digest_mismatch")
            result = await self.worker.execute(payload, claimed.timeout_ms)
            artifact = result.get("artifact")
            receipt = result.get("receipt")
            if (
                result.get("schemaVersion") != "authored-vnext-ooxml-worker/v1"
                or result.get("operation") != "EXPORT_PPTX"
                or result.get("status") != "SUCCEEDED"
                or result.get("operationId") != str(claimed.id)
                or result.get("fencingToken") != claimed.fence_token
                or not isinstance(artifact, dict)
                or not isinstance(receipt, dict)
            ):
                raise ExportServiceError("authored_vnext_worker_receipt_mismatch")
            _assert_worker_readback(receipt, payload)
            try:
                content = base64.b64decode(
                    artifact["contentBase64"], validate=True
                )
            except (KeyError, TypeError, ValueError, binascii.Error) as error:
                raise ExportServiceError("authored_vnext_worker_artifact_invalid") from error
            artifact_digest = f"sha256:{artifact.get('sha256', '')}"
            if (
                artifact.get("byteLength") != len(content)
                or self.artifact_store.digest_bytes(content) != artifact_digest
            ):
                raise ExportServiceError("authored_vnext_worker_artifact_digest_mismatch")
            storage_key, byte_length = self.artifact_store.publish(
                content,
                artifact_digest,
                storage_namespace=claimed.id.hex,
            )
            reopened = self.artifact_store.open_verified(
                storage_key, artifact_digest, byte_length
            ).read_bytes()
            package = receipt.get("package")
            if not isinstance(package, dict) or not isinstance(
                package.get("packageDigest"), str
            ):
                raise ExportServiceError("authored_vnext_worker_read_back_invalid")
            package_digest = f"sha256:{package['packageDigest']}"
            self.repository.complete_export_job(
                claimed.id,
                fence_token=claimed.fence_token,
                artifact=ExportArtifactInput(
                    storage_key=storage_key,
                    artifact_digest=artifact_digest,
                    readback_digest=self.artifact_store.digest_bytes(reopened),
                    package_digest=package_digest,
                    size_bytes=byte_length,
                    master_digest=digests["master"],
                    spec_digest=digests["spec"],
                    asset_manifest_digest=digests["assetManifest"],
                    font_manifest_digest=digests["fontManifest"],
                ),
            )
        except (
            WorkerError,
            ExportServiceError,
            ArtifactStoreError,
            OperationFenceError,
            PublicationConflictError,
            SourceDigestMismatchError,
        ) as error:
            code = getattr(error, "code", str(error))
            try:
                self.repository.fail_export_job(
                    claimed.id,
                    fence_token=claimed.fence_token,
                    failure_code=code,
                    failure_detail=None,
                    cancelled=code in {"authored_vnext_worker_cancelled"},
                )
            except Exception:
                pass
        except Exception:
            # Never strand a claimed durable job in ``running`` because an
            # unexpected adapter/runtime error escaped the known taxonomy.
            try:
                self.repository.fail_export_job(
                    claimed.id,
                    fence_token=claimed.fence_token,
                    failure_code="authored_vnext_export_failed",
                    failure_detail=None,
                )
            except Exception:
                pass
        return self.repository.get_export_job(claimed.id)
