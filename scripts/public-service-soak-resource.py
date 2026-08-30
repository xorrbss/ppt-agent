#!/usr/bin/env python3
"""Resumable, wall-clock-honest public-service worker resource soak.

Only time observed while this process is alive is accumulated.  A restart may
resume the same state file, but the gap between processes is never counted.
The runner samples an explicitly owned process tree and the durable SQLite
operation/outbox queue.  It never starts or stops product processes.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
from typing import Any
import uuid


MANIFEST_SCHEMA = "public-service-soak-resource-manifest/v1"
STATE_SCHEMA = "public-service-soak-resource-state/v1"
STATE_ENTRY_SCHEMA = "public-service-soak-resource-state-entry/v1"
EVIDENCE_SCHEMA = "public-service-soak-resource-evidence/v1"
LIMIT_SCHEMA = "public-service-resource-limit-attestation/v1"
REQUIRED_LIMITS = {"memory", "cpu", "pids", "openFiles", "outputBytes", "wallClock"}
READY_STATES = ("queued",)
RC_ID = re.compile(r"^sha256:[0-9a-f]{64}$")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=True, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass


def sha256_json(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def resolve_child(repo: Path, raw: str, label: str) -> Path:
    path = (repo / raw).resolve() if not Path(raw).is_absolute() else Path(raw).resolve()
    try:
        path.relative_to(repo)
    except ValueError as error:
        raise ValueError(f"{label} must stay below the repository root") from error
    return path


def sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def verify_source_manifest(
    path: Path, repo: Path, release_candidate: str, runner_path: str
) -> dict[str, str]:
    try:
        path.relative_to(repo)
    except ValueError as error:
        raise ValueError("source manifest must stay below the RC worktree") from error
    exact_digest = sha256_file(path)
    if exact_digest != release_candidate:
        raise ValueError("source manifest exact-byte digest does not equal releaseCandidate")
    source = read_json(path)
    if source.get("schemaVersion") != "public-service-source-manifest/v1":
        raise ValueError("source manifest schema is invalid")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    if source.get("sourceTreeCommit") != head:
        raise ValueError("source manifest sourceTreeCommit does not equal HEAD")
    entries = source.get("files")
    if not isinstance(entries, list) or not entries:
        raise ValueError("source manifest files must be a non-empty array")
    seen: set[str] = set()
    runner_digest: str | None = None
    for entry in entries:
        relative = str(entry.get("path", "")).replace("\\", "/") if isinstance(entry, dict) else ""
        expected = str(entry.get("sha256", "")) if isinstance(entry, dict) else ""
        if not relative or relative in seen or not RC_ID.fullmatch(expected):
            raise ValueError("source manifest contains an invalid or duplicate file entry")
        absolute = resolve_child(repo, relative, "source manifest file")
        if relative.startswith(("artifacts/", "app_data/", ".git/")):
            raise ValueError("source manifest file escaped the source boundary")
        if sha256_file(absolute) != expected:
            raise ValueError(f"source manifest file digest mismatch: {relative}")
        seen.add(relative)
        if relative == runner_path:
            runner_digest = expected
    if runner_digest is None:
        raise ValueError(f"source manifest omits evidence runner: {runner_path}")
    return {
        "runnerPath": runner_path,
        "runnerSha256": runner_digest,
        "sourceManifestDigest": exact_digest,
    }


def load_manifest(
    path: Path, source_manifest_path: Path, repo: Path
) -> tuple[dict[str, Any], Path, Path, Path, dict[str, str]]:
    value = read_json(path)
    if value.get("schemaVersion") != MANIFEST_SCHEMA:
        raise ValueError(f"schemaVersion must be {MANIFEST_SCHEMA}")
    if value.get("evidenceClass") != "product-path":
        raise ValueError("evidenceClass must be product-path")
    release_candidate = str(value.get("releaseCandidate", "")).strip()
    if not RC_ID.fullmatch(release_candidate):
        raise ValueError("releaseCandidate must be a sha256:<64 lowercase hex> source-manifest id")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    if value.get("sourceCommit") != head:
        raise ValueError("sourceCommit must equal the checked-out HEAD")
    provenance = verify_source_manifest(
        source_manifest_path,
        repo,
        release_candidate,
        "scripts/public-service-soak-resource.py",
    )
    duration = value.get("durationSeconds")
    interval = value.get("sampleIntervalSeconds")
    if not isinstance(duration, int) or duration < 86_400:
        raise ValueError("durationSeconds must be at least 86400")
    if not isinstance(interval, int) or interval < 5 or interval > 300:
        raise ValueError("sampleIntervalSeconds must be 5..300")
    threshold = value.get("queueStarvationThresholdSeconds")
    if not isinstance(threshold, int) or threshold < interval or threshold > 3600:
        raise ValueError("queueStarvationThresholdSeconds must be sampleIntervalSeconds..3600")
    minimum_operations = value.get("minimumCompletedOperations")
    if not isinstance(minimum_operations, int) or minimum_operations < 1:
        raise ValueError("minimumCompletedOperations must be a positive integer")
    policy = value.get("resourcePolicy")
    if not isinstance(policy, dict):
        raise ValueError("resourcePolicy is required")
    for key in ("memoryPolicyLimitBytes", "handlePolicyLimit"):
        if not isinstance(policy.get(key), int) or policy[key] <= 0:
            raise ValueError(f"resourcePolicy.{key} must be a positive integer")
    workers = value.get("workers")
    if not isinstance(workers, list) or not workers:
        raise ValueError("workers must identify at least one owned process")
    seen: set[int] = set()
    for worker in workers:
        pid = worker.get("pid") if isinstance(worker, dict) else None
        role = worker.get("role") if isinstance(worker, dict) else None
        marker = worker.get("commandLineContains") if isinstance(worker, dict) else None
        if not isinstance(pid, int) or pid <= 0 or pid in seen:
            raise ValueError("worker pids must be unique positive integers")
        if not isinstance(role, str) or not role.strip() or not isinstance(marker, str) or not marker:
            raise ValueError("every worker requires role and commandLineContains")
        seen.add(pid)
    state_path = resolve_child(repo, str(value.get("statePath", "")), "statePath")
    evidence_path = resolve_child(repo, str(value.get("evidencePath", "")), "evidencePath")
    database_path = Path(str(value.get("sqliteDatabasePath", ""))).resolve()
    isolated_root = Path(str(value.get("isolatedRoot", ""))).resolve()
    try:
        database_path.relative_to(isolated_root)
    except ValueError as error:
        raise ValueError("sqliteDatabasePath must stay below isolatedRoot") from error
    if state_path == repo or evidence_path == repo or database_path == isolated_root:
        raise ValueError("state, evidence and database paths must name files")
    return value, state_path, evidence_path, database_path, provenance


def windows_processes(root_pids: list[int]) -> list[dict[str, Any]]:
    roots = ",".join(str(pid) for pid in root_pids)
    script = rf"""
