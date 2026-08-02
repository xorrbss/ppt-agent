"""Unit tests for the authored generation pipeline (no real LLM / no real Chrome).

Covers the resilience guarantees that keep authored generation from aborting on one
bad slide: HTML validity gating, branded fallback, per-slide author retry/fallback,
per-slide render placeholder, style-brief propagation, image-PPTX assembly, the
authored-deck predicate, and brand resolution."""

import asyncio
import hashlib
import io
import uuid
from types import SimpleNamespace

import pytest
from PIL import Image

from utils.owner_context import reset_current_owner_id, set_current_owner_id
from models.generate_presentation_request import GeneratePresentationRequest
from models.presentation_outline_model import (
    PresentationOutlineModel,
    SlideOutlineModel,
)
from models.sql.presentation import PresentationModel
from services import authored_presentation_service as authored_service
from services.authored_presentation_service import resolve_brand
from utils import slide_capture
from utils.llm_calls import author_deck as author_deck_mod
from utils.llm_calls import author_slide as author_slide_mod
from utils.llm_calls import author_vision_qa as author_vision_qa_mod
from utils.llm_calls import critique_slide as critique_slide_mod
from utils.llm_calls.author_deck import (
    AuthoredDeckResult,
    author_deck,
    build_image_pptx,
    plan_deck_roles,
)
from utils.llm_calls.author_slide import (
    Brand,
    EXACT_RECONSTRUCTION,
    STYLE_REFERENCE,
    apply_style_defaults,
    build_design_system,
    fallback_slide_html,
    is_fallback_slide_html,
    is_valid_slide_html,
)
from utils.llm_calls.critique_slide import SlideCritique


def _run(coro):
    return asyncio.run(coro)


_VALID = (
    "<!DOCTYPE html><html lang='ko'><head><meta charset='utf-8'></head>"
    "<body><h1>제목</h1></body></html>"
)


def _outline(n):
    return PresentationOutlineModel(
        slides=[SlideOutlineModel(content=f"슬라이드 {i} 내용") for i in range(n)]
    )


def _brand():
    return Brand(topic="주제", language="Korean", primary="#2563EB", fonts="Noto Sans KR")


def test_save_slide_pngs_keeps_owner_namespace_in_browser_reference(
    monkeypatch, tmp_path
):
    owner_id = uuid.uuid4()
    presentation_id = uuid.uuid4()
    app_data = tmp_path / "app_data"
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))

    owner_token = set_current_owner_id(owner_id)
    try:
        refs = authored_service._save_slide_pngs(presentation_id, [b"png"])
    finally:
        reset_current_owner_id(owner_token)

    image_set = authored_service._authored_image_set_digest([b"png"])
    content_hash = hashlib.sha256(b"png").hexdigest()
    expected = (
        f"users/{owner_id}/authored/{presentation_id}/sets/{image_set}/"
        f"slide_0-{content_hash}.png"
    )
    assert refs == [expected]
    assert (app_data / "images" / expected).read_bytes() == b"png"


def test_save_slide_pngs_never_overwrites_another_publication_set(
    monkeypatch,
    tmp_path,
):
    owner_id = uuid.uuid4()
    presentation_id = uuid.uuid4()
    app_data = tmp_path / "app_data"
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))

    owner_token = set_current_owner_id(owner_id)
    try:
        first = authored_service._save_slide_pngs(
            presentation_id,
            [b"first revision"],
            "operation-a:attempt-a",
        )
        second = authored_service._save_slide_pngs(
            presentation_id,
            [b"second revision"],
            "operation-b:attempt-b",
        )
        first_retry = authored_service._save_slide_pngs(
            presentation_id,
            [b"first revision"],
            "operation-a:attempt-a",
        )
    finally:
        reset_current_owner_id(owner_token)

    assert first != second
    assert first_retry == first
    assert (app_data / "images" / first[0]).read_bytes() == b"first revision"
    assert (app_data / "images" / second[0]).read_bytes() == b"second revision"


# --- HTML validity + fallback ------------------------------------------------


def test_is_valid_slide_html_accepts_full_document():
    assert is_valid_slide_html(_VALID) is True


def test_is_valid_slide_html_rejects_garbage():
    assert is_valid_slide_html("") is False
    assert is_valid_slide_html("   ") is False
    assert is_valid_slide_html("not html") is False
    assert is_valid_slide_html("<html><head></head></html>") is False  # no body


def test_fallback_slide_html_is_valid_and_contains_content():
    html = fallback_slide_html("핵심 메시지 텍스트", _brand(), "COVER", 0, 5)
    assert is_valid_slide_html(html)
    assert is_fallback_slide_html(html)
    assert "핵심 메시지 텍스트" in html
    assert "COVER" in html
    assert "#2563EB" in html


def test_fallback_slide_html_escapes_angle_brackets():
    html = fallback_slide_html("<script>x</script>", _brand(), "PROBLEM", 1, 3)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_fallback_cover_omits_page_number():
    html = fallback_slide_html("cover", _brand(), "COVER", 0, 5)

    assert 'class="foot"' not in html


def test_fallback_slide_html_uses_bottom_center_number_after_cover():
    html = fallback_slide_html("content", _brand(), "PROBLEM", 1, 5)

    assert '<div class="foot">01</div>' in html
    assert "left:0;right:0;bottom:14px;text-align:center" in html
    assert "line-height:16px;font-weight:500;color:#A7ACB3" in html
    assert "01 / 05" not in html


def test_fallback_slide_html_uses_offline_safe_logical_font_stack():
    html = fallback_slide_html("본문", _brand(), "PROBLEM", 1, 3)

    assert 'font-family:"Noto Sans KR","Malgun Gothic",sans-serif' in html
    assert "fonts.googleapis.com" not in html
    assert "fonts.gstatic.com" not in html
    assert "@import" not in html


# --- author_deck resilience (mocked LLM) -------------------------------------


def test_author_deck_uses_authored_html_when_valid(monkeypatch):
    async def fake(content, ds, brand, role, index, n):
        return _VALID

    monkeypatch.setattr(author_deck_mod, "author_slide_html", fake)
    htmls = _run(author_deck(_outline(4), _brand())).htmls
    assert len(htmls) == 4
    assert all(h == _VALID for h in htmls)


def test_author_deck_falls_back_when_authoring_raises(monkeypatch):
    async def boom(content, ds, brand, role, index, n):
        raise RuntimeError("provider down")

    monkeypatch.setattr(author_deck_mod, "author_slide_html", boom)
    htmls = _run(author_deck(_outline(3), _brand())).htmls
    assert len(htmls) == 3
    # Every slide degraded to a valid branded fallback (deck still completes).
    assert all(is_valid_slide_html(h) for h in htmls)
    assert any("슬라이드 0 내용" in h for h in htmls)


