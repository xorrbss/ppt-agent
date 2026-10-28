"""Render a validated SVG through the existing isolated Chromium boundary."""
from __future__ import annotations
# ruff: noqa: ASYNC109

from dataclasses import dataclass
import base64
import json
from dataclasses import replace
from functools import lru_cache
import re
import xml.etree.ElementTree as ET

from services.svg_contract import PAGE_NUMBER_ID, SVG_CONTRACT_VERSION, source_digest, validate_svg
from services.svg_trusted_fonts import trusted_font_weights_used as _trusted_font_weights_used
from services.authored_canvas import DEFAULT_CANVAS_HEIGHT_PX, DEFAULT_CANVAS_WIDTH_PX

SVG_RENDERER_VERSION = "svg-chromium/v1"

# Keep this persisted profile identity for older SVG previews.
TRUSTED_FONT_PROFILE = "composer-bundled-pretendard/v1"


@lru_cache(maxsize=1)
def _trusted_font_materials() -> tuple[tuple[int, str, bytes], ...]:
    """Resolve only the fixed, identity-checked bundled Pretendard bytes."""
    from services.svg_trusted_fonts import read_trusted_pretendard_faces

    return read_trusted_pretendard_faces()


def _trusted_font_marker() -> str:
    materials = _trusted_font_materials()
    return TRUSTED_FONT_PROFILE + "|" + "|".join(
        f"{weight}={digest}" for weight, digest, _payload in materials
    )


def materialize_trusted_svg_fonts(source: str, admitted):
    """Inject fixed bundled faces after normal HTML admission inside the worker."""
    from services.svg_default_fonts import FONT_PROFILE as DEFAULT_FONT_PROFILE, materialize_default_fonts
    if f'<html title="{DEFAULT_FONT_PROFILE}|' in source:
        return materialize_default_fonts(source, admitted)
    from utils.svg_template_fonts import FONT_PROFILE
    if f'<html title="{FONT_PROFILE}|' in source:
        return _materialize_template_font(source, admitted)
    marker = _trusted_font_marker()
    declaration = f'<html title="{marker}">'
    if declaration not in source:
        return admitted
    if source.count(declaration) != 1 or "data:font/" in source.casefold() or "@font-face" in source.casefold():
        raise ValueError("svg_trusted_font_profile_invalid")
    match = re.search(r"(<(?:[A-Za-z_][\w.-]*:)?svg\b.*?</(?:[A-Za-z_][\w.-]*:)?svg>)", source,
                      re.IGNORECASE | re.DOTALL)
    if match is None:
        raise ValueError("svg_trusted_font_wrapper_invalid")
    root = ET.fromstring(match.group(1))
    if not _trusted_font_weights_used(root)[0]:
        raise ValueError("svg_trusted_font_profile_unused")
    csp_font_none = "font-src &#x27;none&#x27;"
    if admitted.html.count(csp_font_none) != 1 or "</head>" not in admitted.html:
        raise ValueError("svg_trusted_font_csp_invalid")
    faces = []
    digests = []
    for weight, digest, payload in _trusted_font_materials():
        encoded = base64.b64encode(payload).decode("ascii")
        faces.append(
            f"@font-face{{font-family:'Pretendard';font-style:normal;font-weight:{weight};"
            f"src:url(data:font/ttf;base64,{encoded}) format('truetype');}}"
        )
        digests.append(digest)
    injected = admitted.html.replace(csp_font_none, "font-src data:", 1).replace(
        "</head>",
        f'<style data-presenton-trusted-font-profile="{TRUSTED_FONT_PROFILE}">'
        + "".join(faces) + "</style></head>",
        1,
    )
    return replace(admitted, html=injected,
                   asset_digests=tuple(dict.fromkeys((*admitted.asset_digests, *digests))),
                   node_count=admitted.node_count + 1)


def _template_font_used(root: ET.Element) -> bool:
    from utils.svg_template_fonts import FONT_FAMILY, font_material, trusted_font_fallback_plan

    nodes = [node for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "text"]
    if not any(node.get("font-family", "").strip("'\"") == FONT_FAMILY for node in nodes):
        return False
    for node in nodes:
        # Source templates pin every text node. Mixed/inherited font stacks
        # cannot use the template profile as evidence for a silent fallback.
        family = node.get("font-family", "").strip("'\"")
        font_material(family, node.get("font-weight", "400"))
        if any(child.get("font-family", family).strip("'\"") != family for child in node.iter()):
            raise ValueError("svg_template_font_mixed_families")
        trusted_font_fallback_plan("".join(node.itertext()), family, node.get("font-weight", "400"))
    return True


