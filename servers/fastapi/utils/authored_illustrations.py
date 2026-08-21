"""Fill authored-slide image slots with source pictures or generated images.

Illustration-enabled authored styles let the model place ONE
``<img data-illust-prompt="...">`` slot per slide (see ``ILLUSTRATION_SLOT_RULES``
in ``author_slide_rules.py``). When source images are offered to a slide, the
model may instead reference one of them by ``<img data-asset-id="...">`` (the
source-image variant of those rules lives in ``author_slide_images.py``); a real
picture the user owns beats invented pixels, so a resolved source image wins over
the illustration slot.

Both kinds are inlined as data URIs so the slide stays fully self-contained for
render, export and the hybrid-PPTX preflight. Every failure degrades to simply
removing the slot — the deck always renders.

This module is also the security boundary for source images: ``data-asset-id``
is model output, so it is only ever resolved by lookup in the candidate mapping
that was offered to that slide. An identifier the model invents is dropped and
never reaches the filesystem.
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
from collections.abc import Mapping as MappingABC
from pathlib import Path
from typing import (
    Dict,
    List,
    Mapping,
    NamedTuple,
    Optional,
    Protocol,
    Sequence,
    Set,
    Union,
)

from models.image_prompt import ImagePrompt
from services.source_images.models import (
    SUPPORTED_SOURCE_IMAGE_MIME_TYPES,
    SourceImageCandidate,
)
from utils.authored_illustration_prompt import (
    MAX_SLIDE_CONTENT_CHARS,
    build_illustration_prompt,
    clean_scene_prompt,
    clean_slide_content,
)
from utils.authored_image_slots import Slot, rewrite_slots, scan_slots
from utils.renderer_isolation import MAX_ASSET_BYTES, MAX_HTML_BYTES

LOGGER = logging.getLogger(__name__)

# The label policy moved to a sibling module, but this one stays the pipeline's
# entry point: the names its tests import are bound here to that one definition.
_MAX_SLIDE_CONTENT_CHARS = MAX_SLIDE_CONTENT_CHARS
_clean_slide_content = clean_slide_content

# One slot per slide keeps cost/latency bounded and matches the authoring rules.
MAX_ILLUSTRATION_BYTES = 12 * 1024 * 1024

# Room left for the slide's own markup once the picture is inlined. Authored
# slides are a few tens of KB of HTML/CSS; a quarter of the budget is generous.
_INLINE_HTML_RESERVE_BYTES = 256 * 1024

#: A source image is whatever the user happened to upload, and a photograph out
#: of a real PPTX is routinely several megabytes — far past what the renderer
#: admits. Base64 costs a third on top, and the encoded document has to fit
#: ``MAX_HTML_BYTES`` as a whole, so the raw file is capped at what will still
#: pass admission. Over it the slot degrades (illustration fallback, or no
#: image) instead of failing the slide with a non-retryable policy error.
MAX_INLINE_SOURCE_IMAGE_BYTES = min(
    MAX_ASSET_BYTES, (MAX_HTML_BYTES - _INLINE_HTML_RESERVE_BYTES) * 3 // 4
)

# Generated illustrations are inlined as a data URI, and base64 costs another
# third on top. What blew the 2MB renderer source limit was the PNG the image
# models return, not their resolution: measured on a real 1536x1024 source,
# 1.04MB of PNG becomes 1.39MB encoded (68% of the budget) while the same pixels
# as quality-95 JPEG are 191KB, or 254KB encoded (12%). So re-encode, but keep
# the provider's own resolution - downscaling to 1280 would have saved a further
# 3% of the budget and cost a third of the linear resolution, which the export
# actually needs: a slot spanning 450 of the slide's 1280px becomes 4.69in in
# PowerPoint, where a 1536px source prints at ~327 DPI and a 1280px one at ~273,
# below the 300 DPI print standard.
#
# The width cap is a safety valve for a provider that returns something enormous,
# not a target: nothing in the current provider table comes close to it.
_ILLUSTRATION_MAX_WIDTH = 2560
_ILLUSTRATION_JPEG_QUALITY = 95

_MIME_BY_SUFFIX = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}

#: One slide's offer: asset id -> candidate. ``None`` means source images are
#: not in play at all, which keeps ``data-asset-id`` inert.
SlideSourceImages = Optional[Mapping[str, SourceImageCandidate]]
DeckSourceImages = Union[
    Mapping[str, SourceImageCandidate],
    Sequence[Mapping[str, SourceImageCandidate]],
    None,
]


def _env_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, "").strip())
        return value if value > 0 else default
    except (TypeError, ValueError):
        return default


ILLUSTRATION_CONCURRENCY = _env_int("AUTHORED_ILLUSTRATION_CONCURRENCY", 4)
_illustration_sem: Optional[asyncio.Semaphore] = None
_illustration_sem_loop: Optional[asyncio.AbstractEventLoop] = None
_illustration_sem_limit: Optional[int] = None


def _get_illustration_sem() -> asyncio.Semaphore:
    """One concurrency cap shared by every deck on the current server event loop."""
    global _illustration_sem, _illustration_sem_loop, _illustration_sem_limit
    loop = asyncio.get_running_loop()
    if (
        _illustration_sem is None
        or _illustration_sem_loop is not loop
        or _illustration_sem_limit != ILLUSTRATION_CONCURRENCY
    ):
        _illustration_sem = asyncio.Semaphore(ILLUSTRATION_CONCURRENCY)
        _illustration_sem_loop = loop
        _illustration_sem_limit = ILLUSTRATION_CONCURRENCY
    return _illustration_sem


class _StyleLike(Protocol):
    id: str
    illustration_prompt: Optional[str]
    reference_mode: str


class _ImageServiceLike(Protocol):
    async def generate_image(self, prompt: ImagePrompt) -> object: ...


class _Placement(NamedTuple):
    slot_index: Optional[int]
    data_uri: Optional[str]


def _is_exact_reconstruction(style: _StyleLike) -> bool:
    mode = getattr(style, "reference_mode", "style_reference")
    return isinstance(mode, str) and mode.strip().lower() == "exact_reconstruction"


def _data_uri_from_asset(asset: object) -> Optional[str]:
    """Return a data URI for a locally generated image, or None on any mismatch.

    Only local ImageAsset results count as success: placeholder/stock URLs would
    make the slide depend on the network (and fail the hybrid preflight).
    """
    path = getattr(asset, "path", None)
    if not isinstance(path, str) or not path:
        return None

    file = Path(path)
    mime = _MIME_BY_SUFFIX.get(file.suffix.lower())
    if mime is None or not file.is_file():
        return None
    return _encode_data_uri(file, mime, recompress=True)


def _data_uri_from_source_image(candidate: SourceImageCandidate) -> Optional[str]:
    """Inline a user-owned picture under the same local-only rule.

    The path comes from the candidate the pipeline built, never from the model,
    and the mime type is the one recorded at extraction time — a file we cannot
    read or a format the renderer would not accept degrades to no image.

    These bytes are never re-encoded: the cap below already keeps the picture
    inside the renderer's budget, so a lossy pass would only degrade what the
    user chose.
    """
    path = getattr(candidate, "path", None)
    if isinstance(path, str):
        path = Path(path)
    if not isinstance(path, Path) or not str(path):
        return None
    mime = getattr(candidate, "mime_type", None)
    if mime not in SUPPORTED_SOURCE_IMAGE_MIME_TYPES or not path.is_file():
        return None
    try:
        size = path.stat().st_size
    except OSError as exc:
        # These paths locate the user's own bytes on this server, so the log
        # carries the reason and never the location. The exception's own text is
        # left out for the same reason: OSError quotes the filename back.
        LOGGER.warning(
            "Unable to read authored source image: %s",
            exc.strerror or type(exc).__name__,
        )
        return None
    if size > MAX_INLINE_SOURCE_IMAGE_BYTES:
        LOGGER.warning(
            "Source image is too large to inline (%d bytes); dropping the slot",
            size,
        )
        return None
    return _encode_data_uri(path, mime, recompress=False)


def _encode_data_uri(file: Path, mime: str, *, recompress: bool) -> Optional[str]:
    try:
        size = file.stat().st_size
        if size > MAX_ILLUSTRATION_BYTES:
            LOGGER.warning("Authored image too large, dropping (%d bytes)", size)
            return None
        payload = file.read_bytes()
    except OSError as exc:
        LOGGER.warning(
            "Unable to read authored image: %s",
            exc.strerror or type(exc).__name__,
        )
        return None
    if recompress:
        mime, payload = _recompress_illustration(mime, payload)
    return f"data:{mime};base64,{base64.b64encode(payload).decode('ascii')}"


def _recompress_illustration(mime: str, payload: bytes) -> tuple[str, bytes]:
    """Shrink a generated illustration to what the slot actually paints.

    Returns the original bytes unchanged if anything goes wrong: an oversized
    image that still renders beats a slide that lost its illustration, and the
    caller's own size ceiling still applies.
    """
    try:
        from PIL import Image

        with Image.open(io.BytesIO(payload)) as image:
            image.load()
            width, height = image.size
            if width > _ILLUSTRATION_MAX_WIDTH:
                target = (
                    _ILLUSTRATION_MAX_WIDTH,
                    max(1, round(height * _ILLUSTRATION_MAX_WIDTH / width)),
                )
                image = image.resize(target, Image.LANCZOS)
            buffer = io.BytesIO()
            image.convert("RGB").save(
                buffer,
                "JPEG",
                quality=_ILLUSTRATION_JPEG_QUALITY,
                optimize=True,
            )
    except Exception as exc:  # noqa: BLE001 - provider output is untrusted
        LOGGER.warning("Could not recompress authored illustration: %s", exc)
        return mime, payload
    encoded = buffer.getvalue()
    if len(encoded) >= len(payload):
        return mime, payload
    return "image/jpeg", encoded


def strip_illustration_slots(html: str) -> str:
    """Drop every illustration slot a slide authored, leaving nothing behind.

    The deck-scoped illustration plan decides which slides carry a generated
    picture, but slides are authored independently and in parallel, so one can
    write a slot the plan did not grant it. Withdrawing it through the same
    removal the failure path uses is what keeps a src-less ``<img>`` box off the
    rendered slide. Source-image slots are untouched: the plan budgets provider
    calls, and placing a picture the user already owns costs none.
    """
    slots = scan_slots(html, want_assets=False, want_illust=True)
    return rewrite_slots(html, slots, None, None)


def reserve_source_images_for_slide(
    html: str,
    candidates: Mapping[str, SourceImageCandidate],
    placed_asset_ids: Set[str],
) -> Dict[str, SourceImageCandidate]:
    """Narrow a slide's offer to the single picture it is still allowed to place.

    A picture belongs on one slide of the deck, so ids already taken by an
    earlier slide are skipped here and the later slide falls back to its
    illustration slot (or to nothing). ``placed_asset_ids`` is updated in place.
    """
    if not candidates:
        return {}
    for slot in scan_slots(html, want_assets=True, want_illust=False):
        asset_id = slot.asset_id
        if not asset_id or asset_id in placed_asset_ids:
            continue
        candidate = candidates.get(asset_id)
        if candidate is None:
            continue
        placed_asset_ids.add(asset_id)
        return {asset_id: candidate}
    return {}


def _place_source_image(
    slots: Sequence[Slot],
    source_images: Mapping[str, SourceImageCandidate],
    slide_index: int,
) -> _Placement:
    """Resolve the first slot whose id was actually offered to this slide."""
    for position, slot in enumerate(slots):
        if not slot.asset_id:
            continue
        candidate = source_images.get(slot.asset_id)
        if candidate is None:
            # Hallucinated, stale or crafted id: drop the slot, touch no file.
            LOGGER.warning(
                "[authored] unknown source image id on slide %d, dropping slot",
                slide_index + 1,
            )
            continue
        data_uri = _data_uri_from_source_image(candidate)
        if data_uri is not None:
            return _Placement(position, data_uri)
    return _Placement(None, None)


async def _generate_slot_image(
    scene_raw: str,
    style_prompt: str,
    image_service: _ImageServiceLike,
    slide_index: int,
    raise_on_failure: bool,
    slide_content: str,
) -> Optional[str]:
    """Generate one slot's illustration; None means the slot has to go."""
    scene = clean_scene_prompt(scene_raw)
    if not scene:
        return None
    content = clean_slide_content(slide_content)
    try:
        async with _get_illustration_sem():
            asset = await image_service.generate_image(
                ImagePrompt(
                    prompt=build_illustration_prompt(scene, content, image_service),
                    theme_prompt=style_prompt,
                )
            )
        data_uri = _data_uri_from_asset(asset)
    except Exception as exc:  # degrade, never break the deck
        LOGGER.warning(
            "[authored] illustration generation failed (slide %d): %s",
            slide_index + 1,
            exc,
        )
        if raise_on_failure:
            raise
        return None
    if data_uri is None and raise_on_failure:
        raise RuntimeError("illustration provider returned no durable local image")
    return data_uri


