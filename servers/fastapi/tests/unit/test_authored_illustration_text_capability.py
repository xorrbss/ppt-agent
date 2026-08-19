"""Provider-dependent text rules for authored illustration prompts.

Some image models (verified: ``gpt-image-2``) render Korean labels correctly;
Gemini-based and unknown backends do not. These tests pin the capability table,
the default-deny behaviour, and the fact that any label the model may draw must
come verbatim from the slide's own content.
"""

import asyncio
import base64
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from utils.authored_illustrations import (
    _MAX_SLIDE_CONTENT_CHARS,
    apply_authored_illustration,
    apply_authored_illustrations,
)
from utils.image_provider import image_provider_renders_text

_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNgYAAAAAMAASsJTYQAAAAASUVORK5CYII="
)

_SLOT_HTML = (
    "<html><body><h1>통합 리뉴얼 로드맵</h1>"
    '<img data-illust-prompt="isometric three phase roadmap" '
    'style="width:640px;height:360px">'
    "<p>본문</p></body></html>"
)

_CONTENT = "# 9개월 통합 리뉴얼 로드맵\n- 요구사항 분석\n- 설계·개발\n- 테스트·안정화"

_NO_TEXT_SENTENCE = "Absolutely no text"


def _run(coro):
    return asyncio.run(coro)


def _style():
    return SimpleNamespace(id="iso", illustration_prompt="isometric 3D, blue")


class _Service:
    """Stand-in for ImageGenerationService; `settings=None` means read the env."""

    def __init__(self, result, settings=None):
        self.result = result
        self.settings = settings
        self.prompts = []

    async def generate_image(self, prompt):
        self.prompts.append(prompt)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _ok_service(tmp_path: Path) -> _Service:
    image = tmp_path / "illust.png"
    image.write_bytes(_PNG_BYTES)
    return _Service(SimpleNamespace(path=str(image)))


# --- capability table -------------------------------------------------------


@pytest.mark.parametrize(
    ("provider", "expected"),
    [
        ("gpt-image-2", True),
        ("gpt-image-1.5", True),
        # dall-e-3 has no verified Korean glyph support: default-deny applies.
        ("dall-e-3", False),
        ("gemini_flash", False),
        ("nanobanana_pro", False),
        ("comfyui", False),
        ("open_webui", False),
        ("pexels", False),
        ("pixabay", False),
    ],
)
def test_image_provider_renders_text_table(monkeypatch, provider: str, expected: bool):
    monkeypatch.setenv("IMAGE_PROVIDER", provider)
    assert image_provider_renders_text() is expected


def test_unset_and_unrecognized_providers_are_not_text_capable(monkeypatch):
    monkeypatch.delenv("IMAGE_PROVIDER", raising=False)
    assert image_provider_renders_text() is False
    # A provider value this build does not know must never be assumed capable,
    # otherwise a new provider silently starts emitting broken Korean.
    monkeypatch.setenv("IMAGE_PROVIDER", "some-future-image-model")
    assert image_provider_renders_text() is False


# --- suffix selection -------------------------------------------------------


