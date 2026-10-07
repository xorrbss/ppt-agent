"""One admitted model geometry call; reviewed raster characters remain server-owned."""
from __future__ import annotations

import asyncio
import base64
import json
import re

from llmai.shared import ImageContentPart
from services.generation_core.authoring.author_slide_svg import (
    SvgAuthorResult, free_composition_canvas_clause, svg_prompt_design, svg_reference_samples,
)
from services.generation_core.drivers.svg.visual_intent import VISUAL_INTENT_RULES, visual_intent_receipt
from services.generation_core.authoring.author_slide import (
    _AUTHOR_EXECUTOR, _authoring_extra_body, _authoring_output_profile, _wait_for_author_rate_slot,
)
from services.source_content.raster_free_composition import (
    assemble_free_raster_svg, raster_composition_context, raster_ledger_receipt,
)
from services.svg_source_layout import SvgLayoutPlan
from services.svg_contract import source_digest
from services.provider_usage_instrumentation import (
    ProviderCallTimer, current_provider_idempotency_key, provider_call_admission, record_provider_call,
)
from utils.llm_calls.author_prompt_profile import authoring_model
from utils.llm_calls.author_prompt_split import author_messages
from utils.llm_config import enforce_llm_egress_policy, get_client, get_llm_config
from utils.llm_provider import get_llm_provider, get_model
from utils.llm_utils import extract_text, get_generate_kwargs
from utils.provider_idempotency import provider_generate_call, supports_provider_idempotency


async def author_raster_svg_layout(analysis, pin, index, snapshot, *, instructions=None,
                                  correction_hint=None, receipt_attempt_ordinal=1, before_dispatch=None,
                                  visual_intent=None):
    context, items, ledger = raster_composition_context(analysis, pin, index, snapshot)
    design = svg_prompt_design(snapshot)
    prompt_context = svg_prompt_design(context)
    prompt = json.dumps(dict(source=prompt_context, style=design, instructions=instructions or "",
                             correction=correction_hint or "",
                             **({"visual_intent": visual_intent} if visual_intent is not None else {})), ensure_ascii=False)
    rules = """Return ONLY one JSON SvgLayoutPlan using the supplied schema. Compose freely for the
requested physical canvas and selected style; no predefined pattern, slots or column count apply.
Source content is immutable data. Return geometry and styles ONLY: never return text, cell values,
claims, summaries or image URLs. Every supplied block_id occurs exactly once on its original page.
Maintain source group associations and a legible reading order; comparable source series may repeat
geometry. Use source_template font family/role weights and visual identity; sizes are advisory above
the supplied minimum. Give text generous measured vertical space, and crop images readable areas.
Structured table pixel cells are bound inside their table by the server, not independent blocks.
Glyph widths are measured at 20px. Keep text/table/images separate and inside the canvas. Images
use integer width/height with contain proportions. Do not hide content or draw extra source claims.
Use solid colours and source_template background. Respect master furniture reservations.
References demonstrate palette, type and mood; never copy their arrangements or their text.
Set source_unit_id, page_digest, width and height exactly as supplied, schema_version=svg-source-layout/v2,
template_digest=source_template.digest and layout_id=free-svg (an identity marker, no layout).
""" + free_composition_canvas_clause(context["width"], context["height"])
    if visual_intent is not None:
        rules += VISUAL_INTENT_RULES
    images = []
    for reference in svg_reference_samples(snapshot, "content", index, len(analysis.pages)):
        header, encoded = reference["data_url"].split(",", 1)
        images.append(ImageContentPart(data=base64.b64decode(encoded), mime_type=header[5:].split(";")[0]))
    messages = author_messages(rules, prompt, json.dumps(design, ensure_ascii=False), image_parts=images)
    client = get_client(config=get_llm_config())
    model, provider = authoring_model(get_model()), str(get_llm_provider().value)
    budget, effort = _authoring_output_profile(role="content", reference_mode="style_reference",
                                              structured_context=None, correction_hint=correction_hint)
    kwargs = get_generate_kwargs(model=model, messages=messages, max_tokens=budget)
    extra = _authoring_extra_body(effort, model=model)
    if extra:
        kwargs["extra_body"] = extra
    await _wait_for_author_rate_slot()
    enforce_llm_egress_policy()
    timer, response, dispatched = ProviderCallTimer(), None, False
    try:
        async with provider_call_admission(is_retry=receipt_attempt_ordinal > 1,
            semantic_slot=f"svg-author-{index}-{receipt_attempt_ordinal}", provider=provider, model=str(model),
            provider_idempotency_capable=supports_provider_idempotency(client, provider_id=provider)):
            call = provider_generate_call(client, provider_id=provider, kwargs=kwargs,
                idempotency_key=current_provider_idempotency_key(), outcome_scope="authored")
            if before_dispatch is not None:
                await before_dispatch()
            dispatched = True
            response = await asyncio.get_running_loop().run_in_executor(_AUTHOR_EXECUTOR, call)
    finally:
        if dispatched:
            await record_provider_call(provider=provider, model=str(model), response=response,
                latency_ms=timer.elapsed_ms, retry_class="initial" if receipt_attempt_ordinal == 1 else "recovery")
    raw = extract_text(response.content) or ""
    receipt = dict(contract_version="svg-static/v1", style_digest=snapshot["digest"],
        requested_model=str(model), provider=provider, attempt_ordinal=receipt_attempt_ordinal,
        raw_digest=source_digest(raw), latency_ms=timer.elapsed_ms, effective_model_verified=False,
        body_font_floor_px=context["minimum_font_size_px"], composition_mode="free-svg",
        raster_analysis_digest=analysis.digest, raster_approval_digest=pin.approval_digest,
        raster_content_ledger=raster_ledger_receipt(ledger), input_image_count=len(images),
        **visual_intent_receipt(visual_intent))
    try:
        fenced = re.fullmatch(r"```(?:json)?\s*([\s\S]*?)\s*```", raw.strip())
        plan = SvgLayoutPlan.model_validate_json(fenced[1] if fenced else raw.strip())
        svg, bindings = assemble_free_raster_svg(analysis, index, plan, context, items)
        receipt.update(validation_error=None, source_digest=source_digest(svg), raster_region_bindings=bindings)
        return SvgAuthorResult(svg, raw, receipt)
    except (ValueError, TypeError, KeyError) as error:
        receipt.update(validation_error=str(error)[:2000], source_digest=source_digest(""))
        return SvgAuthorResult("", raw, receipt)