async def apply_authored_illustration(
    html: str,
    style: _StyleLike,
    image_service: _ImageServiceLike,
    slide_index: int = 0,
    *,
    slide_content: str = "",
    raise_on_failure: bool = False,
    source_images: SlideSourceImages = None,
    generate_fallback: bool = True,
) -> str:
    """Fill one slide's image slot, without ever raising.

    ``source_images`` is the exact candidate set offered to THIS slide, keyed by
    asset id; a resolved source picture wins over the illustration slot. ``None``
    means source images are not in play, so ``data-asset-id`` stays inert and
    behaviour is exactly what it was before that feature.

    ``slide_content`` is the slide's outline markdown (heading plus bullets). It
    is the authoritative — and only — source of any label the image model is
    allowed to draw, and it only reaches providers verified to render text.
    """
    # Exact reconstruction must keep the model-authored source recreation untouched:
    # an illustration-provider call would invent pixels that are absent from the source.
    if _is_exact_reconstruction(style):
        return html
    style_prompt = (getattr(style, "illustration_prompt", None) or "").strip()
    slots = scan_slots(
        html,
        want_assets=source_images is not None,
        want_illust=bool(style_prompt),
    )
    if not slots:
        return html

    keep_index: Optional[int] = None
    data_uri: Optional[str] = None
    if source_images:
        keep_index, data_uri = _place_source_image(slots, source_images, slide_index)
    if data_uri is None and style_prompt and generate_fallback:
        # No user picture landed — the generated illustration is the fallback.
        illust_index = next(
            (i for i, slot in enumerate(slots) if slot.scene is not None), None
        )
        if illust_index is not None:
            keep_index = illust_index
            data_uri = await _generate_slot_image(
                slots[illust_index].scene or "",
                style_prompt,
                image_service,
                slide_index,
                raise_on_failure,
                slide_content,
            )
    if data_uri is None:
        LOGGER.warning(
            "[authored] dropping image slot on slide %d (style=%s)",
            slide_index + 1,
            # This module never raises: a slide degrades to no picture rather
            # than failing the deck. Reading .id off a caller that passed no
            # style would turn the degradation itself into the exception the
            # rest of the function exists to avoid.
            getattr(style, "id", None),
        )
    return rewrite_slots(html, slots, keep_index, data_uri)


