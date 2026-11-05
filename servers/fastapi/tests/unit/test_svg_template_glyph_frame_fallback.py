"""A fixed-glyph (v3) template frame rescues an exhausted page instead of failing the deck.

Drives ``terminal.rescue_terminal_page`` with a real ``svg-template-frame/v3``
frame (a glyph authority sealed over a portable source deck's fixed text and
checked by ``validate_frame_glyph_authority``), the real fallback body, the real
page render step (frame composition, glyph materializer, renderer client and
``fixedGlyphPreview`` marker) and the real publication check. Faked are only the
renderer subprocess (it reports the glyph projection the client bound to it and
box metrics read from the SVG), the asset store's screen placement and the
checkpoint/PNG stores.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock
import xml.etree.ElementTree as ET

import pytest

from test_svg_template_native_fixed_text import _portable_source
from models.svg_template_frame import SvgTemplateFrame, SvgTemplatePackage
from models.svg_template_glyph import glyph_authority_digest
from services.authored_checkpoint_errors import AuthoredStageError
from services.generation_core.drivers.svg import render, terminal
from services.generation_core.runtime.profile import resolve_svg_runtime_profile
from services.svg_presentation_prepare import GenerationContext
from services.svg_templates.frame import extract_template_package
from services.svg_templates.frame_composition import adapt_frame
from services.svg_templates.frame_fonts import (
    artifact_digest, bundled_font_assets, bytes_digest, font_validation_scope,
)
from services.svg_templates.frame_glyphs import (
    materializer_code_digest, source_frame_projection_digest, validate_frame_glyph_authority,
)
from services.svg_templates.frame_native_text import A, NS, validated_parts
from services.svg_templates.generation import (
    page_frame, validate_template_publication, validate_template_publication_page,
)
from utils.hashing import canonical_json_digest, sha256_hex

SVG = "http://www.w3.org/2000/svg"
COUNT = 3
CONTENTS = ["# 시장 개요\n- 2026년 판매 120만 대", "# 실행 일정\n- 1분기: 계획 확정\n- 2분기: 핵심 과제 실행",
            "# 맺음말\n- 다음 단계 점검"]


def _glyph_authority(frame):
    """A sealed source-PDF glyph authority over the frame's fixed ASCII texts.

    Each character is a small closed outline at its source position; the
    digests are the ones ``validate_frame_glyph_authority`` recomputes.
    """
    physical = frame.source_canvas.physical_size
    width_pt, height_pt = physical.width_emu / 12700, physical.height_emu / 12700
    objects = []
    for element in (item for item in frame.elements if item.kind == "text"):
        record, _sp, body, _bounds = validated_parts(element.native_fixed_text, frame.source_canvas,
                                                     frame.font_manifest)
        text = "".join(body.findall(".//a:t", NS)[0].itertext())
        color = body.find(".//a:srgbClr", {"a": A}).get("val")
        rgba = (*bytes.fromhex(color), 255)
        size = 12.0
        left = element.bounds.x * width_pt / frame.source_canvas.width_px
        baseline = float(height_pt - (element.bounds.y + element.bounds.height * .7) * height_pt / frame.source_canvas.height_px)
        characters = []
        for offset, char in enumerate(text):
            # Floats throughout: the digest is taken over the JSON the model dumps.
            x = float(left + offset * size * .5)
            path = () if char == " " else ((2, x + 1, baseline + 1, False), (0, x + 4, baseline + 1, False),
                                           (0, x + 4, baseline + 8, False), (0, x + 1, baseline + 8, True))
            characters.append(dict(unicode=ord(char), text_offset=offset, origin_pdf=(x, baseline),
                font_size_pdf=size, matrix_pdf=(1.0, 0.0, 0.0, 1.0, x, baseline), fill_rgba=rgba, path_pdf=path))
        objects.append(dict(source_part=element.source_part, source_shape_id=element.source_shape_id,
            source_xml_digest=element.source_xml_digest, svg_text_id=record.svg_text_id,
            svg_text_digest=record.svg_text_digest, text_digest=record.text_digest,
            effective_clip_rects=((.5, .5, width_pt - .5, height_pt - .5), (0.0, 0.0, width_pt, height_pt)),
            characters=characters))
        font_digest = record.font_digests[0]
    authority = dict(schema_version="svg-template-fixed-glyph-preview/v1",
        source_pptx_digest=frame.source_digest, source_pdf_digest="sha256:" + "1" * 64,
        source_canvas_digest=artifact_digest(frame.source_canvas),
        source_frame_projection_digest=source_frame_projection_digest(frame),
        approved_font_digest=font_digest, embedded_font_digest="sha256:" + "2" * 64,
        capture_receipt_digest="sha256:" + "3" * 64, graphics_state_digest="sha256:" + "4" * 64,
        producer_version="16.0", producer_build="20326", frame_id=frame.frame_id,
        source_page=frame.source_page, physical_canvas_pt=(width_pt, height_pt),
        source_canvas_px=(frame.source_canvas.width_px, frame.source_canvas.height_px),
        materializer_version="svg-fixed-glyph-materializer/v1", materializer_code_digest=materializer_code_digest(),
        text_objects=objects)
    authority["authority_digest"] = glyph_authority_digest(authority)
    return authority


@pytest.fixture
def snapshot(tmp_path):
    assets = tuple(asset for asset in bundled_font_assets() if asset.weight == 400)
    source = tmp_path / "source.pptx"
    _portable_source(source, assets[0].family)
    with font_validation_scope():
        package = extract_template_package(source, font_assets=assets, native_fixed_text_layout=True)
        v2 = package.frames[0]
        data = v2.model_dump(mode="json")
        data.update(schema_version="svg-template-frame/v3", fixed_glyph_preview=_glyph_authority(v2), digest="")
        frame = SvgTemplateFrame.model_validate(data)
        frame = frame.model_copy(update={"digest": artifact_digest(frame)})
        validate_frame_glyph_authority(frame)
        v3 = package.model_copy(update={"schema_version": "svg-template-package/v3",
                                        "compiler_version": "ooxml-frame/3", "frames": (frame,), "digest": ""})
        v3 = SvgTemplatePackage.model_validate(v3.model_dump(mode="json"))
        v3 = v3.model_copy(update={"digest": artifact_digest(v3)})
        adaptation = adapt_frame(frame, frame.source_canvas)
        selection = {"schema_version": "svg-template-selection/v1", "mode": "template-redesign",
                     "package": v3.model_dump(mode="json"),
                     "frame_map": {str(page): frame.frame_id for page in range(1, COUNT + 1)},
                     "adaptations": {frame.frame_id: adaptation.model_dump(mode="json")},
                     "template_pin": {"templateId": "00000000-0000-0000-0000-000000000001",
                                      "revision": 1, "digest": v3.digest}}
        selection["digest"] = canonical_json_digest(selection)
        yield {"svg_template": selection}


# --------------------------------------------------------------------------
# fakes: renderer subprocess, screen placement, stores
# --------------------------------------------------------------------------


def _metrics(svg):
    """Box metrics the browser would report, read from the SVG's own attributes."""
    elements = []
    for index, node in enumerate(ET.fromstring(svg).iter()):
        tag = node.tag.rsplit("}", 1)[-1]
        qid = f"svg-{index}"
        if tag == "text":
            text = "".join(node.itertext())
            size = float(node.get("font-size", "16"))
            x, y = float(node.get("x", "0")), float(node.get("y", "0"))
            width = len(text) * size * .55
            if node.get("text-anchor") == "middle":
                x -= width / 2
            elif node.get("text-anchor") == "end":
                x -= width
            box = {"x": x, "y": y - size * .8, "width": width, "height": size}
            elements.append({"qid": qid, "tag": tag, "has_owned_text": bool(text.strip()), "owned_text": text,
                "font_size": size, "font_loaded": True, "css_visible": True, "effective_opacity": 1,
                "bbox": box, "text_fragments": [box]})
        elif tag == "tspan":
            elements.append({"qid": qid, "tag": tag, "has_owned_text": False, "css_visible": True,
                             "effective_opacity": 1, "bbox": {"x": 0, "y": 0, "width": 0, "height": 0}})
        elif tag in {"rect", "image"}:
            elements.append({"qid": qid, "tag": tag, "css_visible": True, "effective_opacity": 1,
                "bbox": {key: float(node.get(key, "0")) for key in ("x", "y", "width", "height")}})
    return elements


