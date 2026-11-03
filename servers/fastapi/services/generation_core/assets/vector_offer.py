"""The deck's model-drawn vector pictures, planned from its infographic plan.

With ``SVG_ILLUSTRATION_MODE`` set to ``vector`` or ``hybrid`` the asset offer
asks the model to draw, as contract-subset SVG bound to the deck palette:

- a cover picture for each cover, section and closing page (at most three);
- one picture per ``scene`` page (in ``hybrid``, a scene the plan marked as a
  photograph keeps the image-provider path when one is configured);
- a matched set of small pictures for the steps of a process, cycle,
  timeline or roadmap page with 3-6 nodes (kind ``spot``, one per node);
- a matched before/after pair for a ``before_after`` page (kind ``spot``).

Calls run concurrently, at most ``MAX_CALLS`` per deck. Every drawing is
validated (``vector_illustration.py``); a single picture whose call or
validation failed is asked for once more, and anything still rejected is
dropped with its reason in the receipt. Nothing here raises for a provider.
"""
from __future__ import annotations

import asyncio
import logging
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from services.generation_core.assets.offer import AssetSlot, SCENE_BBOX_HINT
from utils.hashing import sha256_hex

LOGGER = logging.getLogger(__name__)

MAX_CALLS = 12
#: Picture kinds a deck may get. ``SVG_ILLUSTRATION_PARTS`` chooses among them;
#: the default keeps the two that did not add page failures when measured
#: (cover and scene pictures). Step pictures and before/after pairs crowd
#: text-dense pages: 4 of 8 step-picture pages and 1 of 3 pair pages failed
#: the author's own checks in the 2026-11-03 measurement.
ALL_PARTS = ("cover", "scene", "spots", "pairs")
DEFAULT_PARTS = ("cover", "scene")
MAX_COVER_PAGES = 3
COVER_ROLES = frozenset({"COVER", "SECTION", "CLOSING"})
SPOT_ARCHETYPES = frozenset({"process", "cycle", "timeline", "roadmap"})
PAIR_ARCHETYPES = frozenset({"before_after"})
SCENE_SIZE = (512, 460)
COVER_SIZE = (560, 520)
SPOT_SIZE = (220, 180)
PAIR_SIZE = (440, 360)
_CONTEXT_CHARS = 400


@dataclass(frozen=True)
class _Job:
    priority: int
    page: int
    kind: str  # scene | cover | spots | pair
    request: object
    roles: tuple[tuple[str, str, str, Optional[str]], ...]  # (drawing id, slot role, alt, concept)


def deck_palette(brand, style) -> tuple[dict[str, str], str]:
    """The deck colours by role and the slide background colour."""
    from utils.llm_calls.draw_vector_illustrations import palette_roles

    background = (getattr(style, "background_color", None) or getattr(style, "preview_bg", None) or "#FFFFFF")
    accent = (getattr(brand, "primary", None) if getattr(brand, "primary_is_explicit", False)
              else getattr(style, "preview_accent", None)) or getattr(brand, "primary", None)
    colors = [color for color in (accent, *(getattr(style, "preview_palette", None) or ()),
                                  getattr(style, "primary_color", None), background, "#FFFFFF")
              if isinstance(color, str)]
    return palette_roles(colors, accent=accent, background=background), background.upper()


def _first_line(content: str) -> str:
    for line in (content or "").splitlines():
        text = line.strip().lstrip("#").strip()
        if text:
            return text[:120]
    return ""


