"""Source-preserving repair assessment and exact candidate admission evidence.

This is an in-memory boundary. Provisional evidence is deliberately never a
publication receipt; the strict browser verifier remains authoritative.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import asdict, dataclass, field, replace
import hashlib
from html.parser import HTMLParser
import inspect
import json
import re
from typing import Any

from services.authored_structural_repair_compare import (
    _composition_comparison,
    _field,
    _style_report,
    _visibility,
    _visibility_comparison,
    protected_scope_violations,
)
from services.source_content.browser_readback import (
    BrowserSourceReadbackError,
    inspect_browser_source_readback,
)
from services.source_content.source_runs import parse_source_binding_id
from utils.authored_dom_quality import (
    has_dom_quality_regression,
    inspect_authored_dom,
    normalize_browser_metrics,
)
from utils.authored_dom_text_escape import find_text_escapes


REPAIR_GATE_VERSION = "source-structural-repair-v3"
def _digest(value: str | bytes) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


def _plain_copy(value: Any) -> Any:
    """Detach JSON evidence, including recursively frozen product captures.

    Capture bundles freeze mappings with MappingProxyType and arrays as tuples.
    deepcopy cannot pickle those proxies; copy their contents without modifying
    the immutable bundle. Tuple/list arrays have the same canonical JSON digest.
    """
    if isinstance(value, Mapping):
        return {key: _plain_copy(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_copy(item) for item in value]
    return deepcopy(value)


def _json_digest(value: Any) -> str:
    return _digest(json.dumps(_plain_copy(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False))


@dataclass(frozen=True)
class RepairAssessment:
    decision: str
    reasons: tuple[str, ...] = ()
    source_integrity: dict[str, Any] = field(default_factory=dict)
    visibility_by_block: dict[str, Any] = field(default_factory=dict)
    composition_report: dict[str, Any] = field(default_factory=dict)
    style_report: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def admitted(self) -> bool:
        return self.decision == "admitted"


@dataclass
class SlideRepairResult:
    local_index: int
    slide_index: int
    baseline_html_digest: str
    html: str
    png: bytes
    metrics: dict[str, Any]
    assessment: RepairAssessment | None
    changed: bool = False
    cycle_history: list[dict[str, Any]] = field(default_factory=list)

    @property
    def absolute_slide_index(self) -> int:
        return self.slide_index

    @property
    def final_status(self) -> str:
        return "admitted" if self.assessment is not None and self.assessment.admitted else "unresolved"


class _ImmutableDom(HTMLParser):
    """Compare element/text/source/asset identity while permitting style edits."""

    def __init__(self, source: str) -> None:
        super().__init__(convert_charrefs=True)
        self.events: list[Any] = []
        self.qids: list[str] = []
        self.inline_styles: dict[str, str] = {}
        self.feed(source)
        self.close()

    def handle_starttag(self, tag, attrs):
        self.events.append(("start", tag, tuple(sorted((key, value) for key, value in attrs if key != "style"))))
        self.qids.extend(str(value) for key, value in attrs if key == "data-qid")
        attributes = dict(attrs)
        if attributes.get("data-qid"):
            self.inline_styles[str(attributes["data-qid"])] = str(attributes.get("style") or "")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        self.events.append(("end", tag))

    def handle_data(self, data):
        text = re.sub(r"\s+", " ", data).strip()
        if text:
            self.events.append(("text", text))


def source_repair_html_violations(baseline_html: str, candidate_html: str) -> tuple[str, ...]:
    """Cheap pre-render check: structural repair changes inline styles only."""
    try:
        baseline, candidate = _ImmutableDom(baseline_html), _ImmutableDom(candidate_html)
    except (ValueError, TypeError):
        return ("repair_html_invalid",)
    if baseline.events != candidate.events:
        return ("repair_source_dom_changed",)
    if Counter(baseline.qids) != Counter(candidate.qids) or any(count != 1 for count in Counter(candidate.qids).values()):
        return ("repair_qid_identity_invalid",)
    return ()


#: Contract violation recorded for a repair candidate that dropped source
#: text the deck must reproduce verbatim.
SOURCE_VERBATIM_REJECTION = "repair_source_verbatim_binding_failed"


def source_verbatim_rejection(error: BrowserSourceReadbackError) -> dict[str, Any]:
    """A rejecting composition report for a failed strict source check.

    The strict verbatim-binding check is fail-closed by design, and it must
    stay that way for a deck. Inside a repair, though, it has to fail this
    CANDIDATE: raised from a composition validator it escapes the evaluator,
    the repair round and the whole generation, so one unusable candidate ends
    a deck that is otherwise complete. Returning a failing report instead
    leaves the evaluator's original slide selected and the slide unresolved,
    which is exactly what the terminal fallback already flags.

    The caller owns the strict check and catches only
    :class:`BrowserSourceReadbackError`; unrelated failures still propagate.
    """

    return {
        "passed": False,
        "violations": [
            {"category": SOURCE_VERBATIM_REJECTION, "severity": "high"}
        ],
        "telemetryStatus": "unavailable",
        "contract_violations": [SOURCE_VERBATIM_REJECTION],
        "detail": str(getattr(error, "detail", "") or "")[:200],
    }


def make_source_repair_evaluator(
    *, manifest: Any, outline: Any, baseline_html: str, baseline_metrics: dict[str, Any],
    composition_validator: Callable[[str, dict[str, Any]], Any], context: dict[str, Any] | None = None,
):
    """Build a pure callable assessor and explicit, fenced ``advance`` method.

    The composition callback returns ``passed`` and ``violations`` and may add
    ``contract_violations`` for hard non-geometric failures. The caller must
    perform patch-scope validation before submitting a candidate here. Evaluating
    never selects a candidate. After choosing a winner call
    ``evaluate.advance(assessment.evidence['selected_html_digest'], assessment,
    html, metrics)``. ``evaluate.selected_digest`` identifies the current base.
    """
    manifest = _plain_copy(manifest)
    outline = _plain_copy(outline)
    baseline_metrics = _plain_copy(baseline_metrics)
    context = _plain_copy(context or {})
    baseline_digest = _digest(baseline_html)
    original_readback = inspect_browser_source_readback(manifest=manifest, outline=outline, browser_metrics=[baseline_metrics], htmls=[baseline_html])
    original_visibility = _visibility(baseline_metrics, original_readback.visibility_failures)
    original_dom = inspect_authored_dom(baseline_html, baseline_metrics, strict_structural_off_canvas=True)
    selected = {
        "html": baseline_html, "metrics": baseline_metrics, "digest": baseline_digest,
        "metrics_digest": _json_digest(baseline_metrics), "visibility": original_visibility,
        "dom": original_dom, "context": context,
    }
    cache: dict[tuple, RepairAssessment] = {}
    compositions: dict[tuple[str, str], dict[str, Any]] = {}

    async def composition_for(html, metrics):
        key = (_digest(html), _json_digest(metrics))
        if key not in compositions:
            # A validator does not get writable aliases to caller state.
            report = composition_validator(html, deepcopy(metrics))
            if inspect.isawaitable(report):
                report = await report
            if not isinstance(report, Mapping) or not isinstance(report.get("passed"), bool) or not isinstance(report.get("violations", []), (list, tuple)):
                report = {"passed": False, "violations": [], "contract_violations": ["repair_composition_report_invalid"]}
            compositions[key] = _plain_copy(report)
        return deepcopy(compositions[key])

    async def evaluate(slide_index: int, html: str, metrics: dict[str, Any]) -> RepairAssessment:
        state = selected  # Snapshot survives awaits and a concurrent advance.
        metrics = _plain_copy(metrics)
        digest = _digest(html)
        metric_digest = _json_digest(metrics)
        source_context_digest = _json_digest({"manifest": manifest, "outline": outline, "context": state["context"]})
        key = (slide_index, baseline_digest, _json_digest(baseline_metrics), state["digest"], state["metrics_digest"], digest, metric_digest, source_context_digest, REPAIR_GATE_VERSION)
        if key in cache:
            return deepcopy(cache[key])
        reasons = list(source_repair_html_violations(baseline_html, html))
        readback = inspect_browser_source_readback(manifest=manifest, outline=outline, browser_metrics=[metrics], htmls=[html])
        source_report = readback.as_dict()
        if readback.missing_block_ids or readback.order_violations or readback.integrity_failures:
            reasons.append("repair_source_integrity_failed")
        if readback.telemetry_failures or readback.diagnostic_error:
            reasons.append("repair_source_telemetry_incomplete")
        composition = await composition_for(html, metrics)
        original_composition = await composition_for(baseline_html, baseline_metrics)
        selected_composition = await composition_for(state["html"], state["metrics"])
        if composition.get("contract_violations"):
            reasons.append("repair_composition_contract_failed")
        style = _style_report(baseline_metrics, metrics, {"protected_qids": ()})
        style["violations"].extend(protected_scope_violations(
            state["html"], html, state["metrics"], metrics, state["context"].get("protected_qids", ()),
        ))
        style["passed"] = not style["violations"]
        reasons.extend(style["violations"])
        dom = inspect_authored_dom(html, metrics, strict_structural_off_canvas=True)
        if has_dom_quality_regression(original_dom, dom) or has_dom_quality_regression(state["dom"], dom):
            reasons.append("repair_dom_quality_regression")
        for reference in (baseline_metrics, state["metrics"]):
            if any(metrics.get(key) != reference.get(key) for key in ("width", "height")):
                reasons.append("repair_measurements_incompatible")
            for snake, camel in (("probe_version", "probeVersion"), ("measurement_version", "measurementVersion")):
                if _field(reference, snake, camel) != _field(metrics, snake, camel):
                    reasons.append("repair_measurements_incompatible")
        visibility = _visibility(metrics, readback.visibility_failures)
        original_ok, _ = _visibility_comparison(original_visibility, visibility)
        selected_ok, visibility_improved = _visibility_comparison(state["visibility"], visibility)
        if not (original_ok and selected_ok):
            missing = any(value is None for observations in (original_visibility, state["visibility"], visibility) for values in observations.values() for value in values.values())
            reasons.append("repair_visibility_measurement_incomplete" if missing else "repair_visibility_not_monotonic")
        original_composition_ok, _ = _composition_comparison(original_composition, composition)
        selected_composition_ok, composition_improved = _composition_comparison(selected_composition, composition)
        if not (original_composition_ok and selected_composition_ok):
            reasons.append("repair_composition_not_monotonic")
        decision = "reject"
        if not reasons and readback.passed and composition["passed"]:
            decision = "admitted"
        elif not reasons:
            if visibility_improved or composition_improved:
                decision = "provisional"
            else:
                reasons.append("repair_no_measured_improvement")
        evidence = {
            "gate_version": REPAIR_GATE_VERSION, "absolute_slide_index": slide_index,
            "baseline_html_digest": baseline_digest, "html_digest": digest,
            "selected_html_digest": state["digest"], "selected_metrics_digest": state["metrics_digest"],
            "metrics_digest": metric_digest, "source_context_digest": source_context_digest,
            "render_digest": _field(metrics, "render_digest", "renderDigest"),
            "viewport": {"width": metrics.get("width"), "height": metrics.get("height")},
            "fonts_ready": _field(metrics, "fonts_ready", "fontsReady"),
        }
        assessment = RepairAssessment(decision, tuple(dict.fromkeys(reasons)), source_report, visibility, composition, style, evidence)
        cache[key] = deepcopy(assessment)
        return assessment

    def advance(expected_selected_digest: str, assessment: RepairAssessment, html: str, metrics: dict[str, Any], *, context: dict[str, Any] | None = None) -> str:
        nonlocal selected
        if expected_selected_digest != selected["digest"] or assessment.evidence.get("selected_html_digest") != expected_selected_digest or assessment.evidence.get("selected_metrics_digest") != selected["metrics_digest"]:
            raise ValueError("repair_selected_state_stale")
        if assessment.decision not in {"provisional", "admitted"} or assessment.evidence.get("html_digest") != _digest(html) or assessment.evidence.get("metrics_digest") != _json_digest(metrics):
            raise ValueError("repair_selected_candidate_invalid")
        current_context_digest = _json_digest({"manifest": manifest, "outline": outline, "context": selected["context"]})
        cache_key = (assessment.evidence.get("absolute_slide_index"), baseline_digest, _json_digest(baseline_metrics), selected["digest"], selected["metrics_digest"], _digest(html), _json_digest(metrics), current_context_digest, REPAIR_GATE_VERSION)
        recorded = cache.get(cache_key)
        if recorded is None or _assessment_digest(recorded) != _assessment_digest(assessment):
            raise ValueError("repair_selected_assessment_unknown")
        selected = {
            "html": html, "metrics": _plain_copy(metrics), "digest": _digest(html),
            "metrics_digest": _json_digest(metrics), "visibility": deepcopy(assessment.visibility_by_block),
            "dom": inspect_authored_dom(html, metrics, strict_structural_off_canvas=True),
            "context": _plain_copy(selected["context"] if context is None else context),
        }
        evaluate.selected_digest = selected["digest"]
        return selected["digest"]

    evaluate.advance = advance
    evaluate.selected_digest = baseline_digest
    return evaluate


def _assessment_digest(assessment: RepairAssessment) -> str:
    return _json_digest({
        "decision": assessment.decision, "source_integrity": assessment.source_integrity,
        "visibility_by_block": assessment.visibility_by_block,
        "composition_report": assessment.composition_report, "style_report": assessment.style_report,
    })


def bind_repair_candidate(assessment: RepairAssessment, html: str, png: bytes, metrics: dict[str, Any]) -> RepairAssessment:
    """Bind the selected PNG to the exact already-assessed source and metrics."""
    evidence = dict(assessment.evidence)
    if evidence.get("html_digest") != _digest(html) or evidence.get("metrics_digest") != _json_digest(metrics) or not png:
        raise ValueError("repair_candidate_evidence_mismatch")
    evidence["png_digest"] = _digest(png)
    evidence["assessment_digest"] = _assessment_digest(assessment)
    return replace(assessment, evidence=evidence)


def validate_repair_result(result: SlideRepairResult, expected_slide_index: int | None = None) -> None:
    """Fail closed at checkpoint storage; never re-render or mint admission."""
    assessment = result.assessment
    if assessment is None or not assessment.admitted:
        raise ValueError("repair_candidate_not_admitted")
    evidence = assessment.evidence
    if (
        evidence.get("gate_version") != REPAIR_GATE_VERSION
        or evidence.get("absolute_slide_index") != result.slide_index
        or (expected_slide_index is not None and expected_slide_index != result.slide_index)
        or evidence.get("baseline_html_digest") != result.baseline_html_digest
        or evidence.get("html_digest") != _digest(result.html)
        or evidence.get("metrics_digest") != _json_digest(result.metrics)
        or not result.png or evidence.get("png_digest") != _digest(result.png)
        or evidence.get("assessment_digest") != _assessment_digest(assessment)
        or not evidence.get("source_context_digest")
        or assessment.source_integrity.get("verified") is not True
        or assessment.composition_report.get("passed") is not True
        or assessment.style_report.get("passed") is not True
    ):
        raise ValueError("repair_admission_evidence_invalid")


STRUCTURAL_CHECKPOINT_KEY = "authored_structural_repair"


def structural_checkpoint_metrics(result: SlideRepairResult) -> dict[str, Any]:
    """Attach admission separately from measured metrics to avoid a hash cycle."""
    validate_repair_result(result, expected_slide_index=result.slide_index)
    metrics = _plain_copy(result.metrics)
    metrics[STRUCTURAL_CHECKPOINT_KEY] = {
        "schemaVersion": 1,
        "localIndex": result.local_index,
        "slideIndex": result.slide_index,
        "baselineHtmlDigest": result.baseline_html_digest,
        "changed": result.changed,
        "assessment": asdict(result.assessment),
    }
    # The existing artifact writer binds this complete object to its bundle.
    return metrics


def validate_structural_checkpoint(
    html: str, png: bytes, metrics: Mapping[str, Any], *,
    expected_slide_index: int, baseline_html_digest: str,
) -> bool:
    """Reuse only approved bytes bound to the same freshly rebuilt original."""
    try:
        measured = _plain_copy(metrics)
        receipt = measured.pop(STRUCTURAL_CHECKPOINT_KEY, None)
        if not isinstance(receipt, Mapping) or receipt.get("schemaVersion") != 1:
            return False
        if receipt.get("baselineHtmlDigest") != baseline_html_digest:
            return False
        assessment = RepairAssessment(**receipt["assessment"])
        result = SlideRepairResult(
            local_index=receipt["localIndex"], slide_index=receipt["slideIndex"],
            baseline_html_digest=receipt["baselineHtmlDigest"],
            html=html, png=png, metrics=measured, assessment=assessment,
            changed=receipt.get("changed") is True,
        )
        validate_repair_result(result, expected_slide_index=expected_slide_index)
        return True
    except (KeyError, TypeError, ValueError, AttributeError):
        return False
