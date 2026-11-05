"""Drawn bar charts whose bar lengths do not follow their own value labels (advisory).

Measured 2026-11-05 (``docs/svg-mode-quality-performance-review-20261105.md``
section 3.3, D2 p6): the model drew four bars 160/110/190/250 px tall for the
labels 16.2/14.7/18.5/22 (만대), so a 9 % dip read as a 31 % drop. Geometry
checks, ``required_text`` and the vision review all passed that page; the
correct heights were 160/145/183/217 px.

A group is checked only when all of this holds, so a card row, a progress
track or a table band is not read as a chart:

* at least :data:`MIN_BARS` painted ``rect`` bars share one **baseline** (their
  bottom edge, or for horizontal bars their left edge) within
  :data:`BASELINE_TOLERANCE_PX`, have the same thickness within
  :data:`THICKNESS_TOLERANCE`, do not overlap each other, and do not all have
  the same length (equal lengths are cards, not bars). When fills repeat among
  them, each fill is its own series (a grouped chart, possibly on its own
  axis); when every bar has its own fill they are one series;
* every bar has exactly one **numeric label** -- a ``<text>`` whose whole text
  is one number with an optional currency prefix and a short unit suffix --
  placed on the bar or just past its end (above a column, right of a row);
  labels on the far side of the baseline (category axis, years) are not value
  labels, and a unit-bearing label is preferred over a bare number;
* all labels carry **one unit**, and every value is positive.

The group is flagged when a bar's length differs from the least-squares
proportional fit ``length = k * value`` by more than :data:`PROPORTION_TOLERANCE`
of the fitted length (and by more than :data:`ABSOLUTE_TOLERANCE_PX`, so a
sub-pixel rounding of a tiny bar is not a finding). The result is an
**advisory** on the page's quality: it never fails or flags the page until its
precision has been labelled.

Server furniture (``svg-system-*`` other than a template page's body group) is
not read; a subtree under any transform other than translate/scale is skipped.
"""
from __future__ import annotations

import math
import re
from xml.etree import ElementTree as ET

from services.generation_core.drivers.svg.local_page_repair import compose_affine, translate_scale_affine

ADVISORY_KIND = "chart_bar_proportion_mismatch"
#: The composed template page's body group (services/svg_templates/frame_composition.BODY_ID).
TEMPLATE_BODY_ID = "svg-system-template-body"
MIN_BARS = 3
#: Largest relative difference between a bar's length and the proportional fit.
PROPORTION_TOLERANCE = 0.03
#: A difference at or below this many pixels is never a finding.
ABSOLUTE_TOLERANCE_PX = 1.0
BASELINE_TOLERANCE_PX = 1.0
#: Relative thickness difference within which bars belong to one series.
THICKNESS_TOLERANCE = 0.05
#: How far past a bar's end its value label may sit, in label font sizes.
LABEL_REACH_EM = 3.0
#: A rect covering this share of the canvas along either axis is a background.
BACKGROUND_SHARE = 0.9
#: Advance estimates for a label's width (no font metrics in a static check).
_WIDE_ADVANCE_EM, _NARROW_ADVANCE_EM = 1.0, 0.6
_DEFAULT_FONT_PX = 16.0
_LABEL = re.compile(r"([$€£¥₩])?([+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\s*([%A-Za-z가-힣]{0,4})")
_NUMBER = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def _tag(node) -> str:
    return node.tag.rsplit("}", 1)[-1]


def _float(node, name, default=None):
    values = _NUMBER.findall(node.get(name, "") or "")
    if len(values) != 1:
        return default
    value = float(values[0])
    return value if math.isfinite(value) else default


def _walk(node, affine=(1.0, 1.0, 0.0, 0.0), anchor="start", font=_DEFAULT_FONT_PX):
    node_id = node.get("id") or ""
    if node_id.startswith("svg-system-") and node_id != TEMPLATE_BODY_ID:
        return
    item = translate_scale_affine(node.get("transform"))
    if item is None:
        return
    affine = compose_affine(affine, item)
    anchor = node.get("text-anchor") or anchor
    font = _float(node, "font-size", font)
    yield node, affine, anchor, font
    for child in node:
        yield from _walk(child, affine, anchor, font)


def _painted(node) -> bool:
    return node.get("fill", "black") != "none"


def _rects(root, width, height):
    rects = []
    for node, (sx, sy, tx, ty), _anchor, _font in _walk(root):
        if _tag(node) != "rect" or not _painted(node):
            continue
        x, y = _float(node, "x", 0.0), _float(node, "y", 0.0)
        w, h = _float(node, "width"), _float(node, "height")
        if x is None or y is None or not w or not h or w <= 0 or h <= 0:
            continue
        left, top, right, bottom = tx + sx * x, ty + sy * y, tx + sx * (x + w), ty + sy * (y + h)
        if right - left >= BACKGROUND_SHARE * width or bottom - top >= BACKGROUND_SHARE * height:
            continue
        rects.append((left, top, right, bottom, (node.get("fill") or "black").strip().lower()))
    return rects


