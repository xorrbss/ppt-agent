"""Producer adapters retain old checks only for measured shadow comparison."""

from __future__ import annotations

from services.authored_admission_policy import SOURCE_READBACK_FAILED
from services.authored_admission_context import digest_html

from typing import Any, Mapping

from services.authored_admission_session import check_producer, current_session
from services.source_content.browser_readback import (
    BrowserSourceReadbackError,
    verify_browser_source_readback,
    stamp_source_image_block_ids,
)
from services.source_content.text_stamping import stamp_source_blocks
from utils.llm_calls.author_slide import validate_authored_slide_html


def author_html(
    html: str,
    *,
    index: int,
    bindings=None,
    normalization_receipt=None,
    validate=validate_authored_slide_html,
    stamp=stamp_source_blocks,
    producer: str = "author",
    validate_policy: bool = True,
    fresh: bool = True,
    raw_input: bool = False,
) -> str:
    session = current_session()
    if not raw_input and session is not None and (index, digest_html(html)) in session.expanded_outputs:
        fresh = False

    def legacy(value: str) -> str:
        if validate_policy:
            value = validate(value, allow_illustration_slots=True, normalization_receipt=normalization_receipt)
        if bindings is not None:
            value = stamp(value, bindings).html
        return value

    result, admission = check_producer(
        html,
        index=index,
        producer=producer,
        legacy=legacy,
        slots=True,
        representation="token" if fresh and producer != "illustration" else "expanded",
    )
    if admission is not None and session.mode == "enforce":
        session.expanded_outputs.add((index, digest_html(result)))
    if normalization_receipt is not None and admission is not None and session.mode == "enforce":
        normalization_receipt.extend(admission.normalizations)
    return result


def provider_raw_html(html: str, *, index: int, producer="author", normalization_receipt=None) -> str:
    """Enter immediately after extraction, before provider-side legacy rewrites."""
    return author_html(
        html,
        index=index,
        producer=producer,
        validate_policy=False,
        raw_input=True,
        normalization_receipt=normalization_receipt,
    )


def provider_expanded_html(html: str, *, index: int, validate, normalization_receipt=None, producer="author") -> str:
    return author_html(
        html,
        index=index,
        producer=producer,
        validate=validate,
        fresh=False,
        normalization_receipt=normalization_receipt,
    )


def illustrate_html(html: str, *, index: int, validate=validate_authored_slide_html, producer="illustration") -> str:
    result, _ = check_producer(
        html,
        index=index,
        producer=producer,
        legacy=lambda value: validate(value, finalized=True),
        representation="expanded",
        finalized=True,
    )
    return result


def source_image_html(html: str, *, index: int, bindings) -> str:
    result, _ = check_producer(
        html,
        index=index,
        producer="illustration",
        legacy=lambda value: stamp_source_image_block_ids(value, bindings),
        representation="expanded",
    )
    return result


def source_candidate(
    html: str,
    *,
    index: int,
    metrics: Mapping[str, object],
    manifest: Any,
    outline: Any,
    bindings,
    producer="gate_repair",
) -> None:
    def legacy(value: str) -> str:
        if stamp_source_blocks(value, bindings).html != value:
            raise BrowserSourceReadbackError(
                SOURCE_READBACK_FAILED,
                detail="source text was not sealed before Chromium render",
                slide_indices=(index,),
            )
        manifest_value = (
            manifest.model_dump(mode="json", by_alias=True) if hasattr(manifest, "model_dump") else manifest
        )
        outline_value = outline.model_dump(mode="json", by_alias=True) if hasattr(outline, "model_dump") else outline
        slide = outline_value["slides"][index]
        unit_id = str(slide.get("sourceUnitId") or slide.get("source_unit_id") or "")
        unit = next(
            (
                item
                for item in manifest_value.get("units", ())
                if str(item.get("sourceUnitId") or item.get("source_unit_id") or "") == unit_id
            ),
            None,
        )
        if unit is None:
            raise BrowserSourceReadbackError(
                SOURCE_READBACK_FAILED,
                detail="source unit is unavailable during repair admission",
                slide_indices=(index,),
            )
        verify_browser_source_readback(
            manifest={"units": [unit]}, outline={"slides": [slide]}, browser_metrics=[metrics], htmls=[value]
        )
        return value

    check_producer(html, index=index, producer=producer, legacy=legacy, metrics=metrics)


def require_bound_source_html(
    htmls: list[str],
    bindings: tuple[tuple[tuple[str, str], ...], ...] | None,
    *,
    stamp,
) -> None:
    """Require the exact source seal to have existed before Chromium render."""

    session = current_session()
    if session is not None and session.mode == "enforce":
        # The captured gate owns this predicate; rejected captures remain evidence
        # until repair completes and the publication gate admits the final bytes.
        return
    if bindings is None or len(bindings) != len(htmls):
        raise BrowserSourceReadbackError(
            SOURCE_READBACK_FAILED,
            detail="source text bindings are unavailable for the rendered deck",
        )
    for slide_index, (html, slide_bindings) in enumerate(zip(htmls, bindings, strict=True)):
        stamped = stamp(html, slide_bindings)
        if stamped != html:
            raise BrowserSourceReadbackError(
                SOURCE_READBACK_FAILED,
                detail="source text was not sealed before Chromium render",
                slide_indices=(slide_index,),
            )
