"""Pinned primary template font with one reviewed, bounded OS symbol fallback.

No caller-supplied font paths, URLs, aliases or fallback families are accepted.
"""

from __future__ import annotations

from functools import lru_cache
from io import BytesIO
from pathlib import Path
import json
from xml.etree import ElementTree as ET
from utils.hashing import sha256_prefixed

FONT_PROFILE = "svg-template-noto-sans-kr/v1"
FONT_FAMILY = "Noto Sans KR"
FALLBACK_FONT_FAMILY = "Segoe UI Symbol"
FONT_DIGEST = "sha256:194018e6b2b293a7964f037b25c0249ce1418bc9ab3c971060a03aa57861e252"
FONT_PATH = Path(__file__).resolve().parents[2] / "nextjs/public/fonts/noto-sans-kr/NotoSansKR-Variable.ttf"


@lru_cache(maxsize=1)
def _packaged_font() -> tuple[str, bytes, frozenset[int]]:
    from fontTools.ttLib import TTFont
    from services.authored_vnext.semantic.typography import NativeFontByteRegistry

    try:
        payload = FONT_PATH.read_bytes()
    except OSError as exc:
        raise ValueError("svg_template_font_unavailable:Noto Sans KR") from exc
    digest = sha256_prefixed(payload)
    if digest != FONT_DIGEST:
        raise ValueError("svg_template_font_digest_mismatch")
    receipt = NativeFontByteRegistry().register(payload)
    if receipt.family != FONT_FAMILY or not receipt.embedding_eligible:
        raise ValueError("svg_template_font_identity_or_license_invalid")
    with TTFont(BytesIO(payload)) as face:
        coverage = frozenset(face.getBestCmap())
        axes = [(a.axisTag, a.minValue, a.maxValue) for a in face["fvar"].axes]
    if axes != [("wght", 100, 900)] or not all(c in coverage for c in range(0xAC00, 0xD7A4)):
        raise ValueError("svg_template_font_hangul_or_weight_unsupported")
    return digest, payload, coverage


def font_material(family: str, weight: int | str = 400) -> tuple[str, bytes]:
    if family != FONT_FAMILY or str(weight) not in {"400", "700", "800", "normal", "bold"}:
        raise ValueError("svg_template_font_unsupported")
    digest, payload, _ = _packaged_font()
    return digest, payload


def require_font_coverage(text: str, family: str = FONT_FAMILY) -> None:
    trusted_font_fallback_plan(text, family)


@lru_cache(maxsize=1)
def fallback_font_material() -> tuple[str, int, str, bytes]:
    from services.authored_vnext.semantic.typography import load_bounded_windows_symbol_registry

    registry = load_bounded_windows_symbol_registry()
    face = registry.resolve(FALLBACK_FONT_FAMILY, "Normal")
    if face is None or not face.receipt.embedding_eligible:
        raise ValueError("svg_template_fallback_font_unavailable")
    receipt = face.receipt
    return receipt.family, 400, receipt.font_byte_digest, registry.font_bytes(receipt.family, receipt.face)


@lru_cache(maxsize=1)
def _fallback_coverage() -> frozenset[int]:
    from fontTools.ttLib import TTFont

    with TTFont(BytesIO(fallback_font_material()[3])) as face:
        return frozenset(face.getBestCmap())


def trusted_font_fallback_plan(text: str, family: str = FONT_FAMILY, weight: int | str = 400) -> dict:
    """Assign only primary-missing characters to one verified fixed OS face."""
    digest, _ = font_material(family, weight)
    requested = {"normal": 400, "bold": 700}.get(str(weight), int(weight) if str(weight).isdigit() else 0)
    coverage = _packaged_font()[2]
    missing = {ord(char) for char in text if not char.isspace() and ord(char) not in coverage}
    fallback = fallback_font_material() if missing else None
    if missing and not missing.issubset(_fallback_coverage()):
        raise ValueError("svg_template_font_glyph_unavailable")
    segments = []
    for offset, char in enumerate(text):
        use_fallback = ord(char) in missing
        resolved, face_weight, face_digest = ((fallback[0], fallback[1], fallback[2]) if use_fallback
                                             else (family, requested, digest))
        if segments and segments[-1]["family"] == resolved:
            segments[-1]["text"] += char
            segments[-1]["end"] = offset + 1
        else:
            segments.append(dict(text=char, start=offset, end=offset + 1, family=resolved,
                weight=requested, requested_weight=requested, face_weight=face_weight,
                font_digest=face_digest, synthetic_bold=use_fallback and requested >= 600))
    return dict(schema="svg-template-glyph-fallback-plan/v1", offset_unit="unicode-codepoint", primary_family=family,
        primary_font_digest=digest, requested_weight=requested, segments=segments,
        fallback_font_digest=fallback[2] if fallback else None)


