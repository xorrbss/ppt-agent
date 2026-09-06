"""H4 deck instruments: informational geometry/colour signals, no gate.

Design item H4 phase 1b (``docs/authored-quality-speed-programme-design-
20260905.md`` section 6): pure functions over the already-captured browser
readback (``utils/slide_capture.py``'s per-element shape) that give G-G3
("largest empty rect below the content union", median < 80px) and G-G4
("palette conformance"/"radius families") their numbers -- receipt fields a
caller reads, never a reason to block a slide.

The same surface carries the ``text_intrudes_footer_zone`` advisory record.
That detector is deliberately not a publication blocker
(``utils/authored_dom_categories.py`` states why, with the measured
distributions), so its per-occurrence magnitude and qid are recorded here --
reaching both the quality-gate receipt and the attempt-timing receipt through
``services/authored_deck_instruments_stage.py`` -- and a later calibration can
read the whole distribution of a stored deck without a re-render.

``SlideInstrumentInput.html`` excludes elements marked
``data-semantic-role="decoration"`` from the content/colour/font-size
instruments -- a decorative rule's colour says nothing about content
discipline; see ``authored_dom_quality._telemetry_status`` (H5) for the
sibling case. Palette conformance uses CIE76 (Lab Euclidean) distance: the
simplest standard metric, adequate for "close to any palette colour".
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from statistics import median
from typing import Any

from utils.authored_dom_footer_zone import find_footer_zone_intrusions
from utils.authored_infographic.contracts import DEFAULT_CONTENT_BOTTOM_PX

#: A slide's neutral ink/paper says nothing about deck colour discipline.
_IGNORED_RGB = {(255, 255, 255), (0, 0, 0)}
_SMALL_FONT_PX = 12.0
_STRETCH_RATIO = 0.12
_STRETCH_OBJECT_FITS = {"fill", "none", ""}
_PALETTE_DELTA_E_MAX = 6.0
#: An intrusion at or above this is the one shape the offline replay found only
#: on the branch decks (3 of 57 occurrences, none on the deck the owner
#: preferred).  It marks the record; it still gates nothing.
FOOTER_INTRUSION_EXTREME_PX = 90.0
_RADIUS_KEYS = (
    "borderTopLeftRadius",
    "borderTopRightRadius",
    "borderBottomRightRadius",
    "borderBottomLeftRadius",
)
_COLOR_KEYS = ("color", "backgroundColor", "borderColor")

_HEX6 = re.compile(r"^#([0-9a-f]{6})$")
_HEX3 = re.compile(r"^#([0-9a-f]{3})$")
_RGB_FN = re.compile(
    r"^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+))?\s*\)$"
)
#: Same approach as H5's ``_telemetry_status`` decoration tolerance: a plain
#: tag-text scan for the attribute pair, never a full parse.
_DECORATION_QID = re.compile(r'data-qid="([^"]+)"[^>]*data-semantic-role="decoration"')
_DECORATION_QID_REVERSED = re.compile(
    r'data-semantic-role="decoration"[^>]*data-qid="([^"]+)"'
)


@dataclass(frozen=True, slots=True)
class SlideInstrumentInput:
    """One slide: its markup, its browser-readback payload (``{"elements":
    [...]}``/``{"nodes": [...]}``, ``utils/slide_capture.py``'s shape), and
    the deck's frozen palette hex codes."""

    index: int
    html: str
    metrics: Mapping[str, Any]
    palette: Sequence[str] = ()


@dataclass(frozen=True, slots=True)
class _Box:
    x: float
    y: float
    width: float
    height: float

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height


@dataclass(frozen=True, slots=True)
class SlideInstruments:
    """One slide's computed instruments; ``margin_deviation_px`` is filled in
    by :func:`compute_deck_instruments` once every slide's inset is known."""

    index: int
    empty_band_px: float
    palette_conformance: float | None
    radius_families: tuple[float, ...]
    content_inset: dict[str, float]
    small_font_count: int
    stretched_image_count: int
    margin_deviation_px: float = 0.0
    #: Advisory only. One entry per text owner whose ink prints past the deck's
    #: footer safe line: ``qid``, ``intrusion_px``, ``content_bottom_px``,
    #: ``ink_bottom_px``, ``intersects_page_number``, ``severity``.
    footer_intrusions: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True, slots=True)
class DeckInstruments:
    """Every slide's instruments plus the aggregates that only make sense
    deck-wide: radius families used anywhere, and the medians."""

    slides: tuple[SlideInstruments, ...]
    radius_family_count: int
    median_empty_band_px: float
    median_palette_conformance: float | None
    #: Advisory footer-zone aggregates, so a calibration can compare two stored
    #: decks without reassembling the per-slide lists first.
    footer_intrusion_count: int = 0
    footer_intrusion_slide_count: int = 0
    footer_intrusion_px: tuple[float, ...] = ()
    median_footer_intrusion_px: float | None = None

    def to_receipt(self) -> dict[str, Any]:
        return asdict(self)


