"""Unit tests for the authored generation pipeline (no real LLM / no real Chrome).

Covers the resilience guarantees that keep authored generation from aborting on one
bad slide: HTML validity gating, branded fallback, per-slide author retry/fallback,
per-slide render placeholder, style-brief propagation, image-PPTX assembly, the
authored-deck predicate, and brand resolution."""

import asyncio
import io
import uuid
from types import SimpleNamespace

from PIL import Image
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel

from models.generate_presentation_request import GeneratePresentationRequest
from models.presentation_outline_model import (
    PresentationOutlineModel,
    SlideOutlineModel,
)
from models.sql.presentation import PresentationModel
from utils import slide_capture


def _run(coro):
    return asyncio.run(coro)


_VALID = (
    "<!DOCTYPE html><html lang='ko'><head><meta charset='utf-8'></head>"
    "<body><h1>제목</h1></body></html>"
)
_CANONICAL_VALID = _VALID.replace(
    "<meta charset='utf-8'>",
    '<meta charset="utf-8">',
)
# author_deck seals the render-bound owner namespace on every accepted slide
# before the header and capacity contracts are checked: each rendered element
# gets its stable data-qid and the derived data-semantic-role. A valid provider
# result therefore comes back annotated rather than byte-identical.
_SEALED_VALID = _VALID.replace(
    "<h1>제목</h1>",
    '<h1 data-qid="q-h1-94f712e4878d" data-semantic-role="title">제목</h1>',
)


def _outline(n):
    return PresentationOutlineModel(
        slides=[SlideOutlineModel(content=f"슬라이드 {i} 내용") for i in range(n)]
    )


async def _sqlite_engine(tmp_path):
    # Authored publication seals the semantic deck graph against the persisted
    # presentation row it just wrote, so the service needs a real session.
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'authored-service.db'}"
    )
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
    return engine


# --- HTML validity + fallback ------------------------------------------------


# --- author_deck resilience (mocked LLM) -------------------------------------


def test_temporary_authored_render_preserves_sub_12px_source_css(monkeypatch):
    captured = []

    async def fake_uncached(html, timeout=60, **_kwargs):
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


def test_authored_render_cache_identity_version_separates_stale_entries(monkeypatch):
    # The in-process render LRU is keyed by the cache identity digest, whose
    # version string is the invalidation contract: bumping it must never serve
    # pixels rendered under the previous identity.
    calls = 0

    async def fake_uncached(html, timeout=60, **_kwargs):
        nonlocal calls
        calls += 1
        return f"render-{calls}".encode()

    slide_capture._render_cache.clear()
    slide_capture._render_inflight.clear()
    monkeypatch.setattr(slide_capture, "_render_html_to_png_uncached", fake_uncached)
    monkeypatch.setattr(
        slide_capture, "CACHE_IDENTITY_VERSION", "authored-render-cache-identity/v1"
    )
    assert _run(slide_capture.render_html_to_png(_VALID)) == b"render-1"
    assert _run(slide_capture.render_html_to_png(_VALID)) == b"render-1"

    monkeypatch.setattr(
        slide_capture,
        "CACHE_IDENTITY_VERSION",
        "authored-render-cache-identity/v2-capability-registry",
    )
    assert _run(slide_capture.render_html_to_png(_VALID)) == b"render-2"
    assert calls == 2


# --- image reference intent + multimodal resilience ------------------------


def _reference_image(tmp_path):
    path = tmp_path / "reference.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\nreference")
    return path


def _author_user_content(call):
    return call["messages"][1].content


def test_generate_request_accepts_optional_authored_style():
    assert GeneratePresentationRequest(content="topic").authored_style is None
    request = GeneratePresentationRequest(content="topic", authored_style="editorial")
    assert request.authored_style == "editorial"
    assert request.model_dump()["authored_style"] == "editorial"


def _illustration_style():
    return SimpleNamespace(
        id="editorial",
        brief="- Editorial",
        illustration_prompt="editorial illustration",
        reference_mode="style_reference",
    )


def _reauthored_illustration_slot_html():
    return _VALID.replace(
        "</body>",
        '<img data-illust-prompt="an abstract skyline" '
        'style="width:200px;height:100px"></body>',
    )


# --- render resilience (mocked Chrome) ---------------------------------------


