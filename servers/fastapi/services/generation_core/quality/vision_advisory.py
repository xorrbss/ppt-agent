"""H6 (D22): one advisory vision pass over the finished deck.

``docs/authored-quality-speed-programme-design-20260905.md`` section 6, H6.

Nothing in the deterministic pipeline sees "flag-free but ugly": the DOM gate
measures geometry, the capacity checks measure text, and neither can report a
dead band, a page that fights the deck's palette, or a figure that rendered as
a broken box. Only a picture of the deck can. So after the deck tail this
module composes ONE contact sheet from the slide renders the attempt already
holds and asks the configured vision-capable model, once, for a closed-
vocabulary list of what a reader would notice.

It is advisory and only advisory. The result is written to the quality receipt
under ``advisory.vision`` and is read by nothing: it never gates, never flags,
never triggers a repair, and never fails a deck. Every failure path -- no
renders, no Pillow, a provider error, unparseable output -- returns
``{"skipped": <reason>}``. This module never raises.

Cost: one provider call, roughly 5-15 s per deck, behind
``AUTHORED_VISION_ADVISORY_ENABLED`` (default false; the bench turns it on).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Mapping, Sequence
from typing import Any

from services.authored_vision_advisory_prompt import (
    ADVISORY_MAX_OUTPUT_TOKENS,
    ADVISORY_PROMPT_VERSION,
    ADVISORY_REASONING_EFFORT,
    ISSUE_ALIASES,
    ISSUE_VOCABULARY,
    advisory_json_schema,
    build_advisory_messages,
)
from services.authored_vision_contact_sheet import compose_contact_sheet
from utils.provider_idempotency import ProviderOutcomeError

LOGGER = logging.getLogger(__name__)

_MAX_ISSUES_PER_SLIDE = 4


def _normalize_issue(value: object) -> str | None:
    """Map one model-supplied word onto the closed vocabulary, or drop it."""

    text = str(value or "").strip().lower()
    if not text:
        return None
    canonical = re.sub(r"[^a-z]+", "_", text).strip("_")
    if canonical in ISSUE_VOCABULARY:
        return canonical
    return ISSUE_ALIASES.get(canonical.replace("_", ""))


def parse_advisory_payload(payload: object, *, slide_count: int) -> list[dict[str, Any]]:
    """Project a model response into clamped, closed-vocabulary findings.

    Defensive by contract: an index outside the deck is clamped rather than
    dropped (a model that counts from zero should not lose its whole answer),
    unknown issue words are dropped, and a slide that survives with no issue
    left is omitted.
    """

    if not isinstance(payload, Mapping):
        return []
    raw_slides = payload.get("slides")
    if not isinstance(raw_slides, Sequence) or isinstance(raw_slides, (str, bytes)):
        return []
    by_index: dict[int, list[str]] = {}
    for entry in raw_slides:
        if not isinstance(entry, Mapping):
            continue
        raw_index = entry.get("index")
        if isinstance(raw_index, bool) or not isinstance(raw_index, (int, float)):
            continue
        # The sheet labels thumbnails from 1; the receipt speaks the pipeline's
        # own 0-based slide index.
        index = max(0, min(int(raw_index) - 1, max(0, slide_count - 1)))
        raw_issues = entry.get("issues")
        if isinstance(raw_issues, (str, bytes)) or not isinstance(raw_issues, Sequence):
            raw_issues = ()
        issues = by_index.setdefault(index, [])
        for item in raw_issues:
            issue = _normalize_issue(item)
            if issue is not None and issue not in issues:
                issues.append(issue)
    return [
        {"index": index, "issues": issues[:_MAX_ISSUES_PER_SLIDE]}
        for index, issues in sorted(by_index.items())
        if issues
    ]


def _advisory_extra_body() -> dict[str, Any] | None:
    """Ask for the model's cheapest reasoning; this pass is advisory."""

    from utils.llm_provider import is_codex_selected

    if not is_codex_selected():
        return None
    return {"reasoning": {"effort": ADVISORY_REASONING_EFFORT}}


