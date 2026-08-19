"""Fill authored-slide illustration slots with generated images.

Illustration-enabled authored styles let the model place ONE
``<img data-illust-prompt="...">`` slot per slide (see
``ILLUSTRATION_SLOT_RULES`` in ``author_slide_rules.py``). After authoring, this
module generates each slot's image through the configured image provider and
inlines it as a data URI so the slide stays fully self-contained for render,
export and the hybrid-PPTX preflight. Every failure degrades to simply removing
the slot — the deck always renders.
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
import re
from typing import List, Optional, Protocol, Sequence

from models.image_prompt import ImagePrompt
from utils.image_provider import image_provider_renders_text

LOGGER = logging.getLogger(__name__)

# One slot per slide keeps cost/latency bounded and matches the authoring rules.
MAX_ILLUSTRATION_BYTES = 12 * 1024 * 1024

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
# The slide's outline markdown is the ONLY permitted source of label text, and it
# is user/model data — cap it the same defensive way the scene prompt is capped.
_MAX_SLIDE_CONTENT_CHARS = 700
# Matches the cap the authoring rules put on labels inside one illustration.
MAX_ILLUSTRATION_LABELS = 6

_IMG_SLOT_PATTERN = re.compile(
    r"<img\b[^>]*\bdata-illust-prompt\s*=\s*\"([^\"]*)\"[^>]*/?>",
    re.IGNORECASE,
)
_SRC_ATTRIBUTE_PATTERN = re.compile(r"\bsrc\s*=\s*(\"[^\"]*\"|'[^']*')", re.IGNORECASE)

# Appended when the selected image provider cannot render text reliably (Gemini
# based providers, unknown backing models, stock photo providers): generated
# glyphs would be broken, so all labels live in the slide HTML instead.
_NO_TEXT_SUFFIX = (
    "Absolutely no text, letters, numbers, words or labels anywhere in the image."
)

_MIME_BY_SUFFIX = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


class _StyleLike(Protocol):
    id: str
    illustration_prompt: Optional[str]
    reference_mode: str


class _ImageServiceLike(Protocol):
    async def generate_image(self, prompt: ImagePrompt) -> object: ...


def _is_exact_reconstruction(style: _StyleLike) -> bool:
    mode = getattr(style, "reference_mode", "style_reference")
    return isinstance(mode, str) and mode.strip().lower() == "exact_reconstruction"


def _clean_scene_prompt(raw: str) -> str:
    cleaned = re.sub(r"\s+", " ", raw or "").strip()
    return cleaned[:_MAX_SCENE_PROMPT_CHARS]


def _clean_slide_content(raw: str) -> str:
    """Normalize the slide's outline markdown into the label source block.

    Markdown markers are stripped, because the prompt tells the image model to
    copy these strings character for character and it obeys: an outline line of
    ``## 조직 및 인력`` came back painted into the picture with the ``##`` still
    attached. What survives is the line structure - a heading and its bullets is
    what makes a label copyable - with runs of spaces collapsed, blank lines
    dropped, and the whole block capped like the scene prompt is.
    """
    lines: list[str] = []
    for line in (raw or "").splitlines():
        line = re.sub(r"[ \t]+", " ", line).strip()
        line = re.sub(r"^#{1,6}\s*", "", line)
        line = re.sub(r"^[-*+•·]\s+", "", line)
        line = re.sub(r"^\d+[.)]\s+", "", line)
        line = line.strip()
        if line:
            lines.append(line)
    return "\n".join(lines)[:_MAX_SLIDE_CONTENT_CHARS]


def _labeled_text_suffix(slide_content: str) -> str:
    """Suffix for providers that CAN render Korean: labels allowed, never invented."""
    return (
        f"You may place at most {MAX_ILLUSTRATION_LABELS} short Korean label strings "
        "in the image, and only where a label genuinely helps the viewer read the "
        "picture; fewer is better and none is acceptable. "
        "EVERY label must be COPIED VERBATIM, character for character, from the "
        "SLIDE CONTENT below. NEVER invent, translate, paraphrase, abbreviate or "
        "extend anything: no invented word, number, percentage, amount, date, unit, "
        "company name, product name or person name may appear in the image. If a "
        "string does not appear verbatim in the SLIDE CONTENT below, it must not "
        "appear in the image. When in doubt, draw the element with no label at all. "
        "No paragraphs, no body copy, no sentences, no legend, no caption block, no "
        "watermark, no logo, no signature.\n"
        "SLIDE CONTENT (the only permitted source of label text):\n"
        f"{slide_content}"
    )


def _provider_renders_text(image_service: object) -> bool:
    """Ask the image service's own runtime settings whether text is safe to draw."""
    try:
        return image_provider_renders_text(getattr(image_service, "settings", None))
    except Exception:  # capability probing must never break generation
        return False


def _build_illustration_prompt(
    scene: str, slide_content: str, image_service: object
) -> str:
    """Scene plus the suffix the selected provider's text capability calls for.

    Without slide content there is no verbatim source to copy from, so labels
    stay banned even on a text-capable provider.
    """
    if slide_content and _provider_renders_text(image_service):
        return f"{scene}. {_labeled_text_suffix(slide_content)}"
    return f"{scene}. {_NO_TEXT_SUFFIX}"


