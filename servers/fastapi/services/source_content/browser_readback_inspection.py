"""Core browser source-readback inspection behind the public facade."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from typing import Any

from services.source_content.browser_readback import (
    BrowserSourceBlockEvidence,
    BrowserSourceReadbackError,
    BrowserSourceReadbackResult,
    _block_projection,
    _check_slide_capture,
    _field,
    _image_failed,
    _manifest_index,
    _mapping,
    _observe,
    _slides_metrics,
    _span_matcher,
    _verify_run_partition,
)
from services.source_content.source_runs import parse_source_binding_id
from services.source_content.text_stamping import deterministic_source_qid


def assert_deck_source_cardinality(
    *, manifest: Any, outline: Any, browser_metrics: Any, htmls: Sequence[str] | None = None
) -> tuple[Sequence[Any], Sequence[Any], list[Any]]:
    """The deck-level predicate: one source unit, one outline slide, one render.

    This is the only assertion that can see a deck which dropped or duplicated a
    source page, because it is the only one that compares whole-deck lengths.
    The per-slide admission gate inspects a one-unit manifest by construction,
    so publication has to call this once for the deck as well.
    """

    manifest_value = _mapping(manifest, "source manifest")
    outline_value = _mapping(outline, "source outline")
    units = _field(manifest_value, "units", default=())
    slides = _field(outline_value, "slides", default=())
    if not isinstance(units, Sequence) or isinstance(units, (str, bytes)) or not isinstance(slides, Sequence) or isinstance(slides, (str, bytes)):
        raise BrowserSourceReadbackError("source_content_browser_readback_invalid", detail="manifest units and outline slides must be sequences")
    metric_slides = _slides_metrics(browser_metrics)
    if len(units) != len(slides) or len(metric_slides) != len(slides) or (htmls is not None and len(htmls) != len(slides)):
        raise BrowserSourceReadbackError("source_content_browser_readback_failed", detail="source and browser slide cardinality differs", slide_indices=tuple(range(max(len(slides), len(metric_slides)))))
    return units, slides, metric_slides


def inspect_browser_source_readback(*, manifest: Any, outline: Any, browser_metrics: Any, htmls: Sequence[str] | None = None) -> BrowserSourceReadbackResult:
    """Validate source IDs, rendered text, geometry, order, and visibility.

    ``browser_metrics`` is the payload emitted by ``ensure_browser_metrics``
    (one object per slide, or ``{"slides": [...]}``). Every admitted text block
    must have one explicit source-block binding (or a complete run partition)
    and measured, visible DOM boxes.  Missing telemetry, clipping, occlusion,
    duplicate/unknown IDs, and rendered text mismatches all fail closed.
    """

    units, slides, metric_slides = assert_deck_source_cardinality(
        manifest=manifest, outline=outline, browser_metrics=browser_metrics, htmls=htmls
    )
    blocks_by_unit, ordered_ids_by_unit, expected_unit_order = _manifest_index(units)

    evidence: list[BrowserSourceBlockEvidence] = []
    missing: list[str] = []
    visibility_failures: list[str] = []
    integrity_failures: list[str] = []
    telemetry_failures: list[str] = []
    order_violations = 0
    binding_deviations = 0
    checked = 0
    failed_slides: set[int] = set()
    for index, (raw_slide, metrics) in enumerate(zip(slides, metric_slides, strict=True)):
        slide = _mapping(raw_slide, "outline slide")
        unit_id = str(_field(slide, "sourceUnitId", "source_unit_id", default=""))
        expected_ids = ordered_ids_by_unit.get(unit_id, [])
        reading_order = _field(slide, "readingOrder", "reading_order", default=())
        if not isinstance(reading_order, Sequence) or isinstance(reading_order, (str, bytes)):
            raise BrowserSourceReadbackError("source_content_browser_readback_invalid", detail="outline reading order must be a sequence")
        outline_ids = [str(item) for item in reading_order]
        allowed_ids = {
            block_id for block_id, block in blocks_by_unit.get(unit_id, {}).items()
            if str(_field(block, "kind", default="")) in {"text", "image"}
        }
        if any(block_id not in allowed_ids for block_id in outline_ids) or len(outline_ids) != len(set(outline_ids)):
            order_violations += 1
            failed_slides.add(index)
        outline_text_ids = [str(item) for item in reading_order if str(item) in expected_ids]
        if expected_unit_order[index] != unit_id or outline_text_ids != expected_ids:
            order_violations += 1
            failed_slides.add(index)
        canvas = _check_slide_capture(metrics, htmls[index] if htmls is not None else None, index)
        raw_elements = _field(metrics, "elements", default=())
        if not isinstance(raw_elements, Sequence) or isinstance(raw_elements, (str, bytes)):
            raise BrowserSourceReadbackError("source_content_browser_readback_failed", detail="browser elements telemetry is missing", slide_indices=(index,))
        # A source QID is canonical only when Chromium observed exactly one DOM
        # owner for it. Count every measured element, including non-source
        # authored furniture, so a duplicate outside the source subset cannot
        # masquerade as a valid source binding.
        qid_counts = Counter(
            str(_field(_mapping(item, "browser element metric"), "qid", default="") or "").strip()
            for item in raw_elements
        )
        qid_counts.pop("", None)
        observed: list[tuple[int, str, BrowserSourceBlockEvidence]] = []
        seen: set[str] = set()
        seen_qids: set[str] = set()
        run_groups: dict[str, list[tuple[int, int, int, Mapping[str, Any]]]] = {}
        for dom_index, raw_metric in enumerate(raw_elements):
            metric = _mapping(raw_metric, "browser element metric")
            raw_id = str(_field(metric, "sourceBlockId", "source_block_id", "dataSourceBlockId", "data_source_block_id", default="") or "")
            if not raw_id:
                continue
            block_id, run_start, run_end = parse_source_binding_id(raw_id)
            if block_id in seen or block_id not in expected_ids:
                order_violations += 1
                failed_slides.add(index)
                continue
            block = blocks_by_unit[unit_id][block_id]
            block_kind = str(_field(block, "kind", default="text"))
            if run_start is not None:
                if block_kind != "text":
                    order_violations += 1
                    failed_slides.add(index)
                    continue
                run_groups.setdefault(block_id, []).append((dom_index, run_start, run_end or run_start, metric))
                continue
            if block_id in run_groups:
                # A block cannot be bound both as a whole and as runs.
                order_violations += 1
                failed_slides.add(index)
                continue
            observation = _observe(metric, *canvas)
            payload = _mapping(_field(block, "payload", default={}), "source block payload")
            declared_qid = str(_field(block, "qid", "sourceQid", "source_qid", default="") or "").strip()
            expected_qid = declared_qid or deterministic_source_qid(block_id)
            if block_kind == "text":
                projection = _block_projection(block)
                accepts = _span_matcher(projection, *projection.whole) if projection is not None else None
                failed = observation.text_failed(accepts, expected_qid, qid_counts, seen_qids)
                if observation.integrity_failed(accepts, expected_qid, qid_counts, seen_qids):
                    integrity_failures.append(block_id)
            else:
                failed = (
                    not observation.qid
                    or observation.qid in seen_qids
                    or observation.qid != expected_qid
                    or qid_counts.get(observation.qid, 0) != 1
                    or not observation.in_canvas
                    or observation.visible is not True
                    or not observation.clipping_known
                    or observation.clipped
                    or not observation.occlusion_known
                    or (block_kind == "image" and _image_failed(observation, payload))
                )
                if (not observation.qid or observation.qid in seen_qids
                    or observation.qid != expected_qid
                    or qid_counts.get(observation.qid, 0) != 1
                    or (block_kind == "image" and _image_failed(observation, payload))):
                    integrity_failures.append(block_id)
            if observation.telemetry_failed(text=block_kind == "text"):
                telemetry_failures.append(block_id)
            if failed:
                visibility_failures.append(block_id)
                failed_slides.add(index)
            seen.add(block_id)
            seen_qids.add(observation.qid)
            observed.append((
                dom_index,
                block_id,
                BrowserSourceBlockEvidence(
                    block_id,
                    observation.qid,
                    observation.rendered,
                    observation.bbox or {},
                    bool(observation.visible),
                    observation.clipped,
                    observation.occluded,
                    observation.continuous,
                    block_kind,
                    observation.natural_width,
                    observation.natural_height,
                    observation.semantic_role,
                ),
            ))
        for block_id, runs in run_groups.items():
            if block_id in seen:
                order_violations += 1
                failed_slides.add(index)
                continue
            block_evidence, passed, ordered = _verify_run_partition(
                block_id,
                _block_projection(blocks_by_unit[unit_id][block_id]),
                runs,
                canvas=canvas,
                qid_counts=qid_counts,
                seen_qids=seen_qids,
                integrity_failures=integrity_failures,
                telemetry_failures=telemetry_failures,
            )
            seen.add(block_id)
            if not ordered:
                binding_deviations += 1
            if block_evidence is None:
                missing.append(block_id)
                failed_slides.add(index)
                continue
            if not passed:
                visibility_failures.append(block_id)
                failed_slides.add(index)
            observed.append((min(item[0] for item in runs), block_id, block_evidence))
        for block_id in expected_ids:
            checked += 1
            if block_id not in seen:
                missing.append(block_id)
                failed_slides.add(index)
        # DOM order across blocks is deliberately not compared with the manifest
        # order.  Restyling re-lays out the page, and the new template owns
        # where each source block lands; page numbers and running headers in
        # particular move into the template's own bands.  Source shape order is
        # not the source's reading order either, so equality here rejects
        # correct decks.  Every stronger guarantee is kept above: each block is
        # bound to exactly one visible DOM owner, no block is bound twice, none
        # is missing, and ``_verify_run_partition`` still requires a block's own
        # runs to appear in source order.
        evidence.extend(item[2] for item in sorted(observed, key=lambda item: item[0]))

    result = BrowserSourceReadbackResult(
        len(slides), checked, tuple(evidence), tuple(dict.fromkeys(missing)),
        order_violations, tuple(dict.fromkeys(visibility_failures)), binding_deviations,
        tuple(dict.fromkeys(integrity_failures)), tuple(dict.fromkeys(telemetry_failures)),
        tuple(sorted(failed_slides)),
    )
    return result
