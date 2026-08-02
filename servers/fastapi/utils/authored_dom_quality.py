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
_WS = re.compile(r"\s+")
_NUMBER_OR_SYMBOL = re.compile(r"(?:\d+(?:[,.]\d+)*%?|[$€£¥₩]|[→←↑↓↔±×÷#@&])")
_HEX_COLOR = re.compile(r"^#(?P<value>[0-9a-fA-F]{3,8})$")
_RGB_COLOR = re.compile(r"^rgba?\((.+)\)$", re.IGNORECASE)


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
    if tag in _NON_RENDERED_TAGS or tag in {
        "g",
        "path",
        "rect",
        "circle",
        "line",
        "polygon",
        "polyline",
    }:
        return False
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
                    "textAlign",
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
        ):
            aliases = {
                "client_width": "clientWidth",
                "client_height": "clientHeight",
                "scroll_width": "scrollWidth",
                "scroll_height": "scrollHeight",
                "font_size": "fontSize",
                "contrast_ratio": "contrastRatio",
            }
            normalized_item[key] = _coerce_number(item.get(key, item.get(aliases[key])))
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
    if (
        len(reported_qids) != len(raw_elements)
        or len(reported_qids) != len(set(reported_qids))
        or set(reported_qids) != expected_qids
    ):
        return "incomplete"
    return "available"


class _TextByQidParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, str | None]] = []
        self.text: dict[str, list[str]] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = _attrs_dict(attrs)
        qid = (
            attr_map.get("data-qid", "").strip()
            if _is_edit_target(tag.lower(), attr_map)
            else ""
        )
        self.stack.append((tag.lower(), qid or None))
        if qid:
            self.text.setdefault(qid, [])

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = _attrs_dict(attrs)
        qid = (
            attr_map.get("data-qid", "").strip()
            if _is_edit_target(tag.lower(), attr_map)
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
) -> DomQualityReport:
    """Classify cheap, deterministic defects from browser-collected metrics.

    The browser collector should include one item per qid with ``bbox``, dimensions,
    computed styles, font/image state and rendered text.  Missing telemetry never
    becomes a synthetic defect: the VQA stage remains responsible for ambiguous
    visual truth.
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
        horizontal_overflow = (
            item["client_width"] > 0 and item["scroll_width"] > item["client_width"] + 1
        )
        vertical_overflow = (
            item["client_height"] > 0
            and item["scroll_height"] > item["client_height"] + 1
        )
        if horizontal_overflow or vertical_overflow:
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
        if (
            geometry_is_actionable
            and (
                box.x < -1
                or box.y < -1
                or box.right > width + 1
                or box.bottom > height + 1
            )
            and not intentional
        ):
            issues.append(
                DomQualityIssue(
                    "off_canvas",
                    "high",
                    "element extends outside the slide canvas",
                    (qid,),
                    mode,
                )
            )
        if (
            geometry_is_actionable
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
        has_rendered_text = any(
            key in item for key in ("rendered_text", "text", "text_content")
        )
        actual_text = _normal_text(
            item.get("rendered_text", item.get("text", item.get("text_content", ""))),
            limit=2000,
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
            ratio = item["contrast_ratio"] or _contrast_ratio(
                style.get("color"),
                style.get("backgroundColor", style.get("background_color")),
            )
            font_size = item["font_size"]
            threshold = (
                3.0 if bool(item.get("large_text", False)) or font_size >= 24 else 4.5
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

    parent_by_qid = {
        str(item.get("qid")): str(item.get("parent_qid") or "") for item in elements
    }

    def related_by_ancestry(first_qid: str, second_qid: str) -> bool:
        current = parent_by_qid.get(first_qid, "")
        while current:
            if current == second_qid:
                return True
            current = parent_by_qid.get(current, "")
        current = parent_by_qid.get(second_qid, "")
        while current:
            if current == first_qid:
                return True
            current = parent_by_qid.get(current, "")
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
                issues.append(
                    DomQualityIssue(
                        "overlap",
                        "medium",
                        "visible element boxes substantially overlap",
                        tuple(sorted((qid, other_qid))),
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