def _decode_advisory_text(text: str) -> object:
    try:
        return json.loads(text)
    except Exception:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except Exception:
        return None


async def _request_advisory(sheet: bytes, *, slide_count: int, required: bool = False) -> tuple[str, object]:
    """One multimodal call, through the same admission/idempotency wrappers
    every other authored provider call uses."""

    from llmai import get_client
    from llmai.shared import JSONSchemaResponse

    from services.provider_usage_instrumentation import (
        ProviderCallTimer,
        current_provider_idempotency_key,
        provider_call_admission,
        record_provider_call,
        record_provider_terminal_result,
    )
    from utils.llm_config import enforce_llm_egress_policy, get_llm_config
    from utils.llm_provider import get_llm_provider, get_model
    from utils.llm_utils import extract_structured_content, extract_text, get_generate_kwargs
    from utils.provider_idempotency import (
        AUTHORED_DECK_OUTCOME_SCOPE,
        provider_generate_call,
        supports_provider_idempotency,
    )

    client = get_client(config=get_llm_config())
    model = str(get_model() or "")
    schema = advisory_json_schema(slide_count=slide_count) if required else advisory_json_schema()
    messages = (build_advisory_messages(sheet, slide_count=slide_count, required=True)
                if required else build_advisory_messages(sheet, slide_count=slide_count))
    kwargs = get_generate_kwargs(
        model=model,
        messages=messages,
        max_tokens=ADVISORY_MAX_OUTPUT_TOKENS,
        response_format=JSONSchemaResponse(
            name="vision_advisory", json_schema=schema, strict=False
        ),
    )
    reasoning = _advisory_extra_body()
    if reasoning:
        kwargs["extra_body"] = reasoning
    try:
        provider = str(get_llm_provider().value)
    except Exception:
        provider = "unknown"

    timer = ProviderCallTimer()
    response = None
    generate_call = None
    terminal_result = "unmeasured"
    try:
        async with provider_call_admission(
            is_retry=False,
            semantic_slot="authored.vision-advisory",
            provider=provider,
            model=model,
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
                # Deck-level: this call reviews a whole deck and owns no slide
                # attempt, so it takes the authored outcome mapping without
                # ever spending a slide's replay reservation.
                outcome_scope=AUTHORED_DECK_OUTCOME_SCOPE,
            )
            response = await asyncio.get_running_loop().run_in_executor(
                None, generate_call
            )
    except ProviderOutcomeError as error:
        if error.code == "public_provider_result_unknown":
            terminal_result = "unknown"
        terminal_event = (error.diagnostics or {}).get("terminalEvent")
        if terminal_event in {"response.failed", "error"}:
            terminal_result = "failed"
        elif terminal_event == "response.cancelled":
            terminal_result = "cancelled"
        raise
    except asyncio.CancelledError:
        # Local cancellation is not proof the remote provider cancelled.
        terminal_result = "unknown"
        raise
    finally:
        observed = getattr(generate_call, "terminal_outcome", None)
        finish = getattr(observed, "finish_reason", None)
        if terminal_result != "unknown" and finish is not None:
            terminal_result = ("completed" if finish == "stop" else
                               "failed" if finish in {"failed", "error"} else
                               "cancelled" if finish == "cancelled" else "incomplete")
        if generate_call is not None:
            receipt = getattr(generate_call, "receipt", {})
            record_provider_terminal_result(terminal_result,
                receipt.get("physicalCallCount") if isinstance(receipt, Mapping) else None)
        # The usage-receipt schema has no unknown outcome. Preserve that gap
        # explicitly instead of fabricating a successful/failed terminal row.
        if terminal_result != "unknown":
            await record_provider_call(
                provider=provider,
                model=model,
                response=response,
                latency_ms=timer.elapsed_ms,
                retry_class="initial",
                outcome="succeeded" if response is not None else "failed",
            )
    payload = extract_structured_content(response.content)
    return model, payload if payload is not None else _decode_advisory_text(extract_text(response.content) or "")


