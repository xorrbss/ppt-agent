"""Pure report helpers for the Composer offline harness.

Everything here works on plain dicts and lists so it can be unit tested
without Chromium, a database or the Composer stages themselves: source
bindings, the readback assertions, enrichment coverage, receipt aggregation
and the phase 0 acceptance verdict. Markdown lives in
``composer_offline_eval_markdown``.
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

ACCEPTANCE_WALL_SECONDS = 300.0


# --- Source bindings and readback assertions ---------------------------------


def source_block_bindings(
    manifest: Mapping[str, Any], outline: Mapping[str, Any]
) -> tuple[tuple[tuple[str, str], ...], ...]:
    """Project source text into per-slide ``(block_id, text)`` binding pairs.

    Mirrors ``_source_block_bindings`` in
    ``services.authored_presentation_generation`` without importing it.
    """

    blocks_by_id: dict[str, Mapping[str, Any]] = {}
    for unit in manifest.get("units", ()) or ():
        for block in unit.get("blocks", ()) or ():
            if isinstance(block, Mapping):
                blocks_by_id[str(block.get("sourceBlockId", ""))] = block
    result: list[tuple[tuple[str, str], ...]] = []
    for slide in outline.get("slides", ()) or ():
        pairs: list[tuple[str, str]] = []
        for block_id in slide.get("readingOrder", ()) or ():
            block = blocks_by_id.get(str(block_id))
            if not block or block.get("kind") != "text":
                continue
            payload = block.get("payload") or {}
            text = payload.get("text") if isinstance(payload, Mapping) else None
            if isinstance(text, str) and text.strip():
                pairs.append((str(block_id), text))
        result.append(tuple(pairs))
    return tuple(result)


def truncate_for_readback(
    manifest: Mapping[str, Any], outline: Mapping[str, Any], limit: int | None
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Keep the first ``limit`` pages of both contracts so cardinality matches."""

    units = sorted(
        manifest.get("units", ()) or (),
        key=lambda unit: (int(unit.get("sourceIndex", 0)), str(unit.get("sourceUnitId", ""))),
    )
    slides = list(outline.get("slides", ()) or ())
    if limit is not None:
        units = units[:limit]
        slides = slides[:limit]
    return {**manifest, "units": units}, {**outline, "slides": slides}


def stamp_assertions(
    htmls: Sequence[str], bindings: Sequence[Sequence[tuple[str, str]]]
) -> list[dict[str, Any]]:
    """Assert ``stamp_source_block_ids`` succeeds (raises nothing) on every slide.

    The Composer renderer writes ``data-source-block-id`` itself, so stamping
    is usually a no-op; but the stamper may still prefer a coarser parent
    wrapper whose concatenated text equals the whole block, which changes the
    document's bytes without being a defect. Only a raise -- a binding the
    stamper could not place -- is a failure; a byte-different result is a
    legitimate rebind and is reported as ``rebound``, not as a failure.
    """

    from services.source_content.source_stamp_dom import BrowserSourceReadbackError
    from services.source_content.text_stamping import stamp_source_block_ids

    rows: list[dict[str, Any]] = []
    for index, html in enumerate(htmls):
        slide_bindings = tuple(bindings[index]) if index < len(bindings) else ()
        row: dict[str, Any] = {
            "slideIndex": index, "bindings": len(slide_bindings), "passed": False, "rebound": False, "error": None,
        }
        try:
            stamped = stamp_source_block_ids(html, slide_bindings)
            row["passed"] = True
            row["rebound"] = stamped != html
        except BrowserSourceReadbackError as error:
            row["error"] = f"{error.code}: {error.detail}"
        except ValueError as error:  # malformed HTML or bindings
            row["error"] = f"{type(error).__name__}: {error}"
        rows.append(row)
    return rows


