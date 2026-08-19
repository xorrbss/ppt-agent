"""Image attachments and image rules for the authored slide prompt.

Split out of ``author_slide.py``: that module is an audited size exception, and
the picture-related rules and attachment helpers form a self-contained group.
Two kinds of image reach one authoring call and they carry opposite
instructions — a style reference is art direction that must never be copied, a
source image is an asset the slide may place — so the code that builds them
lives together, where the difference is visible.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path

from llmai.shared import ImageContentPart

from services.source_images.models import (
    SUPPORTED_SOURCE_IMAGE_MIME_TYPES,
    SourceImageCandidate,
)
from utils.llm_calls import author_slide_rules

logger = logging.getLogger(__name__)

#: An attachment larger than this is dropped rather than sent: the provider
#: rejects oversized parts, and one unreadable picture must not fail a slide.
REFERENCE_IMAGE_MAX_BYTES = 8 * 1024 * 1024


# The slot rules themselves live in ``author_slide_rules`` with the rest of the
# design-system prose; re-exported here because this module owns the two-kinds-of-
# image story and every caller of ``illustration_slot_rules`` already imports it.
ILLUSTRATION_SLOT_RULES = author_slide_rules.ILLUSTRATION_SLOT_RULES

#: The one sentence the source-image feature has to widen: with the feature on a
#: slide may also place a picture the user uploaded, named by asset id.
_NO_OTHER_IMAGE = "Do not reference any other image (no external URLs, no data URIs)."
_LISTED_ASSETS_ALLOWED = (
    "The images available to a slide are this slot and the source-image asset ids\n"
    "  listed for that slide: no external URLs, no data URIs, no other image source."
)

# Derived from the rule above rather than copied, because a second copy of this
# prose is how the source-image wording and the illustration-relevance wording
# silently drift apart: the copy that is not edited still reads correctly and
# every test still passes. Source-image reuse widens the allowed set by exactly
# one entry, so the sentence is restated rather than contradicted a paragraph
# later. It is only ever shown when the feature is on: an unset gate must leave
# the prompt byte-identical, and a model told about asset ids it was never given
# would author an `<img data-asset-id>` that no stage of the ordinary flow can
# resolve. ``test_illustration_slot_rules_allow_only_the_slot_and_listed_assets``
# fails loudly if the sentence below stops matching the rule text.
ILLUSTRATION_SLOT_RULES_WITH_SOURCE_IMAGES = ILLUSTRATION_SLOT_RULES.replace(
    _NO_OTHER_IMAGE, _LISTED_ASSETS_ALLOWED
)


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


def source_image_parts(
    candidates: Sequence[SourceImageCandidate],
) -> tuple[tuple[SourceImageCandidate, ...], list[ImageContentPart]]:
    """Attach user-owned candidates, returning only the ones actually attached.

    The prompt lists these candidates by asset id, so a picture that could not be
    read must not be described to the model: it would offer an id whose image is
    not in the request.
    """
    attached: list[SourceImageCandidate] = []
    parts: list[ImageContentPart] = []
    for candidate in candidates:
        if candidate.mime_type not in SUPPORTED_SOURCE_IMAGE_MIME_TYPES:
            logger.warning("Skipping source image with unsupported media type")
            continue
        try:
            if candidate.path.stat().st_size > REFERENCE_IMAGE_MAX_BYTES:
                logger.warning("Skipping oversized authored source image")
                continue
            data = candidate.path.read_bytes()
        except OSError:
            logger.warning("Unable to read authored source image")
            continue
        attached.append(candidate)
        parts.append(ImageContentPart(data=data, mime_type=candidate.mime_type))
    return tuple(attached), parts




def illustration_slot_rules(*, source_images_enabled: bool) -> str:
    """Pick the slot rule that matches what this deck can actually resolve.

    A model told about asset ids the run never offers would author a reference
    no stage can resolve, so the wider rule appears only with the feature on.
    """
    return (
        ILLUSTRATION_SLOT_RULES_WITH_SOURCE_IMAGES
        if source_images_enabled
        else ILLUSTRATION_SLOT_RULES
    )