def test_author_deck_falls_back_when_authoring_returns_invalid(monkeypatch):
    async def empty(content, ds, brand, role, index, n):
        return ""

    monkeypatch.setattr(author_deck_mod, "author_slide_html", empty)
    htmls = _run(author_deck(_outline(2), _brand())).htmls
    assert all(is_valid_slide_html(h) for h in htmls)


def test_author_deck_retries_then_succeeds(monkeypatch):
    calls = {"n": 0}

    async def flaky(content, ds, brand, role, index, n):
        calls["n"] += 1
        if calls["n"] == 1:
            return ""  # first attempt invalid -> retry
        return _VALID

    monkeypatch.setattr(author_deck_mod, "author_slide_html", flaky)
    htmls = _run(author_deck(_outline(1), _brand())).htmls
    assert htmls == [_VALID]
    assert calls["n"] == 2


def test_author_deck_backs_off_and_retries_transient_provider_failures(monkeypatch):
    calls = {"n": 0}
    delays = []

    async def flaky(content, ds, brand, role, index, n):
        calls["n"] += 1
        if calls["n"] < 4:
            raise RuntimeError(
                "Our servers are currently overloaded: incomplete chunked read"
            )
        return _VALID

    async def record_sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(author_deck_mod, "author_slide_html", flaky)
    monkeypatch.setattr(author_deck_mod.asyncio, "sleep", record_sleep)

    htmls = _run(author_deck(_outline(1), _brand())).htmls

    assert htmls == [_VALID]
    assert calls["n"] == 4
    assert delays == [1.5, 3.0, 6.0]


def test_author_deck_keeps_two_attempt_limit_for_permanent_failures(monkeypatch):
    calls = {"n": 0}

    async def invalid_request(content, ds, brand, role, index, n):
        calls["n"] += 1
        raise RuntimeError("invalid authored slide request")

    monkeypatch.setattr(author_deck_mod, "author_slide_html", invalid_request)

    htmls = _run(author_deck(_outline(1), _brand())).htmls

    assert calls["n"] == 2
    assert is_fallback_slide_html(htmls[0])


def test_default_design_system_is_byte_for_byte_canonical_output():
    without_style = build_design_system(_brand())
    canonical_default = build_design_system(
        _brand(), SimpleNamespace(id="default", brief="this must not replace legacy")
    )

    assert canonical_default == without_style
    assert "precise bottom" in without_style
    assert "bottom:14px" in without_style
    assert "bottom 72px" in without_style
    assert "#A7ACB3" in without_style
    assert "12px type (9pt equivalent)" in without_style
    assert "number only" in without_style


def test_design_system_prohibits_external_fonts_and_documents_local_fallbacks():
    design_system = build_design_system(_brand())

    assert "Never emit a Google Fonts <link>" in design_system
    assert '"Noto Sans KR"/"Pretendard" -> "Malgun Gothic"' in design_system
    assert '"Source Serif 4" -> "Cambria"' in design_system
    assert '"IBM Plex Mono" -> "Consolas"' in design_system
    assert "Load from Google Fonts" not in design_system
    assert "fonts are embedded in the PPTX" in design_system
    assert "never ::before/::after pseudo-elements" in design_system
    assert "Do not use inline SVG for simple decoration" in design_system
    assert "text-shadow, filter, backdrop-filter, or mix-blend-mode" in design_system
    assert "no asymmetric or percentage radii" in design_system
    assert "linear-gradient backgrounds, layered box-shadow, and raster images" in design_system


def test_custom_design_system_injects_brief_without_weakening_common_rules():
    style = SimpleNamespace(
        id="editorial",
        brief="  - Editorial composition with raw {curly} braces.\n- Restrained typography.  ",
    )
    design_system = build_design_system(_brand(), style)

    assert "- Editorial composition with raw {curly} braces." in design_system
    assert "Brand primary colour: #2563EB" in design_system
    assert "EXACTLY 1280x720px" in design_system
    assert "FIT IS CRITICAL" in design_system
    assert "unnecessarily hard-to-read" in design_system
    assert "precise bottom" in design_system
    assert "bottom:14px" in design_system
    assert "bottom 72px" in design_system
    assert "#A7ACB3" in design_system
    assert "12px type (9pt equivalent)" in design_system
    assert "number only" in design_system
    assert "Return ONLY the complete HTML document." in design_system
    assert "The primary is the ONE accent" not in design_system


def test_author_prompt_uses_cover_exception_and_sequential_bottom_center_marker():
    cover = author_slide_mod._author_prompt(
        "cover", "DESIGN", _brand(), "COVER", 0, 4
    )
    content = author_slide_mod._author_prompt(
        "content", "DESIGN", _brand(), "PROBLEM", 1, 4
    )

    assert "PAGE MARKER FOR THIS SLIDE: OMIT IT" in cover
    assert "render no page number and no footer text" in cover
    assert 'render exactly "01" once' in content
    assert "as the only footer text" in content


def test_temporary_authored_render_preserves_sub_12px_source_css(monkeypatch):
    captured = []

    async def fake_uncached(html, timeout=60):
        captured.append(html)
        return html.encode("utf-8")

    slide_capture._render_cache.clear()
    slide_capture._render_inflight.clear()
    monkeypatch.setattr(slide_capture, "_render_html_to_png_uncached", fake_uncached)

    htmls = [
        f"<html><body><span style='font-size:{size}px'>small</span></body></html>"
        for size in (8, 10, 11)
    ]
    rendered = [_run(slide_capture.render_html_to_png(html)) for html in htmls]

    assert captured == htmls
    assert rendered == [html.encode("utf-8") for html in htmls]


def test_authored_render_cache_version_separates_pre_font_floor_entries(monkeypatch):
    calls = 0

    async def fake_uncached(html, timeout=60):
        nonlocal calls
        calls += 1
        return f"render-{calls}".encode()

    slide_capture._render_cache.clear()
    slide_capture._render_inflight.clear()
    monkeypatch.setattr(slide_capture, "_render_html_to_png_uncached", fake_uncached)
    monkeypatch.setattr(slide_capture, "RENDER_CACHE_VERSION", "legacy-9pt-floor")
    assert _run(slide_capture.render_html_to_png(_VALID)) == b"render-1"

    monkeypatch.setattr(
        slide_capture,
        "RENDER_CACHE_VERSION",
        "authored-render-v3-preserve-authored-font-size",
    )
    assert _run(slide_capture.render_html_to_png(_VALID)) == b"render-2"
    assert calls == 2


# --- image reference intent + multimodal resilience ------------------------


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
    monkeypatch.setattr(author_slide_mod, "_wait_for_author_rate_slot", no_rate_limit)
    return calls


def _reference_image(tmp_path):
    path = tmp_path / "reference.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\nreference")
    return path


def _author_user_content(call):
    return call["messages"][1].content


