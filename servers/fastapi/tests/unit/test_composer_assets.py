"""``services.composer.assets``: marker substitution and the two resolvers.

Covers the Composer image-marker defect directly: a renderer-emitted
``data-asset-id`` marker with no ``src`` must become an admitted ``<img>``
before it ever reaches Chromium measurement, publication, or the pipeline's
own asset-inlining seam (``AssetInliningMeasurer`` / ``inline_result_assets``,
exercised end to end in ``test_composer_pipeline.py``).
"""

from __future__ import annotations

import asyncio
import base64
from types import SimpleNamespace
from typing import Any

import pytest

from services.composer.assets import (
    ComposerAssetError,
    in_memory_asset_resolver,
    inline_source_images,
    manifest_asset_resolver,
    preload_deck_assets,
    resolve_deck_assets,
    substitute_resolved_assets,
)
from services.composer.plan import ComposerComponent, ComposerDeckPlan, ComposerSlidePlan, ComposerTokens
from utils.renderer_isolation import RendererPolicyError, admit_authored_html

#: A real, minimally valid 1x1 transparent PNG (PIL must decode it -- the
#: admission gate opens every image it admits).
_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY"
    "42YAAAAASUVORK5CYII="
)
_JPEG_SIGNATURE = b"\xff\xd8\xff\xe0" + b"\x00" * 32


def _document(body: str) -> str:
    return f'<html><body style="width:1280px;height:720px;margin:0;overflow:hidden">{body}</body></html>'


def _marker(asset_id: str = "asset-1") -> str:
    return f'<img data-asset-id="{asset_id}" data-qid="q1" data-semantic-role="image" alt="">'


def _run(coro):
    return asyncio.run(coro)


# --- substitute_resolved_assets / inline_source_images -----------------------


def test_inline_source_images_replaces_marker_with_admitted_data_url() -> None:
    html = _document(_marker())

    async def resolver(asset_id: str) -> bytes | None:
        assert asset_id == "asset-1"
        return _PNG_1X1

    admitted_html, unresolved = _run(inline_source_images(html, resolver))

    assert unresolved == []
    assert "data-asset-id" not in admitted_html
    assert 'data-qid="q1"' in admitted_html
    assert 'data-semantic-role="image"' in admitted_html
    expected_src = f'src="data:image/png;base64,{base64.b64encode(_PNG_1X1).decode("ascii")}"'
    assert expected_src in admitted_html

    # The whole point: the document Chromium/export would have rejected now
    # passes the same admission policy they run.
    admitted = admit_authored_html(admitted_html)
    assert admitted.html


def test_inline_source_images_reports_unresolved_ids_and_leaves_marker() -> None:
    html = _document(_marker("missing-asset"))

    async def resolver(asset_id: str) -> bytes | None:
        return None

    result_html, unresolved = _run(inline_source_images(html, resolver))

    assert unresolved == ["missing-asset"]
    assert 'data-asset-id="missing-asset"' in result_html
    assert "src=" not in result_html
    with pytest.raises(RendererPolicyError):
        admit_authored_html(result_html)


def test_inline_source_images_is_a_no_op_without_markers() -> None:
    html = _document("<p data-qid=\"q1\" data-semantic-role=\"body\">text</p>")

    async def resolver(asset_id: str) -> bytes | None:
        raise AssertionError("resolver must not be called when no marker is present")

    result_html, unresolved = _run(inline_source_images(html, resolver))

    assert result_html == html
    assert unresolved == []


def test_substitute_resolved_assets_detects_jpeg_mime() -> None:
    html = _document(_marker("asset-jpeg"))

    result_html, unresolved = substitute_resolved_assets(html, {"asset-jpeg": _JPEG_SIGNATURE})

    assert unresolved == []
    assert "data:image/jpeg;base64," in result_html