def _maybe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_color(value: str) -> tuple[int, int, int, float] | None:
    text = (value or "").strip().lower()
    if not text or text == "transparent":
        return None
    match = _HEX6.match(text)
    if match:
        h = match.group(1)
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 1.0)
    match = _HEX3.match(text)
    if match:
        h = match.group(1)
        return (int(h[0] * 2, 16), int(h[1] * 2, 16), int(h[2] * 2, 16), 1.0)
    match = _RGB_FN.match(text)
    if match:
        r, g, b = (min(255, max(0, round(float(match.group(i))))) for i in (1, 2, 3))
        alpha = float(match.group(4)) if match.group(4) is not None else 1.0
        return (r, g, b, alpha)
    return None


def _relevant_rgb(value: str) -> tuple[int, int, int] | None:
    parsed = _parse_color(value)
    if parsed is None:
        return None
    r, g, b, alpha = parsed
    if alpha <= 0.0 or (r, g, b) in _IGNORED_RGB:
        return None
    return (r, g, b)


def _srgb_to_linear(channel: float) -> float:
    c = channel / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _rgb_to_lab(rgb: tuple[int, int, int]) -> tuple[float, float, float]:
    r, g, b = (_srgb_to_linear(v) for v in rgb)
    x = r * 0.4124 + g * 0.3576 + b * 0.1805
    y = r * 0.2126 + g * 0.7152 + b * 0.0722
    z = r * 0.0193 + g * 0.1192 + b * 0.9505
    xn, yn, zn = 0.95047, 1.0, 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 0.008856 else (7.787 * t) + 16 / 116

    fx, fy, fz = f(x / xn), f(y / yn), f(z / zn)
    return (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))


