"""Fixed packaged template fonts shared by SVG measurement and capture.

No caller-supplied font paths, URLs, aliases or fallback families are accepted.
"""
from __future__ import annotations

from functools import lru_cache
from hashlib import sha256
from io import BytesIO
from pathlib import Path

FONT_PROFILE = "svg-template-noto-sans-kr/v1"
FONT_FAMILY = "Noto Sans KR"
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
    digest = "sha256:" + sha256(payload).hexdigest()
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
    font_material(family)
    coverage = _packaged_font()[2]
    if any(ord(c) not in coverage for c in text if not c.isspace()):
        raise ValueError("svg_template_font_glyph_unavailable")


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
        face.set_variation_by_axes([{"normal": 400, "bold": 700}.get(str(weight), int(weight) if str(weight).isdigit() else 400)])
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