def test_author_slide_style_reference_keeps_art_direction_multimodal_contract(
    monkeypatch, tmp_path
):
    calls = _capture_author_requests(monkeypatch, [_VALID])

    html = _run(
        author_slide_mod.author_slide_html(
            "content", "DESIGN", _brand(), "COVER", 0, 1,
            reference_images=[_reference_image(tmp_path)],
        )
    )

    user_content = _author_user_content(calls[0])
    assert html == _VALID
    assert len(calls) == 1
    assert isinstance(user_content, list)
    assert "art direction only" in user_content[0].text
    assert "Do NOT copy their text, logos, brand names" in user_content[0].text
    assert len(user_content) == 2
    assert getattr(user_content[1], "mime_type", None) == "image/png"


def test_author_slide_style_reference_enforces_screen_font_floor(monkeypatch):
    authored = (
        "<!doctype html><html><head><style>.caption{font-size:8px}</style></head>"
        "<body><p class='caption'>Caption</p></body></html>"
    )
    calls = _capture_author_requests(monkeypatch, [authored])

    html = _run(
        author_slide_mod.author_slide_html(
            "content", "DESIGN", _brand(), "CONTENT", 1, 2
        )
    )

    assert ".caption{font-size:12px}" in html
    assert len(calls) == 1


def test_author_slide_style_reference_preserves_text_only_fallback(monkeypatch, tmp_path):
    calls = _capture_author_requests(monkeypatch, [RuntimeError("vision unavailable"), _VALID])

    html = _run(
        author_slide_mod.author_slide_html(
            "content", "DESIGN", _brand(), "COVER", 0, 1,
            reference_images=[_reference_image(tmp_path)],
        )
    )

    assert html == _VALID
    assert len(calls) == 2
    assert isinstance(_author_user_content(calls[0]), list)
    assert _author_user_content(calls[1]) == _author_user_content(calls[0])[0].text.split(
        "\n\nVISUAL REFERENCES", 1
    )[0]


def test_author_slide_exact_reconstruction_uses_fidelity_prompt(monkeypatch, tmp_path):
    authored = (
        "<!doctype html><html><head><style>.source{font-size:8px}</style></head>"
        "<body><p class='source'>Source</p></body></html>"
    )
    calls = _capture_author_requests(monkeypatch, [authored])

    html = _run(
        author_slide_mod.author_slide_html(
            "content", "DESIGN", _brand(), "COVER", 0, 1,
            reference_images=[_reference_image(tmp_path)],
            reference_mode=EXACT_RECONSTRUCTION,
        )
    )

    user_content = _author_user_content(calls[0])
    prompt = " ".join(user_content[0].text.lower().split())
    assert html == authored
    assert len(user_content) == 2
    for required in (
        "exact reconstruction mode",
        "character exactly",
        "numbers",
        "spacing",
        "line breaks",
        "position, width, height, spacing, alignment, color, border, shape",
        "hierarchy, and stacking order",
        "1280x720px",
        "explicit css pixel",
        "do not redesign",
        "do not paraphrase, summarize",
        "do not redesign, restyle, add content",
        "before answering",
        "verify text and",
        "font",
        "css",
        "overlap",
        "clipping",
        "only the complete self-contained html document",
    ):
        assert required in prompt
    assert "art direction only" not in prompt
    assert "do not copy their text" not in prompt


def test_author_slide_exact_reconstruction_never_retries_without_the_image(
    monkeypatch, tmp_path
):
    calls = _capture_author_requests(monkeypatch, [RuntimeError("vision unavailable")])
    monkeypatch.setattr(author_slide_mod, "handle_llm_client_exceptions", lambda error: error)

    with pytest.raises(RuntimeError, match="vision unavailable"):
        _run(
            author_slide_mod.author_slide_html(
                "content", "DESIGN", _brand(), "COVER", 0, 1,
                reference_images=[_reference_image(tmp_path)],
                reference_mode=EXACT_RECONSTRUCTION,
            )
        )

    assert len(calls) == 1
    assert isinstance(_author_user_content(calls[0]), list)


@pytest.mark.parametrize("reference_case", ["missing", "unreadable", "multiple"])
def test_author_slide_exact_reconstruction_requires_one_loaded_image_before_llm_call(
    monkeypatch, tmp_path, reference_case
):
    calls = _capture_author_requests(monkeypatch, [_VALID])
    readable = _reference_image(tmp_path)
    references = {
        "missing": [],
        "unreadable": [tmp_path / "does-not-exist.png"],
        "multiple": [readable, readable],
    }[reference_case]

    with pytest.raises(
        ValueError, match="exactly one readable reference image"
    ):
        _run(
            author_slide_mod.author_slide_html(
                "content", "DESIGN", _brand(), "COVER", 0, 1,
                reference_images=references,
                reference_mode=EXACT_RECONSTRUCTION,
            )
        )

    assert calls == []


def test_author_deck_builds_and_shares_selected_design_system_once(monkeypatch):
    style = SimpleNamespace(id="editorial", brief="- Editorial")
    build_calls = []
    author_design_systems = []

    def fake_build(brand, selected_style):
        build_calls.append((brand, selected_style))
        return "SELECTED DESIGN SYSTEM"

    async def fake_author(content, design_system, brand, role, index, n):
        author_design_systems.append(design_system)
        return _VALID

    monkeypatch.setattr(author_deck_mod, "build_design_system", fake_build)
    monkeypatch.setattr(author_deck_mod, "author_slide_html", fake_author)

    result = _run(author_deck(_outline(3), _brand(), style))

    assert len(build_calls) == 1
    assert build_calls[0][1] is style
    assert result == AuthoredDeckResult(
        htmls=[_VALID, _VALID, _VALID], design_system="SELECTED DESIGN SYSTEM"
    )
    assert author_design_systems == ["SELECTED DESIGN SYSTEM"] * 3


def test_author_deck_sends_role_specific_visual_references(monkeypatch, tmp_path):
    cover = tmp_path / "cover.png"
    content = tmp_path / "content.png"
    closing = tmp_path / "closing.png"
    style = SimpleNamespace(
        id="visual",
        brief="- Visual",
        reference_images={
            "cover": (cover,),
            "content": (content,),
            "closing": (closing,),
        },
    )
    captured = []

    async def fake_author(
        content_text,
        design_system,
        brand,
        role,
        index,
        n,
        reference_images=None,
    ):
        captured.append((role, tuple(reference_images or ())))
        return _VALID

    monkeypatch.setattr(author_deck_mod, "author_slide_html", fake_author)

    _run(author_deck(_outline(3), _brand(), style))

    assert captured == [
        ("COVER", (cover,)),
        ("PROBLEM", (content,)),
        ("CLOSING", (closing,)),
    ]


def test_author_deck_propagates_exact_reference_mode(monkeypatch, tmp_path):
    cover = _reference_image(tmp_path)
    style = SimpleNamespace(
        id="exact-source",
        brief="- Exact source",
        reference_images={"cover": (cover,)},
        slide_reference_images=(cover,),
        reference_mode=EXACT_RECONSTRUCTION,
    )
    captured = []

    async def fake_author(*args, reference_images=None, reference_mode=None, **kwargs):
        captured.append((tuple(reference_images or ()), reference_mode))
        return _VALID

    monkeypatch.setattr(author_deck_mod, "author_slide_html", fake_author)

    result = _run(author_deck(_outline(1), _brand(), style))

    assert result.htmls == [_VALID]
    assert captured == [((cover,), EXACT_RECONSTRUCTION)]


