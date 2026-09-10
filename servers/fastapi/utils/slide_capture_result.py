"""Atomic authored render capture, independent of renderer process state."""
from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

@dataclass(frozen=True, slots=True)
class RenderCapture:
    """Atomic result for one HTML render.

    ``html_digest``, PNG bytes and browser metrics are produced/selected as one
    value so callers cannot accidentally pair telemetry from a different HTML
    revision.  ``__iter__`` retains the historical ``png, metrics = ...`` API.
    ``runtime_counters`` is a snapshot for measuring duplicate browser/I/O work
    across large decks without retaining process objects in the result.
    """

    html_digest: str
    png: bytes
    browser_metrics: Mapping[str, Any] | None
    png_digest: str
    metrics_digest: str | None
    runtime_counters: Mapping[str, int]
    fitted_html: str | None = None
    fit_report: Mapping[str, Any] | None = None

    @classmethod
    def from_payload(
        cls,
        *,
        html_digest: str,
        png: bytes,
        browser_metrics: Mapping[str, Any] | None,
        runtime_counters: Mapping[str, int],
        fitted_html: str | None = None,
        fit_report: Mapping[str, Any] | None = None,
    ) -> "RenderCapture":
        canonical = json.dumps(
            browser_metrics, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, default=str,
        ).encode("utf-8") if browser_metrics is not None else None
        return cls(
            html_digest=html_digest,
            png=png,
            browser_metrics=browser_metrics,
            png_digest="sha256:" + hashlib.sha256(png).hexdigest(),
            metrics_digest=("sha256:" + hashlib.sha256(canonical).hexdigest()) if canonical is not None else None,
            runtime_counters=dict(runtime_counters),
            fitted_html=fitted_html,
            fit_report=fit_report,
        )

    def __iter__(self):
        # Backward compatible with all existing callers.
        yield self.png
        yield self.browser_metrics

    @property
    def metrics(self) -> Mapping[str, Any] | None:
        """Short alias for callers that refer to browser telemetry as metrics."""
        return self.browser_metrics

    def as_dict(self, *, include_payload: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {
            "htmlDigest": self.html_digest,
            "pngDigest": self.png_digest,
            "metricsDigest": self.metrics_digest,
            "browserMetricsCaptured": self.browser_metrics is not None,
            "runtimeCounters": dict(self.runtime_counters),
        }
        if include_payload:
            result["pngBase64"] = base64.b64encode(self.png).decode("ascii")
            result["browserMetrics"] = self.browser_metrics
        return result
