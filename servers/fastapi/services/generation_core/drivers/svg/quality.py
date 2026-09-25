"""The SVG driver's deck quality pass: per-page classification and advisory.

Runs once every page has an outcome and before publication. Each page is
reported as a :class:`PageOutcome`, classified by the shared rule
(``generation_core/quality/page_outcome.py``), and the classification is
written back into that page's checkpoint quality, so a resumed attempt and
the published receipt read the same verdict. The deck then gets one
contact-sheet vision advisory over the page renders when the runtime profile
asks for it; the advisory never gates or fails the deck.
"""
from __future__ import annotations

from hashlib import sha256

from services.generation_core.quality.advisory_record import advisory_record
from services.generation_core.quality.page_outcome import (
    PageOutcome, classify_page_outcomes, deck_quality_summary,
)


def page_outcome(index: int, payload: dict) -> PageOutcome:
    quality = payload.get("quality") or {}
    return PageOutcome(
        index=index, source_format="svg", source=payload.get("svg_content") or "",
        png_path=payload.get("png_path"), quality=quality,
        attempts=sum(a.get("kind") != "local" for a in payload.get("attempts", [])),
        failures=tuple(item for item in quality.get("failures", []) if isinstance(item, dict)),
        verified=quality.get("verified") is True, flag=payload.get("terminal"))


async def classify_pages(pages, *, job, checkpoint) -> dict[str, object]:
    """Classify every page in place and persist it; return the deck summary."""

    outcomes = classify_page_outcomes([page_outcome(i, payload) for i, (payload, _) in enumerate(pages)])
    for outcome, (payload, _) in zip(outcomes, pages, strict=True):
        payload["quality"] = {**payload["quality"], "classification": outcome.classification,
                              "verified": outcome.verified}
        if outcome.flag is not None:
            payload["flag"] = dict(outcome.flag)
        await checkpoint(job, outcome.index, payload=payload)
    return deck_quality_summary(outcomes)


async def deck_advisory(ctx, pages, *, job, read_png, run_advisory):
    """One vision advisory over the deck's renders, bound to their digests."""

    profile = getattr(ctx, "runtime_profile", None)
    if not getattr(profile, "vision_advisory", False):
        return None
    from services.provider_usage_instrumentation import provider_usage_scope

    renders = [png if png is not None else read_png(payload) for payload, png in pages]
    # Bound to the SVG bytes publication stores, which is what an export reads.
    digests = [sha256((payload.get("svg_content") or "").encode()).hexdigest() for payload, _ in pages]
    if any(not png for png in renders):
        # The sheet numbers what it shows; a missing render would shift every
        # later slide's number, so no advisory is better than a misattributed one.
        return advisory_record({"skipped": "render_missing"}, digests)
    with provider_usage_scope(job_id=job.id if job else None, operation="authored_generation",
                              stage="vqa",
                              attempt_number=job.attempt_number if job else 0):
        vision = await run_advisory(renders, slide_count=len(pages))
    return advisory_record(vision, digests)


__all__ = ["classify_pages", "deck_advisory", "page_outcome"]
