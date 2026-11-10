"""Provider-dependent text rules for authored illustration prompts.

Some image models (verified: ``gpt-image-2``) render Korean labels correctly;
Gemini-based and unknown backends do not. These tests pin the capability table,
the default-deny behaviour, and the fact that any label the model may draw must
come verbatim from the slide's own content.
"""

import asyncio
import base64
from pathlib import Path
from types import SimpleNamespace

import pytest

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


# --- failure behaviour is unchanged ----------------------------------------
