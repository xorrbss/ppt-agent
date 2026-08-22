"""Build the Authored-declared semantic half of one dual-output candidate.

This compiler deliberately stays below browser/native admission.  It records the
meaning and source content Authored actually emitted, while geometry, paint ownership,
serializer readiness, and editability remain server-issued derived receipts.
"""

from __future__ import annotations

import hashlib
from html.parser import HTMLParser
import re
from collections.abc import Callable
from typing import Any

from utils.authored_dom_quality import validate_declared_authored_qids


_COMPLEX_CSS = re.compile(
    r"(?:filter|mask|clip-path|mix-blend-mode|backdrop-filter|box-shadow|"
    r"linear-gradient|radial-gradient|conic-gradient|perspective)\s*[:(]",
    re.IGNORECASE,
)
_TEXT_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6", "p", "span", "li", "dt", "dd", "label", "strong", "em", "small"})
_GROUP_TAGS = frozenset({"main", "section", "article", "aside", "header", "footer", "nav", "ul", "ol"})


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _normalized_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


class _DeclaredElementParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str | None] = []
        self.elements: list[dict[str, Any]] = []
        self.by_id: dict[str, dict[str, Any]] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name.lower(): value or "" for name, value in attrs}
        qid = values.get("data-qid") or None
        parent_id = next((item for item in reversed(self.stack) if item), None)
        self.stack.append(qid)
        if qid is None:
            return
        classes = frozenset(values.get("class", "").lower().split())
        style = values.get("style", "")
        kind = self._kind(tag.lower(), values, classes, style)
        payload: dict[str, Any] | None = None
        raster_reason: str | None = None
        # A semantic source may declare only resources whose actual bytes have
        # already been resolved into the candidate transaction.  The ordinary
        # Authored HTML compiler does not own that resolver yet, so text/images
        # remain truthful raster references instead of inventing a font face or
        # treating a locator hash as an asset-content hash.  The bounded product
        # vertical slice supplies resolved manifests and emits typed text/images.
        if kind == "shape":
            payload = {
                "preset": "rectangle",
                "fill": None,
                "stroke": None,
                "radiusPt": 0,
            }
        elif kind == "image":
            kind = "complexIsland"
            raster_reason = "actual_asset_bytes_unresolved"
        elif kind in {"text", "group"}:
            kind = "complexIsland"
            raster_reason = "actual_font_face_unresolved"
        else:
            raster_reason = self._raster_reason(tag.lower(), classes, style)
        element = {
            "elementId": qid,
            "kind": kind,
            "semanticRole": self._role(tag.lower(), values, classes),
            "parentId": parent_id,
            "childIds": [],
            "intendedOrder": len(self.elements),
            "requiredFeatures": self._required_features(tag.lower(), classes, style),
            "_sourceMaterial": {
                "tag": tag.lower(),
                "style": style,
                "class": values.get("class", ""),
                "src": values.get("src", ""),
                "alt": values.get("alt", ""),
                "text": [],
            },
        }
        if payload is not None:
            element["payload"] = payload
        else:
            element["rasterReason"] = raster_reason
        self.elements.append(element)
        self.by_id[qid] = element
        if parent_id is not None:
            self.by_id[parent_id]["childIds"].append(qid)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, _tag: str) -> None:
        if self.stack:
            self.stack.pop()

    def handle_data(self, data: str) -> None:
        text = _normalized_text(data)
        if not text:
            return
        owner_id = next((item for item in reversed(self.stack) if item), None)
        if owner_id is None:
            return
        owner = self.by_id[owner_id]
        owner["_sourceMaterial"]["text"].append(text)

    def finish(self) -> list[dict[str, Any]]:
        for element in self.elements:
            material = element.pop("_sourceMaterial")
            if element["kind"] != "shape":
                source_bytes = repr(
                    (
                        material["tag"],
                        material["style"],
                        material["class"],
                        material["src"],
                        material["alt"],
                        tuple(material["text"]),
                    )
                ).encode("utf-8")
                element["sourceRef"] = _sha256_bytes(source_bytes)
        return self.elements

    @staticmethod
    def _kind(tag: str, values: dict[str, str], classes: frozenset[str], style: str) -> str:
        if tag == "img":
            return "image"
        if tag in {"svg", "canvas", "table"} or "chart" in classes or "complex-island" in classes:
            return "complexIsland"
        if _COMPLEX_CSS.search(style):
            return "complexIsland"
        if "simple-shape" in classes or values.get("role") == "presentation":
            return "shape"
        if tag in _TEXT_TAGS:
            return "text"
        return "group" if tag in _GROUP_TAGS else "shape"

    @staticmethod
    def _role(tag: str, values: dict[str, str], classes: frozenset[str]) -> str:
        if values.get("data-semantic-role"):
            return values["data-semantic-role"]
        if tag == "h1":
            return "title"
        if tag == "img":
            return "image"
        if "chart" in classes:
            return "chart"
        if "sentinel" in classes:
            return "boundarySentinel"
        return "body"

    @staticmethod
    def _required_features(tag: str, classes: frozenset[str], style: str) -> list[str]:
        features: list[str] = []
        lowered = style.lower()
        for feature in ("filter", "mask", "clip-path", "gradient", "box-shadow", "transform"):
            if feature in lowered:
                features.append(feature)
        if tag in {"svg", "canvas", "table"}:
            features.append(tag)
        if "chart" in classes:
            features.append("chart")
        return sorted(set(features))

    @staticmethod
    def _raster_reason(tag: str, classes: frozenset[str], style: str) -> str:
        if "chart" in classes:
            return "declared_chart_serializer_unavailable"
        if tag == "table":
            return "declared_table_serializer_unavailable"
        if tag in {"svg", "canvas"}:
            return "complex_graphics_require_raster"
        if _COMPLEX_CSS.search(style):
            return "complex_css_requires_raster"
        return "complex_island_declared"