def _svg_fragment(html: str):
    match = re.search(r"(<(?:[A-Za-z_][\w.-]*:)?svg\b.*?</(?:[A-Za-z_][\w.-]*:)?svg>)", html, re.I | re.S)
    if match is None:
        raise ValueError("svg_trusted_font_wrapper_invalid")
    return match


def _fallback_binding(proof):
    if proof is None:
        return None
    from utils.hashing import canonical_json_sha256
    return canonical_json_sha256({key: proof[key] for key in (
        "schema", "offset_unit", "primary_family", "primary_font_digest", "fallback_family",
        "fallback_font_digest", "fallback_weight", "runs")})


def _fallback_comment(svg: str) -> str:
    from utils.svg_template_fonts import materialize_template_fallbacks
    _derived, proof = materialize_template_fallbacks(svg)
    marker = _fallback_binding(proof)
    return f"<!--presenton-template-fallback:{marker}-->" if marker else ""


def _declared_fallback(source: str):
    markers = re.findall(r"<!--presenton-template-fallback:([a-f0-9]{64})-->", source)
    if len(markers) > 1:
        raise ValueError("svg_template_fallback_binding_invalid")
    return markers[0] if markers else None


def _inject_template_fonts(admitted, *, expected_fallback):
    """Only derive from admitted markup; never restore pre-admission attributes."""
    from utils.svg_template_fonts import FONT_PROFILE, materialize_template_fallbacks, template_font_faces

    match = _svg_fragment(admitted.html)
    canonical = match.group(1)
    if not _template_font_used(ET.fromstring(canonical)):
        raise ValueError("svg_trusted_font_profile_unused")
    derived, proof = materialize_template_fallbacks(canonical)
    if _fallback_binding(proof) != expected_fallback:
        raise ValueError("svg_template_fallback_binding_invalid")
    csp_font_none = "font-src &#x27;none&#x27;"
    if admitted.html.count(csp_font_none) != 1 or "</head>" not in admitted.html:
        raise ValueError("svg_trusted_font_csp_invalid")
    faces, digests = [], []
    for family, weight, digest, payload in template_font_faces(canonical):
        encoded = base64.b64encode(payload).decode("ascii")
        faces.append(f"@font-face{{font-family:'{family}';font-style:normal;font-weight:{weight};"
                     f"src:url(data:font/ttf;base64,{encoded}) format('truetype');}}")
        digests.append(digest)
    injected = admitted.html[:match.start(1)] + derived + admitted.html[match.end(1):]
    injected = injected.replace(csp_font_none, "font-src data:", 1).replace(
        "</head>", f'<style data-presenton-trusted-font-profile="{FONT_PROFILE}">'
        + "".join(faces) + "</style></head>", 1)
    added = len(list(ET.fromstring(derived).iter())) - len(list(ET.fromstring(canonical).iter()))
    return replace(admitted, html=injected,
        asset_digests=tuple(dict.fromkeys((*admitted.asset_digests, *digests))),
        node_count=admitted.node_count + 1 + added)


def _materialize_template_font(source: str, admitted):
    from utils.svg_template_fonts import trusted_font_marker

    declaration = f'<html title="{trusted_font_marker()}">'
    if source.count(declaration) != 1 or "data:font/" in source.casefold() or "@font-face" in source.casefold():
        raise ValueError("svg_trusted_font_profile_invalid")
    match = re.search(r"(<(?:[A-Za-z_][\w.-]*:)?svg\b.*?</(?:[A-Za-z_][\w.-]*:)?svg>)", source, re.I | re.S)
    if match is None or not _template_font_used(ET.fromstring(match.group(1))):
        raise ValueError("svg_trusted_font_profile_unused")
    return _inject_template_fonts(admitted, expected_fallback=_declared_fallback(source))


def _materialize_authorized_raster_font(canonical: ET.Element, admitted):
    """Embed packaged Noto for authorized raster derivatives with real text."""
    if not _template_font_used(canonical):
        return admitted
    from utils.svg_template_fonts import materialize_template_fallbacks
    _derived, proof = materialize_template_fallbacks(ET.tostring(canonical, encoding="unicode"))
    return _inject_template_fonts(admitted, expected_fallback=_fallback_binding(proof))