def test_substitute_resolved_assets_unrecognized_bytes_are_unresolved() -> None:
    html = _document(_marker("asset-bad"))

    result_html, unresolved = substitute_resolved_assets(html, {"asset-bad": b"not-an-image"})

    assert unresolved == ["asset-bad"]
    assert 'data-asset-id="asset-bad"' in result_html


def test_substitute_resolved_assets_keeps_multiple_markers_independent() -> None:
    html = _document(_marker("asset-1") + _marker("asset-2"))

    result_html, unresolved = substitute_resolved_assets(html, {"asset-1": _PNG_1X1})

    assert unresolved == ["asset-2"]
    assert result_html.count("data:image/png;base64,") == 1
    assert 'data-asset-id="asset-2"' in result_html
    assert 'data-asset-id="asset-1"' not in result_html


# --- ComposerAssetError -------------------------------------------------------


def test_composer_asset_error_carries_slide_index_and_ids() -> None:
    error = ComposerAssetError(3, ["asset-a", "asset-b"])

    assert error.slide_index == 3
    assert error.asset_ids == ("asset-a", "asset-b")
    assert "asset-a" in str(error)
    assert "slide 3" in str(error)


# --- in_memory_asset_resolver --------------------------------------------------


def test_in_memory_asset_resolver_resolves_known_and_unknown_ids() -> None:
    resolver = in_memory_asset_resolver({"asset-1": _PNG_1X1})

    assert _run(resolver("asset-1")) == _PNG_1X1
    assert _run(resolver("asset-unknown")) is None


# --- manifest_asset_resolver ---------------------------------------------------


def test_manifest_asset_resolver_returns_sealed_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    from services.source_content import manifest_store

    seen: dict[str, Any] = {}

    async def fake_load(session, *, owner_id, manifest_id, asset_id):
        seen.update(session=session, owner_id=owner_id, manifest_id=manifest_id, asset_id=asset_id)
        return SimpleNamespace(payload=_PNG_1X1, media_type="image/png")

    monkeypatch.setattr(manifest_store, "load_source_content_manifest_asset", fake_load)

    manifest = SimpleNamespace(manifest_id="manifest-1")
    resolver = manifest_asset_resolver(manifest, owner_id="owner-1", session="session-1")

    payload = _run(resolver("asset-1"))

    assert payload == _PNG_1X1
    assert seen == {
        "session": "session-1", "owner_id": "owner-1", "manifest_id": "manifest-1", "asset_id": "asset-1",
    }


def test_manifest_asset_resolver_returns_none_on_store_miss(monkeypatch: pytest.MonkeyPatch) -> None:
    from services.source_content import manifest_store
    from services.source_content.errors import MANIFEST_INVALID, SourceContentError

    async def fake_load(session, *, owner_id, manifest_id, asset_id):
        raise SourceContentError(MANIFEST_INVALID, status_code=404)

    monkeypatch.setattr(manifest_store, "load_source_content_manifest_asset", fake_load)

    manifest = SimpleNamespace(manifest_id="manifest-1")
    resolver = manifest_asset_resolver(manifest, owner_id="owner-1", session="session-1")

    assert _run(resolver("asset-1")) is None


# --- resolve_deck_assets / preload_deck_assets ---------------------------------
#
# Preloading every deck image into memory on the main loop, before any slide
# is handed to a fit-ladder worker thread (services.composer.pipeline_fit):
# those threads must never open a database session.


def _tokens() -> ComposerTokens:
    return ComposerTokens(
        background="#FFFFFF", surface="#F4F6F8", text_primary="#111827", text_secondary="#4B5563",
        accent="#0F62FE", accent_text="#0F62FE", accent_soft="#D0E2FF", rule="#E5E7EB",
    )


def _image_component(component_id: str, asset_id: str) -> ComposerComponent:
    return ComposerComponent(component_id=component_id, kind="image", semantic_role="image", asset_id=asset_id)


