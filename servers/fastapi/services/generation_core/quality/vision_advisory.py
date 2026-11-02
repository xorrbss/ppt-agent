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
import base64
import hashlib
import json
import logging
import re
import time
from collections.abc import Mapping, Sequence
from typing import Any

from services.authored_vision_advisory_prompt import (
    baseline_review_active,
    baseline_review_json_schema,
    single_current_deck_global,
    GLOBAL_BASELINE_VISUAL_EVIDENCE_REVISION,
    ADVISORY_MAX_OUTPUT_TOKENS,
    ADVISORY_PROMPT_VERSION,
    ADVISORY_REASONING_EFFORT,
    ISSUE_ALIASES,
    ISSUE_VOCABULARY,
    DESIGN_AXES,
    DESIGN_LOCAL_AXES,
    DESIGN_GLOBAL_FINDING_LIMIT,
    design_review_json_schema,
    design_global_json_schema,
    design_edited_json_schema,
    advisory_json_schema,
    build_advisory_messages,
)
from services.authored_vision_contact_sheet import compose_contact_sheet, compose_global_page_panels_sync
from services.generation_core.quality.vision_advisory_prompt import (
    axis_review_active, review_prompt_version, server_review_partition,
)
from services.provider_runtime_primitives import BudgetExceededError
from utils.provider_idempotency import ProviderOutcomeError

LOGGER = logging.getLogger(__name__)

_MAX_ISSUES_PER_SLIDE = 4
_DESIGN_SCHEMA_REJECTION_REASONS = frozenset({
    "server_partition_mismatch",
    "page_fields", "index_coverage", "axis_keys", "axis_semantics",
    "evidence_bounds", "issue_axis_consistency", "prior_coverage",
    "global_finding_fields",
    "global_review_fields",
    "global_group_fields",
    "global_retained_coverage",
    "global_intentional_evidence",
    "global_composition_coverage",
    "full_composition_coverage",
    "design_target_fields",
    "comparison_change_fields",
})


class DesignReviewInputCapacityError(ValueError):
    """A local input envelope is unavailable before any provider request."""

    def __init__(self, code, *, input_envelope=None):
        super().__init__(code)
        self.input_envelope = input_envelope