def test_incapable_provider_keeps_no_text_ban_and_withholds_content(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setenv("IMAGE_PROVIDER", "gemini_flash")
    service = _ok_service(tmp_path)

    _run(
        apply_authored_illustration(
            _SLOT_HTML, _style(), service, 0, slide_content=_CONTENT
        )
    )

    sent = service.prompts[0].prompt
    assert _NO_TEXT_SENTENCE in sent
    assert "요구사항 분석" not in sent
    assert "isometric three phase roadmap" in sent


def test_unknown_provider_keeps_no_text_ban(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("IMAGE_PROVIDER", "some-future-image-model")
    service = _ok_service(tmp_path)

    _run(
        apply_authored_illustration(
            _SLOT_HTML, _style(), service, 0, slide_content=_CONTENT
        )
    )

    assert _NO_TEXT_SENTENCE in service.prompts[0].prompt


def test_text_capable_provider_gets_constrained_label_rules_and_slide_content(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setenv("IMAGE_PROVIDER", "gpt-image-2")
    service = _ok_service(tmp_path)

    out = _run(
        apply_authored_illustration(
            _SLOT_HTML, _style(), service, 0, slide_content=_CONTENT
        )
    )

    sent = service.prompts[0].prompt
    assert _NO_TEXT_SENTENCE not in sent
    assert "at most 6 short Korean label strings" in sent
    assert "COPIED VERBATIM" in sent
    assert "NEVER invent" in sent
    assert "no watermark, no logo" in sent
    # The slide's own words travel with the prompt as the only label source.
    assert "9개월 통합 리뉴얼 로드맵" in sent
    assert "설계·개발" in sent
    assert "SLIDE CONTENT (the only permitted source of label text):" in sent
    assert "data:image/png;base64," in out


def test_text_capable_provider_without_slide_content_still_bans_text(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setenv("IMAGE_PROVIDER", "gpt-image-2")
    service = _ok_service(tmp_path)

    _run(apply_authored_illustration(_SLOT_HTML, _style(), service, 0))

    # No verbatim source means nothing may legally be copied, so labels stay off.
    assert _NO_TEXT_SENTENCE in service.prompts[0].prompt


def test_slide_content_is_capped_like_the_scene_prompt(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("IMAGE_PROVIDER", "gpt-image-2")
    service = _ok_service(tmp_path)
    overlong = "가" * (_MAX_SLIDE_CONTENT_CHARS + 500)

    _run(
        apply_authored_illustration(
            _SLOT_HTML, _style(), service, 0, slide_content=overlong
        )
    )

    sent = service.prompts[0].prompt
    assert sent.count("가") == _MAX_SLIDE_CONTENT_CHARS


def test_slide_contents_map_positionally_in_the_plural_helper(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setenv("IMAGE_PROVIDER", "gpt-image-2")
    service = _ok_service(tmp_path)

    _run(
        apply_authored_illustrations(
            [_SLOT_HTML, _SLOT_HTML],
            _style(),
            service,
            ["# 첫 장 제목", "# 둘째 장 제목"],
        )
    )

    prompts = sorted(prompt.prompt for prompt in service.prompts)
    assert len(prompts) == 2
    assert any("첫 장 제목" in prompt for prompt in prompts)
    assert any("둘째 장 제목" in prompt for prompt in prompts)
    # A short deck of contents must not raise for the slides it does not cover.
    short = _ok_service(tmp_path)
    _run(
        apply_authored_illustrations(
            [_SLOT_HTML, _SLOT_HTML], _style(), short, ["# 하나뿐"]
        )
    )
    assert len(short.prompts) == 2


# --- failure behaviour is unchanged ----------------------------------------


def test_failure_with_text_capable_provider_still_drops_the_slot(
    monkeypatch, caplog
):
    monkeypatch.setenv("IMAGE_PROVIDER", "gpt-image-2")
    service = _Service(RuntimeError("provider down"))

    with caplog.at_level(logging.WARNING):
        out = _run(
            apply_authored_illustration(
                _SLOT_HTML, _style(), service, 0, slide_content=_CONTENT
            )
        )

    assert "<img" not in out
    assert "<h1>통합 리뉴얼 로드맵</h1>" in out and "<p>본문</p>" in out


def test_raise_on_failure_still_propagates_with_slide_content(monkeypatch):
    monkeypatch.setenv("IMAGE_PROVIDER", "gpt-image-2")
    service = _Service(RuntimeError("provider down"))

    with pytest.raises(RuntimeError):
        _run(
            apply_authored_illustration(
                _SLOT_HTML,
                _style(),
                service,
                0,
                slide_content=_CONTENT,
                raise_on_failure=True,
            )
        )


def test_a_landscape_illustration_is_recompressed_to_fit_the_source_limit() -> None:
    """A provider's native source must not blow the renderer's HTML budget.

    The OpenAI image models return 1536x1024, whose PNG runs about 1MB and
    becomes ~1.4MB once base64 adds its third - close enough to the 2MB
    renderer source limit that the slide's own markup pushes it over, which is
    exactly how a real 9-slide run failed at the illustrate stage.
    """
    import io as _io

    from PIL import Image

    from utils.authored_illustrations import _recompress_illustration

    # A flat fill would compress to almost nothing as PNG and the helper would
    # correctly decline to "shrink" it; a detailed image is what the providers
    # actually return and what overran the budget.
    detailed = Image.effect_noise((1536, 1024), 48).convert("RGB")
    source = _io.BytesIO()
    detailed.save(source, "PNG")
    raw = source.getvalue()

    mime, payload = _recompress_illustration("image/png", raw)

    assert mime == "image/jpeg"
    assert len(payload) < len(raw)
    # The provider's own resolution survives: the export needs it, and the
    # format - not the pixel count - was what overran the budget.
    with Image.open(_io.BytesIO(payload)) as encoded:
        assert encoded.size == (1536, 1024)
    # base64 costs a third on top; the result must clear the renderer limit.
    assert len(payload) * 4 / 3 < 2 * 1024 * 1024


def test_an_enormous_source_still_hits_the_width_safety_valve() -> None:
    """The cap exists for a provider that returns something absurd, not as a target."""
    import io as _io

    from PIL import Image

    from utils.authored_illustrations import (
        _ILLUSTRATION_MAX_WIDTH,
        _recompress_illustration,
    )

    source = _io.BytesIO()
    Image.new("RGB", (4096, 2048), (250, 250, 250)).save(source, "PNG")

    _, payload = _recompress_illustration("image/png", source.getvalue())

    with Image.open(_io.BytesIO(payload)) as encoded:
        assert encoded.size[0] == _ILLUSTRATION_MAX_WIDTH


def test_recompression_keeps_the_original_when_it_cannot_help() -> None:
    """Unreadable bytes, or a source already smaller, must pass through intact."""
    from utils.authored_illustrations import _recompress_illustration

    garbage = b"not an image at all"
    assert _recompress_illustration("image/png", garbage) == ("image/png", garbage)


def test_markdown_markers_never_reach_the_label_source() -> None:
    """The model copies these strings literally, so the markers must be gone.

    A real run painted `## 조직 및 인력` into the picture with the hashes still
    attached, because the outline is markdown and the prompt demands a
    character-for-character copy.
    """
    from utils.authored_illustrations import _clean_slide_content

    cleaned = _clean_slide_content(
        "## 조직 및 인력\n- 기술인력 54명 보유\n\n* 6개 본부 전문조직\n1. 첫째\n"
    )

    assert cleaned.splitlines() == [
        "조직 및 인력",
        "기술인력 54명 보유",
        "6개 본부 전문조직",
        "첫째",
    ]
    assert "#" not in cleaned
    assert not any(line.startswith(("-", "*", "•")) for line in cleaned.splitlines())
