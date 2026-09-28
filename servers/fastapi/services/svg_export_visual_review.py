"""The export receipt's ``visual_review`` domain for an SVG deck.

Filled from what publication recorded: the deck's contact-sheet vision
advisory (``theme.svg.advisory``, see
``services/generation_core/quality/advisory_record.py``) and each page's
classification in ``svgReceipt.quality``. The public contract
(``api.v2.contracts.VisualReviewQualityV3``) has no ``flagged`` status and
forbids extra evidence keys, so the agreed field shape maps onto it as:

* ``passed``      -- the advisory ran on exactly the pages being exported and no
  page is classified for review; evidence carries
  ``measurements.advisoryDigest``;
* ``failed`` + ``svg_visual_review_flagged`` -- same evidence, but a page is
  classified for review (the "flagged" outcome);
* ``failed`` + ``svg_visual_advisory_stale`` -- the advisory reviewed other
  page bytes (a page changed after publication) or its record is corrupt;
* ``failed`` + ``svg_visual_advisory_unavailable`` -- the advisory did not run
  (or was skipped) for this deck.

"Flagged" means exactly what publication reported as flagged: the pages
``review_page_indices`` puts in ``theme.svg.reviewPages`` (generation's
``quality_pages.flagged``). The advisory's own findings are recorded as
``measurements.advisoryFlaggedPages`` but never flag the deck: the advisory is
advisory by contract (``generation_core/quality/vision_advisory.py``, D22 in
``docs/authored-quality-speed-programme-design-20260905.md``: "never a gate"),
and counting it here made a deck generation reported as 0 flagged pages fail
export review as flagged. What the advisory contributes to release is proof
that a visual review ran on exactly these bytes.

Visual review is required for an SVG release whatever happened upstream, so
a missing advisory is ``failed``, not the contract's ``unavailable``: that
status must be ``required: false``, and ``release_readiness`` would then read
a deck nobody looked at as releasable.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from hashlib import sha256

from services.generation_core.quality.advisory_record import (
    ADVISORY_RECORD_SCHEMA,
    advisory_flagged_pages,
    advisory_ran,
    content_digest,
    record_matches,
)
from services.generation_core.quality.page_outcome import review_page_indices


def slide_page_digests(slides) -> list[str]:
    """What the advisory record binds to: the digest of each exported SVG."""

    return [sha256((slide.svg_content or "").encode()).hexdigest() for slide in slides]


def _review_pages(slides) -> list[int]:
    return review_page_indices([((slide.content or {}).get("svgReceipt") or {}).get("quality") or {}
                                for slide in slides])


def svg_visual_review(*, slides, advisory: Mapping | None) -> dict:
    if not advisory_ran(advisory):
        return dict(required=True, status="failed", reasonCode="svg_visual_advisory_unavailable")
    digests = slide_page_digests(slides)
    if not record_matches(advisory, digests):
        return dict(required=True, status="failed", reasonCode="svg_visual_advisory_stale")
    vision = advisory["vision"]
    flagged, review = advisory_flagged_pages(advisory), _review_pages(slides)
    evidence = dict(
        checkerVersion=str(vision.get("promptVersion") or ADVISORY_RECORD_SCHEMA),
        inputDigest=content_digest(digests), outputDigest=content_digest(vision),
        measurements={"advisoryDigest": advisory["digest"], "pageCount": len(digests),
                      "advisoryFlaggedPages": len(flagged), "reviewPages": len(review),
                      "model": str(vision.get("model") or "")},
        thresholds={"maxReviewPages": 0})
    if review:
        return dict(required=True, status="failed", reasonCode="svg_visual_review_flagged",
                    evidence=evidence)
    return dict(required=True, status="passed", evidence=evidence)


def release_readiness(domains: Iterable[Mapping]) -> str:
    """``ready`` exactly when every required domain passed."""

    return "ready" if all(
        domain.get("required") is not True or domain.get("status") == "passed" for domain in domains
    ) else "blocked"


__all__ = ["release_readiness", "slide_page_digests", "svg_visual_review"]
