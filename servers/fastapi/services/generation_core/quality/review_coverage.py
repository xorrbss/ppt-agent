"""Read review coverage by durable page identity, never by deck position.

Only the paired reviewedPageIndices/reviewedPngDigests vectors describe which
renders were supplied. slideCount describes the envelope, not that coverage.
Legacy envelopes without either vector remain distinguishable from explicit
empty coverage. This reader never changes the signed envelope.
"""
from __future__ import annotations

from collections.abc import Mapping


class ReviewCoverage:
    def __init__(self, vision: Mapping):
        self.vision = vision
        self.explicit = "reviewedPageIndices" in vision or "reviewedPngDigests" in vision
        self.slide_count = vision.get("slideCount")
        indices, digests = vision.get("reviewedPageIndices"), vision.get("reviewedPngDigests")
        excluded = vision.get("unreviewedPages", [])
        self.valid = (isinstance(indices, list) and isinstance(digests, list)
            and len(indices) == len(digests)
            and all(type(index) is int and index >= 0 for index in indices)
            and len(set(indices)) == len(indices)
            and all(isinstance(digest, str) and len(digest) == 64
                    and all(c in "0123456789abcdef" for c in digest) for digest in digests)
            and isinstance(excluded, list)
            and all(type(index) is int and index >= 0 for index in excluded)
            and not set(indices).intersection(excluded))
        self.digests = dict(zip(indices, digests, strict=True)) if self.valid else {}

    @property
    def indices(self) -> list[int]:
        return list(self.digests)

    def digest(self, index: int) -> str | None:
        """None means unreviewed (including malformed/ambiguous coverage)."""
        return self.digests.get(index)

    def matches(self, indices, digests) -> bool:
        indices, digests = list(indices), list(digests)
        return (self.valid and self.indices == indices and len(indices) == len(digests)
                and all(self.digest(index) == digest for index, digest in zip(indices, digests, strict=True)))

    def unreviewed(self, indices) -> list[int]:
        return [index for index in indices if self.digest(index) is None]

    def projection(self, indices) -> dict:
        indices = list(indices)
        return dict(reviewedPages=[index for index in indices if self.digest(index) is not None],
                    unreviewedPages=self.unreviewed(indices), totalPages=len(indices))

    def findings(self, index: int | None = None) -> list:
        slides = self.vision.get("slides")
        if not isinstance(slides, list):
            return []
        return slides if index is None else [entry for entry in slides
            if isinstance(entry, Mapping) and entry.get("index") == index]

    @property
    def ran(self) -> bool:
        return "skipped" not in self.vision and isinstance(self.vision.get("slides"), list)

    @property
    def design_review(self) -> Mapping:
        review = self.vision.get("designReview")
        return review if isinstance(review, Mapping) else {}

    @property
    def pages(self) -> list:
        pages = self.design_review.get("pages")
        return pages if isinstance(pages, list) else []

    @property
    def deck_flow(self):
        return self.design_review.get("deckFlow")

    @property
    def gate(self):
        return self.vision.get("designGate")


def review_coverage(vision: object) -> ReviewCoverage:
    return ReviewCoverage(vision if isinstance(vision, Mapping) else {})
