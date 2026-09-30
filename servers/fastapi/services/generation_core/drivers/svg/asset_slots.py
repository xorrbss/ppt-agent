"""Project an AssetOffer onto the SVG driver's image slots.

Illustrations and raster icons become ``source-slot-*`` raster slots exactly
like the user's own source images: each file is stored in the owner's image
directory, registered as an ``ImageAsset`` and handed to the existing source
manifest as a ``SourceImageCandidate``. From there the canonical manifest,
screen placement, the ``sourceImages`` offered/used/omitted receipt and export
treat it as any other source image. The candidate's ``source_locator`` carries
``assetOffer`` so the manifest gives it an optional policy: an instruction to
preserve every *source* image never makes a generated picture mandatory.

Vector icons are not raster slots. The model sees a PNG preview under a
``source-slot-*`` handle, and the author step replaces that ``<image>`` with
the icon flattened to the SVG contract subset (``embed_assets``). An icon
whose flattening exceeds ``ICON_POINT_BUDGET`` is rasterized and joins the
raster slots instead; the per-page receipt records which mode each slot took.

The offer is persisted in page 0's checkpoint so a resumed attempt replays the
same files instead of buying new pictures, which would also break the sealed
source manifest's slot plan.
"""
from __future__ import annotations

import asyncio
import base64
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from services.generation_core.assets.offer import AssetOffer, AssetSlot
from services.source_images.models import SourceImageCandidate
from utils.get_env import get_app_data_directory_env
from utils.hashing import sha256_hex

#: Page 0's checkpoint key for the deck's persisted offer. Distinct from the
#: per-page ``assetOffer`` receipt the author step writes on every page.
SEED_KEY = "assetOfferPlan"
_OFFER_NAMESPACE = uuid.UUID("5b0c7e1a-3f4d-5a2e-9c61-7d8e2f4a1b30")
_PREVIEW_EDGE = 128


@dataclass
class SvgAssetOffer:
    """Per-page SVG projection; every list has one entry per page."""
    raster: list = field(default_factory=list)   # {slot_id: SourceImageCandidate}
    vector: list = field(default_factory=list)   # {slot_id: model manifest entry}
    slots: list = field(default_factory=list)    # {slot_id: receipt metadata}
    receipt: dict = field(default_factory=dict)


def _ink(svg_text: str) -> str:
    match = re.search(r'\bstroke="(#[0-9A-Fa-f]{6}|#[0-9A-Fa-f]{3})"', svg_text)
    return match[1] if match else "#1E5EFF"


def _store(payload: bytes, suffix: str) -> Path:
    from utils.asset_directory_utils import get_images_directory

    digest = sha256_hex(payload)
    target = Path(get_images_directory()) / "svg-asset-offer" / f"{digest}{suffix}"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_bytes(payload)
    return target


async def _register(session, presentation_id, slot: AssetSlot, payload: bytes, media_type: str,
                    width: int, height: int) -> tuple[str, SourceImageCandidate]:
    from models.sql.image_asset import ImageAsset
    from utils.authored_image_slots import source_slot_id_for_asset

    suffix = ".png" if media_type == "image/png" else ".jpg"
    path = await asyncio.to_thread(_store, payload, suffix)
    digest = sha256_hex(payload)
    asset_id = uuid.uuid5(_OFFER_NAMESPACE, f"{presentation_id}:{digest}")
    if await session.get(ImageAsset, asset_id) is None:
        session.add(ImageAsset(id=asset_id, path=str(path), is_uploaded=False, extras={
            "presentation_id": str(presentation_id), "digest": digest, "mime_type": media_type,
            "width": width, "height": height, "asset_offer_kind": slot.kind,
            "asset_offer_origin": slot.origin}))
    candidate = SourceImageCandidate(
        asset_id=str(asset_id), path=path, mime_type=media_type, width=width, height=height,
        digest=digest, semantic_description=slot.alt, analysis_status="asset_offer",
        source_locator={"sourceFormat": "image", "extractionKind": "direct_image",
                        "assetOffer": slot.kind})
    return source_slot_id_for_asset(str(asset_id)), candidate


