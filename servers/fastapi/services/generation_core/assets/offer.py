"""AssetOffer: the pictures a deck plans before any page is authored.

Format-neutral. The builder reuses the deck-scoped infographic plan to decide
which pages are scenes (one generated illustration each) and which pages carry
a figure whose nodes get an icon, then produces the files: a transparent scene
cutout (or, without an alpha-capable provider, an ordinary illustration from
the style's illustration prompt) and icon-pack icons (a library vector SVG, a
cached pack PNG, or a newly generated sprite-sheet PNG). It returns slots that
name files on disk; how a slot is placed on a page is the driver's business
(``drivers/svg/asset_slots.py`` for SVG).

Cost and time are bounded by the runtime profile: ``illustration_budget`` is
the number of image-provider calls the offer may spend (one per scene, one per
icon sprite sheet) and ``illustration_timeout_seconds`` bounds each call. A
picture is optional: every failure drops that slot and is recorded in the
offer's receipt; nothing here raises for a provider problem.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional, Sequence

from services import svg_outline_spans
from utils.hashing import sha256_hex

LOGGER = logging.getLogger(__name__)

OFFER_SCHEMA_VERSION = "asset-offer/v1"
SlotKind = Literal["source", "illustration", "icon"]
#: Normalized (x, y, width, height) placement hint on the page canvas.
SCENE_BBOX_HINT = (0.55, 0.18, 0.4, 0.64)
ICON_EDGE_HINT = 0.06


@dataclass(frozen=True)
class AssetSlot:
    """One planned picture for one page. ``path`` is the file the slot shows."""

    page_index: int
    role: str  # "scene" | "figure-icon"
    kind: SlotKind
    alt: str
    path: str
    media_type: str  # image/png | image/jpeg | image/svg+xml
    sha256: str
    width: int
    height: int
    bbox_hint: Optional[tuple[float, float, float, float]] = None
    concept: Optional[str] = None
    origin: str = ""  # cutout | illustration | library | cached | generated

    @property
    def is_vector(self) -> bool:
        return self.media_type == "image/svg+xml"

    def verify(self) -> bytes:
        """Reopen the file and prove it is the bytes the offer recorded."""
        payload = Path(self.path).read_bytes()
        if sha256_hex(payload) != self.sha256:
            raise ValueError("asset_offer_digest_mismatch")
        return payload


@dataclass(frozen=True)
class AssetOffer:
    slots: tuple[AssetSlot, ...] = ()
    receipt: dict = field(default_factory=dict)

    def to_json(self, *, root: str | Path) -> dict:
        """Persistable form; paths are stored relative to ``root`` (app data)."""
        base = Path(root).resolve()
        slots = []
        for slot in self.slots:
            relative = Path(slot.path).resolve().relative_to(base)  # ValueError outside root
            slots.append({**asdict(slot), "path": relative.as_posix()})
        return {"schemaVersion": OFFER_SCHEMA_VERSION, "slots": slots, "receipt": self.receipt}

    @classmethod
    def from_json(cls, value: dict, *, root: str | Path) -> "AssetOffer":
        if not isinstance(value, dict) or value.get("schemaVersion") != OFFER_SCHEMA_VERSION:
            raise ValueError("asset_offer_schema_mismatch")
        base = Path(root).resolve()
        slots = []
        for item in value.get("slots", []):
            path = (base / item["path"]).resolve()
            if not path.is_relative_to(base):
                raise ValueError("asset_offer_path_outside_root")
            hint = item.get("bbox_hint")
            slots.append(AssetSlot(**{**item, "path": str(path), "bbox_hint": tuple(hint) if hint else None}))
        return cls(slots=tuple(slots), receipt=dict(value.get("receipt") or {}))


def _file_slot(path: str | Path, **fields: Any) -> AssetSlot:
    from PIL import Image

    file = Path(path)
    payload = file.read_bytes()
    if file.suffix.lower() == ".svg":
        media_type, width, height = "image/svg+xml", 128, 128
    else:
        with Image.open(file) as image:
            fmt, (width, height) = (image.format or "").lower(), image.size
        media_type = {"png": "image/png", "jpeg": "image/jpeg"}.get(fmt)
        if media_type is None:
            raise ValueError("asset_offer_unsupported_media_type")
    return AssetSlot(path=str(file), media_type=media_type, width=width, height=height,
                     sha256=sha256_hex(payload), **fields)


class _Spend:
    """The profile's provider-call budget, shared by every task of one offer."""

    def __init__(self, budget: int, timeout: float):
        self.left, self.timeout, self.calls, self.timeouts = max(0, budget), timeout, 0, 0

    def take(self, calls: int = 1) -> bool:
        if self.left < calls:
            return False
        self.left -= calls
        self.calls += calls
        return True

    async def bounded(self, awaitable):
        try:
            return await asyncio.wait_for(awaitable, timeout=self.timeout)
        except asyncio.TimeoutError:
            self.timeouts += 1
            return None


