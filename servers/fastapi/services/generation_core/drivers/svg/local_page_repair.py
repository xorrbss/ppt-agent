"""Provider-free second chances a measured page gets before it is classified.

Two measured failures used to decide a page's fate on their first reading:

* ``measurement_incomplete`` -- the renderer's probe did not finish (an error,
  fonts not ready, no text read back). It is a content kind
  (``quality/page_failure_kind.py``): unmeasured visibility is unproven
  completeness, so a designed page went to the text fallback, or bought a
  provider repair, on what may be a transient render. :func:`remeasure_incomplete`
  renders the same bytes once more first; the second reading is the page's.
* ``text_clipped`` -- a text's rendered range escapes its clipping box. In the
  SVG contract (no nested ``<svg>``, no ``clipPath``) that box is the canvas, so
  the defect is a text pushed over the page edge. :func:`text_clip_repair`
  proposes one deterministic candidate -- move each clipped ``<text>`` back
  inside the canvas and, only when it is wider or taller than the canvas,
  shrink it no further than the page's body font floor -- which the caller
  renders and measures with the established renderer. If text still clips
  after that, the page lost visible content and is classed as such.

Neither ever calls a provider; each records what it did on the attempt. The
thresholds below are the only ones this module uses.
"""
from __future__ import annotations

import math
import re
from collections.abc import Awaitable, Callable, Mapping
from xml.etree import ElementTree as ET

#: The failure kinds this module answers.
MEASUREMENT_INCOMPLETE = "measurement_incomplete"
TEXT_CLIPPED = "text_clipped"
#: How far inside the canvas edge a moved text's measured box is placed. The
#: renderer's clip check tolerates 0.5 px (``utils/slide_capture.glyphClip``)
#: and its off-canvas check 0.25 px (``services/svg_renderer``); one pixel
#: clears both without a visible change of composition.
CLIP_REPAIR_EDGE_INSET_PX = 1.0
#: Minimum overlap, per axis, that counts as a moved text covering another text
#: it did not cover before (the same 0.5 px the readability repair uses).
CLIP_REPAIR_OVERLAP_PX = 0.5
#: Server furniture is never moved; a template page's body group is authored.
_SYSTEM_PREFIX = "svg-system-"
_TEMPLATE_BODY_ID = "svg-system-template-body"
_NUMBER = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
_TRANSFORM = re.compile(r"\s*(translate|scale)\s*\(([^)]*)\)\s*,?")


async def remeasure_incomplete(render_once: Callable[[], Awaitable[object]]):
    """Render once; when it failed ``measurement_incomplete``, render the same bytes once more.

    ``render_once`` returns an ``SvgRenderResult``-like object with a
    ``quality`` dict. The second result is returned with a ``remeasurement``
    record naming the first reading, whatever the second one measured.
    """

    rendered = await render_once()
    if not _has_failure(rendered.quality, MEASUREMENT_INCOMPLETE):
        return rendered
    first = rendered.quality
    again = await render_once()
    again.quality["remeasurement"] = {
        "reason": MEASUREMENT_INCOMPLETE, "provider_calls": 0,
        "first_png_digest": first.get("png_digest"), "first_metrics_digest": first.get("metrics_digest"),
        "measurement_complete": again.quality.get("measurement_complete") is True,
    }
    return again


def _tag(node) -> str:
    return node.tag.rsplit("}", 1)[-1]


def _affine(transform: str | None):
    """``(sx, sy, tx, ty)`` of a translate/scale list, or ``None`` for anything else."""

    result = (1.0, 1.0, 0.0, 0.0)
    text = (transform or "").strip()
    position = 0
    while position < len(text):
        match = _TRANSFORM.match(text, position)
        if match is None:
            return None
        values = [float(item) for item in re.findall(_NUMBER, match[2])]
        if match[1] == "translate" and len(values) in (1, 2):
            item = (1.0, 1.0, values[0], values[1] if len(values) == 2 else 0.0)
        elif match[1] == "scale" and len(values) in (1, 2) and all(value > 0 for value in values):
            item = (values[0], values[-1], 0.0, 0.0)
        else:
            return None
        result = _compose(result, item)
        position = match.end()
    return result


def _compose(outer, inner):
    return (outer[0] * inner[0], outer[1] * inner[1],
            outer[0] * inner[2] + outer[2], outer[1] * inner[3] + outer[3])


