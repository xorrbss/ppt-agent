import asyncio
import base64
import logging
from pathlib import Path
from types import SimpleNamespace

from utils import authored_illustrations as illustrations_mod
from utils.authored_illustrations import apply_authored_illustrations
from utils.authored_styles import load_authored_styles
from utils.llm_calls.author_slide import Brand, build_design_system

_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNgYAAAAAMAASsJTYQAAAAASUVORK5CYII="
)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


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


def test_no_illustration_style_returns_htmls_unchanged():
    service = _Service(None)
    htmls = [_SLOT_HTML]
    out = _run(
        apply_authored_illustrations(
            htmls, SimpleNamespace(id="default", illustration_prompt=None), service
        )
    )
    assert out == htmls
    assert service.prompts == []


def test_slot_is_inlined_as_data_uri_with_style_theme_prompt(tmp_path: Path):
    image = tmp_path / "illust.png"
    image.write_bytes(_PNG_BYTES)
    service = _Service(SimpleNamespace(path=str(image)))

    out = _run(apply_authored_illustrations([_SLOT_HTML], _style(), service))

    expected_uri = "data:image/png;base64," + base64.b64encode(_PNG_BYTES).decode()
    assert expected_uri in out[0]
    assert 'style="width:640px;height:480px"' in out[0]  # layout attrs preserved
    assert "<h1>HITL 통제</h1>" in out[0]
    # Scene prompt is model-authored; style prompt rides along as the theme and
    # the no-text rule is always appended.
    sent = service.prompts[0]
    assert "fingerprint gate" in sent.prompt
    assert "no text" in sent.prompt.lower()
    assert sent.theme_prompt == _style().illustration_prompt


def test_failure_placeholder_and_oversize_drop_the_slot(tmp_path: Path, caplog):
    big = tmp_path / "big.png"
    big.write_bytes(_PNG_BYTES + b"0" * (12 * 1024 * 1024))
    cases = [
        _Service(RuntimeError("provider down")),
        _Service("http://localhost/static/images/placeholder.jpg"),  # str = failure
        _Service(SimpleNamespace(path=str(tmp_path / "missing.png"))),
        _Service(SimpleNamespace(path=str(big))),
    ]
    for service in cases:
        with caplog.at_level(logging.WARNING):
            out = _run(apply_authored_illustrations([_SLOT_HTML], _style(), service))
        assert "<img" not in out[0]
        assert "<h1>HITL 통제</h1>" in out[0] and "라벨은 HTML" in out[0]


def test_only_first_slot_is_filled_and_extras_are_removed(tmp_path: Path):
    image = tmp_path / "illust.png"
    image.write_bytes(_PNG_BYTES)
    service = _Service(SimpleNamespace(path=str(image)))
    html = (
        '<img data-illust-prompt="scene one">'
        '<img data-illust-prompt="scene two">'
        "<p>본문</p>"
    )

    out = _run(apply_authored_illustrations([html], _style(), service))

    assert out[0].count("<img") == 1
    assert len(service.prompts) == 1
    assert "scene one" in service.prompts[0].prompt


def test_slides_without_slots_pass_through_untouched(tmp_path: Path):
    service = _Service(SimpleNamespace(path=str(tmp_path / "unused.png")))
    plain = "<html><body><p>일반 슬라이드</p></body></html>"

    out = _run(apply_authored_illustrations([plain], _style(), service))

    assert out == [plain]
    assert service.prompts == []


def test_illustration_limit_is_shared_across_concurrent_decks(monkeypatch, tmp_path):
    image = tmp_path / "illust.png"
    image.write_bytes(_PNG_BYTES)

    class TrackingService:
        def __init__(self):
            self.active = 0
            self.max_active = 0

        async def generate_image(self, prompt):
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            await asyncio.sleep(0.01)
            self.active -= 1
            return SimpleNamespace(path=str(image))

    service = TrackingService()
    monkeypatch.setattr(illustrations_mod, "ILLUSTRATION_CONCURRENCY", 1)

    async def scenario():
        return await asyncio.gather(
            apply_authored_illustrations([_SLOT_HTML], _style(), service),
            apply_authored_illustrations([_SLOT_HTML], _style(), service),
        )

    results = _run(scenario())

    assert service.max_active == 1
    assert all("data:image/png;base64," in deck[0] for deck in results)


def test_design_system_gains_slot_rules_only_for_illustration_styles():
    brand = Brand(topic="주제")
    without = build_design_system(
        brand, SimpleNamespace(id="editorial", brief="- Editorial")
    )
    with_slot = build_design_system(
        brand,
        SimpleNamespace(
            id="iso", brief="- Iso", illustration_prompt="isometric style"
        ),
    )

    assert "ILLUSTRATION SLOT" not in without
    assert "ILLUSTRATION SLOT" in with_slot
    assert "data-illust-prompt" in with_slot


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