def test_author_deck_exact_failure_is_not_silently_replaced_by_placeholder(
    monkeypatch, tmp_path
):
    style = SimpleNamespace(
        id="exact-source",
        brief="- Exact source",
        reference_images={"cover": (_reference_image(tmp_path),)},
        slide_reference_images=(_reference_image(tmp_path),),
        reference_mode=EXACT_RECONSTRUCTION,
    )

    async def unavailable(*args, **kwargs):
        raise RuntimeError("reference-aware author unavailable")

    monkeypatch.setattr(author_deck_mod, "author_slide_html", unavailable)

    with pytest.raises(RuntimeError, match="reference-aware author unavailable"):
        _run(author_deck(_outline(1), _brand(), style))


def test_author_deck_exact_mode_rejects_a_slide_without_its_source(monkeypatch):
    style = SimpleNamespace(
        id="exact-source",
        brief="- Exact source",
        reference_images={},
        slide_reference_images=(),
        reference_mode=EXACT_RECONSTRUCTION,
    )
    called = False

    async def fake_author(*args, **kwargs):
        nonlocal called
        called = True
        return _VALID

    monkeypatch.setattr(author_deck_mod, "author_slide_html", fake_author)

    with pytest.raises(ValueError, match="source count must match"):
        _run(author_deck(_outline(1), _brand(), style))

    assert called is False


def test_style_defaults_preserve_explicit_brand_overrides():
    style = SimpleNamespace(
        id="visual",
        primary_color="#35F2C2",
        body_font="Noto Sans KR",
        heading_font="Space Grotesk",
    )
    preset_brand = apply_style_defaults(_brand(), style)
    custom_brand = apply_style_defaults(
        Brand(
            topic="topic",
            primary="#FF6600",
            fonts="Pretendard",
            primary_is_explicit=True,
            fonts_are_explicit=True,
        ),
        style,
    )

    assert preset_brand.primary == "#35F2C2"
    assert preset_brand.fonts == "Noto Sans KR"
    assert custom_brand.primary == "#FF6600"
    assert custom_brand.fonts == "Pretendard"


def test_style_defaults_fall_back_to_preview_accent_without_a_primary_token():
    # 28 of 30 presets describe their palette in prose and carry no primary_color
    # token; the picker swatch accent must then become the effective brand primary
    # so the prompt and persisted theme match the style instead of the brand blue.
    style = SimpleNamespace(id="exec-report", preview_accent="#00A3E0")
    assert apply_style_defaults(_brand(), style).primary == "#00A3E0"

    # An explicit request colour still wins over the swatch fallback.
    explicit = Brand(topic="t", primary="#FF6600", primary_is_explicit=True)
    assert apply_style_defaults(explicit, style).primary == "#FF6600"

    # A structured primary_color token still takes precedence over the swatch.
    tokened = SimpleNamespace(
        id="cyber", preview_accent="#00A3E0", primary_color="#35F2C2"
    )
    assert apply_style_defaults(_brand(), tokened).primary == "#35F2C2"


def test_generate_request_accepts_optional_authored_style():
    assert GeneratePresentationRequest(content="topic").authored_style is None
    request = GeneratePresentationRequest(content="topic", authored_style="editorial")
    assert request.authored_style == "editorial"
    assert request.model_dump()["authored_style"] == "editorial"


def test_vision_qa_reuses_selected_design_system(monkeypatch):
    captured = []

    async def fake_critique(pngs, contexts=None):
        return [SlideCritique(needs_fix=True)]

    async def fake_author(
        content, design_system, brand, role, index, n, reasoning_effort=None
    ):
        captured.append((design_system, reasoning_effort))
        return _VALID

    async def fake_render(html, timeout=60):
        return b"revised-png"

    monkeypatch.setattr(author_vision_qa_mod, "critique_authored", fake_critique)
    monkeypatch.setattr(author_vision_qa_mod, "author_slide_html", fake_author)
    monkeypatch.setattr(author_vision_qa_mod, "render_html_to_png", fake_render)

    htmls, pngs, fixed = _run(
        author_vision_qa_mod.revise_authored_deck(
            [_VALID],
            [b"original-png"],
            ["content"],
            ["COVER"],
            _brand(),
            "SELECTED DESIGN SYSTEM",
        )
    )

    assert captured == [("SELECTED DESIGN SYSTEM", "high")]
    assert htmls == [_VALID]
    assert pngs == [b"revised-png"]
    assert fixed == [0]


def test_vision_qa_propagates_exact_reference_mode(monkeypatch, tmp_path):
    captured = []
    style = SimpleNamespace(
        id="exact-source",
        brief="- Exact source",
        reference_images={"cover": (_reference_image(tmp_path),)},
        slide_reference_images=(_reference_image(tmp_path),),
        reference_mode=EXACT_RECONSTRUCTION,
    )

    async def fake_critique(pngs, contexts=None, **kwargs):
        return [SlideCritique(needs_fix=True)]

    async def fake_author(*args, reference_images=None, reference_mode=None, **kwargs):
        captured.append((tuple(reference_images or ()), reference_mode, args[0]))
        return _VALID

    async def fake_render(html, timeout=60):
        return b"revised-png"

    monkeypatch.setattr(author_vision_qa_mod, "critique_authored", fake_critique)
    monkeypatch.setattr(author_vision_qa_mod, "author_slide_html", fake_author)
    monkeypatch.setattr(author_vision_qa_mod, "render_html_to_png", fake_render)

    htmls, pngs, fixed = _run(
        author_vision_qa_mod.revise_authored_deck(
            [_VALID],
            [b"original-png"],
            ["content"],
            ["COVER"],
            _brand(),
            "SELECTED DESIGN SYSTEM",
            style=style,
            reference_images_by_slide=[(style.slide_reference_images[0],)],
        )
    )

    assert captured[0][:2] == (
        (style.slide_reference_images[0],),
        EXACT_RECONSTRUCTION,
    )
    assert "Preserve source text character-for-character" in captured[0][2]
    assert "shorten content" in captured[0][2]
    assert "fit at 12px minimum" not in captured[0][2]
    assert htmls == [_VALID]
    assert pngs == [b"revised-png"]
    assert fixed == [0]


