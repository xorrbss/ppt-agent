"""Freeze content-derived deck advice without changing historical page replay."""
from __future__ import annotations

from services.generation_core.drivers.svg.visual_intent import build_deck_visual_intents


def intent_contents(outline, *, source_units=None, raster_analysis=None, raster_pin=None):
    contents = [slide.content for slide in outline.slides]
    if source_units is not None:
        contents = []
        for page in source_units:
            fragments = []
            for block in page.blocks:
                if block.kind == "text":
                    fragments.append(str(block.payload.get("text", "")))
                elif block.kind == "table":
                    fragments.extend(" | ".join(str(cell.get("text", "")) for cell in row)
                                     for row in block.payload.get("rows", []))
            contents.append("\n".join(fragments))
    elif raster_analysis is not None:
        from services.source_content.raster_free_composition import free_raster_items

        for index, page in enumerate(raster_analysis.pages):
            if page.render_mode != "redesign":
                continue
            _page, items, _ledger = free_raster_items(raster_analysis, raster_pin, index)
            fragments = []
            for item in items:
                if item.kind == "text":
                    fragments.append(item.region.text or "")
                elif item.kind == "table":
                    fragments.extend(" | ".join(row) for row in item.rows)
            contents[index] = "\n".join(fragments)
    return contents


async def freeze_visual_intents(*, contents, roles, job, historical_payload, checkpoint, trusted_plan=None):
    """Only new jobs get advice; resumed jobs keep their original frozen input."""
    if job is None:
        return _expected_intents(contents, roles, trusted_plan)
    seed = await checkpoint(job, 0) or {}
    saved = seed.get("deck_visual_intents")
    if saved is None and historical_payload:
        return None
    expected = _expected_intents(contents, roles, trusted_plan)
    if saved is not None:
        if saved != expected:
            raise ValueError("svg_visual_intent_checkpoint_conflict")
        return saved
    seed["deck_visual_intents"] = expected
    await checkpoint(job, 0, payload=seed)
    return expected


def _expected_intents(contents, roles, trusted_plan):
    from copy import deepcopy
    from services.generation_core.drivers.svg.collect import validate_svg_stage_plan
    from services.generation_core.drivers.svg.visual_intent import build_deck_visual_intents
    expected = build_deck_visual_intents(contents, roles)
    plan = validate_svg_stage_plan(trusted_plan)
    if plan is None or plan.get("visual_intents") is None:
        return expected
    intents = plan["visual_intents"]
    if len(intents) != len(expected) or any(actual.get("source_digest") != original.get("source_digest") for actual, original in zip(intents, expected, strict=True)):
        raise ValueError("svg_stage_visual_intent_source_conflict")
    return deepcopy(intents)
