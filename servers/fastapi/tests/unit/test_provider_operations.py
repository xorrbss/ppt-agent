from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest


FASTAPI_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = FASTAPI_ROOT / "scripts" / "provider_operations.py"
FIXTURE_PATH = FASTAPI_ROOT / "tests" / "fixtures" / "provider-operations-90.json"
SPEC = importlib.util.spec_from_file_location("provider_operations", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
operations = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(operations)


def raw_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def fixture() -> dict:
    return operations.validate_fixture(raw_fixture())


def test_fixed_corpus_is_exactly_90_unique_non_sensitive_slides_and_three_runs():
    safe = fixture()
    assert safe["fixture_kind"] == "fixed_non_sensitive_90"
    assert len(safe["slides"]) == 90
    assert len({slide["slide_id"] for slide in safe["slides"]}) == 90
    assert safe["runs"] == 3
    serialized = json.dumps(safe).lower()
    assert "prompt" not in serialized
    assert "api_key" not in serialized
    assert "authorization" not in serialized


@pytest.mark.parametrize("field", ["prompt", "api_key", "authorization", "exception_body", "source_url"])
def test_fixture_rejects_payload_secret_and_diagnostic_fields(field):
    unsafe = raw_fixture()
    unsafe["slides"][0][field] = "sk-this-must-never-enter-a-report"
    with pytest.raises(ValueError, match="privacy-forbidden|unknown fields"):
        operations.validate_fixture(unsafe)


def test_fixture_rejects_sensitive_looking_values_even_under_known_fields():
    unsafe = raw_fixture()
    unsafe["slides"][0]["archetype"] = "secret=not-allowed"
    with pytest.raises(ValueError, match="sensitive-looking value"):
        operations.validate_fixture(unsafe)


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://api.openai.com",
        "https://api.openai.com.evil.example",
        "https://user:pass@api.openai.com",
        "https://api.openai.com:8443",
        "https://api.openai.com/v1",
        "https://127.0.0.1",
        "https://localhost",
    ],
)
def test_staging_endpoint_is_exact_https_allowlist_origin(endpoint):
    with pytest.raises(ValueError, match="allowlisted HTTPS origin|forbidden"):
        operations.validate_endpoint(endpoint, ["api.openai.com"])


def test_dry_run_is_a_conservative_costed_ceiling_without_provider_calls():
    safe = fixture()
    ceiling = operations.estimate_ceiling(safe)
    assert ceiling["slides"] == 90
    assert ceiling["maximum_provider_requests"] == 180
    assert ceiling["estimated_cost_microunits"] > 0
    assert ceiling["pricing_version"] == "ops-ceiling-20260813"
    assert ceiling["ceiling_is_not_a_quote"] is True
    assert ceiling["scheduler_policy"] == {
        "algorithm": "token_bucket_with_bounded_fifo",
        "provider_rpm": 60,
        "maximum_concurrency": 4,
        "maximum_start_rate_per_second": 1.0,
        "maximum_queue_depth": 8,
        "backpressure": "reject_429_with_retry_after_before_enqueue",
        "circuit_breaker": {"open_after_consecutive_failures": 5, "cooldown_seconds": 60},
        "job_budget_microunits": 20_000_000,
        "budget_action": "cancel_before_next_provider_attempt",
    }


def test_offline_90x3_is_deterministic_and_resumes_after_cancel():
    safe = fixture()
    first = operations.simulate_offline(safe)
    second = operations.simulate_offline(safe)
    assert first == second
    assert first["corpus"]["logical_slides"] == 270
    assert first["completed_logical_slides"] == 270
    assert first["network_calls"] == first["provider_calls"] == first["paid_calls"] == 0
    assert first["retry_amplification"] == {
        "logical_requests": 270,
        "attempted_requests": 272,
        "additional_requests": 2,
        "multiplier": 1.007407,
        "logical_slide_count_by_retry_class": {"initial": 268, "resume": 1, "retry": 1},
    }
    assert first["chaos"]["resumed_to_completion"] is True
    assert [event["kind"] for event in first["chaos"]["events"]] == [
        "retryable_provider_timeout",
        "operator_cancel",
    ]
    assert len(first["chaos"]["checkpoints"]) == 1
    assert first["status"] == "pass"


def test_report_contains_latency_critical_path_cost_cpu_ram_and_export_tail():
    report = operations.simulate_offline(fixture())
    for metric in ("p50", "p95", "p99", "max", "samples"):
        assert report["stage_latency_ms"]["provider"][metric] is not None
        assert report["critical_path_ms"][metric] is not None
    assert report["usage_and_cost"]["state"] == "estimated"
    assert report["usage_and_cost"]["reported_cost_microunits"] is None
    assert report["usage_and_cost"]["estimated_cost_microunits"] > 0
    assert report["resources"]["cpu_percent"]["p99"] > 0
    assert report["resources"]["ram_mib"]["max"] > 0
    assert report["resources"]["export_tail_ms"]["p99"] > 0
    assert report["scheduler"]["planned_capacity_floor_ms"] == 300_000
    assert report["scheduler"]["limiting_factor"] == "provider_rpm"


def test_kill_conditions_fail_closed_when_any_limit_is_exceeded():
    safe = fixture()
    report = operations.simulate_offline(safe)
    limits = copy.deepcopy(safe["kill_conditions"])
    limits["ram_peak_mib"] = 1
    result = operations.evaluate_kill_conditions(report, limits)
    assert result["triggered"] == ["ram_peak_mib"]
    assert result["action"] == "cancel_and_open_incident"


def test_staging_gate_is_blocked_without_change_approval_and_never_executes():
    safe = fixture()
    blocked = operations.build_staging_manifest(safe, "https://api.openai.com", None)
    assert blocked["approval"]["state"] == "blocked"
    assert blocked["executor_enabled"] is False
    assert blocked["network_calls"] == blocked["provider_calls"] == 0
    approved = operations.build_staging_manifest(safe, "https://api.openai.com", "CHG-2026081301")
    assert approved["approval"] == {
        "required": True,
        "approval_id": "CHG-2026081301",
        "state": "reference_recorded_unverified",
        "verification": "external_change_system_and_human_verification_required",
    }
    assert approved["executor_enabled"] is False
    assert approved["paid_execution_authorized"] is False
    assert len(approved["paid_execution_commands"]) == 3


def test_cli_offline_writes_report_and_staging_without_approval_returns_three(tmp_path):
    report_path = tmp_path / "offline.json"
    assert operations.main(["--fixture", str(FIXTURE_PATH), "offline", "--output", str(report_path)]) == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["completed_logical_slides"] == 270
    manifest_path = tmp_path / "staging.json"
    assert operations.main(
        [
            "--fixture",
            str(FIXTURE_PATH),
            "staging-gate",
            "--endpoint",
            "https://api.openai.com",
            "--output",
            str(manifest_path),
        ]
    ) == 3
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["approval"]["state"] == "blocked"
