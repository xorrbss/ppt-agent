#!/usr/bin/env python3
"""Offline, timing-only 90-slide authored pipeline baseline simulator.

It never imports application provider modules, reads environment variables, opens
network sockets, invokes browser/OCR binaries, writes outside --output, or stores
prompt/source text.  It schedules supplied aggregate assumptions through the
pipeline's bounded resource pools and records only safe counts and durations.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import shutil
import statistics
import subprocess
import sys
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Permit aggregate token counters and boolean safety assertions.  Reject only keys
# that would carry a raw secret or payload value in this experiment's schema.
SAFE_KEY = re.compile(r"(?:api[_-]?key|authorization|credential_value|secret_value|password_value|prompt_body|source_content|html_payload|raw_ocr_text)", re.I)
SENSITIVE_VALUE = re.compile(
    r"(?:\bsk-[A-Za-z0-9_-]{8,}|\bbearer\s+[A-Za-z0-9._~-]{8,}|"
    r"\b(?:api[_-]?key|secret|password)\s*[:=]\s*\S+)",
    re.I,
)
STAGES = ("author", "illustrate", "render", "ocr", "vqa", "persist", "export")
POOL_FOR = {
    "author": "author", "illustrate": "illustrate", "render": "render",
    "ocr": "ocr", "vqa": "vqa", "persist": "persist", "export": "export",
}


def _json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("fixture root must be an object")
    return data


def _positive(value: Any, field: str, allow_zero: bool = True) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{field} must be a non-negative number")
    if not allow_zero and value == 0:
        raise ValueError(f"{field} must be positive")
    return float(value)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(repo: Path, args: list[str]) -> str | None:
    try:
        return subprocess.run(["git", "-C", str(repo), *args], text=True,
                              capture_output=True, check=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _chrome_manifest() -> tuple[str | None, str | None, str]:
    candidates = [
        shutil.which("chrome"), shutil.which("google-chrome"), shutil.which("chromium"),
        Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
        Path("C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
        Path.home() / "AppData/Local/Google/Chrome/Application/chrome.exe",
    ]
    chrome = next((Path(candidate) for candidate in candidates if candidate and Path(candidate).is_file()), None)
    if chrome is None:
        return None, None, "not_found"
    try:
        completed = subprocess.run([str(chrome), "--version"], text=True,
                                   encoding="utf-8", errors="replace", capture_output=True,
                                   check=False, timeout=5)
        reported = (completed.stdout or completed.stderr or "").strip()
        # Keep only a version-shaped value. Localized error text and executable
        # paths are deliberately excluded from the privacy-safe manifest.
        match = re.search(r"\b\d+(?:\.\d+){1,}\b", reported)
        version = match.group(0) if match else None
    except (OSError, subprocess.SubprocessError):
        version = None
    return str(chrome), version, "available" if version else "version_unavailable"


def _manifest(repo: Path, fixture: Path) -> dict[str, Any]:
    chrome_path, chrome_version, chrome_status = _chrome_manifest()
    return {
        "harness": "offline_timing_only_v1",
        "fixture_sha256": _sha256(fixture),
        "git_sha": _git(repo, ["rev-parse", "HEAD"]),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "browser_path": chrome_path,
        "browser_version": chrome_version,
        "browser_version_status": chrome_status,
        "font_manifest": "not-inspected (no browser/render execution)",
        "provider_manifest": "offline fixture; no provider/model configured",
        "network_calls": 0,
        "provider_calls": 0,
    }


def _pool_caps(defaults: dict[str, Any]) -> dict[str, int]:
    return {
        "author": int(_positive(defaults["author_concurrency"], "author_concurrency", False)),
        "illustrate": int(_positive(defaults["slide_workers"], "slide_workers", False)),
        "render": int(_positive(defaults["render_concurrency"], "render_concurrency", False)),
        "ocr": int(_positive(defaults["ocr_concurrency"], "ocr_concurrency", False)),
        "vqa": int(_positive(defaults["vqa_concurrency"], "vqa_concurrency", False)),
        "persist": 1, "export": 1,
    }


def _schedule(fixture: dict[str, Any]) -> dict[str, Any]:
    defaults, a = fixture["defaults"], fixture["assumptions"]
    slides = int(_positive(fixture["slide_count"], "slide_count", False))
    caps = _pool_caps(defaults)
    ready = [0.0] * slides
    stage_samples: dict[str, list[float]] = defaultdict(list)
    pool_free: dict[str, list[float]] = {name: [0.0] * cap for name, cap in caps.items()}
    intervals: dict[str, list[tuple[float, float]]] = defaultdict(list)
    stage_end: dict[str, float] = {}

    # A deterministic FIFO lower-bound/what-if schedule.  No actual provider work occurs.
    for stage in ("author", "illustrate", "render", "ocr", "vqa"):
        duration = _positive(a[f"{stage}_ms"], f"{stage}_ms") / 1000.0
        if stage == "vqa":
            duration *= _positive(a["vqa_fraction"], "vqa_fraction")
        if duration == 0:
            continue
        pool = POOL_FOR[stage]
        for slide in range(slides):
            lane = min(range(len(pool_free[pool])), key=lambda index: pool_free[pool][index])
            start = max(ready[slide], pool_free[pool][lane])
            finish = start + duration
            pool_free[pool][lane] = finish
            ready[slide] = finish
            stage_samples[stage].append(duration * 1000)
            intervals[stage].append((start, finish))
        stage_end[stage] = max(ready)

    deck_ready = max(ready, default=0.0)
    persist = _positive(a["persist_ms"], "persist_ms") / 1000.0
    export = _positive(a["export_ms"], "export_ms") / 1000.0
    finish = deck_ready + persist + export
    stage_samples["persist"].append(persist * 1000)
    stage_samples["export"].append(export * 1000)
    intervals["persist"].append((deck_ready, deck_ready + persist))
    intervals["export"].append((deck_ready + persist, finish))

    def percentile(values: list[float], p: float) -> float | None:
        if not values:
            return None
        ordered = sorted(values)
        return round(ordered[min(len(ordered) - 1, int((len(ordered) - 1) * p))], 3)

    def peak(values: list[tuple[float, float]]) -> int:
        events = [(point, delta) for start, end in values for point, delta in ((start, 1), (end, -1))]
        active = maximum = 0
        for _point, delta in sorted(events, key=lambda event: (event[0], event[1])):
            active += delta; maximum = max(maximum, active)
        return maximum

    attempts = _positive(a["author_attempts_per_slide"], "author_attempts_per_slide", False)
    author_calls = slides * attempts
    vqa_calls = slides * _positive(a["vqa_fraction"], "vqa_fraction")
    input_tokens = author_calls * _positive(a["author_input_tokens"], "author_input_tokens") + vqa_calls * _positive(a["vqa_input_tokens"], "vqa_input_tokens")
    output_tokens = author_calls * _positive(a["author_output_tokens"], "author_output_tokens") + vqa_calls * _positive(a["vqa_output_tokens"], "vqa_output_tokens")
    pricing = a["model_pricing_usd_per_million"]
    input_price = _positive(pricing["input"], "pricing.input")
    output_price = _positive(pricing["output"], "pricing.output")
    price_available = input_price > 0 or output_price > 0
    cost = (
        input_tokens / 1_000_000 * input_price
        + output_tokens / 1_000_000 * output_price
        if price_available
        else None
    )
    return {
        "kind": "estimated_from_fixture_assumptions",
        "slide_count": slides,
        "critical_path_seconds": round(finish, 3),
        "timeout_seconds": int(_positive(defaults["deck_timeout_seconds"], "deck_timeout_seconds", False)),
        "timeout_assessment": "pass" if finish <= float(defaults["deck_timeout_seconds"]) else "fail",
        "stage_latency_ms": {stage: {"p50": percentile(samples, .5), "p95": percentile(samples, .95), "samples": len(samples)} for stage, samples in stage_samples.items()},
        "peak_concurrency": {stage: peak(values) for stage, values in intervals.items()},
        "configured_concurrency": caps,
        "provider_usage_estimate": {
            "author_calls": author_calls, "vqa_calls": vqa_calls,
            "input_tokens": input_tokens, "output_tokens": output_tokens,
            "estimated_cost_usd": round(cost, 6) if cost is not None else None,
            "cost_status": "estimated_from_fixture_prices" if price_available else "provider_price_unavailable",
        },
        "retry_amplification": {"author_attempts_per_slide": attempts, "multiplier_vs_one_attempt": attempts, "extra_author_calls": author_calls - slides},
        "calculation_limitations": "Synthetic durations; not a live latency, cost, OCR, VQA, export, or provider measurement.",
    }


def _privacy_violations(value: Any, path: str = "$") -> list[str]:
    if isinstance(value, dict):
        return [f"{path}.{key}" for key, item in value.items() if SAFE_KEY.search(str(key))] + [violation for key, item in value.items() if not SAFE_KEY.search(str(key)) for violation in _privacy_violations(item, f"{path}.{key}")]
    if isinstance(value, list):
        return [violation for index, item in enumerate(value) for violation in _privacy_violations(item, f"{path}[{index}]")]
    if isinstance(value, str) and SENSITIVE_VALUE.search(value):
        return [path]
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=Path(__file__).with_name("fixture_offline_v1.json"))
    parser.add_argument("--output", type=Path, required=True, help="new JSON report inside this experiment directory")
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()
    if args.runs < 3:
        parser.error("--runs must be >= 3 for the baseline acceptance criterion")
    experiment_root = Path(__file__).resolve().parent
    fixture_path = args.fixture.resolve()
    output_path = args.output.resolve()
    if not output_path.is_relative_to(experiment_root):
        parser.error(f"--output must stay under {experiment_root}")
    fixture = _json(fixture_path)
    if _privacy_violations(fixture):
        raise ValueError("fixture contains forbidden sensitive key names")
    report = {"schema_version": 1, "created_at_utc": datetime.now(UTC).isoformat(), "manifest": _manifest(Path(__file__).resolve().parents[4], fixture_path), "runs": []}
    for _ in range(args.runs):
        started = time.perf_counter(); estimate = _schedule(fixture)
        report["runs"].append({"estimate": estimate, "harness_wall_ms": round((time.perf_counter() - started) * 1000, 3)})
    paths = _privacy_violations(report)
    report["privacy_check"] = {"pass": not paths, "forbidden_key_paths": paths, "raw_prompt_logged": False, "credentials_logged": False}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output_path), "runs": args.runs, "network_calls": 0, "provider_calls": 0, "privacy_pass": not paths}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
