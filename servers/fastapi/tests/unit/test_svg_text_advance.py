"""Single-line svg text frames are never narrower than their text.

LibreOffice ignores ``wrap="none"`` and folds a frame narrower than its text:
the canvas-bench pilot's "68%" (Noto Sans KR 32px bold) got a 68.45px frame
from the converter's per-character estimate while the glyphs advance 68.59px,
and "%" dropped onto the next line over the neighbouring caption.
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from services.authored_canvas import canvas_metadata_for_aspect_ratio
from services.document_canvas import canvas_physical_size
from services.svg_text_advance import text_advance
from workers.public_svg_pptx_convert import VENDOR_ROOT, convert_pptx

NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
}
EMU_PER_PX = 9525  # 16:9 legacy canvas
NOTO = "'Noto Sans KR', sans-serif"


def _svg(body: str) -> str:
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">{body}</svg>'


def _label(text: str, x: float, y: float, anchor: str, *, family: str = NOTO, size: int = 32) -> str:
    return (f'<text x="{x}" y="{y}" font-family="{family}" font-size="{size}" font-weight="700" '
            f'text-anchor="{anchor}" fill="#0F172A">{text}</text>')


def _frames(pptx: Path) -> dict[str, tuple[int, int, str, str]]:
    """``text -> (off_x, cx, wrap, algn)`` for every text frame on slide 1."""
    with zipfile.ZipFile(pptx) as archive:
        slide = ET.fromstring(archive.read("ppt/slides/slide1.xml"))
    frames = {}
    for shape in slide.iter(f"{{{NS['p']}}}sp"):
        body = shape.find("p:txBody", NS)
        if body is None:
            continue
        text = "".join(node.text or "" for node in body.iter(f"{{{NS['a']}}}t"))
        off, ext = shape.find("p:spPr/a:xfrm/a:off", NS), shape.find("p:spPr/a:xfrm/a:ext", NS)
        wrap = body.find("a:bodyPr", NS).get("wrap")
        algn = body.find("a:p/a:pPr", NS).get("algn")
        frames[text] = (int(off.get("x")), int(ext.get("cx")), wrap, algn)
    return frames


def _convert_vendor(tmp_path: Path, name: str, svg: str, *, measured: bool, merge_paragraphs: bool) -> Path:
    """The worker's converter call shape, with or without the measurer."""
    canvas = canvas_metadata_for_aspect_ratio("16:9")
    width_emu, height_emu = canvas_physical_size(canvas)
    source, target = tmp_path / f"{name}.svg", tmp_path / f"{name}.pptx"
    source.write_text(svg, encoding="utf-8")
    sys.path.insert(0, str(VENDOR_ROOT))
    try:
        from svg_to_pptx import create_pptx_with_native_svg
        from svg_to_pptx.conversion_context import ConversionContext

        context = ConversionContext(canvas.width_px, canvas.height_px, width_emu, height_emu, legacy=True,
                                    text_advance=text_advance if measured else None)
        assert create_pptx_with_native_svg(
            [source], target, verbose=False, transition=None, use_native_shapes=True,
            notes={source.stem: ""}, workers=1, merge_paragraphs=merge_paragraphs,
            image_optimize=False, pptx_structure="flat", use_compat_mode=False,
            conversion_context=context, native_objects=False,
        )
    finally:
        sys.path.remove(str(VENDOR_ROOT))
    return target


def test_measurer_answers_only_for_bundled_families_weights_and_glyphs():
    bold = text_advance("68%", 32, "700", NOTO)
    assert bold == pytest.approx(68.59, abs=0.05)
    assert text_advance("68%", 64, "bold", NOTO) == pytest.approx(bold * 2)
    assert text_advance("68%", 32, "400", NOTO) < bold
    assert text_advance("68%", 32, "600", "Pretendard") == text_advance("68%", 32, "700", "Pretendard")
    assert text_advance("68%", 32, "700", "Arial, 'Noto Sans KR'") is None
    assert text_advance("68%", 32, "700", "sans-serif") is None
    assert text_advance("68%", 32, "bolder", NOTO) is None
    assert text_advance("68%", 32, "950", NOTO) is None
    assert text_advance("68\U0001F600", 32, "700", NOTO) is None
    assert text_advance("68%", 0, "700", NOTO) is None


