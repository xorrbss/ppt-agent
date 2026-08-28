"""Deterministic, local preflight checks for authored slide HTML.

This module deliberately does *not* decide that a slide is visually correct.  Its
output is an inexpensive signal for the authored quality-review router; rendered
image VQA remains the visual source of truth.  The functions are pure so the
browser/capture layer can collect metrics with its preferred implementation and
pass a JSON-compatible payload back here.

``exact_reconstruction`` and ``style_reference`` are intentionally different:

* exact reconstruction checks that rendered text survives, but does not call an
  existing crop or overlap defective unless the browser marks it unintentional;
  fidelity against the source image remains VQA's job.
* style reference applies ordinary readability/safety checks.  It has no source
  geometry to preserve, so unmarked crop/overlap is reported, while explicitly
  intentional decoration is left for image VQA.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from html import unescape
from html.parser import HTMLParser
from typing import Any, Literal

EXACT_RECONSTRUCTION = "exact_reconstruction"
STYLE_REFERENCE = "style_reference"
ReferenceMode = Literal["exact_reconstruction", "style_reference"]
TelemetryStatus = Literal["available", "unavailable", "incomplete"]

# This is deliberately shared with the patch engine.  A qid accepted by the
# migration must also be addressable by a patch, otherwise a seemingly harmless
# preflight repair can make a later local repair fail closed.
_QID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

_NON_RENDERED_TAGS = {
    "html",
    "head",
    "body",
    "base",
    "link",
    "meta",
    "title",
    "style",
    "script",
    "template",
    "noscript",
    "defs",
    "symbol",
    "clipPath".lower(),
}
# Most HTML void elements do not own editable content.  ``img`` is the deliberate
# exception: it is a first-class visual target for fit/crop repair, while still
# never being pushed onto the text parser stack.  In particular, treating ``br``
# as a normal opening tag would make following text belong to a phantom break.
# Keep this local copy rather than importing the patch engine: DOM inspection is a
# one-way dependency used by the patch trust boundary.
_VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)
_WS = re.compile(r"\s+")
_NUMBER_OR_SYMBOL = re.compile(r"(?:\d+(?:[,.]\d+)*%?|[$€£¥₩]|[→←↑↓↔±×÷#@&])")
_HEX_COLOR = re.compile(r"^#(?P<value>[0-9a-fA-F]{3,8})$")
_RGB_COLOR = re.compile(r"^rgba?\((.+)\)$", re.IGNORECASE)
_CJK_GLYPH = re.compile(
    "["
    "\u3040-\u30ff"  # Hiragana and Katakana
    "\u3400-\u4dbf"  # CJK Extension A
    "\u4e00-\u9fff"  # CJK Unified Ideographs
    "\uac00-\ud7a3"  # Hangul syllables
    "]"
)


@dataclass(frozen=True)
class BoundingBox:
    """A viewport-relative CSS-pixel rectangle."""

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


@dataclass(frozen=True)
class CompactDomNode:
    """Small, model-safe edit target map.  No raw HTML or arbitrary attributes."""

    qid: str
    tag: str
    text: str = ""
    bbox: BoundingBox | None = None
    style: dict[str, str] = field(default_factory=dict)
    editable: bool = True

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        if self.bbox is not None:
            result["bbox"] = asdict(self.bbox)
        return result


@dataclass(frozen=True)
class DomQualityIssue:
    category: Literal[
        "overflow",
        "clipping",
        "off_canvas",
        "overlap",
        "font",
        "image",
        "content",
        "contrast",
    ]
    severity: Literal["high", "medium", "low"]
    detail: str
    qids: tuple[str, ...] = ()
    policy: ReferenceMode = STYLE_REFERENCE

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["qids"] = list(self.qids)
        return result


@dataclass(frozen=True)
class DomQualityReport:
    reference_mode: ReferenceMode
    issues: tuple[DomQualityIssue, ...]
    compact_dom: tuple[CompactDomNode, ...]
    inspected_elements: int
    telemetry_status: TelemetryStatus = "available"

    @property
    def needs_attention(self) -> bool:
        # A clean deterministic pass is meaningful only when the browser probe
        # actually covered the authored targets.  Route unavailable telemetry to
        # detailed VQA instead of silently treating it as a clean slide.
        return self.telemetry_status != "available" or bool(self.issues)

    def to_dict(self) -> dict[str, Any]:
        return {
            "reference_mode": self.reference_mode,
            "needs_attention": self.needs_attention,
            "issues": [issue.to_dict() for issue in self.issues],
            "compact_dom": [node.to_dict() for node in self.compact_dom],
            "inspected_elements": self.inspected_elements,
            "telemetry_status": self.telemetry_status,
        }


def has_dom_quality_regression(
    baseline: DomQualityReport, candidate: DomQualityReport
) -> bool:
    """Return true when a candidate adds or worsens any deterministic issue.

    A lexicographic severity total can hide a new medium issue behind a resolved
    high issue.  Compare each category/target bucket independently so strict visual
    improvement can never trade away a deterministic safety check.
    """
    # A candidate without complete probe coverage cannot prove the required
    # deterministic no-regression condition.  Reject it even when the baseline
    # was also unavailable; image VQA alone must not silently bypass this gate.
    if candidate.telemetry_status != "available":
        return True

    severity_rank = {"low": 1, "medium": 2, "high": 3}

    def buckets(
        report: DomQualityReport,
    ) -> dict[tuple[str, tuple[str, ...]], list[int]]:
        grouped: dict[tuple[str, tuple[str, ...]], list[int]] = {}
        for issue in report.issues:
            grouped.setdefault((issue.category, issue.qids), []).append(
                severity_rank[issue.severity]
            )
        return {key: sorted(ranks, reverse=True) for key, ranks in grouped.items()}

    baseline_buckets = buckets(baseline)
    for key, candidate_ranks in buckets(candidate).items():
        baseline_ranks = baseline_buckets.get(key, [])
        if len(candidate_ranks) > len(baseline_ranks):
            return True
        if any(
            candidate_rank > baseline_rank
            for candidate_rank, baseline_rank in zip(candidate_ranks, baseline_ranks)
        ):
            return True
    return False


class _StartTagCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.tags: list[tuple[str, list[tuple[str, str | None]], str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag.lower(), attrs, self.get_starttag_text()))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag.lower(), attrs, self.get_starttag_text()))


def _normal_text(value: object, *, limit: int | None = None) -> str:
    text = _WS.sub(" ", unescape(str(value or ""))).strip()
    return text[:limit] if limit is not None else text


def _attrs_dict(attrs: Iterable[tuple[str, str | None]]) -> dict[str, str]:
    return {
        str(name).lower(): "" if value is None else str(value) for name, value in attrs
    }


def _is_edit_target(tag: str, attrs: Mapping[str, str]) -> bool:
    """Avoid document plumbing but include every visible element that a patch may target."""
    if tag in _NON_RENDERED_TAGS or (tag in _VOID_TAGS and tag != "img") or tag in {
        "g",
        "path",
        "rect",
        "circle",
        "line",
        "polygon",
        "polyline",
    }:
        return False
    if attrs.get("aria-hidden", "").lower() == "true" or "data-qid-ignore" in attrs:
        return False
    # Preserve the historical migration contract: qid-less images are passive.
    # An explicitly addressed image is safe to expose for fit/crop operations.
    if tag == "img":
        return bool(attrs.get("data-qid", "").strip())
    return not (
        attrs.get("aria-hidden", "").lower() == "true" or "data-qid-ignore" in attrs
    )


def _qid_seed(tag: str, attrs: Mapping[str, str], occurrence: int) -> str:
    stable_attrs = "|".join(
        f"{name}={_normal_text(attrs.get(name), limit=80)}"
        for name in ("id", "class", "name", "role", "aria-label", "alt", "title")
        if attrs.get(name)
    )
    digest = hashlib.sha256(f"{tag}|{stable_attrs}|{occurrence}".encode()).hexdigest()[
        :12
    ]
    return f"q-{tag}-{digest}"


def is_valid_authored_qid(value: object) -> bool:
    """Return whether a value is a patch-addressable authored qid token."""
    return isinstance(value, str) and _QID_PATTERN.fullmatch(value) is not None


def _insert_attribute(raw_tag: str, name: str, value: str) -> str:
    # The input is an HTMLParser start-tag token.  Keep every user byte except the
    # one reversible attribute insertion, including quote choices and whitespace.
    match = re.search(r"\s*/?>\s*$", raw_tag)
    if match is None:
        return raw_tag
    insert_at = match.start()
    return f'{raw_tag[:insert_at]} {name}="{value}"{raw_tag[insert_at:]}'


def _replace_attribute(raw_tag: str, name: str, value: str) -> str:
    """Replace an existing valued or valueless attribute in one start token."""
    pattern = re.compile(
        rf"(\s{re.escape(name)})(?:\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s>]+))?(?=\s|/?>)",
        re.IGNORECASE,
    )
    return pattern.sub(lambda match: f'{match.group(1)}="{value}"', raw_tag, count=1)


def ensure_authored_qids(html: str) -> str:
    """Add deterministic ``data-qid`` attributes without rewriting authored HTML.

    Existing valid qids are retained verbatim.  Missing, invalid, and duplicate
    qids are deterministically replaced on the later element; normal ``id``
    attributes are never touched.  Calling this twice is idempotent.  We also
    normalize an invalid/duplicate qid on a non-editable element because the patch
    parser validates the document-wide qid namespace.
    """
    if not isinstance(html, str):
        raise TypeError("authored HTML must be a string")
    parser = _StartTagCollector()
    parser.feed(html)
    parser.close()
    cursor = 0
    used: set[str] = set()
    occurrences: dict[tuple[str, str], int] = {}
    parts: list[str] = []
    for tag, raw_attrs, raw_tag in parser.tags:
        index = html.find(raw_tag, cursor)
        if index < 0:  # Defensive: malformed parser output must leave user HTML intact.
            continue
        parts.append(html[cursor:index])
        attrs = _attrs_dict(raw_attrs)
        has_qid_attribute = "data-qid" in attrs
        raw_qid = attrs.get("data-qid", "")
        qid = raw_qid.strip()
        replacement = raw_tag
        needs_qid = _is_edit_target(tag, attrs)
        if needs_qid or qid:
            signature = (
                tag,
                "|".join(
                    f"{key}={attrs.get(key, '')}"
                    for key in (
                        "id",
                        "class",
                        "name",
                        "role",
                        "aria-label",
                        "alt",
                        "title",
                    )
                ),
            )
            occurrences[signature] = occurrences.get(signature, 0) + 1
            if (
                (needs_qid and not qid)
                or not is_valid_authored_qid(raw_qid)
                or qid in used
            ):
                generated = _qid_seed(tag, attrs, occurrences[signature])
                while generated in used:
                    occurrences[signature] += 1
                    generated = _qid_seed(tag, attrs, occurrences[signature])
                replacement = (
                    _replace_attribute(raw_tag, "data-qid", generated)
                    if has_qid_attribute
                    else _insert_attribute(raw_tag, "data-qid", generated)
                )
                qid = generated
            if qid:
                used.add(qid)
        parts.append(replacement)
        cursor = index + len(raw_tag)
    parts.append(html[cursor:])
    return "".join(parts)


def validate_declared_authored_qids(html: str) -> tuple[str, ...]:
    """Validate a model-declared QID namespace without repairing the source.

    New semantic dual-output candidates use ``data-qid`` as their element identity.
    Unlike the legacy compatibility migration above, this boundary must never invent,
    replace, or de-duplicate an identifier: a retry is safer than publishing a source
    whose semantic identity differs from what Authored declared.
    """

    if not isinstance(html, str):
        raise TypeError("authored HTML must be a string")
    parser = _StartTagCollector()
    parser.feed(html)
    parser.close()
    qids: list[str] = []
    used: set[str] = set()
    for tag, raw_attrs, _raw_tag in parser.tags:
        attrs = _attrs_dict(raw_attrs)
        raw_qid = attrs.get("data-qid")
        needs_qid = _is_edit_target(tag, attrs)
        if needs_qid and raw_qid is None:
            raise ValueError(f"authored_semantic_qid_missing:{tag}")
        if raw_qid is None:
            continue
        if not is_valid_authored_qid(raw_qid):
            raise ValueError("authored_semantic_qid_invalid")
        if raw_qid in used:
            raise ValueError("authored_semantic_qid_duplicate")
        used.add(raw_qid)
        qids.append(raw_qid)
    if not qids:
        raise ValueError("authored_semantic_qid_missing")
    return tuple(qids)


def _coerce_number(value: object, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def _coerce_bbox(value: object) -> BoundingBox | None:
    if not isinstance(value, Mapping):
        return None
    width = _coerce_number(value.get("width"))
    height = _coerce_number(value.get("height"))
    return BoundingBox(
        _coerce_number(value.get("x", value.get("left"))),
        _coerce_number(value.get("y", value.get("top"))),
        width,
        height,
    )


def _metric_elements(metrics: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(metrics, Mapping):
        return []
    raw = metrics.get("elements", metrics.get("nodes", []))
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return []
    result: list[dict[str, Any]] = []
    for value in raw:
        if isinstance(value, Mapping) and str(value.get("qid", "")).strip():
            item = dict(value)
            item["qid"] = str(item["qid"]).strip()
            result.append(item)
    return result


def build_compact_dom_map(
    html: str, browser_metrics: Mapping[str, Any] | None = None
) -> list[CompactDomNode]:
    """Return bounded target metadata for a structured patch-model prompt.

    Browser metrics override static text/style only where supplied; unknown fields
    stay absent rather than being guessed.  The returned data contains no arbitrary
    attributes, URLs, or full HTML.
    """
    enriched = ensure_authored_qids(html)
    parser = _StartTagCollector()
    parser.feed(enriched)
    parser.close()
    metric_by_qid = {item["qid"]: item for item in _metric_elements(browser_metrics)}
    expected_text = _expected_text_by_qid(enriched)
    nodes: list[CompactDomNode] = []
    for tag, raw_attrs, raw_tag in parser.tags:
        attrs = _attrs_dict(raw_attrs)
        if not _is_edit_target(tag, attrs):
            continue
        qid = attrs.get("data-qid", "").strip()
        if not qid:
            continue
        metric = metric_by_qid.get(qid, {})
        style_raw = metric.get("computed_style", metric.get("style", {}))
        style = (
            {
                key: _normal_text(value, limit=100)
                for key, value in dict(style_raw).items()
                if key
                in {
                    "color",
                    "backgroundColor",
                    "fontFamily",
                    "fontSize",
                    "fontWeight",
                    "lineHeight",
                    "position",
                    "zIndex",
                    "overflow",
                    "overflowX",
                    "overflowY",
                    "textAlign",
                    "display",
                    "flexDirection",
                    "alignItems",
                    "justifyContent",
                    "paddingTop",
                    "paddingRight",
                    "paddingBottom",
                    "paddingLeft",
                }
            }
            if isinstance(style_raw, Mapping)
            else {}
        )
        static_text = expected_text.get(
            qid, attrs.get("alt", "") if tag == "img" else ""
        )
        text = _normal_text(
            metric.get("text", metric.get("text_content", static_text)), limit=240
        )
        nodes.append(
            CompactDomNode(
                qid=qid,
                tag=tag,
                text=text,
                bbox=_coerce_bbox(metric.get("bbox", metric.get("rect"))),
                style=style,
                editable=not bool(metric.get("readonly", False)),
            )
        )
    return nodes


def normalize_browser_metrics(metrics: Mapping[str, Any] | None) -> dict[str, Any]:
    """Normalize the browser collector's permissive JSON into the public contract."""
    source = metrics if isinstance(metrics, Mapping) else {}
    viewport_raw = source.get("viewport", {})
    viewport = viewport_raw if isinstance(viewport_raw, Mapping) else {}
    normalized: dict[str, Any] = {
        "viewport": {
            "width": _coerce_number(
                viewport.get("width", source.get("width", 1280)), 1280
            ),
            "height": _coerce_number(
                viewport.get("height", source.get("height", 720)), 720
            ),
        },
        "fonts_ready": bool(
            source.get("fonts_ready", source.get("document_fonts_ready", True))
        ),
        "elements": [],
    }
    for item in _metric_elements(source):
        normalized_item = dict(item)
        normalized_item["parent_qid"] = str(
            item.get("parent_qid", item.get("parentQid", "")) or ""
        ).strip()
        normalized_item["bbox"] = asdict(
            _coerce_bbox(item.get("bbox", item.get("rect"))) or BoundingBox(0, 0, 0, 0)
        )
        for key in (
            "client_width",
            "client_height",
            "scroll_width",
            "scroll_height",
            "font_size",
            "contrast_ratio",
            "occluded_fraction",
            "continuous_occluded_fraction",
            "occlusion_sample_step_px",
            "occlusion_unknown_fraction",
            "rotation_degrees",
            "effective_z_index",
        ):
            aliases = {
                "client_width": "clientWidth",
                "client_height": "clientHeight",
                "scroll_width": "scrollWidth",
                "scroll_height": "scrollHeight",
                "font_size": "fontSize",
                "contrast_ratio": "contrastRatio",
                "occluded_fraction": "occludedFraction",
                "continuous_occluded_fraction": "continuousOccludedFraction",
                "occlusion_sample_step_px": "occlusionSampleStepPx",
                "occlusion_unknown_fraction": "occlusionUnknownFraction",
                "rotation_degrees": "rotationDegrees",
                "effective_z_index": "effectiveZIndex",
            }
            raw_value = item.get(key, item.get(aliases[key]))
            if raw_value is None and key in {
                "contrast_ratio",
                "occluded_fraction",
                "continuous_occluded_fraction",
                "occlusion_sample_step_px",
                "occlusion_unknown_fraction",
                "rotation_degrees",
            }:
                normalized_item[key] = None
            else:
                normalized_item[key] = _coerce_number(raw_value)
        normalized["elements"].append(normalized_item)
    return normalized


