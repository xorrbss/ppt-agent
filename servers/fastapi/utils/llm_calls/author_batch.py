"""One-call Authored HTML generation for a bounded group of slides.

This module deliberately sits beside, rather than inside, ``author_slide``.
The single-slide API remains the repair/compatibility contract; this module
changes only the provider payload and response framing so one physical call can
return at most three independently validated HTML documents.
"""

from __future__ import annotations

import asyncio
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from llmai import get_client
from llmai.shared import SystemMessage, TextContentPart, UserMessage

from models.authored_infographic_plan import AuthoredSlideInfographic
from models.authored_visual_plan import AuthoredSlideVisualIntent
from utils.authored_infographic import finalize_infographic_slide_html
from services.authored_header_body_extent import ensure_body_root_horizontal_extent
from services.provider_usage_instrumentation import (
    ProviderCallTimer,
    current_provider_idempotency_key,
    provider_call_admission,
    record_provider_call,
)
from services.public_operation_admission import PublicAdmissionRejected
from services.source_images.models import SourceImageCandidate
from utils.authored_dom_quality import validate_declared_authored_qids
from utils.authored_font_policy import enforce_authored_font_floor
from utils.authored_fragmentation_gate import fit_font_floor_px
from utils.authored_image_slots import scan_slots, source_slot_id_for_asset
from utils.llm_calls import author_slide_prompt, author_slide_rules
from utils.llm_calls.author_prompt_cache_key import apply_prompt_cache_key
from utils.llm_calls.author_prompt_profile import authoring_model
from utils.llm_calls.author_prompt_split import (
    invariant_prefix_source,
    is_authored_prompt_system_hoist_enabled,
    split_deck_invariant,
)
from utils.llm_calls.author_slide_effort import (
    AUTHOR_MAX_TOKENS,
    EXACT_RECONSTRUCTION,
    authoring_output_profile,
)
from utils.llm_calls.author_slide import (
    STYLE_REFERENCE,
    Brand,
    _AUTHOR_EXECUTOR,
    _author_prompt,
    _authoring_extra_body,
    _bind_offered_source_image_slots,
    _rejected_html_context,
    _register_author_rate_limit,
    _strip_nonvisual_pointer_events,
    _wait_for_author_rate_slot,
    authored_semantic_dual_output_mode,
    extract_html_document,
    normalize_reference_mode,
    validate_authored_slide_html,
)
from utils.llm_calls.author_slide_images import reference_parts, source_image_parts
from utils.llm_calls.author_slide_rules import TEMPLATE_PAGE_NUMBER_MODE
from utils.llm_client_error_handler import handle_llm_client_exceptions
from utils.llm_config import enforce_llm_egress_policy, get_llm_config
from utils.llm_provider import get_llm_provider, get_model
from utils.llm_utils import extract_text, get_generate_kwargs
from utils.provider_idempotency import (
    ProviderOutcomeError,
    provider_generate_call,
    supports_provider_idempotency,
)
from services.authored_canvas import DEFAULT_CANVAS_HEIGHT_PX