$roots=@({roots}); $all=@(Get-CimInstance Win32_Process); $ids=[System.Collections.Generic.HashSet[int]]::new()
foreach($id in $roots){{[void]$ids.Add([int]$id)}}
do{{$changed=$false; foreach($p in $all){{if($ids.Contains([int]$p.ParentProcessId)-and $ids.Add([int]$p.ProcessId)){{$changed=$true}}}}}}while($changed)
$rows=@(); foreach($p in $all|Where-Object{{$ids.Contains([int]$_.ProcessId)}}){{
  $gp=Get-Process -Id $p.ProcessId -ErrorAction SilentlyContinue
  if($null-ne $gp){{$rows+=[ordered]@{{pid=[int]$p.ProcessId;parentPid=[int]$p.ParentProcessId;workingSetBytes=[int64]$gp.WorkingSet64;privateBytes=[int64]$gp.PrivateMemorySize64;handles=[int64]$gp.HandleCount;identitySha256='';commandLine=[string]$p.CommandLine}}}}
}}
$rows|ConvertTo-Json -Compress
"""
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    raw = result.stdout.strip()
    rows = [] if not raw else json.loads(raw)
    if isinstance(rows, dict):
        rows = [rows]
    for row in rows:
        command = str(row.pop("commandLine", ""))
        row["identitySha256"] = hashlib.sha256(command.encode("utf-8")).hexdigest()
        row["_commandLine"] = command
    return rows


def linux_processes(root_pids: list[int]) -> list[dict[str, Any]]:
    raw: dict[int, tuple[int, str]] = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text(encoding="utf-8").split()
            command = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8")
            raw[int(entry.name)] = (int(stat[3]), command)
        except (FileNotFoundError, PermissionError, ValueError):
            continue
    selected = set(root_pids)
    changed = True
    while changed:
        before = len(selected)
        selected.update(pid for pid, (parent, _) in raw.items() if parent in selected)
        changed = len(selected) != before
    rows = []
    for pid in selected:
        try:
            values = {}
            for line in (Path("/proc") / str(pid) / "status").read_text().splitlines():
                key, _, rest = line.partition(":")
                values[key] = rest.strip().split()[0]
            fd_count = len(list((Path("/proc") / str(pid) / "fd").iterdir()))
            parent, command = raw[pid]
            rows.append({
                "pid": pid, "parentPid": parent,
                "workingSetBytes": int(values.get("VmRSS", 0)) * 1024,
                "privateBytes": int(values.get("VmSize", 0)) * 1024,
                "handles": fd_count,
                "identitySha256": hashlib.sha256(command.encode()).hexdigest(),
                "_commandLine": command,
            })
        except (FileNotFoundError, PermissionError, KeyError, ValueError):
            continue
    return rows


def sample_processes(workers: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    roots = [worker["pid"] for worker in workers]
    rows = windows_processes(roots) if os.name == "nt" else linux_processes(roots)
    by_pid = {row["pid"]: row for row in rows}
    errors = []
    for worker in workers:
        row = by_pid.get(worker["pid"])
        if row is None:
            errors.append(f"owned_pid_missing:{worker['role']}")
        elif worker["commandLineContains"].lower() not in row["_commandLine"].lower():
            errors.append(f"owned_pid_identity_mismatch:{worker['role']}")
    for row in rows:
        row.pop("_commandLine", None)
    return rows, errors


def parse_database_time(value: Any) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc)


def queue_sample(database_path: Path, threshold: int, started_at: str) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    with sqlite3.connect(f"file:{database_path.as_posix()}?mode=ro", uri=True, timeout=10) as db:
        ready_count, oldest = db.execute(
            "SELECT COUNT(*), MIN(created_at) FROM workspace_operations "
            "WHERE state IN (?) AND (next_attempt_at IS NULL OR next_attempt_at <= ?)",
            (READY_STATES[0], now.replace(tzinfo=None).isoformat()),
        ).fetchone()
        pending_outbox, oldest_outbox = db.execute(
            "SELECT COUNT(*), MIN(available_at) FROM workspace_outbox "
            "WHERE status IN ('pending','delivering')"
        ).fetchone()
        succeeded, failed = db.execute(
            "SELECT SUM(CASE WHEN state = 'succeeded' THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN state IN ('failed','dead_letter') THEN 1 ELSE 0 END) "
            "FROM workspace_operations WHERE kind IN "
            "('presentation.generate','export.pptx','export.pdf') AND created_at >= ?",
            (started_at.removesuffix("Z"),),
        ).fetchone()
        resource_rows = db.execute(
            "SELECT result FROM workspace_operations WHERE kind IN ('export.pptx','export.pdf') "
            "AND state = 'succeeded' AND created_at >= ?",
            (started_at.removesuffix("Z"),),
        ).fetchall()
        completed_exports = int(
            db.execute(
                "SELECT COUNT(*) FROM workspace_operations WHERE kind IN ('export.pptx','export.pdf') "
                "AND state = 'succeeded' AND created_at >= ?",
                (started_at.removesuffix("Z"),),
            ).fetchone()[0]
        )
    resource_results = []
    for (raw_result,) in resource_rows:
        try:
            parsed = json.loads(raw_result) if isinstance(raw_result, str) else raw_result
        except json.JSONDecodeError:
            parsed = None
        worker_samples = parsed.get("worker_resource_samples") if isinstance(parsed, dict) else None
        sample_roles = {
            sample.get("role") for sample in worker_samples
            if isinstance(sample, dict)
            and sample.get("resourceLimitsEnforced") is True
            and sample.get("recycledWithinBaseline") is True
            and sample.get("finalProcessCount") == 0
            and all(
                isinstance(sample.get(key), int) and int(sample[key]) >= 0
                for key in ("peakPrivateBytes", "peakHandleCount", "peakProcessCount", "peakCpuMillis")
            )
            and isinstance(sample.get("resourceLimits"), dict)
            and all(
                isinstance(sample["resourceLimits"].get(key), int)
                and int(sample["resourceLimits"][key]) > 0
                for key in REQUIRED_LIMITS
            )
        } if isinstance(worker_samples, list) else set()
        if (
            isinstance(parsed, dict)
            and parsed.get("resource_limits_enforced") is True
            and isinstance(parsed.get("worker_peak_memory_bytes"), int)
            and isinstance(parsed.get("worker_peak_handle_count"), int)
            and isinstance(parsed.get("worker_peak_process_count"), int)
            and isinstance(parsed.get("worker_peak_cpu_millis"), int)
            and isinstance(parsed.get("worker_recycle_failure_count"), int)
            and isinstance(parsed.get("worker_execution_count"), int)
            and sample_roles >= {"render", "export"}
        ):
            resource_results.append(parsed)
    oldest_at = parse_database_time(oldest)
    outbox_at = parse_database_time(oldest_outbox)
    oldest_age = max(0.0, (now - oldest_at).total_seconds()) if oldest_at else 0.0
    outbox_age = max(0.0, (now - outbox_at).total_seconds()) if outbox_at else 0.0
    return {
        "readyOperationCount": int(ready_count),
        "oldestReadyAgeSeconds": oldest_age,
        "pendingOutboxCount": int(pending_outbox),
        "oldestPendingOutboxAgeSeconds": outbox_age,
        "starved": oldest_age > threshold,
        "outboxStarved": outbox_age > threshold,
        "completedProductPathOperationCount": int(succeeded or 0),
        "completedResourceOperationCount": completed_exports,
        "failedProductPathOperationCount": int(failed or 0),
        "resourceMetricOperationCount": len(resource_results),
        "missingResourceMetricOperationCount": completed_exports - len(resource_results),
        "resourceLimitFailureCount": sum(item.get("resource_limits_enforced") is not True for item in resource_results),
        "workerRecycleFailureCount": sum(int(item.get("worker_recycle_failure_count", 0)) for item in resource_results),
        "isolatedWorkerPeakMemoryBytes": max((int(item.get("worker_peak_memory_bytes", 0)) for item in resource_results), default=0),
        "isolatedWorkerPeakHandleCount": max((int(item.get("worker_peak_handle_count", 0)) for item in resource_results), default=0),
    }


def limit_attestation(
    manifest: dict[str, Any],
    repo: Path,
    source_manifest_path: Path,
    source_provenance: dict[str, str],
) -> tuple[bool, str | None]:
    raw = str(manifest.get("resourceLimitAttestationPath", "")).strip()
    if not raw:
        return False, "resource_limit_attestation_missing"
    try:
        value = read_json(resolve_child(repo, raw, "resourceLimitAttestationPath"))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return False, f"resource_limit_attestation_invalid:{type(error).__name__}"
    controls = set(value.get("enforcedControls", []))
    source = read_json(source_manifest_path)
    source_files = {
        str(item.get("path", "")).replace("\\", "/"): str(item.get("sha256", ""))
        for item in source.get("files", []) if isinstance(item, dict)
    }
    attestation_provenance = value.get("provenance")
    attestation_provenance = attestation_provenance if isinstance(attestation_provenance, dict) else {}
    attestation_runner_path = str(attestation_provenance.get("runnerPath", "")).replace("\\", "/")
    runner_digest = str(attestation_provenance.get("runnerSha256", ""))
    try:
        runner_current = sha256_file(resolve_child(repo, attestation_runner_path, "attestation runner"))
    except (OSError, ValueError):
        runner_current = ""
    samples = value.get("actualSamples")
    expected_layer = "windows-job-object" if os.name == "nt" else "posix-rlimit"
    valid_samples = isinstance(samples, list) and bool(samples)
    roles: set[str] = set()
    if valid_samples:
        for sample in samples:
            if not isinstance(sample, dict):
                valid_samples = False
                break
            role = sample.get("role")
            if role not in {"render", "export"}:
                valid_samples = False
                break
            roles.add(str(role))
            numeric = ("peakPrivateBytes", "peakHandleCount", "peakProcessCount", "peakCpuMillis")
            sample_limits = sample.get("resourceLimits")
            if (
                any(not isinstance(sample.get(key), int) or int(sample[key]) < 0 for key in numeric)
                or not isinstance(sample_limits, dict)
                or any(
                    not isinstance(sample_limits.get(key), int) or int(sample_limits[key]) <= 0
                    for key in REQUIRED_LIMITS
                )
                or sample.get("resourceLimitsEnforced") is not True
                or sample.get("enforcementLayer") != expected_layer
                or sample.get("recycledWithinBaseline") is not True
                or sample.get("finalProcessCount") != 0
            ):
                valid_samples = False
                break
    valid = (
        value.get("schemaVersion") == LIMIT_SCHEMA
        and value.get("releaseCandidate") == manifest["releaseCandidate"]
        and value.get("sourceDigest") == manifest["sourceCommit"]
        and value.get("evidenceClass") == "product-path"
        and value.get("enforcementLayer") == expected_layer
        and REQUIRED_LIMITS <= controls
        and value.get("enforced") is True
        and value.get("valid") is True
        and value.get("resourceLimitsEnforced") is True
        and set(value.get("workerRoles", [])) >= {"render", "export"}
        and roles >= {"render", "export"}
        and valid_samples
        and attestation_provenance.get("sourceManifestDigest") == source_provenance["sourceManifestDigest"]
        and source_files.get(attestation_runner_path) == runner_digest
        and runner_current == runner_digest
    )
    return valid, None if valid else "resource_limit_attestation_contract_failed"


def totals(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "workingSetBytes": sum(int(row["workingSetBytes"]) for row in rows),
        "privateBytes": sum(int(row["privateBytes"]) for row in rows),
        "handles": sum(int(row["handles"]) for row in rows),
        "processCount": len(rows),
    }


def canonical_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def load_state_ledger(
    path: Path,
    *,
    manifest: dict[str, Any],
    manifest_digest: str,
    provenance: dict[str, str],
) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    previous_hash = "sha256:" + "0" * 64
    if path.exists():
        for sequence, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"soak state ledger line {sequence + 1} is invalid") from error
            if not isinstance(entry, dict):
                raise ValueError("soak state ledger entry must be an object")
            claimed_hash = entry.pop("entryHash", None)
            computed_hash = "sha256:" + hashlib.sha256(canonical_bytes(entry)).hexdigest()
            if (
                entry.get("schemaVersion") != STATE_ENTRY_SCHEMA
                or entry.get("sequence") != sequence
                or entry.get("previousHash") != previous_hash
                or claimed_hash != computed_hash
            ):
                raise ValueError(f"soak state hash chain failed at sequence {sequence}")
            entry["entryHash"] = claimed_hash
            entries.append(entry)
            previous_hash = computed_hash
    if entries:
        binding = entries[0].get("payload", {})
        if (
            binding.get("type") != "start"
            or binding.get("releaseCandidate") != manifest["releaseCandidate"]
            or binding.get("manifestDigest") != manifest_digest
            or binding.get("sourceManifestDigest") != provenance["sourceManifestDigest"]
            or binding.get("runnerSha256") != provenance["runnerSha256"]
        ):
            raise ValueError("existing soak state binding does not match this exact RC")
    return {"entries": entries, "headHash": previous_hash}


def append_state_entry(path: Path, ledger: dict[str, Any], payload: dict[str, Any]) -> None:
    entry = {
        "schemaVersion": STATE_ENTRY_SCHEMA,
        "sequence": len(ledger["entries"]),
        "previousHash": ledger["headHash"],
        "payload": payload,
    }
    entry_hash = "sha256:" + hashlib.sha256(canonical_bytes(entry)).hexdigest()
    entry["entryHash"] = entry_hash
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(entry, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    ledger["entries"].append(entry)
    ledger["headHash"] = entry_hash


def materialize_state(ledger: dict[str, Any]) -> dict[str, Any]:
    samples = [
        entry["payload"] for entry in ledger["entries"]
        if entry.get("payload", {}).get("type") == "sample"
    ]
    starts = [
        entry["payload"] for entry in ledger["entries"]
        if entry.get("payload", {}).get("type") == "start"
    ]
    starvation = outbox_starvation = 0
    queue_active = outbox_active = False
    worker_failures = 0
    for sample in samples:
        queue = sample["queue"]
        if queue["starved"] and not queue_active:
            starvation += 1
        if queue["outboxStarved"] and not outbox_active:
            outbox_starvation += 1
        queue_active = queue["starved"]
        outbox_active = queue["outboxStarved"]
        worker_failures += len(sample.get("workerErrors", []))
    elapsed_ns = sum(int(sample.get("deltaMonotonicNanoseconds", 0)) for sample in samples)
    return {
        "schemaVersion": STATE_SCHEMA,
        "startedAt": starts[0]["observedAt"] if starts else utc_now(),
        "updatedAt": samples[-1]["observedAt"] if samples else (starts[-1]["observedAt"] if starts else utc_now()),
        "verifiedElapsedSeconds": elapsed_ns / 1_000_000_000,
        "samples": samples,
        "starvationEpisodes": starvation,
        "outboxStarvationEpisodes": outbox_starvation,
        "workerRecycleFailureCount": worker_failures,
        "ledgerHeadSha256": ledger["headHash"],
        "ledgerEntryCount": len(ledger["entries"]),
    }


def summarize(
    manifest: dict[str, Any],
    state: dict[str, Any],
    limits: tuple[bool, str | None],
    provenance: dict[str, str],
) -> dict[str, Any]:
    samples = state["samples"]
    policy = manifest["resourcePolicy"]
    window = min(len(samples), int(policy.get("baselineWindowSamples", 5)))
    baseline = samples[:window]
    final = samples[-window:]
    baseline_memory = max((item["totals"]["privateBytes"] for item in baseline), default=0)
    baseline_handles = max((item["totals"]["handles"] for item in baseline), default=0)
    final_memory = max((item["totals"]["privateBytes"] for item in final), default=0)
    final_handles = max((item["totals"]["handles"] for item in final), default=0)
    memory_recovery_limit = int(baseline_memory * float(policy.get("recoveryRatio", 1.2))) + int(policy.get("recoveryAllowanceBytes", 67_108_864))
    handle_recovery_limit = int(baseline_handles * float(policy.get("recoveryRatio", 1.2))) + int(policy.get("recoveryHandleAllowance", 32))
    completed_operations = samples[-1]["queue"]["completedResourceOperationCount"] if samples else 0
    missing_resource_metrics = samples[-1]["queue"]["missingResourceMetricOperationCount"] if samples else 0
    direct_resource_evidence = bool(samples) and completed_operations > 0 and missing_resource_metrics == 0
    baseline_recovered = final_memory <= memory_recovery_limit and final_handles <= handle_recovery_limit
    clean_queue = state["starvationEpisodes"] == 0 and state["outboxStarvationEpisodes"] == 0
    clean_resources = bool(
        limits[0] and direct_resource_evidence
        and samples[-1]["queue"]["resourceLimitFailureCount"] == 0
        and samples[-1]["queue"]["workerRecycleFailureCount"] == 0
        and state["workerRecycleFailureCount"] == 0
    ) if samples else False
    completed = (
        state["verifiedElapsedSeconds"] >= manifest["durationSeconds"]
        and completed_operations >= manifest["minimumCompletedOperations"]
        and clean_resources and clean_queue and baseline_recovered
    )
    return {
        "schemaVersion": EVIDENCE_SCHEMA,
        "releaseCandidate": manifest["releaseCandidate"],
        "sourceDigest": manifest["sourceCommit"],
        "provenance": provenance,
        "evidenceClass": "product-path",
        "completed": completed,
        "durationSeconds": int(state["verifiedElapsedSeconds"]),
        "requestedDurationSeconds": manifest["durationSeconds"],
        "sampleCount": len(samples),
        "completedProductPathOperationCount": completed_operations,
        "missingResourceMetricOperationCount": missing_resource_metrics,
        "minimumCompletedOperations": manifest["minimumCompletedOperations"],
        "failedProductPathOperationCount": samples[-1]["queue"]["failedProductPathOperationCount"] if samples else 0,
        "startedAt": state["startedAt"],
        "observedThrough": state["updatedAt"],
        "memoryRecoveredWithinBaseline": baseline_recovered,
        "handlesRecoveredWithinBaseline": baseline_recovered,
        "finalWorkerMemoryBytes": final_memory,
        "memoryPolicyLimitBytes": int(policy["memoryPolicyLimitBytes"]),
        "finalWorkerHandleCount": final_handles,
        "handlePolicyLimit": int(policy["handlePolicyLimit"]),
        "queueStarvationCount": state["starvationEpisodes"],
        "outboxStarvationCount": state["outboxStarvationEpisodes"],
        "resourceLimitsEnforced": clean_resources,
        "resourceLimitAttestationValid": limits[0],
        "resourceLimitEvidenceError": None if clean_resources else limits[1] or "product_path_resource_metrics_missing_or_invalid",
        "workerRecycleFailureCount": state["workerRecycleFailureCount"] + (samples[-1]["queue"]["workerRecycleFailureCount"] if samples else 0),
        "maximumObserved": {
            "privateBytes": max((item["totals"]["privateBytes"] for item in samples), default=0),
            "handles": max((item["totals"]["handles"] for item in samples), default=0),
            "processCount": max((item["totals"]["processCount"] for item in samples), default=0),
            "oldestReadyAgeSeconds": max((item["queue"]["oldestReadyAgeSeconds"] for item in samples), default=0),
            "isolatedWorkerPrivateBytes": max((item["queue"]["isolatedWorkerPeakMemoryBytes"] for item in samples), default=0),
            "isolatedWorkerHandles": max((item["queue"]["isolatedWorkerPeakHandleCount"] for item in samples), default=0),
        },
        "elapsedTimePolicy": "only monotonic time observed by a live runner is accumulated; restart gaps are excluded",
        "stateIntegrity": {
            "schemaVersion": STATE_ENTRY_SCHEMA,
            "appendOnlyHashChain": True,
            "entryCount": state["ledgerEntryCount"],
            "headSha256": state["ledgerHeadSha256"],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--source-manifest", required=True, type=Path)
    parser.add_argument("--once", action="store_true", help="take one sample without waiting")
    parser.add_argument("--status", action="store_true", help="render current evidence without sampling")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    manifest, state_path, evidence_path, database_path, provenance = load_manifest(
        args.manifest.resolve(), args.source_manifest.resolve(), repo
    )
    manifest_digest = sha256_json(manifest)
    ledger = load_state_ledger(
        state_path,
        manifest=manifest,
        manifest_digest=manifest_digest,
        provenance=provenance,
    )
    if not ledger["entries"]:
        append_state_entry(
            state_path,
            ledger,
            {
                "type": "start",
                "observedAt": utc_now(),
                "releaseCandidate": manifest["releaseCandidate"],
                "manifestDigest": manifest_digest,
                "sourceManifestDigest": provenance["sourceManifestDigest"],
                "runnerSha256": provenance["runnerSha256"],
            },
        )
    state = materialize_state(ledger)
    limits = limit_attestation(
        manifest, repo, args.source_manifest.resolve(), provenance
    )
    if args.status:
        evidence = summarize(manifest, state, limits, provenance)
        atomic_json(evidence_path, evidence)
        print(json.dumps({"statePath": str(state_path), "evidencePath": str(evidence_path), "completed": evidence["completed"]}))
        return 0

    run_id = str(uuid.uuid4())
    previous_tick = time.monotonic_ns()
    append_state_entry(
        state_path,
        ledger,
        {"type": "run_start", "runId": run_id, "observedAt": utc_now(), "monotonicNanoseconds": previous_tick},
    )
    while True:
        processes, errors = sample_processes(manifest["workers"])
        queue = queue_sample(
            database_path,
            int(manifest["queueStarvationThresholdSeconds"]),
            state["startedAt"],
        )
        current_tick = time.monotonic_ns()
        delta_ns = max(0, current_tick - previous_tick)
        previous_tick = current_tick
        append_state_entry(state_path, ledger, {
            "type": "sample",
            "runId": run_id,
            "observedAt": utc_now(),
            "monotonicNanoseconds": current_tick,
            "deltaMonotonicNanoseconds": delta_ns,
            "totals": totals(processes),
            "queue": queue,
            "workerErrors": errors,
        })
        state = materialize_state(ledger)
        evidence = summarize(manifest, state, limits, provenance)
        atomic_json(evidence_path, evidence)
        elapsed_complete = state["verifiedElapsedSeconds"] >= manifest["durationSeconds"]
        if args.once or elapsed_complete:
            break
        time.sleep(manifest["sampleIntervalSeconds"])
    print(json.dumps({
        "statePath": str(state_path),
        "evidencePath": str(evidence_path),
        "completed": evidence["completed"],
        "verifiedElapsedSeconds": state["verifiedElapsedSeconds"],
        "sampleCount": len(state["samples"]),
    }))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError, sqlite3.Error, subprocess.SubprocessError) as error:
        print(f"public-service soak failed: {error}", file=sys.stderr)
        raise SystemExit(1)
