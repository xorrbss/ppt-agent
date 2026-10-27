"""Policy-local repairs precede measurement, correction and publication."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid
import xml.etree.ElementTree as ET

import pytest

from services.generation_core.drivers.svg import collect, publish, render
from services.ppt_writing_visible import assess_visible_nodes, svg_text_nodes
from utils.hashing import sha256_hex
from test_svg_terminal_rescue import _AUTHORED, _Stores, _ctx, _renderer, _request


@pytest.mark.parametrize("symbol", list("·ㆍ‧∙⋅"))
def test_korean_list_substitution_passes_policy_without_touching_attributes(symbol):
    from services.ppt_writing_visible import substitute_svg_visible_symbols

    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" id="기획{symbol}운영">'
           f'<text data-url="https://example.org/기획{symbol}운영">기획{symbol}운영</text></svg>')
    repaired, receipt = substitute_svg_visible_symbols(svg)
    assert svg_text_nodes(repaired) == ("기획, 운영",)
    assert not assess_visible_nodes(svg_text_nodes(repaired))["hard_violation_counts"]
    assert [node.attrib for node in ET.fromstring(repaired).iter()] == [
        node.attrib for node in ET.fromstring(svg).iter()]
    assert receipt == [{"symbol": symbol, "count": 1, "replacement": ", "}]
    assert substitute_svg_visible_symbols(repaired) == (repaired, [])


@pytest.mark.parametrize("protection", ["required", "source", "element"])
def test_split_literal_is_protected_structurally(protection):
    from services.ppt_writing_visible import substitute_svg_visible_symbols

    svg = '<svg><text id="literal">기획<tspan>·</tspan>운영</text><text>설계·개발</text></svg>'
    kwargs = {"required_text": ["기획·운영"]} if protection == "required" else (
        {"source_values": ["기획·운영"]} if protection == "source" else {"protected_element_ids": ["literal"]})
    repaired, receipt = substitute_svg_visible_symbols(svg, **kwargs)
    assert svg_text_nodes(repaired) == ("기획·운영", "설계, 개발")
    assert receipt == [{"symbol": "·", "count": 1, "replacement": ", "}]


@pytest.mark.parametrize("text", [
    "3·5", "N·m", "kg⋅m", "x⋅y", "https://example.org/기획·운영", "10만·20만",
    "기획—운영", "기획|운영", "기획★운영", "alpha·beta",
])
def test_semantic_or_ambiguous_symbols_are_not_rewritten(text):
    from services.ppt_writing_visible import substitute_svg_visible_symbols

    svg = f"<svg><text>{text}</text></svg>"
    assert substitute_svg_visible_symbols(svg) == (svg, [])


def test_hidden_metadata_and_paths_remain_unchanged():
    from services.ppt_writing_visible import substitute_svg_visible_symbols

    svg = ('<svg><defs><text>설계·개발</text></defs><title>설계·개발</title>'
           '<g display="none"><text>설계·개발</text></g>'
           '<text visibility="hidden">설계·개발</text>'
           '<text>설계<tspan display="none">·개발</tspan></text>'
           '<path id="설계·개발" d="M0 0 L10 10"/></svg>')
    assert substitute_svg_visible_symbols(svg) == (svg, [])


def test_spaced_middle_dots_across_runs_are_counted_and_normalized():
    from services.ppt_writing_visible import substitute_svg_visible_symbols

    svg = '<svg><text>설계 <tspan>·</tspan> 개발 · 운영</text></svg>'
    repaired, receipt = substitute_svg_visible_symbols(svg)
    assert svg_text_nodes(repaired) == ("설계, 개발, 운영",)
    assert receipt == [{"symbol": "·", "count": 2, "replacement": ", "}]
    assert not assess_visible_nodes(svg_text_nodes(repaired))["hard_violation_counts"]


def test_required_literal_trailing_whitespace_is_not_normalized():
    from services.ppt_writing_visible import substitute_svg_visible_symbols

    svg = '<svg><text>설계 · 개발</text></svg>'
    assert substitute_svg_visible_symbols(svg, required_text=["설계 "]) == (svg, [])


def _page_flow(monkeypatch, text, *, required=()):
    stores, ctx, request = _Stores(), _ctx(count=1), _request()
    request.writing_policy = "ppt-writing-policy/v1"
    renderer = _renderer(monkeypatch)
    if required:
        monkeypatch.setattr(render, "_required_numeric_claims", lambda *args, **kwargs: list(required))

    async def author(**kwargs):
        payload, ordinal = kwargs["payload"], kwargs["ordinal"]
        svg = _AUTHORED.replace("authored", text if ordinal == 0 else "설계, 개발")
        payload["attempts"].append({"kind": "provider", "receipt": {}})
        payload["svg_content"] = svg
        return SimpleNamespace(svg=svg, retry=False)

    provider = AsyncMock(side_effect=author)
    monkeypatch.setattr(collect.author, "attempt_author", provider)
    payload = {"attempts": [], "input_digest": "test-input"}
    hooks = SimpleNamespace(checkpoint=stores.checkpoint, write_png=stores.write_png,
                            read_png=stores.read_png, async_session_maker=None)
    result = asyncio.run(collect._attempt_loop(ctx, request, None, 0, text, payload, None,
                                              SimpleNamespace(fatal_error=None), hooks))
    return result, provider, renderer, stores


def test_page_publishes_with_zero_corrections_and_substitution_receipt(monkeypatch):
    from models.sql.presentation import PresentationModel
    from services.generation_core.drivers.svg.quality import reclassify_pages
    from test_svg_page_quality_publication import _Session, _outline, _published, _storage
    from test_svg_page_quality_publication import _request as publication_request

    page, provider, renderer, stores = _page_flow(monkeypatch, "설계·개발")
    payload, png = page
    assert provider.await_count == 1  # The only author call: zero LLM corrections.
    assert renderer.await_count == 1
    assert payload["quality"]["passed"] is True
    receipt = payload["quality"]["writing_policy"]["local_substitution"]
    assert receipt["substitutions"] == [{"symbol": "·", "count": 1, "replacement": ", "}]
    assert receipt["output_digest"] == payload["source_digest"] == sha256_hex(payload["svg_content"].encode())
    assert payload["quality"]["source_digest"] == "sha256:" + payload["source_digest"]
    assert png == b"png:" + payload["svg_content"].encode()
    assert stores.saved[0]["quality"]["writing_policy"]["local_substitution"] == receipt
    owner, presentation_id = uuid.uuid4(), uuid.uuid4()
    monkeypatch.setattr("utils.owner_context.get_current_owner_id", lambda: owner)
    _storage(monkeypatch)
    session = _Session(PresentationModel(id=presentation_id, owner_id=owner, content="deck", n_slides=1,
                                         mode="svg", revision=0))
    summary = reclassify_pages([page])
    asyncio.run(publish._publish(publication_request(), presentation_id, _outline(1), "ko", session,
                                None, {}, ["content"], [page], quality_summary=summary))
    theme, slides = _published(session)
    assert theme["qualityStatus"] == "passed"
    assert slides[0].content["svgReceipt"]["quality"]["writing_policy"]["local_substitution"] == receipt


def test_non_substitutable_symbol_still_requests_correction(monkeypatch):
    page, provider, renderer, _ = _page_flow(monkeypatch, "설계—개발")
    assert provider.await_count == renderer.await_count == 2
    first = page[0]["attempts"][0]["quality"]
    assert first["passed"] is False
    assert first["writing_policy"]["hard_violation_counts"] == {"long_dash": 1}
    assert any(failure["kind"] == "writing_policy_visible_symbols" for failure in first["failures"])
    assert "local_substitution" not in page[0]["quality"]["writing_policy"]


def test_required_literal_reaches_renderer_unchanged_without_correction(monkeypatch):
    page, provider, renderer, _ = _page_flow(monkeypatch, "설계·개발", required=["설계·개발"])
    assert provider.await_count == 1
    assert "설계·개발" in renderer.await_args.args[0]
    assert renderer.await_args.kwargs["required_text"] == ["설계·개발"]
    assert "local_substitution" not in page[0]["quality"]["writing_policy"]
    assert page[0]["quality"]["writing_policy"]["protected_symbol_counts"] == {"middle_dot": 1}
