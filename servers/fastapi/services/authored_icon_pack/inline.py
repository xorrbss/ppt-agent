"""Turn a pack icon or a cutout into the data URI a slide references.

Illustrations are inlined as JPEG because they are opaque. Icons and cutouts
are alpha-masked, so they stay PNG - the one alpha format every consumer in
the chain (renderer admission, hybrid export, PowerPoint) accepts - and are
kept inside the renderer's budget by resizing and, if that is not enough,
palette quantization rather than by dropping the alpha channel.
"""

from __future__ import annotations

import base64
import io
import logging
from pathlib import Path
from typing import Optional

from PIL import Image

from utils.renderer_isolation import MAX_ASSET_BYTES

LOGGER = logging.getLogger(__name__)

#: Longest edge an inlined cutout keeps: a 1024px source spans at most ~500
#: of the slide's 1280px, and prints past 300 DPI at that size.
CUTOUT_MAX_EDGE = 1024
#: Longest edge of an inlined icon: pictograms render at 48-160px on-slide.
ICON_MAX_EDGE = 384
#: Raw-byte ceiling for one inlined picture, leaving base64 headroom and the
#: slide's own markup inside ``MAX_HTML_BYTES``.
INLINE_MAX_BYTES = min(MAX_ASSET_BYTES, 900 * 1024)


def _fit(image: Image.Image, max_edge: int) -> Image.Image:
    width, height = image.size
    longest = max(width, height)
    if longest <= max_edge:
        return image
    scale = max_edge / float(longest)
    return image.resize(
        (max(1, round(width * scale)), max(1, round(height * scale))),
        Image.LANCZOS,
    )


def _encode(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, "PNG", optimize=True)
    return buffer.getvalue()


def shrink_png_for_inline(payload: bytes, *, max_edge: int) -> bytes:
    """Resize, then quantize, until the PNG fits the inline budget.

    Returns the original bytes if Pillow cannot read them: the caller's own
    size ceiling still applies and a readable picture beats none.
    """
    try:
        with Image.open(io.BytesIO(payload)) as source:
            source.load()
            image = _fit(source.convert("RGBA"), max_edge)
    except Exception as error:  # noqa: BLE001 - provider output is untrusted
        LOGGER.warning("could not shrink transparent PNG for inlining: %s", error)
        return payload
    encoded = _encode(image)
    if len(encoded) <= INLINE_MAX_BYTES:
        return encoded
    quantized = image.quantize(256, method=Image.Quantize.FASTOCTREE)
    encoded = _encode(quantized)
    return encoded


def png_data_uri(path: Path | str, *, max_edge: int) -> Optional[str]:
    """Inline one transparent PNG, or None if the file cannot be read or fit."""
    file = Path(path)
    try:
        payload = file.read_bytes()
    except OSError as error:
        LOGGER.warning("unable to read icon-pack image: %s", error.strerror or "")
        return None
    shrunk = shrink_png_for_inline(payload, max_edge=max_edge)
    if len(shrunk) > INLINE_MAX_BYTES:
        LOGGER.warning("icon-pack image too large to inline (%d bytes)", len(shrunk))
        return None
    return "data:image/png;base64," + base64.b64encode(shrunk).decode("ascii")


def icon_data_uri(path: Path | str) -> Optional[str]:
    if str(path).lower().endswith(".svg"):
        import base64
        from pathlib import Path

        return "data:image/svg+xml;base64," + base64.b64encode(Path(path).read_bytes()).decode("ascii")
    return png_data_uri(path, max_edge=ICON_MAX_EDGE)


def cutout_data_uri(path: Path | str) -> Optional[str]:
    return png_data_uri(path, max_edge=CUTOUT_MAX_EDGE)


__all__ = [
    "CUTOUT_MAX_EDGE",
    "ICON_MAX_EDGE",
    "INLINE_MAX_BYTES",
    "cutout_data_uri",
    "icon_data_uri",
    "png_data_uri",
    "shrink_png_for_inline",
]
