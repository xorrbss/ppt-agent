"""Style reference images attached to a page-authoring call, and the byte caps.

Moved out of ``author_slide_images`` unchanged so the svg author does not
import the HTML author's image rules; ``author_slide_images`` re-exports every
name here.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path

from llmai.shared import ImageContentPart

logger = logging.getLogger(__name__)

#: An attachment larger than this is dropped rather than sent: the provider
#: rejects oversized parts, and one unreadable picture must not fail a slide.
REFERENCE_IMAGE_MAX_BYTES = 8 * 1024 * 1024
# Aggregate provider preview budget. This is a byte/resource guard, not a
# semantic image-count limit; any number of smaller source slots may be attached.
SOURCE_IMAGE_PREVIEW_TOTAL_MAX_BYTES = 32 * 1024 * 1024


def reference_parts(reference_images: Sequence[Path]) -> list[ImageContentPart]:
    parts: list[ImageContentPart] = []
    mime_by_suffix = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }
    for path in reference_images:
        try:
            if path.stat().st_size > REFERENCE_IMAGE_MAX_BYTES:
                # References may come from a private exact preset. Never expose
                # their local filesystem identity in operational logs.
                logger.warning("Skipping oversized authored reference image")
                continue
            mime = mime_by_suffix.get(path.suffix.lower())
            if mime:
                parts.append(ImageContentPart(data=path.read_bytes(), mime_type=mime))
        except OSError:
            logger.warning("Unable to read authored reference image")
    return parts


__all__ = [
    "REFERENCE_IMAGE_MAX_BYTES",
    "SOURCE_IMAGE_PREVIEW_TOTAL_MAX_BYTES",
    "reference_parts",
]