def _content_at(slide_contents: Optional[Sequence[str]], index: int) -> str:
    if not slide_contents or index >= len(slide_contents):
        return ""
    return slide_contents[index] or ""


def _per_slide_source_images(
    htmls: Sequence[str], source_images: DeckSourceImages
) -> List[SlideSourceImages]:
    """Split a deck-wide offer into per-slide offers; the first slide wins a tie."""
    if source_images is None:
        return [None] * len(htmls)
    placed: Set[str] = set()
    per_slide: List[SlideSourceImages] = []
    for index, html in enumerate(htmls):
        if isinstance(source_images, MappingABC):
            candidates: Mapping[str, SourceImageCandidate] = source_images
        else:
            candidates = source_images[index] if index < len(source_images) else {}
        per_slide.append(reserve_source_images_for_slide(html, candidates, placed))
    return per_slide


async def apply_authored_illustrations(
    htmls: List[str],
    style: _StyleLike,
    image_service: _ImageServiceLike,
    slide_contents: Optional[Sequence[str]] = None,
    *,
    source_images: DeckSourceImages = None,
) -> List[str]:
    """Fill each slide's image slot (at most one per slide).

    ``source_images`` is either one deck-wide mapping of asset id -> candidate or
    one mapping per slide; either way a given picture is placed on at most one
    slide of the deck. ``slide_contents`` is the per-slide outline markdown,
    positionally aligned with ``htmls``; a slide without content simply gets the
    no-text prompt. Extra slots beyond the placed one are removed, as is the slot
    of a slide whose generation failed, returned a provider placeholder, gave
    unreadable output or named an unknown asset id — the HTML layout reserves the
    area explicitly, so the slide still renders. Never raises.
    """
    if _is_exact_reconstruction(style):
        return htmls
    style_prompt = (getattr(style, "illustration_prompt", None) or "").strip()
    if not style_prompt and source_images is None:
        return htmls

    per_slide = _per_slide_source_images(htmls, source_images)
    return list(
        await asyncio.gather(
            *(
                apply_authored_illustration(
                    html,
                    style,
                    image_service,
                    i,
                    slide_content=_content_at(slide_contents, i),
                    source_images=per_slide[i],
                )
                for i, html in enumerate(htmls)
            )
        )
    )