MAX_BATCH_SLIDES = 3
_BATCH_ITEM_PATTERN = re.compile(
    r'<authored-slide\s+index=["\'](?P<index>\d+)["\']\s*>'
    r"(?P<html>.*?)</authored-slide\s*>",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class AuthoredBatchSlideRequest:
    index: int
    content: str
    role: str
    reference_images: tuple[Path, ...] = ()
    reference_mode: str = STYLE_REFERENCE
    structured_context: dict | None = None
    page_number_mode: str = TEMPLATE_PAGE_NUMBER_MODE
    requirements_enabled: bool = False
    chapter_breadcrumb: bool = False
    chapter_prefix: str | None = None
    instructions: str | None = None
    illustration_allowed: bool | None = None
    visual_intent: AuthoredSlideVisualIntent | None = None
    infographic: AuthoredSlideInfographic | None = None
    capacity_brief: str | None = None
    source_images: tuple[SourceImageCandidate, ...] = ()
    require_all_source_images: bool = False
    source_image_placement_plan: Mapping | None = None
    correction_hint: str | None = None
    rejected_html: str | None = None
    # G0: the deck's already-authored anchor slide, offered as data.
    style_anchor: object | None = None
    canvas_height_px: int = DEFAULT_CANVAS_HEIGHT_PX


@dataclass(frozen=True)
class AuthoredBatchCallResult:
    htmls: Mapping[int, str]
    errors: Mapping[int, BaseException]


@dataclass(frozen=True)
class _PreparedSlide:
    request: AuthoredBatchSlideRequest
    prompt: str
    image_parts: tuple[object, ...]
    source_slot_candidates: Mapping[str, SourceImageCandidate]
    output_budget: int
    reasoning_effort: str


def _prepare_slide(
    request: AuthoredBatchSlideRequest,
    *,
    design_system: str,
    brand: Brand,
    slide_count: int,
) -> _PreparedSlide:
    mode = normalize_reference_mode(request.reference_mode)
    output_budget, reasoning_effort = authoring_output_profile(
        role=request.role,
        reference_mode=mode,
        structured_context=request.structured_context,
        correction_hint=request.correction_hint,
    )
    prompt = _author_prompt(
        request.content,
        design_system,
        brand,
        request.role,
        request.index,
        slide_count,
        structured_context=request.structured_context,
        page_number_mode=request.page_number_mode,
        requirements_enabled=request.requirements_enabled,
        chapter_breadcrumb=request.chapter_breadcrumb,
        chapter_prefix=request.chapter_prefix,
        instructions=request.instructions,
        illustration_allowed=request.illustration_allowed,
        visual_intent=request.visual_intent,
        infographic=request.infographic,
        capacity_brief=request.capacity_brief,
        style_anchor=request.style_anchor,
        canvas_height_px=request.canvas_height_px,
    )
    if request.correction_hint:
        prompt += (
            "\n\nSERVER VALIDATION CORRECTION FOR THIS RETRY (authoritative):\n"
            + request.correction_hint
        )
        prompt += _rejected_html_context(request.rejected_html)

    reference_image_parts = reference_parts(request.reference_images)
    if mode == EXACT_RECONSTRUCTION and not (
        len(request.reference_images) == len(reference_image_parts) == 1
    ):
        raise ValueError(
            "Exact reconstruction requires exactly one readable reference image"
        )
    source_candidates, offered_source_parts = (
        ((), [])
        if mode == EXACT_RECONSTRUCTION
        else source_image_parts(request.source_images)
    )
    if mode == EXACT_RECONSTRUCTION:
        reference_guidance = author_slide_rules.EXACT_RECONSTRUCTION_GUIDANCE
    else:
        reference_guidance = (
            author_slide_rules.STYLE_REFERENCE_GUIDANCE
            if reference_image_parts
            else ""
        )
    source_slot_candidates = {
        source_slot_id_for_asset(candidate.asset_id): candidate
        for candidate in source_candidates
    }
    source_guidance = author_slide_prompt.source_image_brief(
        source_slot_candidates,
        required=request.require_all_source_images,
    )
    if not request.require_all_source_images:
        source_guidance += author_slide_prompt.planned_source_image_brief(
            request.source_image_placement_plan or (request.structured_context or {}).get("source_image_placement_plan")
        )
    if request.require_all_source_images and source_slot_candidates:
        source_guidance = (
            "\n\nSLIDE-SPECIFIC SOURCE AUTHORITY: Every listed source slot is "
            "required on this slide and must be materially visible inside the "
            "slide viewport. URL and invented-image bans remain in force."
            + source_guidance
        )
    return _PreparedSlide(
        request=request,
        prompt=prompt + reference_guidance + source_guidance,
        image_parts=tuple([*reference_image_parts, *offered_source_parts]),
        source_slot_candidates=source_slot_candidates,
        output_budget=output_budget,
        reasoning_effort=reasoning_effort,
    )


def _parse_batch_response(text: str, expected: set[int]) -> tuple[dict[int, str], dict[int, BaseException]]:
    parsed: dict[int, str] = {}
    errors: dict[int, BaseException] = {}
    duplicates: set[int] = set()
    for match in _BATCH_ITEM_PATTERN.finditer(text):
        index = int(match.group("index"))
        if index not in expected:
            continue
        if index in parsed:
            duplicates.add(index)
            continue
        parsed[index] = match.group("html").strip()
    for index in duplicates:
        parsed.pop(index, None)
        errors[index] = ValueError("authored_batch_duplicate_slide")
    for index in expected - set(parsed) - set(errors):
        errors[index] = ValueError("authored_batch_slide_missing")
    return parsed, errors


def _postprocess_slide(
    prepared: _PreparedSlide,
    raw_html: str,
    *,
    brand: Brand,
    normalization_receipt: list[dict] | None = None,
) -> str:
    request = prepared.request
    mode = normalize_reference_mode(request.reference_mode)
    authored_html = extract_html_document(raw_html)
    from services.authored_admission_producers import provider_raw_html, provider_expanded_html
    authored_html = provider_raw_html(
        authored_html, index=request.index, producer="author_batch", normalization_receipt=normalization_receipt)
    authored_html = _strip_nonvisual_pointer_events(authored_html)
    if mode != EXACT_RECONSTRUCTION:
        # Same mechanical floor as the single-slide path (see author_slide),
        # including the explicit user-requested override in `instructions`.
        authored_html = enforce_authored_font_floor(
            authored_html,
            minimum_px=fit_font_floor_px(),
            instructions=request.instructions,
        )
        # Same collapse guard, figure contract, and library injection as the
        # single-slide path: a pinned data-header-part="body" wrapper with no
        # left/right/width has no horizontal extent and collapses to zero
        # width, taking every child anchored inside it down with it.
        authored_html = ensure_body_root_horizontal_extent(authored_html)
        authored_html = finalize_infographic_slide_html(
            authored_html, brand=brand, slide=request.infographic
        )
        if request.require_all_source_images and prepared.source_slot_candidates:
            placed = Counter(
                slot.source_slot_id
                for slot in scan_slots(
                    authored_html,
                    want_assets=True,
                    want_illust=False,
                )
                if slot.source_slot_id is not None
            )
            required = Counter(
                {slot_id: 1 for slot_id in prepared.source_slot_candidates}
            )
            if placed != required:
                missing = sorted((required - placed).elements())
                duplicate = sorted(
                    slot_id
                    for slot_id, count in placed.items()
                    if slot_id in required and count > 1
                )
                unexpected = sorted(
                    slot_id for slot_id in placed if slot_id not in required
                )
                error = ValueError(
                    "authored_source_image_slots_missing:"
                    + ",".join(missing)
                    + ";duplicate:"
                    + ",".join(duplicate)
                    + ";unexpected:"
                    + ",".join(unexpected)
                )
                error.rejected_html = authored_html
                raise error
        authored_html = _bind_offered_source_image_slots(
            authored_html,
            prepared.source_slot_candidates,
        )
    authored_html = provider_expanded_html(
        authored_html, index=request.index, producer="author_batch", validate=validate_authored_slide_html,
        normalization_receipt=normalization_receipt,
    )
    if authored_semantic_dual_output_mode() == "declared":
        try:
            validate_declared_authored_qids(authored_html)
        except ValueError as error:
            if str(error).startswith("authored_semantic_qid_"):
                error.rejected_html = authored_html
            raise
    return authored_html


async def author_slides_html(
    requests: Sequence[AuthoredBatchSlideRequest],
    *,
    design_system: str,
    brand: Brand,
    slide_count: int,
    recovery: bool = False,
    normalization_receipts_by_index: Mapping[int, list[dict]] | None = None,
) -> AuthoredBatchCallResult:
    """Return up to three slide documents from exactly one provider call."""

    batch = tuple(requests)
    if not 1 <= len(batch) <= MAX_BATCH_SLIDES:
        raise ValueError("authored batch must contain between one and three slides")
    indexes = tuple(request.index for request in batch)
    if len(set(indexes)) != len(indexes):
        raise ValueError("authored batch slide indexes must be unique")
    if any(index < 0 or index >= slide_count for index in indexes):
        raise ValueError("authored batch slide index is out of range")

    prepared = tuple(
        _prepare_slide(
            request,
            design_system=design_system,
            brand=brand,
            slide_count=slide_count,
        )
        for request in batch
    )
    wrapper_contract = (
        "Author every requested slide below. Return exactly one wrapper per slide, "
        "in the same order, with no markdown or commentary. Each wrapper must be "
        '<authored-slide index="N"> followed by one complete self-contained HTML '
        "document and then </authored-slide>. N must equal the supplied slide index. "
        "Never merge content or assets between wrappers.\n"
    )
    # G8: the deck-invariant block is carried once, in the system message, so
    # every slide of the deck shares one cacheable prefix and a three-slide
    # batch stops repeating the same ~20k characters three times. The text is
    # unchanged: the hoisted head plus each slide text rejoin exactly.
    # C2: every request of one batch shares the same deck-wide style anchor
    # (``author_planned_batch`` passes it uniformly), so the warm-up knob's
    # anchor-inclusive prefix is computed once from the first request.
    invariant_prefix = invariant_prefix_source(
        design_system, batch[0].style_anchor if batch else None
    )
    hoisted = ""
    user_parts: list[object] = [TextContentPart(text=wrapper_contract)]
    for item in prepared:
        head, slide_prompt = (
            split_deck_invariant(item.prompt, invariant_prefix)
            if is_authored_prompt_system_hoist_enabled()
            else ("", item.prompt)
        )
        hoisted = hoisted or head
        user_parts.append(
            TextContentPart(
                text=(
                    f"\n\nBEGIN REQUEST FOR SLIDE INDEX {item.request.index}\n"
                    + slide_prompt
                    + f"\nEND REQUEST FOR SLIDE INDEX {item.request.index}\n"
                )
            )
        )
        user_parts.extend(item.image_parts)

    client = get_client(config=get_llm_config())
    model = authoring_model(get_model())
    messages = [
        SystemMessage(
            content=author_slide_rules.SYSTEM_PROMPT
            + "\n\nBATCH OUTPUT CONTRACT (authoritative):\n"
            + wrapper_contract
            + (f"\n{hoisted}" if hoisted else "")
        ),
        UserMessage(content=user_parts),
    ]
    # A batch needs room for every independently complete document. Provider
    # limits still fail through the normal fenced error path and trigger the one
    # permitted recovery call for only the affected slides.
    max_tokens = min(
        AUTHOR_MAX_TOKENS * MAX_BATCH_SLIDES,
        sum(item.output_budget for item in prepared),
    )
    kwargs = get_generate_kwargs(model=model, messages=messages, max_tokens=max_tokens)
    reasoning_levels = {item.reasoning_effort for item in prepared}
    reasoning_effort = "low" if "low" in reasoning_levels else next(iter(reasoning_levels))
    reasoning = _authoring_extra_body(reasoning_effort, model=model)
    if reasoning:
        kwargs["extra_body"] = reasoning
    apply_prompt_cache_key(kwargs)

    try:
        provider = str(get_llm_provider().value)
    except Exception:
        provider = "unknown"
    semantic_slot = (
        "author.batch.recovery" if recovery else "author.batch.initial"
    ) + ".slides-" + "-".join(str(index) for index in indexes)
    await _wait_for_author_rate_slot()
    loop = asyncio.get_running_loop()
    timer = ProviderCallTimer()
    response = None
    try:
        async with provider_call_admission(
            is_retry=recovery,
            semantic_slot=semantic_slot,
            provider=provider,
            model=str(model),
            provider_idempotency_capable=supports_provider_idempotency(
                client, provider_id=provider
            ),
        ):
            enforce_llm_egress_policy()
            generate_call = provider_generate_call(
                client,
                provider_id=provider,
                kwargs=kwargs,
                idempotency_key=current_provider_idempotency_key(),
                outcome_scope="authored",
            )
            response = await loop.run_in_executor(_AUTHOR_EXECUTOR, generate_call)
    except Exception as error:
        _register_author_rate_limit(error)
        if isinstance(error, (PublicAdmissionRejected, ProviderOutcomeError)):
            raise
        raise handle_llm_client_exceptions(error) from error
    finally:
        await record_provider_call(
            provider=provider,
            model=str(model),
            response=response,
            latency_ms=timer.elapsed_ms,
            retry_class="recovery" if recovery else "initial",
        )

    raw, errors = _parse_batch_response(
        extract_text(response.content) or "",
        set(indexes),
    )
    htmls: dict[int, str] = {}
    prepared_by_index = {item.request.index: item for item in prepared}
    for index, candidate in raw.items():
        try:
            htmls[index] = _postprocess_slide(
                prepared_by_index[index], candidate, brand=brand,
                normalization_receipt=(normalization_receipts_by_index or {}).get(index),
            )
        except BaseException as error:  # per-slide validation boundary
            errors[index] = error
    return AuthoredBatchCallResult(htmls=htmls, errors=errors)


__all__ = [
    "AuthoredBatchCallResult",
    "AuthoredBatchSlideRequest",
    "MAX_BATCH_SLIDES",
    "author_slides_html",
]