def plan_jobs(plan, contents: Sequence[str], roles: Sequence[str], *, topic: str, palette: dict[str, str],
              background: str, vector_scene_pages: set[int], parts: Sequence[str] = DEFAULT_PARTS) -> list[_Job]:
    """Every drawing call this deck would make, highest priority first, capped at ``MAX_CALLS``."""
    from utils.llm_calls.draw_vector_illustrations import DrawingRequest, DrawingSpec

    def request(kind, page, specs, context="", extra=()):
        return DrawingRequest(kind=kind, drawings=tuple(specs), topic=topic, palette=palette,
                              background=background, semantic_slot=f"vector_drawing_p{page}_{kind}",
                              context=context, extra=tuple(extra))

    jobs: list[_Job] = []
    parts = set(parts)
    cover_pages = ([index for index, role in enumerate(roles) if (role or "").strip().upper() in COVER_ROLES]
                   if "cover" in parts else [])
    for page in cover_pages[:MAX_COVER_PAGES]:
        drawing = f"cover-p{page}"
        subject = _first_line(contents[page]) or topic
        jobs.append(_Job(0, page, "cover", request("cover", page, [DrawingSpec(drawing, *COVER_SIZE, subject)],
                                                    context=contents[page][:_CONTEXT_CHARS]),
                         ((drawing, "cover", f"cover picture: {subject}", None),)))
    for slide in plan.slides:
        page, labels = slide.index, [node.label for node in slide.nodes]
        context = contents[page][:_CONTEXT_CHARS]
        if slide.archetype == "scene" and page in vector_scene_pages and "scene" in parts:
            drawing = f"scene-p{page}"
            subject = "; ".join(part for part in (slide.takeaway, ", ".join(labels)) if part)
            jobs.append(_Job(1, page, "scene", request("scene", page, [DrawingSpec(drawing, *SCENE_SIZE, subject)],
                                                        context=context),
                             ((drawing, "scene", f"picture of this slide's message: {slide.takeaway or labels[0]}",
                               None),)))
        elif slide.archetype in PAIR_ARCHETYPES and labels and "pairs" in parts:
            before = "; ".join(f"{node.label} {node.before}" for node in slide.nodes if node.before) or labels[0]
            after = "; ".join(f"{node.label} {node.after}" for node in slide.nodes if node.after) or labels[-1]
            specs = [DrawingSpec(f"pair-p{page}-before", *PAIR_SIZE, f"As-Is (current state): {before}"),
                     DrawingSpec(f"pair-p{page}-after", *PAIR_SIZE, f"To-Be (target state): {after}")]
            jobs.append(_Job(2, page, "pair", request("pair", page, specs, context=context),
                             ((specs[0].id, "comparison-before", "As-Is picture: place it on the current-state side",
                               None),
                              (specs[1].id, "comparison-after", "To-Be picture: place it on the target-state side",
                               None))))
        elif slide.archetype in SPOT_ARCHETYPES and 3 <= len(labels) <= 6 and "spots" in parts:
            specs = [DrawingSpec(f"spot-p{page}-n{number}", *SPOT_SIZE,
                                 f"step {number}: {node.label}" + (f" ({node.desc})" if node.desc else ""))
                     for number, node in enumerate(slide.nodes, start=1)]
            jobs.append(_Job(3, page, "spots", request("spots", page, specs, context=context),
                             tuple((spec.id, "figure-spot",
                                    f"small picture for '{node.label}': place it directly above or beside that "
                                    "item's text", node.label)
                                   for spec, node in zip(specs, slide.nodes, strict=True))))
    jobs.sort(key=lambda job: (job.priority, job.page))
    return jobs[:MAX_CALLS]


def _store(svg: str) -> Path:
    from utils.asset_directory_utils import get_images_directory

    payload = svg.encode("utf-8")
    target = Path(get_images_directory()) / "vector-illustrations" / f"{sha256_hex(payload)}.svg"
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    return target