@pytest.mark.parametrize("weight", [400, 700, 800])
def test_trusted_template_fallback_preserves_source_nodes_text_and_requested_weight(weight):
    from utils.svg_template_fonts import (
        FONT_DIGEST, fallback_font_material, materialize_template_fallbacks,
        template_font_faces, trusted_font_fallback_plan,
    )
    from utils.hashing import canonical_json_sha256, sha256_prefixed

    source = _svg(f'<text id="source-a" data-qid="svg-1" x="40" y="80" font-family="Noto Sans KR" '
        f'font-weight="{weight}" font-size="24">가\u25b8<tspan id="line-a" data-qid="svg-2" '
        'x="40" y="110">나\u21bb</tspan>다\u2610</text>'
        '<text id="source-b" data-qid="svg-3" x="40" y="160" font-family="Noto Sans KR" '
        f'font-weight="{weight}" font-size="24">라\u25be</text>')
    before = ET.fromstring(source)
    derived, proof = materialize_template_fallbacks(source)
    after = ET.fromstring(derived)
    source_texts = [node for node in before.iter() if node.tag.endswith("}text")]
    derived_texts = [node for node in after.iter() if node.tag.endswith("}text")]
    assert ["".join(node.itertext()) for node in derived_texts] == ["".join(node.itertext()) for node in source_texts]
    old_nodes = {node.get("id"): node for node in before.iter() if node.get("id")}
    new_nodes = {node.get("id"): node for node in after.iter() if node.get("id")}
    assert set(old_nodes) == set(new_nodes)
    assert all(old.attrib == new_nodes[identity].attrib for identity, old in old_nodes.items())
    inserted = [node for node in after.iter() if node.get("font-family") == "Segoe UI Symbol"]
    assert len(inserted) == 4
    assert all(node.attrib == {"font-family": "Segoe UI Symbol", "font-weight": str(weight)} for node in inserted)
    assert proof["canonical_svg_digest"] == sha256_prefixed(source.encode())
    assert proof["derived_svg_digest"] == sha256_prefixed(derived.encode())
    assert proof["primary_font_digest"] == FONT_DIGEST
    assert proof["proof_digest"] == "sha256:" + canonical_json_sha256(
        {key: value for key, value in proof.items() if key != "proof_digest"})
    assert len(proof["runs"]) == 4
    for run in proof["runs"]:
        value = "".join(source_texts[run["text_index"]].itertext())[run["start"]:run["end"]]
        assert run["codepoints"] == list(map(ord, value))
        assert run["weight"] == run["requested_weight"] == weight
        assert run["face_weight"] == 400 and run["synthetic_bold"] == (weight >= 600)
    family, face_weight, digest, payload = fallback_font_material()
    assert (family, face_weight) == ("Segoe UI Symbol", 400)
    assert sha256_prefixed(payload) == digest == proof["fallback_font_digest"]
    faces = template_font_faces(source)
    assert [(f, w, d) for f, w, d, _ in faces] == [
        ("Noto Sans KR", "100 900", FONT_DIGEST), (family, 400, digest)]
    plan = trusted_font_fallback_plan("가\u25b8나", weight=weight)
    assert "".join(segment["text"] for segment in plan["segments"]) == "가\u25b8나"
    assert [segment["family"] for segment in plan["segments"]] == ["Noto Sans KR", family, "Noto Sans KR"]


def test_trusted_template_fallback_keeps_plain_pages_and_rejects_untrusted_sources():
    from utils.svg_template_fonts import materialize_template_fallbacks, require_font_coverage, template_font_faces

    source = _svg('<text id="source" x="40" y="80" font-family="Noto Sans KR">원문 125%</text>')
    derived, proof = materialize_template_fallbacks(source)
    assert derived is source and proof is None
    assert len(template_font_faces(source)) == 1
    with pytest.raises(ValueError, match="svg_template_font_glyph_unavailable"):
        require_font_coverage("\U0010ffff")
    for family in ("Arial", "Segoe UI Symbol", "C:/Windows/Fonts/seguisym.ttf", "Noto Sans KR, Segoe UI Symbol"):
        with pytest.raises(ValueError, match="svg_template_font_unsupported"):
            materialize_template_fallbacks(source.replace("Noto Sans KR", family))
    with pytest.raises(ValueError, match="svg_template_font_unsupported"):
        materialize_template_fallbacks(source.replace("원문 125%", '<tspan font-family="Segoe UI Symbol">\u25b8</tspan>'))


