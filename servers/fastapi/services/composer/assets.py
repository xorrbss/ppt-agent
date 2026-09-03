"""Resolve ``data-asset-id`` image markers to admitted inline data URLs.

The Composer renderer places a source image as
``<img data-asset-id="..." data-qid="..." data-semantic-role="image">`` with
no ``src`` -- the same authoring-only marker convention ordinary authored
generation uses (``utils.authored_image_slots``). ``render_slide`` validates
an admission projection with the marker stripped (``admission_projection`` in
``services.composer.render``), but nothing before this module resolves the
marker to an actual ``src``, so the raw HTML it returns still fails
``utils.renderer_isolation.admit_authored_html`` -- ``data-asset-id`` is not
an admitted attribute -- the moment any Chromium measurement or export sees
it unresolved. ``inline_source_images`` is the single place that substitutes
the asset bytes and drops the marker, the composer analogue of how
``utils.llm_calls.author_slide.canonicalize_authored_slide_html`` finalizes
an authored slide's image slots.
"""

from __future__ import annotations

import base64
import re
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from services.composer.contracts import Measurement, Measurer
from services.composer.plan import ComposerDeckPlan, ComposerSlidePlan, ComposerSlideResult

#: One ``<img ...>`` start tag, non-greedy up to its closing ``>``.
_IMG_TAG = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_ASSET_ID_ATTRIBUTE = re.compile(r'\sdata-asset-id="([^"]*)"', re.IGNORECASE)
_SRC_ATTRIBUTE = re.compile(r'\ssrc="[^"]*"', re.IGNORECASE)

#: Magic-byte prefixes for the raster types ``admit_authored_html`` accepts
#: (``utils.renderer_isolation._ALLOWED_IMAGE_TYPES``). WEBP is checked
#: separately below since its signature spans two non-contiguous windows.
_MIME_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


class AssetResolver(Protocol):
    """Asset id -> bytes, or ``None`` when this resolver cannot produce them."""

    async def __call__(self, asset_id: str) -> bytes | None: ...


class ComposerAssetError(RuntimeError):
    """A ``data-asset-id`` marker referenced bytes the resolver could not produce.

    Raised by the pipeline, never swallowed into a blank image: a source
    picture the deck cannot resolve is a data problem, not a layout one.
    """

    code = "composer_asset_unresolved"

    def __init__(self, slide_index: int, asset_ids: Sequence[str]) -> None:
        detail = "unresolved source image asset id(s): " + ", ".join(asset_ids)
        super().__init__(f"slide {slide_index}: {detail}")
        self.slide_index = slide_index
        self.asset_ids = tuple(asset_ids)
        self.detail = detail


def _sniff_mime(payload: bytes) -> str | None:
    """The one raster MIME type ``admit_authored_html`` would accept for these bytes."""

    for signature, mime in _MIME_SIGNATURES:
        if payload.startswith(signature):
            return mime
    if payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        return "image/webp"
    return None


def _asset_ids(html: str) -> list[str]:
    """Every ``data-asset-id`` value in document order, each listed once."""

    seen: set[str] = set()
    ids: list[str] = []
    for tag in _IMG_TAG.finditer(html):
        match = _ASSET_ID_ATTRIBUTE.search(tag.group(0))
        if match is None:
            continue
        asset_id = match.group(1)
        if asset_id and asset_id not in seen:
            seen.add(asset_id)
            ids.append(asset_id)
    return ids


def substitute_resolved_assets(html: str, resolved: Mapping[str, bytes]) -> tuple[str, list[str]]:
    """Rewrite every marker ``resolved`` covers; report the rest unresolved.

    Pure and synchronous: ``resolved`` is already-fetched bytes, so this is
    the core both ``inline_source_images`` (below) and a caller holding a
    pre-resolved byte map can share.
    """

    unresolved: list[str] = []

    def replace(match: re.Match[str]) -> str:
        tag = match.group(0)
        asset_match = _ASSET_ID_ATTRIBUTE.search(tag)
        if asset_match is None:
            return tag
        asset_id = asset_match.group(1)
        payload = resolved.get(asset_id)
        mime = _sniff_mime(payload) if payload else None
        if not payload or mime is None:
            unresolved.append(asset_id)
            return tag
        data_uri = f"data:{mime};base64,{base64.b64encode(payload).decode('ascii')}"
        without_marker = _ASSET_ID_ATTRIBUTE.sub("", tag, count=1)
        without_src = _SRC_ATTRIBUTE.sub("", without_marker)
        return without_src.replace("<img", f'<img src="{data_uri}"', 1)

    return _IMG_TAG.sub(replace, html), unresolved


async def inline_source_images(html: str, resolver: AssetResolver) -> tuple[str, list[str]]:
    """Resolve every marker in ``html`` through ``resolver`` and substitute it.

    Returns the admitted HTML and the asset ids ``resolver`` could not
    produce, in document order. Never raises on its own -- a missing id is
    reported so the caller decides; the pipeline turns a non-empty list into
    ``ComposerAssetError``.
    """

    resolved: dict[str, bytes] = {}
    for asset_id in _asset_ids(html):
        payload = await resolver(asset_id)
        if payload:
            resolved[asset_id] = payload
    return substitute_resolved_assets(html, resolved)