def materialize_template_fallbacks(svg: str) -> tuple[str, dict | None]:
    """Make a font-only derivative; preserve original nodes, positions and text."""
    root = ET.fromstring(svg)
    namespace = "{http://www.w3.org/2000/svg}"
    texts = [node for node in root.iter() if node.tag == namespace + "text"]
    runs = []
    for text_index, text_node in enumerate(texts):
        offset = 0
        original_text = "".join(text_node.itertext())

        def piece(value, family, weight):
            nonlocal offset
            if not value:
                return value, []
            plan = trusted_font_fallback_plan(value, family, weight)
            prefix, added = None, []
            for segment in plan["segments"]:
                if segment["family"] == FONT_FAMILY:
                    if added:
                        added[-1].tail = (added[-1].tail or "") + segment["text"]
                    else:
                        prefix = (prefix or "") + segment["text"]
                else:
                    node = ET.Element(namespace + "tspan", {
                        "font-family": segment["family"], "font-weight": str(segment["weight"])})
                    node.text = segment["text"]
                    added.append(node)
                    runs.append(dict(text_index=text_index, element_id=text_node.get("id"),
                        start=offset + segment["start"], end=offset + segment["end"],
                        codepoints=[ord(char) for char in segment["text"]],
                        **{key: segment[key] for key in ("family", "weight", "requested_weight",
                            "face_weight", "font_digest", "synthetic_bold")}))
            offset += len(value)
            return prefix, added

        def visit(node, inherited_family, inherited_weight):
            family = node.get("font-family", inherited_family).strip("'\"")
            weight = node.get("font-weight", inherited_weight)
            font_material(family, weight)
            children = list(node)
            node.text, rebuilt = piece(node.text, family, weight)
            for child in children:
                if child.tag != namespace + "tspan":
                    raise ValueError("svg_template_font_mixed_families")
                tail = child.tail
                visit(child, family, weight)
                child.tail, tail_nodes = piece(tail, family, weight)
                rebuilt.append(child)
                rebuilt.extend(tail_nodes)
            node[:] = rebuilt

        visit(text_node, "", "400")
        if "".join(text_node.itertext()) != original_text:
            raise ValueError("svg_template_fallback_text_changed")
    if not runs:
        return svg, None
    derived = ET.tostring(root, encoding="unicode")
    family, weight, digest, _ = fallback_font_material()
    proof = dict(schema="svg-template-glyph-fallback/v1", offset_unit="unicode-codepoint", canonical_svg_digest=sha256_prefixed(svg.encode()),
        derived_svg_digest=sha256_prefixed(derived.encode()), primary_family=FONT_FAMILY,
        primary_font_digest=FONT_DIGEST, fallback_family=family, fallback_font_digest=digest,
        fallback_weight=weight, runs=runs)
    proof["proof_digest"] = sha256_prefixed(json.dumps(proof, sort_keys=True, ensure_ascii=False,
                                                     separators=(",", ":")).encode())
    return derived, proof


def template_font_faces(svg: str) -> tuple[tuple[str, int | str, str, bytes], ...]:
    _, proof = materialize_template_fallbacks(svg)
    digest, payload = font_material(FONT_FAMILY)
    primary = (FONT_FAMILY, "100 900", digest, payload)
    return (primary, fallback_font_material()) if proof else (primary,)


@lru_cache(maxsize=64)
def measure_font(family: str, weight: int | str, size: float):
    from PIL import ImageFont

    if family == "Pretendard":
        from services.svg_renderer import _trusted_font_materials

        normalized = {"normal": 400, "bold": 700}.get(str(weight), int(weight) if str(weight).isdigit() else 0)
        payload = next((data for w, _digest, data in _trusted_font_materials() if w == normalized), None)
        if payload is None:
            raise ValueError("svg_template_font_unsupported")
    else:
        _, payload = font_material(family, weight)
    if not 0 < size <= 4096:
        raise ValueError("svg_template_font_size_invalid")
    face = ImageFont.truetype(BytesIO(payload), size=size)
    if family == FONT_FAMILY:
        face.set_variation_by_axes(
            [{"normal": 400, "bold": 700}.get(str(weight), int(weight) if str(weight).isdigit() else 400)]
        )
    return face


def trusted_font_marker(family: str = FONT_FAMILY) -> str:
    digest, _ = font_material(family)
    return FONT_PROFILE + "|" + digest


def source_font_profile(snapshot: dict) -> str:
    template = snapshot.get("source_template", snapshot)
    roles = template.get("fonts", {})
    if not roles or any(r.get("family") != FONT_FAMILY or r.get("digest") != FONT_DIGEST for r in roles.values()):
        raise ValueError("svg_template_font_snapshot_invalid")
    return trusted_font_marker()
