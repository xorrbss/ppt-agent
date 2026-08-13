#!/usr/bin/env python3
"""Fail-closed P0 gate for pre-collected authored-renderer security outcomes.

This program never executes authored HTML, starts a renderer, or opens a network
connection.  It only evaluates a JSON observation document produced by an
isolated harness and emits machine-readable promotion evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


INPUT_SCHEMA = "authored-security-observation/v1"
OUTPUT_SCHEMA = "authored-security-gate/v1"
SARIF_SCHEMA = "https://json.schemastore.org/sarif-2.1.0.json"

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_INCONCLUSIVE = 2
EXIT_INPUT_ERROR = 3

BOUNDARIES = (
    ("host_read", "Successful host filesystem reads"),
    ("external_egress", "Successful external network egress"),
    ("loopback_egress", "Successful loopback network egress"),
    ("metadata_egress", "Successful metadata-address egress"),
    ("child_process", "Successful child-process starts"),
    ("ipc", "Successful privileged IPC calls"),
    ("secret_log", "Secrets detected in renderer or gate logs"),
    ("resource_escape", "Resource-limit or containment escapes"),
)

EXECUTION_EXPECTATIONS = (
    ("completed", True, "The isolated corpus run completed"),
    ("timed_out", False, "The isolated corpus run did not time out"),
    ("crashed", False, "The isolated corpus run did not crash"),
    ("monitor_healthy", True, "All boundary monitors stayed healthy"),
    ("isolation_intact", True, "The isolation boundary stayed intact"),
)

CONTAINMENT_EXPECTATIONS = (
    ("non_root", True, "Renderer ran without root/administrator privilege"),
    ("read_only_root", True, "Renderer root filesystem was read-only"),
    ("tmpfs_writable_only", True, "Writable scratch space was isolated tmpfs"),
    ("resource_limits", True, "CPU, memory, PID, and wall-clock limits were active"),
    ("browser_sandbox", True, "Browser sandbox was enabled"),
    ("file_url_disabled", True, "file: navigation and file access were disabled"),
    ("preload_disabled", True, "Electron preload capability was absent"),
    ("ipc_disabled", True, "Privileged Electron IPC was absent"),
    ("os_egress_deny", True, "OS-level egress deny was active"),
    ("csp_enforced", True, "Restrictive CSP was enforced"),
    ("dom_allowlist", True, "AST/DOM allowlist validation was enforced"),
    ("timeout_cleanup", True, "A timed-out renderer was killed and reaped"),
    ("crash_cleanup", True, "A crashed renderer was reaped without a surviving process"),
)

ELECTRON_EXPECTATIONS = (
    ("renderer_pid_observed", True, "Electron reported a real renderer OS PID"),
    ("renderer_process_metric_observed", True, "Electron process metrics matched the renderer PID"),
    ("packaged_runtime_observed", True, "The observation came from a packaged Electron executable"),
    ("os_sandboxed", True, "Electron reported the renderer as OS-sandboxed"),
    ("actual_web_preferences_observed", True, "Electron reported the applied renderer webPreferences"),
    ("sandbox_preference", True, "Electron's configured sandbox preference was enabled"),
    ("web_security", True, "Electron's configured webSecurity preference was enabled"),
    ("context_isolation", True, "Electron's configured contextIsolation preference was enabled"),
    ("node_integration_disabled", True, "Electron's configured Node integration preference was disabled"),
    ("subframe_node_integration_disabled", True, "Electron subframe Node integration was disabled"),
    ("worker_node_integration_disabled", True, "Electron worker Node integration was disabled"),
    ("preload_absent", True, "The disposable authored renderer had no preload"),
    ("configured_preferences_match_actual", True, "Configured and applied security preferences matched"),
    ("ipc_negative_probe_blocked", True, "Active privileged IPC attempts from the renderer were blocked"),
    ("privileged_bridge_absent", True, "The renderer exposed no require/process/Electron bridge"),
    ("ephemeral_session", True, "The renderer used an ephemeral in-memory session"),
    (
        "compiled_policy_matches_build_provenance",
        True,
        "The packaged compiled policy matched its TypeScript build provenance",
    ),
)

DEPLOYMENT_EXPECTATIONS = (
    ("product_worker_smoke", True, "The exact isolated product worker rendered a safe PNG"),
    ("image_id_observed", True, "The executed immutable image ID was observed"),
    ("source_revision_observed", True, "The image source revision matched the requested revision"),
    ("source_tree_digest_observed", True, "The image source bundle digest matched the request"),
    ("contract_digest_observed", True, "The launcher/compose contract digest matched the image"),
    ("no_host_mounts", True, "The container had zero bind or host-volume mounts"),
    ("tmpfs_exact", True, "Only the approved tmpfs scratch mounts were configured"),
    ("no_devices", True, "The container received no host devices"),
    ("no_provider_secrets", True, "The container received no provider secret variables"),
    ("no_preload", True, "The container received no preload injection"),
    ("no_privileged_ipc", True, "The container used private non-privileged IPC"),
    ("init_process", True, "The container used an init process for descendant reaping"),
    ("non_root", True, "The resolved container user was non-root 65532:65532"),
    ("read_only_root", True, "The resolved container root filesystem was read-only"),
    ("network_none", True, "The resolved container network mode was none"),
    ("cap_drop_all", True, "All Linux capabilities were dropped"),
    ("no_new_privileges", True, "No-new-privileges was enabled"),
    ("chromium_seccomp_profile", True, "The fail-closed Chromium namespace seccomp profile was active"),
    ("resource_limits", True, "Resolved PID, CPU, RAM, swap, and nofile limits matched policy"),
    ("no_published_ports", True, "The container exposed no published port"),
)

RESILIENCE_EXPECTATIONS = (
    ("crash_cleanup", True, "A crashed renderer was cleaned up"),
    ("timeout_cleanup", True, "A timed-out renderer was cleaned up"),
    ("lkg_preserved", True, "The last-known-good artifact remained unchanged"),
    ("candidate_unpublished", True, "A failed candidate was not published"),
    ("kill_switch_rejects_new_work", True, "The product kill switch rejected new work"),
)

LIST_EXECUTION_EXPECTATIONS = (
    ("unsafe_launch_options", "No unsafe browser or Electron launch options"),
    ("host_stability_risk_cases_executed", "No host-stability-risk cases ran on the host"),
    ("observer_errors", "No observer errors occurred"),
    ("reported_kill_switch_events", "The harness reported no prior kill-switch event"),
)


@dataclass(frozen=True)
class Check:
    check_id: str
    title: str
    category: str
    status: str
    reason: str
    observed: bool
    expected: Any
    actual: Any
    evidence_count: int = 0


class InputError(ValueError):
    """The input is malformed rather than merely incomplete."""


def _safe_run_id(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        return "unknown-run"
    cleaned = re.sub(r"[^A-Za-z0-9_.-]", "_", value.strip())[:64]
    return cleaned or "unknown-run"


def _is_non_negative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _safe_list_length(value: Any, field: str) -> int:
    if not isinstance(value, list):
        raise InputError(f"{field} must be an array")
    if not all(isinstance(item, str) for item in value):
        raise InputError(f"{field} must contain only strings")
    return len(value)


def _boolean_check(
    source: dict[str, Any] | None,
    key: str,
    expected: bool,
    title: str,
    category: str,
) -> Check:
    check_id = f"{category}.{key}"
    if source is None or key not in source:
        return Check(
            check_id, title, category, "INCONCLUSIVE",
            "required observation is missing", False, expected, None,
        )
    actual = source[key]
    if not isinstance(actual, bool):
        raise InputError(f"{check_id} must be a boolean")
    if actual == expected:
        return Check(check_id, title, category, "PASS", "expected state observed", True, expected, actual)
    return Check(check_id, title, category, "FAIL", "unsafe state observed", True, expected, actual)


def _empty_list_check(
    source: dict[str, Any] | None,
    key: str,
    title: str,
) -> Check:
    check_id = f"execution.{key}"
    if source is None or key not in source:
        return Check(
            check_id, title, "execution", "INCONCLUSIVE",
            "required observation is missing", False, 0, None,
        )
    count = _safe_list_length(source[key], check_id)
    if count == 0:
        return Check(check_id, title, "execution", "PASS", "no events observed", True, 0, 0)
    return Check(
        check_id, title, "execution", "FAIL",
        f"{count} forbidden event(s) reported", True, 0, count,
    )


def _boundary_check(boundaries: dict[str, Any] | None, key: str, title: str) -> Check:
    check_id = f"boundary.{key}"
    if boundaries is None or key not in boundaries:
        return Check(
            check_id, title, "boundary", "INCONCLUSIVE",
            "boundary observation is missing", False, 0, None,
        )
    item = boundaries[key]
    if not isinstance(item, dict):
        raise InputError(f"{check_id} must be an object")

    observed = item.get("observed")
    if observed is None:
        return Check(
            check_id, title, "boundary", "INCONCLUSIVE",
            "observed flag is missing", False, 0, None,
        )
    if not isinstance(observed, bool):
        raise InputError(f"{check_id}.observed must be a boolean")

    evidence = item.get("evidence", [])
    evidence_count = _safe_list_length(evidence, f"{check_id}.evidence")
    if any(not re.fullmatch(r"sha256:[0-9a-f]{64}", value) for value in evidence):
        raise InputError(f"{check_id}.evidence must contain only sha256 digests")
    violations = item.get("violations")

    if violations is not None and not _is_non_negative_int(violations):
        raise InputError(f"{check_id}.violations must be null or a non-negative integer")
    if violations is not None and violations > 0:
        return Check(
            check_id, title, "boundary", "FAIL",
            f"{violations} boundary violation(s) observed", observed, 0, violations, evidence_count,
        )
    if not observed:
        return Check(
            check_id, title, "boundary", "INCONCLUSIVE",
            "boundary was not observed", False, 0, violations, evidence_count,
        )
    if violations is None:
        return Check(
            check_id, title, "boundary", "INCONCLUSIVE",
            "violation count is missing", True, 0, None, evidence_count,
        )
    attempts = item.get("attempts")
    blocked = item.get("blocked")
    if not _is_non_negative_int(attempts):
        raise InputError(f"{check_id}.attempts must be a non-negative integer")
    if not _is_non_negative_int(blocked):
        raise InputError(f"{check_id}.blocked must be a non-negative integer")
    if blocked > attempts:
        raise InputError(f"{check_id}.blocked cannot exceed attempts")
    if attempts == 0:
        return Check(
            check_id, title, "boundary", "INCONCLUSIVE",
            "no active negative probe was attempted", True, ">=1", attempts, evidence_count,
        )
    if blocked != attempts:
        return Check(
            check_id, title, "boundary", "FAIL",
            "one or more active negative probes were not blocked",
            True, attempts, blocked, evidence_count,
        )
    if evidence_count == 0:
        return Check(
            check_id, title, "boundary", "INCONCLUSIVE",
            "observer evidence reference is missing", True, 0, violations, 0,
        )
    return Check(
        check_id, title, "boundary", "PASS",
        "zero violations observed with evidence", True, 0, violations, evidence_count,
    )


def _object_or_none(document: dict[str, Any], key: str) -> dict[str, Any] | None:
    value = document.get(key)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise InputError(f"{key} must be an object")
    return value


def evaluate_document(document: dict[str, Any], source_digest: str = "") -> dict[str, Any]:
    if not isinstance(document, dict):
        raise InputError("top-level input must be an object")
    schema_version = document.get("schema_version")
    if schema_version != INPUT_SCHEMA:
        raise InputError(f"schema_version must equal {INPUT_SCHEMA}")

    run_id = _safe_run_id(document.get("run_id"))
    execution = _object_or_none(document, "execution")
    containment = _object_or_none(document, "containment")
    boundaries = _object_or_none(document, "boundaries")
    electron = _object_or_none(document, "electron")
    deployment = _object_or_none(document, "deployment")
    resilience = _object_or_none(document, "resilience")

    checks: list[Check] = []
    checks.extend(
        _boolean_check(execution, key, expected, title, "execution")
        for key, expected, title in EXECUTION_EXPECTATIONS
    )
    checks.extend(_empty_list_check(execution, key, title) for key, title in LIST_EXECUTION_EXPECTATIONS)
    checks.extend(
        _boolean_check(containment, key, expected, title, "containment")
        for key, expected, title in CONTAINMENT_EXPECTATIONS
    )
    checks.extend(_boundary_check(boundaries, key, title) for key, title in BOUNDARIES)
    checks.extend(
        _boolean_check(electron, key, expected, title, "electron")
        for key, expected, title in ELECTRON_EXPECTATIONS
    )
    checks.extend(
        _boolean_check(deployment, key, expected, title, "deployment")
        for key, expected, title in DEPLOYMENT_EXPECTATIONS
    )
    checks.extend(
        _boolean_check(resilience, key, expected, title, "resilience")
        for key, expected, title in RESILIENCE_EXPECTATIONS
    )

    statuses = [check.status for check in checks]
    if "FAIL" in statuses:
        overall = "FAIL"
    elif "INCONCLUSIVE" in statuses:
        overall = "INCONCLUSIVE"
    else:
        overall = "PASS"

    kill_reasons = [
        f"{check.check_id}: {check.reason}"
        for check in checks
        if check.status != "PASS"
    ]
    result_checks = [
        {
            "id": check.check_id,
            "title": check.title,
            "category": check.category,
            "status": check.status,
            "reason": check.reason,
            "observed": check.observed,
            "expected": check.expected,
            "actual": check.actual,
            "evidence_count": check.evidence_count,
        }
        for check in checks
    ]
    summary = {
        "total": len(checks),
        "passed": statuses.count("PASS"),
        "failed": statuses.count("FAIL"),
        "inconclusive": statuses.count("INCONCLUSIVE"),
    }
    return {
        "schema_version": OUTPUT_SCHEMA,
        "run_id": run_id,
        "status": overall,
        "promotion_allowed": overall == "PASS",
        "fail_closed": True,
        "exit_code": exit_code_for_status(overall),
        "source_sha256": source_digest,
        "summary": summary,
        "kill_switch": {
            "triggered": overall != "PASS",
            "reasons": kill_reasons,
            "required_action": (
                "stop scheduling cases, terminate only the isolated harness, and quarantine evidence"
                if overall != "PASS" else "none"
            ),
        },
        "checks": result_checks,
    }


def error_result(message: str, source_digest: str = "") -> dict[str, Any]:
    return {
        "schema_version": OUTPUT_SCHEMA,
        "run_id": "unknown-run",
        "status": "ERROR",
        "promotion_allowed": False,
        "fail_closed": True,
        "exit_code": EXIT_INPUT_ERROR,
        "source_sha256": source_digest,
        "summary": {"total": 1, "passed": 0, "failed": 1, "inconclusive": 0},
        "kill_switch": {
            "triggered": True,
            "reasons": ["input.invalid: observation document could not be evaluated"],
            "required_action": "stop scheduling cases and quarantine the invalid input",
        },
        "checks": [{
            "id": "input.invalid",
            "title": "Observation input is valid",
            "category": "input",
            "status": "FAIL",
            "reason": message,
            "observed": False,
            "expected": "valid input",
            "actual": "invalid input",
            "evidence_count": 0,
        }],
    }


def exit_code_for_status(status: str) -> int:
    return {
        "PASS": EXIT_PASS,
        "FAIL": EXIT_FAIL,
        "INCONCLUSIVE": EXIT_INCONCLUSIVE,
        "ERROR": EXIT_INPUT_ERROR,
    }[status]


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        temporary.write_bytes(data)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def junit_document(result: dict[str, Any]) -> bytes:
    checks = result["checks"]
    failures = sum(1 for check in checks if check["status"] == "FAIL")
    errors = sum(1 for check in checks if check["status"] == "INCONCLUSIVE")
    suite = ET.Element(
        "testsuite",
        {
            "name": "authored-renderer-security-p0",
            "tests": str(len(checks)),
            "failures": str(failures),
            "errors": str(errors),
            "skipped": "0",
        },
    )
    properties = ET.SubElement(suite, "properties")
    ET.SubElement(properties, "property", {"name": "status", "value": result["status"]})
    ET.SubElement(properties, "property", {"name": "fail_closed", "value": "true"})
    for check in checks:
        case = ET.SubElement(
            suite,
            "testcase",
            {"classname": f"authored_security.{check['category']}", "name": check["id"]},
        )
        if check["status"] == "FAIL":
            failure = ET.SubElement(case, "failure", {"type": "security-boundary", "message": check["reason"]})
            failure.text = check["reason"]
        elif check["status"] == "INCONCLUSIVE":
            error = ET.SubElement(case, "error", {"type": "inconclusive-observation", "message": check["reason"]})
            error.text = check["reason"]
    tree = ET.ElementTree(suite)
    ET.indent(tree, space="  ")
    return ET.tostring(suite, encoding="utf-8", xml_declaration=True) + b"\n"


def sarif_document(result: dict[str, Any]) -> dict[str, Any]:
    rules = []
    sarif_results = []
    for check in result["checks"]:
        rules.append({
            "id": check["id"],
            "name": re.sub(r"[^A-Za-z0-9_]", "_", check["id"]),
            "shortDescription": {"text": check["title"]},
            "defaultConfiguration": {"level": "error"},
            "properties": {"category": check["category"], "failClosed": True},
        })
        if check["status"] != "PASS":
            sarif_results.append({
                "ruleId": check["id"],
                "level": "error",
                "message": {"text": f"{check['status']}: {check['reason']}"},
                "locations": [{
                    "physicalLocation": {
                        "artifactLocation": {"uri": "security-observation.json"},
                    }
                }],
                "properties": {
                    "gateStatus": check["status"],
                    "promotionAllowed": False,
                    "evidenceCount": check["evidence_count"],
                },
            })
    return {
        "$schema": SARIF_SCHEMA,
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {
                "name": "authored-renderer-security-gate",
                "informationUri": "https://github.com/presenton/presenton",
                "rules": rules,
            }},
            "automationDetails": {"id": result["run_id"]},
            "results": sarif_results,
            "properties": {
                "gateStatus": result["status"],
                "promotionAllowed": result["promotion_allowed"],
                "failClosed": True,
            },
        }],
    }


def write_outputs(result: dict[str, Any], output_dir: Path) -> tuple[Path, Path, Path]:
    json_path = output_dir / "gate-result.json"
    junit_path = output_dir / "gate-junit.xml"
    sarif_path = output_dir / "gate.sarif"
    _write_atomic(json_path, _json_bytes(result))
    _write_atomic(junit_path, junit_document(result))
    _write_atomic(sarif_path, _json_bytes(sarif_document(result)))
    return json_path, junit_path, sarif_path


def _load_input(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    try:
        document = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InputError("input is not valid UTF-8 JSON") from exc
    if not isinstance(document, dict):
        raise InputError("top-level input must be an object")
    return document, digest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="pre-collected observation JSON")
    parser.add_argument("--output-dir", type=Path, required=True, help="directory for JSON, JUnit, and SARIF")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    digest = ""
    try:
        document, digest = _load_input(args.input)
        result = evaluate_document(document, digest)
    except InputError as exc:
        # InputError messages are field/schema diagnostics and never include values.
        result = error_result(str(exc), digest)
    except OSError as exc:
        # OSError text can contain a caller-supplied path, so expose only its type.
        result = error_result(f"input could not be read ({type(exc).__name__})", digest)
    try:
        paths = write_outputs(result, args.output_dir)
    except OSError as exc:
        print(f"security gate could not write output: {type(exc).__name__}", file=sys.stderr)
        return EXIT_INPUT_ERROR

    print(
        f"status={result['status']} promotion_allowed={str(result['promotion_allowed']).lower()} "
        f"kill_switch={str(result['kill_switch']['triggered']).lower()}"
    )
    print("outputs=" + ",".join(path.name for path in paths))
    return exit_code_for_status(result["status"])


if __name__ == "__main__":
    raise SystemExit(main())