def _design_input_sizes(messages, schema):
    images, text_bytes = [], len(json.dumps(schema, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    for message in messages:
        content = message.content
        if isinstance(content, str):
            text_bytes += len(content.encode("utf-8"))
            continue
        for part in content:
            data = getattr(part, "data", None)
            if isinstance(data, (bytes, bytearray)):
                images.append(len(data))
            else:
                value = part if isinstance(part, str) else getattr(part, "text", None)
                if not isinstance(value, str):
                    raise DesignReviewInputCapacityError("review_input_shape_unavailable")
                text_bytes += len(value.encode("utf-8"))
    # Count base64 bytes, MIME/data-url overhead, response schema and the
    # serialized message envelopes, rather than raw PNG bytes alone.
    encoded_images = sum(4 * ((size + 2) // 3) + 64 for size in images)
    request_bytes = text_bytes + encoded_images + 512 + 256 * len(messages) + 128 * len(images)
    return dict(imageBytes=images, imageCount=len(images), encodedImageBytes=encoded_images,
                serializedContextBytes=text_bytes, serializedRequestBytes=request_bytes)


def _design_input_limits(client=None):
    from services.generation_core.authoring.author_slide_images import (
        REFERENCE_IMAGE_MAX_BYTES, SOURCE_IMAGE_PREVIEW_TOTAL_MAX_BYTES)
    limits = dict(maxImageBytes=REFERENCE_IMAGE_MAX_BYTES,
        maxEncodedImageBytes=SOURCE_IMAGE_PREVIEW_TOTAL_MAX_BYTES,
        maxSerializedContextBytes=REFERENCE_IMAGE_MAX_BYTES,
        maxSerializedRequestBytes=SOURCE_IMAGE_PREVIEW_TOTAL_MAX_BYTES)
    explicit = False
    names = dict(maxImageBytes=("max_image_bytes",),
        maxEncodedImageBytes=("max_encoded_image_bytes", "max_total_image_bytes"),
        maxSerializedContextBytes=("max_context_bytes", "max_text_input_bytes"),
        maxSerializedRequestBytes=("max_request_bytes", "max_input_bytes", "max_request_body_bytes"))
    for capabilities in (getattr(client, "capabilities", None), getattr(client, "config", None)):
        if capabilities is None:
            continue
        for flag in ("supports_images", "supports_vision"):
            supported = capabilities.get(flag) if isinstance(capabilities, Mapping) else getattr(capabilities,
                flag, None)
            if supported is False:
                raise DesignReviewInputCapacityError("review_vision_capability_unavailable")
        for key, aliases in names.items():
            for alias in aliases:
                value = capabilities.get(alias) if isinstance(capabilities, Mapping) else getattr(capabilities,
                    alias, None)
                if type(value) is int and value > 0:
                    limits[key] = min(limits[key], value)
                    explicit = True
    return {**limits, "limitSource": "local-envelope-with-client-byte-limits" if explicit else "local-envelope",
            "providerCapacityVerified": False}


def _check_design_input(messages, schema, *, client=None):
    sizes, limits = _design_input_sizes(messages, schema), _design_input_limits(client)
    if (any(size > limits["maxImageBytes"] for size in sizes["imageBytes"])
            or sizes["encodedImageBytes"] > limits["maxEncodedImageBytes"]
            or sizes["serializedContextBytes"] > limits["maxSerializedContextBytes"]
            or sizes["serializedRequestBytes"] > limits["maxSerializedRequestBytes"]):
        envelope = {key: sizes[key] for key in (
            "imageCount", "encodedImageBytes", "serializedContextBytes", "serializedRequestBytes")}
        envelope["largestImageBytes"] = max(sizes["imageBytes"], default=0)
        envelope.update({key: limits[key] for key in (
            "maxImageBytes", "maxEncodedImageBytes", "maxSerializedContextBytes", "maxSerializedRequestBytes")})
        raise DesignReviewInputCapacityError("review_input_capacity_unavailable", input_envelope=envelope)
    return {**{key: value for key, value in sizes.items() if key != "imageBytes"}, **limits}


def _global_intentional_page_indices(context):
    """Use only server composition observations; absent basis never authorizes true."""
    composition = context.get("composition") or {}
    observations = composition.get("pages", []) if isinstance(composition, Mapping) else []
    if not isinstance(observations, list):
        return []
    return [index for index, page in enumerate(observations)
            if isinstance(page, Mapping) and page.get("comparisonBasis")]


def _prepare_global_input(sheet, renders, context, *, client=None):
    """Keep complete required evidence and pack supplemental family examples."""
    context = {**context, "intentionalPageIndices": _global_intentional_page_indices(context)}
    if single_current_deck_global(context):
        if "_globalPagePanelImages" in context or "globalPagePanels" in context:
            # Only this request's first packing pass creates these values.
            # Revalidate them instead of decoding/encoding all PNGs a second time.
            try:
                from PIL import Image
                import io
                images, metadata = context["_globalPagePanelImages"], context["globalPagePanels"]
                if (context.get("globalVisualEvidenceRevision") != GLOBAL_BASELINE_VISUAL_EVIDENCE_REVISION
                        or context.get("globalEvaluationMode") != "single-current-deck"
                        or not isinstance(images, list) or not isinstance(metadata, list)
                        or not images or len(images) != len(metadata)
                        or any(type(index) is not int for panel in metadata for index in panel["pageIndices"])
                        or [index for panel in metadata for index in panel["pageIndices"]] !=
                            list(range(len(renders)))):
                    raise ValueError("global_page_panel_unavailable")
                for data, panel in zip(images, metadata, strict=True):
                    if (not isinstance(data, bytes) or not panel["pageIndices"]
                            or panel["sheetDigest"] != hashlib.sha256(data).hexdigest()
                            or panel["sourcePngDigests"] != [hashlib.sha256(renders[index]).hexdigest()
                                for index in panel["pageIndices"]]):
                        raise ValueError("global_page_panel_unavailable")
                    with Image.open(io.BytesIO(data)) as image:
                        width = image.width // min(2, len(panel["pageIndices"]))
                        if width < 640 or width != panel["thumbnailWidthPx"] or image.height > 2048:
                            raise ValueError("global_page_panel_unavailable")
            except Exception as error:
                raise DesignReviewInputCapacityError("review_input_capacity_unavailable") from error
        else:
            try:
                panels = compose_global_page_panels_sync(renders)
            except Exception as error:
                raise DesignReviewInputCapacityError("review_input_capacity_unavailable") from error
            indices = [index for panel in panels for index in panel["pageIndices"]]
            if any(type(index) is not int for index in indices) or indices != list(range(len(renders))):
                raise DesignReviewInputCapacityError("review_input_capacity_unavailable")
            context = {**context,
                "globalVisualEvidenceRevision": GLOBAL_BASELINE_VISUAL_EVIDENCE_REVISION,
                "globalEvaluationMode": "single-current-deck",
                "globalPagePanels": [dict(pageIndices=panel["pageIndices"],
                    thumbnailWidthPx=panel["thumbnailWidthPx"],
                    sourcePngDigests=[hashlib.sha256(renders[index]).hexdigest() for index in panel["pageIndices"]],
                    sheetDigest=hashlib.sha256(panel["data"]).hexdigest()) for panel in panels],
                "_globalPagePanelImages": [panel["data"] for panel in panels]}
    schema = (baseline_review_json_schema(slide_count=len(renders), context=context)
        if baseline_review_active(context) else design_global_json_schema(slide_count=len(renders),
        comparison_indices=sorted(context.get("priorCandidates", {})), finite_review=True,
        intentional_page_indices=_global_intentional_page_indices(context)))
    candidates = list(context.get("representativeIndices", []))
    selected = []
    working = {**context, "representativeIndices": [], "omittedSupplementalRepresentativeIndices": candidates}
    _check_design_input(build_advisory_messages(sheet, slide_count=len(renders), required=True,
        pngs=renders, design_context=working), schema, client=client)
    for index in candidates:
        trial = {**context, "representativeIndices": selected + [index],
                 "omittedSupplementalRepresentativeIndices": [item for item in candidates if item not in selected
                     + [index]]}
        try:
            _check_design_input(build_advisory_messages(sheet, slide_count=len(renders), required=True,
                pngs=renders, design_context=trial), schema, client=client)
        except DesignReviewInputCapacityError:
            continue
        selected.append(index)
    return {**context, "representativeIndices": selected,
            "omittedSupplementalRepresentativeIndices": [index for index in candidates if index not in selected]}


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


async def _request_advisory(sheet: bytes, *, slide_count: int, required: bool = False, pngs=None,
                            design_context=None) -> tuple[str, object]:
    """One multimodal call, through the same admission/idempotency wrappers
    every other authored provider call uses."""

    from utils.llm_config import get_client
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

    client = None if design_context is not None else get_client(config=get_llm_config())
    model = str(get_model() or "")
    if design_context is not None:
        if design_context.get("reviewScope") == "global":
            design_context = _prepare_global_input(sheet, pngs, design_context)
        schema = (baseline_review_json_schema(slide_count=slide_count, context=design_context)
            if baseline_review_active(design_context) else design_edited_json_schema(slide_count=slide_count,
            changed_indices=design_context["changedPageIndices"])
            if design_context.get("reviewScope") == "edited" else
            design_global_json_schema(slide_count=slide_count,
            comparison_indices=sorted(design_context.get("priorCandidates", {})), finite_review=True,
            intentional_page_indices=_global_intentional_page_indices(design_context))
            if design_context.get("reviewScope") == "global" else
            design_review_json_schema(slide_count=slide_count,
                comparison_indices=sorted(design_context.get("priorCandidates", {})),
                review_scope=design_context.get("reviewScope", "full")))
        messages = build_advisory_messages(sheet, slide_count=slide_count, required=True,
                                          pngs=pngs, design_context=design_context)
    else:
        schema = advisory_json_schema(slide_count=slide_count) if required else advisory_json_schema()
        messages = (build_advisory_messages(sheet, slide_count=slide_count, required=True, pngs=pngs)
                    if required else build_advisory_messages(sheet, slide_count=slide_count))
    input_envelope = None
    if design_context is not None:
        _check_design_input(messages, schema)
        client = get_client(config=get_llm_config())
        if design_context.get("reviewScope") == "global":
            design_context = _prepare_global_input(sheet, pngs, design_context, client=client)
            messages = build_advisory_messages(sheet, slide_count=slide_count, required=True,
                pngs=pngs, design_context=design_context)
        input_envelope = _check_design_input(messages, schema, client=client)
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
    payload = payload if payload is not None else _decode_advisory_text(extract_text(response.content) or "")
    if input_envelope is not None and isinstance(payload, Mapping):
        payload = {**payload, "_serverInputEnvelope": input_envelope,
            "_serverRepresentativeIndices": design_context.get("representativeIndices", []),
            "_serverOmittedRepresentativeIndices": design_context.get("omittedSupplementalRepresentativeIndices", [])}
        if single_current_deck_global(design_context):
            payload["_serverGlobalVisualEvidence"] = dict(
                revision=design_context["globalVisualEvidenceRevision"],
                evaluationMode=design_context["globalEvaluationMode"],
                panels=design_context["globalPagePanels"],
                panelBytes=sum(map(len, design_context["_globalPagePanelImages"])))
    return model, payload


def _reject_design_review(rejection_reasons, reason):
    if reason in _DESIGN_SCHEMA_REJECTION_REASONS and rejection_reasons is not None and not rejection_reasons:
        rejection_reasons.append(reason)
    return None


def _parse_design_axes(value, axis_names=DESIGN_AXES, *, rejection_reasons=None):
    if not isinstance(value, Mapping) or set(value) != set(axis_names):
        return _reject_design_review(rejection_reasons, "axis_keys")
    severity_for = {"passed": {"none"}, "advisory": {"minor"},
                    "failed": {"major", "critical"}, "unreviewed": {"none"}}
    axes = {}
    for name in axis_names:
        axis = value[name]
        if (not isinstance(axis, Mapping) or axis.get("verdict") not in severity_for
                or axis.get("severity") not in severity_for[axis["verdict"]]):
            return _reject_design_review(rejection_reasons, "axis_semantics")
        if (not isinstance(axis.get("visibleEvidence"), str) or not axis["visibleEvidence"].strip()
                or len(axis["visibleEvidence"]) > 160 or len(axis["visibleEvidence"].split()) > 14):
            return _reject_design_review(rejection_reasons, "evidence_bounds")
        if (axis.get("repairScope") not in {"none", "local_repair", "design_rewrite"}
                or (axis["verdict"] != "failed" and axis["repairScope"] != "none")
                or (axis["verdict"] == "failed" and axis["repairScope"] == "none")):
            return _reject_design_review(rejection_reasons, "axis_semantics")
        axes[name] = {key: axis[key] for key in ("verdict", "severity", "visibleEvidence", "repairScope")}
    return axes


def _parse_design_targets(value, names):
    if not isinstance(value, list) or len(value) > len(names):
        return None
    targets, seen = [], set()
    keys = {"axis", "evidenceRegion", "visibleEvidence", "sourceEvidence", "improvementGoal"}
    for target in value:
        if (not isinstance(target, Mapping) or set(target) != keys
                or target.get("axis") not in names or target["axis"] in seen
                or any(not isinstance(target[key], str) or not target[key].strip()
                       or len(target[key]) > 160 or len(target[key].split()) > 14 for key in keys - {"axis"})):
            return None
        seen.add(target["axis"])
        targets.append(dict(target))
    return targets


def _parse_baseline_review(payload, *, slide_count, context, rejection_reasons=None, rejection_fields=None):
    """Project anonymous current-review evidence into the existing sealed gate shape."""
    from copy import deepcopy
    if not isinstance(payload, Mapping):
        return _reject_design_review(rejection_reasons, "page_fields")
    if context.get("blindVariantLabel", "A") not in {"A", "B"}:
        return _reject_design_review(rejection_reasons, "comparison_change_fields")
    normalized = deepcopy(dict(payload))
    global_review = context.get("reviewScope") == "global"
    if global_review and axis_review_active(context):
        keys = {"variantReview", "designTargets"}
        if context.get("priorCandidates"):
            keys |= {"alternativeReview", "pairComparisons"}
        metadata = {"_serverInputEnvelope", "_serverRepresentativeIndices", "_serverOmittedRepresentativeIndices",
                    "_serverGlobalVisualEvidence"}
        if set(payload) - metadata != keys:
            return _reject_design_review(rejection_reasons, "server_partition_mismatch")
    names = (DESIGN_AXES[-1:] if global_review else
             DESIGN_LOCAL_AXES if context.get("reviewScope") == "local" else DESIGN_AXES)
    if global_review:
        normalized["deckReview"] = normalized.get("variantReview")
        normalized["priorReview"] = normalized.get("alternativeReview")
        result = _parse_global_review(normalized, slide_count=slide_count, context=context,
            rejection_reasons=rejection_reasons, rejection_fields=rejection_fields)
        raw_targets = payload.get("designTargets")
        if not isinstance(raw_targets, list) or len(raw_targets) > slide_count:
            return _reject_design_review(rejection_reasons, "design_target_fields")
        targets_by_index = {}
        for entry in raw_targets:
            if (not isinstance(entry, Mapping) or set(entry) != {"index", "targets"}
                    or type(entry.get("index")) is not int or not 1 <= entry["index"] <= slide_count
                    or entry["index"] - 1 in targets_by_index):
                return _reject_design_review(rejection_reasons, "design_target_fields")
            targets_by_index[entry["index"] - 1] = _parse_design_targets(entry.get("targets"), names)
    else:
        if not isinstance(payload.get("slides"), list) or not isinstance(payload.get("pairComparisons", []), list):
            return _reject_design_review(rejection_reasons, "page_fields")
        normalized["candidateComparisons"] = [dict(entry, priorAxes=entry.get("alternativeAxes"))
            for entry in payload.get("pairComparisons", []) if isinstance(entry, Mapping)]
        result = _parse_design_review(normalized, slide_count=slide_count, context=context,
                                     rejection_reasons=rejection_reasons)
        targets_by_index = {entry["index"] - 1: _parse_design_targets(entry.get("designTargets"), names)
            for entry in payload.get("slides", []) if isinstance(entry, Mapping) and type(entry.get("index")) is int}
    if result is None:
        return None
    if any(value is None for value in targets_by_index.values()):
        return _reject_design_review(rejection_reasons, "design_target_fields")
    for page in result["pages"]:
        targets = targets_by_index.get(page["index"], [] if global_review else None)
        if targets is None or targets and page.get("evaluated") is not True:
            return _reject_design_review(rejection_reasons, "design_target_fields")
        page["designTargets"] = targets
    expected = set(context.get("priorCandidates", {}))
    if not expected:
        return result
    pairs = payload.get("pairComparisons")
    if not isinstance(pairs, list) or len(pairs) != len(expected):
        return _reject_design_review(rejection_reasons, "prior_coverage")
    changes_by_index = {}
    main = context.get("blindVariantLabel", "A")
    for pair in pairs:
        if (not isinstance(pair, Mapping) or type(pair.get("index")) is not int
                or pair["index"] - 1 not in expected or pair["index"] - 1 in changes_by_index
                or not isinstance(pair.get("axisChanges"), Mapping) or set(pair["axisChanges"]) != set(names)):
            return _reject_design_review(rejection_reasons, "comparison_change_fields")
        changes = {}
        for name, change in pair["axisChanges"].items():
            if (not isinstance(change, Mapping) or set(change) != {"preferred", "visibleEvidence"}
                    or change.get("preferred") not in {"A", "B", "equivalent", "unjudged"}
                    or not isinstance(change.get("visibleEvidence"), str) or not change["visibleEvidence"].strip()
                    or len(change["visibleEvidence"]) > 160 or len(change["visibleEvidence"].split()) > 14):
                return _reject_design_review(rejection_reasons, "comparison_change_fields")
            preferred = change["preferred"]
            changes[name] = dict(change="improved" if preferred == main else
                "worsened" if preferred in {"A", "B"} else preferred, visibleEvidence=change["visibleEvidence"])
        changes_by_index[pair["index"] - 1] = changes
    for comparison in result.get("candidateComparisons", []):
        comparison["axisChanges"] = changes_by_index[comparison["index"]]
        comparison["blindedPresentation"] = dict(candidateLabel=main,
            priorLabel="B" if main == "A" else "A", displayOrder=["A", "B"],
            chronologyExposed=False, versionExposed=False)
    return result


def _parse_design_review(payload, *, slide_count, context, rejection_reasons=None):
    """Reject ambiguous coverage and invented review authority; retain visible reasons."""
    if not isinstance(payload, Mapping) or not isinstance(payload.get("slides"), list):
        return _reject_design_review(rejection_reasons, "page_fields")
    local = context.get("reviewScope") == "local"
    axis_names = DESIGN_LOCAL_AXES if local else DESIGN_AXES
    pages, seen = [], set()
    for page in payload["slides"]:
        if not isinstance(page, Mapping):
            return _reject_design_review(rejection_reasons, "page_fields")
        if (type(page.get("index")) is not int
                or not 1 <= page["index"] <= slide_count or page["index"] in seen):
            return _reject_design_review(rejection_reasons, "index_coverage")
        if type(page.get("evaluated")) is not bool:
            return _reject_design_review(rejection_reasons, "page_fields")
        if not isinstance(page.get("axes"), Mapping) or set(page["axes"]) != set(axis_names):
            return _reject_design_review(rejection_reasons, "axis_keys")
        if (not isinstance(page.get("issues"), list)
                or any(not isinstance(issue, str) or _normalize_issue(issue) is None for issue in page["issues"])):
            return _reject_design_review(rejection_reasons, "page_fields")
        axes = _parse_design_axes(page["axes"], axis_names, rejection_reasons=rejection_reasons)
        if axes is None:
            return _reject_design_review(rejection_reasons, "axis_semantics")
        issues = [_normalize_issue(issue) for issue in page["issues"]]
        if ("illegible" in issues and not any(axes[name]["verdict"] == "failed"
                for name in ("hierarchy_emphasis", "density_balance"))
                or "broken_figure" in issues and axes["information_structure"]["verdict"] != "failed"):
            return _reject_design_review(rejection_reasons, "issue_axis_consistency")
        if page["evaluated"] and any(a["verdict"] == "unreviewed" for a in axes.values()):
            return _reject_design_review(rejection_reasons, "axis_semantics")
        if not local and not context.get("deckComplete") and axes["repetition_deck_flow"]["verdict"] != "unreviewed":
            return _reject_design_review(rejection_reasons, "axis_semantics")
        seen.add(page["index"])
        pages.append(dict(index=page["index"] - 1, evaluated=page["evaluated"], axes=axes,
                          issues=issues))
    if seen != set(range(1, slide_count + 1)):
        return _reject_design_review(rejection_reasons, "index_coverage")
    flow = (dict(evaluated=False, visibleEvidence="Global flow is reviewed separately.", repetitionGroups=[])
            if local else payload.get("deckFlow"))
    if (not isinstance(flow, Mapping) or type(flow.get("evaluated")) is not bool
            or not isinstance(flow.get("visibleEvidence"), str) or not flow["visibleEvidence"].strip()
            or len(flow["visibleEvidence"]) > 160 or len(flow["visibleEvidence"].split()) > 14
            or not isinstance(flow.get("repetitionGroups"), list)
            or flow["evaluated"] and not context.get("deckComplete")):
        return _reject_design_review(rejection_reasons, "page_fields")
    groups = []
    observations = (context.get("composition") or {}).get("pages") or []
    for group in flow["repetitionGroups"]:
        if (not isinstance(group, Mapping) or not isinstance(group.get("pages"), list)
                or not group["pages"]
                or any(type(index) is not int or not 1 <= index <= slide_count for index in group["pages"])
                or len(set(group["pages"])) != len(group["pages"])
                or type(group.get("intentional")) is not bool
                or not isinstance(group.get("sourceEvidence"), str)
                or len(group["sourceEvidence"]) > 160
                or not isinstance(group.get("visibleEvidence"), str) or not group["visibleEvidence"].strip()
                or len(group["visibleEvidence"]) > 160 or len(group["visibleEvidence"].split()) > 14):
            return _reject_design_review(rejection_reasons, "page_fields")
        indices = sorted(index - 1 for index in group["pages"])
        if group["intentional"] and (not group["sourceEvidence"].strip()
                or any(index >= len(observations) or not observations[index].get("comparisonBasis") for index in
                    indices)):
            return _reject_design_review(rejection_reasons, "issue_axis_consistency")
        groups.append(dict(pages=indices, intentional=group["intentional"],
                           sourceEvidence=group["sourceEvidence"], visibleEvidence=group["visibleEvidence"]))
    if flow["evaluated"]:
        # Every observed distant/adjacent cluster needs an explicit semantic
        # explanation, even when geometry repetition is judged harmless.
        for observed in (context.get("composition") or {}).get("groups", []):
            if not any(set(observed["pages"]) <= set(group["pages"]) for group in groups):
                return _reject_design_review(rejection_reasons,
                    "full_composition_coverage" if baseline_review_active(context) else "index_coverage")
    comparisons = []
    prior_candidates = context.get("priorCandidates") or {}
    if prior_candidates:
        raw_comparisons = payload.get("candidateComparisons")
        if not isinstance(raw_comparisons, list) or len(raw_comparisons) != len(prior_candidates):
            return _reject_design_review(rejection_reasons, "prior_coverage")
        seen = set()
        for comparison in raw_comparisons:
            if (not isinstance(comparison, Mapping) or type(comparison.get("index")) is not int
                    or comparison["index"] - 1 not in prior_candidates or comparison["index"] in seen):
                return _reject_design_review(rejection_reasons, "prior_coverage")
            if type(comparison.get("evaluated")) is not bool:
                return _reject_design_review(rejection_reasons, "page_fields")
            if (not isinstance(comparison.get("visibleEvidence"), str) or not comparison["visibleEvidence"].strip()
                    or len(comparison["visibleEvidence"]) > 160 or len(comparison["visibleEvidence"].split()) > 14):
                return _reject_design_review(rejection_reasons, "evidence_bounds")
            axes = _parse_design_axes(comparison.get("priorAxes"), axis_names,
                                      rejection_reasons=rejection_reasons)
            if axes is None or comparison["evaluated"] and any(axis["verdict"] == "unreviewed" for axis in
                axes.values()):
                return _reject_design_review(rejection_reasons, "axis_semantics")
            seen.add(comparison["index"])
            comparisons.append(dict(index=comparison["index"] - 1, evaluated=comparison["evaluated"],
                                    priorAxes=axes, visibleEvidence=comparison["visibleEvidence"]))
    return dict(pages=sorted(pages, key=lambda page: page["index"]), candidateComparisons=comparisons,
                deckFlow=dict(evaluated=flow["evaluated"], visibleEvidence=flow["visibleEvidence"],
                              repetitionGroups=groups))


def _global_finding_evidence_diagnostics(finding):
    finding_id = finding.get("id")
    if type(finding_id) is not int or not 1 <= finding_id <= DESIGN_GLOBAL_FINDING_LIMIT:
        raise ValueError("svg_global_review_diagnostics_invalid")
    axis = finding.get("axis")
    state = ("missing" if "axis" not in finding else "null" if axis is None else
             "object" if isinstance(axis, Mapping) else "other")
    present = isinstance(axis, Mapping) and "visibleEvidence" in axis
    evidence = axis.get("visibleEvidence") if isinstance(axis, Mapping) else None
    is_string = isinstance(evidence, str)
    bounds = 0
    if is_string:
        if evidence.strip():
            bounds |= 1
        if len(evidence) <= 160:
            bounds |= 2
        if len(evidence.split()) <= 14:
            bounds |= 4
    return {"reviewTarget": "findingAxis", "findingId": finding_id, "reviewState": state,
            "presenceMask": int(present), "nullMask": int(present and evidence is None),
            "expectedTypeMask": int(is_string), "boundsMask": bounds}


def _global_review_field_diagnostics(review, *, target, present=True):
    if target not in {"deckReview", "priorReview"}:
        raise ValueError("svg_global_review_diagnostics_invalid")
    fields = ("cleanPages", "unreviewedPages", "groups", "cleanEvidence")
    state = ("missing" if not present else "null" if review is None else
             "object" if isinstance(review, Mapping) else "other")
    presence = nulls = expected_types = bounds = 0
    if isinstance(review, Mapping):
        for index, key in enumerate(fields):
            bit = 1 << index
            if key in review:
                presence |= bit
                if review[key] is None:
                    nulls |= bit
                if isinstance(review[key], list if index < 3 else str):
                    expected_types |= bit
        groups, evidence = review.get("groups"), review.get("cleanEvidence")
        if isinstance(groups, list) and len(groups) <= DESIGN_GLOBAL_FINDING_LIMIT:
            bounds |= 1
        if isinstance(evidence, str):
            if evidence.strip():
                bounds |= 2
            if len(evidence) <= 160:
                bounds |= 4
            if len(evidence.split()) <= 14:
                bounds |= 8
    return {"reviewTarget": target, "reviewState": state, "presenceMask": presence,
            "nullMask": nulls, "expectedTypeMask": expected_types, "boundsMask": bounds}


def _global_partition_diagnostics(review, *, expected):
    """Count explicit wire references only; emit no model text or arbitrary values."""
    expected_wire = {index + 1 for index in expected}
    references, array_errors = [], 0
    review = review if isinstance(review, Mapping) else {}
    def collect(value):
        nonlocal array_errors
        if isinstance(value, list):
            references.extend(value)
        else:
            array_errors += 1
    collect(review.get("cleanPages"))
    collect(review.get("unreviewedPages"))
    groups = review.get("groups")
    if isinstance(groups, list):
        for group in groups:
            collect(group.get("pages") if isinstance(group, Mapping) else None)
    else:
        array_errors += 1
    seen, duplicates, out_of_range = set(), set(), set()
    invalid_types = duplicate_count = out_of_range_count = 0
    for index in references:
        if type(index) is not int:
            invalid_types += 1
            continue
        if index in seen:
            duplicate_count += 1
            duplicates.add(index)
        seen.add(index)
        if index not in expected_wire:
            out_of_range_count += 1
            out_of_range.add(index)
    assigned = seen & expected_wire
    missing = expected_wire - assigned
    safe_max = max(expected_wire, default=0) + 16
    def sample(indices):
        # Only page numbers and nearby indexing mistakes may escape. Larger
        # model integers might be source values or identifiers; count only.
        return sorted(index for index in indices if -16 <= index <= safe_max)[:16]
    samples = {"duplicatePageIndices": sample(duplicates),
               "outOfRangePageIndices": sample(out_of_range), "missingPageIndices": sample(missing)}
    exact = not (array_errors or invalid_types or duplicate_count or out_of_range_count or missing)
    return dict(indexBase=1, expectedPageCount=len(expected_wire),
        explicitReferenceCount=len(references), assignedPageCount=len(assigned),
        arrayTypeErrorCount=array_errors, invalidIndexTypeCount=invalid_types,
        duplicateReferenceCount=duplicate_count, outOfRangeReferenceCount=out_of_range_count,
        missingPageCount=len(missing), exactCoverage=exact, indexSampleLimit=16,
        safeIndexMinimum=-16, safeIndexMaximum=safe_max,
        indexSamplesTruncated=any(len(values) != len(samples[key]) for key, values in
            (("duplicatePageIndices", duplicates), ("outOfRangePageIndices", out_of_range),
             ("missingPageIndices", missing))), **samples)


def _parse_global_review(payload, *, slide_count, context, rejection_reasons=None, rejection_fields=None):
    if axis_review_active(context):
        return _parse_server_global_review(payload, slide_count=slide_count, context=context,
                                           rejection_reasons=rejection_reasons)
    if (not isinstance(payload, Mapping) or not isinstance(payload.get("findings"), list)
            or len(payload["findings"]) > DESIGN_GLOBAL_FINDING_LIMIT):
        _reject_design_review(rejection_reasons, "global_finding_fields")
        return None
    findings = {}
    for finding in payload["findings"]:
        if (not isinstance(finding, Mapping) or type(finding.get("id")) is not int
                or not 1 <= finding["id"] <= DESIGN_GLOBAL_FINDING_LIMIT
                or finding["id"] in findings or type(finding.get("intentional")) is not bool
                or not isinstance(finding.get("sourceEvidence"), str) or len(finding["sourceEvidence"]) > 160):
            _reject_design_review(rejection_reasons, "global_finding_fields")
            return None
        axes = _parse_design_axes({"repetition_deck_flow": finding.get("axis")}, ("repetition_deck_flow",),
            rejection_reasons=rejection_reasons)
        if axes is None:
            if (rejection_fields is not None and rejection_reasons
                    and rejection_reasons[-1] == "evidence_bounds"):
                rejection_fields.update(_global_finding_evidence_diagnostics(finding))
            return None
        findings[finding["id"]] = {**finding, "axis": axes["repetition_deck_flow"]}
    def project(review, expected, *, prior=False, present=True):
        if (not isinstance(review, Mapping) or not isinstance(review.get("cleanPages"), list)
                or not isinstance(review.get("unreviewedPages"), list) or not isinstance(review.get("groups"), list)
                or len(review["groups"]) > DESIGN_GLOBAL_FINDING_LIMIT
                or not isinstance(review.get("cleanEvidence"), str) or not review["cleanEvidence"].strip()
                or len(review["cleanEvidence"]) > 160 or len(review["cleanEvidence"].split()) > 14):
            if rejection_fields is not None and not rejection_fields:
                rejection_fields.update(_global_review_field_diagnostics(
                    review, target="priorReview" if prior else "deckReview", present=present))
            _reject_design_review(rejection_reasons, "global_review_fields")
            return None
        assignments, groups, used_findings = {}, [], set()
        def reject_partition(field, *, group_ordinal=None):
            if (rejection_fields is not None and baseline_review_active(context)
                    and context.get("reviewScope") == "global" and not rejection_fields):
                rejection_fields.update(reviewTarget="priorReview" if prior else "deckReview",
                    partitionField=field, groupOrdinal=group_ordinal,
                    partitionCoverage=_global_partition_diagnostics(review, expected=expected))
            _reject_design_review(rejection_reasons, "index_coverage")
        def assign(indices, axis):
            if not isinstance(indices, list):
                return False
            for index in indices:
                if type(index) is not int or index - 1 not in expected or index - 1 in assignments:
                    return False
                assignments[index - 1] = axis
            return True
        if not assign(review["cleanPages"], dict(verdict="passed", severity="none",
                visibleEvidence=review["cleanEvidence"], repairScope="none")):
            reject_partition("cleanPages")
            return None
        if not assign(review["unreviewedPages"], dict(verdict="unreviewed", severity="none",
                visibleEvidence="Global composition evidence remains insufficient.", repairScope="none")):
            reject_partition("unreviewedPages")
            return None
        for group_ordinal, group in enumerate(review["groups"], 1):
            if (not isinstance(group, Mapping) or not isinstance(group.get("pages"), list)
                    or not group["pages"] or type(group.get("findingId")) is not int
                    or group["findingId"] not in findings or not isinstance(group.get("retainedPages"), list)
                    or group["findingId"] in used_findings
                    or any(type(index) is not int or index not in group["pages"] for index in group["retainedPages"])
                    or len(set(group["retainedPages"])) != len(group["retainedPages"])):
                _reject_design_review(rejection_reasons, "global_group_fields")
                return None
            finding = findings[group["findingId"]]
            used_findings.add(group["findingId"])
            axis = finding["axis"]
            if not assign(group["pages"], axis):
                reject_partition("groupPages", group_ordinal=group_ordinal)
                return None
            indices = sorted(index - 1 for index in group["pages"])
            retained = [index - 1 for index in group["retainedPages"]]
            observed_repetition = any(set(observation["pages"]) & set(indices)
                                      for observation in context["composition"].get("groups", []))
            if axis["verdict"] == "failed" and observed_repetition and not retained and not prior:
                _reject_design_review(rejection_reasons, "global_retained_coverage")
                return None
            if axis["verdict"] == "failed" and not prior:
                for observation in context["composition"].get("groups", []):
                    if set(observation["pages"]) <= set(indices) and not set(observation["pages"]) & set(retained):
                        _reject_design_review(rejection_reasons, "global_retained_coverage")
                        return None
            if axis["verdict"] == "failed" and len(retained) == len(indices):
                _reject_design_review(rejection_reasons, "global_retained_coverage")
                return None
            for index in retained:
                assignments[index] = dict(verdict="passed", severity="none",
                    visibleEvidence=axis["visibleEvidence"], repairScope="none")
            observations = context["composition"]["pages"]
            if finding["intentional"] and (not finding["sourceEvidence"].strip()
                    or any(not observations[index].get("comparisonBasis") for index in indices)):
                if rejection_fields is not None and not rejection_fields:
                    blank_source = not finding["sourceEvidence"].strip()
                    missing_basis = any(not observations[index].get("comparisonBasis") for index in indices)
                    rejection_fields["reviewTarget"] = "priorReview" if prior else "deckReview"
                    rejection_fields["intentionalFailureMask"] = int(blank_source) | (int(missing_basis) << 1)
                _reject_design_review(rejection_reasons, "global_intentional_evidence")
                return None
            groups.append(dict(pages=indices, intentional=finding["intentional"],
                retainedPages=retained, sourceEvidence=finding["sourceEvidence"],
                    visibleEvidence=axis["visibleEvidence"]))
        if set(assignments) != expected:
            reject_partition("coverage")
            return None
        for observation in context["composition"].get("groups", []):
            unreviewed = {index - 1 for index in review["unreviewedPages"]}
            affected = (set(observation["pages"]) & expected) - unreviewed
            if affected and not any(affected <= set(group["pages"]) for group in groups):
                _reject_design_review(rejection_reasons, "global_composition_coverage")
                return None
        return dict(pages=[dict(index=index, evaluated=axis["verdict"] != "unreviewed",
                    axes={"repetition_deck_flow": axis}, issues=[]) for index, axis in sorted(assignments.items())],
            deckFlow=dict(evaluated=all(axis["verdict"] != "unreviewed" for axis in assignments.values()),
                visibleEvidence=review["cleanEvidence"], repetitionGroups=groups))
    if not isinstance(payload, Mapping):
        _reject_design_review(rejection_reasons, "global_finding_fields")
        return None
    result = project(payload.get("deckReview"), set(range(slide_count)), present="deckReview" in payload)
    if result is None:
        _reject_design_review(rejection_reasons, "global_review_fields")
        return None
    prior_indices = set(context.get("priorCandidates", {}))
    if prior_indices:
        prior = project(payload.get("priorReview"), prior_indices, prior=True, present="priorReview" in payload)
        if prior is None:
            _reject_design_review(rejection_reasons, "prior_coverage")
            return None
        result["candidateComparisons"] = [dict(index=page["index"], evaluated=page["evaluated"],
            priorAxes=page["axes"], visibleEvidence=page["axes"]["repetition_deck_flow"]["visibleEvidence"])
            for page in prior["pages"]]
    return result


def _parse_server_global_review(payload, *, slide_count, context, rejection_reasons):
    """The response supplies judgments only; coverage belongs to the server."""
    partition = server_review_partition(context, slide_count)

    def project(raw, allowed):
        expected = {group: [key for key in keys if int(key[5:]) - 1 in allowed]
                    for group, keys in partition.items()}
        expected = {group: keys for group, keys in expected.items() if keys}
        if not isinstance(raw, Mapping) or set(raw) != set(expected):
            return _reject_design_review(rejection_reasons, "server_partition_mismatch")
        pages, repetitions = [], []
        for group, keys in expected.items():
            if not isinstance(raw[group], Mapping) or set(raw[group]) != set(keys):
                return _reject_design_review(rejection_reasons, "server_partition_mismatch")
            for key in keys:
                index, item = int(key[5:]) - 1, raw[group][key]
                if (not isinstance(item, Mapping) or set(item) != {"axis", "intentional", "sourceEvidence"}
                        or type(item["intentional"]) is not bool
                        or not isinstance(item["sourceEvidence"], str) or len(item["sourceEvidence"]) > 160):
                    return _reject_design_review(rejection_reasons, "global_finding_fields")
                axes = _parse_design_axes({"repetition_deck_flow": item["axis"]}, DESIGN_AXES[-1:],
                                          rejection_reasons=rejection_reasons)
                if axes is None:
                    return None
                if item["intentional"] and (not item["sourceEvidence"].strip()
                        or not context["composition"]["pages"][index].get("comparisonBasis")):
                    return _reject_design_review(rejection_reasons, "global_intentional_evidence")
                axis = axes["repetition_deck_flow"]
                pages.append(dict(index=index, evaluated=axis["verdict"] != "unreviewed", axes=axes, issues=[]))
                repetitions.append(dict(pages=[index], intentional=item["intentional"],
                    retainedPages=[index] if axis["verdict"] == "passed" else [],
                    sourceEvidence=item["sourceEvidence"], visibleEvidence=axis["visibleEvidence"]))
        return dict(pages=sorted(pages, key=lambda page: page["index"]),
                    deckFlow=dict(evaluated=all(page["evaluated"] for page in pages), repetitionGroups=repetitions))

    if not isinstance(payload, Mapping):
        return _reject_design_review(rejection_reasons, "global_review_fields")
    result = project(payload.get("deckReview"), set(range(slide_count)))
    if result is None:
        return None
    prior_indices = set(context.get("priorCandidates", {}))
    if prior_indices:
        prior = project(payload.get("priorReview"), prior_indices)
        if prior is None:
            return None
        result["candidateComparisons"] = [dict(index=page["index"], evaluated=page["evaluated"],
            priorAxes=page["axes"], visibleEvidence=page["axes"]["repetition_deck_flow"]["visibleEvidence"])
            for page in prior["pages"]]
    return result


def _parse_edited_review(payload, *, slide_count, context):
    global_review = _parse_global_review(payload, slide_count=slide_count, context=context)
    if global_review is None or not isinstance(payload.get("localReview"), Mapping):
        return None
    changed = context["changedPageIndices"]
    if changed:
        local_review = _parse_design_review(payload["localReview"], slide_count=len(changed),
            context={**context, "reviewScope": "local", "deckComplete": False, "composition": {}})
        if local_review is None:
            return None
        if any(len(page["issues"]) > _MAX_ISSUES_PER_SLIDE for page in local_review["pages"]):
            return None
        local_pages = [{**page, "index": changed[page["index"]]} for page in local_review["pages"]]
    elif payload["localReview"].get("slides") == []:
        local_pages = []
    else:
        return None
    return {**global_review, "localPages": local_pages}


async def run_vision_advisory(
    pngs: Sequence[bytes], *, slide_count: int, required: bool = False, design_context=None
) -> dict[str, Any]:
    """Ask one vision model, once, what a reader would notice about this deck.

    Returns ``{"model", "latencyMs", "slideCount", "slides"}`` on success and
    ``{"skipped": <reason>}`` on unavailable evidence. Legacy reviews remain
    optional; the fresh design policy propagates provider outcomes and budget
    failures so callers preserve UNKNOWN and durable accounting history.
    """

    started = time.monotonic()
    if design_context is not None:
        required = True
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
            sheet = (await compose_contact_sheet(renders)
                     if not required or design_context is not None and design_context.get("reviewScope") in
                         {"global", "edited"} else b"")
        except Exception:
            LOGGER.warning("[authored] vision advisory sheet failed", exc_info=True)
            return {"skipped": "contact_sheet_failed"}
        if not sheet and not required:
            return {"skipped": "contact_sheet_unavailable"}
        if design_context is not None and design_context.get("reviewScope") in {"global", "edited"} and not sheet:
            return {"skipped": "contact_sheet_unavailable"}
        if (design_context is not None
                and design_context.get("reviewScope") == "global" and design_context.get("priorCandidates")):
            prior_renders = ([design_context["priorCandidates"].get(index, {}).get("png", png)
                              for index, png in enumerate(renders)] if baseline_review_active(design_context) else
                             [prior["png"] for _, prior in sorted(design_context["priorCandidates"].items())])
            prior_sheet = await compose_contact_sheet(prior_renders)
            if not prior_sheet:
                return {"skipped": "prior_contact_sheet_unavailable"}
            design_context = {**design_context, "priorSheet": prior_sheet}
        # What the provider was actually handed, so an empty answer next time
        # can be read against the image that produced it.
        LOGGER.info(
            "[authored] vision advisory request sheet_bytes=%d slides=%d prompt=%s",
            sum(len(png) for png in renders) if required else len(sheet),
            len(renders),
            ADVISORY_PROMPT_VERSION,
        )
        try:
            model, payload = await _request_advisory(sheet, slide_count=len(renders),
                **({"required": True, "pngs": renders} if required else {}),
                **({"design_context": design_context} if design_context is not None else {}))
        except ProviderOutcomeError as error:
            # A model that answered with no text at all is a distinct outcome
            # from a transport failure, and the one bench run 30 hit; keep them
            # apart in the receipt so the next run can tell them apart.
            LOGGER.warning("[authored] vision advisory outcome=%s", error.code)
            if design_context is not None:
                raise
            return {"skipped": f"provider_outcome:{error.code}"}
        if payload is None:
            return {"skipped": "unparseable_response"}
        design_review = None
        if design_context is not None:
            rejection_reasons = [] if (baseline_review_active(design_context)
                or design_context.get("reviewScope") in {"local", "global"}) else None
            rejection_fields = {} if design_context.get("reviewScope") == "global" else None
            design_review = (_parse_baseline_review(payload, slide_count=len(renders), context=design_context,
                rejection_reasons=rejection_reasons, rejection_fields=rejection_fields)
                if baseline_review_active(design_context) else _parse_edited_review(payload,
                    slide_count=len(renders), context=design_context)
                if design_context.get("reviewScope") == "edited" else
                _parse_global_review(payload, slide_count=len(renders), context=design_context,
                    rejection_reasons=rejection_reasons, rejection_fields=rejection_fields)
                if design_context.get("reviewScope") == "global" else
                _parse_design_review(payload, slide_count=len(renders), context=design_context,
                                     rejection_reasons=rejection_reasons))
            if design_review is None:
                if baseline_review_active(design_context) and design_context.get("reviewScope") == "full":
                    reason = rejection_reasons[0] if rejection_reasons else "page_fields"
                    field_names = {
                        "axis_keys": ["axes"], "axis_semantics": ["evaluated", "verdict", "severity", "repairScope"],
                        "evidence_bounds": ["visibleEvidence"], "index_coverage": ["index"],
                        "full_composition_coverage": ["deckFlow", "repetitionGroups", "pages"],
                        "issue_axis_consistency": ["issues", "axes", "intentional", "sourceEvidence"],
                        "design_target_fields": ["designTargets", "axis", "evidenceRegion", "visibleEvidence",
                            "sourceEvidence", "improvementGoal"],
                        "comparison_change_fields": ["pairComparisons", "axisChanges", "preferred", "visibleEvidence"],
                        "prior_coverage": ["pairComparisons", "index"],
                        "page_fields": ["slides", "index", "evaluated", "issues", "deckFlow"],
                    }.get(reason, ["slides"])
                    rejection_fields = {"fieldNames": field_names}
                parsed_pages = payload.get("slides") if isinstance(payload, Mapping) else None
                parsed_indices = [page.get("index") for page in parsed_pages if isinstance(page,
                    Mapping)] if isinstance(parsed_pages, list) else []
                parsed_coverage = (len(parsed_indices) == len(renders)
                    and all(type(index) is int for index in parsed_indices)
                    and set(parsed_indices) == set(range(1, len(renders) + 1)))
                parsed_count = len(parsed_pages) if isinstance(parsed_pages, list) else 0
                if (baseline_review_active(design_context) and design_context.get("reviewScope") == "global"
                        and not axis_review_active(design_context)):
                    partition = _global_partition_diagnostics(payload.get("variantReview")
                        if isinstance(payload, Mapping) else None, expected=set(range(len(renders))))
                    parsed_count, parsed_coverage = partition["assignedPageCount"], partition["exactCoverage"]
                    if rejection_fields is not None and "partitionCoverage" not in rejection_fields:
                        rejection_fields["partitionCoverage"] = partition
                return {"skipped": "design_review_schema_or_coverage_invalid",
                        "parsedPageCount": parsed_count,
                        "parsedPageIndexCoverage": parsed_coverage,
                        **({"schemaRejectionReason": rejection_reasons[0]} if rejection_reasons else {}),
                            **({"schemaRejectionFields": rejection_fields} if rejection_fields else {})}
        global_review = design_context is not None and design_context.get("reviewScope") in {"global", "edited"}
        if required and not global_review:
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
        slides = [] if global_review else parse_advisory_payload(payload, slide_count=len(renders))
        from utils.hashing import sha256_hex
        latency_ms = int((time.monotonic() - started) * 1000)
        LOGGER.info(
            "[authored] vision advisory model=%s slides=%d flagged=%d latency_ms=%d",
            model,
            len(renders),
            len(slides),
            latency_ms,
        )
        return {
            "promptVersion": (review_prompt_version(design_context) if design_context is not None else
                              "svg-required-visual-review/v1" if required else ADVISORY_PROMPT_VERSION),
            **({"designReview": design_review} if design_review is not None else {}),
            **({"inputEnvelope": payload.get("_serverInputEnvelope"),
                "representativeIndices": payload.get("_serverRepresentativeIndices", []),
                "omittedSupplementalRepresentativeIndices": payload.get("_serverOmittedRepresentativeIndices", [])}
               if design_context is not None else {}),
            **({"reviewedPageIndices": list(range(len(renders)))} if required else {}),
            **({"reviewedPngDigests": [sha256_hex(png) for png in renders],
                "reviewInputKind": "individual-page-pngs/v1"} if required else {}),
            "model": model,
            "latencyMs": latency_ms,
            "slideCount": len(renders),
            **({"inputImageBytes": sum(len(png) for png in renders)} if required else {"sheetBytes": len(sheet)}),
            **({"inputImageBytes": sum(len(png) for png in renders)
                + sum(len(prior["png"]) for prior in design_context.get("priorCandidates", {}).values())
                + sum(len(base64.b64decode(example["data_url"].split(",", 1)[1], validate=True))
                      for example in design_context.get("examples", []))} if design_context is not None else {}),
            **({"reviewInputKind": "deck-contact-sheet-and-representatives/v1",
                "inputImageBytes": len(sheet)
                    + sum(len(renders[index]) for index in (
                        design_context["changedPageIndices"] if design_context.get("reviewScope") == "edited"
                        else payload.get("_serverRepresentativeIndices", [])))
                    + (sum(len(base64.b64decode(example["data_url"].split(",", 1)[1], validate=True))
                           for example in design_context.get("examples", []))
                       if design_context.get("reviewScope") == "edited" and design_context["changedPageIndices"] else 0)
                    + len(design_context.get("priorSheet") or b"")}
               if global_review else {}),
            **({"globalVisualEvidence": payload["_serverGlobalVisualEvidence"],
                "reviewInputKind": GLOBAL_BASELINE_VISUAL_EVIDENCE_REVISION,
                "inputImageBytes": len(sheet)
                    + payload["_serverGlobalVisualEvidence"]["panelBytes"]
                    + sum(len(renders[index]) for index in payload.get("_serverRepresentativeIndices", []))}
               if global_review and payload.get("_serverGlobalVisualEvidence") else {}),
            "slides": slides,
        }
    except DesignReviewInputCapacityError as error:
        return {"skipped": "review_input_capacity_unavailable", "reasonCode": str(error),
                **({"inputEnvelope": error.input_envelope} if error.input_envelope is not None else {})}
    except (ProviderOutcomeError, BudgetExceededError):
        if design_context is not None:
            raise
        LOGGER.warning("[authored] vision advisory failed", exc_info=True)
        return {"skipped": "provider_failed"}
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
