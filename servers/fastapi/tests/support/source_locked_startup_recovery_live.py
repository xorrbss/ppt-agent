"""Seed and verify production-lifespan source-locked startup recovery."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time

import httpx

from services.source_locked.admission import admit_source_locked_v1
from services.source_locked.contracts import GenerationMode, PageCountPolicy
from services.source_locked.digests import idempotency_payload_digest, sha256_digest
from services.source_locked.job_store import DurableSourceLockedJobStore


def contract_and_digest(path: Path):
    payload = path.read_bytes()
    contract = admit_source_locked_v1(
        [path.name],
        generation_mode=GenerationMode.SOURCE_LOCKED,
        page_count_policy=PageCountPolicy.SOURCE,
        preserve_all_image_occurrences=True,
    )
    return payload, contract, idempotency_payload_digest(contract, sha256_digest(payload))


def rows(database_path: Path, job_ids: list[str]) -> dict[str, dict[str, object]]:
    placeholders = ",".join("?" for _ in job_ids)
    with sqlite3.connect(database_path, timeout=30) as connection:
        connection.row_factory = sqlite3.Row
        selected = connection.execute(
            f"""
            SELECT job_id, owner_id, status, cancel_requested, commit_started,
                   runner_token, lease_expires_at, result_json, error_json
            FROM generation_jobs_v1 WHERE job_id IN ({placeholders})
            """,
            job_ids,
        ).fetchall()
    return {
        row["job_id"]: {
            "owner_id": row["owner_id"],
            "status": row["status"],
            "cancel_requested": bool(row["cancel_requested"]),
            "commit_started": bool(row["commit_started"]),
            "runner_token_present": row["runner_token"] is not None,
            "lease_expires_at": row["lease_expires_at"],
            "result_present": row["result_json"] is not None,
            "error_present": row["error_json"] is not None,
        }
        for row in selected
    }


def seed(args: argparse.Namespace) -> None:
    os.environ["APP_DATA_DIRECTORY"] = str(args.app_data.resolve())
    payload, contract, digest = contract_and_digest(args.fixture)
    store = DurableSourceLockedJobStore.from_app_data()
    queued, queued_created = store.create(
        owner_id=args.owner_id,
        idempotency_key=f"{args.key_prefix}-queued",
        idempotency_payload_digest=digest,
        admission_contract=contract,
        source_bytes=payload,
    )
    stale, stale_created = store.create(
        owner_id=args.owner_id,
        idempotency_key=f"{args.key_prefix}-stale-running",
        idempotency_payload_digest=digest,
        admission_contract=contract,
        source_bytes=payload,
    )
    runner_token = store.claim(owner_id=args.owner_id, job_id=stale.job_id)
    if runner_token is None:
        raise AssertionError("could not claim the stale-running recovery fixture")
    expired = "2000-01-01T00:00:00+00:00"
    with sqlite3.connect(store.database_path, timeout=30) as connection:
        updated = connection.execute(
            """
            UPDATE generation_jobs_v1
            SET lease_expires_at = ?, updated_at = ?
            WHERE job_id = ? AND owner_id = ? AND status = 'running'
              AND runner_token = ?
            """,
            (
                expired,
                datetime.now(timezone.utc).isoformat(),
                stale.job_id,
                args.owner_id,
                runner_token,
            ),
        )
        connection.commit()
    if updated.rowcount != 1:
        raise AssertionError("could not expire the stale-running recovery lease")
    job_ids = [queued.job_id, stale.job_id]
    seeded_rows = rows(store.database_path, job_ids)
    if seeded_rows[queued.job_id]["status"] != "queued":
        raise AssertionError("queued recovery fixture is not queued")
    if (
        seeded_rows[stale.job_id]["status"] != "running"
        or not seeded_rows[stale.job_id]["runner_token_present"]
        or seeded_rows[stale.job_id]["lease_expires_at"] != expired
    ):
        raise AssertionError("stale-running recovery fixture is not expired running")
    print(
        json.dumps(
            {
                "database_path": str(store.database_path),
                "fixture": str(args.fixture.resolve()),
                "fixture_bytes": len(payload),
                "fixture_sha256": hashlib.sha256(payload).hexdigest(),
                "owner_id": args.owner_id,
                "queued_job_id": queued.job_id,
                "queued_created": queued_created,
                "stale_job_id": stale.job_id,
                "stale_created": stale_created,
                "seeded_rows": seeded_rows,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


def require_status(response: httpx.Response, expected: int, label: str) -> None:
    if response.status_code != expected:
        raise AssertionError(
            f"{label}: expected HTTP {expected}, got {response.status_code}: "
            f"{response.text[:1000]}"
        )


def verify(args: argparse.Namespace) -> None:
    database_path = (
        args.app_data.resolve() / "source_locked" / "v1" / "source_locked_jobs.sqlite3"
    )
    fixture = args.fixture.read_bytes()
    expected_hash = hashlib.sha256(fixture).hexdigest()
    job_ids = [args.queued_job_id, args.stale_job_id]
    transitions: dict[str, list[str]] = {job_id: [] for job_id in job_ids}
    observed_details: dict[str, dict[str, object]] = {}
    started = time.monotonic()
    while time.monotonic() - started < args.timeout_seconds:
        current = rows(database_path, job_ids)
        if set(current) != set(job_ids):
            raise AssertionError("one or more seeded recovery jobs disappeared")
        for job_id in job_ids:
            status = str(current[job_id]["status"])
            if not transitions[job_id] or transitions[job_id][-1] != status:
                transitions[job_id].append(status)
        observed_details = current
        if all(current[job_id]["status"] in {"succeeded", "failed", "cancelled"} for job_id in job_ids):
            break
        time.sleep(0.02)
    else:
        raise AssertionError(f"startup recovery did not become terminal: {transitions}")
    if any(observed_details[job_id]["status"] != "succeeded" for job_id in job_ids):
        raise AssertionError(
            f"startup maintenance did not succeed every seeded job: {observed_details}"
        )

    client = httpx.Client(
        base_url=args.base_url.rstrip("/"),
        timeout=httpx.Timeout(180.0, connect=10.0),
    )
    try:
        login = client.post(
            "/api/v1/auth/login",
            json={"username": args.username, "password": args.password},
        )
        require_status(login, 200, "startup recovery owner login")
        http_evidence: dict[str, dict[str, object]] = {}
        for job_id in job_ids:
            status_response = client.get(
                f"/api/v1/ppt/source-locked/v1/jobs/{job_id}"
            )
            require_status(status_response, 200, "startup recovery owner status")
            body = status_response.json()
            if body["status"] != "succeeded":
                raise AssertionError("owner GET did not return recovered success")
            artifact = body["result"]["artifact"]
            download = client.get(artifact["download_url"])
            require_status(download, 200, "startup recovery artifact download")
            if download.content != fixture:
                raise AssertionError("recovered PPTX artifact is not byte-exact")
            downloaded_hash = hashlib.sha256(download.content).hexdigest()
            if downloaded_hash != expected_hash:
                raise AssertionError("recovered PPTX artifact digest changed")
            http_evidence[job_id] = {
                "status_http": status_response.status_code,
                "status": body["status"],
                "artifact_http": download.status_code,
                "artifact_id": artifact["artifact_id"],
                "artifact_bytes": len(download.content),
                "artifact_sha256": downloaded_hash,
                "byte_exact": download.content == fixture,
            }
    finally:
        client.close()
    print(
        json.dumps(
            {
                "database_path": str(database_path),
                "fixture_bytes": len(fixture),
                "fixture_sha256": expected_hash,
                "startup_sqlite_only_seconds": round(time.monotonic() - started, 3),
                "source_http_before_terminal": False,
                "sqlite_transitions": transitions,
                "terminal_rows_before_source_http": observed_details,
                "owner_http_after_terminal": http_evidence,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    seed_parser = subparsers.add_parser("seed")
    seed_parser.add_argument("--app-data", type=Path, required=True)
    seed_parser.add_argument("--fixture", type=Path, required=True)
    seed_parser.add_argument("--owner-id", required=True)
    seed_parser.add_argument("--key-prefix", required=True)
    seed_parser.set_defaults(handler=seed)

    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--app-data", type=Path, required=True)
    verify_parser.add_argument("--base-url", required=True)
    verify_parser.add_argument("--fixture", type=Path, required=True)
    verify_parser.add_argument("--username", required=True)
    verify_parser.add_argument("--password", required=True)
    verify_parser.add_argument("--queued-job-id", required=True)
    verify_parser.add_argument("--stale-job-id", required=True)
    verify_parser.add_argument("--timeout-seconds", type=float, default=60.0)
    verify_parser.set_defaults(handler=verify)
    args = parser.parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
