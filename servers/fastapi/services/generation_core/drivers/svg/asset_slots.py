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
from dataclasses import dataclass, field, replace
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
#: Model-drawn pictures are larger than icons; the author sees a larger preview.
_DRAWING_PREVIEW_EDGE = 256
_DRAWING_KINDS = frozenset({"illustration", "spot"})


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
    flattened = rasterized = drawings = 0
    for slot in offer.slots:
        if not 0 <= slot.page_index < count:
            raise ValueError("svg_asset_offer_page_out_of_range")
        payload = await asyncio.to_thread(slot.verify)
        meta = {"kind": slot.kind, "role": slot.role, "origin": slot.origin}
        if slot.is_vector and slot.kind in _DRAWING_KINDS:
            # A model-drawn picture, already validated and normalised into
            # contract shapes in its own palette: keep its colours.
            from services.generation_core.assets.vector_illustration import palette_of

            text = payload.decode("utf-8")
            colors = palette_of(text) or ["#000000"]
            drawing = flatten_icon_svg(text, color=colors[0], point_budget=None, palette=colors)
            drawing = replace(drawing, line_cap="round", line_join="round")
            # Two step pictures of one page may be byte-identical; the slot id names the item too.
            slot_id = source_slot_id_for_asset(
                f"vector:{presentation_id}:{slot.page_index}:{slot.role}:{slot.concept or ''}:{slot.sha256}")
            result.vector[slot.page_index][slot_id] = {
                "data_url": _data_url(rasterize_icon(drawing, size=_DRAWING_PREVIEW_EDGE)),
                "description": slot.alt, "required": False, "allowOmit": True,
                "kind": slot.kind, "vector": drawing}
            result.slots[slot.page_index][slot_id] = {**meta, "mode": "drawing",
                                                      "points": drawing.point_count}
            drawings += 1
            continue
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
    result.receipt.update(iconsFlattened=flattened, iconsRasterized=rasterized,
                          **({"drawingsPlaced": drawings} if drawings else {}))
    return result


_NATIVE_LITERAL_VISUAL_REQUEST = re.compile(
    r"image|photo|picture|illustrat|artwork|diagram|flowchart|hierarch|roadmap|timeline|"
    r"process|workflow|isometric|icon|이미지|사진|삽화|일러스트|그림|관계도|흐름도|조직도|"
    r"프로세스|아이콘|도식|관계|절차|단계|→|->", re.I,
)


def _native_literal_pages(request, outline, *, style_snapshot, has_source_assets):
    """Require complete literal coverage, rather than finding a chart inside prose."""
    from services.generation_core.drivers.svg.native_requirements import requested_native_data

    if (has_source_assets is not False or getattr(request, "strategy", None) != "svg"
            or getattr(request, "generation_mode", None) not in {"auto", "free-design"}
            or getattr(request, "authored_style", None) != "default"
            or not isinstance(style_snapshot, dict) or style_snapshot.get("composition_mode") != "free-svg"
            or style_snapshot.get("style_id") != "default"):
        return None
    if any(getattr(request, key, None) for key in (
        "files", "reference_snapshot_ids", "_reference_snapshots", "master_pin",
        "svg_design_reference_snapshot_id", "_svg_uploaded_design_reference",
        "svg_template_pin",
        "_source_content_svg_reflow", "_raster_reconstruction")):
        return None
    if any(style_snapshot.get(key) for key in ("master_template", "design_pack_template")):
        return None
    supplied = getattr(request, "slides_markdown", None)
    contents = [slide.content for slide in outline.slides]
    if not supplied or supplied != contents:
        return None
    if _NATIVE_LITERAL_VISUAL_REQUEST.search("\n".join([getattr(request, "instructions", None) or "", *contents])):
        return None
    pages = []
    for content in contents:
        wanted = requested_native_data(content)
        if not wanted:
            return None
        rows, charts = [], []
        for line in content.splitlines():
            line = line.strip()
            if not line or re.fullmatch(r"#{1,6}\s+\S.*", line):
                continue
            if "|" in line:
                cells = [cell.strip() for cell in line.strip("|").split("|")]
                if len(cells) < 2 or len(cells) > 8 or not all(cells):
                    return None
                if all(re.fullmatch(r"[:\-\s]+", cell) for cell in cells):
                    continue
                rows.append(cells)
            elif re.fullmatch(r"(?:chart|차트)\s*[:：]\s*.+", line, re.I):
                literal = requested_native_data(line).get("charts", [])
                if len(literal) != 1:
                    return None
                parts = re.split(r"[,;]\s*(?!\d{3}(?:\D|$))", re.split(r"[:：]", line, maxsplit=1)[1])
                if not all(re.fullmatch(r"\s*(.+?)\s+([-+]?\d+(?:,\d{3})*(?:\.\d+)?)\s*", part) for part in parts):
                    return None
                charts.extend(literal)
            else:
                return None
        required_rows = [row for table in wanted.get("tables", []) for row in table["rows"]]
        def chart_data(items):
            return [{key: value for key, value in item.items() if key != "id"} for item in items]

        if (rows != required_rows or chart_data(charts) != chart_data(wanted.get("charts", []))
                or len(rows) > 64 or any(len(chart["categories"]) > 32 for chart in charts)):
            return None
        labels = (rows[0] if rows else []) + [label for chart in charts for label in chart["categories"]]
        pages.append(list(dict.fromkeys(label for label in labels if len(label) <= 80))[:4])
    return pages