def test_find_chrome_prefers_playwright_headless_shell(monkeypatch, tmp_path):
    import json
    import os
    from pathlib import Path
    import subprocess
    import sys

    from services.isolated_worker_process import worker_environment
    from services.trusted_executor.contracts import (
        ExecutionIdentity, ResourceLimits, SandboxLaunchSpec, SandboxPolicy,
    )

    # A host-level PLAYWRIGHT_BROWSERS_PATH (CI images, cloud VMs) would rank its
    # own, newer headless shell above the one this test provisions.
    for name in (
        "CHROME_PATH", "PUPPETEER_EXECUTABLE_PATH", "CHROMIUM_PATH", "PLAYWRIGHT_BROWSERS_PATH",
    ):
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
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)

    def home_unavailable(cls):
        raise RuntimeError("Could not determine home directory.")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "home", classmethod(home_unavailable))
        assert slide_capture.find_chrome() == str(shell)
        explicit = tmp_path / "explicit-chrome.exe"
        explicit.write_bytes(b"explicit browser")
        patch.setenv("CHROME_PATH", str(explicit))
        assert slide_capture.find_chrome() == str(explicit)
        patch.delenv("CHROME_PATH")
        patch.setenv("LOCALAPPDATA", str(tmp_path / "absent"))
        fixed = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
        patch.setattr(os.path, "isfile", lambda value: value == fixed)
        assert slide_capture.find_chrome() == fixed
        patch.setattr(os.path, "isfile", lambda value: False)
        assert slide_capture.find_chrome() is None

    # Run only discovery in a real child with the product's two env filters.
    # The fixture executable is never started; no browser/profile is created.
    source = {**os.environ, "PUBLIC_TRUSTED_WORKER_LOCAL_POLICY": "development"}
    spec = SandboxLaunchSpec(
        identity=ExecutionIdentity(
            operation_id=uuid.uuid4(), tenant_id="discovery-test",
            owner_id=uuid.uuid4(), presentation_id=uuid.uuid4(),
        ),
        role="render", executable=Path(sys.executable), arguments=("-c", "discovery"),
        limits=ResourceLimits(memory_bytes=536870912, process_limit=32, deadline_seconds=30),
        policy=SandboxPolicy(),
    )
    environment = spec.environment(worker_environment("render", source, trusted_dispatch=True))
    assert all(key not in environment for key in ("HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH"))
    completed = subprocess.run(
        [sys.executable, "-c", "import json; from utils.slide_capture_chrome_discovery import find_chrome; print(json.dumps(find_chrome()))"],
        env=environment, capture_output=True, text=True, timeout=20, check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert json.loads(completed.stdout) == str(shell)


def test_render_html_cache_reuses_successful_identical_render(monkeypatch):
    calls = 0
    rendered = _png_1280x720((12, 34, 56))

    async def fake_uncached(html, timeout=60, **_kwargs):
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


# --- image PPTX assembly -----------------------------------------------------


def _png_1280x720(color):
    img = Image.new("RGB", (1280, 720), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# --- authored predicate + roles + brand --------------------------------------


def test_is_authored_predicate():
    by_theme = PresentationModel(
        content="x", n_slides=1, language="ko", theme={"mode": "authored"},
        layout={"name": "ignored"},
    )
    without_mode_or_sentinel = PresentationModel(content="x", n_slides=1, language="ko")
    templated = PresentationModel(
        content="x", n_slides=1, language="ko", layout={"name": "adaptive"},
    )
    assert by_theme.is_authored() is True
    # A missing React layout is no longer an authored sentinel: the mode column
    # is backfilled for legacy rows and only the theme sentinel still counts.
    assert without_mode_or_sentinel.is_authored() is False
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


# --- E4 (docs/generation-programme-master-design-20260904.md section 8):
# a slide whose authored HTML needs A2 normalisation carries the fired rules
# in its own ``slide_quality_receipts`` entry. --------------------------------


# ---------------------------------------------------------------------------
# A footer-only deck publishes without spending a remake
#
# ``text_intrudes_footer_zone`` is detected and recorded but advisory
# (``utils/authored_dom_categories.py`` carries the measured reason). The
# pipeline seam where that has to hold is ``_evaluate_slide_quality``: it is
# what turns a DOM report into a gate FAILURE, and a gate failure is what
# flags the slide, feeds the remake-first round and spends the deck's shared
# remake ceiling. A footer-only slide must produce no failure at all.
# ---------------------------------------------------------------------------


def _footer_zone_fixture(name: str) -> dict:
    import json
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "authored_dom_text_escape"
        / f"{name}.metrics.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))