def admit_preserved_raster_preview(svg: str, materialized_svg: str, *, width: int, height: int,
                                   system_furniture: dict | None = None):
    """Recheck the bounded raster derivative in both parent and isolated worker.

    One embedded crop for a full_page_preserve page, N for a redesign page
    (screenshot/logo/table captures); either way every slot is required, so
    the canonical (slot-referenced) SVG keeps the normal 2MB contract limit
    while the expanded, base64-embedded materialized_svg is measured through
    the authorized-materialized admission's much larger wire/decode budget.
    """
    from utils.renderer_isolation import admit_authorized_svg_materialized

    canonical = ET.fromstring(svg)
    furniture = {}
    if system_furniture:
        from services.svg_master_furniture import attested_svg_furniture
        offered = {node.get("href"): {"required": True, "allowOmit": False} for node in canonical.iter()
                   if node.tag.rsplit("}", 1)[-1] == "image" and node.get("href", "").startswith("source-slot-")}
        furniture = attested_svg_furniture(svg, system_furniture, source_manifest=offered, width=width, height=height)
    slots = [node.get("href") for node in canonical.iter()
             if node.tag.rsplit("}", 1)[-1] == "image" and node.get("id") not in furniture]
    if (not slots and not system_furniture) or any(not slot for slot in slots):
        raise ValueError("raster_preview_image_slots_invalid")
    admitted = admit_authorized_svg_materialized(
        svg, materialized_svg,
        source_manifest={slot: {"required": True, "allowOmit": False} for slot in slots},
        width_px=width, height_px=height, allow_image_only=True,
        **({"system_furniture": system_furniture} if system_furniture else {}),
    )
    root = ET.fromstring(materialized_svg)
    for index, node in enumerate(root.iter()):
        node.set("data-qid", f"svg-{index}")
    measured = ET.tostring(root, encoding="unicode")
    fallback = _fallback_comment(measured) if _template_font_used(canonical) else ""
    wrapper = (f'<!DOCTYPE html><html><head><meta charset="utf-8"><style>'
               f'html,body{{margin:0;width:{width}px;height:{height}px;overflow:hidden}}'
            f'body>svg{{display:block}}</style>{fallback}</head><body>{measured}</body></html>')
    admitted = replace(admitted, html=admitted.html.replace(materialized_svg, measured),
                       source_digest=source_digest(wrapper))
    return _materialize_authorized_raster_font(canonical, admitted), wrapper



TEMPLATE_FONT_PROFILE = "svg-user-template-fonts/v1"


def _template_manifest_materials(font_manifest, svg: str):
    from models.svg_template_frame import SvgTemplateFontManifest
    from services.svg_templates.frame_fonts import resolve_font_manifest, verify_font_text

    manifest = SvgTemplateFontManifest.model_validate(font_manifest)
    entries = resolve_font_manifest(manifest)
    faces = {}
    for asset, _payload in entries:
        for name in (asset.family, *asset.family_aliases):
            key = (name.casefold(), asset.weight)
            if key in faces and faces[key].digest != asset.digest:
                raise ValueError("svg_template_font_face_conflict")
            faces[key] = asset
    used = {}
    if any(asset.collection_index != 0 for asset, _ in entries):
        raise ValueError("svg_template_font_collection_unsupported")
    root = ET.fromstring(svg)
    def visit(node, family=None, weight=400, font_style="normal"):
        style = {}
        for part in node.get("style", "").split(";"):
            if ":" in part:
                key, value = part.split(":", 1)
                style[key.strip().lower()] = value.strip()
        family = style.get("font-family", node.get("font-family", family))
        raw_weight = style.get("font-weight", node.get("font-weight", weight))
        font_style = style.get("font-style", node.get("font-style", font_style))
        try:
            weight = {"normal": 400, "bold": 700}.get(str(raw_weight).lower(), int(raw_weight) if str(raw_weight).isdigit() else None)
        except (TypeError, ValueError):
            weight = None
        tag = node.tag.rsplit("}", 1)[-1]
        if tag in {"style", "textPath"}:
            raise ValueError("svg_template_font_style_unsupported")
        if tag in {"text", "tspan"}:
            family = family.strip().strip("'\"") if isinstance(family, str) else None
            asset = faces.get((family.casefold(), weight)) if family and weight is not None else None
            if node.text and node.text.strip():
                if asset is None or font_style != "normal":
                    raise ValueError("svg_template_font_unavailable")
                verify_font_text(asset, node.text)
                used[(family.casefold(), weight)] = (family, weight, asset)
        for child in node:
            visit(child, family, weight, font_style)
            if child.tail and child.tail.strip() and tag in {"text", "tspan"}:
                asset = faces.get((family.casefold(), weight)) if family and weight is not None else None
                if asset is None or font_style != "normal":
                    raise ValueError("svg_template_font_unavailable")
                verify_font_text(asset, child.tail)
                used[(family.casefold(), weight)] = (family, weight, asset)
    visit(root)
    return manifest, entries, tuple(used.values())



