"""Fill authored-slide illustration slots with generated images.

Illustration-enabled authored styles let the model place ONE
``<img data-illust-prompt="...">`` slot per slide (see
``_ILLUSTRATION_SLOT_RULES`` in ``author_slide.py``). After authoring, this
module generates each slot's image through the configured image provider and
inlines it as a data URI so the slide stays fully self-contained for render,
export and the hybrid-PPTX preflight. Every failure degrades to simply removing
the slot — the deck always renders.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
from typing import List, Optional, Protocol

from models.image_prompt import ImagePrompt

LOGGER = logging.getLogger(__name__)

# One slot per slide keeps cost/latency bounded and matches the authoring rules.
MAX_ILLUSTRATION_BYTES = 12 * 1024 * 1024


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

_IMG_SLOT_PATTERN = re.compile(
    r"<img\b[^>]*\bdata-illust-prompt\s*=\s*\"([^\"]*)\"[^>]*/?>",
    re.IGNORECASE,
)
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


class _StyleLike(Protocol):
    id: str
    illustration_prompt: Optional[str]


class _ImageServiceLike(Protocol):
    async def generate_image(self, prompt: ImagePrompt) -> object: ...


def _clean_scene_prompt(raw: str) -> str:
    cleaned = re.sub(r"\s+", " ", raw or "").strip()
    return cleaned[:_MAX_SCENE_PROMPT_CHARS]


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
    return f"data:{mime};base64,{base64.b64encode(payload).decode('ascii')}"


def _resolve_slot(tag: str, data_uri: Optional[str]) -> str:
    """Rewrite one slot tag: inject the generated src, or drop the tag entirely."""
    if data_uri is None:
        return ""
    without_src = _SRC_ATTRIBUTE_PATTERN.sub("", tag)
    return without_src.replace("<img", f'<img src="{data_uri}"', 1)


async def apply_authored_illustration(
    html: str,
    style: _StyleLike,
    image_service: _ImageServiceLike,
    slide_index: int = 0,
) -> str:
    """Generate and inline one slide's illustration slot, without ever raising."""
    style_prompt = (getattr(style, "illustration_prompt", None) or "").strip()
    if not style_prompt:
        return html

    matches = list(_IMG_SLOT_PATTERN.finditer(html))
    if not matches:
        return html
    first = matches[0]
    scene = _clean_scene_prompt(first.group(1))
    data_uri: Optional[str] = None
    if scene:
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
    if data_uri is None:
        LOGGER.warning(
            "[authored] dropping illustration slot on slide %d (style=%s)",
            slide_index + 1,
            style.id,
        )
    # Replace from the last match backwards so spans stay valid; only the
    # first slot may receive the image, the rest are always removed.
    result = html
    for position, match in reversed(list(enumerate(matches))):
        replacement = _resolve_slot(match.group(0), data_uri) if position == 0 else ""
        result = result[: match.start()] + replacement + result[match.end() :]
    return result


async def apply_authored_illustrations(
    htmls: List[str],
    style: _StyleLike,
    image_service: _ImageServiceLike,
) -> List[str]:
    """Generate and inline each slide's illustration slot (at most one per slide).

    Extra slots beyond the first are removed. On generation failure, provider
    placeholders, or unreadable output the slot is removed so the HTML layout —
    which reserves the area explicitly — still renders. Never raises.
    """
    style_prompt = (getattr(style, "illustration_prompt", None) or "").strip()
    if not style_prompt:
        return htmls

    return list(
        await asyncio.gather(
            *(
                apply_authored_illustration(html, style, image_service, i)
                for i, html in enumerate(htmls)
            )
        )
    )
