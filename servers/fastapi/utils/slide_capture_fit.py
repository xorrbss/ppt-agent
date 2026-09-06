"""API-side plumbing for the in-render card fit.

Three concerns live here so ``utils/slide_capture.py`` only gains a call site:

* **class injection** -- the opt-in signal.  The probe fits only when the
  request HTML carries ``codex-fit-v1`` on the ``<html>`` element.  ``class`` is
  a global attribute in ``utils/renderer_markup_policy.py``, so the marker
  passes renderer admission without touching the attribute allowlist, and it is
  present only on the render request: it never reaches the persisted document.
* **the precondition** -- with ``fit=True`` the caller must hand over
  qid-complete HTML, because the report addresses elements by ``data-qid`` and
  those qids have to resolve against the string the API keeps.
* **capture assembly** -- when the report carries edits, the fitted document is
  reconstructed (``utils/authored_fit_edits.py``), the metrics are cached under
  *that* string and under nothing else, and the capture's ``html_digest``
  becomes its digest.  There is deliberately no redirect entry under the pre-fit
  string: two entries would be evicted independently by the LRU and reproduce
  the "browser observation missing after eviction" failure class.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Mapping
from typing import Any

from utils.authored_fit_edits import apply_fit_edits
from utils.authored_qid_namespace import ensure_authored_qids
from utils.slide_capture_fit_script import FIT_CLASS, FIT_VERSION, fit_script_fragment

_HTML_TAG_PATTERN = re.compile(r"<html(?:\s[^>]*)?>", re.IGNORECASE)
_CLASS_PATTERN = re.compile(
    r"(\sclass)(?:\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+)))?(?=\s|/?>)",
    re.IGNORECASE,
)
_TAG_END_PATTERN = re.compile(r"\s*/?>\s*$")


def fit_probe_prelude() -> str:
    """The fitter plus the wrapper the probe calls, as plain JavaScript.

    The fragment guarantees ``apply`` never throws, but a fitter defect must
    never make the metrics unavailable either: unavailable metrics block
    publication, whereas an unfitted slide merely reaches the existing gate.
    The wrapper therefore also catches, and reports the failure in the payload.
    """
    return fit_script_fragment() + (
        "  const __codexFitPass = (pass) => {\n"
        "    try { __codexFit.reset(); return __codexFit.apply(pass); }\n"
        "    catch (error) { return {version: '" + FIT_VERSION + "', requested: false, "
        "fit_state: pass, partial: false, "
        "error: String(error && (error.message || error)), cards: [], edits: []}; }\n"
        "  };\n"
    )


#: Bound on the wait for the probe's post-``fonts.ready`` refit: a count and an
#: interval, not a deadline, so it stays well inside the worker's own timeout.
FIT_POLL_ATTEMPTS = 20
FIT_POLL_INTERVAL_MS = 50


def fit_marker_poll_js(metrics_element_id: str, *, indent: str = "") -> str:
    """JavaScript that reads the metrics marker once the fit pass is final.

    The worker's evaluate expression and the probe both hang off
    ``document.fonts.ready``; relying on microtask registration order to decide
    which runs first is fragile, so the reader polls the marker instead.  A
    marker without a ``fit`` report, or one the probe did not fit, is taken
    immediately; so is the marker as it stands after the last poll, in which case
    the report still says ``fit_state: provisional`` and the deck counts it.
    """
    lines = (
        f"let marker = document.getElementById('{metrics_element_id}');",
        "let content = marker ? marker.getAttribute('content') : null;",
        f"for (let poll = 0; poll < {FIT_POLL_ATTEMPTS}; poll += 1) {{",
        "  let fit = null;",
        "  try { const parsed = JSON.parse(decodeURIComponent(content || '')); fit = parsed && parsed.fit; }",
        "  catch (error) { fit = null; }",
        "  if (!fit || fit.requested === false || fit.fit_state === 'final') break;",
        f"  await new Promise(resolve => setTimeout(resolve, {FIT_POLL_INTERVAL_MS}));",
        f"  marker = document.getElementById('{metrics_element_id}');",
        "  content = marker ? marker.getAttribute('content') : null;",
        "}",
        "return content;",
    )
    return "\n".join(indent + line for line in lines)


def html_digest(html: str) -> str:
    return "sha256:" + hashlib.sha256(html.encode("utf-8")).hexdigest()


def require_qid_complete(html: str) -> None:
    """Refuse a fit render whose qids the report could not be resolved against."""
    if ensure_authored_qids(html) != html:
        raise ValueError("fit requires qid-complete HTML")


def _with_fit_class(raw_tag: str) -> str:
    match = _CLASS_PATTERN.search(raw_tag)
    if match is None:
        end = _TAG_END_PATTERN.search(raw_tag)
        insert_at = end.start() if end is not None else len(raw_tag)
        return f'{raw_tag[:insert_at]} class="{FIT_CLASS}"{raw_tag[insert_at:]}'
    existing = next((group for group in match.groups()[1:] if group is not None), "")
    if FIT_CLASS in existing.split():
        return raw_tag
    value = f"{existing} {FIT_CLASS}".strip()
    return raw_tag[: match.start()] + f' class="{value}"' + raw_tag[match.end() :]


def fit_request_html(html: str) -> str:
    """Return the render-request copy of ``html`` carrying the opt-in class.

    Only the request copy is marked.  A document without an ``<html>`` element is
    wrapped the way the isolation admission wraps one, so the class always has an
    element to sit on.
    """
    match = _HTML_TAG_PATTERN.search(html)
    if match is None:
        return (
            f'<!doctype html><html class="{FIT_CLASS}"><body>{html}</body></html>'
        )
    tag = match.group(0)
    return html[: match.start()] + _with_fit_class(tag) + html[match.end() :]


def fit_report_of(metrics: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    """Return the probe's ``fit`` report, or ``None`` when it is absent."""
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
    cache_metrics: Callable[[str, Mapping[str, Any]], Mapping[str, Any]],
) -> tuple[str, str | None, Mapping[str, Any] | None, Mapping[str, Any] | None]:
    """Return ``(html_digest, fitted_html, fit_report, metrics)`` for one capture.

    Without ``fit`` this is the identity.  With ``fit`` and no edits the measured
    DOM *is* the source DOM, so the metrics are keyed by the unmarked source (the
    render itself keyed them by the class-bearing request copy).  With edits the
    fitted string becomes the HTML of record.

    The metrics come back rebound to whichever string this capture is keyed by.
    The renderer measured them from the class-bearing request copy, so their
    ``source_digest`` names a document nothing else ever holds; the evidence
    ledger, the final-metrics readback and the composition gate all compare that
    digest against the HTML they carry, and an unbound mapping makes every
    fitted slide look stale (bench run 45 re-rendered all 24).
    """
    if not fit:
        return digest, None, None, metrics
    report = fit_report_of(metrics)
    edits = _report_edits(report)
    if not edits:
        if metrics is not None:
            metrics = cache_metrics(html, metrics)
        return digest, None, report, metrics
    fitted_html = apply_fit_edits(html, edits)
    if metrics is not None:
        metrics = cache_metrics(fitted_html, metrics)
    return html_digest(fitted_html), fitted_html, report, metrics


__all__ = [
    "FIT_CLASS",
    "FIT_POLL_ATTEMPTS",
    "FIT_POLL_INTERVAL_MS",
    "fit_capture_fields",
    "fit_marker_poll_js",
    "fit_probe_prelude",
    "fit_report_of",
    "fit_request_html",
    "html_digest",
    "require_qid_complete",
]