def template_image_bindings(canonical_svg: str, materialized_svg: str) -> dict[str, str]:
    """Bind each authorized source slot to the exact resolved raster bytes."""
    from utils.hashing import sha256_prefixed
    canonical = ET.fromstring(canonical_svg)
    materialized = ET.fromstring(materialized_svg)
    originals = [node for node in canonical.iter() if node.tag.rsplit("}", 1)[-1] == "image"]
    resolved = [node for node in materialized.iter() if node.tag.rsplit("}", 1)[-1] == "image"]
    if len(originals) != len(resolved):
        raise ValueError("svg_template_image_binding_mismatch")
    bindings = {}
    for original, derived in zip(originals, resolved, strict=True):
        old, new = original.get("href", ""), derived.get("href", "")
        if old.startswith("source-slot-"):
            match = re.fullmatch(r"data:image/(png|jpeg);base64,([A-Za-z0-9+/=]+)", new)
            if match is None:
                raise ValueError("svg_template_image_binding_invalid")
            try:
                digest = sha256_prefixed(base64.b64decode(match.group(2), validate=True))
            except ValueError as error:
                raise ValueError("svg_template_image_binding_invalid") from error
            if old in bindings and bindings[old] != digest:
                raise ValueError("svg_template_image_binding_mismatch")
            bindings[old] = digest
        elif old != new:
            raise ValueError("svg_template_fixed_image_changed")
    return bindings


def validate_template_render_furniture(source: str, canonical_svg: str, furniture: dict, *,
                                       width: int, height: int, image_bindings: dict[str, str]) -> None:
    """Re-admit measured raster derivatives while binding frame and slot bytes."""
    from utils.renderer_isolation import admit_authorized_svg_materialized
    match = _svg_fragment(source)
    if match is None:
        raise ValueError("svg_template_furniture_wrapper_invalid")
    canonical = ET.fromstring(canonical_svg)
    measured = ET.fromstring(match.group(1))
    for index, node in enumerate(measured.iter()):
        if node.attrib.pop("data-qid", None) != f"svg-{index}":
            raise ValueError("svg_template_furniture_wrapper_invalid")
    resolved = ET.tostring(measured, encoding="unicode")
    expected = template_image_bindings(canonical_svg, resolved)
    if image_bindings != expected:
        raise ValueError("svg_template_image_binding_mismatch")
    source_manifest = {node.get("href"): {"required": True, "allowOmit": False}
        for node in canonical.iter() if node.tag.rsplit("}", 1)[-1] == "image"
        and node.get("href", "").startswith("source-slot-")}
    admit_authorized_svg_materialized(canonical_svg, resolved, source_manifest=source_manifest,
        width_px=width, height_px=height, system_furniture=furniture)


def materialize_template_manifest_fonts(source: str, admitted, font_manifest):
    match = _svg_fragment(source)
    if match is None:
        raise ValueError("svg_trusted_font_wrapper_invalid")
    manifest, entries, _used = _template_manifest_materials(font_manifest, match.group(1))
    declaration = f'<html title="{TEMPLATE_FONT_PROFILE}|{manifest.digest}">'
    if source.count(declaration) != 1 or "data:font/" in source.casefold() or "@font-face" in source.casefold():
        raise ValueError("svg_template_font_binding_invalid")
    return materialize_template_manifest_admitted(admitted, font_manifest, match.group(1))