def test_trusted_template_fallback_registry_keeps_license_and_embedding_checks():
    from io import BytesIO
    from fontTools.ttLib import TTFont
    from services.authored_vnext.semantic.typography import NativeFontByteRegistry
    from utils.svg_template_fonts import fallback_font_material

    family, _, _, content = fallback_font_material()
    registry = NativeFontByteRegistry()
    receipt = registry.register(content)
    assert receipt.embedding_eligible and receipt.license_policy == "microsoft-windows-font-eula"
    assert registry.font_bytes(family, "Normal") == content
    for mutation in ("restricted", "license_missing"):
        with TTFont(BytesIO(content)) as font:
            if mutation == "restricted":
                font["OS/2"].fsType = 2
            else:
                font["name"].names = [entry for entry in font["name"].names if entry.nameID not in {13, 14}]
            buffer = BytesIO()
            font.save(buffer)
        rejected = NativeFontByteRegistry()
        assert not rejected.register(buffer.getvalue()).embedding_eligible
        with pytest.raises(ValueError, match="authored_native_font_material_unavailable"):
            rejected.font_bytes(family, "Normal")


def test_trusted_template_fallback_advances_use_the_same_bytes_and_do_not_guess_faux_bold():
    from utils.svg_template_fonts import FALLBACK_FONT_FAMILY

    glyph = text_advance("\u25b8", 32, "400", FALLBACK_FONT_FAMILY)
    assert glyph is not None and glyph > 0
    assert text_advance("한\u25b8글", 32, "400", NOTO) == pytest.approx(
        text_advance("한", 32, "400", NOTO) + glyph + text_advance("글", 32, "400", NOTO))
    assert text_advance("한\u25b8글", 32, "700", NOTO) is None
    assert text_advance("\u25b8", 32, "700", FALLBACK_FONT_FAMILY) is None


@pytest.mark.parametrize("anchor,algn", [("start", "l"), ("middle", "ctr"), ("end", "r")])
def test_single_line_frame_holds_its_text_and_keeps_the_anchor(tmp_path, anchor, algn):
    x = 400.0
    source, target = tmp_path / "slide.svg", tmp_path / "deck.pptx"
    source.write_text(_svg(_label("68%", x, 300, anchor)), encoding="utf-8")
    convert_pptx([source], target, [""], canvas_metadata_for_aspect_ratio("16:9"))
    off_x, cx, wrap, frame_algn = _frames(target)["68%"]
    assert (wrap, frame_algn) == ("none", algn)
    assert cx / EMU_PER_PX >= text_advance("68%", 32, "700", NOTO)
    pad = 32 * 0.04  # the converter's per-side frame padding at this size
    anchor_emu = {"start": off_x + pad * EMU_PER_PX, "middle": off_x + cx / 2,
                  "end": off_x + cx - pad * EMU_PER_PX}[anchor]
    assert anchor_emu == pytest.approx(x * EMU_PER_PX, abs=2)


def test_measurer_only_widens_frames_the_estimate_made_too_narrow(tmp_path):
    body = _label("68%", 100, 100, "start") + _label("평균 5일", 100, 200, "start")
    estimated = _frames(_convert_vendor(tmp_path, "estimated", _svg(body), measured=False, merge_paragraphs=False))
    measured = _frames(_convert_vendor(tmp_path, "measured", _svg(body), measured=True, merge_paragraphs=False))
    # Grown to the true advance where the per-character table undercounts "%".
    assert estimated["68%"][1] < text_advance("68%", 32, "700", NOTO) * EMU_PER_PX < measured["68%"][1]
    # Hangul is already over-estimated: the frame is left exactly as it was.
    assert measured["평균 5일"] == estimated["평균 5일"]


def test_unmeasurable_family_keeps_the_estimate(tmp_path):
    body = _label("68%", 100, 100, "start", family="Arial")
    estimated = _frames(_convert_vendor(tmp_path, "estimated", _svg(body), measured=False, merge_paragraphs=False))
    measured = _frames(_convert_vendor(tmp_path, "measured", _svg(body), measured=True, merge_paragraphs=False))
    assert measured == estimated


def test_paragraph_frames_keep_their_wrapping_width(tmp_path):
    paragraph = (
        f'<text x="100" y="100" font-family="{NOTO}" font-size="20" data-paragraph-line-height="30">'
        '<tspan x="100" dy="0">가동률 68% 성능률 80% 양품률</tspan>'
        '<tspan x="100" dy="30">동일 기준으로 도입 전후 변화를 비교</tspan></text>'
    )
    estimated = _frames(_convert_vendor(tmp_path, "estimated", _svg(paragraph), measured=False,
                                       merge_paragraphs=True))
    measured = _frames(_convert_vendor(tmp_path, "measured", _svg(paragraph), measured=True,
                                      merge_paragraphs=True))
    assert measured == estimated
    assert {wrap for _x, _cx, wrap, _algn in measured.values()} == {"square"}
