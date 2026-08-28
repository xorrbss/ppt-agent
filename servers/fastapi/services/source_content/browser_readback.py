"""Fail closed source-verbatim readback against Chromium DOM telemetry.

The source-content manifest identifies immutable blocks.  This module binds each
block to an explicit ``data-source-block-id`` (or its metrics equivalent) and
checks the *rendered* Chromium observation, rather than trusting HTML strings.
The resulting evidence keeps geometry and visibility facts attached to the
source block for publication gates and later audit.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import html
from html.parser import HTMLParser
import math
import re
from typing import Any


class BrowserSourceReadbackError(ValueError):
    """Publication-blocking failure in browser-observed source readback."""

    def __init__(self, code: str, *, detail: str, block_ids: tuple[str, ...] = (), slide_indices: tuple[int, ...] = (), order_violations: int = 0) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail
        self.block_ids = block_ids
        self.slide_indices = slide_indices
        self.order_violations = order_violations


@dataclass(frozen=True, slots=True)
class BrowserSourceBlockEvidence:
    source_block_id: str
    qid: str
    rendered_text: str
    bbox: dict[str, float]
    visible: bool
    clipped: bool
    occluded_fraction: float | None
    continuous_occluded_fraction: float | None
    kind: str = "text"
    natural_width: float | None = None
    natural_height: float | None = None
    semantic_role: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "sourceBlockId": self.source_block_id,
            "qid": self.qid,
            "renderedText": self.rendered_text,
            "bbox": dict(self.bbox),
            "visible": self.visible,
            "clipped": self.clipped,
            "occludedFraction": self.occluded_fraction,
            "continuousOccludedFraction": self.continuous_occluded_fraction,
            "kind": self.kind,
            "naturalWidth": self.natural_width,
            "naturalHeight": self.natural_height,
            "semanticRole": self.semantic_role,
        }


@dataclass(frozen=True, slots=True)
class BrowserSourceReadbackResult:
    checked_slide_count: int
    checked_block_count: int
    evidence: tuple[BrowserSourceBlockEvidence, ...] = ()
    missing_block_ids: tuple[str, ...] = ()
    order_violations: int = 0
    visibility_failures: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return not (
            self.missing_block_ids
            or self.order_violations
            or self.visibility_failures
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "checkedSlideCount": self.checked_slide_count,
            "checkedBlockCount": self.checked_block_count,
            "evidence": [item.as_dict() for item in self.evidence],
            "missingBlockIds": list(self.missing_block_ids),
            "orderViolations": self.order_violations,
            "visibilityFailures": list(self.visibility_failures),
            "passed": self.passed,
        }


class _StampNode:
    __slots__ = ("start", "end", "text", "hidden", "stamped", "source_id", "has_qid", "has_semantic_role")

    def __init__(self, start: int, hidden: bool) -> None:
        self.start = start
        self.end: int | None = None
        self.text: list[str] = []
        self.hidden = hidden
        self.stamped = False
        self.source_id: str | None = None
        self.has_qid = False
        self.has_semantic_role = False


class _StampParser(HTMLParser):
    """Token-preserving parser used only to add source IDs before capture."""

    _HIDDEN = frozenset({"script", "style", "noscript", "template"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.tokens: list[str] = []
        self.stack: list[_StampNode] = []
        self.nodes: list[_StampNode] = []

    def _append_text(self, value: str) -> None:
        for node in self.stack:
            if node.hidden:
                break
            node.text.append(value)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        raw = self.get_starttag_text() or f"<{tag}>"
        index = len(self.tokens)
        self.tokens.append(raw)
        inherited_hidden = bool(self.stack and self.stack[-1].hidden)
        node = _StampNode(index, inherited_hidden or tag.lower() in self._HIDDEN)
        for name, value in attrs:
            lowered = name.lower()
            if lowered == "data-source-block-id":
                node.stamped = True
                node.source_id = value or ""
            elif lowered == "data-qid":
                node.has_qid = bool(value)
            elif lowered in {"data-semantic-role", "data-role"} or lowered == "role":
                node.has_semantic_role = bool(value)
        self.nodes.append(node)
        self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        self.tokens.append(f"</{tag}>")
        if self.stack:
            self.stack[-1].end = len(self.tokens) - 1
            self.stack.pop()

    def handle_data(self, data: str) -> None:
        self.tokens.append(data)
        self._append_text(data)

    def handle_entityref(self, name: str) -> None:
        self.tokens.append(f"&{name};")
        self._append_text(html.unescape(f"&{name};"))

    def handle_charref(self, name: str) -> None:
        self.tokens.append(f"&#{name};")
        self._append_text(html.unescape(f"&#{name};"))

    def handle_comment(self, data: str) -> None:
        self.tokens.append(f"<!--{data}-->")

    def handle_decl(self, decl: str) -> None:
        self.tokens.append(f"<!{decl}>")

    def handle_pi(self, data: str) -> None:
        self.tokens.append(f"<?{data}>")


def stamp_source_block_ids(html_source: str, block_texts: Sequence[tuple[str, str]]) -> str:
    """Stamp one deterministic source ID on each uniquely matching DOM element.

    Matching is against an element's complete rendered text (nested text is
    included and hidden script/style text is ignored).  A missing or ambiguous
    match raises instead of guessing, keeping the browser binding fail closed.
    """

    parser = _StampParser()
    try:
        parser.feed(str(html_source))
        parser.close()
    except Exception as error:  # pragma: no cover - HTMLParser is forgiving
        raise BrowserSourceReadbackError("source_content_browser_readback_invalid", detail="authored slide HTML could not be parsed") from error
    used: set[str] = set()
    for raw_block_id, raw_expected in block_texts:
        block_id, expected = str(raw_block_id).strip(), _text(raw_expected)
        if not block_id or not expected or block_id in used:
            raise BrowserSourceReadbackError("source_content_browser_readback_invalid", detail="source block IDs/text must be unique and non-empty")
        matching = [node for node in parser.nodes if not node.hidden and _text("".join(node.text)) == expected]
        conflicts = [node for node in matching if node.stamped and node.source_id != block_id]
        if conflicts:
            raise BrowserSourceReadbackError("source_content_browser_readback_failed", detail="source block text is already bound to a conflicting ID", block_ids=(block_id,))
        candidates = [node for node in matching if not node.stamped or node.source_id == block_id]
        # A wrapper and its leaf can expose the same rendered text (for example
        # ``<p><span>text</span></p>``).  Bind the deepest unique match; two
        # unrelated leaves remain ambiguous and fail closed.
        candidates = [
            node for node in candidates
            if not any(
                other is not node and other.start > node.start and
                (node.end is None or (other.end is not None and other.end <= node.end))
                for other in candidates
            )
        ]
        if len(candidates) != 1:
            raise BrowserSourceReadbackError("source_content_browser_readback_failed", detail="source block text has no unique DOM element", block_ids=(block_id,))
        node = candidates[0]
        raw_tag = parser.tokens[node.start]
        close = raw_tag.rfind("/") if raw_tag.rstrip().endswith("/>") else raw_tag.rfind(">")
        if close < 0:
            raise BrowserSourceReadbackError("source_content_browser_readback_invalid", detail="invalid DOM start tag", block_ids=(block_id,))
        attributes = "" if node.stamped else source_block_data_attribute(block_id)
        if not node.has_qid:
            attributes = (attributes + " " if attributes else "") + f'data-qid="{deterministic_source_qid(block_id)}"'
        if not node.has_semantic_role:
            # Keep source text in the same semantic contract as authored text.
            # Heading tags are title targets; all other visible source text is
            # body content.  This marker is observed by Chromium and avoids
            # accepting an HTML-only match.
            tag_match = re.match(r"\s*<\s*([a-z0-9]+)", raw_tag, flags=re.IGNORECASE)
            semantic_role = "title" if tag_match and tag_match.group(1).lower() in {"h1", "h2"} else "body"
            attributes = (attributes + " " if attributes else "") + f'data-semantic-role="{semantic_role}"'
        if attributes:
            parser.tokens[node.start] = raw_tag[:close].rstrip() + " " + attributes + raw_tag[close:]
        node.stamped = True
        node.source_id = block_id
        used.add(block_id)
    return "".join(parser.tokens)


def deterministic_source_block_id(source_unit_id: str, order: int, text: str) -> str:
    """Return a stable fallback ID for newly materialized source blocks.

    Existing manifest IDs remain authoritative.  This helper is for adapters
    that need to stamp a block before a manifest is persisted; it is deliberately
    content addressed so retries cannot produce a different binding.
    """

    canonical = re.sub(r"\s+", " ", str(text)).strip()
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return f"source-block:{source_unit_id}:{int(order)}:{digest}"


def deterministic_source_image_block_id(
    source_unit_id: str,
    order: int,
    asset_digest: str,
    occurrence: int = 1,
) -> str:
    """Return a stable identity for one source-image occurrence.

    Image bytes may be deduplicated in the manifest, so the occurrence ordinal
    is part of the identity.  Including the asset digest prevents two images
    at the same source order from silently aliasing during retries.
    """

    unit = str(source_unit_id).strip()
    digest = str(asset_digest).strip()
    if not unit or not digest or int(occurrence) < 1:
        raise ValueError("source image occurrence identity is invalid")
    canonical = f"{unit}|image|{int(order)}|{digest}|{int(occurrence)}"
    suffix = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return f"source-block:{unit}:image:{int(order)}:{int(occurrence)}:{suffix}"


def stamp_source_image_block_ids(
    html_source: str,
    image_block_ids: Sequence[str],
) -> str:
    """Bind source-image occurrence IDs to actual ``<img>`` elements.

    Binding follows DOM order and only touches previously unbound images.  A
    cardinality mismatch fails closed; this keeps duplicate asset bytes from
    collapsing multiple occurrences into one browser owner.
    """

    parser = _StampParser()
    parser.feed(str(html_source))
    parser.close()
    ids = [str(value).strip() for value in image_block_ids]
    if not ids and not any(
        parser.tokens[node.start].lstrip().lower().startswith("<img")
        and not node.hidden
        and not _is_decorative_image_tag(parser.tokens[node.start])
        for node in parser.nodes
    ):
        return str(html_source)
    if len(ids) != len(set(ids)) or any(not value for value in ids):
        raise BrowserSourceReadbackError(
            "source_content_browser_readback_invalid",
            detail="source image block IDs must be unique and non-empty",
        )
    image_nodes = [
        node for node in parser.nodes
        if parser.tokens[node.start].lstrip().lower().startswith("<img")
        and not node.hidden
    ]
    # Decorative/template images are outside the source occurrence contract.
    # They must be explicitly marked so a source binding can never silently
    # consume them.  Unknown unmarked images remain eligible; if a caller has
    # mixed unmarked decorative and source images it must fail by cardinality
    # rather than guessing.
    available = [
        node for node in image_nodes
        if not node.stamped and not _is_decorative_image_tag(parser.tokens[node.start])
    ]
    existing = [node.source_id or "" for node in image_nodes if node.stamped and node.source_id]
    if not available and existing == ids:
        # Retry-safe: a second stamping pass with the same occurrence contract
        # must not append duplicate attributes or consume a different image.
        return str(html_source)
    if len(available) != len(ids):
        raise BrowserSourceReadbackError(
            "source_content_browser_readback_failed",
            detail="source image occurrence count does not match DOM images",
            block_ids=tuple(ids),
        )
    for node, block_id in zip(available, ids, strict=True):
        raw_tag = parser.tokens[node.start]
        close = raw_tag.rfind("/") if raw_tag.rstrip().endswith("/>") else raw_tag.rfind(">")
        if close < 0:
            raise BrowserSourceReadbackError(
                "source_content_browser_readback_invalid",
                detail="invalid image start tag",
                block_ids=(block_id,),
            )
        attrs = source_block_data_attribute(block_id)
        attrs += f' data-qid="{deterministic_source_qid(block_id)}"'
        # Source occurrences participate in the same semantic capacity gate as
        # authored text.  Stamp the role at the DOM boundary so Chromium
        # readback can prove that this is an intentional image element rather
        # than merely an HTML string containing an <img> tag.
        attrs += ' data-semantic-role="image"'
        parser.tokens[node.start] = raw_tag[:close].rstrip() + " " + attrs + raw_tag[close:]
        node.stamped = True
        node.source_id = block_id
    return "".join(parser.tokens)


def _is_decorative_image_tag(raw_tag: str) -> bool:
    """Return whether an image is explicitly outside source content."""

    attrs: dict[str, str] = {}
    for match in re.finditer(
        r"([:\w-]+)(?:\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+)))?",
        raw_tag,
        flags=re.IGNORECASE,
    ):
        name = match.group(1).lower()
        # Ignore the tag name itself, which the expression also matches.
        if name == "img":
            continue
        attrs[name] = (match.group(2) or match.group(3) or match.group(4) or "").strip().lower()
    if attrs.get("aria-hidden") == "true" or attrs.get("role") in {"presentation", "none"}:
        return True
    if attrs.get("data-source-image") in {"false", "0", "no"}:
        return True
    return attrs.get("data-decorative") in {"true", "1", "yes"} or attrs.get("data-decoration") in {"true", "1", "yes"}


def source_block_data_attribute(source_block_id: str) -> str:
    """Render the escaped attribute used by authored HTML instrumentation."""

    value = str(source_block_id).strip()
    if not value or any(char in value for char in "\r\n\t"):
        raise ValueError("source block id must be a non-empty single-line value")
    return f'data-source-block-id="{html.escape(value, quote=True)}"'


def deterministic_source_qid(source_block_id: str) -> str:
    """Stable qid used when source HTML has no editable identity."""

    return "source-block-" + hashlib.sha256(str(source_block_id).encode("utf-8")).hexdigest()[:16]


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json", by_alias=True)
    if not isinstance(value, Mapping):
        raise BrowserSourceReadbackError("source_content_browser_readback_invalid", detail=f"{label} must be an object")
    return value


def _field(value: Mapping[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in value:
            return value[name]
    return default


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _finite_bbox(value: Any) -> dict[str, float] | None:
    if not isinstance(value, Mapping):
        return None
    result: dict[str, float] = {}
    for key in ("x", "y", "width", "height"):
        try:
            number = float(value[key])
        except (KeyError, TypeError, ValueError):
            return None
        if not math.isfinite(number) or (key in {"width", "height"} and number <= 0):
            return None
        result[key] = number
    return result


def _metric_visible(metric: Mapping[str, Any], bbox: Mapping[str, float]) -> bool | None:
    explicit = _field(metric, "visible", "css_visible", "cssVisible", default=None)
    style = _field(metric, "computed_style", "computedStyle", default={})
    if not isinstance(style, Mapping):
        return None
    # The element's own opacity does not expose a transparent ancestor.  The
    # probe must report the cumulative ancestor opacity and CSS visibility.
    effective_opacity = _field(metric, "effective_opacity", "effectiveOpacity", default=None)
    if not isinstance(explicit, bool) or effective_opacity is None or not all(key in style for key in ("display", "visibility")):
        return None
    display = str(style.get("display", "")).lower()
    visibility = str(style.get("visibility", "")).lower()
    try:
        opacity = float(effective_opacity)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(opacity):
        return None
    return explicit is not False and display not in {"none", "contents"} and visibility not in {"hidden", "collapse"} and opacity > 0 and bbox["width"] > 0 and bbox["height"] > 0


def _block_text(block: Mapping[str, Any]) -> str:
    payload = _mapping(_field(block, "payload", default={}), "source block payload")
    if isinstance(payload.get("text"), str):
        return _text(payload["text"])
    runs = payload.get("runs", ())
    if isinstance(runs, Sequence) and not isinstance(runs, (str, bytes)):
        return _text("".join(str(run.get("text", "")) if isinstance(run, Mapping) else str(run) for run in runs))
    return ""


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _slides_metrics(browser_metrics: Any) -> list[Mapping[str, Any]]:
    if isinstance(browser_metrics, Sequence) and not isinstance(browser_metrics, (str, bytes)):
        return [_mapping(item, "browser slide metrics") for item in browser_metrics]
    root = _mapping(browser_metrics, "browser metrics")
    slides = _field(root, "slides", "slideMetrics", default=None)
    if isinstance(slides, Sequence) and not isinstance(slides, (str, bytes)):
        return [_mapping(item, "browser slide metrics") for item in slides]
    return [root]


def verify_browser_source_readback(*, manifest: Any, outline: Any, browser_metrics: Any, htmls: Sequence[str] | None = None) -> BrowserSourceReadbackResult:
    """Validate source IDs, rendered text, geometry, order, and visibility.

    ``browser_metrics`` is the payload emitted by ``ensure_browser_metrics``
    (one object per slide, or ``{"slides": [...]}``). Every admitted text block
    must have one explicit source-block binding and a measured, visible DOM box.
    Missing telemetry, clipping, occlusion, duplicate/unknown IDs, and rendered
    text mismatches all fail closed.
    """

    manifest_value = _mapping(manifest, "source manifest")
    outline_value = _mapping(outline, "source outline")
    units = _field(manifest_value, "units", default=())
    slides = _field(outline_value, "slides", default=())
    if not isinstance(units, Sequence) or isinstance(units, (str, bytes)) or not isinstance(slides, Sequence) or isinstance(slides, (str, bytes)):
        raise BrowserSourceReadbackError("source_content_browser_readback_invalid", detail="manifest units and outline slides must be sequences")
    metric_slides = _slides_metrics(browser_metrics)
    if len(units) != len(slides) or len(metric_slides) != len(slides) or (htmls is not None and len(htmls) != len(slides)):
        raise BrowserSourceReadbackError("source_content_browser_readback_failed", detail="source and browser slide cardinality differs", slide_indices=tuple(range(max(len(slides), len(metric_slides)))))

    blocks_by_unit: dict[str, dict[str, Mapping[str, Any]]] = {}
    ordered_ids_by_unit: dict[str, list[str]] = {}
    unit_order: list[tuple[int, str]] = []
    all_block_ids: set[str] = set()
    for raw_unit in units:
        unit = _mapping(raw_unit, "source unit")
        unit_id = str(_field(unit, "sourceUnitId", "source_unit_id", default=""))
        if not unit_id or unit_id in blocks_by_unit:
            raise BrowserSourceReadbackError("source_content_browser_readback_invalid", detail="source unit identity is missing or duplicated")
        blocks_by_unit[unit_id] = {}
        ordered_blocks: list[tuple[int, str]] = []
        try:
            unit_order.append((int(_field(unit, "sourceIndex", "source_index", default=0)), unit_id))
            raw_blocks = _field(unit, "blocks", default=())
            if not isinstance(raw_blocks, Sequence) or isinstance(raw_blocks, (str, bytes)):
                raise ValueError("source blocks must be a sequence")
            for raw_block in raw_blocks:
                block = _mapping(raw_block, "source block")
                block_id = str(_field(block, "sourceBlockId", "source_block_id", default=""))
                if not block_id or block_id in all_block_ids:
                    raise ValueError("source block identity is missing or duplicated")
                all_block_ids.add(block_id)
                blocks_by_unit[unit_id][block_id] = block
                if str(block.get("kind", "")) in {"text", "image"}:
                    # Images are occurrences, not merely unique assets. Keep
                    # each occurrence in deterministic source reading order.
                    ordered_blocks.append((int(_field(block, "order", default=0)), block_id))
        except (TypeError, ValueError) as error:
            raise BrowserSourceReadbackError("source_content_browser_readback_invalid", detail=str(error)) from error
        ordered_ids_by_unit[unit_id] = [item[1] for item in sorted(ordered_blocks)]
    expected_unit_order = [item[1] for item in sorted(unit_order)]

    evidence: list[BrowserSourceBlockEvidence] = []
    missing: list[str] = []
    visibility_failures: list[str] = []
    order_violations = 0
    checked = 0
    failed_slides: set[int] = set()
    for index, (raw_slide, metrics) in enumerate(zip(slides, metric_slides, strict=True)):
        slide = _mapping(raw_slide, "outline slide")
        unit_id = str(_field(slide, "sourceUnitId", "source_unit_id", default=""))
        expected_ids = ordered_ids_by_unit.get(unit_id, [])
        reading_order = _field(slide, "readingOrder", "reading_order", default=())
        if not isinstance(reading_order, Sequence) or isinstance(reading_order, (str, bytes)):
            raise BrowserSourceReadbackError("source_content_browser_readback_invalid", detail="outline reading order must be a sequence")
        outline_ids = [str(item) for item in reading_order]
        allowed_ids = {
            block_id for block_id, block in blocks_by_unit.get(unit_id, {}).items()
            if str(_field(block, "kind", default="")) in {"text", "image"}
        }
        if any(block_id not in allowed_ids for block_id in outline_ids) or len(outline_ids) != len(set(outline_ids)):
            order_violations += 1
            failed_slides.add(index)
        outline_text_ids = [str(item) for item in reading_order if str(item) in expected_ids]
        if expected_unit_order[index] != unit_id or outline_text_ids != expected_ids:
            order_violations += 1
            failed_slides.add(index)
        if htmls is not None and metrics.get("source_digest") != "sha256:" + hashlib.sha256(htmls[index].encode("utf-8")).hexdigest():
            raise BrowserSourceReadbackError("source_content_browser_readback_failed", detail="browser source digest does not match the captured HTML", slide_indices=(index,))
        if _field(metrics, "probe_error", "probeError", default=None) or _field(metrics, "fonts_ready", "fontsReady", default=None) is not True:
            raise BrowserSourceReadbackError("source_content_browser_readback_failed", detail="browser font/probe telemetry is incomplete", slide_indices=(index,))
        canvas_width, canvas_height = _number(metrics.get("width")), _number(metrics.get("height"))
        if canvas_width is None or canvas_height is None or canvas_width <= 0 or canvas_height <= 0:
            raise BrowserSourceReadbackError("source_content_browser_readback_failed", detail="browser canvas geometry is unavailable", slide_indices=(index,))
        raw_elements = _field(metrics, "elements", default=())
        if not isinstance(raw_elements, Sequence) or isinstance(raw_elements, (str, bytes)):
            raise BrowserSourceReadbackError("source_content_browser_readback_failed", detail="browser elements telemetry is missing", slide_indices=(index,))
        observed: list[tuple[int, str, BrowserSourceBlockEvidence]] = []
        seen: set[str] = set()
        seen_qids: set[str] = set()
        for dom_index, raw_metric in enumerate(raw_elements):
            metric = _mapping(raw_metric, "browser element metric")
            block_id = str(_field(metric, "sourceBlockId", "source_block_id", "dataSourceBlockId", "data_source_block_id", default="") or "")
            if not block_id:
                continue
            if block_id in seen or block_id not in expected_ids:
                order_violations += 1
                failed_slides.add(index)
                continue
            bbox = _finite_bbox(_field(metric, "bbox", default=None))
            qid = str(_field(metric, "qid", default="") or "").strip()
            rendered = _text(_field(metric, "rendered_text", "renderedText", "innerText", "text", default=""))
            visible = _metric_visible(metric, bbox or {}) if bbox is not None else None
            clip_values = [_field(metric, "clipped", default=None), _field(metric, "glyph_clipped", "glyphClipped", default=None), _field(metric, "line_box_clipped", "lineBoxClipped", default=None)]
            clipped = any(value is True for value in clip_values)
            clipping_known = all(isinstance(value, bool) for value in clip_values)
            occluded = _number(_field(metric, "occluded_fraction", "occludedFraction", default=None))
            continuous = _number(_field(metric, "continuous_occluded_fraction", "continuousOccludedFraction", default=None))
            occlusion_known = (
                occluded is not None
                and continuous is not None
                and 0 <= occluded <= 1
                and 0 <= continuous <= 1
                and occluded == 0
                and continuous == 0
            )
            measurement = _field(metric, "measurement", default={})
            occlusion_known = (
                occlusion_known
                and isinstance(measurement, Mapping)
                and measurement.get("occlusion") is True
            )
            occlusion_evidence = _field(
                metric,
                "occlusion_measurement_evidence",
                "occlusionMeasurementEvidence",
                default={},
            )
            occlusion_known = (
                occlusion_known
                and isinstance(occlusion_evidence, Mapping)
                and occlusion_evidence.get("status") == "measured"
            )
            block = blocks_by_unit[unit_id][block_id]
            block_kind = str(_field(block, "kind", default="text"))
            expected_text = _block_text(block) if block_kind == "text" else ""
            payload = _mapping(_field(block, "payload", default={}), "source block payload")
            in_canvas = bbox is not None and bbox["x"] >= 0 and bbox["y"] >= 0 and bbox["x"] + bbox["width"] <= canvas_width and bbox["y"] + bbox["height"] <= canvas_height
            image_payload = _field(metric, "image", default=None)
            if not isinstance(image_payload, Mapping) and (
                str(_field(metric, "tag", default="")).lower() == "img"
            ):
                image_payload = metric
            natural_width = _number(
                _field(image_payload or {}, "natural_width", "naturalWidth", default=None)
            ) if isinstance(image_payload, Mapping) else None
            natural_height = _number(
                _field(image_payload or {}, "natural_height", "naturalHeight", default=None)
            ) if isinstance(image_payload, Mapping) else None
            expected_natural_width = _number(
                _field(payload, "naturalWidth", "natural_width", "intrinsicWidth", "intrinsic_width", default=None)
            )
            expected_natural_height = _number(
                _field(payload, "naturalHeight", "natural_height", "intrinsicHeight", "intrinsic_height", default=None)
            )
            image_failure = False
            semantic_role = str(_field(metric, "semantic_role", "semanticRole", "role", default="") or "").strip()
            declared_qid = str(_field(block, "qid", "sourceQid", "source_qid", default="") or "").strip()
            qid_failure = bool(declared_qid and qid != declared_qid)
            if block_kind == "image":
                complete = _field(image_payload or {}, "complete", default=None) if isinstance(image_payload, Mapping) else None
                image_visible = _field(image_payload or {}, "cssVisible", "css_visible", default=None) if isinstance(image_payload, Mapping) else None
                image_failure = (
                    str(_field(metric, "tag", default="")).lower() != "img"
                    or complete is not True
                    or natural_width is None or natural_width <= 0
                    or natural_height is None or natural_height <= 0
                    or image_visible is False
                    or not semantic_role
                    or not (semantic_role.lower().startswith("image") or semantic_role.lower().endswith("image"))
                    or (expected_natural_width is not None and abs(natural_width - expected_natural_width) > 0.5)
                    or (expected_natural_height is not None and abs(natural_height - expected_natural_height) > 0.5)
                )
            text_failure = block_kind == "text" and (not expected_text or rendered != expected_text or not semantic_role)
            font_failure = block_kind == "text" and _field(metric, "font_loaded", "fontLoaded", default=None) is not True
            if not qid or qid in seen_qids or qid_failure or not in_canvas or visible is not True or font_failure or not clipping_known or clipped or not occlusion_known or text_failure or image_failure:
                visibility_failures.append(block_id)
                failed_slides.add(index)
            seen.add(block_id)
            seen_qids.add(qid)
            observed.append((dom_index, block_id, BrowserSourceBlockEvidence(block_id, qid, rendered, bbox or {}, bool(visible), clipped, occluded, continuous, block_kind, natural_width, natural_height, semantic_role)))
        actual_ids = [item[1] for item in sorted(observed)]
        for block_id in expected_ids:
            checked += 1
            if block_id not in seen:
                missing.append(block_id)
                failed_slides.add(index)
        if actual_ids != expected_ids:
            order_violations += 1
            failed_slides.add(index)
        evidence.extend(item[2] for item in sorted(observed))

    result = BrowserSourceReadbackResult(len(slides), checked, tuple(evidence), tuple(dict.fromkeys(missing)), order_violations, tuple(dict.fromkeys(visibility_failures)))
    if not result.passed:
        raise BrowserSourceReadbackError("source_content_browser_readback_failed", detail="browser source text/geometry/visibility contract failed", block_ids=tuple(dict.fromkeys((*result.missing_block_ids, *result.visibility_failures))), slide_indices=tuple(sorted(failed_slides)), order_violations=result.order_violations)
    return result


# Descriptive alias for callers that use the contract name in prose.
verify_source_verbatim_browser_readback = verify_browser_source_readback


__all__ = [
    "BrowserSourceBlockEvidence",
    "BrowserSourceReadbackError",
    "BrowserSourceReadbackResult",
    "deterministic_source_block_id",
    "deterministic_source_image_block_id",
    "deterministic_source_qid",
    "source_block_data_attribute",
    "stamp_source_block_ids",
    "stamp_source_image_block_ids",
    "verify_browser_source_readback",
    "verify_source_verbatim_browser_readback",
]