def materialize_template_manifest_admitted(admitted, font_manifest, svg: str):
    _manifest, entries, used = _template_manifest_materials(font_manifest, svg)
    csp = "font-src &#x27;none&#x27;"
    if admitted.html.count(csp) != 1 or "</head>" not in admitted.html:
        raise ValueError("svg_trusted_font_csp_invalid")
    faces = []
    payloads = {asset.digest: payload for asset, payload in entries}
    for declared_family, weight, asset in used:
        family = '"' + "".join(f"\\{ord(char):x} " for char in declared_family) + '"'
        encoded = base64.b64encode(payloads[asset.digest]).decode("ascii")
        faces.append(f"@font-face{{font-family:{family};font-style:normal;font-weight:{weight};"
                     f"src:url(data:font/ttf;base64,{encoded}) format('truetype');}}")
    html = admitted.html.replace(csp, "font-src data:", 1).replace(
        "</head>", f'<style data-presenton-trusted-font-profile="{TEMPLATE_FONT_PROFILE}">'
        + "".join(faces) + "</style></head>", 1)
    return replace(admitted, html=html,
        asset_digests=tuple(dict.fromkeys((*admitted.asset_digests, *(a.digest for a, _ in entries)))),
        node_count=admitted.node_count + 1)

def svg_preview_html(svg: str, *, width: int = DEFAULT_CANVAS_WIDTH_PX, height: int = DEFAULT_CANVAS_HEIGHT_PX,
                     materialized_svg: str | None = None,
                     source_font_profile: str | None = None,
                     allow_image_only: bool = False, system_furniture: dict | None = None,
                     font_manifest: dict | None = None, _frame_review: bool = False) -> str:
    if font_manifest is not None and (allow_image_only or source_font_profile is not None):
        raise ValueError("svg_template_font_profile_conflict")
    if allow_image_only and materialized_svg is not None:
        return admit_preserved_raster_preview(svg, materialized_svg, width=width, height=height,
            **({"system_furniture": system_furniture} if system_furniture else {}))[1]
    rendered_source = materialized_svg if materialized_svg is not None else svg
    validate_svg(rendered_source, width=width, height=height, allow_image_only=allow_image_only,
                 **({"_allow_textless_frame_review": True} if _frame_review else {}))
    if materialized_svg is not None:
        # Only image hrefs may differ at the authorized resolver boundary.
        # Geometry and text must remain the canonical document being measured.
        canonical_root = ET.fromstring(svg)
        resolved_root = ET.fromstring(materialized_svg)
        for document in (canonical_root, resolved_root):
            for node in document.iter():
                if node.tag.rsplit("}", 1)[-1] == "image":
                    for key in ("href", "{http://www.w3.org/1999/xlink}href"):
                        node.attrib.pop(key, None)
        if ET.tostring(canonical_root) != ET.tostring(resolved_root):
            raise ValueError("svg_materialized_source_mismatch")
    root = ET.fromstring(rendered_source)
    weights, _advisories = _trusted_font_weights_used(root)
    template_font = False
    if source_font_profile is not None:
        from utils.svg_template_fonts import trusted_font_marker
        if source_font_profile == trusted_font_marker():
            template_font = _template_font_used(root)
            if not template_font:
                raise ValueError("svg_template_font_profile_unused")
        elif source_font_profile != _trusted_font_marker():
            raise ValueError("svg_trusted_font_profile_invalid")
    # Measurement IDs exist only in this deterministic derivative. The SVG
    # source remains the same bytes for persistence and editable export.
    for i, node in enumerate(root.iter()):
        node.set("data-qid", f"svg-{i}")
    rendered = ET.tostring(root, encoding="unicode")
    marker = f' title="{_trusted_font_marker()}"' if weights else ""
    fallback = ""
    if font_manifest is not None:
        manifest, _entries, _used = _template_manifest_materials(font_manifest, rendered_source)
        marker = f' title="{TEMPLATE_FONT_PROFILE}|{manifest.digest}"'
    elif template_font:
        from utils.svg_template_fonts import trusted_font_marker
        marker = f' title="{trusted_font_marker()}"'
        fallback = _fallback_comment(rendered)
    elif source_font_profile is None:
        from services.svg_default_fonts import font_marker, uses_default_font
        if uses_default_font(root):
            marker = f' title="{font_marker()}"'
    return (f'<!DOCTYPE html><html{marker}><head><meta charset="utf-8"><style>'
            f'html,body{{margin:0;width:{width}px;height:{height}px;overflow:hidden}}'
            f'body>svg{{display:block}}</style>{fallback}</head><body>{rendered}</body></html>')