def test_vision_qa_subset_preserves_absolute_slide_position(monkeypatch):
    captured = []

    async def fake_critique(pngs, contexts=None):
        return [SlideCritique(needs_fix=True)]

    async def fake_author(
        content, design_system, brand, role, index, n, reasoning_effort=None
    ):
        captured.append((index, n))
        return _VALID

    async def fake_render(html, timeout=60):
        return b"revised-png"

    monkeypatch.setattr(author_vision_qa_mod, "critique_authored", fake_critique)
    monkeypatch.setattr(author_vision_qa_mod, "author_slide_html", fake_author)
    monkeypatch.setattr(author_vision_qa_mod, "render_html_to_png", fake_render)

    _run(
        author_vision_qa_mod.revise_authored_deck(
            [_VALID],
            [b"original-png"],
            ["content"],
            ["PILLARS"],
            _brand(),
            "SELECTED DESIGN SYSTEM",
            slide_indices=[4],
            total_slides=10,
        )
    )

    assert captured == [(4, 10)]


def test_vision_qa_pipelines_fast_slide_fix_before_slowest_review(monkeypatch):
    """A reviewed slide must start correction without waiting for every review."""

    async def scenario():
        first_correction_started = asyncio.Event()

        async def fake_critique(pngs, contexts=None):
            if pngs[0] == b"png-1":
                await asyncio.wait_for(first_correction_started.wait(), timeout=0.5)
            return [SlideCritique(needs_fix=True)]

        async def fake_author(
            content, design_system, brand, role, index, n, reasoning_effort=None
        ):
            if index == 0:
                first_correction_started.set()
            return _VALID.replace("</body>", f"<p>fixed-{index}</p></body>")

        async def fake_render(html, timeout=60):
            if "fixed-0" in html:
                return b"fixed-png-0"
            return b"fixed-png-1"

        monkeypatch.setattr(author_vision_qa_mod, "critique_authored", fake_critique)
        monkeypatch.setattr(author_vision_qa_mod, "author_slide_html", fake_author)
        monkeypatch.setattr(author_vision_qa_mod, "render_html_to_png", fake_render)

        return await asyncio.wait_for(
            author_vision_qa_mod.revise_authored_deck(
                [_VALID, _VALID],
                [b"png-0", b"png-1"],
                ["content 0", "content 1"],
                ["COVER", "CONTENT"],
                _brand(),
                "SELECTED DESIGN SYSTEM",
            ),
            timeout=1,
        )

    htmls, pngs, fixed = _run(scenario())

    assert ["fixed-0" in htmls[0], "fixed-1" in htmls[1]] == [True, True]
    assert pngs == [b"fixed-png-0", b"fixed-png-1"]
    assert fixed == [0, 1]


def test_vision_qa_rejects_invalid_reauthor_before_render(monkeypatch):
    render_calls = 0

    async def fake_critique(pngs, contexts=None):
        return [SlideCritique(needs_fix=True)]

    async def fake_author(*args, **kwargs):
        return "truncated output"

    async def fake_render(html, timeout=60):
        nonlocal render_calls
        render_calls += 1
        return b"must-not-render"

    monkeypatch.setattr(author_vision_qa_mod, "critique_authored", fake_critique)
    monkeypatch.setattr(author_vision_qa_mod, "author_slide_html", fake_author)
    monkeypatch.setattr(author_vision_qa_mod, "render_html_to_png", fake_render)

    htmls, pngs, fixed = _run(
        author_vision_qa_mod.revise_authored_deck(
            [_VALID],
            [b"original-png"],
            ["content"],
            ["COVER"],
            _brand(),
            "SELECTED DESIGN SYSTEM",
        )
    )

    assert render_calls == 0
    assert htmls == [_VALID]
    assert pngs == [b"original-png"]
    assert fixed == []


def test_vision_qa_cache_reuses_identical_successful_review(monkeypatch):
    calls = 0

    async def fake_review(image, slide_context=None):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return SlideCritique(needs_fix=False)

    author_vision_qa_mod._vqa_cache.clear()
    author_vision_qa_mod._vqa_inflight.clear()
    author_vision_qa_mod._vqa_sem = None
    monkeypatch.setattr(author_vision_qa_mod, "VQA_CACHE_SIZE", 2)
    monkeypatch.setattr(author_vision_qa_mod, "get_model", lambda: "test-model")
    monkeypatch.setattr(author_vision_qa_mod, "critique_slide_image", fake_review)

    async def review_twice():
        first = await author_vision_qa_mod.critique_authored(
            [b"same-png", b"same-png"], ["same-context", "same-context"]
        )
        second = await author_vision_qa_mod.critique_authored(
            [b"same-png"], ["same-context"]
        )
        return first, second

    first, second = _run(review_twice())

    assert calls == 1
    assert first[0] is first[1]
    assert second[0].needs_fix is False


def test_vision_qa_style_review_keeps_render_only_multimodal_call(monkeypatch):
    calls = []

    async def fake_review(image, slide_context=None, **kwargs):
        calls.append((image, slide_context, kwargs))
        return SlideCritique(needs_fix=False)

    author_vision_qa_mod._vqa_cache.clear()
    author_vision_qa_mod._vqa_inflight.clear()
    author_vision_qa_mod._vqa_sem = None
    monkeypatch.setattr(author_vision_qa_mod, "get_model", lambda: "test-model")
    monkeypatch.setattr(author_vision_qa_mod, "critique_slide_image", fake_review)

    critiques = _run(
        author_vision_qa_mod.critique_authored(
            [b"render-only"], contexts=["style context"]
        )
    )

    assert critiques[0].needs_fix is False
    assert calls == [(b"render-only", "style context", {})]


def test_critique_message_assembly_uses_one_render_for_style_and_ordered_source_render_for_exact():
    render = b"\x89PNG\r\n\x1a\nrender"
    source = b"\xff\xd8\xffsource"

    style_messages = critique_slide_mod._build_critique_messages(
        render, slide_context="style context"
    )
    exact_messages = critique_slide_mod._build_critique_messages(
        render,
        slide_context="exact context",
        source_image=source,
        reference_mode=EXACT_RECONSTRUCTION,
    )

    style_content = style_messages[1].content
    assert len(style_content) == 2
    assert style_content[1].data == render
    assert style_content[1].mime_type == "image/png"
    assert "source" not in style_messages[0].content.lower()

    exact_content = exact_messages[1].content
    assert len(exact_content) == 4
    assert exact_content[1].data == source
    assert exact_content[1].mime_type == "image/jpeg"
    assert exact_content[3].data == render
    assert exact_content[3].mime_type == "image/png"
    assert "intentional" in exact_messages[0].content.lower()
    assert "source and rendered" in exact_content[2].text.lower()


