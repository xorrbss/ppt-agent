"""Python boundary for the authored local-fit browser fragment."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from typing import Any

from utils.authored_fit_edits import apply_fit_edits
from utils.authored_qid_namespace import ensure_authored_qids
from utils.slide_capture_fit_script import FIT_VERSION, fit_script_fragment


def html_digest(html: str) -> str:
    return "sha256:" + hashlib.sha256(html.encode("utf-8")).hexdigest()


def require_qid_complete(html: str) -> None:
    if ensure_authored_qids(html) != html:
        raise ValueError("fit requires qid-complete HTML")


def fit_probe_prelude(policy: Mapping[str, Any] | None = None) -> str:
    """Return the fitter and its no-throw ``__codexFitPass`` probe API."""

    encoded = json.dumps(
        dict(policy or {}),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).replace("</", "<\\/")
    return (
        f"const __codexLocalFitPolicy = {encoded};\n"
        + fit_script_fragment()
        + "\nconst __codexFitPass = (pass) => {\n"
        + "  try { return __codexFit.apply(pass); }\n"
        + "  catch (error) { return {version: "
        + json.dumps(FIT_VERSION)
        + ", requested: Boolean(__codexLocalFitPolicy.fit), fit_state: String(pass || 'provisional'), "
        + "status: 'unresolved', changed: false, partial: true, "
        + "error: String(error && (error.message || error) || 'fit failed').slice(0, 240), "
        + "unresolved_reasons: ['unsupported_geometry'], operations: [], edits: [], baseline: null, "
        + "runs: [], groups: [], cards: [], candidates: [], telemetry: {}}; }\n"
        + "};\n"
    )


def fit_report_of(metrics: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    if not isinstance(metrics, Mapping):
        return None
    report = metrics.get("fit")
    return report if isinstance(report, Mapping) else None


def _report_edits(report: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    edits = report.get("edits") if report is not None else None
    if not isinstance(edits, (list, tuple)):
        return []
    return [edit for edit in edits if isinstance(edit, Mapping)]


def fit_capture_fields(
    html: str,
    metrics: Mapping[str, Any] | None,
    *,
    fit: bool,
    digest: str,
    cache_metrics: Callable[[str, Mapping[str, Any]], Mapping[str, Any] | None],
) -> tuple[str, str | None, Mapping[str, Any] | None, Mapping[str, Any] | None]:
    """Bind a measured fit report to the exact reconstructed source string."""

    report = fit_report_of(metrics)
    if report is None:
        return digest, None, None, metrics
    edits = _report_edits(report)
    if not fit or not edits:
        if metrics is not None:
            metrics = cache_metrics(html, metrics) or metrics
        return digest, None, report, metrics
    fitted_html = apply_fit_edits(html, edits)
    if metrics is not None:
        metrics = cache_metrics(fitted_html, metrics) or metrics
    return html_digest(fitted_html), fitted_html, report, metrics


__all__ = [
    "fit_capture_fields",
    "fit_probe_prelude",
    "fit_report_of",
    "html_digest",
    "require_qid_complete",
]