def deck_readback(
    manifest: Mapping[str, Any], outline: Mapping[str, Any], htmls: Sequence[str]
) -> dict[str, Any]:
    """Run ``verify_source_verbatim_readback`` and flatten its verdict."""

    from services.source_content.readback import (
        SourceContentReadbackError,
        verify_source_verbatim_readback,
    )

    try:
        result = verify_source_verbatim_readback(manifest=manifest, outline=outline, htmls=list(htmls))
    except SourceContentReadbackError as error:
        return {
            "passed": False,
            "code": error.code,
            "detail": error.detail,
            "slideIndices": list(error.slide_indices),
            "missingBlockIds": list(error.block_ids),
            "orderViolations": error.order_violations,
        }
    return {"passed": result.passed, **result.as_dict()}


# --- Enrichment coverage -------------------------------------------------------

_SIZE_KEYS = ("slideSize", "slide_size", "sourcePageEmu", "source_page_emu", "pageSize", "page_size")
_SIZE_PAIRS = (("cx", "cy"), ("widthEmu", "heightEmu"), ("width", "height"))


def _emu_pair(value: Any) -> tuple[int, int] | None:
    if isinstance(value, Mapping):
        for width_key, height_key in _SIZE_PAIRS:
            if width_key in value and height_key in value:
                try:
                    return int(value[width_key]), int(value[height_key])
                except (TypeError, ValueError):
                    return None
        return None
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and len(value) == 2:
        try:
            return int(value[0]), int(value[1])
        except (TypeError, ValueError):
            return None
    return None


def find_slide_size(manifest: Mapping[str, Any]) -> tuple[str | None, tuple[int, int] | None]:
    """Locate the source page size: manifest ``slideSize`` (``{cx, cy}`` EMU) first."""

    for key in _SIZE_KEYS:
        pair = _emu_pair(manifest.get(key))
        if pair:
            return f"manifest.{key}", pair
    for unit in (manifest.get("units", ()) or ())[:1]:
        for key in _SIZE_KEYS:
            pair = _emu_pair(unit.get(key))
            if pair:
                return f"unit.{key}", pair
    return None, None


def _run_size(run: Any, style: Any) -> float | None:
    """``sizePt`` of one run from its parallel ``runStyles`` entry (or a dict run)."""

    if isinstance(style, Mapping) and style.get("sizePt") is not None:
        return float(style["sizePt"])
    if isinstance(run, Mapping):
        for key in ("sizePt", "size_pt"):
            if run.get(key) is not None:
                return float(run[key])
    return None


def _paragraph_runs(paragraph: Mapping[str, Any]) -> list[tuple[Any, Any]]:
    runs = list(paragraph.get("runs") or ())
    styles = list(paragraph.get("runStyles") or ())
    styles += [None] * (len(runs) - len(styles))
    return [(run, style) for run, style in zip(runs, styles, strict=False) if run != "\n"]


