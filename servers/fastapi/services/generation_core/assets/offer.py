"""AssetOffer: the pictures a deck plans before any page is authored.

Format-neutral. The builder reuses the deck-scoped infographic plan to decide
which pages are scenes (one generated illustration each) and which pages carry
a figure whose nodes get an icon, then produces the files: a transparent scene
cutout (or, without an alpha-capable provider, an ordinary illustration from
the style's illustration prompt) and icons: the icon the model picked for each
label from the style's vendored family (``icon_pack/families.py``), else the
bundled library's vector icon for it. It returns slots that
name files on disk; how a slot is placed on a page is the driver's business
(``drivers/svg/asset_slots.py`` for SVG).

Cost and time are bounded by the runtime profile: ``illustration_budget`` is
the number of image-provider calls the offer may spend (one per scene) and
``illustration_timeout_seconds`` bounds each call. A
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
SlotKind = Literal["source", "illustration", "icon", "spot"]
#: Normalized (x, y, width, height) placement hint on the page canvas.
SCENE_BBOX_HINT = (0.55, 0.18, 0.4, 0.64)
ICON_EDGE_HINT = 0.06


@dataclass(frozen=True)
class AssetSlot:
    """One planned picture for one page. ``path`` is the file the slot shows."""

    page_index: int
    role: str  # "scene" | "cover" | "figure-icon" | "figure-spot" | "comparison-before" | "comparison-after"
    kind: SlotKind
    alt: str
    path: str
    media_type: str  # image/png | image/jpeg | image/svg+xml
    sha256: str
    width: int
    height: int
    bbox_hint: Optional[tuple[float, float, float, float]] = None
    concept: Optional[str] = None
    origin: str = ""  # cutout | illustration | library | cached | generated | <icon family> | model-vector
    #: Ink the vector icon was painted with; ``None`` for offers stored before it was carried.
    ink: Optional[str] = None

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


_FALLBACK_ICON_INK = "#1E5EFF"


def icon_ink(brand, style) -> str:
    """The deck accent icons are painted with: the author's own (an explicit brand colour wins)."""
    from services.generation_core.authoring.author_slide_design_system import resolve_style_primary

    return resolve_style_primary(brand, style) or _FALLBACK_ICON_INK


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


def _is_library_vector(icon) -> bool:
    return icon.source == "library" and Path(icon.path).suffix.lower() == ".svg"


async def _family_icons(labels, *, color, receipt, family_id=None) -> dict[str, Path]:
    """Icons the model picked for this deck's labels from one vendored family, as icon files.

    One deck-scoped LLM call chooses names from the family's list; the icons
    are composed from the vendored geometry. Fails open: any failure leaves
    every label to the existing resolution below.
    """
    from services.generation_core.assets.icon_pack import families
    from utils.asset_directory_utils import get_images_directory
    from utils.llm_calls import select_deck_icons as icon_selection

    labels = list(dict.fromkeys(labels))
    if not labels:
        return {}
    family_id = family_id or families.DEFAULT_FAMILY
    try:
        family = families.load_family(family_id)
    except (OSError, ValueError, KeyError) as error:
        receipt.update(status="unavailable", family=family_id, reason=type(error).__name__)
        return {}
    receipt.update(family=family.id, version=family.version, requested=len(labels))
    picks = await icon_selection.select_deck_icons(labels, family.names)
    if picks is None:
        receipt.update(status="selection_failed", selected=0)
        return {}
    directory = Path(get_images_directory()) / "icon-families" / family.id
    paths: dict[str, Path] = {}
    for label, (main, badge) in picks.items():
        try:
            paths[label] = await asyncio.to_thread(families.write_icon, main, badge, color=color,
                                                   directory=directory, family_id=family.id)
        except (OSError, ValueError) as error:
            receipt.setdefault("failures", []).append({"label": label, "reason": type(error).__name__})
    receipt.update(status="selected", selected=len(paths),
                   badges=sum(1 for label in paths if picks[label][1] is not None),
                   distinctMains=len({picks[label][0] for label in paths}))
    return paths