def manifest_asset_resolver(manifest: Any, *, owner_id: Any, session: Any) -> AssetResolver:
    """Resolve against the DB-backed sealed manifest asset store.

    Reuses ``services.source_content.manifest_store.load_source_content_manifest_asset``
    -- the same owner-scoped lookup ``services.authored_source_images`` uses
    for the ordinary authored source-image path against
    ``models.sql.source_content.SourceContentManifestAsset`` -- so this adds
    no new storage code. A miss (wrong owner, unknown id, unsealed manifest)
    resolves to ``None`` rather than raising: the pipeline decides whether
    that is fatal.
    """

    manifest_id = str(getattr(manifest, "manifest_id", ""))

    async def resolve(asset_id: str) -> bytes | None:
        from services.source_content.errors import SourceContentError
        from services.source_content.manifest_store import load_source_content_manifest_asset

        try:
            asset = await load_source_content_manifest_asset(
                session, owner_id=owner_id, manifest_id=manifest_id, asset_id=asset_id,
            )
        except SourceContentError:
            return None
        return bytes(asset.payload)

    return resolve


def in_memory_asset_resolver(mapping: Mapping[str, bytes]) -> AssetResolver:
    """Resolve against an already-loaded byte map: tests and the offline harness.

    ``services.source_content.pptx.extract_pptx_source_content`` returns an
    ``ExtractedSourceContent`` whose ``.assets`` is a tuple of
    ``SealedManifestAsset(asset_id, media_type, content_digest, payload)`` --
    exactly ``{asset.asset_id: asset.payload for asset in extracted.assets}``
    for this resolver, with no database and no owner scope.
    """

    async def resolve(asset_id: str) -> bytes | None:
        payload = mapping.get(asset_id)
        return bytes(payload) if payload else None

    return resolve


async def resolve_deck_assets(deck: ComposerDeckPlan, resolver: AssetResolver) -> dict[str, bytes]:
    """Every asset id the deck's image components reference, resolved once each.

    Walks ``ComposerSlidePlan.images`` -- the only place a plan carries an
    image component, and the only source ``render_image``
    (``services.composer.render_chrome``) draws a ``data-asset-id`` marker
    from -- collecting each distinct id's bytes through ``resolver``. Raises
    ``ComposerAssetError`` for the first slide whose image references bytes
    the resolver could not produce, before any render is attempted: a fit
    ladder that later hits an unresolved marker mid-render is a bug in this
    walk, not an expected outcome.

    Used to preload a deck's images into memory on the main loop before the
    fit ladder runs in worker threads (see ``services.composer.pipeline_fit``)
    -- those threads must never open a database session, so the resolver
    they receive must already be an ``in_memory_asset_resolver`` over this
    function's result.
    """

    resolved: dict[str, bytes] = {}
    for slide in deck.slides:
        missing: list[str] = []
        for component in slide.images:
            asset_id = component.asset_id
            if not asset_id or asset_id in resolved:
                continue
            payload = await resolver(asset_id)
            if payload is None:
                missing.append(asset_id)
            else:
                resolved[asset_id] = payload
        if missing:
            raise ComposerAssetError(slide.slide_index, missing)
    return resolved


async def preload_deck_assets(
    deck: ComposerDeckPlan,
    manifest: Any,
    owner_id: Any,
    session: Any,
) -> dict[str, bytes]:
    """``resolve_deck_assets`` against the DB-backed sealed manifest asset store.

    The convenience entry point ``services.composer.pipeline`` uses for the
    production path: builds a ``manifest_asset_resolver`` and resolves every
    asset id the deck references through it, once, on the main loop.
    """

    resolver = manifest_asset_resolver(manifest, owner_id=owner_id, session=session)
    return await resolve_deck_assets(deck, resolver)


class AssetInliningMeasurer:
    """Resolve every ``data-asset-id`` marker before the inner ``Measurer`` runs.

    ``render_slide`` admits a projection with the marker stripped but returns
    it unresolved, and the ``Renderer`` protocol stays synchronous, so the
    marker cannot be resolved inside the render step itself without an
    event-loop bridge. The ``Measurer`` protocol is already awaited, so this
    is the seam ``services.composer.pipeline`` wraps every fit-ladder
    measurer with: every Chromium call -- not just the final one -- sees
    fully admitted HTML. The pipeline re-applies ``inline_source_images`` to
    each accepted result's HTML afterwards so the published document matches
    exactly what was measured.
    """

    def __init__(self, inner: Measurer, resolver: AssetResolver) -> None:
        self.inner = inner
        self.resolver = resolver

    async def __call__(self, html: str, plan: ComposerSlidePlan) -> Measurement:
        admitted_html, unresolved = await inline_source_images(html, self.resolver)
        if unresolved:
            raise ComposerAssetError(plan.slide_index, unresolved)
        return await self.inner(admitted_html, plan)


async def inline_result_assets(
    results: Sequence[ComposerSlideResult],
    resolver: AssetResolver,
) -> list[ComposerSlideResult]:
    """Re-apply the same marker resolution ``AssetInliningMeasurer`` already admitted.

    Deterministic given the same HTML and resolver, so this reproduces
    exactly the admitted document the accepted render was measured against --
    a downstream PNG cache keyed by that admitted HTML still hits. A slide
    only reaches here after already resolving cleanly during the fit ladder,
    so a miss here is a genuine inconsistency, not expected.
    """

    admitted: list[ComposerSlideResult] = []
    for result in results:
        html, unresolved = await inline_source_images(result.html, resolver)
        if unresolved:
            raise ComposerAssetError(result.slide_index, unresolved)
        admitted.append(result if html == result.html else result.model_copy(update={"html": html}))
    return admitted


__all__ = [
    "AssetInliningMeasurer",
    "AssetResolver",
    "ComposerAssetError",
    "in_memory_asset_resolver",
    "inline_result_assets",
    "inline_source_images",
    "manifest_asset_resolver",
    "preload_deck_assets",
    "resolve_deck_assets",
    "substitute_resolved_assets",
]
