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
