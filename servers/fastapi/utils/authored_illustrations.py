"""Fill authored-slide image slots with source pictures or generated images.

Illustration-enabled authored styles let the model place ONE
``<img data-illust-prompt="...">`` slot per slide (see
``ILLUSTRATION_SLOT_RULES`` in ``author_slide_images.py``). When source images are
offered to a slide, the model may instead reference one of them by
``<img data-asset-id="...">``; a real picture the user owns beats invented
pixels, so a resolved source image wins over the illustration slot.

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
import logging
import os
import re
from collections.abc import Mapping as MappingABC
from dataclasses import dataclass
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
from utils.renderer_isolation import MAX_ASSET_BYTES, MAX_HTML_BYTES

LOGGER = logging.getLogger(__name__)

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

# The scene prompt comes from model output — cap it and strip quotes/newlines.
_MAX_SCENE_PROMPT_CHARS = 600

_IMG_TAG_PATTERN = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_ILLUST_PROMPT_ATTRIBUTE = re.compile(
    r"\bdata-illust-prompt\s*=\s*\"([^\"]*)\"", re.IGNORECASE
)
_ASSET_ID_ATTRIBUTE = re.compile(r"\bdata-asset-id\s*=\s*\"([^\"]*)\"", re.IGNORECASE)
_SRC_ATTRIBUTE_PATTERN = re.compile(r"\bsrc\s*=\s*(\"[^\"]*\"|'[^']*')", re.IGNORECASE)

# Appended to every slot prompt: generated text is unreliable (especially
# Korean), all labels live in HTML per the authoring rules.
_NO_TEXT_SUFFIX = (
    "Absolutely no text, letters, numbers, words or labels anywhere in the image."
)

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


class _StyleLike(Protocol):
    id: str
    illustration_prompt: Optional[str]
    reference_mode: str


class _ImageServiceLike(Protocol):
    async def generate_image(self, prompt: ImagePrompt) -> object: ...


@dataclass(frozen=True)
class _Slot:
    """One ``<img>`` this module owns: a source-image or an illustration slot."""

    start: int
    end: int
    tag: str
    asset_id: Optional[str]
    scene: Optional[str]


class _Placement(NamedTuple):
    slot_index: Optional[int]
    data_uri: Optional[str]


def _is_exact_reconstruction(style: _StyleLike) -> bool:
    mode = getattr(style, "reference_mode", "style_reference")
    return isinstance(mode, str) and mode.strip().lower() == "exact_reconstruction"


def _clean_scene_prompt(raw: str) -> str:
    cleaned = re.sub(r"\s+", " ", raw or "").strip()
    return cleaned[:_MAX_SCENE_PROMPT_CHARS]


def _scan_slots(html: str, *, want_assets: bool, want_illust: bool) -> List[_Slot]:
    """Collect the img tags this module owns, in document order.

    A kind the caller did not ask for stays invisible, so a deck generated with
    source images disabled sees exactly the slots it saw before the feature.
    """
    slots: List[_Slot] = []
    for match in _IMG_TAG_PATTERN.finditer(html):
        tag = match.group(0)
        asset = _ASSET_ID_ATTRIBUTE.search(tag) if want_assets else None
        illust = _ILLUST_PROMPT_ATTRIBUTE.search(tag) if want_illust else None
        if asset is None and illust is None:
            continue
        slots.append(
            _Slot(
                start=match.start(),
                end=match.end(),
                tag=tag,
                asset_id=asset.group(1) if asset is not None else None,
                scene=illust.group(1) if illust is not None else None,
            )
        )
    return slots


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
    return _encode_data_uri(file, mime)


def _data_uri_from_source_image(candidate: SourceImageCandidate) -> Optional[str]:
    """Inline a user-owned picture under the same local-only rule.

    The path comes from the candidate the pipeline built, never from the model,
    and the mime type is the one recorded at extraction time — a file we cannot
    read or a format the renderer would not accept degrades to no image.
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
    return _encode_data_uri(path, mime)


def _encode_data_uri(file: Path, mime: str) -> Optional[str]:
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
    return f"data:{mime};base64,{base64.b64encode(payload).decode('ascii')}"


def _resolve_slot(tag: str, data_uri: Optional[str]) -> str:
    """Rewrite one slot tag: inject the resolved src, or drop the tag entirely."""
    if data_uri is None:
        return ""
    without_src = _SRC_ATTRIBUTE_PATTERN.sub("", tag)
    return without_src.replace("<img", f'<img src="{data_uri}"', 1)


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
    for slot in _scan_slots(html, want_assets=True, want_illust=False):
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
    slots: Sequence[_Slot],
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
) -> Optional[str]:
    """Generate one slot's illustration; None means the slot has to go."""
    scene = _clean_scene_prompt(scene_raw)
    if not scene:
        return None
    try:
        async with _get_illustration_sem():
            asset = await image_service.generate_image(
                ImagePrompt(
                    prompt=f"{scene}. {_NO_TEXT_SUFFIX}",
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
    raise_on_failure: bool = False,
    source_images: SlideSourceImages = None,
) -> str:
    """Fill one slide's image slot, without ever raising.

    ``source_images`` is the exact candidate set offered to THIS slide, keyed by
    asset id; a resolved source picture wins over the illustration slot. ``None``
    means source images are not in play, so ``data-asset-id`` stays inert and
    behaviour is exactly what it was before the feature.
    """
    # Exact reconstruction must keep the model-authored source recreation untouched:
    # an illustration-provider call would invent pixels that are absent from the source.
    if _is_exact_reconstruction(style):
        return html
    style_prompt = (getattr(style, "illustration_prompt", None) or "").strip()
    slots = _scan_slots(
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
    if data_uri is None and style_prompt:
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
    # Replace from the last slot backwards so spans stay valid; only the chosen
    # slot may receive the image, the rest are always removed.
    result = html
    for position, slot in reversed(list(enumerate(slots))):
        keep = position == keep_index and data_uri is not None
        replacement = _resolve_slot(slot.tag, data_uri) if keep else ""
        result = result[: slot.start] + replacement + result[slot.end :]
    return result


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
    *,
    source_images: DeckSourceImages = None,
) -> List[str]:
    """Fill each slide's image slot (at most one per slide).

    ``source_images`` is either one deck-wide mapping of asset id -> candidate or
    one mapping per slide; either way a given picture is placed on at most one
    slide of the deck. Extra slots beyond the placed one are removed. On
    generation failure, provider placeholders, unreadable output or an unknown
    asset id the slot is removed so the HTML layout — which reserves the area
    explicitly — still renders. Never raises.
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
                    html, style, image_service, i, source_images=per_slide[i]
                )
                for i, html in enumerate(htmls)
            )
        )
    )