def _delta_e76(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return math.sqrt(sum((left - right) ** 2 for left, right in zip(a, b, strict=True)))


def _decoration_qids(html: str) -> set[str]:
    return set(_DECORATION_QID.findall(html)) | set(_DECORATION_QID_REVERSED.findall(html))


def _elements(metrics: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = metrics.get("elements", metrics.get("nodes", []))
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return []
    return [item for item in raw if isinstance(item, Mapping)]


def _is_owned(item: Mapping[str, Any]) -> bool:
    """Mirrors ``authored_dom_quality.browser_proves_owned_content``."""
    if "has_owned_text" not in item:
        return True
    if bool(item.get("has_owned_text")):
        return True
    return str(item.get("tag", "")).lower() in {"img", "picture", "svg", "video"}


def _box(item: Mapping[str, Any]) -> _Box | None:
    raw = item.get("bbox")
    if not isinstance(raw, Mapping):
        return None
    x, y = _maybe_float(raw.get("x")), _maybe_float(raw.get("y"))
    width, height = _maybe_float(raw.get("width")), _maybe_float(raw.get("height"))
    if None in (x, y, width, height):
        return None
    return _Box(x, y, width, height)


def _style_of(item: Mapping[str, Any]) -> Mapping[str, Any]:
    style = item.get("computed_style")
    return style if isinstance(style, Mapping) else {}


def _radius_px(value: Any) -> float | None:
    text = str(value or "").strip().lower()
    if not text.endswith("px"):
        return None
    px = _maybe_float(text[:-2])
    return px if px is not None and px > 0 else None


def _is_stretched_image(item: Mapping[str, Any], box: _Box | None) -> bool:
    image = item.get("image")
    if not isinstance(image, Mapping) or box is None or box.height <= 0:
        return False
    natural_w = _maybe_float(image.get("naturalWidth"))
    natural_h = _maybe_float(image.get("naturalHeight"))
    if not natural_w or not natural_h or natural_h <= 0:
        return False
    if str(_style_of(item).get("objectFit", "")).strip().lower() not in _STRETCH_OBJECT_FITS:
        return False
    natural_ar = natural_w / natural_h
    if natural_ar <= 0:
        return False
    return abs((box.width / box.height) - natural_ar) / natural_ar > _STRETCH_RATIO


def _content_envelope(boxes: Sequence[_Box]) -> _Box | None:
    if not boxes:
        return None
    left = min(box.x for box in boxes)
    top = min(box.y for box in boxes)
    right = max(box.right for box in boxes)
    bottom = max(box.bottom for box in boxes)
    return _Box(left, top, right - left, bottom - top)


def _footer_intrusions(
    elements: Sequence[Mapping[str, Any]], canvas: tuple[float, float]
) -> tuple[dict[str, Any], ...]:
    """The footer-zone detector's own reports, recorded rather than gated.

    Reusing ``find_footer_zone_intrusions`` keeps one measurement of this
    defect: the receipt records exactly what the DOM gate detected, which is
    the point of keeping an advisory category detected at all.
    """

    width, height = canvas
    try:
        intrusions = find_footer_zone_intrusions(
            elements, viewport_width=width, viewport_height=height
        )
    except Exception:  # pragma: no cover - an instrument never breaks a deck
        return ()
    return tuple(
        {
            "qid": intrusion.qid,
            "intrusion_px": intrusion.intrusion_px,
            "content_bottom_px": intrusion.content_bottom_px,
            "ink_bottom_px": intrusion.ink_bottom_px,
            "intersects_page_number": intrusion.intersects_page_number,
            "severity": (
                "high"
                if intrusion.intrusion_px >= FOOTER_INTRUSION_EXTREME_PX
                else "advisory"
            ),
        }
        for intrusion in intrusions
    )


def _compute_slide(slide: SlideInstrumentInput, canvas: tuple[float, float]) -> SlideInstruments:
    canvas_w, canvas_h = canvas
    decoration_qids = _decoration_qids(slide.html)
    elements = _elements(slide.metrics)
    palette_lab = [
        _rgb_to_lab(rgb)
        for colour in slide.palette
        for rgb in [_relevant_rgb(colour)]
        if rgb is not None
    ]

    owned_boxes: list[_Box] = []
    color_rgbs: list[tuple[int, int, int]] = []
    radius_values: set[float] = set()
    small_font_count = 0
    stretched_count = 0

    for item in elements:
        box = _box(item)
        if _is_stretched_image(item, box):
            stretched_count += 1
        qid = str(item.get("qid", "")).strip()
        if qid in decoration_qids or not _is_owned(item):
            continue
        if box is not None:
            owned_boxes.append(box)
        style = _style_of(item)
        for key in _COLOR_KEYS:
            rgb = _relevant_rgb(str(style.get(key, "")))
            if rgb is not None:
                color_rgbs.append(rgb)
        for key in _RADIUS_KEYS:
            px = _radius_px(style.get(key))
            if px is not None:
                radius_values.add(round(px))
        font_size = _maybe_float(item.get("font_size"))
        if bool(item.get("has_owned_text")) and font_size is not None and font_size < _SMALL_FONT_PX:
            small_font_count += 1

    envelope = _content_envelope(owned_boxes)
    # G-G3: the largest empty rect below the content union, on the common
    # case (a full-width band at the slide's foot) -- see the module docstring.
    target_line = min(float(DEFAULT_CONTENT_BOTTOM_PX), canvas_h)
    empty_band_px = max(0.0, target_line - (envelope.bottom if envelope else 0.0))

    palette_conformance = None
    if palette_lab and color_rgbs:
        hits = sum(
            1
            for rgb in color_rgbs
            if min(_delta_e76(_rgb_to_lab(rgb), lab) for lab in palette_lab) <= _PALETTE_DELTA_E_MAX
        )
        palette_conformance = hits / len(color_rgbs)

    content_inset = (
        {"left": envelope.x, "top": envelope.y, "right": max(0.0, canvas_w - envelope.right)}
        if envelope is not None
        else {"left": 0.0, "top": 0.0, "right": 0.0}
    )

    return SlideInstruments(
        index=slide.index,
        empty_band_px=round(empty_band_px, 1),
        palette_conformance=round(palette_conformance, 4) if palette_conformance is not None else None,
        radius_families=tuple(sorted(radius_values)),
        content_inset={side: round(value, 1) for side, value in content_inset.items()},
        small_font_count=small_font_count,
        stretched_image_count=stretched_count,
        footer_intrusions=_footer_intrusions(elements, canvas),
    )


def compute_deck_instruments(
    slides: Sequence[SlideInstrumentInput], *, canvas: tuple[float, float] = (1280.0, 720.0)
) -> DeckInstruments:
    """Every slide's instruments, then the deck-wide aggregates: distinct
    radius families used anywhere, and each slide's inset deviation from the
    deck's own median inset (G-G4's deck-coherence half)."""

    computed = [_compute_slide(slide, canvas) for slide in slides]
    sides = ("left", "top", "right")
    medians = {
        side: median(slide.content_inset[side] for slide in computed) if computed else 0.0
        for side in sides
    }
    finalized = tuple(
        replace(
            slide,
            margin_deviation_px=round(
                max(abs(slide.content_inset[side] - medians[side]) for side in sides), 1
            ),
        )
        for slide in computed
    )

    palette_scores = [s.palette_conformance for s in finalized if s.palette_conformance is not None]
    intrusion_px = tuple(
        float(entry["intrusion_px"])
        for slide in finalized
        for entry in slide.footer_intrusions
    )
    return DeckInstruments(
        slides=finalized,
        radius_family_count=len({value for slide in finalized for value in slide.radius_families}),
        median_empty_band_px=round(median(s.empty_band_px for s in finalized), 1) if finalized else 0.0,
        median_palette_conformance=round(median(palette_scores), 4) if palette_scores else None,
        footer_intrusion_count=len(intrusion_px),
        footer_intrusion_slide_count=sum(1 for s in finalized if s.footer_intrusions),
        footer_intrusion_px=tuple(sorted(intrusion_px)),
        median_footer_intrusion_px=round(median(intrusion_px), 2) if intrusion_px else None,
    )


__all__ = [
    "FOOTER_INTRUSION_EXTREME_PX",
    "DeckInstruments",
    "SlideInstrumentInput",
    "SlideInstruments",
    "compute_deck_instruments",
]