async def render_template_frame_for_review(frame) -> bytes:
    """Render a server-derived fixed frame, including textless graphic frames."""
    from models.svg_template_frame import SvgTemplateFrame
    from services.svg_templates.frame_composition import render_frame_svg
    from utils.renderer_worker_policy import ensure_renderer_enabled
    from utils.slide_capture import _get_render_sem
    from utils.slide_capture_worker_client import _render_html_in_isolated_worker

    frame = SvgTemplateFrame.model_validate(frame)
    svg = render_frame_svg(frame)
    canvas = frame.source_canvas
    wrapper = svg_preview_html(svg, width=canvas.width_px, height=canvas.height_px,
                               font_manifest=frame.font_manifest, _frame_review=True)
    ensure_renderer_enabled()
    async with _get_render_sem():
        png, metrics = await _render_html_in_isolated_worker(wrapper, 60,
            width_px=canvas.width_px, height_px=canvas.height_px,
            font_manifest=frame.font_manifest.model_dump(mode="json"))
    has_text = any(node.tag.rsplit("}", 1)[-1] in {"text", "tspan"}
                   and (node.text or "").strip() for node in ET.fromstring(svg).iter())
    if has_text:
        text_metrics = [item for item in (metrics or {}).get("elements", []) if item.get("has_owned_text")]
        if not (metrics or {}).get("fonts_ready") or not text_metrics or any(
                item.get("font_loaded") is not True for item in text_metrics):
            raise ValueError("svg_template_review_font_unresolved")
    return png

def _template_fallback_evidence(svg: str, wrapper: str, *, authorized_raster=False):
    """Bind raw canonical runs to the admitted, measured derivative and font bytes."""
    from utils.svg_template_fonts import materialize_template_fallbacks

    _derived, canonical = materialize_template_fallbacks(svg)
    if canonical is None:
        return None
    if authorized_raster:
        measured = _svg_fragment(wrapper).group(1)
    else:
        from utils.renderer_isolation import RendererPolicyError, admit_authored_html, admit_static_output_html
        try:
            admitted = admit_authored_html(wrapper)
        except RendererPolicyError:
            admitted = admit_static_output_html(wrapper)
        measured = _svg_fragment(admitted.html).group(1)
    _derived, measured_proof = materialize_template_fallbacks(measured)
    binding = _fallback_binding(canonical)
    if binding != _fallback_binding(measured_proof) or binding != _declared_fallback(wrapper):
        raise ValueError("svg_template_fallback_binding_invalid")
    return canonical, measured_proof


@dataclass(frozen=True)
class SvgRenderResult:
    png: bytes
    quality: dict
    wrapper_html: str
    source_digest: str