async def run_vision_advisory(
    pngs: Sequence[bytes], *, slide_count: int, required: bool = False
) -> dict[str, Any]:
    """Ask one vision model, once, what a reader would notice about this deck.

    Returns ``{"model", "latencyMs", "slideCount", "slides"}`` on success and
    ``{"skipped": <reason>}`` on every other outcome. Never raises: an advisory
    that could fail a deck would stop being advisory.
    """

    started = time.monotonic()
    try:
        renders = [png for png in pngs if isinstance(png, (bytes, bytearray)) and png]
        if not renders:
            return {"skipped": "no_renders"}
        if len(renders) != int(slide_count or 0):
            if required:
                return {"skipped": "render_coverage_invalid"}
            # The sheet is the authority for numbering: a slide with no render
            # cannot be reviewed, so the advisory speaks about what it saw.
            LOGGER.info(
                "[authored] vision advisory renders=%d deck_slides=%d",
                len(renders),
                int(slide_count or 0),
            )
        try:
            sheet = await compose_contact_sheet(renders)
        except Exception:
            LOGGER.warning("[authored] vision advisory sheet failed", exc_info=True)
            return {"skipped": "contact_sheet_failed"}
        if not sheet:
            return {"skipped": "contact_sheet_unavailable"}
        # What the provider was actually handed, so an empty answer next time
        # can be read against the image that produced it.
        LOGGER.info(
            "[authored] vision advisory request sheet_bytes=%d slides=%d prompt=%s",
            len(sheet),
            len(renders),
            ADVISORY_PROMPT_VERSION,
        )
        try:
            model, payload = await _request_advisory(sheet, slide_count=len(renders),
                **({"required": True} if required else {}))
        except ProviderOutcomeError as error:
            # A model that answered with no text at all is a distinct outcome
            # from a transport failure, and the one bench run 30 hit; keep them
            # apart in the receipt so the next run can tell them apart.
            LOGGER.warning("[authored] vision advisory outcome=%s", error.code)
            return {"skipped": f"provider_outcome:{error.code}"}
        if payload is None:
            return {"skipped": "unparseable_response"}
        if required:
            findings = payload.get("slides") if isinstance(payload, Mapping) else None
            seen = set()
            if not isinstance(findings, list):
                return {"skipped": "review_schema_invalid"}
            for finding in findings:
                if (not isinstance(finding, Mapping) or type(finding.get("index")) is not int
                        or not 1 <= finding["index"] <= len(renders) or finding["index"] in seen
                        or not isinstance(finding.get("issues"), list)
                        or len(finding["issues"]) > _MAX_ISSUES_PER_SLIDE
                        or any(not isinstance(issue, str) or _normalize_issue(issue) is None
                               for issue in finding["issues"])):
                    return {"skipped": "review_schema_invalid"}
                seen.add(finding["index"])
            if seen != set(range(1, len(renders) + 1)):
                return {"skipped": "review_coverage_incomplete"}
        slides = parse_advisory_payload(payload, slide_count=len(renders))
        latency_ms = int((time.monotonic() - started) * 1000)
        LOGGER.info(
            "[authored] vision advisory model=%s slides=%d flagged=%d latency_ms=%d",
            model,
            len(renders),
            len(slides),
            latency_ms,
        )
        return {
            "promptVersion": "svg-required-visual-review/v1" if required else ADVISORY_PROMPT_VERSION,
            **({"reviewedPageIndices": list(range(len(renders)))} if required else {}),
            "model": model,
            "latencyMs": latency_ms,
            "slideCount": len(renders),
            "sheetBytes": len(sheet),
            "slides": slides,
        }
    except Exception:
        LOGGER.warning("[authored] vision advisory failed", exc_info=True)
        return {"skipped": "provider_failed"}


__all__ = [
    "ADVISORY_MAX_OUTPUT_TOKENS",
    "ADVISORY_PROMPT_VERSION",
    "ISSUE_VOCABULARY",
    "build_advisory_messages",
    "compose_contact_sheet",
    "parse_advisory_payload",
    "run_vision_advisory",
]