async def _scene_slot(index, slide, content, *, style, context, image_service, spend, failures):
    from services.generation_core.assets.icon_pack import generate_scene_cutout

    labels = [node.label for node in slide.nodes]
    subject = slide.takeaway or (labels[0] if labels else "")
    alt = subject or "illustration"
    if context is not None and spend.take():
        cutout = await spend.bounded(generate_scene_cutout(
            takeaway=subject, nodes=labels, output_directory=context.output_directory,
            style_prompt=context.style_prompt, settings=context.settings, provider=context.provider))
        if cutout is not None:
            return _file_slot(cutout.path, page_index=index, role="scene", kind="illustration",
                              alt=alt, bbox_hint=SCENE_BBOX_HINT, origin="cutout")
        failures.append({"page": index, "reason": "cutout_unavailable"})
    style_prompt = (getattr(style, "illustration_prompt", None) or "").strip()
    if not style_prompt or image_service is None or getattr(image_service, "is_image_generation_disabled", False):
        return None
    if not spend.take():
        failures.append({"page": index, "reason": "budget_exhausted"})
        return None
    from models.image_prompt import ImagePrompt
    from services.generation_core.assets.illustration_prompt import (
        build_illustration_prompt, clean_scene_prompt, clean_slide_content,
    )

    scene = clean_scene_prompt(", ".join([subject, *labels]))
    prompt = build_illustration_prompt(scene, clean_slide_content(content), image_service)
    asset = await spend.bounded(image_service.generate_image(
        ImagePrompt(prompt=prompt, theme_prompt=style_prompt)))
    path = getattr(asset, "path", None)
    if not isinstance(path, str) or not await asyncio.to_thread(Path(path).is_file):
        failures.append({"page": index, "reason": "illustration_unavailable"})
        return None
    return _file_slot(path, page_index=index, role="scene", kind="illustration",
                      alt=alt, bbox_hint=SCENE_BBOX_HINT, origin="illustration")


