"""Shared types for source-image reuse in ordinary authored generation.

A source image is a picture the user already owns: one uploaded directly, or one
extracted from an uploaded PPTX. It is offered to the slide author as a
candidate to place, in contrast to a style reference (art direction only) and to
a generated illustration (invented pixels).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

#: Only formats the authoring call can attach as an image content part.
SUPPORTED_SOURCE_IMAGE_MIME_TYPES = frozenset(
    {"image/png", "image/jpeg", "image/webp"}
)

#: Smaller pictures are bullet glyphs, rules, and icon chrome rather than
#: content worth carrying into a new deck.
MIN_SOURCE_IMAGE_EDGE_PX = 200

#: A picture repeated on at least this many source slides is deck furniture
#: (logo, watermark) and is handled by the master contract, never placed as
#: slide content.
FURNITURE_REPEAT_THRESHOLD = 3


@dataclass(frozen=True)
class SourceImageCandidate:
    """One reusable picture, addressable by the slide author.

    ``asset_id`` is the stable identifier the model writes back as
    ``data-asset-id``; it is resolved against the candidate set of that slide,
    so an identifier the model invents can never reach the filesystem.
    """

    asset_id: str
    path: Path
    mime_type: str
    width: int
    height: int
    digest: str
    #: Zero-based slide position in the uploaded PPTX, when it came from one.
    source_slide_index: Optional[int] = None
    #: Heading of the source slide, used to match the picture to a new slide.
    source_heading: Optional[str] = None

    @property
    def aspect_ratio(self) -> float:
        return self.width / self.height if self.height else 0.0
