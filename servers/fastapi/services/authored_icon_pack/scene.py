"""The scene archetype's picture: one large object cutout per slide.

A scene slide is a real thing - a machine, a room, a site - with its parts
called out. The parts are HTML callouts the authoring model writes (Stage 2
owns that markup); this module produces only the object they point at, as a
transparent PNG the slide inlines the same way an illustration is inlined.
The prompt draws the object the takeaway names and lets the node labels
steer which parts must be visible, but no label is ever drawn: the callouts
are the labels, and they stay editable text.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from services.authored_icon_pack.checks import check_no_text, check_transparency
from services.authored_icon_pack.inline import cutout_data_uri
from services.authored_icon_pack.provider import (
    TransparentPngProvider,
    TransparentPngUnsupported,
)
from utils.runtime_settings import RuntimeSettings

LOGGER = logging.getLogger(__name__)

MAX_CUTOUT_NODES = 6
_MAX_TAKEAWAY_CHARS = 80
_MAX_NODE_CHARS = 24


@dataclass(frozen=True)
class SceneCutout:
    path: str
    data_uri: str
    width: int
    height: int
    checks: dict
    latency_seconds: float


def _clip(text: str, limit: int) -> str:
    cleaned = " ".join((text or "").split())
    return cleaned[:limit]


def build_cutout_prompt(
    takeaway: str,
    nodes: Sequence[str],
    *,
    style_prompt: Optional[str] = None,
) -> str:
    """One isolated object, parts visible, on transparency, no text."""
    subject = _clip(takeaway, _MAX_TAKEAWAY_CHARS)
    parts = [_clip(node, _MAX_NODE_CHARS) for node in nodes[:MAX_CUTOUT_NODES]]
    parts = [part for part in parts if part]
    style = (style_prompt or "").strip()
    style_clause = f" Visual style: {style}." if style else ""
    parts_clause = (
        " The following parts or aspects must each be clearly visible as a "
        "distinct region of the object so a caption can point at it: "
        + "; ".join(f"'{part}'" for part in parts)
        + "."
        if parts
        else ""
    )
    return (
        "A single isolated object cutout on a fully TRANSPARENT background, "
        "seen in three-quarter view, filling most of the canvas with nothing "
        "touching the canvas edge. No floor, no shadow, no scenery, no frame. "
        f"The object depicts: '{subject}'.{parts_clause}{style_clause} "
        "Clean flat vector illustration with clear separable parts. "
        "ABSOLUTELY NO TEXT, letters, digits, labels, arrows or captions "
        "anywhere in the image."
    )


async def generate_scene_cutout(
    *,
    takeaway: str,
    nodes: Sequence[str],
    output_directory: str,
    style_prompt: Optional[str] = None,
    settings: RuntimeSettings | None = None,
    provider: Optional[TransparentPngProvider] = None,
    raise_on_failure: bool = False,
) -> Optional[SceneCutout]:
    """Generate and vet one scene's object; None means the slide has no picture.

    ``output_directory`` is the deck's image directory (the same one the
    illustration service writes to). A picture that fails the transparency
    or no-text check is discarded, because a cutout that paints a rectangle
    or bakes a label in is worse than the HTML-only fallback figure.
    """
    provider = provider or TransparentPngProvider(settings)
    if not provider.available:
        if raise_on_failure:
            raise TransparentPngUnsupported("scene cutout needs a gpt-image provider")
        return None
    prompt = build_cutout_prompt(takeaway, nodes, style_prompt=style_prompt)
    started = time.perf_counter()
    try:
        path = await provider.generate(
            prompt, output_directory, size="1024x1024", semantic_slot="image.cutout"
        )
    except Exception as error:  # noqa: BLE001 - degrade, never break the deck
        LOGGER.warning("[authored] scene cutout generation failed: %s", error)
        if raise_on_failure:
            raise
        return None
    latency = time.perf_counter() - started
    payload = await asyncio.to_thread(Path(path).read_bytes)
    transparency = check_transparency(payload)
    text = await check_no_text(payload, forbidden=[takeaway, *nodes])
    checks = {"transparency": transparency.to_dict(), "text": text.to_dict()}
    if not (transparency.passed and text.passed):
        LOGGER.warning("[authored] scene cutout rejected: %s", checks)
        await asyncio.to_thread(Path(path).unlink, True)
        if raise_on_failure:
            raise RuntimeError("scene cutout failed acceptance checks")
        return None
    data_uri = cutout_data_uri(path)
    if data_uri is None:
        if raise_on_failure:
            raise RuntimeError("scene cutout could not be inlined")
        return None
    from services.authored_icon_pack.sheet import open_rgba

    width, height = open_rgba(payload).size
    return SceneCutout(
        path=path,
        data_uri=data_uri,
        width=width,
        height=height,
        checks=checks,
        latency_seconds=latency,
    )


__all__ = [
    "MAX_CUTOUT_NODES",
    "SceneCutout",
    "build_cutout_prompt",
    "generate_scene_cutout",
]