class _Renderer:
    def __init__(self, *, forge_projection=False):
        self.calls, self.forge_projection = [], forge_projection

    async def __call__(self, html, _timeout, **kwargs):
        self.calls.append(kwargs)
        glyph = {}
        if kwargs.get("fixed_glyph_preview_ref") is not None:
            glyph = {"fixedGlyphPreview": {
                "displayDigest": "sha256:" + "f" * 64 if self.forge_projection else kwargs["fixed_glyph_display_digest"],
                "renderDigest": kwargs["fixed_glyph_render_digest"],
                "projectionDigest": kwargs["fixed_glyph_projection_digest"],
                "fontCssDigest": kwargs["fixed_glyph_font_css_digest"],
                "authorityDigest": kwargs["fixed_glyph_authority_digest"],
                "materializerCodeDigest": kwargs["fixed_glyph_code_digest"]}}
        png = b"\x89PNG-test:" + hashlib.sha256(html.encode()).digest()
        return png, {"fonts_ready": True, "elements": _metrics(kwargs["canonical_svg"]), **glyph}


class _Placements:
    """The asset store's screen placement: one sealed pointer per composed SVG."""

    def __init__(self):
        self.svgs = []

    async def __call__(self, _session, _service, *, svg, page_index, revision, saved_envelope=None, **_kwargs):
        self.svgs.append(svg)
        pointer = sha256_hex(f"{page_index}:{sha256_hex(svg.encode())}".encode())
        return ({"schemaVersion": "svg-slide-assets/v1", "screenPlacement": {"sha256": pointer},
                 "sourcePageIndex": page_index, "slideRevision": revision}, svg)