def test_vision_qa_exact_review_pairs_each_render_with_its_own_source(monkeypatch):
    calls = []

    async def fake_review(image, slide_context=None, **kwargs):
        calls.append((image, slide_context, kwargs))
        return SlideCritique(needs_fix=False)

    author_vision_qa_mod._vqa_cache.clear()
    author_vision_qa_mod._vqa_inflight.clear()
    author_vision_qa_mod._vqa_sem = None
    monkeypatch.setattr(author_vision_qa_mod, "get_model", lambda: "test-model")
    monkeypatch.setattr(author_vision_qa_mod, "critique_slide_image", fake_review)

    critiques = _run(
        author_vision_qa_mod.critique_authored(
            [b"render-0", b"render-1"],
            contexts=["first", "second"],
            source_images=[b"source-0", b"source-1"],
            reference_mode=EXACT_RECONSTRUCTION,
        )
    )

    assert [critique.needs_fix for critique in critiques] == [False, False]
    assert calls == [
        (
            b"render-0",
            "first",
            {
                "source_image": b"source-0",
                "reference_mode": EXACT_RECONSTRUCTION,
            },
        ),
        (
            b"render-1",
            "second",
            {
                "source_image": b"source-1",
                "reference_mode": EXACT_RECONSTRUCTION,
            },
        ),
    ]


@pytest.mark.parametrize("source_images", [None, [], [b"only-one"], [b"", b"source"]])
def test_vision_qa_exact_review_fails_closed_without_complete_sources(source_images):
    with pytest.raises(ValueError, match="source image"):
        _run(
            author_vision_qa_mod.critique_authored(
                [b"render-0", b"render-1"],
                source_images=source_images,
                reference_mode=EXACT_RECONSTRUCTION,
            )
        )


def test_vision_qa_cache_key_separates_source_render_and_mode(monkeypatch):
    monkeypatch.setattr(author_vision_qa_mod, "get_model", lambda: "test-model")
    common = author_vision_qa_mod._critique_cache_key(
        b"render", "context", source_image=b"source-a", reference_mode=EXACT_RECONSTRUCTION
    )

    assert common != author_vision_qa_mod._critique_cache_key(
        b"render", "context", source_image=b"source-b", reference_mode=EXACT_RECONSTRUCTION
    )
    assert common != author_vision_qa_mod._critique_cache_key(
        b"render-changed", "context", source_image=b"source-a", reference_mode=EXACT_RECONSTRUCTION
    )
    assert common != author_vision_qa_mod._critique_cache_key(
        b"render", "context", source_image=b"source-a", reference_mode=STYLE_REFERENCE
    )


def test_vision_qa_reuses_initial_critique_and_rechecks_only_changed_slide(
    monkeypatch, tmp_path
):
    source = _reference_image(tmp_path)
    source.write_bytes(b"source-0")
    style = SimpleNamespace(
        id="exact-source",
        brief="- Exact source",
        reference_mode=EXACT_RECONSTRUCTION,
    )
    critique_calls = []
    author_calls = []

    async def fake_critique(pngs, contexts=None, **kwargs):
        critique_calls.append((list(pngs), kwargs))
        return [SlideCritique(needs_fix=False)]

    async def fake_author(*args, reference_images=None, reference_mode=None, **kwargs):
        author_calls.append((tuple(reference_images or ()), reference_mode))
        return _VALID.replace("</body>", "<p>fixed</p></body>")

    async def fake_render(html, timeout=60):
        return b"fixed-render"

    monkeypatch.setattr(author_vision_qa_mod, "critique_authored", fake_critique)
    monkeypatch.setattr(author_vision_qa_mod, "author_slide_html", fake_author)
    monkeypatch.setattr(author_vision_qa_mod, "render_html_to_png", fake_render)

    htmls, pngs, fixed = _run(
        author_vision_qa_mod.revise_authored_deck(
            [_VALID, _VALID],
            [b"original-0", b"original-1"],
            ["content-0", "content-1"],
            ["CONTENT", "CONTENT"],
            _brand(),
            "DESIGN",
            style=style,
            initial_critiques=[SlideCritique(needs_fix=True), SlideCritique(needs_fix=False)],
            reference_images_by_slide=[(source,), (source,)],
        )
    )

    assert author_calls == [((source,), EXACT_RECONSTRUCTION)]
    # The service performs the final comparison.  `revise_authored_deck` consumes
    # the already-paid initial critique and does not duplicate it for max_cycles=1.
    assert critique_calls == []
    assert fixed == [0]
    assert pngs == [b"fixed-render", b"original-1"]
    assert "fixed" in htmls[0]


def test_vision_qa_exact_same_role_slides_keep_distinct_per_slide_sources(
    monkeypatch, tmp_path
):
    source_0 = tmp_path / "source-0.png"
    source_1 = tmp_path / "source-1.png"
    source_0.write_bytes(b"source-0")
    source_1.write_bytes(b"source-1")
    style = SimpleNamespace(
        id="exact-source",
        brief="- Exact source",
        reference_mode=EXACT_RECONSTRUCTION,
    )
    received_sources = []

    async def fake_author(*args, reference_images=None, **kwargs):
        received_sources.append(tuple(reference_images or ()))
        return _VALID

    async def fake_render(html, timeout=60):
        return b"revised"

    monkeypatch.setattr(author_vision_qa_mod, "author_slide_html", fake_author)
    monkeypatch.setattr(author_vision_qa_mod, "render_html_to_png", fake_render)

    _run(
        author_vision_qa_mod.revise_authored_deck(
            [_VALID, _VALID],
            [b"render-0", b"render-1"],
            ["content-0", "content-1"],
            ["CONTENT", "CONTENT"],
            _brand(),
            "DESIGN",
            style=style,
            initial_critiques=[SlideCritique(needs_fix=True), SlideCritique(needs_fix=True)],
            reference_images_by_slide=[(source_0,), (source_1,)],
        )
    )

    assert received_sources == [(source_0,), (source_1,)]


def test_authored_service_resolves_passes_and_persists_canonical_style(
    monkeypatch, fake_async_session
):
    selected_style = SimpleNamespace(id="editorial", brief="- Editorial")
    captured = {}

    async def fake_pipeline(outline, brand, style):
        captured["style"] = style
        return authored_service.AuthoredGenerationPipelineResult(
            deck=AuthoredDeckResult([_VALID], "SELECTED DESIGN SYSTEM"),
            pngs=[b"png"],
            elapsed=0.1,
            author_work=0.1,
            illustration_work=0.0,
            render_work=0.1,
        )

    async def fake_revise(
        htmls, pngs, contents, roles, brand, design_system, max_cycles, style=None
    ):
        captured["vision_design_system"] = design_system
        captured["vision_style"] = style
        return htmls, pngs, []

    monkeypatch.setattr(
        authored_service,
        "resolve_authored_style",
        lambda style_id: selected_style if style_id == "editorial-alias" else None,
    )
    monkeypatch.setattr(
        authored_service, "_author_illustrate_render_pipeline", fake_pipeline
    )
    monkeypatch.setattr(authored_service, "revise_authored_deck", fake_revise)
    monkeypatch.setattr(authored_service, "find_chrome", lambda: True)
    monkeypatch.setattr(
        authored_service, "_save_slide_pngs", lambda presentation_id, pngs: ["slide.png"]
    )
    monkeypatch.setattr(
        authored_service,
        "_build_authored_export",
        lambda request, presentation_id, pngs, title: "deck.pptx",
    )

    from utils import llm_provider

    monkeypatch.setattr(
        llm_provider, "get_llm_provider", lambda: SimpleNamespace(value="stub")
    )
    monkeypatch.setattr(llm_provider, "get_model", lambda: "stub-model")

    session = fake_async_session
    request = GeneratePresentationRequest(
        content="topic",
        template="authored",
        authored_style="editorial-alias",
        vision_qa=True,
    )
    result = _run(
        authored_service.generate_authored_presentation(
            request, uuid.uuid4(), _outline(1), "English", session
        )
    )
    presentation = next(x for x in session.added if isinstance(x, PresentationModel))

    assert captured["style"] is selected_style
    assert captured["vision_design_system"] == "SELECTED DESIGN SYSTEM"
    assert captured["vision_style"] is selected_style
    assert presentation.theme["style"] == "editorial"
    assert presentation.theme["mode"] == "authored"
    assert "reference_mode" not in presentation.theme
    assert result.path == "deck.pptx"


