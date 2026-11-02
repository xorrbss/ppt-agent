"""Rescue pages in a deck with a common header carry that page's header binding.

A real Claude run lost a whole deck here: one page exhausted its attempts, the
placeholder rescue page was finalized without its header binding, and
finalize_svg raised svg_common_header_digest_mismatch.
"""

from xml.etree import ElementTree as ET

from models.presentation_outline_model import PresentationOutlineModel, SlideOutlineModel
from services.generation_core.drivers.svg.fallback import fallback_page_svg, placeholder_page_svg
from services.svg_common_header import GROUP_ID, prepare_common_header

SVG_NS = "{http://www.w3.org/2000/svg}"


def _deck():
    snapshot = {"canvas": {"width": 1280, "height": 720}}
    outline = PresentationOutlineModel(slides=[
        SlideOutlineModel(content="# 도입 단계\n- 진단\n- 시범 적용", title="도입 단계"),
        SlideOutlineModel(content="# 기대 효과\n- 불량률 감소", title="기대 효과"),
    ])
    header, bindings = prepare_common_header(snapshot, outline, snapshot["canvas"])
    return {**snapshot, "common_header": header}, bindings, header


def _texts(svg):
    root = ET.fromstring(svg)
    header = next(node for node in root.iter() if node.get("id") == GROUP_ID)
    header_nodes = set(header.iter())
    body = [node for node in root.iter(f"{SVG_NS}text") if node not in header_nodes]
    return header, body


def test_placeholder_page_keeps_the_header_and_starts_below_it():
    snapshot, bindings, header = _deck()
    svg = placeholder_page_svg(title="기대 효과", width=1280, height=720, font_family="Noto Sans KR",
                               index=1, count=2, style_snapshot=snapshot, header_page=bindings[1])
    found, body = _texts(svg)
    assert found is not None
    assert body and all(float(node.get("y")) > header["content_top_px"] for node in body)


def test_fallback_page_keeps_the_header_and_starts_below_it():
    snapshot, bindings, header = _deck()
    svg = fallback_page_svg(content="# 도입 단계\n- 진단\n- 시범 적용", role="content", index=0, count=2,
                            width=1280, height=720, style_snapshot=snapshot, header_page=bindings[0])
    _found, body = _texts(svg)
    assert body and all(float(node.get("y")) > header["content_top_px"] for node in body)