def _data_url(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode()


async def project_offer(session, offer: AssetOffer, *, presentation_id, count: int) -> SvgAssetOffer:
    """Turn the format-neutral offer into this driver's per-page slots."""
    from services.generation_core.drivers.svg.embed_assets import (
        IconFlattenBudgetExceeded, flatten_icon_svg, rasterize_icon,
    )
    from utils.authored_image_slots import source_slot_id_for_asset

    result = SvgAssetOffer(raster=[{} for _ in range(count)], vector=[{} for _ in range(count)],
                           slots=[{} for _ in range(count)], receipt=dict(offer.receipt))
    flattened = rasterized = 0
    for slot in offer.slots:
        if not 0 <= slot.page_index < count:
            raise ValueError("svg_asset_offer_page_out_of_range")
        payload = await asyncio.to_thread(slot.verify)
        meta = {"kind": slot.kind, "role": slot.role, "origin": slot.origin}
        if slot.is_vector:
            text = payload.decode("utf-8")
            try:
                icon = flatten_icon_svg(text, color=_ink(text))
            except IconFlattenBudgetExceeded:
                png = rasterize_icon(flatten_icon_svg(text, color=_ink(text), point_budget=None))
                slot_id, candidate = await _register(session, presentation_id, slot, png, "image/png",
                                                     256, 256)
                result.raster[slot.page_index][slot_id] = candidate
                result.slots[slot.page_index][slot_id] = {**meta, "mode": "rasterized"}
                rasterized += 1
                continue
            slot_id = source_slot_id_for_asset(f"vector:{presentation_id}:{slot.sha256}")
            result.vector[slot.page_index][slot_id] = {
                "data_url": _data_url(rasterize_icon(icon, size=_PREVIEW_EDGE)),
                "description": slot.alt, "required": False, "allowOmit": True,
                "kind": slot.kind, "vector": icon}
            result.slots[slot.page_index][slot_id] = {**meta, "mode": "flattened",
                                                      "points": icon.point_count}
            flattened += 1
            continue
        slot_id, candidate = await _register(session, presentation_id, slot, payload,
                                             slot.media_type, slot.width, slot.height)
        result.raster[slot.page_index][slot_id] = candidate
        result.slots[slot.page_index][slot_id] = {**meta, "mode": "raster"}
    await session.flush()
    result.receipt.update(iconsFlattened=flattened, iconsRasterized=rasterized)
    return result


async def resolve_offer(request, outline, roles, job, *, profile, checkpoint, durable: bool) -> AssetOffer:
    """Replay the persisted offer on resume, or build and persist a new one."""

    if job is not None and not durable:
        # A non-durable job replays inline checkpoints whose digest pins the
        # offered bytes; without a durable seed a resume would buy new ones.
        return AssetOffer(receipt={"status": "non_durable_job"})
    seed = (await checkpoint(job, 0) or {}) if job is not None else {}
    if seed.get(SEED_KEY):
        return AssetOffer.from_json(seed[SEED_KEY], root=get_app_data_directory_env())
    if seed.get("source_manifest_ref"):
        # An attempt sealed before offers existed must resume with its plan.
        return AssetOffer(receipt={"status": "sealed_without_offer"})
    from services.image_generation_service import ImageGenerationService
    from utils.asset_directory_utils import get_images_directory

    image_service = ImageGenerationService(get_images_directory())
    if image_service.is_image_generation_disabled or image_service.image_gen_func is None:
        # No picture can be made, so the plan call that would place them is
        # not bought either. Persisted like any offer, so a resume after the
        # setting changes still matches the sealed source manifest.
        offer = AssetOffer(receipt={"status": "image_generation_unavailable"})
    else:
        offer = await _build(request, outline, roles, job, profile, image_service)
    if job is not None:
        seed = await checkpoint(job, 0) or {}
        seed[SEED_KEY] = offer.to_json(root=get_app_data_directory_env())
        await checkpoint(job, 0, payload=seed)
    return offer


async def _build(request, outline, roles, job, profile, image_service) -> AssetOffer:
    from services.generation_core.assets.offer import build_asset_offer
    from services.generation_core.style.build import resolve_brand
    from services.provider_usage_instrumentation import provider_usage_scope
    from utils.authored_styles import resolve_authored_style

    style = resolve_authored_style(request.authored_style)
    brand = resolve_brand(request, outline, getattr(request, "language", None))
    with provider_usage_scope(job_id=job.id if job else None, operation="authored_generation",
                              stage="illustrate", attempt_number=job.attempt_number if job else 0):
        offer = await build_asset_offer(
            outline=outline, brand=brand, style=style, roles=roles,
            instructions=request.instructions, budget=profile.illustration_budget,
            timeout_seconds=profile.illustration_timeout_seconds, image_service=image_service)
    return offer


def legacy_manifest(page: dict) -> dict:
    """Inline data-URL entries for the non-canonical path, optional like the canonical policy."""
    from services.svg_presentation_legacy_assets import _legacy_source_image_manifest

    manifest = _legacy_source_image_manifest(page)
    for slot_id, candidate in page.items():
        manifest[slot_id].update(required=False, allowOmit=True, allowExcludeOnFailure=False,
                                 kind=candidate.source_locator["assetOffer"])
    return manifest


__all__ = ["SvgAssetOffer", "legacy_manifest", "project_offer", "resolve_offer"]