def _labels(root):
    labels = []
    for node, (sx, sy, tx, ty), anchor, font in _walk(root):
        if _tag(node) != "text":
            continue
        text = "".join("".join(node.itertext()).split())
        match = _LABEL.fullmatch(text)
        x, y = _float(node, "x", 0.0), _float(node, "y", 0.0)
        if match is None or x is None or y is None:
            continue
        size = font * sy
        advance = sum(_WIDE_ADVANCE_EM if ord(char) >= 0x2E80 else _NARROW_ADVANCE_EM for char in text) * size
        cx = tx + sx * x
        cx += advance / 2 if anchor == "start" else -advance / 2 if anchor == "end" else 0.0
        cy = ty + sy * y - 0.35 * size
        labels.append(dict(text=text, value=float(match[2].replace(",", "")), unit=(match[1] or "") + match[3],
                           cx=cx, cy=cy, half=advance / 2, size=size))
    return labels


def _series(rects, orientation):
    """Groups of bars sharing a baseline and a thickness, ordered across the axis."""

    # Rect tuples are (left, top, right, bottom): a column stands on its bottom
    # edge and spans x; a row starts at its left edge and spans y.
    base_at, low, high = (3, 0, 2) if orientation == "vertical" else (0, 1, 3)

    def thickness(rect):
        return rect[high] - rect[low]

    baselines, current = [], []
    for rect in sorted(rects, key=lambda item: item[base_at]):
        if current and rect[base_at] - current[0][base_at] > BASELINE_TOLERANCE_PX:
            baselines.append(current)
            current = []
        current.append(rect)
    if current:
        baselines.append(current)
    result = []
    for group in baselines:
        series = []
        for rect in sorted(group, key=thickness):
            if not series or thickness(rect) - thickness(series[-1][0]) > THICKNESS_TOLERANCE * thickness(series[-1][0]):
                series.append([])
            series[-1].append(rect)
        for bars in [part for same_thickness in series for part in _by_fill(same_thickness)]:
            bars = sorted(bars, key=lambda item: item[low])
            if (len(bars) >= MIN_BARS and all(a[high] <= b[low] + BASELINE_TOLERANCE_PX
                                              for a, b in zip(bars, bars[1:], strict=False))):
                result.append(bars)
    return result


def _by_fill(bars):
    """One series per fill when fills repeat (a grouped chart); one series when every bar has its own."""

    fills = [rect[4] for rect in bars]
    if len(set(fills)) == len(fills):
        return [bars]
    return [[rect for rect in bars if rect[4] == fill] for fill in dict.fromkeys(fills)]


def _extent(rect, orientation):
    return rect[3] - rect[1] if orientation == "vertical" else rect[2] - rect[0]


def _bar_label(rect, orientation, labels):
    left, top, right, bottom = rect[:4]
    found = []
    for label in labels:
        reach = LABEL_REACH_EM * label["size"]
        if orientation == "vertical":
            on_bar = left <= label["cx"] <= right and top - reach <= label["cy"] < bottom
        else:
            on_bar = (top <= label["cy"] <= bottom
                      and left <= label["cx"] - label["half"] <= right + reach)
        if on_bar:
            found.append(label)
    if len(found) > 1:
        found = [label for label in found if label["unit"]]
    return found[0] if len(found) == 1 else None


def bar_proportion_mismatches(svg: str, *, width: float, height: float) -> list[dict]:
    """Every checked bar group whose lengths are not proportional to their labels."""

    root = ET.fromstring(svg)
    rects, labels = _rects(root, width, height), _labels(root)
    findings = []
    for orientation in ("vertical", "horizontal"):
        for bars in _series(rects, orientation):
            extents = [_extent(rect, orientation) for rect in bars]
            if max(extents) - min(extents) <= ABSOLUTE_TOLERANCE_PX:
                continue  # equal lengths: a row of cards, not a chart
            matched = [_bar_label(rect, orientation, labels) for rect in bars]
            if any(label is None for label in matched) or len({id(label) for label in matched}) != len(bars):
                continue
            if len({label["unit"] for label in matched}) != 1 or any(label["value"] <= 0 for label in matched):
                continue
            values = [label["value"] for label in matched]
            scale = sum(e * v for e, v in zip(extents, values, strict=True)) / sum(v * v for v in values)
            rows, worst = [], 0.0
            for extent, value, label in zip(extents, values, matched, strict=True):
                expected = scale * value
                deviation = abs(extent - expected) / expected
                if abs(extent - expected) > ABSOLUTE_TOLERANCE_PX:
                    worst = max(worst, deviation)
                rows.append({"label": label["text"], "value": value, "extentPx": round(extent, 2),
                             "proportionalExtentPx": round(expected, 2), "deviation": round(deviation, 4)})
            if worst > PROPORTION_TOLERANCE:
                findings.append({"orientation": orientation, "unit": matched[0]["unit"], "bars": rows,
                                 "maxDeviation": round(worst, 4), "tolerance": PROPORTION_TOLERANCE})
    return findings


def apply_bar_proportion_advisory(quality: dict, svg: str, *, width: float, height: float) -> None:
    """Add a ``chart_bar_proportion_mismatch`` advisory; never a failure (advisory first)."""

    try:
        findings = bar_proportion_mismatches(svg, width=width, height=height)
    except (ET.ParseError, ValueError, ZeroDivisionError):
        return
    if findings:
        quality.setdefault("advisories", []).append({"kind": ADVISORY_KIND, "groups": findings})


__all__ = [
    "ABSOLUTE_TOLERANCE_PX", "ADVISORY_KIND", "BASELINE_TOLERANCE_PX", "LABEL_REACH_EM", "MIN_BARS",
    "PROPORTION_TOLERANCE", "THICKNESS_TOLERANCE", "apply_bar_proportion_advisory", "bar_proportion_mismatches",
]