def _telemetry_status(
    html: str,
    source: Mapping[str, Any] | None,
    normalized: Mapping[str, Any],
) -> TelemetryStatus:
    """Classify browser-probe coverage without confusing an empty slide with loss.

    A present ``elements: []`` payload is complete for a truly empty slide.  It is
    incomplete for a slide that has patchable elements, while a missing/malformed
    payload is unavailable.  This distinction lets callers surface useful
    diagnostics while treating both states as escalation-worthy.
    """
    if not isinstance(source, Mapping):
        return "unavailable"
    raw_elements = source.get("elements", source.get("nodes"))
    if not isinstance(raw_elements, Sequence) or isinstance(raw_elements, (str, bytes)):
        return "unavailable"

    expected_qids = {node.qid for node in build_compact_dom_map(html)}
    reported_qids = [
        str(item.get("qid", "")).strip() for item in normalized["elements"]
    ]
    reported_editable_qids = [qid for qid in reported_qids if qid in expected_qids]
    if (
        len(reported_qids) != len(raw_elements)
        or len(reported_editable_qids) != len(set(reported_editable_qids))
        or set(reported_editable_qids) != expected_qids
    ):
        return "incomplete"
    return "available"


class _TextByQidParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, str | None]] = []
        self.text: dict[str, list[str]] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attr_map = _attrs_dict(attrs)
        qid = (
            attr_map.get("data-qid", "").strip()
            if _is_edit_target(tag, attr_map)
            else ""
        )
        # HTMLParser does not synthesize an end tag for ordinary HTML void
        # syntax (``<br>``).  Never push one, otherwise later text leaks into a
        # phantom stack frame.
        if tag not in _VOID_TAGS:
            self.stack.append((tag, qid or None))
        if qid:
            self.text.setdefault(qid, [])

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attr_map = _attrs_dict(attrs)
        qid = (
            attr_map.get("data-qid", "").strip()
            if _is_edit_target(tag, attr_map)
            else ""
        )
        if qid:
            self.text.setdefault(qid, [])

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag.lower():
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        for _, qid in self.stack:
            if qid:
                self.text[qid].append(data)


