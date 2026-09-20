"""Per-run and per-stage metric extraction for summarize_series.py.

Split out of summarize_series.py to stay under the repo's file-size guidance
(docs/file-size-policy.md, aim <=350 lines); summarize_series.py is the CLI
and rendering facade over this module. See that script's module docstring and
RUNBOOK.md section 6a for what each field means and why two fields are always
"unavailable".
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

try:
    # Reuse the exact same log-line parsing rule the shadow report already
    # uses (scripts/authored_admission_shadow_report.py), instead of
    # re-implementing it. Only the numeric mismatch rollup below is new; the
    # full per-producer/verdict markdown breakdown is that script's own job --
    # run it directly against a stage's worker log for that.
    from authored_admission_shadow_report import aggregate as shadow_aggregate
except ImportError:
    shadow_aggregate = None

UNAVAILABLE = "unavailable"
REVIEW_REQUIRED = "review_required"
REPAIR_NOT_ADMITTED = "repair_not_admitted"
_ORIGINAL_PRESERVED_OUTCOMES = {"verbatim_flag_only", "not_attempted"}
_REPAIR_LOOP_LINE = re.compile(
    r"\[authored-repair-loop\] slides=(\d+) admitted=(\d+) model_calls=(\d+) budget=(\d+)"
)
_RECOVERY_TRIGGER = re.compile(r"^provider:slide:\d+:recovery$")


def load_json(path: Path) -> Any:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def dig(d: Any, *keys: str, default: Any = None) -> Any:
    cur = d
    for key in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
    return cur if cur is not None else default


def parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def classify_flag(entry: dict) -> tuple[str, bool]:
    """(classification, was_derived). Mirrors
    services/authored_flag_classification.needs_review, replicated read-only
    because pre-T2 receipts (and any receipt read without the module) do not
    carry the "classification" field. Caveat: the residual-failures partition
    is internal generation state not present on a persisted flaggedSlides
    entry, so the derived fallback can slightly over-count repair_not_admitted
    relative to a receipt that carries its own classification natively --
    was_derived=True flags every case where that caveat applies.
    """
    native = entry.get("classification")
    if native in (REVIEW_REQUIRED, REPAIR_NOT_ADMITTED):
        return native, False
    reasons = entry.get("reasons")
    if isinstance(reasons, list):
        reasons = [r for r in reasons if isinstance(r, str)]
    else:
        reason = entry.get("reason")
        reasons = [reason] if isinstance(reason, str) else []
    substitute = any(r.startswith("author:") for r in reasons)
    if entry.get("remakeAttempted") is True:
        substitute = True
    outcome = entry.get("remakeOutcome")
    if isinstance(outcome, str) and outcome not in _ORIGINAL_PRESERVED_OUTCOMES:
        substitute = True
    if substitute or entry.get("sourceVisibilityBlockIds") or "source_visibility" in reasons:
        return REVIEW_REQUIRED, True
    return REPAIR_NOT_ADMITTED, True


def run_metrics(run_dir: Path) -> dict[str, Any]:
    """Everything this script can learn about one run.ps1 output directory."""

    m: dict[str, Any] = {"runDirectory": str(run_dir), "caveats": []}
    final = load_json(run_dir / "06-final-status.json")
    download = load_json(run_dir / "07-download-evidence.json")
    analysis = load_json(run_dir / "03-analysis.json")
    audit = load_json(run_dir / "audit.json")
    usage = load_json(run_dir / "provider-usage.json")

    m["sourceUnits"] = dig(analysis, "summary", "sourceUnits")
    m["textBlocks"] = dig(analysis, "summary", "textBlocks")

    status = dig(final, "status")
    presentation_id = dig(final, "data", "presentation_id")
    downloaded_sha = dig(download, "downloadedSha256")
    audit_verdict = dig(audit, "verdict")
    if audit_verdict is not None:
        m["published"] = audit_verdict == "passed"
        m["publishSource"] = "audit.json"
    else:
        m["published"] = bool(status == "completed" and presentation_id and downloaded_sha)
        m["publishSource"] = "06-final-status.json+07-download-evidence.json (no audit.json)"
        m["caveats"].append(
            "no audit.json for this run; publish/deck-death verdict is a heuristic, "
            "not audit-full-run.ps1's release-gate checks"
        )
    m["status"] = status

    receipt = dig(final, "data", "quality_gate_receipt") or {}
    page_summary = receipt.get("pageSummary") or {}
    m["pageSummaryTotal"] = page_summary.get("total")
    flagged = receipt.get("flaggedSlides")
    flagged = flagged if isinstance(flagged, list) else []
    m["flaggedCount"] = len(flagged)
    review_required = 0
    repair_not_admitted = 0
    derived_any = False
    for entry in flagged:
        if not isinstance(entry, dict):
            continue
        classification, derived = classify_flag(entry)
        derived_any = derived_any or derived
        if classification == REVIEW_REQUIRED:
            review_required += 1
        else:
            repair_not_admitted += 1
    m["reviewRequiredCount"] = review_required
    m["repairNotAdmittedCount"] = repair_not_admitted
    if derived_any:
        m["caveats"].append(
            "classification split derived offline (receipt predates the "
            "classification field or lacked it); see classify_flag() caveat"
        )

    capture = receipt.get("captureOwnership")
    if isinstance(capture, dict):
        m["captureCount"] = capture.get("captureCount")
        m["captureReuseCount"] = capture.get("captureReuseCount")
        slides = capture.get("slides")
        if isinstance(slides, list) and slides:
            m["captureCountPerSlideAvg"] = round(
                sum(int(s.get("captureCount") or 0) for s in slides) / len(slides), 2
            )
    else:
        m["captureCount"] = None
        m["captureReuseCount"] = None
        m["captureCountPerSlideAvg"] = None
        m["caveats"].append(
            "quality_gate_receipt.captureOwnership is null (no active admission "
            "session on this run -- pre-T2 run, or the gate was not exercised)"
        )

    shadow = receipt.get("admissionShadow")
    if isinstance(shadow, dict):
        rows = shadow.get("rows") or []
        mismatch = sum(
            int(r.get("count") or 0) for r in rows
            if isinstance(r, dict) and r.get("existingVerdict") != r.get("gateVerdict")
        )
        m["shadowRowCount"] = shadow.get("count")
        m["shadowDropped"] = shadow.get("dropped")
        m["shadowMismatchCount"] = mismatch
        m["gateMode"] = dig(shadow, "rolloutProfile", "gateMode")
        m["policyRevision"] = shadow.get("policyRevision")
    else:
        m["shadowRowCount"] = None
        m["shadowMismatchCount"] = None
        m["caveats"].append("quality_gate_receipt.admissionShadow is null (no active admission session)")

    t1_triggers = dig(receipt, "qualityReceiptV3", "t1Triggers")
    # Generation reserves ordinal 2 for every VQA-eligible slide, even when
    # the reviser makes no call for it. Keep this legacy gate-derived proxy,
    # but do not interpret it as physical requests or observed repair work.
    m["recoveryTriggerProxyBasis"] = "reserved_provider_budget"
    m["recoveryPhysicalRequestCount"] = None
    m["caveats"].append(
        "physical recovery requests are unavailable: recoveryTriggerProxy counts "
        "budget-based V3 slide triggers, including reserved VQA eligibility, "
        "not observed calls or quality defects; the separate provider-usage "
        "requestCountTotal does not attribute requests to slide recovery"
    )
    if isinstance(t1_triggers, list):
        m["recoveryTriggerProxy"] = sum(1 for t in t1_triggers if isinstance(t, str) and _RECOVERY_TRIGGER.match(t))
    else:
        m["recoveryTriggerProxy"] = None

    if isinstance(usage, dict):
        summary = usage.get("summary") or {}
        m["providerRequestCount"] = summary.get("requestCountTotal")
        m["providerOutputTokens"] = summary.get("outputTokensTotal")
        m["providerRetryClassCounts"] = summary.get("retryClassCounts")
    else:
        m["providerRequestCount"] = None
        m["providerOutputTokens"] = None
        m["caveats"].append(
            "no provider-usage.json sidecar for this run; run "
            "run_authored_live_series_provider_usage.ps1 to get provider calls/output tokens"
        )

    created = parse_dt(dig(final, "created_at"))
    updated = parse_dt(dig(final, "updated_at"))
    if created and updated:
        m["wallSecondsProxy"] = round((updated - created).total_seconds(), 1)
        m["wallSecondsSource"] = "06-final-status.json created_at/updated_at (task lifetime proxy)"
    else:
        m["wallSecondsProxy"] = None

    return m


def apply_series_summary(stage_dir: Path, run_metrics_by_dir: dict[str, dict]) -> None:
    """Overlay series.ps1's own per-run wall time / audit verdict when present --
    it is measured directly around the call, not derived from timestamps."""

    summary = load_json(stage_dir / "series-summary.json")
    if not isinstance(summary, list):
        return
    for entry in summary:
        if not isinstance(entry, dict):
            continue
        run_dir = str(Path(entry.get("runDirectory", "")))
        target = run_metrics_by_dir.get(run_dir)
        if target is None:
            continue
        if entry.get("wallSeconds") is not None:
            target["wallSecondsProxy"] = entry["wallSeconds"]
            target["wallSecondsSource"] = "series.ps1 (measured around run.ps1 + audit-full-run.ps1)"
        if entry.get("auditVerdict") is not None:
            target["published"] = entry.get("auditVerdict") == "passed"
            target["publishSource"] = "series-summary.json"


def repair_loop_from_log(worker_log: Path | None) -> dict[str, Any]:
    if worker_log is None or not worker_log.is_file():
        return {"available": False, "reason": "no worker log provided for this stage"}
    calls = admitted = slides = budget = lines = 0
    with worker_log.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            match = _REPAIR_LOOP_LINE.search(line)
            if not match:
                continue
            lines += 1
            s, a, c, b = (int(x) for x in match.groups())
            slides += s
            admitted += a
            calls += c
            budget += b
    return {
        "available": lines > 0,
        "matchedLines": lines,
        "slidesTotal": slides,
        "admittedTotal": admitted,
        "modelCallsTotal": calls,
        "budgetTotal": budget,
        "reason": None if lines else "worker log had no [authored-repair-loop] lines in this window",
    }


def shadow_parity_from_log(worker_log: Path | None) -> dict[str, Any]:
    if worker_log is None or not worker_log.is_file() or shadow_aggregate is None:
        return {"available": False}
    with worker_log.open(encoding="utf-8", errors="replace") as handle:
        counts = shadow_aggregate(handle)
    total = sum(counts.values())
    mismatched = sum(
        count for (producer, existing, gate), count in counts.items() if existing != gate
    )
    return {"available": True, "totalRows": total, "mismatchedRows": mismatched, "rowsByKey": dict(counts)}


def build_stage(name: str, run_dirs: list[Path], worker_log: Path | None, is_dry_run: bool) -> dict[str, Any]:
    runs = [run_metrics(d) for d in run_dirs]
    if not is_dry_run and run_dirs:
        apply_series_summary(run_dirs[0].parent, {r["runDirectory"]: r for r in runs})

    n = len(runs)
    published = sum(1 for r in runs if r.get("published"))
    flagged_total = sum(r.get("flaggedCount") or 0 for r in runs)
    review_total = sum(r.get("reviewRequiredCount") or 0 for r in runs)
    repair_total = sum(r.get("repairNotAdmittedCount") or 0 for r in runs)

    capture_counts = [r["captureCount"] for r in runs if r.get("captureCount") is not None]
    reuse_counts = [r["captureReuseCount"] for r in runs if r.get("captureReuseCount") is not None]
    per_slide_avgs = [r["captureCountPerSlideAvg"] for r in runs if r.get("captureCountPerSlideAvg") is not None]

    shadow_rows = [r["shadowRowCount"] for r in runs if r.get("shadowRowCount") is not None]
    shadow_mismatches = [r["shadowMismatchCount"] for r in runs if r.get("shadowMismatchCount") is not None]

    provider_requests = [r["providerRequestCount"] for r in runs if r.get("providerRequestCount") is not None]
    provider_tokens = [r["providerOutputTokens"] for r in runs if r.get("providerOutputTokens") is not None]

    wall_times = [r["wallSecondsProxy"] for r in runs if r.get("wallSecondsProxy") is not None]

    return {
        "name": name,
        "runCount": n,
        "runs": runs,
        "publishedCount": published,
        "publishRate": round(published / n, 3) if n else None,
        "flaggedTotal": flagged_total,
        "flaggedAvgPerRun": round(flagged_total / n, 2) if n else None,
        "reviewRequiredTotal": review_total,
        "repairNotAdmittedTotal": repair_total,
        "captureCountTotal": sum(capture_counts) if capture_counts else None,
        "captureReuseCountTotal": sum(reuse_counts) if reuse_counts else None,
        "captureCountPerSlideAvg": (
            round(sum(per_slide_avgs) / len(per_slide_avgs), 2) if per_slide_avgs else None
        ),
        "captureDataRuns": f"{len(capture_counts)}/{n}",
        "shadowRowTotal": sum(shadow_rows) if shadow_rows else None,
        "shadowMismatchTotal": sum(shadow_mismatches) if shadow_mismatches else None,
        "shadowDataRuns": f"{len(shadow_rows)}/{n}",
        "shadowLog": shadow_parity_from_log(worker_log),
        "repairLoop": repair_loop_from_log(worker_log),
        "visionReviewSlidesReviewed": UNAVAILABLE,
        "providerRequestTotal": sum(provider_requests) if provider_requests else None,
        "providerOutputTokensTotal": sum(provider_tokens) if provider_tokens else None,
        "providerDataRuns": f"{len(provider_requests)}/{n}",
        "wallSecondsAvg": round(sum(wall_times) / len(wall_times), 1) if wall_times else None,
        "wallDataRuns": f"{len(wall_times)}/{n}",
    }


def discover_run_dirs(stage_dir: Path) -> list[Path]:
    return sorted(
        (p for p in stage_dir.glob("run-*") if p.is_dir() and (p / "06-final-status.json").is_file()),
        key=lambda p: p.name,
    )
