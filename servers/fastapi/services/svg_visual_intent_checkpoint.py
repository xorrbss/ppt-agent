"""Freeze content-derived deck advice without changing historical page replay."""
from __future__ import annotations

import re

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


async def freeze_visual_intents(*, contents, roles, job, historical_payload, checkpoint, trusted_plan=None, quality_policy=None):
    """Only new jobs get advice; resumed jobs keep their original frozen input."""
    if job is None:
        return _expected_intents(contents, roles, trusted_plan, quality_policy=quality_policy)
    seed = await checkpoint(job, 0) or {}
    saved = seed.get("deck_visual_intents")
    if saved is None and historical_payload:
        return None
    expected = _expected_intents(contents, roles, trusted_plan, quality_policy=quality_policy)
    if saved is not None:
        if saved != expected and not _previous_quality_support_matches(
                saved, expected, contents, quality_policy=quality_policy, trusted_plan=trusted_plan):
            raise ValueError("svg_visual_intent_checkpoint_conflict")
        return saved
    seed["deck_visual_intents"] = expected
    await checkpoint(job, 0, payload=seed)
    return expected


def _previous_quality_support_matches(saved, expected, contents, *, quality_policy, trusted_plan):
    """Recognize only the exact v2 advice frozen before topic deduplication."""
    if (not isinstance(quality_policy, dict) or quality_policy.get("version") != 2
            or trusted_plan is not None or not isinstance(expected, list)
            or len(expected) != len(contents)):
        return False
    from copy import deepcopy
    from services.generation_core.drivers.svg.visual_intent import _TABLE_DIVIDER, source_content_evidence

    previous = deepcopy(expected)
    for content, intent in zip(contents, previous, strict=True):
        emphasis = intent["emphasis"]
        emphasis["supporting_facts"] = [block["text"]
            for block in source_content_evidence(str(content or ""))["blocks"]
            if block["text"].strip() != emphasis["focal_text"]
            and not re.match(r"^#{1,6}\s+", block["text"].strip())
            and not _TABLE_DIVIDER.match(block["text"])][:3]
    # All source digests, roles, focal text, density, relations and neighboring
    # advice still have to agree. Never rewrite the saved frozen vector.
    return saved == previous


def _expected_intents(contents, roles, trusted_plan, *, quality_policy=None):
    from copy import deepcopy
    from services.generation_core.drivers.svg.collect import validate_svg_stage_plan
    expected = (build_deck_visual_intents(contents, roles, quality_policy=quality_policy)
                if isinstance(quality_policy, dict) and quality_policy.get("version") == 2
                else build_deck_visual_intents(contents, roles))
    plan = validate_svg_stage_plan(trusted_plan)
    if plan is None or plan.get("visual_intents") is None:
        return expected
    intents = plan["visual_intents"]
    if len(intents) != len(expected) or any(actual.get("source_digest") != original.get("source_digest") for actual, original in zip(intents, expected, strict=True)):
        raise ValueError("svg_stage_visual_intent_source_conflict")
    return deepcopy(intents)