def test_authored_service_validates_exact_mapping_before_pipeline(monkeypatch):
    selected_style = SimpleNamespace(
        id="exact",
        brief="- Exact",
        reference_mode=EXACT_RECONSTRUCTION,
    )
    calls = {"pipeline": 0, "validation": 0}

    def reject_incomplete_mapping(style, *, slide_count, roles):
        calls["validation"] += 1
        assert style is selected_style
        assert slide_count == 1
        assert len(roles) == 1
        raise ValueError("incomplete exact mapping")

    async def fake_pipeline(*args, **kwargs):
        calls["pipeline"] += 1
        raise AssertionError("pipeline must not start before exact validation")

    monkeypatch.setattr(
        authored_service,
        "resolve_authored_style",
        lambda style_id: selected_style,
    )
    monkeypatch.setattr(
        authored_service,
        "validate_exact_reconstruction_style",
        reject_incomplete_mapping,
    )
    monkeypatch.setattr(
        authored_service,
        "_author_illustrate_render_pipeline",
        fake_pipeline,
    )
    request = GeneratePresentationRequest(
        content="topic",
        template="authored",
        authored_style="exact",
    )

    with pytest.raises(ValueError, match="incomplete exact mapping"):
        _run(
            authored_service.generate_authored_presentation(
                request,
                uuid.uuid4(),
                _outline(1),
                "English",
                SimpleNamespace(),
            )
        )

    assert calls == {"pipeline": 0, "validation": 1}


def test_slide_pipeline_renders_fast_slide_before_slowest_author_finishes(monkeypatch):
    """A slide must advance immediately; deck-wide author/render barriers regress this."""

    async def scenario():
        first_rendered = asyncio.Event()
        plan = SimpleNamespace(
            contents=("slide 0", "slide 1"), design_system="PIPELINED DESIGN SYSTEM"
        )

        async def fake_author(selected_plan, index):
            assert selected_plan is plan
            if index == 1:
                await asyncio.wait_for(first_rendered.wait(), timeout=0.5)
            return (
                "<!DOCTYPE html><html><head></head><body>"
                f"<h1>slide-{index}</h1></body></html>"
            )

        async def fake_render(html):
            if "slide-0" in html:
                first_rendered.set()
                return b"png-0"
            return b"png-1"

        monkeypatch.setattr(
            authored_service, "prepare_authored_deck", lambda *args: plan
        )
        monkeypatch.setattr(authored_service, "author_planned_slide", fake_author)
        monkeypatch.setattr(authored_service, "render_html_to_png", fake_render)

        return await asyncio.wait_for(
            authored_service._author_illustrate_render_pipeline(
                _outline(2),
                _brand(),
                SimpleNamespace(id="default", illustration_prompt=None),
            ),
            timeout=1,
        )

    result = _run(scenario())

    assert ["slide-0" in html for html in result.deck.htmls] == [True, False]
    assert ["slide-1" in html for html in result.deck.htmls] == [False, True]
    assert result.pngs == [b"png-0", b"png-1"]
    assert result.deck.design_system == "PIPELINED DESIGN SYSTEM"


def test_slide_pipeline_rejects_fallback_instead_of_publishing_incomplete_deck(
    monkeypatch,
):
    async def scenario():
        plan = SimpleNamespace(
            contents=("slide 0", "slide 1"), design_system="PIPELINED DESIGN SYSTEM"
        )

        async def fake_author(selected_plan, index):
            assert selected_plan is plan
            if index == 1:
                return fallback_slide_html(
                    "slide 1", _brand(), "PROBLEM", index, len(plan.contents)
                )
            return _VALID

        async def fake_render(_html):
            return b"png"

        monkeypatch.setattr(
            authored_service, "prepare_authored_deck", lambda *args: plan
        )
        monkeypatch.setattr(authored_service, "author_planned_slide", fake_author)
        monkeypatch.setattr(authored_service, "render_html_to_png", fake_render)

        return await authored_service._author_illustrate_render_pipeline(
            _outline(2),
            _brand(),
            SimpleNamespace(id="default", illustration_prompt=None),
        )

    with pytest.raises(
        RuntimeError,
        match=r"failed after retries for slide\(s\): 2",
    ):
        _run(scenario())


def test_ninety_slide_pipeline_keeps_worker_concurrency_bounded(monkeypatch):
    async def scenario():
        plan = SimpleNamespace(
            contents=tuple(f"slide {index}" for index in range(90)),
            design_system="BOUNDED DESIGN SYSTEM",
        )
        active = 0
        maximum_active = 0
        authored_indexes: list[int] = []
        rendered = 0

        async def fake_author(selected_plan, index):
            nonlocal active, maximum_active
            assert selected_plan is plan
            active += 1
            maximum_active = max(maximum_active, active)
            try:
                await asyncio.sleep(0.001)
                authored_indexes.append(index)
                return (
                    "<!DOCTYPE html><html><head></head><body>"
                    f"<h1>slide-{index}</h1></body></html>"
                )
            finally:
                active -= 1

        async def fake_render(_html):
            nonlocal rendered
            rendered += 1
            await asyncio.sleep(0)
            return b"png"

        monkeypatch.setattr(authored_service, "_AUTHORED_SLIDE_WORKERS", 8)
        monkeypatch.setattr(
            authored_service, "prepare_authored_deck", lambda *args: plan
        )
        monkeypatch.setattr(authored_service, "author_planned_slide", fake_author)
        monkeypatch.setattr(authored_service, "render_html_to_png", fake_render)

        result = await authored_service._author_illustrate_render_pipeline(
            _outline(90),
            _brand(),
            SimpleNamespace(id="default", illustration_prompt=None),
        )
        return result, maximum_active, authored_indexes, rendered

    result, maximum_active, authored_indexes, rendered = _run(scenario())

    assert len(result.pngs) == 90
    assert sorted(authored_indexes) == list(range(90))
    assert rendered == 90
    assert 1 < maximum_active <= 8


# --- render resilience (mocked Chrome) ---------------------------------------


