from __future__ import annotations

import argparse
import inspect
import hashlib
import json
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, TypeVar


T = TypeVar("T")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _measure(name: str, action: Callable[[], T], phases: list[dict[str, Any]]) -> T:
    start_ns = time.perf_counter_ns()
    value = action()
    end_ns = time.perf_counter_ns()
    phases.append(
        {
            "name": name,
            "startNs": start_ns,
            "endNs": end_ns,
            "durationMs": round((end_ns - start_ns) / 1_000_000, 3),
        }
    )
    return value


def run_offline_comparison(initial_html: Path, final_html: Path, output_dir: Path) -> dict[str, Any]:
    initial_html = initial_html.resolve(strict=True)
    final_html = final_html.resolve(strict=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    total_start_ns = time.perf_counter_ns()
    phases: list[dict[str, Any]] = []
    initial_bytes = _measure("readInitialHtml", initial_html.read_bytes, phases)
    final_bytes = _measure("readFinalHtml", final_html.read_bytes, phases)

    initial_snapshot = output_dir / "initial.html"
    final_snapshot = output_dir / "final.html"
    _measure("preserveInitialHtml", lambda: initial_snapshot.write_bytes(initial_bytes), phases)
    _measure("preserveFinalHtml", lambda: final_snapshot.write_bytes(final_bytes), phases)

    comparison = _measure(
        "compareHtml",
        lambda: {
            "sameBytes": initial_bytes == final_bytes,
            "initialSizeBytes": len(initial_bytes),
            "finalSizeBytes": len(final_bytes),
            "initialSha256": _sha256(initial_bytes),
            "finalSha256": _sha256(final_bytes),
        },
        phases,
    )
    total_end_ns = time.perf_counter_ns()
    phase_sum_ms = round(sum(phase["durationMs"] for phase in phases), 3)
    total_ms = round((total_end_ns - total_start_ns) / 1_000_000, 3)
    receipt = {
        "schemaVersion": "sol-common-repair-offline-comparison/v1",
        "measurementKind": "fileComparisonOnly",
        "completionLatencyAvailable": False,
        "providerCalls": 0,
        "inputs": {
            "initialHtml": str(initial_html),
            "finalHtml": str(final_html),
        },
        "snapshots": {
            "initialHtml": str(initial_snapshot.resolve()),
            "finalHtml": str(final_snapshot.resolve()),
        },
        "comparison": comparison,
        "timing": {
            "clock": "time.perf_counter_ns",
            "boundary": "input reads through completed comparison; receipt persistence excluded",
            "totalStartNs": total_start_ns,
            "totalEndNs": total_end_ns,
            "totalMs": total_ms,
            "phaseDurationSumMs": phase_sum_ms,
            "unattributedWithinBoundaryMs": round(total_ms - phase_sum_ms, 3),
            "phases": phases,
        },
    }
    receipt_path = output_dir / "comparison.json"
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return receipt


async def _resolve(value: T | Awaitable[T]) -> T:
    if inspect.isawaitable(value):
        return await value
    return value


async def _measure_async(
    name: str,
    action: Callable[[], T | Awaitable[T]],
    phases: list[dict[str, Any]],
) -> T:
    start_ns = time.perf_counter_ns()
    try:
        value = await _resolve(action())
    except BaseException as error:
        end_ns = time.perf_counter_ns()
        phases.append(
            {
                "name": name,
                "status": "failed",
                "errorType": type(error).__name__,
                "startNs": start_ns,
                "endNs": end_ns,
                "durationMs": round((end_ns - start_ns) / 1_000_000, 3),
            }
        )
        raise
    end_ns = time.perf_counter_ns()
    phases.append(
        {
            "name": name,
            "status": "completed",
            "startNs": start_ns,
            "endNs": end_ns,
            "durationMs": round((end_ns - start_ns) / 1_000_000, 3),
        }
    )
    return value


def _pipeline_receipt(
    *,
    status: str,
    total_start_ns: int,
    total_end_ns: int,
    phases: list[dict[str, Any]],
    html: dict[str, Any] | None = None,
    error: BaseException | None = None,
) -> dict[str, Any]:
    phase_sum_ms = round(sum(phase["durationMs"] for phase in phases), 3)
    elapsed_ms = round((total_end_ns - total_start_ns) / 1_000_000, 3)
    receipt: dict[str, Any] = {
        "schemaVersion": "sol-common-repair-measured-pipeline/v1",
        "status": status,
        "measurementKind": "offlineRepairCaptureInspection",
        "completionLatencyAvailable": status == "completed",
        "providerCalls": 0,
        "providerCallsBasis": "caller contract: repair, capture, and inspection callables are offline",
        "timing": {
            "clock": "time.perf_counter_ns",
            "boundary": "repair start through completed inspection or terminal failure",
            "totalStartNs": total_start_ns,
            "totalEndNs": total_end_ns,
            "elapsedMs": elapsed_ms,
            "completionLatencyMs": elapsed_ms if status == "completed" else None,
            "failedElapsedMs": elapsed_ms if status == "failed" else None,
            "phaseDurationSumMs": phase_sum_ms,
            "unattributedWithinBoundaryMs": round(elapsed_ms - phase_sum_ms, 3),
            "phases": phases,
        },
    }
    if html is not None:
        receipt["html"] = html
    if error is not None:
        failed = next((phase for phase in reversed(phases) if phase.get("status") == "failed"), None)
        receipt["failure"] = {
            "failedPhase": failed.get("name") if failed else "pipeline",
            "errorType": type(error).__name__,
        }
    return receipt


async def run_measured_pipeline(
    initial_html: str,
    output_dir: Path,
    *,
    repair: Callable[[str], str | Awaitable[str]],
    capture: Callable[[str], T | Awaitable[T]],
    inspection: Callable[[str, T], Any | Awaitable[Any]],
    fitted_html: Callable[[T, str], str] | None = None,
) -> tuple[str, T, Any, dict[str, Any]]:
    """Measure a real offline repair -> capture -> inspection pipeline.

    Capture may perform local-fit and return a different ``fitted_html``. That
    captured HTML is passed to inspection and preserved as final.html.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    phases: list[dict[str, Any]] = []
    total_start_ns = time.perf_counter_ns()
    receipt_path = output_dir / "pipeline-timing.json"
    try:
        candidate_html = await _measure_async("repair", lambda: repair(initial_html), phases)
        if not isinstance(candidate_html, str) or not candidate_html:
            raise ValueError("repair must return non-empty HTML")
        initial_bytes = initial_html.encode("utf-8")
        await _measure_async(
            "preserveInitialHtml",
            lambda: (output_dir / "initial.html").write_bytes(initial_bytes),
            phases,
        )
        capture_result = await _measure_async("capture", lambda: capture(candidate_html), phases)

        def select_fitted_html() -> str:
            if fitted_html is not None:
                selected = fitted_html(capture_result, candidate_html)
            elif isinstance(capture_result, dict):
                selected = capture_result.get("fitted_html") or capture_result.get("fittedHtml") or candidate_html
            else:
                selected = getattr(capture_result, "fitted_html", None) or candidate_html
            if not isinstance(selected, str) or not selected:
                raise ValueError("capture fitted_html must be non-empty HTML")
            return selected

        final_html = await _measure_async("selectFittedHtml", select_fitted_html, phases)
        final_bytes = final_html.encode("utf-8")
        await _measure_async(
            "preserveFinalHtml",
            lambda: (output_dir / "final.html").write_bytes(final_bytes),
            phases,
        )
        inspection_result = await _measure_async(
            "inspection", lambda: inspection(final_html, capture_result), phases
        )
        receipt = _pipeline_receipt(
            status="completed",
            total_start_ns=total_start_ns,
            total_end_ns=time.perf_counter_ns(),
            phases=phases,
            html={
                "initialSha256": _sha256(initial_bytes),
                "repairCandidateSha256": _sha256(candidate_html.encode("utf-8")),
                "finalSha256": _sha256(final_bytes),
                "sameBytes": initial_bytes == final_bytes,
                "captureChangedHtml": candidate_html != final_html,
            },
        )
        receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return final_html, capture_result, inspection_result, receipt
    except BaseException as error:
        receipt = _pipeline_receipt(
            status="failed",
            total_start_ns=total_start_ns,
            total_end_ns=time.perf_counter_ns(),
            phases=phases,
            error=error,
        )
        receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="Preserve and compare existing HTML without provider calls.")
    parser.add_argument("--initial-html", type=Path, required=True)
    parser.add_argument("--final-html", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    run_offline_comparison(args.initial_html, args.final_html, args.output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
