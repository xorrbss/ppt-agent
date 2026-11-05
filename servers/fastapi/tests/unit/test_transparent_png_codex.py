"""Transparent Codex assets use OAuth once and retain shared acceptance gates."""

from __future__ import annotations

import asyncio
import base64
from contextlib import asynccontextmanager
import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from PIL import Image, ImageDraw, ImageOps

from services import image_generation_codex_adapter as adapter
from services import image_generation_service as image_service
from services.generation_core.assets import infographic_assets, infographic_plan, offer
from services.generation_core.assets.icon_pack import provider as provider_mod
from services.generation_core.assets.icon_pack.scene import generate_scene_cutout
from services.generation_core.assets.icon_pack.service import resolve_icons
from services.generation_core.assets.icon_pack.store import IconPack
from utils.runtime_settings import RuntimeSettings


def _settings(provider="codex_image", **overrides):
    return RuntimeSettings.from_sources(environ={
        "IMAGE_PROVIDER": provider,
        "ENABLE_PARALLEL_IMAGE_GENERATION": "false",
        "GPT_IMAGE_2_5_QUALITY": "medium",
        **overrides,
    })


def _png(size=(1536, 1024), *, mode="RGBA", opaque=False, radii=(90, 90, 90), columns=3):
    image = Image.new(mode, size, (255, 255, 255) if mode == "RGB" else
                      ((255, 255, 255, 255) if opaque else (0, 0, 0, 0)))
    draw = ImageDraw.Draw(image)
    for index, radius in enumerate(radii):
        cx, cy = size[0] * (2 * index + 1) // (2 * columns), size[1] // 2
        draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius),
                     fill=(30, 94, 255))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def transport(monkeypatch):
    state = SimpleNamespace(png=_png(), requests=[], clients=[], admissions=[], receipts=[],
                            failure=None, status=200, effective_model=None)

    @asynccontextmanager
    async def admission(**kwargs):
        state.admissions.append(kwargs)
        yield

    async def record(**kwargs):
        state.receipts.append(kwargs)

    class Response:
        headers = {"content-type": "text/event-stream"}

        @property
        def status_code(self):
            return state.status

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def aiter_lines(self):
            if state.failure:
                raise state.failure
            event = {"type": "image_generation.completed",
                     "b64_json": base64.b64encode(state.png).decode("ascii")}
            if state.effective_model is not None:
                event["model"] = state.effective_model
            yield "data: " + json.dumps(event)
            yield ""

    class Client:
        def __init__(self, **kwargs):
            state.clients.append(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def stream(self, method, url, **kwargs):
            state.requests.append(httpx.Request(method, url, **kwargs))
            return Response()

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Codex must never construct an API-key client or read its key")

    async def ocr(_payload, **_kwargs):
        return SimpleNamespace(available=True, status="success", text="")

    monkeypatch.setattr(adapter.httpx, "AsyncClient", Client)
    monkeypatch.setattr(adapter, "ensure_fresh_codex_access_token", lambda _settings: "oauth-test")
    monkeypatch.setattr(adapter, "get_codex_account_id_env", lambda _settings: "account-test")
    monkeypatch.setattr(image_service, "provider_call_admission", admission)
    monkeypatch.setattr(image_service, "record_provider_call", record)
    monkeypatch.setattr(provider_mod, "AsyncOpenAI", forbidden)
    monkeypatch.setattr(provider_mod, "get_openai_api_key_env", forbidden)
    monkeypatch.setattr("utils.authored_ocr_quality.inspect_authored_ocr", ocr)
    monkeypatch.setattr(
        "services.generation_core.assets.icon_pack.service.lookup_reusable_icons",
        lambda *_args, **_kwargs: {},
    )
    return state


def test_codex_capability_reaches_asset_context(tmp_path):
    settings = _settings()
    assert provider_mod.transparent_model_for(settings) == adapter.CODEX_IMAGE_REQUESTED_MODEL
    assert provider_mod.TransparentPngProvider(settings).available
    context = infographic_assets.build_infographic_asset_context(
        style=SimpleNamespace(id="test", illustration_prompt=""), brand=SimpleNamespace(),
        image_service=SimpleNamespace(output_directory=str(tmp_path)), settings=settings,
    )
    assert context is not None and context.settings is settings
    assert infographic_assets.deck_has_picture_source(SimpleNamespace(), settings)
    disabled = _settings(DISABLE_IMAGE_GENERATION="true")
    assert provider_mod.transparent_model_for(disabled) is None


@pytest.mark.parametrize("size", ["1024x1024", "1536x1024", "1024x1536"])
def test_codex_transparent_request_uses_oauth_and_one_boundary(transport, tmp_path, size):
    transport.png = _png(tuple(int(value) for value in size.split("x")))
    path = asyncio.run(provider_mod.TransparentPngProvider(_settings()).generate(
        "transparent icons", str(tmp_path / "nested"), size=size, semantic_slot="image.icon_sheet",
    ))
    assert Path(path).read_bytes() == transport.png
    assert len(transport.requests) == len(transport.admissions) == len(transport.receipts) == 1
    request = transport.requests[0]
    payload = json.loads(request.content)
    assert str(request.url) == adapter.CODEX_IMAGE_ENDPOINT
    assert request.headers["authorization"] == "Bearer oauth-test"
    assert payload == {
        "model": adapter.CODEX_IMAGE_REQUESTED_MODEL, "prompt": "transparent icons", "n": 1,
        "size": size, "quality": "medium", "stream": True,
        "background": "transparent", "output_format": "png",
    }
    assert transport.clients[0]["follow_redirects"] is False
    assert transport.admissions == [{
        "provider": "codex", "model": "codex-image-default",
        "semantic_slot": "image.icon_sheet.codex", "is_retry": False,
        "provider_idempotency_capable": False,
    }]
    assert transport.receipts[0]["outcome"] == "succeeded"
    metadata = json.loads(Path(path).with_suffix(".metadata.json").read_text())
    assert metadata == {
        "provider": "codex", "authentication": "codex_oauth",
        "requested_model": adapter.CODEX_IMAGE_REQUESTED_MODEL,
        "effective_model": None, "model_verified": False,
    }


def test_ordinary_codex_request_bytes_and_metadata_are_unchanged(transport, tmp_path):
    path = asyncio.run(adapter.generate_codex_image("ordinary scene", str(tmp_path), _settings()))
    assert transport.requests[0].content == (
        b'{"model":"gpt-image-2.5-sunburst","prompt":"ordinary scene","n":1,'
        b'"size":"1536x1024","quality":"medium","stream":true}'
    )
    assert Path(path).read_bytes() == transport.png
    assert transport.admissions[0]["semantic_slot"] == "image.primary.codex"
    assert len(transport.admissions) == len(transport.receipts) == 1


def test_codex_attested_model_remains_separate_from_requested_model(transport, tmp_path):
    transport.effective_model = "attested-model"
    path = asyncio.run(provider_mod.TransparentPngProvider(_settings()).generate(
        "icons", str(tmp_path), size="1536x1024",
    ))
    metadata = json.loads(Path(path).with_suffix(".metadata.json").read_text())
    assert metadata["effective_model"] == "attested-model" and metadata["model_verified"] is True
    assert metadata["requested_model"] == adapter.CODEX_IMAGE_REQUESTED_MODEL
    assert transport.receipts[0]["model"] == "codex-image-default"


@pytest.mark.parametrize("failure", [httpx.ReadError("disconnected"), RuntimeError("uncertain")])
def test_codex_uncertain_failure_never_retries_or_falls_back(transport, tmp_path, failure):
    transport.failure = failure
    with pytest.raises(type(failure)):
        asyncio.run(provider_mod.TransparentPngProvider(_settings()).generate("icons", str(tmp_path)))
    assert len(transport.requests) == len(transport.admissions) == len(transport.receipts) == 1
    assert transport.receipts[0]["outcome"] == "failed"
    assert not list(tmp_path.glob("*.png"))


def test_codex_rate_limit_never_retries_or_falls_back(transport, tmp_path):
    transport.status = 429
    with pytest.raises(RuntimeError, match="HTTP 429"):
        asyncio.run(provider_mod.TransparentPngProvider(_settings()).generate("icons", str(tmp_path)))
    assert len(transport.requests) == len(transport.receipts) == 1
    assert transport.receipts[0]["outcome"] == "failed"


@pytest.mark.parametrize("model", [
    "gpt-image-2", "gpt-image-1.5", "gpt-image-2.5-flare", "gpt-image-2.5-sunburst",
])
def test_openai_request_is_unchanged(transport, monkeypatch, tmp_path, model):
    generate = AsyncMock(return_value=SimpleNamespace(data=[SimpleNamespace(
        b64_json=base64.b64encode(transport.png).decode("ascii"),
    )]))
    clients = []

    def client(**kwargs):
        clients.append(kwargs)
        return SimpleNamespace(images=SimpleNamespace(generate=generate))

    monkeypatch.setattr(provider_mod, "AsyncOpenAI", client)
    monkeypatch.setattr(provider_mod, "get_openai_api_key_env", lambda _settings: "api-test")
    monkeypatch.setattr(provider_mod, "current_provider_idempotency_key", lambda: "idempotency-test")
    settings = _settings(model)
    path = asyncio.run(provider_mod.TransparentPngProvider(settings).generate(
        "icons", str(tmp_path), size="1536x1024", semantic_slot="image.icon_sheet",
    ))
    generate.assert_awaited_once_with(
        model=model, prompt="icons", n=1, quality="medium", size="1536x1024",
        background="transparent", output_format="png",
        extra_headers={"Idempotency-Key": "idempotency-test"},
    )
    assert clients == [{"api_key": "api-test", "max_retries": 0,
                        "timeout": provider_mod.get_image_generation_timeout_seconds(settings)}]
    assert Path(path).read_bytes() == transport.png
    assert not transport.requests
    assert len(transport.admissions) == len(transport.receipts) == 1
    assert transport.admissions[0] == {
        "provider": "openai", "model": model, "semantic_slot": "image.icon_sheet.attempt-1",
        "is_retry": False, "provider_idempotency_capable": True,
    }


@pytest.mark.parametrize("provider", ["codex_image", "gpt-image-2"])
@pytest.mark.parametrize("parallel", ["true", "false"])
def test_transparent_provider_preserves_output_bytes_and_lock_modes(
    transport, monkeypatch, tmp_path, provider, parallel,
):
    # Both providers keep their output bytes even for a different requested size.
    generate = AsyncMock(return_value=SimpleNamespace(data=[SimpleNamespace(
        b64_json=base64.b64encode(transport.png).decode("ascii"),
    )]))
    if provider == "gpt-image-2":
        monkeypatch.setattr(provider_mod, "get_openai_api_key_env", lambda _settings: "api-test")
        monkeypatch.setattr(provider_mod, "AsyncOpenAI", lambda **_kwargs:
                            SimpleNamespace(images=SimpleNamespace(generate=generate)))
    entered = []

    @asynccontextmanager
    async def lock():
        entered.append(True)
        yield

    monkeypatch.setattr(provider_mod, "_get_image_generation_lock", lock)
    settings = _settings(provider, ENABLE_PARALLEL_IMAGE_GENERATION=parallel)
    path = asyncio.run(provider_mod.TransparentPngProvider(settings).generate("icons", str(tmp_path)))
    with Image.open(path) as image:
        assert image.size == (1536, 1024)
    assert Path(path).read_bytes() == transport.png
    assert len(entered) == (0 if parallel == "true" else 1)
    assert len(transport.admissions) == len(transport.receipts) == 1
    assert transport.receipts[0]["outcome"] == "succeeded"
    assert len(transport.requests) == (1 if provider == "codex_image" else 0)
    assert generate.await_count == (0 if provider == "codex_image" else 1)


@pytest.mark.parametrize("mode,opaque", [("RGB", False), ("RGBA", True)])
def test_codex_nontransparent_sheet_and_cutout_fail_closed(transport, tmp_path, mode, opaque):
    settings = _settings()
    transport.png = _png(mode=mode, opaque=opaque)
    result = asyncio.run(resolve_icons(["alpha", "beta", "gamma"], pack_key="test",
                                      settings=settings, app_data=str(tmp_path)))
    assert result.icons == {} and result.missing == ["alpha", "beta", "gamma"]
    assert not IconPack("test", app_data=str(tmp_path)).index_path.exists()
    transport.png = _png((1024, 1024), mode=mode, opaque=opaque)
    cutout = asyncio.run(generate_scene_cutout(
        takeaway="test", nodes=["alpha"], output_directory=str(tmp_path / "cutout"), settings=settings,
    ))
    assert cutout is None
    assert not list((tmp_path / "cutout").glob("*.png"))
    assert len(transport.requests) == 2


@pytest.mark.parametrize("size", [(1287, 1222), (1312, 1199), (1024, 1024)])
def test_codex_nonrequested_dimensions_preserve_a_valid_grid(transport, tmp_path, size):
    transport.png = _png(size)
    result = asyncio.run(resolve_icons(["alpha", "beta", "gamma"], pack_key="test",
                                      settings=_settings(), app_data=str(tmp_path)))
    assert len(result.icons) == 3 and result.missing == []
    assert result.sheets_generated == 1 and result.sheets_failed == 0
    assert len(transport.requests) == 1


@pytest.mark.parametrize("size", [(1287, 1222), (1312, 1199)])
def test_codex_wrong_grid_is_not_cached_at_actual_dimensions(transport, tmp_path, size):
    transport.png = _png(size, columns=2, radii=(200, 200))
    result = asyncio.run(resolve_icons(["alpha", "beta", "gamma"], pack_key="test",
                                      settings=_settings(), app_data=str(tmp_path)))
    assert result.icons == {} and result.missing == ["alpha", "beta", "gamma"]
    assert result.sheets_generated == 0
    assert not IconPack("test", app_data=str(tmp_path)).index_path.exists()
    assert len(transport.requests) == 1


@pytest.mark.parametrize("fixture", ["codex-adapter.png", "codex-explicit.png"])
def test_img1_real_codex_cutout_is_accepted(transport, tmp_path, fixture):
    # Reduced real IMG-1 OAuth PNGs retain their native alpha; no live request.
    source = Path(__file__).parent / "fixtures" / "codex_transparency" / fixture
    transport.png = source.read_bytes()
    with Image.open(source) as image:
        source_size = image.size
    cutout = asyncio.run(generate_scene_cutout(
        takeaway="document", nodes=["document"], output_directory=str(tmp_path), settings=_settings(),
    ))
    assert cutout is not None and (cutout.width, cutout.height) == source_size
    assert Path(cutout.path).read_bytes() == transport.png
    assert cutout.checks["transparency"]["passed"]
    assert len(transport.requests) == len(transport.admissions) == len(transport.receipts) == 1


@pytest.mark.parametrize("fixture,size", [
    ("codex-adapter.png", (1287, 1222)),
    ("codex-explicit.png", (1312, 1199)),
])
def test_img1_real_alpha_sheet_preserves_each_icon_aspect(transport, tmp_path, fixture, size):
    source = Path(__file__).parent / "fixtures" / "codex_transparency" / fixture
    assert source.stat().st_size < 100_000
    with Image.open(source) as image:
        icon = image.convert("RGBA")
    alpha = icon.getchannel("A")
    histogram = alpha.histogram()
    assert histogram[0] > 0 and sum(histogram[1:255]) > 0 and histogram[255] > 0

    def content_aspect(image):
        box = image.getchannel("A").point(lambda value: 255 if value >= 24 else 0).getbbox()
        assert box is not None
        return (box[2] - box[0]) / (box[3] - box[1])

    source_aspect = content_aspect(icon)
    cell_width = size[0] // 3
    icon = ImageOps.contain(icon, (cell_width - 64, size[1] - 64))
    sheet = Image.new("RGBA", size, (0, 0, 0, 0))
    for column in range(3):
        sheet.paste(icon, (column * cell_width + (cell_width - icon.width) // 2,
                           (size[1] - icon.height) // 2))
    buffer = io.BytesIO()
    sheet.save(buffer, format="PNG")
    transport.png = buffer.getvalue()
    result = asyncio.run(resolve_icons(["alpha", "beta", "gamma"], pack_key="test",
                                      settings=_settings(), app_data=str(tmp_path)))
    assert len(result.icons) == 3 and result.missing == []
    for entry in result.icons.values():
        with Image.open(entry.path) as output:
            assert content_aspect(output) == pytest.approx(source_aspect, rel=0.02)
    assert len(transport.requests) == len(transport.admissions) == len(transport.receipts) == 1


def test_ordinary_codex_dimensions_are_still_unrestricted(transport, tmp_path):
    transport.png = _png((1312, 1199))
    path = asyncio.run(adapter.generate_codex_image("scene", str(tmp_path), _settings()))
    assert Path(path).read_bytes() == transport.png


def test_codex_sheet_still_requires_uniform_extents(transport, tmp_path):
    transport.png = _png(radii=(10, 70, 200))
    result = asyncio.run(resolve_icons(["alpha", "beta", "gamma"], pack_key="test",
                                      settings=_settings(), app_data=str(tmp_path)))
    assert result.icons == {} and result.missing == ["alpha", "beta", "gamma"]
    assert not IconPack("test", app_data=str(tmp_path)).index_path.exists()
    assert len(transport.requests) == 1


def test_offer_never_asks_the_codex_provider_for_an_icon_sheet(transport, tmp_path, monkeypatch):
    # e5fbc818f: icons come only from the deck's family picks and the bundled
    # vector library; an image provider (here the transparent Codex one) is
    # left to scenes, so a figure-only plan spends no provider call.
    from models.authored_infographic_plan import (
        AuthoredInfographicPlan, AuthoredSlideInfographic, InfographicNode,
    )

    labels = ["alpha", "beta", "gamma"]
    plan = AuthoredInfographicPlan(slides=(AuthoredSlideInfographic(
        index=0, archetype="process", weight="hero",
        nodes=tuple(InfographicNode(label=label) for label in labels),
    ),))
    monkeypatch.setattr(infographic_plan, "resolve_infographic_plan", AsyncMock(return_value=plan))
    monkeypatch.setattr("services.generation_core.assets.icon_pack.store.get_app_data_directory_env",
                        lambda: str(tmp_path))
    result = asyncio.run(offer.build_asset_offer(
        outline=SimpleNamespace(slides=[SimpleNamespace(content="alpha beta gamma")]),
        brand=SimpleNamespace(primary="#123456"), style=SimpleNamespace(id="test"),
        roles=["content"], instructions=None, budget=1, timeout_seconds=5,
        image_service=SimpleNamespace(output_directory=str(tmp_path / "images")), settings=_settings(),
    ))
    assert result.receipt["transparentProvider"] is True
    assert result.receipt["providerCalls"] == 0
    assert not {slot.concept for slot in result.slots} & set(labels)
    assert not transport.requests and not transport.admissions and not transport.receipts