def _deck(images_by_slide: list[list[str]]) -> ComposerDeckPlan:
    slides = [
        ComposerSlidePlan(
            slide_index=index,
            source_unit_id=f"u{index}",
            archetype="text",
            images=[_image_component(f"img-{index}-{i}", asset_id) for i, asset_id in enumerate(ids)],
        )
        for index, ids in enumerate(images_by_slide)
    ]
    return ComposerDeckPlan(slides=slides, tokens=_tokens())


def test_resolve_deck_assets_resolves_each_referenced_id_once() -> None:
    deck = _deck([["asset-1", "asset-2"], ["asset-1"], []])
    calls: list[str] = []

    async def resolver(asset_id: str) -> bytes | None:
        calls.append(asset_id)
        return {"asset-1": _PNG_1X1, "asset-2": _JPEG_SIGNATURE}.get(asset_id)

    resolved = _run(resolve_deck_assets(deck, resolver))

    assert resolved == {"asset-1": _PNG_1X1, "asset-2": _JPEG_SIGNATURE}
    # slide 1 repeats asset-1, already resolved -- the resolver is not called again.
    assert calls == ["asset-1", "asset-2"]


def test_resolve_deck_assets_ignores_slides_without_images() -> None:
    deck = _deck([[], []])

    async def resolver(asset_id: str) -> bytes | None:
        raise AssertionError("resolver must not be called when no image references an asset id")

    assert _run(resolve_deck_assets(deck, resolver)) == {}


def test_resolve_deck_assets_raises_for_the_first_slide_with_missing_bytes() -> None:
    deck = _deck([["asset-1"], ["asset-missing"], ["asset-3"]])
    calls: list[str] = []

    async def resolver(asset_id: str) -> bytes | None:
        calls.append(asset_id)
        return None if asset_id == "asset-missing" else _PNG_1X1

    with pytest.raises(ComposerAssetError) as raised:
        _run(resolve_deck_assets(deck, resolver))

    assert raised.value.slide_index == 1
    assert raised.value.asset_ids == ("asset-missing",)
    # The third slide's asset is never requested: this fails before fitting,
    # not after walking every slide.
    assert calls == ["asset-1", "asset-missing"]


def test_preload_deck_assets_reuses_manifest_asset_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    from services.source_content import manifest_store

    seen: list[dict[str, Any]] = []

    async def fake_load(session, *, owner_id, manifest_id, asset_id):
        seen.append({"owner_id": owner_id, "manifest_id": manifest_id, "asset_id": asset_id})
        return SimpleNamespace(payload=_PNG_1X1, media_type="image/png")

    monkeypatch.setattr(manifest_store, "load_source_content_manifest_asset", fake_load)

    manifest = SimpleNamespace(manifest_id="manifest-1")
    deck = _deck([["asset-1"], ["asset-1", "asset-2"]])

    resolved = _run(preload_deck_assets(deck, manifest, "owner-1", "session-1"))

    assert resolved == {"asset-1": _PNG_1X1, "asset-2": _PNG_1X1}
    assert [item["asset_id"] for item in seen] == ["asset-1", "asset-2"]
    assert all(item["owner_id"] == "owner-1" and item["manifest_id"] == "manifest-1" for item in seen)


def test_preload_deck_assets_raises_composer_asset_error_before_fitting(monkeypatch: pytest.MonkeyPatch) -> None:
    from services.source_content import manifest_store
    from services.source_content.errors import MANIFEST_INVALID, SourceContentError

    async def fake_load(session, *, owner_id, manifest_id, asset_id):
        raise SourceContentError(MANIFEST_INVALID, status_code=404)

    monkeypatch.setattr(manifest_store, "load_source_content_manifest_asset", fake_load)

    manifest = SimpleNamespace(manifest_id="manifest-1")
    deck = _deck([["asset-missing"]])

    with pytest.raises(ComposerAssetError) as raised:
        _run(preload_deck_assets(deck, manifest, "owner-1", "session-1"))

    assert raised.value.slide_index == 0
    assert raised.value.asset_ids == ("asset-missing",)