class _Session:
    async def __aenter__(self):
        return SimpleNamespace(commit=AsyncMock())

    async def __aexit__(self, *_args):
        return False


class _Stores:
    def __init__(self):
        self.saved, self.pngs, self.writes = {}, {}, []

    async def checkpoint(self, _job, index, *, payload=None):
        if payload is not None:
            self.saved[index] = deepcopy(payload)
            self.writes.append(index)
        return self.saved.get(index, {})

    def write_png(self, png):
        digest = sha256_hex(png)
        self.pngs[digest] = png
        return {"png_sha256": digest, "png_path": f"svg/{digest}.png"}


@pytest.fixture
def browser(monkeypatch):
    renderer, placements = _Renderer(), _Placements()
    monkeypatch.setattr("utils.slide_capture_worker_client._render_html_in_isolated_worker_impl", renderer)
    monkeypatch.setattr("services.svg_generation_assets.screen_placement", placements)
    return renderer, placements


def _ctx(snapshot):
    return GenerationContext(
        source_reflow=False, raster_reconstruction=False, raster_analysis=None, raster_pin=None,
        source_document=None, source_units=None, canonical_assets=True,
        canvas=SimpleNamespace(width_px=1280, height_px=720), physical_kwargs={}, snapshot=snapshot,
        source_font_profile=None, count=COUNT, roles=["content"] * COUNT, manifests=[{}] * COUNT,
        planned=[{}] * COUNT, source_manifest=SimpleNamespace(digest="sha256:" + "5" * 64),
        source_pointer={"ref": "source-manifest"}, asset_context=SimpleNamespace(), asset_revision=1,
        service=None, semaphore=asyncio.Semaphore(COUNT), render_semaphore=asyncio.Semaphore(COUNT),
        runtime_profile=resolve_svg_runtime_profile(source_reflow=False))