async def _icon_slots(pages, *, pack_key, context, color, receipt, preselected=None, family_origin="family"):
    """One icon slot per planned node: the deck's family picks first, then the bundled library.

    ``preselected`` maps labels to the family icon files picked for this deck;
    only the labels it does not cover go through the icon pack, and only its
    bundled vector library icons are offered. No icon is generated by an image
    provider, and a cached sprite-sheet PNG one once made is not offered
    either: those were raster, uneditable and in another style than the
    deck's vector icons.
    """
    from services.generation_core.assets.icon_pack import resolve_icons
    from services.generation_core.assets.icon_pack.service import IconResolution
    from services.generation_core.assets.icon_pack.store import concept_key

    labels = list(dict.fromkeys(label for _index, nodes in pages for label in nodes))
    if not labels:
        return []
    preselected = dict(preselected or {})
    remaining = [label for label in labels if label not in preselected]
    common = dict(pack_key=pack_key, color=color,
                  style_prompt=getattr(context, "style_prompt", None),
                  settings=getattr(context, "settings", None),
                  provider=getattr(context, "provider", None),
                  app_data=getattr(context, "app_data", None))
    resolution = await resolve_icons(remaining, generate=False, **common) if remaining else IconResolution()
    resolution.icons = {key: icon for key, icon in resolution.icons.items() if _is_library_vector(icon)}
    receipt.update(requested=len(labels), resolved=len(resolution.icons) + len(preselected),
                   family=len(preselected),
                   origins=sorted({icon.source for icon in resolution.icons.values()}
                                  | ({family_origin} if preselected else set())))
    slots = []
    for index, nodes in pages:
        for label in dict.fromkeys(nodes):
            if label in preselected:
                slots.append(_file_slot(preselected[label], page_index=index, role="figure-icon", kind="icon",
                                        alt=f"icon: {label}", concept=label,
                                        bbox_hint=(0.0, 0.0, ICON_EDGE_HINT, ICON_EDGE_HINT),
                                        origin=family_origin, ink=color))
                continue
            icon = resolution.icons.get(concept_key(label))
            if icon is None:
                continue
            slots.append(_file_slot(icon.path, page_index=index, role="figure-icon", kind="icon",
                                    alt=f"icon: {label}", concept=label,
                                    bbox_hint=(0.0, 0.0, ICON_EDGE_HINT, ICON_EDGE_HINT),
                                    origin=icon.source, ink=color))
    return slots


