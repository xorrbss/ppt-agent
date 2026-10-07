"""Opt-in caption ink checks for schedules proven by source dates and bars."""
from __future__ import annotations

from datetime import date
import math
import re
from xml.etree import ElementTree as ET

from services.svg_text_overlap import OVERLAP_THRESHOLD_PX, _ink_rect

GUARD_VERSION = "source-timeline-caption-ink/v1"
GUARD_VERSION_V2 = "source-timeline-caption-ink/v2"
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_DAYS = re.compile(r"(\d+)\s*(?:일|days?\b)", re.I)
_DEPENDENCY = re.compile(r"선행|완료\s*후|의존|depend|after", re.I)


def _normalized(value):
    return "".join(str(value or "").split())


def _dependency_signature(value):
    normalized = _normalized(value)
    if re.fullmatch(r"선행(?:작업)?없음", normalized):
        return ("none", ())
    if re.search(r"완료후(?:시작)?$", normalized):
        identifiers = tuple(sorted(set(re.findall(r"(?<![A-Za-z])([A-Z])(?![A-Za-z])", normalized))))
        if identifiers:
            return ("after", identifiers)
    return None


def _caption_matches(row, item):
    text = _normalized(item.get("rendered_text") or item.get("owned_text"))
    if not all(value in text for value in [*row["dates"], f'{row["days"]}일']):
        return False
    if row["dependency"] in text:
        return True
    # The dates/duration bind the row; compare an explicit dependency contract
    # when captions abbreviate "선행 작업 없음" or "B와 C 완료 후 시작".
    signature = _dependency_signature(row["dependency"])
    duration = _DAYS.search(text)
    return signature is not None and duration is not None and signature == _dependency_signature(
        text[duration.end():].strip(" ,·|;-/()"))


def source_timeline_rows(source: str) -> list[dict]:
    """Require explicit date ranges, inclusive durations and dependency captions."""
    rows = []
    for line in (source or "").splitlines():
        dates = _DATE.findall(line)
        duration = _DAYS.search(line)
        if len(dates) != 2 or duration is None:
            continue
        dependency = line[duration.end():].strip(" ,·|;-/()")
        if not dependency or not _DEPENDENCY.search(dependency):
            continue
        try:
            start, end = (date.fromisoformat(value) for value in dates)
            days = int(duration[1])
        except ValueError:
            continue
        if days <= 0 or (end - start).days + 1 != days:
            continue
        rows.append({"dates": dates, "start": start.toordinal(), "days": days,
                     "dependency": _normalized(dependency)})
    if len(rows) < 3 or len({(row["start"], row["days"]) for row in rows}) < 2:
        return []
    return rows


def _contains(outer, inner):
    return bool(inner) and (inner["x"] >= outer["x"] and inner["y"] >= outer["y"]
            and inner["x"] + inner["width"] <= outer["x"] + outer["width"]
            and inner["y"] + inner["height"] <= outer["y"] + outer["height"])


def _caption_members(caption, elements):
    members = []
    by_qid = {item.get("qid"): item for item in elements}
    for item in elements:
        if not item.get("has_owned_text"):
            continue
        node = item
        seen = set()
        while node and node.get("qid") not in seen:
            if node.get("qid") == caption.get("qid"):
                members.append(item)
                break
            seen.add(node.get("qid"))
            node = by_qid.get(node.get("parent_qid"))
    return members


def _proven_bars(svg, elements, captions, *, allow_unlabeled=False):
    source_nodes = {f"svg-{index}": node for index, node in enumerate(ET.fromstring(svg).iter())}
    candidates = []
    labels = [item for item in elements if item.get("has_owned_text")
              and re.fullmatch(r"\d+(?:일|days?)", _normalized(item.get("owned_text")), re.I)]
    for item in elements:
        node = source_nodes.get(item.get("qid"))
        box = _ink_rect(item.get("bbox"))
        if (node is None or item.get("tag") != "rect" or box is None
                or not item.get("css_visible") or item.get("effective_opacity", 0) < .9
                or item.get("rotation_degrees", 0) != 0 or box["width"] <= box["height"]):
            continue
        # Only simple, directly painted source rectangles; unsupported paint is
        # not evidence of a foreground schedule bar.
        if not re.fullmatch(r"#[0-9A-Fa-f]{3}(?:[0-9A-Fa-f]{3})?", node.get("fill", "")):
            continue
        if float(node.get("fill-opacity", "1")) < .9:
            continue
        values = [int(_DAYS.fullmatch(label["owned_text"].strip())[1]) for label in labels
                  if _contains(box, _ink_rect(label.get("bbox")))]
        if len(values) == 1 or allow_unlabeled and not values:
            candidates.append({"item": item, "box": box, "days": values[0] if values else None})
    proven = {}
    for candidate in candidates:
        height = candidate["box"]["height"]
        group = sorted((bar for bar in candidates if abs(bar["box"]["height"] - height) <= .5),
                       key=lambda bar: bar["box"]["y"])
        if len(group) != len(captions):
            continue
        paired = list(zip(captions, group, strict=True))
        if any(bar["days"] is not None and row["days"] != bar["days"] or
               abs(caption["bbox"]["y"] + caption["bbox"]["height"] / 2
                   - bar["box"]["y"] - height / 2) > height
               for (row, caption), bar in paired):
            continue
        scales = [bar["box"]["width"] / row["days"] for (row, _), bar in paired]
        scale = sum(scales) / len(scales)
        if not math.isfinite(scale) or scale <= 0 or any(abs(value - scale) > .5 / max(row["days"] for row, _ in captions)
                                                       for value in scales):
            continue
        first = min(row["start"] for row, _ in captions)
        origins = [bar["box"]["x"] - (row["start"] - first) * scale for (row, _), bar in paired]
        if max(origins) - min(origins) > .5:
            continue
        if not allow_unlabeled:
            return group
        proven[tuple(bar["item"]["qid"] for bar in group)] = group
    if len(proven) == 1:
        return next(iter(proven.values()))
    return None