def _request():
    return SimpleNamespace(instructions=None, _document_canvas=None, writing_policy=None, strategy="svg",
                           aspect_ratio="16:9", content="deck")


def _authored_page(ctx, stores, index):
    """A page the author drew, measured by the normal page render step."""
    body = (f'<svg xmlns="{SVG}" width="1280" height="720" viewBox="0 0 1280 720">'
            f'<text id="body-title" x="120" y="200" font-family="Pretendard" font-weight="400" font-size="32" '
            f'fill="#202020">{CONTENTS[index].splitlines()[0][2:]}</text>'
            f'<text id="body-line" x="120" y="300" font-family="Pretendard" font-weight="400" font-size="24" '
            f'fill="#202020">{CONTENTS[index].splitlines()[1][2:]}</text></svg>')
    payload = {"schema_version": "svg-slide-checkpoint/v2", "source_format": "svg", "input_digest": f"in-{index}",
               "attempts": [{"kind": "provider", "receipt": {}}]}
    return asyncio.run(render.attempt_render(ctx=ctx, request=_request(), presentation_id=None, index=index,
        content=CONTENTS[index], payload=payload, svg=body, ordinal=1, job=None, checkpoint=stores.checkpoint,
        async_session_maker=_Session, write_png=stores.write_png))


def _exhausted(index):
    """The checkpoint of a page whose author attempts all failed validation."""
    return {"schema_version": "svg-slide-checkpoint/v2", "source_format": "svg", "input_digest": f"in-{index}",
            "attempts": [{"kind": "provider", "receipt": {"validation_error": "svg_contract_invalid"}}] * 3,
            "svg_content": None}


def _rescue(ctx, stores, index):
    payload = _exhausted(index)
    stores.saved[index] = deepcopy(payload)
    error = AuthoredStageError(slide_index=index, stage="render", code="svg_repair_budget_exhausted",
                               retryable=False)
    return payload, asyncio.run(terminal.rescue_terminal_page(
        ctx=ctx, request=_request(), index=index, content=CONTENTS[index], payload=payload, error=error,
        job=None, checkpoint=stores.checkpoint, write_png=stores.write_png, read_png=lambda _payload: None,
        presentation_id=None, async_session_maker=_Session))


# --------------------------------------------------------------------------
# tests
# --------------------------------------------------------------------------


def test_an_exhausted_page_of_a_glyph_frame_deck_publishes_as_a_validated_fallback(snapshot, browser):
    renderer, placements = browser
    ctx, stores = _ctx(snapshot), _Stores()
    with font_validation_scope():
        first, last = _authored_page(ctx, stores, 0), _authored_page(ctx, stores, 2)
        before = deepcopy([first, last])
        _original, rescued = _rescue(ctx, stores, 1)
        assert rescued is not None
        page, png = rescued

        # A flagged substitute that still carries everything a v3 page publishes with.
        assert page["terminal"] == {"index": 1, "reason": "author:attempts_exhausted",
                                    "remakeAttempted": False, "remakeOutcome": "svg_template_fallback_page"}
        assert page["terminal_error"] == "svg_repair_budget_exhausted"
        assert page["attempts"][:3] == _exhausted(1)["attempts"]
        assert page["attempts"][-1]["kind"] == "local"
        assert page["attempts"][-1]["receipt"]["template_fallback"] == terminal.TEMPLATE_GLYPH_FALLBACK_VERSION
        root = ET.fromstring(page["svg_content"])
        assert 'id="body-fallback"' in page["svg_content"]
        shown = " ".join("".join(node.itertext()) for node in root.iter(f"{{{SVG}}}text"))
        assert shown.count("실행 일정") == 1  # the frame shows no title: the body draws it once
        assert "1분기: 계획 확정" in shown and "2분기: 핵심 과제 실행" in shown
        assert "Fixed header" in shown and "Fixed footer" in shown  # the frame's own fixed text
        marker = page["fixedGlyphPreview"]
        frame = page_frame(snapshot, 1, COUNT)
        assert marker["authorityDigest"] == frame.fixed_glyph_preview.authority_digest
        assert marker["materializerCodeDigest"] == materializer_code_digest()
        assert marker["pngDigest"] == bytes_digest(png) == "sha256:" + page["png_sha256"]
        assert page["quality"]["template_preservation"]["body_paint_measured"] is True
        # The page render step placed and captured exactly the composed fallback page.
        assert placements.svgs[-1] == page["svg_content"]
        assert renderer.calls[-1]["fixed_glyph_preview_ref"] is not None
        assert renderer.calls[-1]["canonical_svg"] == page["svg_content"]
        assert stores.saved[1] == page

        # The deck's publication check accepts every page; the others are untouched.
        assert [first, last] == before
        pages = [first, (page, png), last]
        outline = SimpleNamespace(slides=[SimpleNamespace(content=content) for content in CONTENTS])
        validate_template_publication(snapshot, pages, outline)
        assert not first[0].get("terminal") and not last[0].get("terminal")