def enrichment_coverage(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Coverage of the typography enrichment on a manifest.

    Counts ``payload.paragraphs[i].runs[j]`` against the parallel
    ``payload.paragraphs[i].runStyles[j].sizePt``, blocks carrying
    ``geometry`` (``cx``/``cy``), ``dominantSizePt`` and ``placeholderType``,
    and whether the manifest carries ``slideSize``.
    """

    counts = {"blocks": 0, "geometry": 0, "text": 0, "runs": 0, "sized": 0, "dominant": 0, "placeholder": 0}
    for unit in manifest.get("units", ()) or ():
        for block in unit.get("blocks", ()) or ():
            counts["blocks"] += 1
            payload = block.get("payload") if isinstance(block.get("payload"), Mapping) else {}
            geometry = payload.get("geometry")
            if isinstance(geometry, Mapping) and geometry.get("cx") and geometry.get("cy"):
                counts["geometry"] += 1
            if block.get("kind") != "text":
                continue
            counts["text"] += 1
            counts["dominant"] += payload.get("dominantSizePt") is not None
            counts["placeholder"] += payload.get("placeholderType") is not None
            paragraphs = [p for p in payload.get("paragraphs") or () if isinstance(p, Mapping)]
            pairs = [pair for paragraph in paragraphs for pair in _paragraph_runs(paragraph)]
            if not paragraphs:
                pairs = _paragraph_runs({"runs": payload.get("runs"), "runStyles": payload.get("runStyles")})
            counts["runs"] += len(pairs)
            counts["sized"] += sum(_run_size(run, style) is not None for run, style in pairs)
    location, page_emu = find_slide_size(manifest)
    return {
        "blocks": counts["blocks"],
        "blocksWithGeometry": counts["geometry"],
        "blockGeometryShare": _share(counts["geometry"], counts["blocks"]),
        "textBlocks": counts["text"],
        "textBlocksWithDominantSizePt": counts["dominant"],
        "textBlocksWithPlaceholderType": counts["placeholder"],
        "textRuns": counts["runs"],
        "textRunsWithSizePt": counts["sized"],
        "textRunSizeShare": _share(counts["sized"], counts["runs"]),
        "slideSizePresent": page_emu is not None,
        "slideSizeLocation": location,
        "sourcePageEmu": list(page_emu) if page_emu else None,
    }


def _share(part: int, whole: int) -> float:
    return round(part / whole, 4) if whole else 0.0


# --- Aggregation ---------------------------------------------------------------


def percentile(values: Sequence[float], share: float) -> float | None:
    """Nearest-rank percentile; ``None`` for an empty sample."""

    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(share * len(ordered)))
    return float(ordered[min(rank, len(ordered)) - 1])


def aggregate_slides(
    slides: Sequence[Mapping[str, Any]], stamps: Sequence[Mapping[str, Any]] | None = None
) -> dict[str, Any]:
    """Histogram, percentiles and the blocking list over per-slide rows.

    ``stamps`` (the ``stamp_assertions`` rows) is optional so callers that
    have not run the readback yet keep the same aggregate shape; when given,
    ``stampingRebound`` counts slides whose stamping was a legitimate coarser
    rebind rather than a byte-identical no-op.
    """

    histogram: dict[str, int] = {}
    renders: list[float] = []
    durations: list[float] = []
    blocking: list[int] = []
    errors: list[int] = []
    for row in slides:
        if row.get("error"):
            key = "error"
        else:
            key = str(row["rung"]) if row.get("rung") is not None else "unfitted"
        histogram[key] = histogram.get(key, 0) + 1
        if row.get("renders") is not None:
            renders.append(float(row["renders"]))
        if row.get("fitMs") is not None:
            durations.append(float(row["fitMs"]))
        if row.get("blockingIssues"):
            blocking.append(int(row["slideIndex"]))
        if row.get("error"):
            errors.append(int(row["slideIndex"]))
    result = {
        "slides": len(slides),
        "rungHistogram": dict(sorted(histogram.items())),
        "rendersTotal": int(sum(renders)),
        "rendersP50": percentile(renders, 0.5),
        "rendersP90": percentile(renders, 0.9),
        "fitMsP50": percentile(durations, 0.5),
        "fitMsP90": percentile(durations, 0.9),
        "blockingSlides": blocking,
        "errorSlides": errors,
    }
    if stamps is not None:
        result["stampingRebound"] = sum(1 for row in stamps if row.get("rebound"))
    return result


def acceptance(report: Mapping[str, Any]) -> dict[str, Any]:
    """Phase 0 acceptance verdict from the assembled report."""

    aggregate = report.get("aggregate") or {}
    readback = report.get("readback") or {}
    deck = readback.get("deck") or {}
    stamps = readback.get("stamping") or []
    total_ms = float((report.get("timings") or {}).get("totalMs") or 0.0)
    checks = {
        "allSlidesFitted": not aggregate.get("errorSlides") and bool(aggregate.get("slides")),
        "zeroBlockingIssues": not aggregate.get("blockingSlides"),
        "stampingPassed": bool(stamps) and all(row.get("passed") for row in stamps),
        "deckReadbackPassed": bool(deck.get("passed")),
        "underFiveMinutes": total_ms <= ACCEPTANCE_WALL_SECONDS * 1000.0,
        "zeroProviderCalls": int(report.get("providerCalls") or 0) == 0,
    }
    return {"passed": all(checks.values()), "checks": checks}


__all__ = [
    "ACCEPTANCE_WALL_SECONDS",
    "acceptance",
    "aggregate_slides",
    "deck_readback",
    "enrichment_coverage",
    "find_slide_size",
    "percentile",
    "source_block_bindings",
    "stamp_assertions",
    "truncate_for_readback",
]
