"""Bounded geometry observations; neither an aesthetic score nor a repair gate."""
from __future__ import annotations

from collections import Counter
import copy
import hashlib
import json
import math
from xml.etree import ElementTree as ET

from services.generation_core.drivers.svg.native_geometry import collect_primitives
from services.svg_contract import source_digest

_ALLOWED = frozenset({"g", "text", "rect", "circle", "ellipse", "line", "polyline", "polygon", "path", "image"})


def _geometry(svg: str):
    if not svg or len(svg) > 2_000_000 or "<!" in svg or "<?" in svg:
        return None
    try:
        root = ET.fromstring(svg)
        width, height = float(root.get("width", "0")), float(root.get("height", "0"))
        if not all(math.isfinite(x) and x > 0 for x in (width, height)):
            return None
        if sum(1 for _ in root.iter()) > 5000:
            return None
        # Work on a copy: stored drawing bytes and publication evidence stay intact.
        root = copy.deepcopy(root)
        for parent in root.iter():
            for child in list(parent):
                if (child.get("id") or "").startswith(("svg-system-", "template-")):
                    parent.remove(child)
        drawing = collect_primitives(root, (0., 0.), {}, _ALLOWED)
        features = Counter()
        units = 0

        def add(kind, box, weight=1):
            nonlocal units
            values = (box.x0 / width, box.y0 / height, box.width / width, box.height / height)
            if not all(math.isfinite(x) for x in values):
                return
            key = (kind, *(round(x * 8) for x in values))
            features[key] += weight
            units += 1

        for shape in drawing.shapes:
            box = shape.box
            if (shape.tag != "image" and not ((shape.fill and shape.fill_alpha > .01)
                    or (shape.stroke and shape.stroke_alpha > .01))):
                continue
            # Full canvas paint and thin perimeter bands are background/chrome.
            if shape.tag != "image" and ((box.width >= width * .9 and box.height >= height * .9)
                    or (box.width >= width * .8 and box.height <= height * .035
                        and (box.y0 <= height * .06 or box.y1 >= height * .94))
                    or (box.height >= height * .8 and box.width <= width * .035
                        and (box.x0 <= width * .06 or box.x1 >= width * .94))):
                continue
            add("image" if shape.tag == "image" else "shape", box)
        for text in drawing.texts:
            if not text.fill:
                continue
            box = text.box()  # approximate glyph geometry, never a readability verdict
            if box.y0 >= height * .90 and text.size <= height * .025:
                continue
            add("text", box, 2)
        serialized = json.dumps(sorted((list(key), count) for key, count in features.items()))
        return features, units, "sha256:" + hashlib.sha256(serialized.encode()).hexdigest()
    except (ET.ParseError, ValueError, TypeError, IndexError, KeyError):
        return None


def _comparison_basis(payload):
    application = (payload.get("source_preservation") or {}).get("template_application") or {}
    # Source parallel groups are evidence of comparison structure. A repeated
    # drawing alone cannot establish deliberate repetition or semantic intent.
    return ["source_parallel_groups"] if application.get("source_parallel_groups") else []


def observe_svg_deck_composition(pages):
    """Report comparable geometry clusters with at most 32 comparisons per page.

    Output binds each observation to its SVG bytes. The caller persists page
    observations through existing checkpoints; no model or redraw is requested.
    """
    measured, observations = [], []
    for index, (payload, _) in enumerate(pages):
        svg = payload.get("svg_content") or ""
        geometry = _geometry(svg)
        evidence = dict(index=index, sourceDigest=source_digest(svg),
                        comparisonBasis=_comparison_basis(payload))
        if geometry is None:
            evidence.update(status="unmeasured")
            measured.append(None)
            observations.append(evidence)
            continue
        features, units, digest = geometry
        evidence.update(status="observed", geometryDigest=digest, contentUnits=units,
                        similarPages=[])
        if units >= 3:
            for previous in range(max(0, index - 32), index):
                prior = measured[previous]
                if prior is None or prior[1] < 3:
                    continue
                other = prior[0]
                keys = features.keys() | other.keys()
                total = sum(max(features[k], other[k]) for k in keys)
                similarity = sum(min(features[k], other[k]) for k in keys) / max(1, total)
                if similarity >= .86:
                    evidence["similarPages"].append(previous)
                    observations[previous]["similarPages"].append(index)
        measured.append(geometry)
        observations.append(evidence)
    # A pair is normal reuse; describe clusters of at least three pages.
    groups = []
    visited = set()
    for index, evidence in enumerate(observations):
        if index in visited or not evidence.get("similarPages"):
            continue
        pending, members = [index], set()
        while pending:
            current = pending.pop()
            if current in members:
                continue
            members.add(current)
            pending.extend(observations[current].get("similarPages", []))
        visited.update(members)
        if len(members) >= 3:
            basis = sorted({b for i in members for b in observations[i]["comparisonBasis"]})
            groups.append(dict(pages=sorted(members), comparisonBasis=basis,
                observation="comparison_structure_repetition" if basis else "similar_geometry_repetition"))
    return dict(schemaVersion="svg-composition-observation/v1", advisoryOnly=True,
                aestheticsVerified=False, basis="canvas_normalized_approximate_geometry",
                pageCount=len(pages), observedPages=sum(g is not None for g in measured),
                groups=groups, pages=observations)
