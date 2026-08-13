"""Admission policy and process boundary for authored HTML rendering.

The renderer accepts a deliberately small, self-contained HTML/CSS language.  It
does not sanitize and continue: a rejected authored document must be regenerated
or served from an already-published artifact, never silently downgraded to the
legacy file renderer.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import html as html_lib
import io
import json
import re
import secrets
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Mapping

from PIL import Image, UnidentifiedImageError

from utils.renderer_internal_capability import consume_internal_render_capability
from utils.renderer_worker_policy import (
    RendererDisabledError as RendererDisabledError,
    RendererPolicyError as RendererPolicyError,
    assert_no_provider_secrets as assert_no_provider_secrets,
    build_worker_environment as build_worker_environment,
    ensure_renderer_enabled as ensure_renderer_enabled,
    env_enabled as env_enabled,
    renderer_mode as renderer_mode,
    renderer_requires_external_worker as renderer_requires_external_worker,
    validate_command as validate_command,
    worker_command as worker_command,
)
from utils.renderer_static_policy import neutralize_static_output_html


MAX_HTML_BYTES = 2 * 1024 * 1024
MAX_REQUEST_BYTES = 12 * 1024 * 1024 + 4096
MAX_RESPONSE_BYTES = 32 * 1024 * 1024
MAX_DOM_NODES = 4096
MAX_DOM_DEPTH = 64
MAX_ATTRIBUTES_PER_NODE = 32
MAX_TEXT_BYTES = 1024 * 1024
MAX_ASSET_BYTES = 1536 * 1024
MAX_TOTAL_ASSET_BYTES = 2 * 1024 * 1024
MAX_IMAGE_DIMENSION = 8192
MAX_IMAGE_PIXELS = 16_777_216
MAX_FONT_DECLARATIONS = 256
MAX_FONT_FAMILIES = 16

_ALLOWED_TAGS = {
    "html", "head", "title", "meta", "body", "main", "section", "article",
    "header", "footer", "nav", "aside", "div", "span", "p", "h1", "h2",
    "h3", "h4", "h5", "h6", "ul", "ol", "li", "dl", "dt", "dd",
    "table", "caption", "colgroup", "col", "thead", "tbody", "tfoot", "tr",
    "th", "td", "strong", "b", "em", "i", "small", "sub", "sup", "br",
    "hr", "img", "figure", "figcaption", "blockquote", "style", "svg", "g", "path",
    "rect", "circle", "ellipse", "line", "polyline", "polygon", "text", "tspan",
    "defs", "lineargradient", "radialgradient", "stop", "clippath", "mask",
}
_VOID_TAGS = {"meta", "br", "hr", "img", "col"}
_DROP_TAGS = {
    "script", "iframe", "frame", "frameset", "object", "embed", "applet",
    "link", "base", "form", "input", "button", "textarea", "select", "option",
    "video", "audio", "source", "track", "canvas", "template", "math",
    "foreignobject", "use",
}
_GLOBAL_ATTRIBUTES = {
    "id", "class", "role", "aria-label", "aria-hidden", "lang", "dir", "style",
    "title", "data-qid", "data-qid-ignore", "data-allow-overlap",
}
_TAG_ATTRIBUTES: Mapping[str, set[str]] = {
    "meta": {"charset"},
    "img": {"src", "alt", "width", "height", "loading", "decoding"},
    "th": {"colspan", "rowspan", "scope"},
    "td": {"colspan", "rowspan"},
    "col": {"span"},
    "svg": {"viewbox", "width", "height", "preserveaspectratio"},
    "g": {"transform", "fill", "stroke", "stroke-width", "opacity", "clip-path"},
    "path": {"d", "transform", "fill", "stroke", "stroke-width", "opacity"},
    "rect": {"x", "y", "width", "height", "rx", "ry", "fill", "stroke", "stroke-width", "opacity"},
    "circle": {"cx", "cy", "r", "fill", "stroke", "stroke-width", "opacity"},
    "ellipse": {"cx", "cy", "rx", "ry", "fill", "stroke", "stroke-width", "opacity"},
    "line": {"x1", "y1", "x2", "y2", "fill", "stroke", "stroke-width", "opacity"},
    "polyline": {"points", "fill", "stroke", "stroke-width", "opacity"},
    "polygon": {"points", "fill", "stroke", "stroke-width", "opacity"},
    "text": {
        "x", "y", "dx", "dy", "fill", "stroke", "font-size", "font-family",
        "font-weight", "text-anchor", "transform",
    },
    "tspan": {"x", "y", "dx", "dy", "fill", "font-size", "font-family", "font-weight"},
    "lineargradient": {"id", "x1", "y1", "x2", "y2", "gradientunits", "gradienttransform"},
    "radialgradient": {"id", "cx", "cy", "r", "fx", "fy", "gradientunits", "gradienttransform"},
    "stop": {"offset", "stop-color", "stop-opacity"},
    "clippath": {"id", "transform"},
    "mask": {"id", "x", "y", "width", "height", "maskunits"},
}
_ALLOWED_CSS_PROPERTIES = {
    "align-content", "align-items", "align-self", "background", "background-color",
    "background-image", "background-position", "background-repeat", "background-size",
    "border", "border-bottom", "border-bottom-color", "border-bottom-left-radius",
    "border-bottom-right-radius", "border-bottom-style", "border-bottom-width",
    "border-collapse", "border-color", "border-left", "border-left-color",
    "border-left-style", "border-left-width", "border-radius", "border-right",
    "border-right-color", "border-right-style", "border-right-width", "border-spacing",
    "border-style", "border-top", "border-top-color", "border-top-left-radius",
    "border-top-right-radius", "border-top-style", "border-top-width", "border-width",
    "bottom", "box-shadow", "box-sizing", "clip-path", "color", "column-gap",
    "display", "fill", "filter", "flex", "flex-basis", "flex-direction", "flex-flow",
    "flex-grow", "flex-shrink", "flex-wrap", "font", "font-family", "font-size",
    "font-stretch", "font-style", "font-variant", "font-weight", "gap", "grid",
    "grid-area", "grid-auto-columns", "grid-auto-flow", "grid-auto-rows", "grid-column",
    "grid-column-end", "grid-column-start", "grid-row", "grid-row-end", "grid-row-start",
    "grid-template", "grid-template-areas", "grid-template-columns", "grid-template-rows",
    "height", "inset", "justify-content", "justify-items", "justify-self", "left",
    "letter-spacing", "line-height", "list-style", "list-style-position", "margin",
    "margin-bottom", "margin-left", "margin-right", "margin-top", "mask", "max-height",
    "max-width", "min-height", "min-width", "object-fit", "object-position", "opacity",
    "outline", "outline-color", "outline-offset", "outline-style", "outline-width",
    "overflow", "overflow-wrap", "overflow-x", "overflow-y", "padding", "padding-bottom",
    "padding-left", "padding-right", "padding-top", "place-content", "place-items",
    "place-self", "position", "right", "row-gap", "stroke", "stroke-dasharray",
    "stroke-linecap", "stroke-linejoin", "stroke-width", "table-layout", "text-align",
    "text-decoration", "text-decoration-color", "text-decoration-line",
    "text-decoration-style", "text-indent", "text-overflow", "text-shadow",
    "text-transform", "top", "transform", "transform-origin", "vertical-align",
    "white-space", "width", "word-break", "word-spacing", "z-index",
}
_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_CSS_BLOCK = re.compile(r"([^{}]+)\{([^{}]*)\}", re.DOTALL)
_UNSAFE_CSS = re.compile(
    r"@(?:import|font-face|namespace|supports|document|page|keyframes)|"
    r"(?:url|image-set|cross-fade|element|expression)\s*\(|"
    r"-moz-binding|behavior\s*:|javascript\s*:|file\s*:|https?\s*:",
    re.IGNORECASE,
)
_FONT_FAMILY = re.compile(r"(?:^|[;{])\s*font-family\s*:\s*([^;}]+)", re.IGNORECASE)
_DATA_URL = re.compile(r"^data:([^;,]+);base64,([A-Za-z0-9+/=\s]+)$", re.IGNORECASE)
_ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
_SVG_PAINT_ATTRIBUTES = {"fill", "stroke", "clip-path", "mask"}
_LOCAL_SVG_URL = re.compile(r"url\(\s*#[A-Za-z_][A-Za-z0-9_.:-]*\s*\)$", re.IGNORECASE)
_CSP = (
    "default-src 'none'; base-uri 'none'; connect-src 'none'; frame-src 'none'; "
    "child-src 'none'; object-src 'none'; media-src 'none'; worker-src 'none'; "
    "manifest-src 'none'; img-src data:; font-src 'none'; style-src 'unsafe-inline'; "
    "script-src 'nonce-{nonce}'; form-action 'none'; navigate-to 'none'"
)


@dataclass(frozen=True)
class AdmittedHtml:
    html: str
    nonce: str
    source_digest: str
    asset_digests: tuple[str, ...]
    node_count: int


def _validate_css(css: str) -> set[str]:
    normalized = html_lib.unescape(_CSS_COMMENT.sub("", css))
    if len(normalized.encode("utf-8")) > MAX_HTML_BYTES:
        raise RendererPolicyError("CSS exceeds authored source limit")
    if _UNSAFE_CSS.search(normalized):
        raise RendererPolicyError("CSS contains an external, active, or font resource")
    declarations: list[str] = []
    blocks = list(_CSS_BLOCK.finditer(normalized))
    if "{" in normalized or "}" in normalized:
        residue = _CSS_BLOCK.sub("", normalized)
        if "{" in residue or "}" in residue:
            raise RendererPolicyError("CSS contains unbalanced or nested rules")
        declarations.extend(match.group(2) for match in blocks)
    else:
        declarations.append(normalized)
    families: set[str] = set()
    for declaration_block in declarations:
        for declaration in declaration_block.split(";"):
            if not declaration.strip():
                continue
            if ":" not in declaration:
                raise RendererPolicyError("CSS declaration is malformed")
            property_name, value = declaration.split(":", 1)
            property_name = property_name.strip().lower()
            if property_name not in _ALLOWED_CSS_PROPERTIES:
                raise RendererPolicyError(f"CSS property is not allowed: {property_name}")
            if len(value) > 8192 or "\\" in property_name or "\\" in value:
                raise RendererPolicyError("CSS value exceeds policy")
    for match in _FONT_FAMILY.finditer(";" + normalized):
        family_value = match.group(1).strip()
        if len(family_value) > 512:
            raise RendererPolicyError("font-family declaration exceeds policy")
        families.update(
            item.strip().strip("\"'").lower()
            for item in family_value.split(",")
            if item.strip()
        )
    return families


class _PolicyParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.nodes = 0
        self.text_bytes = 0
        self.asset_bytes = 0
        self.asset_digests: list[str] = []
        self.font_families: set[str] = set()
        self.font_declarations = 0
        self._style_depth = 0
        self._stack: list[str] = []

    def _start(self, tag: str, attrs: list[tuple[str, str | None]], *, void: bool) -> None:
        tag = tag.lower()
        if tag in _DROP_TAGS or tag not in _ALLOWED_TAGS:
            raise RendererPolicyError(f"HTML element is not allowed: {tag}")
        self.nodes += 1
        if self.nodes > MAX_DOM_NODES:
            raise RendererPolicyError("DOM node limit exceeded")
        if len(attrs) > MAX_ATTRIBUTES_PER_NODE:
            raise RendererPolicyError("attribute-per-node limit exceeded")
        if not void:
            self.depth += 1
            self._stack.append(tag)
            if self.depth > MAX_DOM_DEPTH:
                raise RendererPolicyError("DOM depth limit exceeded")
        seen: set[str] = set()
        values: dict[str, str] = {}
        allowed = _GLOBAL_ATTRIBUTES | _TAG_ATTRIBUTES.get(tag, set())
        for raw_name, raw_value in attrs:
            name = raw_name.lower()
            if name in seen:
                raise RendererPolicyError(f"duplicate HTML attribute: {name}")
            seen.add(name)
            if name.startswith("on") or name not in allowed:
                raise RendererPolicyError(f"HTML attribute is not allowed: {name}")
            value = html_lib.unescape(raw_value or "")
            if len(value.encode("utf-8")) > MAX_HTML_BYTES:
                raise RendererPolicyError("HTML attribute exceeds source limit")
            if "\x00" in value:
                raise RendererPolicyError("HTML attribute contains NUL")
            values[name] = value
            if tag in {
                "svg", "g", "path", "rect", "circle", "ellipse", "line",
                "polyline", "polygon", "text", "tspan",
            } and name in _SVG_PAINT_ATTRIBUTES:
                normalized_paint = value.strip()
                if (
                    "\\" in normalized_paint
                    or ":" in normalized_paint
                    or (
                        "url(" in normalized_paint.lower()
                        and _LOCAL_SVG_URL.fullmatch(normalized_paint) is None
                    )
                ):
                    raise RendererPolicyError(
                        "SVG paint attributes may reference only local fragment ids"
                    )
            if name == "style":
                families = _validate_css(value)
                self.font_declarations += value.lower().count("font-family")
                self.font_families.update(families)
            if name in {"href", "action", "formaction", "poster", "data", "xlink:href", "srcdoc"}:
                raise RendererPolicyError(f"URL-bearing attribute is not allowed: {name}")
            if re.search(r"(?:^|[/\\])\.\.(?:[/\\]|%2f|%5c)", value, re.IGNORECASE):
                raise RendererPolicyError("path traversal marker is not allowed")
        if tag == "img" and "src" in values:
            self._validate_image(values["src"], values)
        if tag == "style":
            self._style_depth += 1

    def _validate_image(self, source: str, attrs: Mapping[str, str]) -> None:
        match = _DATA_URL.fullmatch(source.strip())
        if match is None or match.group(1).lower() not in _ALLOWED_IMAGE_TYPES:
            raise RendererPolicyError("images must be base64 raster data URLs")
        try:
            payload = base64.b64decode(match.group(2), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise RendererPolicyError("image data URL is not valid base64") from exc
        if len(payload) > MAX_ASSET_BYTES:
            raise RendererPolicyError("image asset size limit exceeded")
        self.asset_bytes += len(payload)
        if self.asset_bytes > MAX_TOTAL_ASSET_BYTES:
            raise RendererPolicyError("total image asset size limit exceeded")
        try:
            with Image.open(io.BytesIO(payload)) as image:
                width, height = image.size
                detected = str(image.format or "").lower()
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise RendererPolicyError("image data URL is not a supported raster") from exc
        expected_formats = {
            "image/png": "png",
            "image/jpeg": "jpeg",
            "image/webp": "webp",
            "image/gif": "gif",
        }
        if detected != expected_formats[match.group(1).lower()]:
            raise RendererPolicyError("image media type does not match its header")
        if (
            width <= 0
            or height <= 0
            or width > MAX_IMAGE_DIMENSION
            or height > MAX_IMAGE_DIMENSION
            or width * height > MAX_IMAGE_PIXELS
        ):
            raise RendererPolicyError("decoded image pixel limit exceeded")
        for name in ("width", "height"):
            if name in attrs:
                try:
                    declared = int(attrs[name])
                except ValueError as exc:
                    raise RendererPolicyError(f"image {name} must be an integer") from exc
                if declared <= 0 or declared > MAX_IMAGE_DIMENSION:
                    raise RendererPolicyError(f"image {name} limit exceeded")
        self.asset_digests.append("sha256:" + hashlib.sha256(payload).hexdigest())

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(tag, attrs, void=tag.lower() in _VOID_TAGS)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(tag, attrs, void=True)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _VOID_TAGS or not self._stack or self._stack[-1] != tag:
            raise RendererPolicyError(f"HTML closing tag is unbalanced: {tag}")
        self._stack.pop()
        if tag == "style" and self._style_depth:
            self._style_depth -= 1
        self.depth -= 1

    def handle_data(self, data: str) -> None:
        encoded = data.encode("utf-8")
        self.text_bytes += len(encoded)
        if self.text_bytes > MAX_TEXT_BYTES:
            raise RendererPolicyError("HTML text limit exceeded")
        if self._style_depth:
            families = _validate_css(data)
            self.font_declarations += data.lower().count("font-family")
            self.font_families.update(families)

    def handle_entityref(self, name: str) -> None:
        return

    def handle_charref(self, name: str) -> None:
        return

    def unknown_decl(self, data: str) -> None:
        raise RendererPolicyError("unknown HTML declaration is not allowed")


def _inject_csp(source: str, nonce: str) -> str:
    csp = html_lib.escape(_CSP.format(nonce=nonce), quote=True)
    meta = f'<meta http-equiv="Content-Security-Policy" content="{csp}">'
    head = re.search(r"<head(?:\s[^>]*)?>", source, flags=re.IGNORECASE)
    if head:
        return source[: head.end()] + meta + source[head.end() :]
    html_tag = re.search(r"<html(?:\s[^>]*)?>", source, flags=re.IGNORECASE)
    if html_tag:
        return source[: html_tag.end()] + "<head>" + meta + "</head>" + source[html_tag.end() :]
    return "<!doctype html><html><head>" + meta + "</head><body>" + source + "</body></html>"


def admit_authored_html(source: str) -> AdmittedHtml:
    if not isinstance(source, str):
        raise RendererPolicyError("authored HTML must be a string")
    raw = source.encode("utf-8")
    if not raw or len(raw) > MAX_HTML_BYTES:
        raise RendererPolicyError("authored HTML source size limit exceeded")
    if "\x00" in source:
        raise RendererPolicyError("authored HTML contains NUL")
    if "__METADATA_ENDPOINT_DO_NOT_RESOLVE__" in source:
        raise RendererPolicyError("reserved metadata endpoint marker is not renderable")
    parser = _PolicyParser()
    try:
        parser.feed(source)
        parser.close()
    except RendererPolicyError:
        raise
    except Exception as exc:
        raise RendererPolicyError("authored HTML could not be parsed safely") from exc
    if parser._stack or parser.depth:
        raise RendererPolicyError("HTML contains unclosed elements")
    if parser.font_declarations > MAX_FONT_DECLARATIONS:
        raise RendererPolicyError("font declaration limit exceeded")
    if len(parser.font_families) > MAX_FONT_FAMILIES:
        raise RendererPolicyError("font family limit exceeded")
    nonce = secrets.token_urlsafe(24)
    return AdmittedHtml(
        html=_inject_csp(source, nonce),
        nonce=nonce,
        source_digest="sha256:" + hashlib.sha256(raw).hexdigest(),
        asset_digests=tuple(parser.asset_digests),
        node_count=parser.nodes,
    )


def admit_static_output_html(source: str) -> AdmittedHtml:
    """Admit inert renderer output without widening public authored admission."""

    neutralized, normalized, extension_assets = neutralize_static_output_html(source)
    validated = admit_authored_html(neutralized)
    raw = source.encode("utf-8")
    return AdmittedHtml(
        html=_inject_csp(normalized, validated.nonce),
        nonce=validated.nonce,
        source_digest="sha256:" + hashlib.sha256(raw).hexdigest(),
        asset_digests=tuple(
            dict.fromkeys((*validated.asset_digests, *extension_assets))
        ),
        node_count=validated.node_count,
    )


def admit_internal_instrumented_html(source: str, capability: str) -> AdmittedHtml:
    """Consume a source-bound capability and admit the exact internal probe once."""

    consume_internal_render_capability(source, capability)
    scripts = list(
        re.finditer(
            r"<script\b(?=[^>]*\bdata-codex-dom-probe\b)[^>]*>[\s\S]*?</script\s*>",
            source,
            flags=re.IGNORECASE,
        )
    )
    if len(scripts) != 1:
        raise RendererPolicyError(
            "internal renderer source must contain one probe script"
        )
    stripped = source[: scripts[0].start()] + source[scripts[0].end() :]
    validated = admit_static_output_html(stripped)
    nonce = validated.nonce
    script = scripts[0].group(0)
    opening_end = script.find(">")
    opening = re.sub(
        r"\s+nonce\s*=\s*([\"']).*?\1", "", script[:opening_end]
    )
    authorized_script = opening + f' nonce="{nonce}">' + script[opening_end + 1 :]
    authorized = (
        source[: scripts[0].start()]
        + authorized_script
        + source[scripts[0].end() :]
    )
    raw = source.encode("utf-8")
    return AdmittedHtml(
        html=_inject_csp(authorized, nonce),
        nonce=nonce,
        source_digest="sha256:" + hashlib.sha256(raw).hexdigest(),
        asset_digests=validated.asset_digests,
        node_count=validated.node_count + 1,
    )


def encode_request(html: str, timeout: int) -> bytes:
    wire = json.dumps(
        {"schema_version": "authored-render-request/v1", "html": html, "timeout": timeout},
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"
    if len(wire) > MAX_REQUEST_BYTES:
        raise RendererPolicyError("renderer request wire limit exceeded")
    return wire
