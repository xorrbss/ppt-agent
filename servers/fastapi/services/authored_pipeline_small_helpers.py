# ruff: noqa: F821
from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from typing import Any
from services.authored_local_fit_evidence_types import AcceptedLocalFit


def _resolve_authored_render_workers(
    author_workers: int,
    environment: Mapping[str, str] = os.environ,
) -> int:
    """Resolve the authored render queue against the managed Chromium ceiling."""
    raw = environment.get("AUTHORED_SLIDE_RENDER_CONCURRENCY")
    if raw is None:
        raw = environment.get("PUBLIC_RENDER_MAX_CONCURRENCY", str(author_workers))
    return max(1, int(raw))

def _reusable_source_repair_scene(
    *,
    source_reflow: bool,
    durable_evidence: Any,
    original_html: str,
    scene_html: str,
    slide_index: int,
) -> bool:
    """Reuse an admitted source repair only through its validated durable tuple.

    The caller has already obtained the checkpoint under attempt fencing and
    verified its immutable artifact revision. Admission additionally binds the
    reconstructed original, exact repaired scene, PNG, metrics, and slide index.
    """
    if not source_reflow or durable_evidence is None or durable_evidence.html != scene_html:
        return False
    return validate_structural_checkpoint(
        html=scene_html,
        png=durable_evidence.png,
        metrics=durable_evidence.metrics,
        expected_slide_index=slide_index,
        baseline_html_digest=html_digest(original_html),
    )

def _rehydrate_accepted_local_fit(
    receipt: Mapping[str, Any],
    *,
    html: str,
    png: bytes | None,
    metrics: Mapping[str, Any],
) -> AcceptedLocalFit | None:
    """Restore only an already validated, immutable normal-fit acceptance."""

    if receipt_is_preserved_original(receipt) or png is None:
        return None
    if receipt.get("png_digest") != "sha256:" + hashlib.sha256(png).hexdigest():
        return None
    return AcceptedLocalFit(
        html=html,
        png=png,
        metrics=dict(metrics),
        receipt=dict(receipt),
    )

def bind_runtime(runtime) -> None:
    for name, value in vars(runtime).items():
        if name not in {
            "_resolve_authored_render_workers",
            "_reusable_source_repair_scene",
            "_rehydrate_accepted_local_fit",
            "bind_runtime",
            "_call_pipeline_helper",
            "_evaluate_slide_quality",
            "_local_fit_quality_flag",
            "_local_fit_unresolved_flag",
        } and not name.startswith("__"):
            globals()[name] = value

def _call_pipeline_helper(name: str, *args, **kwargs):
    from services import authored_presentation_pipeline as runtime
    runtime._pipeline_helpers.bind_runtime(runtime)
    return getattr(runtime._pipeline_helpers, name)(*args, **kwargs)

def _evaluate_slide_quality(*args, **kwargs):
    return _call_pipeline_helper("_evaluate_slide_quality", *args, **kwargs)

def _local_fit_quality_flag(*args, **kwargs):
    return _call_pipeline_helper("_local_fit_quality_flag", *args, **kwargs)

def _local_fit_unresolved_flag(*args, **kwargs):
    return _call_pipeline_helper("_local_fit_unresolved_flag", *args, **kwargs)