def test_publication_still_rejects_a_forged_fallback_marker(snapshot, browser):
    ctx, stores = _ctx(snapshot), _Stores()
    with font_validation_scope():
        _original, (page, png) = _rescue(ctx, stores, 1)
        check = dict(index=1, count=COUNT, content=CONTENTS[1])
        validate_template_publication_page(snapshot, page, **check)
        for key, value in (("authorityDigest", "sha256:" + "0" * 64), ("pngDigest", "sha256:" + "0" * 64),
                           ("screenPlacementDigest", "0" * 64), ("materializerCodeDigest", "sha256:" + "0" * 64)):
            forged = deepcopy(page)
            forged["fixedGlyphPreview"][key] = value
            with pytest.raises(ValueError, match="identity_mismatch"):
                validate_template_publication_page(snapshot, forged, **check)
        missing = deepcopy(page)
        missing.pop("fixedGlyphPreview")
        with pytest.raises(ValueError, match="identity_mismatch"):
            validate_template_publication_page(snapshot, missing, **check)
        outline = SimpleNamespace(slides=[SimpleNamespace(content=content) for content in CONTENTS])
        first, last = _authored_page(ctx, stores, 0), _authored_page(ctx, stores, 2)
        with pytest.raises(ValueError, match="png_mismatch"):
            validate_template_publication(snapshot, [first, (page, png + b"forged"), last], outline)


def test_a_capture_that_does_not_bind_the_glyph_projection_keeps_failing_the_deck(snapshot, monkeypatch):
    renderer = _Renderer(forge_projection=True)
    monkeypatch.setattr("utils.slide_capture_worker_client._render_html_in_isolated_worker_impl", renderer)
    monkeypatch.setattr("services.svg_generation_assets.screen_placement", _Placements())
    ctx, stores = _ctx(snapshot), _Stores()
    with font_validation_scope():
        original, rescued = _rescue(ctx, stores, 1)
    assert rescued is None
    assert renderer.calls  # the fallback was rendered, and its capture refused
    assert stores.saved[1] == original  # the page's checkpoint is as it was before the rescue


def test_an_integrity_error_of_the_render_step_is_not_hidden_by_the_rescue(snapshot, monkeypatch):
    async def lost_fence(*_args, **_kwargs):
        raise ValueError("svg_asset_publication_fence_lost")

    monkeypatch.setattr("services.svg_generation_assets.screen_placement", lost_fence)
    ctx, stores = _ctx(snapshot), _Stores()
    with font_validation_scope(), pytest.raises(AuthoredStageError) as raised:
        _rescue(ctx, stores, 1)
    assert raised.value.code == "svg_asset_screen_derivative_failed"

