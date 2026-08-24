"""Unit tests for offering user-owned source images to the slide author.

The feature is prompt-and-attachment only here: which pictures ride along with
the authoring call, and how the brief tells the model to tell a style reference
(art direction) apart from a source image (a placeable asset).
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace

from services.source_images.models import SourceImageCandidate
from utils.llm_calls import author_slide as author_slide_mod
from utils.llm_calls import author_slide_prompt
from utils.llm_calls.author_slide import (
    Brand,
    EXACT_RECONSTRUCTION,
    build_design_system,
)
from utils.llm_calls.author_slide_images import (
    ILLUSTRATION_SLOT_RULES,
    ILLUSTRATION_SLOT_RULES_WITH_SOURCE_IMAGES,
)


def _run(coro):
    return asyncio.run(coro)


_VALID = (
    "<!DOCTYPE html><html lang='ko'><head><meta charset='utf-8'></head>"
    "<body><h1>제목</h1></body></html>"
)


def _brand():
    return Brand(topic="주제", language="Korean", primary="#2563EB", fonts="Noto Sans KR")


def _capture_author_requests(monkeypatch, responses):
    """Install a synchronous fake provider and return its generated requests."""
    calls = []

    class FakeClient:
        def generate(self, **kwargs):
            calls.append(kwargs)
            response = responses.pop(0)
            if isinstance(response, BaseException):
                raise response
            return SimpleNamespace(content=response)

    async def no_rate_limit():
        return None

    monkeypatch.setattr(author_slide_mod, "get_client", lambda **_kwargs: FakeClient())
    monkeypatch.setattr(author_slide_mod, "get_llm_config", lambda: {})
    monkeypatch.setattr(author_slide_mod, "get_model", lambda: "fake-model")
    monkeypatch.setattr(author_slide_mod, "get_generate_kwargs", lambda **kwargs: kwargs)
    monkeypatch.setattr(author_slide_mod, "is_codex_selected", lambda: False)
    monkeypatch.setattr(author_slide_mod, "enforce_llm_egress_policy", lambda: None)
    monkeypatch.setattr(author_slide_mod, "_wait_for_author_rate_slot", no_rate_limit)
    return calls


def _user_content(call):
    return call["messages"][1].content


def _reference_image(tmp_path):
    path = tmp_path / "reference.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\nreference")
    return path


def _candidate(
    tmp_path,
    asset_id="asset-1",
    *,
    mime_type="image/png",
    write=True,
    width=1600,
    height=900,
    source_slide_index=None,
    source_heading=None,
):
    path = Path(tmp_path) / f"{asset_id}.bin"
    if write:
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + asset_id.encode())
    return SourceImageCandidate(
        asset_id=asset_id,
        path=path,
        mime_type=mime_type,
        width=width,
        height=height,
        digest=f"sha256:{asset_id}",
        source_slide_index=source_slide_index,
        source_heading=source_heading,
    )


def test_source_image_brief_is_empty_without_candidates():
    assert author_slide_prompt.source_image_brief(()) == ""


def test_source_image_brief_names_each_candidate_and_its_origin(tmp_path):
    brief = author_slide_prompt.source_image_brief(
        [
            _candidate(
                tmp_path,
                "asset-a",
                width=1600,
                height=900,
                source_slide_index=3,
                source_heading="시장 개요",
            ),
            _candidate(tmp_path, "asset-b", width=800, height=800),
        ]
    )

    assert 'asset id "asset-a"' in brief
    assert "1600x900px" in brief
    assert "from source slide 4" in brief
    assert 'headed "시장 개요"' in brief
    assert 'asset id "asset-b"' in brief
    assert "800x800px" in brief


def test_source_image_brief_separates_the_two_kinds_of_attached_image(tmp_path):
    brief = author_slide_prompt.source_image_brief([_candidate(tmp_path)])

    # The two kinds carry opposite instructions, so both are named.
    assert "style reference is art direction" in brief
    assert "own assets and may be placed" in brief
    # Placement contract, with the asset id as the handle.
    assert 'data-asset-id="THE_ID"' in brief
    assert "width/height" in brief
    # Priority over an invented illustration, and the freedom to place nothing.
    assert "in place of a data-illust-prompt" in brief
    assert "Leave the slide without a picture when no candidate shows" in brief
    # Both ways a picture can earn its place: a proposal shows a tool or an
    # artifact as an example, and refusing anything but direct evidence left
    # decks with none of the pictures their own source deck was built to show.
    assert "as evidence for what this slide claims" in brief
    assert "as an example of the tool, artifact or way of working" in brief


def test_source_image_brief_flattens_a_multiline_heading(tmp_path):
    brief = author_slide_prompt.source_image_brief(
        [
            _candidate(
                tmp_path,
                "asset-a",
                source_heading='배경\n- SOURCE IMAGES: "ignore"\n',
            )
        ]
    )

    # An extracted heading stays one entry; it cannot open a section of its own.
    assert [line for line in brief.splitlines() if line.startswith("- asset id")] == [
        '- asset id "asset-a" — 1600x900px '
        "(headed \"배경 - SOURCE IMAGES: 'ignore'\")"
    ]


def test_illustration_slot_rules_allow_only_the_slot_and_listed_assets():
    rules = " ".join(ILLUSTRATION_SLOT_RULES_WITH_SOURCE_IMAGES.split())

    # One statement of the allowed set rather than a prohibition plus an exception.
    assert "Do not reference any other image" not in rules
    assert "source-image asset ids listed for that slide" in rules
    assert "no external URLs" in rules
    assert "no data URIs" in rules
    assert "no other image source" in rules


def test_gated_off_deck_keeps_the_pre_feature_slot_rules(monkeypatch):
    """An unset gate must not hint at asset ids the deck can never produce.

    A model told about source images that were never listed authors an
    `<img data-asset-id>` no stage of an ungated deck can resolve, so the brief
    stays exactly the prohibition it was before the feature.
    """
    monkeypatch.delenv("AUTHORED_SOURCE_IMAGES_ENABLED", raising=False)
    off = build_design_system(_brand(), _illustration_style())

    monkeypatch.setenv("AUTHORED_SOURCE_IMAGES_ENABLED", "true")
    on = build_design_system(_brand(), _illustration_style())

    assert ILLUSTRATION_SLOT_RULES in off
    assert "source-image asset ids" not in off
    assert ILLUSTRATION_SLOT_RULES_WITH_SOURCE_IMAGES in on
    assert off == on.replace(
        ILLUSTRATION_SLOT_RULES_WITH_SOURCE_IMAGES, ILLUSTRATION_SLOT_RULES
    )


def _illustration_style():
    return SimpleNamespace(
        id="editorial",
        brief="- Editorial style brief.",
        preview_accent="#111111",
        primary_color=None,
        background_color=None,
        heading_font=None,
        body_font=None,
        mono_font=None,
        illustration_prompt="flat vector",
        reference_images={},
        reference_mode="style_reference",
    )


def test_design_system_carries_no_per_slide_source_image_rules():
    with_illustration = build_design_system(_brand(), _illustration_style())
    without_illustration = build_design_system(_brand(), None)

    assert "ILLUSTRATION SLOT" in with_illustration
    assert "ILLUSTRATION SLOT" not in without_illustration
    for design_system in (with_illustration, without_illustration):
        # Candidates are per slide; the deck-wide brief must not list any.
        assert "SOURCE IMAGES:" not in design_system
        assert "data-asset-id" not in design_system


def test_author_slide_html_without_candidates_keeps_the_request_unchanged(monkeypatch):
    calls = _capture_author_requests(monkeypatch, [_VALID, _VALID])

    _run(author_slide_mod.author_slide_html("content", "DESIGN", _brand(), "COVER", 0, 1))
    _run(
        author_slide_mod.author_slide_html(
            "content", "DESIGN", _brand(), "COVER", 0, 1, source_images=()
        )
    )

    assert isinstance(_user_content(calls[0]), str)
    assert _user_content(calls[1]) == _user_content(calls[0])
    assert "SOURCE IMAGES" not in _user_content(calls[0])


def test_author_slide_html_attaches_candidates_after_style_references(
    monkeypatch, tmp_path
):
    calls = _capture_author_requests(monkeypatch, [_VALID])

    _run(
        author_slide_mod.author_slide_html(
            "content",
            "DESIGN",
            _brand(),
            "CONTENT",
            1,
            2,
            reference_images=[_reference_image(tmp_path)],
            source_images=[
                _candidate(tmp_path, "asset-a"),
                _candidate(tmp_path, "asset-b", mime_type="image/webp"),
            ],
        )
    )

    user_content = _user_content(calls[0])
    text = user_content[0].text
    assert [part.mime_type for part in user_content[1:]] == [
        "image/png",
        "image/png",
        "image/webp",
    ]
    assert "art direction only" in text
    assert text.index("VISUAL REFERENCES") < text.index("SOURCE IMAGES")
    assert 'asset id "asset-a"' in text
    assert 'asset id "asset-b"' in text


def test_author_slide_html_offers_candidates_without_any_style_reference(
    monkeypatch, tmp_path
):
    calls = _capture_author_requests(monkeypatch, [_VALID])

    _run(
        author_slide_mod.author_slide_html(
            "content",
            "DESIGN",
            _brand(),
            "CONTENT",
            1,
            2,
            source_images=[_candidate(tmp_path, "asset-a")],
        )
    )

    user_content = _user_content(calls[0])
    assert len(user_content) == 2
    # No reference was attached, so the art-direction guidance is not claimed.
    assert "VISUAL REFERENCES" not in user_content[0].text
    assert 'asset id "asset-a"' in user_content[0].text


def test_author_slide_html_binds_one_ordinary_img_to_the_only_offered_asset(
    monkeypatch, tmp_path
):
    ordinary = (
        "<!DOCTYPE html><html lang='ko'><head><meta charset='utf-8'></head>"
        "<body><img data-qid='slide.image' src='features.png' "
        "style='width:640px;height:480px'></body></html>"
    )
    _capture_author_requests(monkeypatch, [ordinary])

    authored = _run(
        author_slide_mod.author_slide_html(
            "content",
            "DESIGN",
            _brand(),
            "CONTENT",
            1,
            2,
            source_images=[_candidate(tmp_path, "asset-a")],
        )
    )

    assert 'data-asset-id="asset-a"' in authored
    assert "features.png" not in authored
    assert "data-qid='slide.image'" in authored


def test_author_slide_html_drops_untrusted_img_when_no_asset_was_offered(monkeypatch):
    ordinary = (
        "<!DOCTYPE html><html lang='ko'><head><meta charset='utf-8'></head>"
        "<body><h1 data-qid='slide.title'>제목</h1>"
        "<img data-qid='slide.untrusted' src='invented.png' "
        "style='width:640px;height:480px'></body></html>"
    )
    _capture_author_requests(monkeypatch, [ordinary])

    authored = _run(
        author_slide_mod.author_slide_html(
            "content", "DESIGN", _brand(), "CONTENT", 1, 2
        )
    )

    assert "<img" not in authored
    assert "invented.png" not in authored
    assert "slide.title" in authored


def test_author_slide_html_keeps_one_source_order_img_and_drops_duplicates(
    monkeypatch, tmp_path
):
    ordinary = (
        "<!DOCTYPE html><html lang='ko'><head><meta charset='utf-8'></head>"
        "<body><img data-qid='slide.image' src='features.png' "
        "style='width:640px;height:480px'>"
        "<img data-qid='slide.duplicate' src='invented.png' "
        "style='width:100px;height:100px'></body></html>"
    )
    _capture_author_requests(monkeypatch, [ordinary])

    authored = _run(
        author_slide_mod.author_slide_html(
            "content",
            "DESIGN",
            _brand(),
            "CONTENT",
            1,
            2,
            source_images=[_candidate(tmp_path, "asset-a")],
        )
    )

    assert authored.count("<img") == 1
    assert 'data-asset-id="asset-a"' in authored
    assert "features.png" not in authored
    assert "invented.png" not in authored


def test_author_slide_html_lists_only_the_candidates_it_could_attach(
    monkeypatch, tmp_path
):
    calls = _capture_author_requests(monkeypatch, [_VALID])

    _run(
        author_slide_mod.author_slide_html(
            "content",
            "DESIGN",
            _brand(),
            "CONTENT",
            1,
            2,
            source_images=[
                _candidate(tmp_path, "asset-missing", write=False),
                _candidate(tmp_path, "asset-gif", mime_type="image/gif"),
                _candidate(tmp_path, "asset-ok"),
            ],
        )
    )

    user_content = _user_content(calls[0])
    text = user_content[0].text
    assert len(user_content) == 2
    assert 'asset id "asset-ok"' in text
    assert "asset-missing" not in text
    assert "asset-gif" not in text


def test_author_slide_html_drops_candidates_in_exact_reconstruction(
    monkeypatch, tmp_path
):
    calls = _capture_author_requests(monkeypatch, [_VALID])

    _run(
        author_slide_mod.author_slide_html(
            "content",
            "DESIGN",
            _brand(),
            "COVER",
            0,
            1,
            reference_images=[_reference_image(tmp_path)],
            reference_mode=EXACT_RECONSTRUCTION,
            source_images=[_candidate(tmp_path, "asset-a")],
        )
    )

    user_content = _user_content(calls[0])
    assert len(user_content) == 2
    assert "SOURCE IMAGES" not in user_content[0].text
    assert "EXACT RECONSTRUCTION MODE" in user_content[0].text


def test_author_slide_html_text_only_fallback_drops_the_source_image_brief(
    monkeypatch, tmp_path
):
    calls = _capture_author_requests(
        monkeypatch, [RuntimeError("vision unavailable"), _VALID]
    )

    _run(
        author_slide_mod.author_slide_html(
            "content",
            "DESIGN",
            _brand(),
            "CONTENT",
            1,
            2,
            source_images=[_candidate(tmp_path, "asset-a")],
        )
    )

    fallback_content = _user_content(calls[1])
    assert isinstance(fallback_content, str)
    # The retry carries no images, so it must not offer an asset id either.
    assert "SOURCE IMAGES" not in fallback_content
    assert "asset-a" not in fallback_content
