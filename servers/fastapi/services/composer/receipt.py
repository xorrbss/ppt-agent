"""Composer errors, stage timing and the ``composer-receipt/v1`` document.

The receipt is written for every run, succeeded or failed, next to the
authored quality receipts so operators can read the rung a page settled on,
how many Chromium renders it cost and which rungs were rejected on the way.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from services.composer.plan import ComposerSlideResult

RECEIPT_SCHEMA_VERSION = "composer-receipt/v1"


class ComposerPipelineError(RuntimeError):
    """A Composer failure with a stable durable code; non-retryable by default."""

    code = "composer_generation_failed"
    retryable = False

    def __init__(self, code: str | None = None, *, detail: str | None = None, retryable: bool = False) -> None:
        resolved = code or type(self).code
        super().__init__(resolved if detail is None else f"{resolved}: {detail}")
        self.code = resolved
        self.detail = detail
        self.retryable = retryable


class ComposerAssertionError(ComposerPipelineError):
    """A readback gate rejected rendered HTML.

    The renderer writes every attribute the gates check, so a rejection is a
    renderer bug rather than a user error: the operation fails non-retryably
    with ``code`` and the receipt records the slide.
    """

    code = "composer_assertion_failed"

    def __init__(self, slide_index: int, code: str, detail: str | None = None) -> None:
        super().__init__(code, detail=detail, retryable=False)
        self.slide_index = slide_index


class ComposerPublicationError(ComposerPipelineError):
    """Persistence or publication CAS failed after every slide was accepted."""

    code = "composer_publication_failed"


class StageTimer:
    """Wall-clock milliseconds per named stage, monotonic and additive."""

    def __init__(self) -> None:
        self._started = time.monotonic()
        self._stages: dict[str, int] = {}
        self._open: dict[str, float] = {}

    def start(self, stage: str) -> None:
        self._open[stage] = time.monotonic()

    def stop(self, stage: str) -> int:
        started = self._open.pop(stage, None)
        elapsed = 0 if started is None else int(round((time.monotonic() - started) * 1000))
        self._stages[stage] = self._stages.get(stage, 0) + max(0, elapsed)
        return elapsed

    def as_dict(self) -> dict[str, int]:
        total = int(round((time.monotonic() - self._started) * 1000))
        return {**self._stages, "total": max(0, total)}

    @property
    def active_stage(self) -> str | None:
        return next(reversed(self._open), None)


def _issue_category(issue: str) -> str:
    return issue.split(":", 1)[0].strip()


def is_accepted_residual(
    rung: int, blocking_issues: Sequence[str], *, fit_policy_revision: str | None = None,
) -> bool:
    """Rung 5's own overlap residue is the source page's geometry, not a defect.

    ``fit_slide`` always terminates rung 5 (source geometry, page_scale 0.90)
    regardless of any remaining blocking issue. The pipeline still fails the
    operation unless every residual issue is category ``overlap`` -- shapes
    the source page itself overlaps, reproduced verbatim in source-geometry
    mode. A residual ``clipping``, ``off_canvas`` or ``overflow`` issue, or
    any residual issue at a rung before 5, is never accepted.
    """

    return fit_policy_revision != "composer-source-fit/v2" and rung == 5 and bool(blocking_issues) and all(
        _issue_category(issue) == "overlap" for issue in blocking_issues
    )


def slide_receipt_entry(result: ComposerSlideResult, *, stamping_rebound: bool = False) -> dict[str, Any]:
    receipt = result.receipt
    residual = is_accepted_residual(
        receipt.rung, receipt.blocking_issues, fit_policy_revision=receipt.fit_policy_revision,
    )
    return {
        "index": receipt.slide_index,
        "rung": receipt.rung,
        "parameters": receipt.parameters.model_dump(mode="json"),
        "renders": receipt.renders,
        "estimateUsed": receipt.estimate_used,
        "blockingIssues": [] if residual else list(receipt.blocking_issues),
        #: Accepted rung-5 overlap residue, kept for observability only; it
        #: never fails the operation (see ``is_accepted_residual``).
        "residualIssues": list(receipt.blocking_issues) if residual else [],
        "sourceTopologyWitnesses": list(result.metrics.get("source_topology_witnesses", [])),
        "stampingRebound": bool(stamping_rebound),
        "trail": list(receipt.trail),
    }


def rung_histogram(results: Sequence[ComposerSlideResult]) -> dict[str, int]:
    histogram: dict[str, int] = {}
    for result in results:
        key = str(result.receipt.rung)
        histogram[key] = histogram.get(key, 0) + 1
    return dict(sorted(histogram.items(), key=lambda item: int(item[0])))


def build_composer_receipt(
    *,
    presentation_id: Any,
    job_id: Any,
    page_count: int,
    results: Sequence[ComposerSlideResult],
    timings_ms: Mapping[str, int],
    failure: Mapping[str, Any] | None = None,
    publication: Mapping[str, Any] | None = None,
    stamping_rebound: Mapping[int, bool] | None = None,
    fit_policy: Mapping[str, Any] | None = None,
    source_readback: Mapping[str, Any] | None = None,
    source_structure_readback: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    ordered = sorted(results, key=lambda item: item.slide_index)
    rebound = stamping_rebound or {}
    document: dict[str, Any] = {
        "schemaVersion": RECEIPT_SCHEMA_VERSION,
        "presentationId": str(presentation_id),
        "jobId": None if job_id is None else str(job_id),
        "pageCount": int(page_count),
        "status": "failed" if failure else "succeeded",
        "slides": [
            slide_receipt_entry(item, stamping_rebound=rebound.get(item.slide_index, False))
            for item in ordered
        ],
        "rungHistogram": rung_histogram(ordered),
        "timingsMs": dict(timings_ms),
    }
    if failure:
        document["failure"] = dict(failure)
    if fit_policy is not None:
        document["fitPolicy"] = dict(fit_policy)
    if source_readback is not None:
        document["sourceReadback"] = dict(source_readback)
    if source_structure_readback is not None:
        document["sourceStructureReadback"] = dict(source_structure_readback)
    if publication:
        document["publication"] = dict(publication)
    return document


def composer_receipt_path(job_id: Any, presentation_id: Any) -> Path:
    """``job-<id>-composer.json`` under the authored quality receipt root."""

    # Lazy: the receipt root resolves the settings registry and app data.
    from services.authored_quality_receipt_v2 import quality_receipt_root

    identity = str(job_id or presentation_id or "").strip()
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in identity) or "unknown"
    return quality_receipt_root() / f"job-{safe}-composer.json"


def _write_json(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


async def write_composer_receipt(document: Mapping[str, Any], *, job_id: Any, presentation_id: Any) -> Path:
    path = composer_receipt_path(job_id, presentation_id)
    await asyncio.to_thread(_write_json, path, document)
    return path


__all__ = [
    "RECEIPT_SCHEMA_VERSION",
    "ComposerAssertionError",
    "ComposerPipelineError",
    "ComposerPublicationError",
    "StageTimer",
    "build_composer_receipt",
    "composer_receipt_path",
    "is_accepted_residual",
    "rung_histogram",
    "slide_receipt_entry",
    "write_composer_receipt",
]