def _expected_text_by_qid(html: str) -> dict[str, str]:
    # Text extraction uses only element-level immediate content.  A later browser
    # collector may provide a richer textContent value; this static fallback catches
    # model/DOM losses without pretending to perform OCR.
    enriched = ensure_authored_qids(html)
    parser = _TextByQidParser()
    parser.feed(enriched)
    parser.close()
    return {
        qid: text
        for qid, values in parser.text.items()
        if (text := _normal_text(" ".join(values), limit=2000))
    }


def _color_rgb(value: object) -> tuple[int, int, int] | None:
    raw = str(value or "").strip()
    match = _HEX_COLOR.match(raw)
    if match:
        code = match.group("value")
        if len(code) in (3, 4):
            code = "".join(char * 2 for char in code[:3])
        if len(code) in (6, 8):
            return tuple(int(code[index : index + 2], 16) for index in (0, 2, 4))
    match = _RGB_COLOR.match(raw)
    if match:
        values = [part.strip() for part in match.group(1).split(",")]
        if len(values) >= 3:
            try:
                # A transparent computed background is not black.  Its effective
                # colour comes from an ancestor and cannot be inferred locally.
                if len(values) >= 4 and float(values[3]) <= 0:
                    return None
                return tuple(
                    max(0, min(255, round(float(value)))) for value in values[:3]
                )
            except ValueError:
                return None
    return None


