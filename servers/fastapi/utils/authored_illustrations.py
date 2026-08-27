"""Fill authored-slide image slots with source pictures or generated images.

Illustration-enabled authored styles let the model place one or more source
image slots and (separately) a generated illustration
``<img data-illust-prompt="...">`` slot per slide (see ``ILLUSTRATION_SLOT_RULES``
in ``author_slide_rules.py``). When source images are offered to a slide, the
model may instead reference one or more by ``<img data-source-slot-id="...">``
(legacy ``data-asset-id`` is accepted for compatibility; the source-image variant
of those rules lives in ``author_slide_images.py``); a real
picture the user owns beats invented pixels, so a resolved source image wins over
the illustration slot.

Both kinds are inlined as data URIs so the slide stays fully self-contained for
render, export and the hybrid-PPTX preflight. Every failure degrades to simply
removing the slot — the deck always renders.

This module is also the security boundary for source images: source-slot ids are
model output, so they are only ever resolved by lookup in the candidate mapping
that was offered to that slide. An identifier the model invents is dropped and
never reaches the filesystem.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import logging
import os
import re
from collections import Counter
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
from utils.authored_image_slots import (
    Slot,
    rewrite_slots,
    scan_slots,
    source_slot_id_for_asset,
)
from utils.renderer_isolation import MAX_ASSET_BYTES, MAX_HTML_BYTES

LOGGER = logging.getLogger(__name__)

_FINAL_IMG_TAG = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_FINAL_IMG_SRC = re.compile(r"\bsrc\s*=\s*(['\"])(.*?)\1", re.IGNORECASE)
_FINAL_IMG_QID = re.compile(
    r"\bdata-qid\s*=\s*(['\"])([A-Za-z0-9][A-Za-z0-9._:-]*)\1",
    re.IGNORECASE,
)
_FINAL_DATA_IMAGE = re.compile(
    r"^data:(image/(?:png|jpeg|webp));base64,([A-Za-z0-9+/=]+)$",
    re.IGNORECASE,
)
_STRICT_SOURCE_MIN_EDGE_PX = 24.0
_STRICT_SOURCE_MIN_AREA_PX = 24.0 * 24.0
_STRICT_SOURCE_EVIDENCE_INTRINSIC_EDGE_PX = 640.0
_STRICT_SOURCE_EVIDENCE_RENDERED_MIN_EDGE_PX = 64.0
_STRICT_SOURCE_EVIDENCE_RENDERED_MAX_EDGE_PX = 144.0
_STRICT_SOURCE_MIN_VIEWPORT_RATIO = 0.75
_STRICT_SOURCE_MIN_EFFECTIVE_OPACITY = 0.2
_STRICT_SOURCE_MAX_OCCLUDED_FRACTION = 0.8
_STRICT_SOURCE_MAX_ASPECT_DISTORTION = 4.0
_STRICT_SOURCE_MULTI_IMAGE_MIN_COUNT = 5
_STRICT_SOURCE_MULTI_IMAGE_MAX_COUNT = 8
_STRICT_SOURCE_FLOOR_STYLE_ID = "authored-source-visibility-floor"
_STRICT_SOURCE_FLOOR_STYLE = re.compile(
    rf'<style\b[^>]*\bid=["\']{_STRICT_SOURCE_FLOOR_STYLE_ID}["\'][^>]*>.*?</style\s*>',
    re.IGNORECASE | re.DOTALL,
)

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
    placed_asset_ids: Optional[Set[str]] = None,
) -> Dict[str, SourceImageCandidate]:
    """Return every verified source slot authored on this slide.

    ``placed_asset_ids`` remains accepted for callers from the old API but is no
    longer used: the same asset is explicitly allowed to be reused across slides.
    """
    if not candidates:
        return {}
    offered: Dict[str, SourceImageCandidate] = {}
    for slot in scan_slots(html, want_assets=True, want_illust=False):
        slot_id = slot.source_slot_id or (
            source_slot_id_for_asset(slot.asset_id) if slot.asset_id else None
        )
        if not slot_id:
            continue
        candidate = candidates.get(slot_id)
        if candidate is None and slot.asset_id:
            candidate = candidates.get(slot.asset_id)
        if candidate is None:
            continue
        offered[slot_id] = candidate
    return offered


def validate_bound_source_images(
    html: str,
    candidates: Mapping[str, SourceImageCandidate],
) -> str:
    """Fail unless finalized HTML contains every expected source occurrence once."""

    expected = Counter(
        candidate.digest.removeprefix("sha256:").lower()
        for candidate in candidates.values()
    )
    actual: Counter[str] = Counter()
    for tag_match in _FINAL_IMG_TAG.finditer(html or ""):
        src_match = _FINAL_IMG_SRC.search(tag_match.group(0))
        if src_match is None:
            continue
        data_match = _FINAL_DATA_IMAGE.fullmatch(src_match.group(2).strip())
        if data_match is None:
            continue
        try:
            payload = base64.b64decode(data_match.group(2), validate=True)
        except (ValueError, TypeError) as error:
            raise ValueError("authored_source_image_binding_invalid") from error
        actual[hashlib.sha256(payload).hexdigest()] += 1
    if actual != expected:
        raise ValueError(
            "authored_source_image_binding_mismatch:"
            f"expected={sum(expected.values())};actual={sum(actual.values())}"
        )
    return html


def enforce_strict_source_image_size_floor(
    html: str,
    candidates: Mapping[str, SourceImageCandidate],
) -> str:
    """Give large evidence a deterministic CSS floor in dense source galleries.

    The authored model still owns the layout. This only closes the small numeric
    gap that repeatedly occurred on 5-8 image source-reflow slides (for example,
    113x71 or 201x58 screenshot boxes despite a 144x64 prompt requirement).
    Ordinary authored decks never call this helper, and icons keep their authored
    size because the 24px icon floor is already enforced by the final DOM seal.
    """

    if not (
        _STRICT_SOURCE_MULTI_IMAGE_MIN_COUNT
        <= len(candidates)
        <= _STRICT_SOURCE_MULTI_IMAGE_MAX_COUNT
    ):
        return html
    validate_bound_source_images(html, candidates)
    dimensions = {
        candidate.digest.removeprefix("sha256:").lower(): (
            float(candidate.width),
            float(candidate.height),
        )
        for candidate in candidates.values()
    }
    rules: list[str] = []
    for tag_match in _FINAL_IMG_TAG.finditer(html or ""):
        tag = tag_match.group(0)
        src_match = _FINAL_IMG_SRC.search(tag)
        qid_match = _FINAL_IMG_QID.search(tag)
        if src_match is None or qid_match is None:
            continue
        digest = _data_image_digest(src_match.group(2))
        if digest is None or digest not in dimensions:
            continue
        source_width, source_height = dimensions[digest]
        if (
            max(source_width, source_height)
            < _STRICT_SOURCE_EVIDENCE_INTRINSIC_EDGE_PX
        ):
            continue
        if source_width >= source_height:
            min_width = _STRICT_SOURCE_EVIDENCE_RENDERED_MAX_EDGE_PX
            min_height = _STRICT_SOURCE_EVIDENCE_RENDERED_MIN_EDGE_PX
        else:
            min_width = _STRICT_SOURCE_EVIDENCE_RENDERED_MIN_EDGE_PX
            min_height = _STRICT_SOURCE_EVIDENCE_RENDERED_MAX_EDGE_PX
        qid = qid_match.group(2)
        rules.append(
            f'img[data-qid="{qid}"]'
            f'{{min-width:{min_width:g}px;min-height:{min_height:g}px;'
            "object-fit:contain;}"
        )
    if not rules:
        return html
    # Replace our own prior seal before reinserting it so render/checkpoint
    # retries are idempotent and provider-authored CSS cannot spoof the floor.
    source = _STRICT_SOURCE_FLOOR_STYLE.sub("", html)
    style = (
        f'<style id="{_STRICT_SOURCE_FLOOR_STYLE_ID}">'
        + "".join(rules)
        + "</style>"
    )
    sealed, count = re.subn(
        r"</head\s*>",
        lambda match: style + match.group(0),
        source,
        count=1,
        flags=re.IGNORECASE,
    )
    if count != 1:
        raise ValueError("authored_source_image_visibility_style_injection_failed")
    return sealed


def _data_image_digest(value: object) -> str | None:
    data_match = _FINAL_DATA_IMAGE.fullmatch(str(value or "").strip())
    if data_match is None:
        return None
    try:
        payload = base64.b64decode(data_match.group(2), validate=True)
    except (ValueError, TypeError):
        return None
    return hashlib.sha256(payload).hexdigest()


def _metric_number(value: object, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if number == number else default


def validate_visible_source_images(
    html: str,
    candidates: Mapping[str, SourceImageCandidate],
    browser_metrics: Mapping[str, object] | None,
) -> str:
    """Seal that every strict source occurrence is materially visible on-slide."""

    validate_bound_source_images(html, candidates)
    if not isinstance(browser_metrics, Mapping):
        raise ValueError("authored_source_image_visibility_telemetry_missing")
    raw_elements = browser_metrics.get("elements")
    if not isinstance(raw_elements, Sequence) or isinstance(raw_elements, (str, bytes)):
        raise ValueError("authored_source_image_visibility_telemetry_missing")
    viewport_width = _metric_number(
        browser_metrics.get("width", browser_metrics.get("viewportWidth")),
        1280.0,
    )
    viewport_height = _metric_number(
        browser_metrics.get("height", browser_metrics.get("viewportHeight")),
        720.0,
    )
    expected = Counter(
        candidate.digest.removeprefix("sha256:").lower()
        for candidate in candidates.values()
    )
    dimensions = {
        candidate.digest.removeprefix("sha256:").lower(): (
            float(candidate.width),
            float(candidate.height),
        )
        for candidate in candidates.values()
    }
    visible: Counter[str] = Counter()
    for raw in raw_elements:
        if not isinstance(raw, Mapping) or str(raw.get("tag", "")).lower() != "img":
            continue
        image = raw.get("image")
        if not isinstance(image, Mapping):
            continue
        digest = _data_image_digest(image.get("src"))
        if digest is None or digest not in expected:
            continue
        style = raw.get("computed_style", raw.get("computedStyle", {}))
        style = style if isinstance(style, Mapping) else {}
        bbox = raw.get("bbox", raw.get("rect", {}))
        bbox = bbox if isinstance(bbox, Mapping) else {}
        x = _metric_number(bbox.get("x"))
        y = _metric_number(bbox.get("y"))
        width = _metric_number(bbox.get("width"))
        height = _metric_number(bbox.get("height"))
        intersection_width = max(
            0.0,
            min(x + width, viewport_width) - max(x, 0.0),
        )
        intersection_height = max(
            0.0,
            min(y + height, viewport_height) - max(y, 0.0),
        )
        computed_intersection = (
            intersection_width * intersection_height / (width * height)
            if width > 0 and height > 0
            else 0.0
        )
        intersection_ratio = _metric_number(
            image.get("viewportIntersectionRatio"),
            computed_intersection,
        )
        effective_opacity = _metric_number(
            image.get("effectiveOpacity"),
            _metric_number(style.get("opacity"), 1.0),
        )
        display = str(style.get("display", "")).strip().lower()
        visibility = str(style.get("visibility", "visible")).strip().lower()
        css_visible = image.get("cssVisible") is not False
        loaded = (
            image.get("complete") is not False
            and _metric_number(image.get("naturalWidth")) > 0
            and _metric_number(image.get("naturalHeight")) > 0
        )
        occluded = _metric_number(raw.get("occluded_fraction"), 0.0)
        source_width, source_height = dimensions[digest]
        source_ratio = source_width / source_height if source_height > 0 else 0.0
        rendered_ratio = width / height if height > 0 else 0.0
        aspect_distortion = (
            max(rendered_ratio / source_ratio, source_ratio / rendered_ratio)
            if source_ratio > 0 and rendered_ratio > 0
            else float("inf")
        )
        large_evidence = (
            max(source_width, source_height)
            >= _STRICT_SOURCE_EVIDENCE_INTRINSIC_EDGE_PX
        )
        materially_sized = (
            min(width, height) >= _STRICT_SOURCE_EVIDENCE_RENDERED_MIN_EDGE_PX
            and max(width, height) >= _STRICT_SOURCE_EVIDENCE_RENDERED_MAX_EDGE_PX
            if large_evidence
            else min(width, height) >= _STRICT_SOURCE_MIN_EDGE_PX
        )
        materially_visible = (
            css_visible
            and display != "none"
            and visibility not in {"hidden", "collapse"}
            and effective_opacity >= _STRICT_SOURCE_MIN_EFFECTIVE_OPACITY
            and materially_sized
            and width * height >= _STRICT_SOURCE_MIN_AREA_PX
            and intersection_ratio >= _STRICT_SOURCE_MIN_VIEWPORT_RATIO
            and occluded <= _STRICT_SOURCE_MAX_OCCLUDED_FRACTION
            and aspect_distortion <= _STRICT_SOURCE_MAX_ASPECT_DISTORTION
            and loaded
        )
        if materially_visible:
            visible[digest] += 1
    if visible != expected:
        raise ValueError(
            "authored_source_image_visibility_mismatch:"
            f"expected={sum(expected.values())};visible={sum(visible.values())}"
        )
    return html


def _place_source_image(
    slots: Sequence[Slot],
    source_images: Mapping[str, SourceImageCandidate],
    slide_index: int,
) -> dict[int, str]:
    """Resolve every source slot whose id was offered to this slide."""
    resolved: dict[int, str] = {}
    for position, slot in enumerate(slots):
        slot_id = slot.source_slot_id or (
            source_slot_id_for_asset(slot.asset_id) if slot.asset_id else None
        )
        if not slot_id:
            continue
        candidate = source_images.get(slot_id)
        if candidate is None and slot.asset_id:
            candidate = source_images.get(slot.asset_id)
        if candidate is None:
            # Hallucinated, stale or crafted id: drop the slot, touch no file.
            LOGGER.warning(
                "[authored] unknown source image slot on slide %d, dropping slot",
                slide_index + 1,
            )
            continue
        data_uri = _data_uri_from_source_image(candidate)
        if data_uri is not None:
            resolved[position] = data_uri
    return resolved


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
    opaque source-slot id (legacy asset-id keys are also accepted); every
    resolved source picture wins over the illustration slot. ``None``
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

    resolved_by_index: dict[int, str] = {}
    if source_images:
        resolved_by_index = _place_source_image(slots, source_images, slide_index)
    if not resolved_by_index and style_prompt and generate_fallback:
        # No user picture landed — the generated illustration is the fallback.
        illust_index = next(
            (i for i, slot in enumerate(slots) if slot.scene is not None), None
        )
        if illust_index is not None:
            data_uri = await _generate_slot_image(
                slots[illust_index].scene or "",
                style_prompt,
                image_service,
                slide_index,
                raise_on_failure,
                slide_content,
            )
            if data_uri is not None:
                resolved_by_index[illust_index] = data_uri
    if not resolved_by_index:
        LOGGER.warning(
            "[authored] dropping image slot on slide %d (style=%s)",
            slide_index + 1,
            # This module never raises: a slide degrades to no picture rather
            # than failing the deck. Reading .id off a caller that passed no
            # style would turn the degradation itself into the exception the
            # rest of the function exists to avoid.
            getattr(style, "id", None),
        )
    return rewrite_slots(
        html,
        slots,
        None,
        None,
        resolved_by_index=resolved_by_index,
    )


def _content_at(slide_contents: Optional[Sequence[str]], index: int) -> str:
    if not slide_contents or index >= len(slide_contents):
        return ""
    return slide_contents[index] or ""


def _per_slide_source_images(
    htmls: Sequence[str], source_images: DeckSourceImages
) -> List[SlideSourceImages]:
    """Split a deck-wide offer into per-slide offers without consuming assets."""
    if source_images is None:
        return [None] * len(htmls)
    per_slide: List[SlideSourceImages] = []
    for index, html in enumerate(htmls):
        if isinstance(source_images, MappingABC):
            candidates: Mapping[str, SourceImageCandidate] = source_images
        else:
            candidates = source_images[index] if index < len(source_images) else {}
        per_slide.append(reserve_source_images_for_slide(html, candidates))
    return per_slide


async def apply_authored_illustrations(
    htmls: List[str],
    style: _StyleLike,
    image_service: _ImageServiceLike,
    slide_contents: Optional[Sequence[str]] = None,
    *,
    source_images: DeckSourceImages = None,
) -> List[str]:
    """Fill each slide's source slots, with generated illustration as fallback.

    ``source_images`` is either one deck-wide mapping of source-slot/legacy asset
    id -> candidate or one mapping per slide; candidates may be reused across
    slides. ``slide_contents`` is the per-slide outline markdown,
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