def compile_authored_semantic_source(html: str, *, slide_index: int) -> dict[str, Any]:
    """Compile one strict declared source; never repair a QID or infer readiness."""

    declared_qids = validate_declared_authored_qids(html)
    parser = _DeclaredElementParser()
    parser.feed(html)
    parser.close()
    elements = parser.finish()
    observed_ids = tuple(item["elementId"] for item in elements)
    if observed_ids != declared_qids:
        raise ValueError("authored_semantic_qid_mismatch")
    return {
        "schemaVersion": "authored-semantic-source/v1",
        "origin": "authored-declared-v1",
        "slideIndex": slide_index,
        "htmlDigest": _sha256_bytes(html.encode("utf-8")),
        "elements": elements,
        "elementIdentityRegistry": {
            "schemaVersion": "authored-element-identity-registry/v1",
            "activeQids": sorted(declared_qids),
            "retiredQids": [],
        },
    }


def semantic_unavailable_receipt(
    html: str,
    *,
    slide_index: int,
    failure_code: str,
    bounded_attempt_count: int = 1,
) -> dict[str, Any]:
    return {
        "schemaVersion": "semantic-unavailable-receipt/v1",
        "state": "degradedFidelityOnly",
        "slideIndex": slide_index,
        "htmlDigest": _sha256_bytes(html.encode("utf-8")),
        "failureCode": failure_code,
        "retryPolicy": "authored-semantic-bounded-retry/v1",
        "boundedAttemptCount": bounded_attempt_count,
        "hybridEligible": False,
        "workbenchEditable": False,
    }


def compile_semantic_component_with_bounded_retry(
    html: str,
    *,
    slide_index: int,
    max_attempts: int = 2,
    compiler: Callable[..., dict[str, Any]] = compile_authored_semantic_source,
) -> dict[str, Any]:
    """Compile once-or-twice, then emit an explicit Fidelity-only receipt.

    The retry bound is server-owned.  Callers cannot turn repeated invalid
    semantic output into native eligibility, and the receipt records the exact
    number of compiler attempts that were consumed.
    """

    if max_attempts < 1 or max_attempts > 2:
        raise ValueError("authored_semantic_retry_bound_invalid")
    last_code = "authored_semantic_generation_failed"
    for attempt in range(1, max_attempts + 1):
        try:
            return {"semanticSource": compiler(html, slide_index=slide_index)}
        except (TypeError, ValueError) as error:
            last_code = (
                str(error)
                if str(error).startswith("authored_semantic_")
                else "authored_semantic_validation_failed"
            )
    return {
        "semanticUnavailableReceipt": semantic_unavailable_receipt(
            html,
            slide_index=slide_index,
            failure_code="authored_semantic_retry_exhausted",
            bounded_attempt_count=max_attempts,
        )
        | {"lastFailureCode": last_code}
    }


def compile_semantic_components(htmls: list[str]) -> list[dict[str, Any]]:
    """Return an exact-one semantic-source/unavailable component per slide."""

    components: list[dict[str, Any]] = []
    for slide_index, html in enumerate(htmls):
        components.append(
            compile_semantic_component_with_bounded_retry(
                html,
                slide_index=slide_index,
            )
        )
    return components


__all__ = [
    "compile_authored_semantic_source",
    "compile_semantic_component_with_bounded_retry",
    "compile_semantic_components",
    "semantic_unavailable_receipt",
]
