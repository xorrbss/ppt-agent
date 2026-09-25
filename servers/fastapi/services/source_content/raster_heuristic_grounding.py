"""What the heuristic page plan may publish as editable text: nothing.

Editable text must be read by two independent readers that agree. On the
structured path the second reader is the vision model's transcription
(``raster_structure_apply._resolve_by_agreement``). The heuristic plan, used
when no structurer is configured or the structuring of a page failed, has only
the local OCR passes, and those share failure modes: offline on the evaluation
decks the page pass and the line re-read read 17 lines identically wrong
("Al"/"시" for "AI", "(<민)" for "(KPI)", a dropped middle dot) even when both
had to contain the published text. Agreement between them is not proof, as
``raster_text_guards`` already states for numbers.

So no heuristic reading is independently confirmed: every title, body and
table region keeps its source pixels (lossless), with its reading kept only as
the suggestion text. A title stays in the title band (``group_id`` "title").
The page-level ``heuristic_plan`` advisory (or the deck-level
``vision_structurer_unavailable`` one) says why; the regions add no advisory
of their own.
"""

from __future__ import annotations

from dataclasses import replace

from services.source_content.raster_page_plan import PagePlan, PlannedRegion

REASON = "unconfirmed_text_pixels"


def _pixels(region: PlannedRegion) -> PlannedRegion:
    if region.role == "table":
        return replace(
            region,
            text="\n".join("\t".join(row) for row in region.table_cells or ()),
            table_cells=None,
            editable_fallback=True,
            reason=REASON,
        )
    return replace(
        region,
        role="screenshot",
        editable_fallback=True,
        reason=REASON,
        group_id="title" if region.role == "title" else region.group_id,
    )


def _editable(region: PlannedRegion) -> bool:
    if region.editable_fallback:
        return False
    return region.role in ("title", "body") or (region.role == "table" and region.table_cells is not None)


def unconfirmed_text_as_pixels(plan: PagePlan) -> PagePlan:
    """The heuristic plan with every editable text or table region kept as pixels."""

    if not any(_editable(region) for region in plan.regions):
        return plan
    return replace(plan, regions=tuple(_pixels(r) if _editable(r) else r for r in plan.regions))