async def render_svg_with_metrics(svg: str, *, width: int = DEFAULT_CANVAS_WIDTH_PX,
                                  height: int = DEFAULT_CANVAS_HEIGHT_PX,
                                  timeout: int = 60, deck_scope: str | None = None,
                                  minimum_font_px: float | None = None,
                                  instructions: str | None = None,
                                  required_text: list[str] | None = None,
                                  materialized_svg: str | None = None,
                                  physical_canvas: object | None = None,
                                  readability_policy: dict | None = None,
                                  source_font_profile: str | None = None,
                                  allow_image_only: bool = False,
                                  system_furniture: dict | None = None,
                                  timeline_ink_probe: bool | None = None,
                                  font_manifest: dict | None = None) -> SvgRenderResult:
    from utils.authored_font_policy import requested_font_floor_px
    from utils.slide_capture import render_html_to_png_with_metrics
    if readability_policy is not None and physical_canvas is not None:
        from services.document_canvas import canvas_physical_size
        from services.document_canvas_generation import validate_svg_readability_policy

        policy = validate_svg_readability_policy(readability_policy)
        if canvas_physical_size(physical_canvas) != (policy["width_emu"], policy["height_emu"]):
            raise ValueError("svg_readability_canvas_mismatch")
    wrapper = svg_preview_html(svg, width=width, height=height,
                               materialized_svg=materialized_svg, source_font_profile=source_font_profile,
                               allow_image_only=allow_image_only, font_manifest=font_manifest,
                               **({"system_furniture": system_furniture} if system_furniture else {}))
    if timeline_ink_probe is True:
        # Internal measurement option belongs to the derivative HTML, never SVG.
        wrapper = wrapper.replace("<body>", '<body class="codex-timeline-ink-v1">', 1)
    if (allow_image_only and materialized_svg is not None) or font_manifest is not None:
        from utils.slide_capture_worker_client import _render_html_in_isolated_worker
        from utils.slide_capture_result import RenderCapture
        from utils.renderer_worker_policy import ensure_renderer_enabled
        from utils.slide_capture import _get_render_sem
        ensure_renderer_enabled()
        async with _get_render_sem():
            png, browser_metrics = await _render_html_in_isolated_worker(
                wrapper if font_manifest is not None else svg, timeout, width_px=width, height_px=height,
                **({"font_manifest": font_manifest.model_dump(mode="json") if hasattr(font_manifest, "model_dump") else font_manifest} if font_manifest is not None else {"preserved_raster_svg": materialized_svg}),
                **({"system_furniture": system_furniture} if system_furniture else {}),
                **({"canonical_svg": svg,
                    "image_bindings": template_image_bindings(svg, materialized_svg or svg)}
                   if font_manifest is not None and system_furniture else {}),
            )
        capture = RenderCapture.from_payload(
            html_digest=source_digest(wrapper), png=png, browser_metrics=browser_metrics,
            runtime_counters={},
        )
    else:
        capture = await render_html_to_png_with_metrics(wrapper, timeout=timeout,
                width_px=width, height_px=height, deck_scope=deck_scope, fit=False)
    # Probe cache keys contain NUL separators, which PostgreSQL JSON operators
    # cannot decode. They are diagnostic keys, never source text.
    metrics = json.loads(json.dumps(capture.browser_metrics or {}).replace("\\u0000", "\\u241f"))
    elements = metrics.get("elements") or []
    text = [item for item in elements if item.get("has_owned_text")]
    failures = []
    from services.document_canvas_generation import generation_font_floor
    floor = minimum_font_px if minimum_font_px is not None else generation_font_floor(None, physical_canvas)
    requested_floor = requested_font_floor_px(
        instructions, point_per_unit=float(getattr(physical_canvas, "point_per_unit", 0.75))
    )
    if requested_floor is not None:
        floor = requested_floor
    if readability_policy is not None:
        from services.document_canvas_generation import svg_readability_font_floor

        floor = max(floor, svg_readability_font_floor(readability_policy))
    root = ET.fromstring(svg)
    footer_qid = next((f"svg-{i}" for i, node in enumerate(root.iter()) if node.get("id") == PAGE_NUMBER_ID), None)
    furniture_qids = set()
    if system_furniture:
        from services.svg_master_furniture import attested_svg_furniture
        source_contract = {node.get("href"): {"required": True} for node in root.iter()
                           if node.tag.rsplit("}", 1)[-1] == "image" and node.get("href", "").startswith("source-slot-")}
        furniture_nodes = attested_svg_furniture(svg, system_furniture,
            source_manifest=source_contract if materialized_svg is not None else None, width=width, height=height)
        furniture_qids = {f"svg-{i}" for i, node in enumerate(root.iter()) if node.get("id") in furniture_nodes}
    images = [item for item in elements if item.get("tag") == "image"]
    # Approved raster pages have already passed digest, decoding and source
    # geometry checks at materialization. Measure their visible image surface.
    image_measurement_complete = bool(allow_image_only and materialized_svg and images) and all(
        item.get("css_visible", False) and item.get("effective_opacity", 0) >= 0.9
        and item.get("bbox", {}).get("width", 0) > 0
        and item.get("bbox", {}).get("height", 0) > 0
        and item.get("viewport_intersection_ratio", 0) >= 0.999
        for item in images
    )
    measurement_complete = bool(metrics and not metrics.get("probe_error")
                                and metrics.get("fonts_ready") and (text or image_measurement_complete))
    if not measurement_complete:
        failures.append({"kind": "measurement_incomplete"})
    for item in text:
        qid = item.get("qid")
        rects = item.get("text_fragments") or [item.get("bbox", {})]
        for r in rects:
            x, y = r.get("x", r.get("left", 0)), r.get("y", r.get("top", 0))
            if x < -0.25 or y < -0.25 or x + r.get("width", 0) > width + 0.25 or y + r.get("height", 0) > height + 0.25:
                failures.append({"kind": "text_off_canvas", "qid": qid})
        if item.get("glyph_clipped"):
            failures.append({"kind": "text_clipped", "qid": qid})
        if not item.get("css_visible", True) or item.get("effective_opacity", 1) < 0.9:
            failures.append({"kind": "text_hidden", "qid": qid})
        if qid != footer_qid and qid not in furniture_qids and item.get("font_size", 0) < floor:
            failures.append({"kind": "small_body_text", "qid": qid, "minimum_px": floor})
        if item.get("font_loaded") is False:
            failures.append({"kind": "font_unresolved", "qid": qid})
    # Glyph ink where the probe established it, else the line box (fail closed).
    from services.svg_text_overlap import text_overlaps
    overlaps, overlap_basis = text_overlaps(text)
    if overlaps:
        failures.append({"kind": "text_overlap", "pairs": overlaps})
    if "ordered_owned_text" in metrics:
        measured_owners = {item.get("qid") for item in text if item.get("qid")}
        ordered = metrics["ordered_owned_text"]
        owned = "".join(item["text"] for item in ordered
            if isinstance(item, dict) and item.get("qid") in measured_owners
            and isinstance(item.get("text"), str)) if isinstance(ordered, list) else ""
    else:
        owned = "".join(item.get("owned_text", "") for item in text)
    normalized = "".join(owned.split())
    missing = [value for value in required_text or [] if "".join(value.split()) not in normalized]
    if missing:
        # Keep the literal gate unchanged, but give its bounded author repair
        # the exact missing wording rather than an unactionable count alone.
        witnesses = [value for value in missing if len(value) <= 160][:8]
        failures.append({"kind": "required_text_missing", "count": len(missing),
            "missing_literals": witnesses,
            **({"missing_literals_truncated": True} if len(witnesses) < len(missing) else {})})
    font_profile = None
    trusted_weights, weight_advisories = _trusted_font_weights_used(root)
    if trusted_weights:
        font_profile = {"profile": TRUSTED_FONT_PROFILE,
            "font_digests": [digest for _weight, digest, _payload in _trusted_font_materials()],
            "worker_materialized": bool(text) and all(item.get("font_loaded") is True for item in text),
            **({"weight_normalizations": weight_advisories} if weight_advisories else {})}
    from utils.svg_template_fonts import FONT_PROFILE
    raster_template_font = bool(allow_image_only and materialized_svg is not None and _template_font_used(root))
    if f'<html title="{FONT_PROFILE}|' in wrapper or raster_template_font:
        from utils.svg_template_fonts import FONT_DIGEST, FONT_FAMILY, FONT_PROFILE
        font_profile = {"profile": FONT_PROFILE, "family": FONT_FAMILY,
            "font_digests": [FONT_DIGEST],
            "worker_materialized": bool(text) and all(item.get("font_loaded") is True for item in text)}
        fallback = _template_fallback_evidence(svg, wrapper, authorized_raster=raster_template_font)
        if fallback is not None:
            canonical_proof, measured_proof = fallback
            font_profile.update(
                font_digests=[FONT_DIGEST, canonical_proof["fallback_font_digest"]],
                fallback=canonical_proof, render_fallback=measured_proof)
    if font_manifest is not None:
        manifest, entries, _used = _template_manifest_materials(font_manifest, svg)
        font_profile = {"profile": TEMPLATE_FONT_PROFILE, "manifest_digest": manifest.digest,
            "font_digests": [asset.digest for asset, _ in entries],
            "worker_materialized": bool(text) and all(item.get("font_loaded") is True for item in text)}
    from services.svg_default_fonts import FONT_PROFILE as DEFAULT_FONT_PROFILE
    if f'<html title="{DEFAULT_FONT_PROFILE}|' in wrapper:
        from services.svg_trusted_fonts import DEFAULT_FONT_FAMILY
        font_profile = {"profile": DEFAULT_FONT_PROFILE, "family": DEFAULT_FONT_FAMILY,
            "worker_materialized": bool(text) and all(item.get("font_loaded") is True for item in text)}
    quality = {"contract_version": SVG_CONTRACT_VERSION, "renderer_version": SVG_RENDERER_VERSION,
               "source_digest": source_digest(svg), "png_digest": capture.png_digest,
               "wrapper_digest": capture.html_digest, "metrics_digest": capture.metrics_digest,
               "measurement_complete": measurement_complete,
               "geometry_passed": not failures, "passed": not failures,
               "verified": False, "semantic_review": "required", "failures": failures,
               "body_font_floor_px": floor, "browser_metrics": metrics,
               "overlap_basis": overlap_basis,
               "trusted_font_materialization": font_profile}
    if readability_policy is not None:
        from services.document_canvas_generation import measure_svg_readability

        content_text = [item for item in text if item.get("qid") != footer_qid
                        and item.get("qid") not in furniture_qids]
        physical, physical_failures = measure_svg_readability(readability_policy, width=width,
            height=height, text=content_text, measurement_complete=measurement_complete)
        quality["physical_readability"] = physical
        quality["failures"].extend(physical_failures)
        quality["geometry_passed"] = quality["passed"] = not quality["failures"]
    return SvgRenderResult(capture.png, quality, wrapper, source_digest(svg))
