"""Bundled font materialization for ordinary SVG slide previews."""

from __future__ import annotations

import base64
from dataclasses import replace
from functools import lru_cache
import re
import xml.etree.ElementTree as ET

from services.svg_trusted_fonts import (
    DEFAULT_FONT_FAMILY as FONT_FAMILY,
    bundled_svg_font_family,
    read_trusted_pretendard_faces,
    trusted_font_weights_used,
)
from utils.svg_template_fonts import font_material


FONT_PROFILE = "svg-default-bundled-fonts/v1"


def normalize_model_font_families(svg: str) -> tuple[str, list[dict]]:
    """Use the bundled SVG font registry for model-authored text families."""
    root = ET.fromstring(svg)
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


def materialize_default_fonts(source: str, admitted):
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
    csp_font_none = "font-src &#x27;none&#x27;"
    if admitted.html.count(csp_font_none) != 1 or "</head>" not in admitted.html:
        raise ValueError("svg_default_font_csp_invalid")

    noto_digest, noto_payload = font_material(FONT_FAMILY)
    materials = [(FONT_FAMILY, "100 900", noto_digest, noto_payload)]
    weights, _ = trusted_font_weights_used(root)
    materials.extend(
        ("Pretendard", str(weight), digest, payload)
        for weight, digest, payload in _pretendard_materials()
        if weight in weights
    )
    faces = [
        f"@font-face{{font-family:'{family}';font-style:normal;font-weight:{weight};"
        f"src:url(data:font/ttf;base64,{base64.b64encode(payload).decode('ascii')}) format('truetype');}}"
        for family, weight, _digest, payload in materials
    ]
    injected = admitted.html.replace(csp_font_none, "font-src data:", 1).replace(
        "</head>",
        f'<style data-presenton-trusted-font-profile="{FONT_PROFILE}">'
        + "".join(faces) + "</style></head>",
        1,
    )
    return replace(
        admitted,
        html=injected,
        asset_digests=tuple(dict.fromkeys(
            (*admitted.asset_digests, *(digest for _family, _weight, digest, _payload in materials))
        )),
        node_count=admitted.node_count + 1,
    )