def _data_uri_from_asset(asset: object) -> Optional[str]:
    """Return a data URI for a locally generated image, or None on any mismatch.

    Only local ImageAsset results count as success: placeholder/stock URLs would
    make the slide depend on the network (and fail the hybrid preflight).
    """
    path = getattr(asset, "path", None)
    if not isinstance(path, str) or not path:
        return None
    from pathlib import Path

    file = Path(path)
    mime = _MIME_BY_SUFFIX.get(file.suffix.lower())
    if mime is None or not file.is_file():
        return None
    try:
        if file.stat().st_size > MAX_ILLUSTRATION_BYTES:
            LOGGER.warning("Authored illustration too large, dropping: %s", file)
            return None
        payload = file.read_bytes()
    except OSError as exc:
        LOGGER.warning("Unable to read authored illustration %s: %s", file, exc)
        return None
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


def _resolve_slot(tag: str, data_uri: Optional[str]) -> str:
    """Rewrite one slot tag: inject the generated src, or drop the tag entirely."""
    if data_uri is None:
        return ""
    without_src = _SRC_ATTRIBUTE_PATTERN.sub("", tag)
    return without_src.replace("<img", f'<img src="{data_uri}"', 1)


def _rewrite_slots(html: str, data_uri: Optional[str]) -> str:
    """Give the first slot the generated image and remove every other one.

    Replacing from the last match backwards is what keeps the earlier spans
    valid. ``None`` removes them all, which is the degradation path every
    failure already takes: the layout reserves the area explicitly, so a slide
    without its picture still renders.
    """
    result = html
    for position, match in reversed(list(enumerate(_IMG_SLOT_PATTERN.finditer(html)))):
        replacement = _resolve_slot(match.group(0), data_uri) if position == 0 else ""
        result = result[: match.start()] + replacement + result[match.end() :]
    return result


def strip_illustration_slots(html: str) -> str:
    """Drop every illustration slot a slide authored, leaving nothing behind.

    The deck-scoped illustration plan decides which slides carry a picture, but
    slides are authored independently and in parallel, so one can write a slot
    the plan did not grant it. This is how such a slot is withdrawn — through
    the same removal the failure path uses, so an unplanned slot cannot leave a
    src-less ``<img>`` box on the rendered slide.
    """
    return _rewrite_slots(html, None)


async def apply_authored_illustration(
    html: str,
    style: _StyleLike,
    image_service: _ImageServiceLike,
    slide_index: int = 0,
    *,
    slide_content: str = "",
    raise_on_failure: bool = False,
) -> str:
    """Generate and inline one slide's illustration slot, without ever raising.

    ``slide_content`` is the slide's outline markdown (heading plus bullets). It
    is the authoritative — and only — source of any label the image model is
    allowed to draw, and it is only sent to providers verified to render text.
    """
    # Exact reconstruction must keep the model-authored source recreation untouched:
    # an illustration-provider call would invent pixels that are absent from the source.
    if _is_exact_reconstruction(style):
        return html
    style_prompt = (getattr(style, "illustration_prompt", None) or "").strip()
    if not style_prompt:
        return html

    matches = list(_IMG_SLOT_PATTERN.finditer(html))
    if not matches:
        return html
    first = matches[0]
    scene = _clean_scene_prompt(first.group(1))
    content = _clean_slide_content(slide_content)
    data_uri: Optional[str] = None
    if scene:
        try:
            async with _get_illustration_sem():
                asset = await image_service.generate_image(
                    ImagePrompt(
                        prompt=_build_illustration_prompt(
                            scene, content, image_service
                        ),
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
    if scene and data_uri is None and raise_on_failure:
        raise RuntimeError("illustration provider returned no durable local image")
    if data_uri is None:
        LOGGER.warning(
            "[authored] dropping illustration slot on slide %d (style=%s)",
            slide_index + 1,
            style.id,
        )
    return _rewrite_slots(html, data_uri)


def _content_at(slide_contents: Optional[Sequence[str]], index: int) -> str:
    if not slide_contents or index >= len(slide_contents):
        return ""
    return slide_contents[index] or ""


async def apply_authored_illustrations(
    htmls: List[str],
    style: _StyleLike,
    image_service: _ImageServiceLike,
    slide_contents: Optional[Sequence[str]] = None,
) -> List[str]:
    """Generate and inline each slide's illustration slot (at most one per slide).

    ``slide_contents`` is the per-slide outline markdown, positionally aligned
    with ``htmls``; a slide without content simply gets the no-text prompt.
    Extra slots beyond the first are removed. On generation failure, provider
    placeholders, or unreadable output the slot is removed so the HTML layout —
    which reserves the area explicitly — still renders. Never raises.
    """
    if _is_exact_reconstruction(style):
        return htmls
    style_prompt = (getattr(style, "illustration_prompt", None) or "").strip()
    if not style_prompt:
        return htmls

    return list(
        await asyncio.gather(
            *(
                apply_authored_illustration(
                    html,
                    style,
                    image_service,
                    i,
                    slide_content=_content_at(slide_contents, i),
                )
                for i, html in enumerate(htmls)
            )
        )
    )