async def _run(job: _Job, *, palette: Sequence[str], ink: str, call_seconds: float,
               receipt: dict) -> list[AssetSlot]:
    from dataclasses import replace

    from services.generation_core.assets.vector_illustration import validate_drawing
    from utils.llm_calls import draw_vector_illustrations as drawing_calls

    specs = {spec.id: spec for spec in job.request.drawings}
    accepted: dict[str, object] = {}
    for attempt in range(2):
        missing = [spec for spec in job.request.drawings if spec.id not in accepted]
        if not missing:
            break
        request = job.request if attempt == 0 else replace(
            job.request, drawings=tuple(missing), semantic_slot=job.request.semantic_slot + "_retry")
        receipt["calls"] += 1
        receipt["retries"] += attempt
        try:
            answer = await asyncio.wait_for(drawing_calls.draw_vector_illustrations(request), timeout=call_seconds)
        except asyncio.TimeoutError:
            receipt["timeouts"] += 1
            answer = None
        if answer is None:
            receipt["rejected"]["call_failed"] += len(missing)
        else:
            for spec in missing:
                svg = answer.get(spec.id)
                if svg is None:
                    receipt["rejected"]["missing"] += 1
                    continue
                try:
                    accepted[spec.id] = validate_drawing(svg, width=spec.width, height=spec.height,
                                                         palette=palette, ink=ink)
                except ValueError as error:
                    receipt["rejected"][str(error).split(":", 1)[0]] += 1
        # A set keeps the members it got; only a single picture is worth asking twice.
        if len(job.request.drawings) > 1:
            break
    slots = []
    for drawing_id, role, alt, concept in job.roles:
        drawing = accepted.get(drawing_id)
        if drawing is None:
            continue
        path = await asyncio.to_thread(_store, drawing.svg)
        spec = specs[drawing_id]
        kind = "illustration" if job.kind in {"scene", "cover"} else "spot"
        slots.append(AssetSlot(
            page_index=job.page, role=role, kind=kind, alt=alt, path=str(path), media_type="image/svg+xml",
            sha256=sha256_hex(path.read_bytes()), width=spec.width, height=spec.height,
            bbox_hint=SCENE_BBOX_HINT if job.kind == "scene" else None, concept=concept, origin="model-vector"))
        receipt["accepted"] += 1
        receipt["fillRatios"].append(drawing.fill_ratio)
    return slots


async def build_vector_drawings(plan, *, outline, roles: Sequence[str], brand, style, timeout_seconds: float,
                                vector_scene_pages: set[int],
                                parts: Sequence[str] = DEFAULT_PARTS) -> tuple[list[AssetSlot], dict]:
    """Draw this deck's vector pictures; returns their slots and a receipt. Never raises."""
    receipt: dict = {"calls": 0, "retries": 0, "timeouts": 0, "accepted": 0, "requested": 0,
                     "rejected": Counter(), "fillRatios": [], "jobs": {}}
    try:
        palette, background = deck_palette(brand, style)
        contents = [slide.content for slide in outline.slides]
        jobs = plan_jobs(plan, contents, list(roles), topic=getattr(brand, "topic", "") or "",
                         palette=palette, background=background, vector_scene_pages=vector_scene_pages,
                         parts=parts)
    except (ValueError, IndexError, AttributeError) as error:
        LOGGER.warning("[svg] vector drawing plan failed: %s", type(error).__name__)
        return [], {**receipt, "rejected": {}, "status": "plan_failed", "reason": type(error).__name__}
    receipt["requested"] = sum(len(job.request.drawings) for job in jobs)
    receipt["parts"] = sorted(set(parts))
    receipt["jobs"] = dict(Counter(job.kind for job in jobs))
    receipt["palette"] = palette
    colors = list(dict.fromkeys(palette.values()))

    async def run(job):
        try:
            return await _run(job, palette=colors, ink=palette["DARK"], call_seconds=timeout_seconds,
                              receipt=receipt)
        except Exception as error:  # noqa: BLE001 - a picture is optional
            LOGGER.warning("[svg] vector drawing failed page=%s kind=%s: %s", job.page, job.kind,
                           type(error).__name__)
            receipt["rejected"]["error"] += len(job.request.drawings)
            return []

    results = await asyncio.gather(*(run(job) for job in jobs))
    slots = [slot for group in results for slot in group]
    ratios = receipt.pop("fillRatios")
    receipt.update(status="drawn", rejected=dict(receipt["rejected"]),
                   meanFillRatio=round(sum(ratios) / len(ratios), 3) if ratios else None)
    return slots, receipt


__all__ = ["ALL_PARTS", "COVER_ROLES", "DEFAULT_PARTS", "MAX_CALLS", "PAIR_ARCHETYPES", "SPOT_ARCHETYPES", "build_vector_drawings",
           "deck_palette", "plan_jobs"]
