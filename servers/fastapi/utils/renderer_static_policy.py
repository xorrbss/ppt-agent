"""Policy helpers for deterministic, internally generated static HTML output.

This profile remains separate from public authored admission. It validates
inert SlideSpec metadata and bounded embedded CSS images, then produces a
neutralized copy for the stricter public parser to validate.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
import urllib.parse
import xml.etree.ElementTree as ET

from utils.renderer_worker_policy import RendererPolicyError


MAX_STATIC_ASSET_BYTES = 1536 * 1024
MAX_STATIC_SVG_NODES = 512
_STATIC_DATA_ATTRIBUTES = {
    "data-intentional-overlap-group",
    "data-layout-id",
    "data-render-input-hash",
    "data-semantic-id",
    "data-slidespec",
    "data-slidespec-connector",
}
_DATA_ATTRIBUTE = re.compile(
    r"\s+(?P<name>data-[a-z0-9_-]+)\s*=\s*(?P<quote>[\"'])(?P<value>.*?)(?P=quote)",
    re.IGNORECASE | re.DOTALL,
)
_STYLE_BLOCK = re.compile(
    r"(<style\b[^>]*>)([\s\S]*?)(</style\s*>)", re.IGNORECASE
)
_STYLE_ATTRIBUTE = re.compile(
    r"(\bstyle\s*=\s*)(?P<quote>[\"'])(?P<value>.*?)(?P=quote)",
    re.IGNORECASE | re.DOTALL,
)
_CSS_URL = re.compile(
    r"url\(\s*(?P<quote>[\"']?)(?P<value>.*?)(?P=quote)\s*\)", re.IGNORECASE
)
_STATIC_PROPERTY = re.compile(
    r"(?P<prefix>(?:^|[;{])\s*)(?P<name>pointer-events|mask-image)\s*:\s*(?P<value>[^;}]+)",
    re.IGNORECASE,
)
_STATIC_SVG_ATTRIBUTE = re.compile(
    r"\s+vector-effect\s*=\s*(?P<quote>[\"'])(?P<value>.*?)(?P=quote)",
    re.IGNORECASE | re.DOTALL,
)
_SAFE_POINTER_EVENTS = {
    "auto", "none", "visible", "visiblepainted", "visiblefill", "visiblestroke",
    "painted", "fill", "stroke", "all", "bounding-box", "inherit", "initial", "unset",
}
_EXTERNAL_VALUE = re.compile(
    r"(?:https?|file|ftp|blob|javascript|vbscript)\s*:", re.IGNORECASE
)
_SVG_FORBIDDEN_TAGS = {
    "script", "foreignobject", "iframe", "image", "audio", "video"
}


def _static_data_attribute(match: re.Match[str]) -> str:
    name = match.group("name").lower()
    value = match.group("value")
    if name not in _STATIC_DATA_ATTRIBUTES:
        return match.group(0)
    if (
        len(value.encode("utf-8")) > 512
        or "\x00" in value
        or _EXTERNAL_VALUE.search(value)
    ):
        raise RendererPolicyError(f"static output attribute is invalid: {name}")
    if (
        name == "data-render-input-hash"
        and re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None
    ):
        raise RendererPolicyError("static render input hash is invalid")
    return ""


def _validate_svg(payload: bytes) -> None:
    if len(payload) > MAX_STATIC_ASSET_BYTES:
        raise RendererPolicyError("static SVG asset size limit exceeded")
    lowered = payload.lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise RendererPolicyError("static SVG declarations are not allowed")
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise RendererPolicyError("static SVG data URL is malformed") from exc
    nodes = list(root.iter())
    if len(nodes) > MAX_STATIC_SVG_NODES:
        raise RendererPolicyError("static SVG node limit exceeded")
    for node in nodes:
        tag = node.tag.rsplit("}", 1)[-1].lower()
        if tag in _SVG_FORBIDDEN_TAGS:
            raise RendererPolicyError(f"static SVG element is not allowed: {tag}")
        for raw_name, raw_value in node.attrib.items():
            name = raw_name.rsplit("}", 1)[-1].lower()
            value = str(raw_value).strip()
            if name.startswith("on") or name in {"href", "src"}:
                raise RendererPolicyError("static SVG active attributes are not allowed")
            if _EXTERNAL_VALUE.search(value):
                raise RendererPolicyError("static SVG external references are not allowed")
            for url in re.findall(r"url\(\s*([^)]*?)\s*\)", value, re.IGNORECASE):
                if re.fullmatch(r"#[A-Za-z_][A-Za-z0-9_.:-]*", url.strip()) is None:
                    raise RendererPolicyError("static SVG URL must be a local fragment")


def _decode_static_data_url(value: str) -> bytes:
    header, separator, body = value.partition(",")
    if not separator:
        raise RendererPolicyError("static CSS URL is malformed")
    normalized_header = header.lower()
    try:
        payload = (
            base64.b64decode(body, validate=True)
            if normalized_header.endswith(";base64")
            else urllib.parse.unquote_to_bytes(body)
        )
    except (binascii.Error, ValueError) as exc:
        raise RendererPolicyError("static CSS data URL encoding is invalid") from exc
    if len(payload) > MAX_STATIC_ASSET_BYTES:
        raise RendererPolicyError("static CSS asset size limit exceeded")
    if normalized_header.startswith("data:image/svg+xml"):
        _validate_svg(payload)
    elif normalized_header.startswith("data:image/png"):
        if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
            raise RendererPolicyError("static PNG media type does not match its header")
    elif normalized_header.startswith("data:image/jpeg"):
        if not payload.startswith(b"\xff\xd8\xff"):
            raise RendererPolicyError("static JPEG media type does not match its header")
    elif normalized_header.startswith("data:image/webp"):
        if len(payload) < 12 or payload[:4] != b"RIFF" or payload[8:12] != b"WEBP":
            raise RendererPolicyError("static WebP media type does not match its header")
    elif normalized_header.startswith("data:image/gif"):
        if not payload.startswith((b"GIF87a", b"GIF89a")):
            raise RendererPolicyError("static GIF media type does not match its header")
    else:
        raise RendererPolicyError(
            "static CSS URL must be a supported embedded image"
        )
    return payload


def _neutralize_css(css: str, asset_digests: list[str]) -> str:
    def replace_url(match: re.Match[str]) -> str:
        value = match.group("value").strip()
        if re.fullmatch(r"#[A-Za-z_][A-Za-z0-9_.:-]*", value):
            return "none"
        if not value.lower().startswith("data:image/"):
            raise RendererPolicyError("static CSS URLs must be embedded images")
        payload = _decode_static_data_url(value)
        asset_digests.append("sha256:" + hashlib.sha256(payload).hexdigest())
        return "none"

    neutralized = _CSS_URL.sub(replace_url, css)

    def replace_property(match: re.Match[str]) -> str:
        name = match.group("name").lower()
        value = match.group("value").strip().lower()
        if name == "pointer-events":
            if value not in _SAFE_POINTER_EVENTS:
                raise RendererPolicyError("static pointer-events value is invalid")
            return f"{match.group('prefix')}display:initial"
        if "url(" in value or _EXTERNAL_VALUE.search(value):
            raise RendererPolicyError("static mask-image value is active")
        if not (value == "none" or value.startswith("linear-gradient(")):
            raise RendererPolicyError("static mask-image value is not allowlisted")
        return f"{match.group('prefix')}mask:none"

    return _STATIC_PROPERTY.sub(replace_property, neutralized)


def neutralize_static_output_html(
    source: str,
) -> tuple[str, str, tuple[str, ...]]:
    """Return policy-check and render copies after validating static extensions."""

    asset_digests: list[str] = []
    neutralized = _DATA_ATTRIBUTE.sub(_static_data_attribute, source)

    def replace_svg_attribute(match: re.Match[str]) -> str:
        if match.group("value").strip().lower() != "non-scaling-stroke":
            raise RendererPolicyError("static SVG vector-effect is not allowlisted")
        return ""

    neutralized = _STATIC_SVG_ATTRIBUTE.sub(replace_svg_attribute, neutralized)
    neutralized = _STYLE_BLOCK.sub(
        lambda match: (
            match.group(1)
            + _neutralize_css(match.group(2), asset_digests)
            + match.group(3)
        ),
        neutralized,
    )

    def replace_style(match: re.Match[str]) -> str:
        quote = match.group("quote")
        return (
            match.group(1)
            + quote
            + _neutralize_css(match.group("value"), asset_digests)
            + quote
        )

    neutralized = _STYLE_ATTRIBUTE.sub(replace_style, neutralized)

    def normalize_css(css: str) -> str:
        def replace_url(match: re.Match[str]) -> str:
            value = match.group("value").strip()
            if re.fullmatch(r"#[A-Za-z_][A-Za-z0-9_.:-]*", value):
                return match.group(0)
            payload = _decode_static_data_url(value)
            header = value.partition(",")[0].split(";", 1)[0].lower()
            encoded = base64.b64encode(payload).decode("ascii")
            return f'url("{header};base64,{encoded}")'

        return _CSS_URL.sub(replace_url, css)

    normalized = _STYLE_BLOCK.sub(
        lambda match: (
            match.group(1) + normalize_css(match.group(2)) + match.group(3)
        ),
        source,
    )

    def normalize_style(match: re.Match[str]) -> str:
        quote = match.group("quote")
        return match.group(1) + quote + normalize_css(match.group("value")) + quote

    normalized = _STYLE_ATTRIBUTE.sub(normalize_style, normalized)
    return neutralized, normalized, tuple(asset_digests)