async def build_asset_offer(*, outline, brand, style, roles: Sequence[str], instructions: Optional[str],
                            budget: int, timeout_seconds: float, image_service=None,
                            settings=None, icons_only: bool = False,
                            illustration_mode: str = "off",
                            illustration_parts: Optional[Sequence[str]] = None,
                            template_picture_policy: Optional[dict] = None,
                            projection_revision: str = "svg-asset-projection/v2") -> AssetOffer:
    """Plan and produce one deck's illustrations and icons; never raises for a provider.

    ``icons_only`` is the no-image-provider mode: the same deck plan decides
    which pages carry a figure, but every scene is demoted to its HTML-only
    fallback, no illustration is requested, and each node gets
    its bundled library vector icon when the library has one. It costs the
    plan's one LLM call and zero image-provider calls.

    In every mode the figure labels first get the deck's family picks (one more
    LLM call, run while the scenes are made, ``iconSelection`` in the receipt);
    only the labels it leaves uncovered go through the icon pack.

    ``illustration_mode`` ``vector`` or ``hybrid`` has the model draw the deck's
    pictures as vector SVG (``vector_offer.py``): scenes (no image provider
    needed, no scene ceiling), cover/section/closing pictures, and, when
    ``illustration_parts`` names them, step pictures and before/after pairs
    (default: cover and scene only). In ``hybrid`` a scene marked as a photograph still
    takes the image-provider path when a provider is configured. A node that
    got a step picture gets no icon.
    """
    from services.generation_core.drivers.svg.icon_flatten import validate_asset_projection_revision
    validate_asset_projection_revision(projection_revision)
    from models.authored_illustration_plan import illustrations_are_prohibited
    from models.authored_infographic_plan import without_scenes
    from services.generation_core.assets.infographic_assets import (
        DEFAULT_PACK_KEY, build_infographic_asset_context,
    )
    from services.generation_core.assets.infographic_plan import resolve_infographic_plan
    from utils.llm_calls.author_slide_effort import EXACT_RECONSTRUCTION
    from utils.llm_calls.author_slide_options import normalize_reference_mode
    from services.image_generation_service import image_request_scope, observe_image_provider_calls

    policy_kwargs = {}
    if template_picture_policy is not None:
        from services.generation_core.assets.vector_offer import validate_template_picture_policy

        template_picture_policy = validate_template_picture_policy(template_picture_policy)
        policy_kwargs = {"template_picture_policy": template_picture_policy}
        illustration_mode = "vector"
        illustration_parts = template_picture_policy["parts"]
        icons_only, image_service = True, None
    # ``budget`` is the image-provider call budget (zero when icons only). Vector drawing
    # calls are bounded and recorded separately (the vector receipt's callLimit and
    # physicalCalls), so a template-pictures offer reports budget 0 while it may draw.
    receipt: dict = {"budget": 0 if icons_only else budget, "timeoutSeconds": timeout_seconds}
    if template_picture_policy is not None:
        receipt["templatePicturePolicy"] = template_picture_policy
    if icons_only:
        receipt["iconsOnly"] = True
    if illustrations_are_prohibited(instructions):
        return AssetOffer(receipt={**receipt, "status": "prohibited_by_instructions"})
    if normalize_reference_mode(getattr(style, "reference_mode", None)) == EXACT_RECONSTRUCTION:
        return AssetOffer(receipt={**receipt, "status": "exact_reconstruction"})
    vector = illustration_mode in {"vector", "hybrid"}
    if vector:
        receipt["illustrationMode"] = illustration_mode
    # In vector mode a scene is drawn only when the parts name it; otherwise the plan is made
    # as in off mode (the provider scene guide, the scene ceiling) and its scenes are demoted
    # to their icon figures, so a parts list such as ``spots`` adds pictures without losing
    # icons. Hybrid mode keeps its vector plan whatever the parts: its photograph scenes go to
    # the image provider, and the parts only pick what the model draws.
    scene_part = illustration_parts is None or "scene" in illustration_parts
    demote_scenes = illustration_mode == "vector" and not scene_part
    with svg_outline_spans.span("plan_llm"):
        plan = await resolve_infographic_plan(outline, brand, style, roles=list(roles),
                                              instructions=instructions,
                                              vector_illustrations=vector and not demote_scenes,
                                              **policy_kwargs)
    if plan is None:
        return AssetOffer(receipt={**receipt, "status": "plan_unavailable"})
    if demote_scenes:
        plan = without_scenes(plan)
    if icons_only and vector:
        # No image provider: every picture is drawn as vector SVG instead.
        context = None
        spend = _Spend(0, timeout_seconds)
    elif icons_only:
        # No picture can be made: a scene page is drawn as its HTML figure
        # instead, and nothing below may reach an image provider.
        plan = without_scenes(plan)
        context = None
        spend = _Spend(0, timeout_seconds)
    else:
        context = build_infographic_asset_context(style=style, brand=brand, instructions=instructions,
                                                  image_service=image_service, settings=settings)
        spend = _Spend(budget, timeout_seconds)
    contents = [slide.content for slide in outline.slides]
    all_scenes = [slide for slide in plan.slides if slide.archetype == "scene"]
    # In vector mode a scene is drawn unless hybrid mode sends a photograph to the provider.
    provider_scenes = (all_scenes if not vector else [
        slide for slide in all_scenes
        if illustration_mode == "hybrid" and context is not None and slide.depiction == "photo"])
    scenes = provider_scenes
    figures = [(slide.index, [node.label for node in slide.nodes]) for slide in plan.slides
               if slide.has_figure and slide.archetype != "scene" and slide.nodes]
    failures: list[dict] = []
    # Scenes spend the image-provider budget, in page order.
    async def scene(slide):
        try:
            with image_request_scope(f"scene-{slide.index}"):
                return await _scene_slot(slide.index, slide, contents[slide.index], style=style,
                                         context=context, image_service=image_service, spend=spend,
                                         failures=failures)
        except Exception as error:  # noqa: BLE001 - a picture is optional
            failures.append({"page": slide.index, "reason": type(error).__name__})
            return None

    family_receipt: dict = {}
    ink = icon_ink(brand, style)
    family_id = getattr(style, "icon_family", None)
    figure_labels = [label for _index, nodes in figures for label in nodes]
    family_task = asyncio.ensure_future(_family_icons(
        figure_labels, color=ink, receipt=family_receipt,
        family_id=family_id if isinstance(family_id, str) and family_id else None))
    vector_slots: list[AssetSlot] = []
    vector_receipt: dict = {}
    vector_task = None
    if vector:
        from services.generation_core.assets.vector_offer import build_vector_drawings

        vector_task = asyncio.ensure_future(build_vector_drawings(
            plan, outline=outline, roles=roles, brand=brand, style=style, timeout_seconds=timeout_seconds,
            projection_revision=projection_revision,
            vector_scene_pages={slide.index for slide in all_scenes} - {slide.index for slide in scenes},
            **({**policy_kwargs, "budget": budget} if policy_kwargs else {}),
            **({"parts": tuple(illustration_parts)} if illustration_parts else {})))
    with observe_image_provider_calls() as scene_calls, svg_outline_spans.span("scenes"):
        scene_slots = await asyncio.gather(*(scene(slide) for slide in scenes))
        if vector_task is not None:
            try:
                vector_slots, vector_receipt = await vector_task
            except Exception as error:  # noqa: BLE001 - pictures are optional
                LOGGER.warning("[svg] vector drawings failed: %s", type(error).__name__)
                vector_receipt = {"status": "error", "reason": type(error).__name__}
    icon_receipt: dict = {}
    style_id = (getattr(style, "id", None) or "").strip() or DEFAULT_PACK_KEY
    try:
        with observe_image_provider_calls() as icon_calls, svg_outline_spans.span("icon_sheets"):
            try:
                preselected = await family_task
            except Exception as error:  # noqa: BLE001 - icons are optional
                LOGGER.warning("[svg] deck icon selection failed: %s", type(error).__name__)
                preselected = {}
                family_receipt.update(status="error", reason=type(error).__name__)
            icon_slots = await _icon_slots(figures, pack_key=getattr(context, "pack_key", style_id),
                                           context=context, receipt=icon_receipt,
                                           color=ink,
                                           preselected=preselected,
                                           family_origin=family_receipt.get("family", "family"))
    except Exception as error:  # noqa: BLE001 - icons are optional
        LOGGER.warning("[svg] asset offer icon resolution failed: %s", type(error).__name__)
        icon_slots, icon_receipt = [], {"error": type(error).__name__}
        failures.append({"kind": "icon_resolution", "reason": type(error).__name__})
    if icon_receipt:
        icon_receipt.update(providerCalls=icon_calls.entered)
    # A node that got its own step picture keeps that picture instead of an icon.
    spotted = {(slot.page_index, slot.concept) for slot in vector_slots if slot.kind == "spot" and slot.concept}
    icon_slots = [slot for slot in icon_slots if (slot.page_index, slot.concept) not in spotted]
    slots = (tuple(slot for slot in scene_slots if slot is not None) + tuple(vector_slots)
             + tuple(icon_slots))
    receipt.update(status="library_icons_only" if icons_only and not vector else "resolved",
                   scenesPlanned=len(all_scenes), figurePages=len(figures),
                   illustrations=sum(slot.kind == "illustration" for slot in slots),
                   icons=sum(slot.kind == "icon" for slot in slots), icon=icon_receipt,
                   iconSelection=family_receipt,
                   **({"vectorDrawings": vector_receipt,
                       "spots": sum(slot.kind == "spot" for slot in slots)} if vector else {}),
                   providerCalls=scene_calls.entered + icon_calls.entered,
                   budgetAllocatedCalls=spend.calls, timeouts=spend.timeouts, failures=failures,
                   transparentProvider=context is not None)
    if icons_only:
        receipt.update(planLlmCalls=1, reason="image_generation_unavailable")
    return AssetOffer(slots=slots, receipt=receipt)


__all__ = ["AssetOffer", "AssetSlot", "build_asset_offer"]