def _single(node, name: str) -> float | None:
    """A single-valued coordinate attribute (absent is ``0``), ``None`` when per-glyph."""

    values = re.findall(_NUMBER, node.get(name, "0"))
    if len(values) != 1:
        return None
    value = float(values[0])
    return value if math.isfinite(value) else None


def _format(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _rects(item: Mapping) -> list[tuple[float, float, float, float]]:
    rects = []
    for rect in item.get("text_fragments") or [item.get("bbox") or {}]:
        try:
            x, y = float(rect.get("x", rect.get("left"))), float(rect.get("y", rect.get("top")))
            width, height = float(rect["width"]), float(rect["height"])
        except (TypeError, KeyError, ValueError):
            continue
        if all(math.isfinite(value) for value in (x, y, width, height)) and width > 0 and height > 0:
            rects.append((x, y, x + width, y + height))
    return rects


def _union(rects):
    return (min(r[0] for r in rects), min(r[1] for r in rects), max(r[2] for r in rects), max(r[3] for r in rects))


def _overlaps(a, b) -> bool:
    return (min(a[2], b[2]) - max(a[0], b[0]) > CLIP_REPAIR_OVERLAP_PX
            and min(a[3], b[3]) - max(a[1], b[1]) > CLIP_REPAIR_OVERLAP_PX)


def text_clip_repair(svg: str, quality: Mapping, *, width: float, height: float,
                     metrics: Mapping | None = None) -> dict:
    """One deterministic candidate for every clipped ``<text>``, or why there is none.

    ``quality`` is the page's measurement of ``svg`` and ``metrics`` its
    browser metrics (``quality["browser_metrics"]`` when omitted). The
    candidate is returned under ``svg``; the caller must render and measure it.
    """

    from services.svg_contract import source_digest

    refused = {"status": "not_repairable", "provider_calls": 0}
    failures = quality.get("failures") or []
    clipped = sorted({item.get("qid") for item in failures
                      if isinstance(item, Mapping) and item.get("kind") == TEXT_CLIPPED and item.get("qid")})
    if not clipped:
        return {**refused, "reason": "no_clipped_text"}
    if quality.get("source_digest") != source_digest(svg) or quality.get("measurement_complete") is not True:
        return {**refused, "reason": "measurement_binding_unavailable"}
    floor = quality.get("body_font_floor_px")
    if type(floor) not in (int, float) or not math.isfinite(floor) or floor <= 0:
        return {**refused, "reason": "font_floor_unavailable"}
    elements = (metrics if metrics is not None else quality.get("browser_metrics") or {}).get("elements") or []
    by_qid = {item.get("qid"): item for item in elements if isinstance(item, Mapping) and item.get("qid")}
    root = ET.fromstring(svg)
    nodes = list(root.iter())
    qid_of = {node: f"svg-{index}" for index, node in enumerate(nodes)}
    parents = {child: parent for parent in nodes for child in parent}

    plans = {}
    for qid in clipped:
        if not qid.startswith("svg-") or not qid[4:].isdigit() or int(qid[4:]) >= len(nodes):
            return {**refused, "reason": "element_identity_unavailable"}
        node = nodes[int(qid[4:])]
        while _tag(node) == "tspan" and node in parents:
            node = parents[node]
        if _tag(node) != "text":
            return {**refused, "reason": "clipped_element_not_text"}
        if node in plans:
            continue
        chain, ancestor = [], node
        while ancestor is not None:
            if ((ancestor.get("id") or "").startswith(_SYSTEM_PREFIX)
                    and ancestor.get("id") != _TEMPLATE_BODY_ID):
                return {**refused, "reason": "server_furniture_protected"}
            chain.append(ancestor)
            ancestor = parents.get(ancestor)
        affine = (1.0, 1.0, 0.0, 0.0)
        for element in reversed(chain):
            item = _affine(element.get("transform"))
            if item is None:
                return {**refused, "reason": "transformed_text_unsupported"}
            affine = _compose(affine, item)
        subtree = list(node.iter())
        if any(_tag(child) == "tspan" and child.get("transform") for child in subtree):
            return {**refused, "reason": "transformed_text_unsupported"}
        positions = {}
        for child in subtree:
            for name in ("x", "y"):
                if child is node or child.get(name) is not None:
                    value = _single(child, name)
                    if value is None:
                        return {**refused, "reason": "glyph_positioned_text_unsupported"}
                    positions[(child, name)] = value
        rects = [rect for child in subtree for rect in _rects(by_qid.get(qid_of[child], {}))]
        if not rects:
            return {**refused, "reason": "text_geometry_unavailable"}
        plans[node] = dict(qid=qid_of[node], affine=affine, subtree=subtree, positions=positions,
                           box=_union(rects))

    inset = CLIP_REPAIR_EDGE_INSET_PX
    planned_boxes, changes = {}, []
    for node, plan in plans.items():
        sx, sy, tx, ty = plan["affine"]
        left, top, right, bottom = plan["box"]
        factor = min(1.0, (width - 2 * inset) / (right - left), (height - 2 * inset) / (bottom - top))
        sizes = [(child, float(child.get("font-size"))) for child in plan["subtree"]
                 if child.get("font-size") is not None]
        if factor < 1.0:
            if not any(child is node for child, _ in sizes):
                return {**refused, "reason": "font_size_unavailable"}
            if any(size * factor < floor for _, size in sizes):
                return {**refused, "reason": "readability_floor"}
        anchor_x = sx * plan["positions"][(node, "x")] + tx
        anchor_y = sy * plan["positions"][(node, "y")] + ty
        left, right = anchor_x + (left - anchor_x) * factor, anchor_x + (right - anchor_x) * factor
        top, bottom = anchor_y + (top - anchor_y) * factor, anchor_y + (bottom - anchor_y) * factor
        dx = (inset - left) if left < inset else (width - inset - right) if right > width - inset else 0.0
        dy = (inset - top) if top < inset else (height - inset - bottom) if bottom > height - inset else 0.0
        planned_boxes[node] = (left + dx, top + dy, right + dx, bottom + dy)
        changes.append(dict(node=node, qid=plan["qid"], factor=factor, sizes=sizes,
                            local_dx=dx / sx, local_dy=dy / sy, translate_x=dx, translate_y=dy))

    moved = {qid_of[child] for plan in plans.values() for child in plan["subtree"]}
    for node, box in planned_boxes.items():
        before = plans[node]["box"]
        for qid, item in by_qid.items():
            if qid in moved or not item.get("has_owned_text"):
                continue
            for rect in _rects(item):
                if _overlaps(box, rect) and not _overlaps(before, rect):
                    return {**refused, "reason": "available_region_insufficient"}
        for other, other_box in planned_boxes.items():
            if other is not node and _overlaps(box, other_box):
                return {**refused, "reason": "available_region_insufficient"}

    receipt = []
    for change in changes:
        node, factor = change["node"], change["factor"]
        positions = plans[node]["positions"]
        anchor_x, anchor_y = positions[(node, "x")], positions[(node, "y")]
        for (child, name), value in positions.items():
            anchor, delta = (anchor_x, change["local_dx"]) if name == "x" else (anchor_y, change["local_dy"])
            new = anchor + (value - anchor) * factor + delta
            if new != value:
                child.set(name, _format(new))
        for child, size in change["sizes"]:
            if factor < 1.0:
                child.set("font-size", _format(size * factor))
        if factor < 1.0:
            for child in plans[node]["subtree"]:
                spacing = child.get("letter-spacing")
                if spacing is not None and re.fullmatch(_NUMBER, spacing.strip()):
                    child.set("letter-spacing", _format(float(spacing) * factor))
        receipt.append({"qid": change["qid"], "font_scale": factor,
                        "translate_x": change["translate_x"], "translate_y": change["translate_y"]})
    candidate = ET.tostring(root, encoding="unicode")
    return {"status": "candidate_requires_remeasurement", "svg": candidate, "changes": receipt,
            "font_floor_px": floor, "source_digest_before": source_digest(svg),
            "source_digest_after": source_digest(candidate), "provider_calls": 0}


def _has_failure(quality: object, kind: str) -> bool:
    return isinstance(quality, Mapping) and any(
        isinstance(item, Mapping) and item.get("kind") == kind for item in quality.get("failures") or [])


def still_clipped(quality: object) -> bool:
    """Whether a measurement still reports clipped text."""

    return _has_failure(quality, TEXT_CLIPPED)


__all__ = [
    "CLIP_REPAIR_EDGE_INSET_PX", "CLIP_REPAIR_OVERLAP_PX", "MEASUREMENT_INCOMPLETE", "TEXT_CLIPPED",
    "remeasure_incomplete", "still_clipped", "text_clip_repair",
]