def _css_pixel_value(value: object) -> float:
    match = re.match(
        r"^\s*(\d+(?:\.\d+)?)px\s*$", str(value or ""), re.IGNORECASE
    )
    return float(match.group(1)) if match else 0.0


def _measured_cjk_line_fit_detail(
    item: Mapping[str, Any], text: str
) -> str | None:
    """Return a CJK line-fit defect proven by Chromium's resolved font boxes.

    Character-count estimates are unreliable for Korean/Japanese/Chinese.  The
    capture probe already reports the actual Range rectangles after fonts settle;
    use those rectangles and the resolved computed type metrics instead.
    """
    if not text or _CJK_GLYPH.search(text) is None:
        return None
    if bool(item.get("glyph_clipped", False)) or bool(
        item.get("line_box_clipped", False)
    ):
        return "resolved CJK font glyph or line box is clipped"

    raw_fragments = item.get("text_fragments")
    if not isinstance(raw_fragments, Sequence) or isinstance(
        raw_fragments, (str, bytes)
    ):
        return None
    fragments = [
        box
        for value in raw_fragments
        if (box := _coerce_bbox(value)) is not None and box.height > 0
    ]
    if len(fragments) < 2:
        return None

    # Collapse multiple text nodes occupying the same rendered line.
    line_tops: list[float] = []
    line_heights: list[float] = []
    for fragment in sorted(fragments, key=lambda box: (box.y, box.x)):
        match_index = next(
            (
                index
                for index, top in enumerate(line_tops)
                if abs(fragment.y - top) <= 2
            ),
            None,
        )
        if match_index is None:
            line_tops.append(fragment.y)
            line_heights.append(fragment.height)
        else:
            line_heights[match_index] = max(
                line_heights[match_index], fragment.height
            )
    if len(line_tops) < 2:
        return None

    style = item.get("computed_style", item.get("style", {}))
    style = style if isinstance(style, Mapping) else {}
    font_size = _coerce_number(item.get("font_size")) or _css_pixel_value(
        style.get("fontSize", style.get("font_size"))
    )
    line_height = _css_pixel_value(style.get("lineHeight", style.get("line_height")))
    if font_size > 0 and line_height > 0 and line_height < font_size * 1.08:
        return (
            f"resolved CJK line-height {line_height:.1f}px is too tight for "
            f"{font_size:.1f}px text"
        )

    ordered = sorted(zip(line_tops, line_heights))
    for (top, height), (next_top, next_height) in zip(ordered, ordered[1:]):
        if next_top - top < min(height, next_height) * 0.92:
            return "resolved CJK glyph boxes collide across adjacent lines"
    return None


def _contrast_ratio(foreground: object, background: object) -> float | None:
    fg, bg = _color_rgb(foreground), _color_rgb(background)
    if fg is None or bg is None:
        return None

    def luminance(color: tuple[int, int, int]) -> float:
        channels = [value / 255 for value in color]
        adjusted = [
            value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
            for value in channels
        ]
        return 0.2126 * adjusted[0] + 0.7152 * adjusted[1] + 0.0722 * adjusted[2]

    one, two = luminance(fg), luminance(bg)
    return (max(one, two) + 0.05) / (min(one, two) + 0.05)


def _intersection_area(first: BoundingBox, second: BoundingBox) -> float:
    width = max(0.0, min(first.right, second.right) - max(first.x, second.x))
    height = max(0.0, min(first.bottom, second.bottom) - max(first.y, second.y))
    return width * height


def _mode(value: str | None) -> ReferenceMode:
    mode = str(value or STYLE_REFERENCE).strip().lower()
    if mode not in (EXACT_RECONSTRUCTION, STYLE_REFERENCE):
        raise ValueError(f"Unsupported reference mode {value!r}")
    return mode  # type: ignore[return-value]


