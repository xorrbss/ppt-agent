"""Bundled font materialization for ordinary SVG slide previews.

The renderer embeds a per-page subset of each bundled face
(``services.svg_render_font_subset``). The wrapper's profile marker still
names the full source faces' digests, so wrapper and cache identities are
unchanged.
"""

from __future__ import annotations

import base64
from dataclasses import replace
from functools import lru_cache
import re
import xml.etree.ElementTree as ET

from services.svg_render_font_subset import SubsetFace, render_charset, subset_face, subset_provenance
from services.svg_trusted_fonts import (
    DEFAULT_FONT_FAMILY as FONT_FAMILY,
    bundled_svg_font_family,
    read_trusted_pretendard_faces,
    trusted_font_weights_used,
)
from utils.svg_template_fonts import font_material


FONT_PROFILE = "svg-default-bundled-fonts/v1"
_CSP_FONT_NONE = "font-src &#x27;none&#x27;"
#: The variable face keeps its whole weight axis (see svg_render_font_subset).
_NOTO_AXES = "wght:100-900"


def normalize_model_font_families(svg: str) -> tuple[str, list[dict]]:
    """Use the bundled SVG font registry for model-authored text families.

    Unparseable output is returned unchanged: the author step has already
    recorded it as a validation error, and the render step retries the page.
    Raising here instead failed the whole deck as an internal defect.
    """
    try:
        root = ET.fromstring(svg)
    except ET.ParseError:
        return svg, []
    advisories = []
    for node in root.iter():
        requested = node.get("font-family")
        if requested is None:
            continue
        resolved = bundled_svg_font_family(requested)
        if requested.strip().strip("'\"") == resolved:
            continue
        node.set("font-family", resolved)
        advisories.append({"kind": "font_family_substituted", "requested_family": requested,
                           "resolved_family": resolved})
    return (ET.tostring(root, encoding="unicode"), advisories) if advisories else (svg, [])


@lru_cache(maxsize=1)
def _pretendard_materials():
    return read_trusted_pretendard_faces()


def uses_default_font(root: ET.Element) -> bool:
    return any(
        node.tag.rsplit("}", 1)[-1] == "text"
        and node.get("font-family", "").strip("'\"").casefold() == FONT_FAMILY.casefold()
        for node in root.iter()
    )


@lru_cache(maxsize=1)
def font_marker() -> str:
    noto_digest, _ = font_material(FONT_FAMILY)
    pretendard = _pretendard_materials()
    return FONT_PROFILE + "|" + noto_digest + "|" + "|".join(
        f"{weight}={digest}" for weight, digest, _ in pretendard
    )


def _validated_default_font_root(source: str, admitted) -> ET.Element:
    """Check a default-profile request and return the SVG it renders."""
    marker = font_marker()
    declaration = f'<html title="{marker}">'
    if source.count(declaration) != 1 or "data:font/" in source.casefold() or "@font-face" in source.casefold():
        raise ValueError("svg_default_font_profile_invalid")
    match = re.search(
        r"(<(?:[A-Za-z_][\w.-]*:)?svg\b.*?</(?:[A-Za-z_][\w.-]*:)?svg>)",
        source,
        re.IGNORECASE | re.DOTALL,
    )
    if match is None:
        raise ValueError("svg_default_font_wrapper_invalid")
    root = ET.fromstring(match.group(1))
    if not uses_default_font(root):
        raise ValueError("svg_default_font_profile_unused")
    if admitted.html.count(_CSP_FONT_NONE) != 1 or "</head>" not in admitted.html:
        raise ValueError("svg_default_font_csp_invalid")
    return root


def _default_font_faces(root: ET.Element) -> list[tuple[SubsetFace, bytes]]:
    """The bundled faces a page embeds: variable Noto plus the used Pretendard weights."""
    noto_digest, noto_payload = font_material(FONT_FAMILY)
    faces = [(SubsetFace(FONT_FAMILY, "100 900", _NOTO_AXES, noto_digest), noto_payload)]
    weights, _ = trusted_font_weights_used(root)
    faces.extend(
        (SubsetFace("Pretendard", str(weight), "static", digest), payload)
        for weight, digest, payload in _pretendard_materials()
        if weight in weights
    )
    return faces


def default_font_subset_provenance(svg: str) -> dict:
    """Subset provenance of a default-profile render of ``svg``.

    It is deterministic from the SVG and the pinned sources, so the parent
    records what the isolated worker embedded without building the subset.
    """
    root = ET.fromstring(svg)
    return subset_provenance([face for face, _payload in _default_font_faces(root)], render_charset(root))


def validate_default_fonts(source: str, admitted):
    """Validate a default-profile request without materializing its faces.

    A parent that only binds the worker's response digest needs the same
    fail-closed request checks, not the face payloads.
    """
    _validated_default_font_root(source, admitted)
    return admitted


def materialize_default_fonts(source: str, admitted):
    root = _validated_default_font_root(source, admitted)
    charset = render_charset(root)
    materials = [(face, subset_face(face, payload, charset)) for face, payload in _default_font_faces(root)]
    faces = [
        f"@font-face{{font-family:'{face.family}';font-style:normal;font-weight:{face.weight};"
        f"src:url(data:font/ttf;base64,{base64.b64encode(payload).decode('ascii')}) format('truetype');}}"
        for face, payload in materials
    ]
    injected = admitted.html.replace(_CSP_FONT_NONE, "font-src data:", 1).replace(
        "</head>",
        f'<style data-presenton-trusted-font-profile="{FONT_PROFILE}">'
        + "".join(faces) + "</style></head>",
        1,
    )
    return replace(
        admitted,
        html=injected,
        asset_digests=tuple(dict.fromkeys(
            (*admitted.asset_digests, *(face.source_digest for face, _payload in materials))
        )),
        node_count=admitted.node_count + 1,
    )
