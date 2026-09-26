from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import os
from pathlib import Path

from pptx import Presentation as PptxPresentation

from tests.support.authored_checkpoint_public_fixture import set_checkpoint_environment


from tests.support.authored_checkpoint_process_harness import (
    _COMPLETION_TIMEOUT,
    _CONDITION_TIMEOUT,
    _CRASHED_LEASE_SECONDS,
    _LEASE_EXPIRY_TIMEOUT,
    _RESUMING_LEASE_SECONDS,
    _as_utc,
    _events,
    _kill_process_tree,
    _one,
    _query,
    _request,
    _start_server,
    _wait_until,
)

def test_hard_process_kill_expires_lease_and_resumes_same_job(tmp_path):
    """A new OS process resumes durable checkpoints without duplicate success."""

    server_root = Path(__file__).resolve().parents[2]
    python = server_root / ".venv" / "Scripts" / "python.exe"
    if not python.exists():
        python = Path(os.environ.get("PYTHON", "python"))

    database_path = tmp_path / "process-restart.db"
    event_log = tmp_path / "external-calls.jsonl"
    common_env = os.environ.copy()
    common_env.update(
        {
            "DATABASE_URL": f"sqlite:///{database_path.as_posix()}",
            "AUTHORED_SUBPROCESS_EVENT_LOG": str(event_log),
            "AUTHORED_SUBPROCESS_SLIDES": "6",
            "AUTHORED_TEST_FAILURES_ENABLED": "false",
            "MIGRATE_DATABASE_ON_STARTUP": "false",
            "PYTHONUNBUFFERED": "1",
        }
    )
    set_checkpoint_environment(common_env, tmp_path)
    payload = {
        "content": "hard process checkpoint recovery deck",
        "template": "authored",
        "strategy": "authored",
        "n_slides": 6,
        "include_title_slide": False,
        "vision_qa": False,
        "export_as": "pptx",
    }
    headers = {
        "Idempotency-Key": "hard-process-authored-resume",
        "Cookie": "test_session=process-restart",
    }

    first = None
    second = None
    first_runtime_pid = None
    second_runtime_pid = None
    first_output = ""
    second_output = ""
    try:
        first_env = common_env | {
            "AUTHORED_SUBPROCESS_BLOCK_RENDER_SLIDE": "2",
            "AUTHORED_SUBPROCESS_LEASE_SECONDS": _CRASHED_LEASE_SECONDS,
            "WORKSPACE_OPERATION_LEASE_SECONDS": _CRASHED_LEASE_SECONDS,
            "WORKSPACE_OPERATION_HEARTBEAT_SECONDS": "1",
        }
        first, first_url = _start_server(
            python=python,
            env=first_env,
            description="the crashing runtime",
        )
        first_runtime_pid = _wait_until(
            lambda: next(
                (event["pid"] for event in _events(event_log) if event["event"] == "startup"),
                None,
            ),
            timeout=_CONDITION_TIMEOUT,
            description="the crashing runtime to log its startup pid",
        )
        response = _request(
            "POST",
            f"{first_url}/api/v1/ppt/presentation/generate/async",
            json=payload,
            headers=headers,
            description="the initial authored generate request",
        )
        assert response.status_code == 202, response.text
        task_id = response.json()["id"]

        _wait_until(
            lambda: any(
                event["event"] == "render_blocked" and event.get("slide_index") == 2 for event in _events(event_log)
            ),
            timeout=_CONDITION_TIMEOUT,
            description="slide 2 to block inside render",
        )

        def running_render_state():
            rows = _query(
                database_path,
                "SELECT jobs.state, jobs.completed_slides, checkpoints.render_status "
                "FROM presentation_generation_jobs AS jobs "
                "JOIN authored_slide_checkpoints AS checkpoints ON checkpoints.job_id = jobs.id "
                "WHERE checkpoints.slide_index = 2",
            )
            return rows[0] if rows and rows[0] == ("running", 2, "running") else None

        _wait_until(
            running_render_state,
            timeout=_CONDITION_TIMEOUT,
            description="the durable job and checkpoint to report the blocked render",
        )
        assert _one(
            database_path,
            "SELECT author_status, illustrate_status, render_status, "
            "persist_status FROM authored_slide_checkpoints "
            "WHERE slide_index = 2",
        ) == ("completed", "skipped", "running", "pending")

        first_pid = first.pid
        first_output = _kill_process_tree(first)
        first = None

        crashed_job = _one(
            database_path,
            "SELECT state, attempt_number, completed_slides FROM presentation_generation_jobs",
        )
        # A forced process-tree termination normally leaves the leased row
        # running. On Windows the parent can briefly process task cancellation
        # while taskkill tears down its children, in which case the same
        # attempt is already placed in the safe resume state. Both boundaries
        # must preserve checkpoints and require the same authenticated
        # idempotent resume below.
        assert crashed_job in {
            ("running", 1, 2),
            ("awaiting_resume", 1, 2),
        }
        assert _one(
            database_path,
            "SELECT lifecycle_status, n_slides FROM presentations",
        ) == ("staging", 6)

        def lease_is_recoverable():
            state, job_lease_expires_at, operation_lease_expires_at = _one(
                database_path,
                "SELECT jobs.state, jobs.lease_expires_at, "
                "operations.lease_expires_at "
                "FROM presentation_generation_jobs AS jobs "
                "JOIN workspace_operations AS operations "
                "ON operations.id = jobs.workspace_operation_id",
            )
            # The workspace operation is the canonical lease and the linked
            # generation row mirrors it for checkpoint fencing. Both must be
            # reclaimable before the new process performs startup recovery.
            now = datetime.now(timezone.utc)
            leases = (job_lease_expires_at, operation_lease_expires_at)
            return state if all(value is None or _as_utc(value) <= now for value in leases) else None

        # Waiting for the recorded expiry instant -- instead of sleeping for a
        # duration that merely tends to exceed the lease -- is what makes the
        # takeover below deterministic. No heartbeat can renew the lease: the
        # process tree that owned it is gone.
        _wait_until(
            lease_is_recoverable,
            timeout=_LEASE_EXPIRY_TIMEOUT,
            description="the killed attempt's lease to become reclaimable",
        )

        second_env = common_env | {
            "AUTHORED_SUBPROCESS_BLOCK_RENDER_SLIDE": "-1",
            "AUTHORED_SUBPROCESS_LEASE_SECONDS": _RESUMING_LEASE_SECONDS,
            "WORKSPACE_OPERATION_LEASE_SECONDS": _RESUMING_LEASE_SECONDS,
            "WORKSPACE_OPERATION_HEARTBEAT_SECONDS": "1",
        }
        second, second_url = _start_server(
            python=python,
            env=second_env,
            description="the resuming runtime",
        )
        second_runtime_pid = _wait_until(
            lambda: ([event["pid"] for event in _events(event_log) if event["event"] == "startup"] + [None])[1],
            timeout=_CONDITION_TIMEOUT,
            description="the resuming runtime to log its startup pid",
        )
        assert second.pid != first_pid
        assert second_runtime_pid != first_runtime_pid

        def published_state():
            rows = _query(
                database_path,
                "SELECT jobs.state, presentations.lifecycle_status "
                "FROM presentation_generation_jobs AS jobs "
                "JOIN presentations ON presentations.id = jobs.presentation_id",
            )
            return rows[0] if rows and rows[0] == ("published", "published") else None

        try:
            _wait_until(
                published_state,
                timeout=_COMPLETION_TIMEOUT,
                description="the resumed attempt to publish the deck",
            )
        except AssertionError as error:
            stalled_job = _one(
                database_path,
                "SELECT state, attempt_number, completed_slides, "
                "current_stage, last_error FROM presentation_generation_jobs",
            )
            raise AssertionError(f"{error}; durable job row={stalled_job!r}") from error
        completed_response = _request(
            "GET",
            f"{second_url}/api/v1/ppt/presentation/status/{task_id}",
            description="the published status readback",
        )
        completed_response.raise_for_status()
        completed = completed_response.json()
        assert completed["status"] == "completed"
        assert completed["progress"]["completed_slides"] == 6
        assert completed["progress"]["percent"] == 100
        assert completed["failure"] is None
        storage_key = _one(database_path, "SELECT storage_key FROM workspace_artifacts")[0]
        assert completed["data"]["path"].replace("\\", "/").endswith(f"/{storage_key}")
        output_path = Path(common_env["APP_DATA_DIRECTORY"]) / storage_key
        assert output_path.exists()
        assert len(PptxPresentation(output_path).slides) == 6

        # Replaying the original idempotency key after recovery must resolve to
        # the same durable task instead of replacing the historical row.
        replayed = _request(
            "POST",
            f"{second_url}/api/v1/ppt/presentation/generate/async",
            json=payload,
            headers=headers,
            description="the completed idempotent replay",
        )
        assert replayed.status_code == 202, replayed.status_code
        assert replayed.json()["id"] == task_id
        assert replayed.json()["status"] == "completed"

        second_output = _kill_process_tree(second)
        second = None

        recorded = _events(event_log)
        assert Counter(event["slide_index"] for event in recorded if event["event"] == "author_success") == Counter(
            {index: 1 for index in range(6)}
        )
        assert Counter(event["slide_index"] for event in recorded if event["event"] == "render_success") == Counter(
            {index: 1 for index in range(6)}
        )
        assert sum(event["event"] == "brief_success" for event in recorded) == 0
        assert sum(event["event"] == "outline_success" for event in recorded) == 1
        assert {event["pid"] for event in recorded if event["event"] == "startup"} == {
            first_runtime_pid,
            second_runtime_pid,
        }

        published_job = _one(
            database_path,
            "SELECT state, attempt_number, completed_slides, current_stage FROM presentation_generation_jobs",
        )
        assert published_job == ("published", 2, 6, "completed")
        assert (
            _one(
                database_path,
                "SELECT COUNT(*) FROM authored_slide_checkpoints WHERE persist_status = 'completed'",
            )[0]
            == 6
        )
        assert _one(database_path, "SELECT lifecycle_status, n_slides FROM presentations") == ("published", 6)
        assert _one(database_path, "SELECT COUNT(*) FROM slides")[0] == 6
    except Exception as error:
        # Preserve subprocess diagnostics even when the failure happens before
        # the normal hard-kill points (for example, during the initial POST).
        if first is not None:
            first_output = _kill_process_tree(first)
            first = None
        if second is not None:
            second_output = _kill_process_tree(second)
            second = None
        raise AssertionError(f"subprocess recovery failed; first={first_output!r}, second={second_output!r}") from error
    finally:
        if first is not None:
            _kill_process_tree(first)
        if second is not None:
            _kill_process_tree(second)