def apply_timeline_ink_quality(quality: dict, svg: str, source: str, *, allow_unlabeled=False) -> None:
    receipt = {"version": GUARD_VERSION_V2 if allow_unlabeled else GUARD_VERSION,
               "status": "not_applicable", "collisions": []}
    if allow_unlabeled:
        receipt["bar_proof"] = "source_dates_duration_row_unique_scale_optional_inside_labels"
    quality["timeline_caption_ink"] = receipt
    rows = source_timeline_rows(source)
    elements = (quality.get("browser_metrics") or {}).get("elements") or []
    captions = []
    for row in rows:
        matches = [item for item in elements if item.get("tag") == "text" and _caption_matches(row, item)]
        if len(matches) != 1 or _ink_rect(matches[0].get("bbox")) is None:
            receipt["reason"] = "source_caption_unproven"
            return
        captions.append((row, matches[0]))
    captions.sort(key=lambda pair: pair[1]["bbox"]["y"])
    if not captions:
        receipt["reason"] = "source_timeline_unproven"
        return
    bars = _proven_bars(svg, elements, captions, allow_unlabeled=allow_unlabeled)
    if bars is None:
        receipt["reason"] = "source_bar_geometry_unproven"
        return
    count = 0
    coverage_complete = True
    expected_count = 0
    for _, caption in captions:
        for member in _caption_members(caption, elements):
            fragments = member.get("text_ink_fragments") or []
            if not fragments or len(fragments) != len(member.get("text_fragments") or []):
                coverage_complete = False
            for fragment in fragments:
                coverage = (fragment or {}).get("glyphInkCoverage") or {}
                clusters = (fragment or {}).get("glyphInkClusters", [])
                expected = coverage.get("expected")
                if (coverage.get("complete") is not True or not isinstance(expected, int)
                        or expected <= 0 or coverage.get("measured") != expected
                        or len(clusters) != expected
                        or len({cluster.get("index") for cluster in clusters}) != expected):
                    coverage_complete = False
                elif expected:
                    expected_count += expected
                for cluster in (fragment or {}).get("glyphInkClusters", []):
                    ink = _ink_rect(cluster)
                    if ink is None or cluster.get("basis") != "isolated-matching-advance":
                        coverage_complete = False
                        continue
                    count += 1
                    for bar in bars:
                        box = bar["box"]
                        dx = min(ink["x"] + ink["width"], box["x"] + box["width"]) - max(ink["x"], box["x"])
                        dy = min(ink["y"] + ink["height"], box["y"] + box["height"]) - max(ink["y"], box["y"])
                        if dx > OVERLAP_THRESHOLD_PX and dy > OVERLAP_THRESHOLD_PX and not _contains(box, ink):
                            receipt["collisions"].append({"caption_qid": member["qid"], "bar_qid": bar["item"]["qid"],
                                "cluster_index": cluster.get("index"), "ink": ink,
                                "intersection_width": dx, "intersection_height": dy})
    receipt.update(status="measured" if count and coverage_complete else "not_applicable",
                   reliable_glyph_clusters=count, expected_glyph_clusters=expected_count,
                   complete_glyph_coverage=bool(count and coverage_complete),
                   proven_bars=len(bars), source_rows=len(rows), basis="actual_glyph_ink_only")
    if not count or not coverage_complete:
        receipt["reason"] = "reliable_glyph_coverage_incomplete"
    if receipt["collisions"]:
        quality["passed"] = quality["geometry_passed"] = False
        quality.setdefault("failures", []).append({"kind": "timeline_caption_bar_ink_overlap",
                                                   "pairs": receipt["collisions"]})