async def _native_literal_offer(pages, request):
    """Offer bounded catalogue vectors; no planner or image provider is dispatched."""
    from services.generation_core.assets.icon_pack import concept_key, resolve_icons
    from services.generation_core.assets.offer import _file_slot, ICON_EDGE_HINT

    labels = list(dict.fromkeys(label for page in pages for label in page))[:8]
    slots = []
    failures = []
    try:
        resolution = await resolve_icons(labels, generate=False, pack_key="default",
                                         color=getattr(request, "primary_color", None) or "#1E5EFF")
        for index, page in enumerate(pages):
            for label in page:
                if label not in labels or len(slots) >= 8:
                    continue
                icon = resolution.icons.get(concept_key(label))
                if icon is None or icon.source != "library" or Path(icon.path).suffix.lower() != ".svg":
                    continue
                slots.append(_file_slot(icon.path, page_index=index, role="figure-icon", kind="icon",
                                        alt=f"icon: {label}", concept=label,
                                        bbox_hint=(0.0, 0.0, ICON_EDGE_HINT, ICON_EDGE_HINT), origin="library"))
    except (ValueError, OSError) as error:
        failures.append(type(error).__name__)
    return AssetOffer(slots=tuple(slots), receipt={"status": "native_literal_library_only", "providerCalls": 0,
        "illustrations": 0, "icons": len(slots), "failures": failures,
        "eligibilityVersion": "svg-native-literal-offer/v1"})


def template_step_pictures(request) -> bool:
    """Whether this deck gets model-drawn step pictures: a template-redesign deck with
    ``SVG_TEMPLATE_STEP_PICTURES`` on (``utils/get_env_concurrency.py``)."""
    from utils.get_env_concurrency import get_svg_template_step_pictures_env

    return (getattr(request, "svg_template_pin", None) is not None
            and getattr(request, "svg_template_mode", None) == "template-redesign"
            and get_svg_template_step_pictures_env())


async def resolve_offer(request, outline, roles, job, *, profile, checkpoint, durable: bool,
                        style_snapshot: dict | None = None, has_source_assets: bool | None = None) -> AssetOffer:
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

    from utils.get_env import get_svg_illustration_mode_env, get_svg_illustration_parts_env

    literal_pages = _native_literal_pages(request, outline, style_snapshot=style_snapshot,
                                          has_source_assets=has_source_assets)
    image_service = ImageGenerationService(get_images_directory()) if literal_pages is None else None
    mode, parts = get_svg_illustration_mode_env(), get_svg_illustration_parts_env()
    step_pictures = mode == "off" and template_step_pictures(request)
    if step_pictures:
        # SVG_TEMPLATE_STEP_PICTURES: step pictures only, no cover or scene drawing.
        mode, parts = "vector", ("spots",)
    if literal_pages is not None:
        offer = await _native_literal_offer(literal_pages, request)
    elif mode != "off":
        provider = not (image_service.is_image_generation_disabled or image_service.image_gen_func is None)
        offer = await _build(request, outline, roles, job, profile, image_service if provider else None,
                             icons_only=not provider, illustration_mode=mode, illustration_parts=parts)
        if step_pictures:
            # Names the route in the receipt; every other receipt keeps its bytes.
            offer = replace(offer, receipt={**offer.receipt, "templateStepPictures": True})
    elif image_service.is_image_generation_disabled or image_service.image_gen_func is None:
        # No picture can be made, but the bundled vector library needs no
        # provider: the plan still places icons, with no illustration or sprite
        # sheet. Persisted like any offer, so a resume after the setting
        # changes still matches the sealed source manifest.
        offer = await _build(request, outline, roles, job, profile, None, icons_only=True)
    else:
        offer = await _build(request, outline, roles, job, profile, image_service)
    if job is not None:
        seed = await checkpoint(job, 0) or {}
        seed[SEED_KEY] = offer.to_json(root=get_app_data_directory_env())
        await checkpoint(job, 0, payload=seed)
    return offer


async def _build(request, outline, roles, job, profile, image_service, *, icons_only: bool = False,
                 illustration_mode: str = "off", illustration_parts=None) -> AssetOffer:
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
            timeout_seconds=profile.illustration_timeout_seconds, image_service=image_service,
            icons_only=icons_only, illustration_mode=illustration_mode,
            illustration_parts=illustration_parts)
    return offer


def legacy_manifest(page: dict) -> dict:
    """Inline data-URL entries for the non-canonical path, optional like the canonical policy."""
    from services.svg_presentation_legacy_assets import _legacy_source_image_manifest

    manifest = _legacy_source_image_manifest(page)
    for slot_id, candidate in page.items():
        manifest[slot_id].update(required=False, allowOmit=True, allowExcludeOnFailure=False,
                                 kind=candidate.source_locator["assetOffer"])
    return manifest


__all__ = ["SvgAssetOffer", "legacy_manifest", "project_offer", "resolve_offer", "template_step_pictures"]