def test_find_chrome_prefers_playwright_headless_shell(monkeypatch, tmp_path):
    for name in ("CHROME_PATH", "PUPPETEER_EXECUTABLE_PATH", "CHROMIUM_PATH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    shell = (
        tmp_path
        / "ms-playwright"
        / "chromium_headless_shell-1228"
        / "chrome-headless-shell-win64"
        / "chrome-headless-shell.exe"
    )
    shell.parent.mkdir(parents=True)
    shell.write_bytes(b"browser")

    assert slide_capture.find_chrome() == str(shell)


def test_render_html_cache_reuses_successful_identical_render(monkeypatch):
    calls = 0
    rendered = _png_1280x720((12, 34, 56))

    async def fake_uncached(html, timeout=60):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return rendered

    slide_capture._render_cache.clear()
    slide_capture._render_inflight.clear()
    monkeypatch.setattr(slide_capture, "RENDER_CACHE_SIZE", 2)
    monkeypatch.setattr(slide_capture, "_render_html_to_png_uncached", fake_uncached)

    async def render_twice():
        return await asyncio.gather(
            slide_capture.render_html_to_png(_VALID),
            slide_capture.render_html_to_png(_VALID),
        )

    first = _run(render_twice())
    second = _run(slide_capture.render_html_to_png(_VALID))
    assert calls == 1
    assert first == [rendered, rendered]
    assert second == rendered


def test_render_list_uses_placeholder_on_partial_failure(monkeypatch):
    calls = 0

    async def boom(html, timeout=60):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("one bad slide")
        return _png_1280x720((10, 20, 30))

    monkeypatch.setattr(slide_capture, "render_html_to_png", boom)
    pngs = _run(slide_capture.render_html_list_to_pngs([_VALID, _VALID]))
    assert len(pngs) == 2
    for png in pngs:
        img = Image.open(io.BytesIO(png))
        assert img.size == (slide_capture.SLIDE_W, slide_capture.SLIDE_H)


def test_render_list_rejects_deck_when_every_slide_fails(monkeypatch):
    async def boom(html, timeout=60):
        raise RuntimeError("no working chrome")

    monkeypatch.setattr(slide_capture, "render_html_to_png", boom)
    with pytest.raises(RuntimeError, match="Every authored slide failed"):
        _run(slide_capture.render_html_list_to_pngs([_VALID, _VALID]))


# --- image PPTX assembly -----------------------------------------------------


def _png_1280x720(color):
    img = Image.new("RGB", (1280, 720), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_build_image_pptx_one_fullbleed_picture_per_slide(tmp_path):
    from pptx import Presentation

    pngs = [_png_1280x720((i * 10, 0, 0)) for i in range(3)]
    out = str(tmp_path / "deck.pptx")
    build_image_pptx(pngs, out)
    prs = Presentation(out)
    assert len(prs.slides._sldIdLst) == 3
    assert prs.slide_width == 12191695 and prs.slide_height == 6858000  # 13.333x7.5"
    for s in prs.slides:
        pics = [sh for sh in s.shapes if sh.shape_type == 13]
        assert len(pics) == 1


def test_build_image_pdf_is_valid_multipage(tmp_path):
    from services.authored_presentation_service import _build_image_pdf

    pngs = [_png_1280x720((0, i * 20, 0)) for i in range(4)]
    out = str(tmp_path / "deck.pdf")
    _build_image_pdf(pngs, out)
    with open(out, "rb") as f:
        data = f.read()
    assert data[:5] == b"%PDF-"  # valid PDF magic (PIL can write but not read PDFs)
    assert b"/Count 4" in data  # four pages, one per slide image
    assert len(data) > 4000


# --- authored predicate + roles + brand --------------------------------------


def test_is_authored_predicate():
    by_theme = PresentationModel(
        content="x", n_slides=1, language="ko", theme={"mode": "authored"},
        layout={"name": "ignored"},
    )
    by_null_layout = PresentationModel(content="x", n_slides=1, language="ko")
    templated = PresentationModel(
        content="x", n_slides=1, language="ko", layout={"name": "adaptive"},
    )
    assert by_theme.is_authored() is True
    assert by_null_layout.is_authored() is True
    assert templated.is_authored() is False


def test_mode_column_is_authoritative_over_sentinels():
    # An explicit mode wins over the legacy theme/layout sentinels.
    authored_with_layout = PresentationModel(
        content="x", n_slides=1, language="ko",
        mode="authored", layout={"name": "korean-biz"},
    )
    template_without_layout = PresentationModel(
        content="x", n_slides=1, language="ko",
        mode="template", layout=None,
    )
    assert authored_with_layout.is_authored() is True
    assert template_without_layout.is_authored() is False


def test_get_new_presentation_preserves_mode_and_theme():
    # /derive previously dropped theme (losing authored brand colours + the legacy
    # sentinel); mode + theme must carry to the copy.
    original = PresentationModel(
        content="x", n_slides=1, language="ko",
        mode="authored", theme={"mode": "authored", "primary": "#123456"},
    )
    clone = original.get_new_presentation()
    assert clone.id != original.id
    assert clone.mode == "authored"
    assert clone.theme == {"mode": "authored", "primary": "#123456"}
    assert clone.is_authored() is True


def test_plan_deck_roles_cover_and_closing():
    roles = plan_deck_roles(_outline(5))
    assert roles[0] == "COVER"
    assert roles[-1] == "CLOSING"


def test_resolve_brand_language_fonts():
    req = GeneratePresentationRequest(content="t", language="Korean (한국어)", template="authored")
    ko = resolve_brand(req, _outline(2), "Korean (한국어)")
    assert ko.fonts == "Noto Sans KR"
    assert ko.primary == "#2563EB"

    req_en = GeneratePresentationRequest(content="t", language="English", template="authored")
    en = resolve_brand(req_en, _outline(2), "English")
    assert en.fonts == "Inter"


def test_resolve_brand_honours_custom_request_fields():
    req = GeneratePresentationRequest(
        content="제조업 스마트팩토리 전환 전략 2026",
        language="Korean",
        template="authored",
        primary_color="#FF6600",
        fonts="Pretendard",
        wordmark="ACME",
    )
    brand = resolve_brand(req, _outline(3), "Korean")
    assert brand.primary == "#FF6600"
    assert brand.fonts == "Pretendard"
    assert brand.wordmark == "ACME"


def test_authored_title_prefers_clean_content_over_verbose_outline():
    from services.authored_presentation_service import authored_title

    outline = PresentationOutlineModel(
        slides=[
            SlideOutlineModel(
                content="# 표지\n발표자 [발표자 이름]  날짜 2026-06-08  개요 매우 긴 생성 텍스트"
            ),
            SlideOutlineModel(content="본문"),
        ]
    )
    req = GeneratePresentationRequest(
        content="제조업 스마트팩토리 전환 전략 2026", template="authored"
    )
    title = authored_title(req, outline)
    assert title == "제조업 스마트팩토리 전환 전략 2026"
    assert "발표자" not in title