async def _icon_slots(pages, *, pack_key, context, color, spend, receipt):
    """One icon slot per planned node, resolved through the icon pack."""
    from services.generation_core.assets.icon_pack import resolve_icons
    from services.generation_core.assets.icon_pack.sheet import MAX_CONCEPTS_PER_SHEET
    from services.generation_core.assets.icon_pack.service import IconResolution
    from services.generation_core.assets.icon_pack.store import concept_key

    labels = list(dict.fromkeys(label for _index, nodes in pages for label in nodes))
    if not labels:
        return []
    common = dict(pack_key=pack_key, color=color,
                  style_prompt=getattr(context, "style_prompt", None),
                  settings=getattr(context, "settings", None),
                  provider=getattr(context, "provider", None),
                  app_data=getattr(context, "app_data", None))
    resolution = await resolve_icons(labels, generate=False, **common)
    missing = list(resolution.missing)
    sheets = -(-len(missing) // MAX_CONCEPTS_PER_SHEET) if missing else 0
    affordable = min(sheets, spend.left) if context is not None else 0
    generated = IconResolution()
    if affordable and spend.take(affordable):
        wanted = missing[: affordable * MAX_CONCEPTS_PER_SHEET]
        await spend.bounded(resolve_icons(wanted, generate=True, result=generated, **common))
        resolution.icons.update(generated.icons)
    receipt.update(requested=len(labels), resolved=len(resolution.icons),
                   sheetsAllocated=affordable,
                   sheetsAttempted=generated.sheets_attempted,
                   sheetsSucceeded=generated.sheets_generated, sheetsFailed=generated.sheets_failed,
                   failures=sorted(generated.sheet_failures, key=lambda failure: failure["sheet"]),
                   origins=sorted({icon.source for icon in resolution.icons.values()}))
    slots = []
    for index, nodes in pages:
        for label in dict.fromkeys(nodes):
            icon = resolution.icons.get(concept_key(label))
            if icon is None:
                continue
            slots.append(_file_slot(icon.path, page_index=index, role="figure-icon", kind="icon",
                                    alt=f"icon: {label}", concept=label,
                                    bbox_hint=(0.0, 0.0, ICON_EDGE_HINT, ICON_EDGE_HINT),
                                    origin=icon.source))
    return slots


async def build_asset_offer(*, outline, brand, style, roles: Sequence[str], instructions: Optional[str],
                            budget: int, timeout_seconds: float, image_service=None,
                            settings=None) -> AssetOffer:
    """Plan and produce one deck's illustrations and icons; never raises for a provider."""
    from models.authored_illustration_plan import illustrations_are_prohibited
    from services.generation_core.assets.infographic_assets import (
        DEFAULT_PACK_KEY, build_infographic_asset_context,
    )
    from services.generation_core.assets.infographic_plan import resolve_infographic_plan
    from services.generation_core.authoring.author_slide import EXACT_RECONSTRUCTION, normalize_reference_mode
    from services.image_generation_service import image_request_scope, observe_image_provider_calls

    receipt: dict = {"budget": budget, "timeoutSeconds": timeout_seconds}
    if illustrations_are_prohibited(instructions):
        return AssetOffer(receipt={**receipt, "status": "prohibited_by_instructions"})
    if normalize_reference_mode(getattr(style, "reference_mode", None)) == EXACT_RECONSTRUCTION:
        return AssetOffer(receipt={**receipt, "status": "exact_reconstruction"})
    with svg_outline_spans.span("plan_llm"):
        plan = await resolve_infographic_plan(outline, brand, style, roles=list(roles),
                                              instructions=instructions)
    if plan is None:
        return AssetOffer(receipt={**receipt, "status": "plan_unavailable"})
    context = build_infographic_asset_context(style=style, brand=brand, instructions=instructions,
                                              image_service=image_service, settings=settings)
    spend = _Spend(budget, timeout_seconds)
    contents = [slide.content for slide in outline.slides]
    scenes = [slide for slide in plan.slides if slide.archetype == "scene"]
    figures = [(slide.index, [node.label for node in slide.nodes]) for slide in plan.slides
               if slide.has_figure and slide.archetype != "scene" and slide.nodes]
    failures: list[dict] = []
    # Scenes spend first, in page order; the icon sheets take what is left.
    async def scene(slide):
        try:
            with image_request_scope(f"scene-{slide.index}"):
                return await _scene_slot(slide.index, slide, contents[slide.index], style=style,
                                         context=context, image_service=image_service, spend=spend,
                                         failures=failures)
        except Exception as error:  # noqa: BLE001 - a picture is optional
            failures.append({"page": slide.index, "reason": type(error).__name__})
            return None

    with observe_image_provider_calls() as scene_calls, svg_outline_spans.span("scenes"):
        scene_slots = await asyncio.gather(*(scene(slide) for slide in scenes))
    icon_receipt: dict = {}
    style_id = (getattr(style, "id", None) or "").strip() or DEFAULT_PACK_KEY
    try:
        with observe_image_provider_calls() as icon_calls, svg_outline_spans.span("icon_sheets"):
            icon_slots = await _icon_slots(figures, pack_key=getattr(context, "pack_key", style_id),
                                           context=context, spend=spend, receipt=icon_receipt,
                                           color=getattr(brand, "primary", None) or "#1E5EFF")
    except Exception as error:  # noqa: BLE001 - icons are optional
        LOGGER.warning("[svg] asset offer icon resolution failed: %s", type(error).__name__)
        icon_slots, icon_receipt = [], {"error": type(error).__name__}
        failures.append({"kind": "icon_resolution", "reason": type(error).__name__})
    if icon_receipt:
        icon_receipt.update(sheetsSpent=icon_calls.entered, providerCalls=icon_calls.entered)
    failures.extend({"kind": "icon_sheet", **failure} for failure in icon_receipt.get("failures", []))
    slots = tuple(slot for slot in scene_slots if slot is not None) + tuple(icon_slots)
    receipt.update(status="resolved", scenesPlanned=len(scenes), figurePages=len(figures),
                   illustrations=sum(slot.kind == "illustration" for slot in slots),
                   icons=sum(slot.kind == "icon" for slot in slots), icon=icon_receipt,
                   providerCalls=scene_calls.entered + icon_calls.entered,
                   budgetAllocatedCalls=spend.calls, timeouts=spend.timeouts, failures=failures,
                   transparentProvider=context is not None)
    return AssetOffer(slots=slots, receipt=receipt)


__all__ = ["AssetOffer", "AssetSlot", "build_asset_offer"]
