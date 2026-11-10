import asyncio
import base64
from pathlib import Path
from types import SimpleNamespace

from utils.authored_styles import load_authored_styles

_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNgYAAAAAMAASsJTYQAAAAASUVORK5CYII="
)


def _run(coro):
    return asyncio.run(coro)


class _Service:
    """Scriptable stand-in for ImageGenerationService."""

    def __init__(self, result):
        self.result = result
        self.prompts = []

    async def generate_image(self, prompt):
        self.prompts.append(prompt)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _style(prompt="clean isometric 3D illustration, blue and orange"):
    return SimpleNamespace(id="iso-blueprint", illustration_prompt=prompt)


_SLOT_HTML = (
    "<html><body><h1>HITL 통제</h1>"
    '<img data-illust-prompt="isometric pipeline with a fingerprint gate" '
    'style="width:640px;height:480px">'
    "<p>라벨은 HTML</p></body></html>"
)


def test_loader_parses_illustration_style_prompt(tmp_path: Path):
    (tmp_path / "iso.yaml").write_text(
        "id: iso\nname: 아이소\ndescription: D\npreview:\n  bg: '#FFFFFF'\n"
        "  accent: '#FF6D00'\nbrief: B\nillustration:\n  style_prompt: isometric 3d\n",
        encoding="utf-8",
    )
    (tmp_path / "flat.yaml").write_text(
        "id: flat\nname: 플랫\ndescription: D\npreview:\n  bg: '#FFFFFF'\n"
        "  accent: '#0055FF'\nbrief: B\n",
        encoding="utf-8",
    )

    styles = {s.id: s for s in load_authored_styles(tmp_path)}

    assert styles["iso"].illustration_prompt == "isometric 3d"
    assert styles["flat"].illustration_prompt is None
