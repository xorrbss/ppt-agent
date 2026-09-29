"""Python boundary for the authored browser fitters.

Two fitters share this boundary and the probe injects exactly one of them:

* **local fit** (the live fitter) -- ``fit_probe_prelude(policy)`` splices the
  local-fit fragment of ``utils/slide_capture_fit_script.py``.  The policy rides
  on the render request as a comment marker (``utils/slide_capture_local_fit.py``).
* **in-render card fit** (the alternative, off unless
  ``AUTHORED_IN_RENDER_FIT_ENABLED``) -- ``in_render_fit_probe_prelude`` splices
  ``utils/slide_capture_fit_in_render_script.py``.  The opt-in signal is the
  ``codex-fit-v1`` class on the request copy's ``<html>`` element
  (``fit_request_html``); ``class`` is a global attribute in
  ``utils/renderer_markup_policy.py``, so the marker passes renderer admission and
  never reaches the persisted document.  With ``fit=True`` the caller must hand
  over qid-complete HTML (``require_qid_complete``), because the report addresses
  elements by ``data-qid``.

Capture assembly is shared: when a report carries edits, the fitted document is
reconstructed (``utils/authored_fit_edits.py``), the metrics are cached under
*that* string and under nothing else, and the capture's ``html_digest`` becomes
its digest.  There is deliberately no redirect entry under the pre-fit string:
two entries would be evicted independently by the LRU and reproduce the
"browser observation missing after eviction" failure class.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from typing import Any

from utils.authored_fit_edits import apply_fit_edits
from utils.authored_fragmentation_gate import fit_font_floor_px
from utils.authored_fragmentation_gate import page_number_max_font_px as resolved_page_number_max_font_px
from utils.authored_qid_namespace import ensure_authored_qids
from utils.slide_capture_fit_in_render_script import FIT_CLASS
from utils.slide_capture_fit_in_render_script import FIT_VERSION as IN_RENDER_FIT_VERSION
from utils.slide_capture_fit_in_render_script import fit_script_fragment as in_render_fit_script_fragment
from utils.slide_capture_fit_script import FIT_VERSION, fit_script_fragment
from utils.hashing import sha256_prefixed

_HTML_TAG_PATTERN = re.compile(r"<html(?:\s[^>]*)?>", re.IGNORECASE)
_CLASS_PATTERN = re.compile(
    r"(\sclass)(?:\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+)))?(?=\s|/?>)",
    re.IGNORECASE,
)
_TAG_END_PATTERN = re.compile(r"\s*/?>\s*$")


def html_digest(html: str) -> str:
    return sha256_prefixed(html.encode("utf-8"))


def require_qid_complete(html: str) -> None:
    """Refuse a fit render whose qids the report could not be resolved against."""
    if ensure_authored_qids(html) != html:
        raise ValueError("fit requires qid-complete HTML")


def fit_probe_prelude(policy: Mapping[str, Any] | None = None) -> str:
    """Return the local fitter and its no-throw ``__codexFitPass`` probe API."""

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


# --- the alternative in-render card fit ---------------------------------------


def in_render_fit_probe_prelude(
    *, font_floor_px: float | None = None, page_number_max_font_px: float | None = None
) -> str:
    """The in-render card fitter plus the wrapper the probe calls, as plain JavaScript.

    The fragment guarantees ``apply`` never throws, but a fitter defect must
    never make the metrics unavailable either: unavailable metrics block
    publication, whereas an unfitted slide merely reaches the existing gate.
    The wrapper therefore also catches, and reports the failure in the payload.

    ``font_floor_px`` is the shrink floor; ``None`` resolves it from the
    fragmentation gate knobs (the gate floor when on, 12px otherwise).
    """
    floor = fit_font_floor_px() if font_floor_px is None else font_floor_px
    page_max = resolved_page_number_max_font_px() if page_number_max_font_px is None else page_number_max_font_px
    return in_render_fit_script_fragment(font_floor_px=floor, page_max_px=page_max) + (
        "  const __codexFitPass = (pass) => {\n"
        "    try { __codexFit.reset(); return __codexFit.apply(pass); }\n"
        "    catch (error) { return {version: '" + IN_RENDER_FIT_VERSION + "', requested: false, "
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
    which runs first is fragile, so the reader first asks the probe to observe
    the current state (``__codexObserveFinal``, installed by the observation
    script of ``utils/slide_capture_local_fit.py``) and then polls the marker.
    A marker without a ``fit`` report, or one the probe did not fit, is taken
    immediately; so is the marker as it stands after the last poll, in which case
    the report still says ``fit_state: provisional`` and the deck counts it.
    """
    lines = (
        "if (typeof window.__codexObserveFinal === 'function') window.__codexObserveFinal();",
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
    """Return the render-request copy of ``html`` carrying the in-render opt-in class.

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


def in_render_fit_requested(html: str) -> bool:
    """Whether ``html`` is a ``fit_request_html`` copy, i.e. selects the in-render fitter."""
    match = _HTML_TAG_PATTERN.search(html)
    if match is None:
        return False
    class_match = _CLASS_PATTERN.search(match.group(0))
    if class_match is None:
        return False
    existing = next((group for group in class_match.groups()[1:] if group is not None), "")
    return FIT_CLASS in existing.split()


NO_FIT_PRELUDE = "const __codexFitPass = () => null;"


def probe_fit_prelude(html: str) -> str:
    """Select the one fitter the probe injects for the render request ``html``.

    A local-fit policy marker selects the live local fitter; otherwise a
    ``fit_request_html`` copy selects the in-render card fit, whose floor rides in
    the probe so a worker fits to the floor the API resolved; otherwise no fitter.
    """
    from utils.authored_qid_namespace import declared_authored_qids
    from utils.slide_capture_local_fit import policy_from_request

    policy = policy_from_request(html)
    if policy:
        # The compatibility renderer adds QIDs only to its disposable measurement
        # document.  Restrict proposals to identifiers already present in source,
        # so every accepted edit remains byte-replayable without minting identities.
        policy = {**policy, "_replayable_qids": list(declared_authored_qids(html))}
        return fit_probe_prelude(policy=policy)
    if in_render_fit_requested(html):
        return in_render_fit_probe_prelude(font_floor_px=fit_font_floor_px())
    return NO_FIT_PRELUDE


# --- capture assembly (both fitters) --------------------------------------------


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
    cache_metrics: Callable[[str, Mapping[str, Any]], Mapping[str, Any] | None],
) -> tuple[str, str | None, Mapping[str, Any] | None, Mapping[str, Any] | None]:
    """Return ``(html_digest, fitted_html, fit_report, metrics)`` for one capture.

    Only the in-render path calls this (local fit assembles its capture in
    ``utils/slide_capture_local_fit.py``).  Without ``fit`` this is the identity.
    With ``fit`` and no edits the measured DOM *is* the source DOM, so the
    metrics are keyed by the unmarked source (the render itself keyed them by
    the class-bearing request copy).  With edits the fitted string becomes the
    HTML of record.

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
            metrics = cache_metrics(html, metrics) or metrics
        return digest, None, report, metrics
    fitted_html = apply_fit_edits(html, edits)
    if metrics is not None:
        metrics = cache_metrics(fitted_html, metrics) or metrics
    return html_digest(fitted_html), fitted_html, report, metrics


__all__ = [
    "FIT_CLASS",
    "FIT_POLL_ATTEMPTS",
    "FIT_POLL_INTERVAL_MS",
    "NO_FIT_PRELUDE",
    "fit_capture_fields",
    "fit_font_floor_px",
    "fit_marker_poll_js",
    "fit_probe_prelude",
    "fit_report_of",
    "fit_request_html",
    "html_digest",
    "in_render_fit_probe_prelude",
    "in_render_fit_requested",
    "probe_fit_prelude",
    "require_qid_complete",
]
