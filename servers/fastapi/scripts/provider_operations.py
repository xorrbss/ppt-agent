#!/usr/bin/env python3
"""Deterministic, network-free provider staging capacity and operations planner.

This tool never imports a provider SDK and never opens a socket.  It validates a
fixed, non-sensitive slide corpus, calculates a conservative paid-run ceiling,
and exercises a deterministic offline 90 x 3 run with chaos/checkpoint/resume.
The staging command only emits an approval-gated execution manifest; an external
operator must deliberately hand that manifest to the generation executor.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from scripts.provider_operations_fixture import validate_endpoint, validate_fixture


SCHEMA = "provider-operations/1"
APPROVAL_ID = re.compile(r"^(?:CHG|CAB)-[0-9]{6,12}$")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def percentile(values: Iterable[int], fraction: float) -> int | None:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)] if ordered else None


def _metric(values: list[int]) -> dict[str, int | None]:
    return {
        "p50": percentile(values, 0.50),
        "p95": percentile(values, 0.95),
        "p99": percentile(values, 0.99),
        "max": max(values) if values else None,
        "samples": len(values),
    }


def _one_attempt_cost(slide: dict[str, Any], fixture: dict[str, Any]) -> dict[str, int]:
    capacity = fixture["capacity"]
    pricing = fixture["pricing"]
    input_tokens = slide["complexity_units"] * capacity["input_tokens_per_complexity"]
    output_tokens = slide["complexity_units"] * capacity["output_tokens_per_complexity"]
    images = capacity["images_per_slide"]
    cost = math.ceil(input_tokens * pricing["input_microunits_per_1k"] / 1000)
    cost += math.ceil(output_tokens * pricing["output_microunits_per_1k"] / 1000)
    cost += images * pricing["image_microunits_each"]
    return {"input_tokens": input_tokens, "output_tokens": output_tokens, "images": images, "cost": cost}


def estimate_ceiling(fixture: dict[str, Any]) -> dict[str, Any]:
    per_attempt = [_one_attempt_cost(slide, fixture) for slide in fixture["slides"]]
    attempts = fixture["capacity"]["max_attempts_per_slide"]
    cost = sum(item["cost"] for item in per_attempt) * attempts
    provider_rpm = fixture["capacity"]["provider_rpm"]
    concurrency = fixture["capacity"]["concurrency"]
    return {
        "pricing_version": fixture["pricing"]["version"],
        "currency": fixture["staging"]["currency"],
        "slides": 90,
        "maximum_attempts_per_slide": attempts,
        "maximum_provider_requests": 90 * attempts,
        "input_tokens": sum(item["input_tokens"] for item in per_attempt) * attempts,
        "output_tokens": sum(item["output_tokens"] for item in per_attempt) * attempts,
        "images": sum(item["images"] for item in per_attempt) * attempts,
        "estimated_cost_microunits": cost,
        "estimated_cost": round(cost / 1_000_000, 6),
        "ceiling_is_not_a_quote": True,
        "scheduler_policy": {
            "algorithm": "token_bucket_with_bounded_fifo",
            "provider_rpm": provider_rpm,
            "maximum_concurrency": concurrency,
            "maximum_start_rate_per_second": round(provider_rpm / 60, 6),
            "maximum_queue_depth": concurrency * 2,
            "backpressure": "reject_429_with_retry_after_before_enqueue",
            "circuit_breaker": {"open_after_consecutive_failures": 5, "cooldown_seconds": 60},
            "job_budget_microunits": fixture["kill_conditions"]["estimated_cost_microunits"],
            "budget_action": "cancel_before_next_provider_attempt",
        },
    }


def _event_id(corpus_id: str, run: int, slide_id: str, attempt: int) -> str:
    material = f"{corpus_id}:{run}:{slide_id}:{attempt}".encode()
    return hashlib.sha256(material).hexdigest()


def simulate_offline(fixture: dict[str, Any]) -> dict[str, Any]:
    """Run the deterministic 90 x 3 capacity model, including retry and resume."""
    stage_values: dict[str, list[int]] = defaultdict(list)
    critical_paths: list[int] = []
    cpu_values: list[int] = []
    ram_values: list[int] = []
    request_count = 0
    logical_slides = 0
    retry_classes: Counter[str] = Counter()
    costs = 0
    job_costs: list[int] = []
    input_tokens = 0
    output_tokens = 0
    images = 0
    checkpoints: list[dict[str, Any]] = []
    chaos_events: list[dict[str, Any]] = []
    seen_events: set[str] = set()
    for run in range(1, fixture["runs"] + 1):
        run_cost = 0
        for slide in fixture["slides"]:
            logical_slides += 1
            complexity = slide["complexity_units"]
            export_units = slide["export_units"]
            provider_ms = 420 + complexity * 37 + ((run * 17 + complexity * 11) % 53)
            export_ms = 110 + export_units * 29 + ((run * 13 + export_units * 7) % 31)
            checkpoint_ms = 18 + (complexity % 5)
            resume_ms = 0
            attempt_cost = _one_attempt_cost(slide, fixture)
            attempts = 1
            retry_class = "initial"
            retry_fault = fixture["chaos"]["retry"]
            resume_fault = fixture["chaos"]["cancel_resume"]
            if run == retry_fault["run"] and slide["slide_id"] == retry_fault["slide_id"]:
                attempts += 1
                retry_class = "retry"
                chaos_events.append(
                    {
                        "kind": "retryable_provider_timeout",
                        "run": run,
                        "slide_id": slide["slide_id"],
                        "failed_attempt": retry_fault["attempt"],
                        "action": "bounded_retry",
                    }
                )
            if run == resume_fault["run"] and slide["slide_id"] == resume_fault["slide_id"]:
                attempts += 1
                retry_class = "resume"
                resume_ms = 63
                checkpoint = {
                    "checkpoint_id": _event_id(fixture["corpus_id"], run, slide["slide_id"], 0),
                    "run": run,
                    "slide_id": slide["slide_id"],
                    "state": "cancelled_then_resumed",
                }
                checkpoints.append(checkpoint)
                chaos_events.append(
                    {
                        "kind": "operator_cancel",
                        "run": run,
                        "slide_id": slide["slide_id"],
                        "failed_attempt": resume_fault["attempt"],
                        "action": "resume_from_checkpoint",
                    }
                )
            for attempt in range(1, attempts + 1):
                event_id = _event_id(fixture["corpus_id"], run, slide["slide_id"], attempt)
                if event_id in seen_events:
                    raise AssertionError("deterministic event ID collision")
                seen_events.add(event_id)
            request_count += attempts
            retry_classes[retry_class] += 1
            costs += attempt_cost["cost"] * attempts
            run_cost += attempt_cost["cost"] * attempts
            input_tokens += attempt_cost["input_tokens"] * attempts
            output_tokens += attempt_cost["output_tokens"] * attempts
            images += attempt_cost["images"] * attempts
            stage_values["provider"].append(provider_ms * attempts)
            stage_values["checkpoint"].append(checkpoint_ms)
            stage_values["resume"].append(resume_ms)
            stage_values["export"].append(export_ms)
            critical_paths.append(provider_ms * attempts + checkpoint_ms + resume_ms + export_ms)
            cpu_values.append(21 + complexity * 3 + export_units * 2)
            ram_values.append(180 + complexity * 9 + export_units * 6)
        job_costs.append(run_cost)
    amplification = round(request_count / logical_slides, 6)
    rpm = fixture["capacity"]["provider_rpm"]
    concurrency = fixture["capacity"]["concurrency"]
    rpm_floor_ms = math.ceil(request_count / rpm) * 60_000
    concurrency_floor_ms = math.ceil(sum(stage_values["provider"]) / concurrency)
    report: dict[str, Any] = {
        "schema": SCHEMA,
        "mode": "offline_local_deterministic",
        "corpus": {"id": fixture["corpus_id"], "slides": 90, "runs": 3, "logical_slides": logical_slides},
        "network_calls": 0,
        "provider_calls": 0,
        "paid_calls": 0,
        "completed_logical_slides": logical_slides,
        "stage_latency_ms": {name: _metric(values) for name, values in sorted(stage_values.items())},
        "critical_path_ms": {
            "method": "per_logical_slide_sum_of_provider_checkpoint_resume_export",
            **_metric(critical_paths),
        },
        "retry_amplification": {
            "logical_requests": logical_slides,
            "attempted_requests": request_count,
            "additional_requests": request_count - logical_slides,
            "multiplier": amplification,
            "logical_slide_count_by_retry_class": dict(sorted(retry_classes.items())),
        },
        "usage_and_cost": {
            "state": "estimated",
            "pricing_version": fixture["pricing"]["version"],
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "images": images,
            "estimated_cost_microunits": costs,
            "maximum_job_estimated_cost_microunits": max(job_costs),
            "job_estimated_cost_microunits": job_costs,
            "reported_cost_microunits": None,
        },
        "resources": {
            "measurement_kind": "deterministic_capacity_model_not_host_telemetry",
            "cpu_percent": _metric(cpu_values),
            "ram_mib": _metric(ram_values),
            "export_tail_ms": {
                "p95": percentile(stage_values["export"], 0.95),
                "p99": percentile(stage_values["export"], 0.99),
            },
        },
        "scheduler": {
            "model": "rpm_aware_bounded_concurrency",
            "maximum_concurrency": concurrency,
            "provider_rpm": rpm,
            "queue_high_watermark": concurrency * 2,
            "rpm_capacity_floor_ms": rpm_floor_ms,
            "concurrency_capacity_floor_ms": concurrency_floor_ms,
            "planned_capacity_floor_ms": max(rpm_floor_ms, concurrency_floor_ms),
            "limiting_factor": "provider_rpm" if rpm_floor_ms >= concurrency_floor_ms else "concurrency",
            "backpressure_events": 0,
            "circuit_breaker_open_events": 0,
            "budget_cancel_events": 0,
        },
        "chaos": {
            "deterministic": True,
            "events": chaos_events,
            "checkpoints": checkpoints,
            "resumed_to_completion": len(checkpoints) == 1 and logical_slides == 270,
        },
        "privacy": {
            "fixed_non_sensitive_corpus": True,
            "raw_payloads_persisted": False,
            "credentials_read": False,
        },
    }
    report["kill_conditions"] = evaluate_kill_conditions(report, fixture["kill_conditions"])
    report["status"] = "pass" if not report["kill_conditions"]["triggered"] else "kill"
    return report


def evaluate_kill_conditions(report: dict[str, Any], limits: dict[str, int | float]) -> dict[str, Any]:
    observed = {
        "estimated_cost_microunits": report["usage_and_cost"]["maximum_job_estimated_cost_microunits"],
        "retry_amplification": report["retry_amplification"]["multiplier"],
        "critical_path_p99_ms": report["critical_path_ms"]["p99"],
        "ram_peak_mib": report["resources"]["ram_mib"]["max"],
        "export_p99_ms": report["resources"]["export_tail_ms"]["p99"],
    }
    checks = []
    for name, limit in limits.items():
        value = observed[name]
        checks.append({"metric": name, "observed": value, "limit": limit, "triggered": value >= limit})
    triggered = [check["metric"] for check in checks if check["triggered"]]
    return {"triggered": triggered, "checks": checks, "action": "cancel_and_open_incident" if triggered else "continue"}


def build_staging_manifest(fixture: dict[str, Any], endpoint: str, approval_id: str | None) -> dict[str, Any]:
    endpoint = validate_endpoint(endpoint, fixture["staging"]["hostname_allowlist"])
    ceiling = estimate_ceiling(fixture)
    approval_reference_present = approval_id is not None and APPROVAL_ID.fullmatch(approval_id) is not None
    execution_commands = [
        (
            'node ..\\..\\scripts\\ppt-agent.mjs --content "Public operational capacity fixture A" '
            "--slides 30 --mode authored --async --timeout 1800 --base http://127.0.0.1:8000"
        ),
        (
            'node ..\\..\\scripts\\ppt-agent.mjs --content "Public operational capacity fixture B" '
            "--slides 30 --mode authored --async --timeout 1800 --base http://127.0.0.1:8000"
        ),
        (
            'node ..\\..\\scripts\\ppt-agent.mjs --content "Public operational capacity fixture C" '
            "--slides 30 --mode authored --async --timeout 1800 --base http://127.0.0.1:8000"
        ),
    ]
    return {
        "schema": SCHEMA,
        "mode": "staging_approval_gate",
        "endpoint": endpoint,
        "hostname_allowlist": fixture["staging"]["hostname_allowlist"],
        "approval": {
            "required": True,
            "approval_id": approval_id if approval_reference_present else None,
            "state": "reference_recorded_unverified" if approval_reference_present else "blocked",
            "verification": "external_change_system_and_human_verification_required",
        },
        "estimated_ceiling": ceiling,
        "kill_conditions": fixture["kill_conditions"],
        "network_calls": 0,
        "provider_calls": 0,
        "executor_enabled": False,
        "paid_execution_authorized": False,
        "paid_execution_commands": execution_commands,
        "next_action": (
            "verify_approval_externally_then_run_recorded_commands"
            if approval_reference_present
            else "obtain_approval"
        ),
    }


def _load_fixture(path: Path) -> dict[str, Any]:
    return validate_fixture(_read_json(path))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate", help="validate the fixed corpus and privacy contract")
    validate.add_argument("--output", type=Path)
    dry_run = subparsers.add_parser("dry-run", help="calculate the conservative paid staging ceiling")
    dry_run.add_argument("--output", type=Path, required=True)
    offline = subparsers.add_parser("offline", help="execute deterministic local 90 x 3 plus chaos/resume")
    offline.add_argument("--output", type=Path, required=True)
    staging = subparsers.add_parser("staging-gate", help="emit a manifest; never executes paid calls")
    staging.add_argument("--endpoint", required=True)
    staging.add_argument("--approval-id")
    staging.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        fixture = _load_fixture(args.fixture)
        if args.command == "validate":
            result = {
                "schema": SCHEMA,
                "status": "valid",
                "corpus_id": fixture["corpus_id"],
                "slides": len(fixture["slides"]),
                "runs": fixture["runs"],
                "network_calls": 0,
                "provider_calls": 0,
            }
            if args.output:
                _write_json(args.output, result)
        elif args.command == "dry-run":
            result = {
                "schema": SCHEMA,
                "mode": "dry_run",
                "estimated_ceiling": estimate_ceiling(fixture),
                "network_calls": 0,
                "provider_calls": 0,
            }
            _write_json(args.output, result)
        elif args.command == "offline":
            result = simulate_offline(fixture)
            _write_json(args.output, result)
        else:
            result = build_staging_manifest(fixture, args.endpoint, args.approval_id)
            _write_json(args.output, result)
            if result["approval"]["state"] == "blocked":
                print(json.dumps(result, ensure_ascii=False))
                return 3
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, json.JSONDecodeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