def inspect_authored_dom(
    html: str,
    browser_metrics: Mapping[str, Any] | None,
    *,
    reference_mode: str = STYLE_REFERENCE,
    strict_structural_off_canvas: bool = False,
) -> DomQualityReport:
    """Classify cheap, deterministic defects from browser-collected metrics.

    The browser collector should include one item per qid with ``bbox``, dimensions,
    computed styles, font/image state and rendered text.  Missing telemetry never
    becomes a synthetic defect: the VQA stage remains responsible for ambiguous
    visual truth. ``strict_structural_off_canvas`` is reserved for source-reflow
    publication: it lets painted layout panels prove viewport escape without
    changing ordinary Authored best-effort review behavior.
    """
    mode = _mode(reference_mode)
    normalized = normalize_browser_metrics(browser_metrics)
    telemetry_status = _telemetry_status(html, browser_metrics, normalized)
    compact = build_compact_dom_map(html, normalized)
    expected = _expected_text_by_qid(html)
    issues: list[DomQualityIssue] = []
    width = normalized["viewport"]["width"] or 1280
    height = normalized["viewport"]["height"] or 720
    elements = normalized["elements"]
    item_by_qid = {str(item.get("qid")): item for item in elements}
    parent_by_qid = {
        str(item.get("qid")): str(item.get("parent_qid") or "") for item in elements
    }

    def related_by_ancestry(first_qid: str, second_qid: str) -> bool:
        current = parent_by_qid.get(first_qid, "")
        seen: set[str] = set()
        while current and current not in seen:
            if current == second_qid:
                return True
            seen.add(current)
            current = parent_by_qid.get(current, "")
        current = parent_by_qid.get(second_qid, "")
        seen.clear()
        while current and current not in seen:
            if current == first_qid:
                return True
            seen.add(current)
            current = parent_by_qid.get(current, "")
        return False

    def browser_proves_owned_content(item: Mapping[str, Any]) -> bool:
        """Limit local geometry repairs to paint owned by the measured target.

        The v2 Chrome probe distinguishes direct glyph/image/shape ownership from
        descendant content.  Structural card and label wrappers are still useful
        layout bindings, but treating their descendants as the wrapper's own
        clipped/occluded paint duplicates the same defect through every ancestor.
        Older external payloads keep their historical conservative behaviour.
        """

        if "has_owned_text" not in item:
            return True
        if bool(item.get("has_owned_text")):
            return True
        return str(item.get("tag", "")).lower() in {
            "img",
            "picture",
            "svg",
            "video",
        }

    occluded_fraction_threshold = 0.2
    continuous_occluded_fraction_threshold = 0.12

    def _rect_union_area(rects: Sequence[BoundingBox]) -> float:
        """Return exact union area for a small set of axis-aligned probe boxes."""

        xs = sorted({edge for rect in rects for edge in (rect.x, rect.right)})
        area = 0.0
        for left, right in zip(xs, xs[1:]):
            if right <= left:
                continue
            intervals = sorted(
                (rect.y, rect.bottom)
                for rect in rects
                if rect.x < right and rect.right > left and rect.bottom > rect.y
            )
            covered = 0.0
            cursor_start: float | None = None
            cursor_end: float | None = None
            for start, end in intervals:
                if cursor_start is None:
                    cursor_start, cursor_end = start, end
                elif start <= cursor_end:
                    cursor_end = max(cursor_end, end)
                else:
                    covered += cursor_end - cursor_start
                    cursor_start, cursor_end = start, end
            if cursor_start is not None and cursor_end is not None:
                covered += cursor_end - cursor_start
            area += (right - left) * covered
        return area

    def _intersection(first: BoundingBox, second: BoundingBox) -> BoundingBox | None:
        left = max(first.x, second.x)
        top = max(first.y, second.y)
        right = min(first.right, second.right)
        bottom = min(first.bottom, second.bottom)
        if right <= left or bottom <= top:
            return None
        return BoundingBox(left, top, right - left, bottom - top)

    def owned_text_occlusion_fraction(
        item: Mapping[str, Any],
        *,
        occluder_filter=None,
    ) -> float | None:
        """Measure how much owned text geometry, rather than its box, is covered.

        Chrome's v3 paint probe intentionally samples both glyphs and an element's
        own background/border. That is useful evidence, but a card laid over a
        decorative panel must not become a blocking text collision when all glyphs
        remain readable. Range rectangles let the detector retain the same 12%
        continuous-paint threshold on the content that users actually read.
        """

        raw_fragments = item.get("text_fragments")
        if not isinstance(raw_fragments, Sequence) or isinstance(
            raw_fragments, (str, bytes)
        ):
            return None
        fragments = [
            box
            for value in raw_fragments
            if isinstance(value, Mapping)
            and (box := _coerce_bbox(value)) is not None
            and box.width > 0
            and box.height > 0
        ]
        if not fragments:
            return 0.0
        raw_occluders = item.get("occlusion_occluder_qids", ())
        if not isinstance(raw_occluders, Sequence) or isinstance(
            raw_occluders, (str, bytes)
        ):
            return None
        occluder_boxes: list[BoundingBox] = []
        for raw_qid in raw_occluders:
            occluder_qid = str(raw_qid)
            if occluder_filter is not None and not occluder_filter(occluder_qid):
                continue
            occluder = item_by_qid.get(occluder_qid)
            if occluder is None or bool(
                occluder.get("intentional", occluder.get("allow_overlap", False))
            ):
                continue
            box = _coerce_bbox(occluder.get("bbox"))
            if box is not None and box.width > 0 and box.height > 0:
                occluder_boxes.append(box)
        if not occluder_boxes:
            return 0.0
        total = sum(fragment.width * fragment.height for fragment in fragments)
        covered = 0.0
        for fragment in fragments:
            intersections = [
                overlap
                for occluder in occluder_boxes
                if (overlap := _intersection(fragment, occluder)) is not None
            ]
            covered += _rect_union_area(intersections)
        return min(1.0, covered / total) if total > 0 else 0.0

    def pixel_occlusion_is_proven(item: Mapping[str, Any]) -> bool:
        probe_version = item.get("occlusion_probe_version")
        if probe_version not in {"owned-content-v2", "owned-paint-v3"}:
            return True
        qid = str(item.get("qid", ""))
        raw = item.get("occlusion_occluder_qids", ())
        occluders = (
            tuple(str(value) for value in raw)
            if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes))
            else ()
        )
        for occluder_qid in occluders:
            if not occluder_qid or occluder_qid == qid:
                continue
            if related_by_ancestry(qid, occluder_qid):
                continue
            occluder = item_by_qid.get(occluder_qid)
            if occluder is None:
                continue
            if bool(occluder.get("intentional", occluder.get("allow_overlap", False))):
                continue
            # v3's elementsFromPoint probe has already discarded transparent
            # layout wrappers.  Requiring the attributed qid to retain owned
            # paint prevents stale/synthetic payloads from claiming proof, but
            # an empty absolute element with an opaque background remains a
            # valid occluder and must fail closed.
            if probe_version == "owned-paint-v3" and not bool(
                occluder.get("has_owned_paint", False)
            ):
                continue
            tag = str(item.get("tag", "")).lower()
            if tag in {"img", "picture", "svg", "video"}:
                return True
            if bool(item.get("has_owned_text")):
                content_fraction = owned_text_occlusion_fraction(item)
                # Older/external telemetry has no Range rectangles. Preserve its
                # conservative behaviour instead of interpreting missing proof as
                # a clean slide.
                return (
                    True
                    if content_fraction is None
                    else content_fraction > continuous_occluded_fraction_threshold
                )
            return False
        return _coerce_number(item.get("occlusion_unbound_count")) > 0

    def title_edge_occlusion_is_proven(item: Mapping[str, Any]) -> bool:
        """Reject narrow paint collisions at the leading edge of title text.

        A vertical accent rail can cover only a tiny fraction of a large title's
        total range area while still running through the first glyph.  The normal
        percentage threshold intentionally filters harmless box overlap, so title
        edges need a small geometry-specific guard based on browser-attributed
        occluders and the actual line fragment rectangles.
        """

        role = str(
            item.get("semantic_role", item.get("semanticRole", "")) or ""
        ).strip().lower()
        if role != "title" or not bool(item.get("has_owned_text")):
            return False
        raw_fragments = item.get("text_fragments")
        raw_occluders = item.get("occlusion_occluder_qids", ())
        if (
            not isinstance(raw_fragments, Sequence)
            or isinstance(raw_fragments, (str, bytes))
            or not isinstance(raw_occluders, Sequence)
            or isinstance(raw_occluders, (str, bytes))
        ):
            return False
        qid = str(item.get("qid", ""))
        fragments = [
            box
            for value in raw_fragments
            if isinstance(value, Mapping)
            and (box := _coerce_bbox(value)) is not None
            and box.width > 0
            and box.height > 0
        ]
        for raw_qid in raw_occluders:
            occluder_qid = str(raw_qid)
            if (
                not occluder_qid
                or occluder_qid == qid
                or related_by_ancestry(qid, occluder_qid)
            ):
                continue
            occluder = item_by_qid.get(occluder_qid)
            if occluder is None or bool(
                occluder.get("intentional", occluder.get("allow_overlap", False))
            ):
                continue
            if not bool(occluder.get("has_owned_paint", False)):
                continue
            occluder_box = _coerce_bbox(occluder.get("bbox"))
            if occluder_box is None:
                continue
            for fragment in fragments:
                overlap = _intersection(fragment, occluder_box)
                if overlap is None:
                    continue
                touches_leading_edge = overlap.x <= fragment.x + 2
                meaningful_intrusion = overlap.width >= 2 and overlap.height >= min(
                    12, fragment.height * 0.2
                )
                if touches_leading_edge and meaningful_intrusion:
                    return True
        return False

    def clipped_by_authored_parent(item: Mapping[str, Any]) -> bool:
        """Recognize viewport-safe clipping of absolute decorative bleed only."""

        if str(item.get("computed_style", {}).get("position", "")) != "absolute":
            return False
        if browser_proves_owned_content(item):
            return False
        box = _coerce_bbox(item.get("bbox"))
        if box is None:
            return False
        escaped_x = box.x < -1 or box.right > width + 1
        escaped_y = box.y < -1 or box.bottom > height + 1
        clipped_x = not escaped_x
        clipped_y = not escaped_y
        current = parent_by_qid.get(str(item.get("qid")), "")
        seen: set[str] = set()
        while current and current not in seen:
            seen.add(current)
            parent = item_by_qid.get(current)
            if parent is None:
                break
            style = parent.get("computed_style", parent.get("style", {}))
            parent_box = _coerce_bbox(parent.get("bbox"))
            if isinstance(style, Mapping) and parent_box is not None:
                overflow_x = str(
                    style.get("overflowX", style.get("overflow", ""))
                ).lower()
                overflow_y = str(
                    style.get("overflowY", style.get("overflow", ""))
                ).lower()
                if (
                    escaped_x
                    and overflow_x in {"hidden", "clip"}
                    and parent_box.x >= -1
                    and parent_box.right <= width + 1
                ):
                    clipped_x = True
                if (
                    escaped_y
                    and overflow_y in {"hidden", "clip"}
                    and parent_box.y >= -1
                    and parent_box.bottom <= height + 1
                ):
                    clipped_y = True
                if clipped_x and clipped_y:
                    return True
            current = parent_by_qid.get(current, "")
        return False

    def off_canvas_owner(qid: str) -> str:
        """Promote escaped descendant paint to its escaped layout container."""

        owner = qid
        current = parent_by_qid.get(qid, "")
        seen: set[str] = set()
        while current and current not in seen:
            seen.add(current)
            ancestor = item_by_qid.get(current)
            ancestor_box = (
                _coerce_bbox(ancestor.get("bbox")) if ancestor is not None else None
            )
            if ancestor_box is None or not (
                ancestor_box.x < -1
                or ancestor_box.y < -1
                or ancestor_box.right > width + 1
                or ancestor_box.bottom > height + 1
            ):
                break
            owner = current
            current = parent_by_qid.get(current, "")
        return owner

    if not normalized["fonts_ready"]:
        issues.append(
            DomQualityIssue(
                "font", "medium", "document fonts did not finish loading", policy=mode
            )
        )
    boxes: list[tuple[str, BoundingBox, dict[str, Any]]] = []
    for item in elements:
        qid = item["qid"]
        box = _coerce_bbox(item.get("bbox")) or BoundingBox(0, 0, 0, 0)
        if box.width > 0 and box.height > 0:
            boxes.append((qid, box, item))
        intentional = bool(item.get("intentional", item.get("allow_overlap", False)))
        geometry_is_actionable = mode == STYLE_REFERENCE or bool(
            item.get("unintentional", False)
        )
        owned_geometry_is_actionable = geometry_is_actionable and (
            browser_proves_owned_content(item) or bool(item.get("unintentional", False))
        )
        if bool(item.get("font_loaded", True)) is False:
            issues.append(
                DomQualityIssue(
                    "font", "medium", "requested font is unavailable", (qid,), mode
                )
            )
        if str(item.get("tag", "")).lower() == "img" or "image" in item:
            image = item.get("image", item)
            if isinstance(image, Mapping) and (
                image.get("complete") is False
                or (
                    image.get("src")
                    and _coerce_number(
                        image.get("natural_width", image.get("naturalWidth"))
                    )
                    <= 0
                )
            ):
                issues.append(
                    DomQualityIssue(
                        "image", "high", "image failed to load", (qid,), mode
                    )
                )
        horizontal_scroll_excess = (
            item["client_width"] > 0 and item["scroll_width"] > item["client_width"] + 1
        )
        vertical_scroll_excess = (
            item["client_height"] > 0
            and item["scroll_height"] > item["client_height"] + 1
        )
        style = item.get("computed_style", item.get("style", {}))
        style = style if isinstance(style, Mapping) else {}
        display = str(style.get("display", "")).strip().lower()
        visibility = str(style.get("visibility", "visible")).strip().lower()
        opacity = _coerce_number(style.get("opacity"), 1.0)
        image = item.get("image") if isinstance(item.get("image"), Mapping) else {}
        css_visible = image.get("cssVisible", image.get("css_visible", True)) is not False
        effective_opacity = _coerce_number(
            image.get("effectiveOpacity", image.get("effective_opacity", opacity)),
            opacity,
        )
        materially_hidden = (
            display == "none"
            or visibility in {"hidden", "collapse"}
            or not css_visible
            or effective_opacity < 0.01
        )
        tag = str(item.get("tag", "")).lower()
        required_content = expected.get(qid, "")
        if materially_hidden and (tag == "img" or required_content):
            issues.append(
                DomQualityIssue(
                    "image" if tag == "img" else "content",
                    "high",
                    "required visual content is hidden by computed CSS",
                    (qid,),
                    mode,
                )
            )
        if tag == "img" and not intentional:
            viewport_intersection = _coerce_number(
                image.get(
                    "viewportIntersectionRatio",
                    image.get("viewport_intersection_ratio", -1),
                ),
                -1,
            )
            if min(box.width, box.height) < 4 or box.width * box.height < 16:
                issues.append(
                    DomQualityIssue(
                        "image",
                        "high",
                        "image is too small to be materially visible",
                        (qid,),
                        mode,
                    )
                )
            if viewport_intersection >= 0 and viewport_intersection < 0.75:
                issues.append(
                    DomQualityIssue(
                        "off_canvas",
                        "high",
                        "most of the image lies outside the slide viewport",
                        (qid,),
                        mode,
                    )
                )

        def overflow_axis_is_actionable(axis: str) -> bool:
            """Do not confuse visible text ink/line-box variance with clipping.

            Chromium commonly reports a few extra scrollHeight pixels for glyph
            ascenders, line boxes, and transformed static containers.  When CSS
            explicitly keeps that axis visible, the content is not clipped; real
            visual harm is covered by sibling-overlap, off-canvas, and VQA checks.
            Missing style stays actionable for older/external probe payloads.
            """

            axis_key = "overflowX" if axis == "x" else "overflowY"
            mode_value = style.get(axis_key, style.get("overflow"))
            if mode_value is None or not str(mode_value).strip():
                return True
            if str(mode_value).strip().lower() != "visible":
                return True
            return False

        horizontal_overflow = horizontal_scroll_excess and overflow_axis_is_actionable("x")
        vertical_overflow = vertical_scroll_excess and overflow_axis_is_actionable("y")
        if owned_geometry_is_actionable and (horizontal_overflow or vertical_overflow):
            severity = (
                "high"
                if (
                    (
                        item["client_width"] > 0
                        and item["scroll_width"] > item["client_width"] * 1.1
                    )
                    or (
                        item["client_height"] > 0
                        and item["scroll_height"] > item["client_height"] * 1.1
                    )
                )
                else "medium"
            )
            issues.append(
                DomQualityIssue(
                    "overflow",
                    severity,
                    "content scroll dimensions exceed its client box",
                    (qid,),
                    mode,
                )
            )
        structural_off_canvas_is_actionable = (
            strict_structural_off_canvas
            and geometry_is_actionable
            and bool(item.get("has_owned_paint", False))
        )
        if (
            (owned_geometry_is_actionable or structural_off_canvas_is_actionable)
            and (
                box.x < -1
                or box.y < -1
                or box.right > width + 1
                or box.bottom > height + 1
            )
            and not intentional
            and not clipped_by_authored_parent(item)
        ):
            issues.append(
                DomQualityIssue(
                    "off_canvas",
                    "high",
                    "element extends outside the slide canvas",
                    (off_canvas_owner(qid),),
                    mode,
                )
            )
        if (
            owned_geometry_is_actionable
            and bool(item.get("clipped", False))
            and not intentional
        ):
            issues.append(
                DomQualityIssue(
                    "clipping",
                    "medium",
                    "browser reports clipped visible content",
                    (qid,),
                    mode,
                )
            )
        occluded_fraction = item.get("occluded_fraction")
        continuous_occluded_fraction = item.get("continuous_occluded_fraction")
        title_edge_occluded = title_edge_occlusion_is_proven(item)
        if (
            owned_geometry_is_actionable
            and not intentional
            and (pixel_occlusion_is_proven(item) or title_edge_occluded)
            and (
                title_edge_occluded
                or
                (
                    occluded_fraction is not None
                    and occluded_fraction > occluded_fraction_threshold
                )
                or (
                    continuous_occluded_fraction is not None
                    and continuous_occluded_fraction
                    > continuous_occluded_fraction_threshold
                )
            )
        ):
            issues.append(
                DomQualityIssue(
                    "overlap",
                    "high" if (occluded_fraction or 0) > 0.5 else "medium",
                    (
                        "painted decoration intersects the leading edge of title text"
                        if title_edge_occluded
                        else "browser pixel-area probe reports a continuous occluding layer"
                    ),
                    (qid,),
                    mode,
                )
            )
        has_rendered_text = any(
            key in item for key in ("rendered_text", "text", "text_content")
        )
        actual_text = _normal_text(
            item.get("rendered_text", item.get("text", item.get("text_content", ""))),
            limit=2000,
        )
        cjk_line_fit_detail = (
            _measured_cjk_line_fit_detail(item, actual_text)
            if owned_geometry_is_actionable
            and bool(item.get("font_loaded", True))
            else None
        )
        if cjk_line_fit_detail:
            issues.append(
                DomQualityIssue(
                    "font", "medium", cjk_line_fit_detail, (qid,), mode
                )
            )
        required = expected.get(
            qid, _normal_text(item.get("expected_text", ""), limit=2000)
        )
        if required and has_rendered_text:
            missing_tokens = [
                token
                for token in _NUMBER_OR_SYMBOL.findall(required)
                if token not in actual_text
            ]
            if not actual_text or missing_tokens:
                detail = (
                    "rendered text is missing"
                    if not actual_text
                    else f"rendered text is missing tokens: {', '.join(missing_tokens[:4])}"
                )
                issues.append(
                    DomQualityIssue(
                        "content",
                        "high" if not actual_text else "medium",
                        detail,
                        (qid,),
                        mode,
                    )
                )
        style = item.get("computed_style", item.get("style", {}))
        if isinstance(style, Mapping):
            measurement = item.get("measurement")
            pixel_contrast_unavailable = (
                item["contrast_ratio"] is None
                and bool(item.get("requires_pixel_contrast"))
                and isinstance(measurement, Mapping)
                and measurement.get("contrast") is False
            )
            if pixel_contrast_unavailable:
                issues.append(
                    DomQualityIssue(
                        "contrast",
                        "high",
                        "browser could not establish pixel contrast for complex painted text",
                        (qid,),
                        mode,
                    )
                )
            else:
                ratio = item["contrast_ratio"] or _contrast_ratio(
                    style.get("color"),
                    style.get("backgroundColor", style.get("background_color")),
                )
                font_size = item["font_size"]
                threshold = (
                    3.0
                    if bool(item.get("large_text", False)) or font_size >= 24
                    else 4.5
                )
                if ratio is not None and ratio < threshold:
                    issues.append(
                        DomQualityIssue(
                            "contrast",
                            "medium" if ratio >= 2 else "high",
                            f"text contrast ratio {ratio:.2f} is below {threshold:.1f}",
                            (qid,),
                            mode,
                        )
                    )

    box_by_qid = {qid: box for qid, box, _item in boxes}

    def rendered_text_is_visible_or_unknown(item: Mapping[str, Any]) -> bool:
        keys = ("rendered_text", "text", "text_content")
        if not any(key in item for key in keys):
            return True
        return bool(
            _normal_text(
                item.get("rendered_text", item.get("text", item.get("text_content", ""))),
                limit=2000,
            )
        )

    def canonical_overlap_pair(first_qid: str, second_qid: str) -> tuple[str, str]:
        """Collapse descendant collision noise to the two sibling layout branches."""

        def lineage(qid: str) -> list[str]:
            result = [qid]
            current = parent_by_qid.get(qid, "")
            seen = {qid}
            while current and current not in seen:
                seen.add(current)
                result.append(current)
                current = parent_by_qid.get(current, "")
            return result

        first_lineage = lineage(first_qid)
        second_lineage = lineage(second_qid)
        second_ancestors = set(second_lineage)
        common = next(
            (qid for qid in first_lineage[1:] if qid in second_ancestors), None
        )
        if common is None:
            return tuple(sorted((first_qid, second_qid)))

        def branch(line: Sequence[str]) -> str:
            common_index = line.index(common)
            return line[max(0, common_index - 1)]

        return tuple(sorted((branch(first_lineage), branch(second_lineage))))

    overlap_pairs: set[tuple[str, str]] = set()

    def qid_in_branch(qid: str, branch_qid: str) -> bool:
        return qid == branch_qid or related_by_ancestry(qid, branch_qid)

    def measured_paint_overlap(
        first_qid: str,
        first_item: Mapping[str, Any],
        second_qid: str,
        second_item: Mapping[str, Any],
    ) -> bool:
        """Require Chrome paint ownership for v2 pairwise collision reports."""

        first_version = first_item.get("occlusion_probe_version")
        second_version = second_item.get("occlusion_probe_version")
        if first_version not in {"owned-content-v2", "owned-paint-v3"} or second_version not in {
            "owned-content-v2",
            "owned-paint-v3",
        }:
            return True
        for target_qid, target, other_qid in (
            (first_qid, first_item, second_qid),
            (second_qid, second_item, first_qid),
        ):
            if not browser_proves_owned_content(target):
                continue
            if target.get("occlusion_probe_version") == "owned-paint-v3" and not bool(
                target.get("has_owned_paint", False)
            ):
                continue
            fraction = target.get("occluded_fraction")
            continuous = target.get("continuous_occluded_fraction")
            if not (
                (
                    fraction is not None
                    and fraction > occluded_fraction_threshold
                )
                or (continuous is not None and continuous > 0.12)
            ):
                continue
            raw = target.get("occlusion_occluder_qids", ())
            if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
                continue
            if any(qid_in_branch(str(value), other_qid) for value in raw):
                tag = str(target.get("tag", "")).lower()
                if tag in {"img", "picture", "svg", "video"}:
                    return True
                if bool(target.get("has_owned_text")):
                    content_fraction = owned_text_occlusion_fraction(
                        target,
                        occluder_filter=lambda value: qid_in_branch(
                            value, other_qid
                        ),
                    )
                    return (
                        True
                        if content_fraction is None
                        else content_fraction
                        > continuous_occluded_fraction_threshold
                    )
        return False

    for index, (qid, box, item) in enumerate(boxes):
        if bool(item.get("intentional", item.get("allow_overlap", False))):
            continue
        if mode == EXACT_RECONSTRUCTION and not bool(item.get("unintentional", False)):
            continue
        for other_qid, other_box, other_item in boxes[index + 1 :]:
            if bool(
                other_item.get("intentional", other_item.get("allow_overlap", False))
            ):
                continue
            if mode == EXACT_RECONSTRUCTION and not bool(
                other_item.get("unintentional", False)
            ):
                continue
            # Containment is the normal DOM layout relationship, not a collision.
            if related_by_ancestry(qid, other_qid):
                continue
            area = _intersection_area(box, other_box)
            smallest = min(box.width * box.height, other_box.width * other_box.height)
            if smallest > 0 and area / smallest >= 0.12:
                if not measured_paint_overlap(qid, item, other_qid, other_item):
                    continue
                pair = canonical_overlap_pair(qid, other_qid)
                if pair[0] == pair[1] or pair in overlap_pairs:
                    continue
                pair_items = (item_by_qid.get(pair[0]), item_by_qid.get(pair[1]))
                pair_boxes = (box_by_qid.get(pair[0]), box_by_qid.get(pair[1]))
                if any(value is None for value in (*pair_items, *pair_boxes)):
                    continue
                if not all(rendered_text_is_visible_or_unknown(value) for value in pair_items):
                    continue
                canonical_area = _intersection_area(pair_boxes[0], pair_boxes[1])
                canonical_smallest = min(
                    pair_boxes[0].width * pair_boxes[0].height,
                    pair_boxes[1].width * pair_boxes[1].height,
                )
                if canonical_smallest <= 0 or canonical_area / canonical_smallest < 0.12:
                    continue
                overlap_pairs.add(pair)
                issues.append(
                    DomQualityIssue(
                        "overlap",
                        "medium",
                        "visible element boxes substantially overlap",
                        pair,
                        mode,
                    )
                )
    # Coalesce same category/target reports while retaining deterministic ordering.
    unique = {(issue.category, issue.qids, issue.detail): issue for issue in issues}
    ordered = tuple(
        sorted(
            unique.values(),
            key=lambda issue: (issue.category, issue.qids, issue.detail),
        )
    )
    return DomQualityReport(
        mode,
        ordered,
        tuple(compact),
        len(elements),
        telemetry_status,
    )
