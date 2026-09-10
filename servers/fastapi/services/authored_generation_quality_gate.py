"""Extracted implementation behind the authored generation facade."""

from __future__ import annotations


def _source_block_bindings(request) -> tuple[tuple[tuple[str, str], ...], ...] | None:
    """Project immutable source text into deterministic HTML binding pairs."""

    manifest = getattr(request, "_source_content_manifest", None)
    outline = getattr(request, "_source_document_outline", None)
    if manifest is None or outline is None:
        return None
    dump = getattr(manifest, "model_dump", None)
    value = dump(mode="json", by_alias=True) if callable(dump) else manifest
    odump = getattr(outline, "model_dump", None)
    outline_value = odump(mode="json", by_alias=True) if callable(odump) else outline
    units = value.get("units", ()) if isinstance(value, dict) else ()
    slides = outline_value.get("slides", ()) if isinstance(outline_value, dict) else ()
    blocks_by_id: dict[str, dict] = {}
    for unit in units:
        for block in unit.get("blocks", ()) if isinstance(unit, dict) else ():
            if isinstance(block, dict):
                blocks_by_id[str(block.get("sourceBlockId", ""))] = block
    result: list[tuple[tuple[str, str], ...]] = []
    for slide in slides:
        pairs: list[tuple[str, str]] = []
        for block_id in slide.get("readingOrder", ()) if isinstance(slide, dict) else ():
            block = blocks_by_id.get(str(block_id))
            if not block or block.get("kind") != "text":
                continue
            payload = block.get("payload") or {}
            text = payload.get("text") if isinstance(payload, dict) else None
            if isinstance(text, str) and text.strip():
                pairs.append((str(block_id), text))
        result.append(tuple(pairs))
    return tuple(result)

def _require_source_verbatim_bindings(
    htmls: list[str],
    bindings: tuple[tuple[tuple[str, str], ...], ...] | None,
) -> None:
    """Require the exact source seal to have existed before Chromium render."""

    if bindings is None or len(bindings) != len(htmls):
        raise BrowserSourceReadbackError(
            "source_content_browser_readback_failed",
            detail="source text bindings are unavailable for the rendered deck",
        )
    for slide_index, (html, slide_bindings) in enumerate(
        zip(htmls, bindings, strict=True)
    ):
        stamped = stamp_source_block_ids(html, slide_bindings)
        if stamped != html:
            raise BrowserSourceReadbackError(
                "source_content_browser_readback_failed",
                detail="source text was not sealed before Chromium render",
                slide_indices=(slide_index,),
            )

def _source_image_block_bindings(request) -> tuple[tuple[str, ...], ...] | None:
    """Project source image *occurrences* into deterministic DOM bindings."""

    manifest = getattr(request, "_source_content_manifest", None)
    outline = getattr(request, "_source_document_outline", None)
    if manifest is None or outline is None:
        return None
    dump = getattr(manifest, "model_dump", None)
    value = dump(mode="json", by_alias=True) if callable(dump) else manifest
    odump = getattr(outline, "model_dump", None)
    outline_value = odump(mode="json", by_alias=True) if callable(odump) else outline
    units = value.get("units", ()) if isinstance(value, dict) else ()
    slides = outline_value.get("slides", ()) if isinstance(outline_value, dict) else ()
    blocks_by_id: dict[str, dict] = {}
    for unit in units:
        for block in unit.get("blocks", ()) if isinstance(unit, dict) else ():
            if isinstance(block, dict):
                blocks_by_id[str(block.get("sourceBlockId", ""))] = block
    result: list[tuple[str, ...]] = []
    for slide in slides:
        ids: list[str] = []
        for block_id in slide.get("readingOrder", ()) if isinstance(slide, dict) else ():
            block = blocks_by_id.get(str(block_id))
            if block and block.get("kind") == "image":
                ids.append(str(block_id))
        result.append(tuple(ids))
    return tuple(result)

async def _final_authored_quality_gate(
    *,
    presentation_id: uuid.UUID | None = None,
    htmls: list[str],
    capacity_plan,
    profile,
    reference_mode: str,
    browser_metrics_by_slide=None,
    flagged_slide_indices: Sequence[int] = (),
    # M1 (docs/authored-fit-magnitude-measurement-design-20260904.md, 3.4): an
    # out-parameter rather than a new key in the residual dicts, so the gate
    # receipt this function feeds keeps its exact recorded shape.
    fit_summaries: dict[int, Mapping[str, object]] | None = None,
) -> list[dict[str, object]]:
    """Re-check the exact final HTML before export/publication.

    The first pipeline pass can be followed by VQA or a bounded slide repair.
    This final readback keeps the receipt bound to the bytes that are actually
    exported and prevents a post-gate edit from reintroducing a safety defect.
    Browser metrics are cache hits for unchanged HTML, so shadow semantic mode
    does not launch a second render merely to collect the gate evidence.
    """
    if browser_metrics_by_slide is not None:
        metrics = browser_metrics_by_slide
    else:
        with set_render_deck_scope(str(presentation_id)):
            metrics = await asyncio.gather(*(ensure_browser_metrics(html) for html in htmls))
    residual: list[dict[str, object]] = []
    for index, (html, browser_metrics) in enumerate(zip(htmls, metrics, strict=True)):
        if capacity_plan is not None:
            violations = capacity_violations(html, browser_metrics, capacity_plan.at(index))
            if violations and not flagged_slide_absorbs_capacity(index, violations, flagged=flagged_slide_indices):
                # Keep a content-free, digest-bound diagnostic in the service
                # log even though the workspace projection exposes only a safe
                # terminal code.
                LOGGER.error(
                    "[authored] final capacity failure slide=%d html_digest=%s violations=%s",
                    index + 1,
                    digest_html(html),
                    list(violations),
                )
                raise AuthoredStageError(
                    slide_index=index,
                    stage="render",
                    code="authored_slide_capacity_exceeded",
                    # The first-pass gate already sent this slide through the
                    # one bounded repair cycle. Replaying the same completed
                    # checkpoint cannot change a residual deterministic limit.
                    retryable=False,
                    message=(
                        f"Slide {index + 1} exceeded its frozen capacity envelope: "
                        + ", ".join(violations)
                    ),
                )
        report = inspect_authored_dom(
            html,
            browser_metrics,
            reference_mode=reference_mode,
            strict_structural_off_canvas=(profile.profile_id != "ordinary"),
        )
        if report.telemetry_status != "available":
            if index in {int(value) for value in flagged_slide_indices}:
                residual.append(
                    {
                        "slide_index": index,
                        "issues": [
                            {
                                "category": "telemetry_unavailable",
                                "severity": "high",
                                "message": (
                                    "composition safety telemetry is "
                                    + str(report.telemetry_status)
                                ),
                            }
                        ],
                    }
                )
                continue
            raise AuthoredStageError(
                slide_index=index,
                stage="render",
                code="authored_composition_safety_gate_failed",
                retryable=True,
                message=f"Slide {index + 1} failed the composition safety gate (telemetry:{report.telemetry_status})",
            )
        defects = blocking_safety_issues(report)
        if defects:
            residual.append(
                {
                    "slide_index": index,
                    "issues": [issue.to_dict() for issue in defects],
                }
            )
            if fit_summaries is not None and report.fit_summary is not None:
                fit_summaries[index] = dict(report.fit_summary)
    return residual


_LOCAL_NAMES = frozenset(globals())

def bind_runtime(runtime) -> None:
    for name, value in vars(runtime).items():
        if name not in _LOCAL_NAMES and not name.startswith("__"):
            globals()[name] = value
