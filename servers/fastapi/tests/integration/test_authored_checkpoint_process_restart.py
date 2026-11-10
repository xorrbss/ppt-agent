from __future__ import annotations

import os
from pathlib import Path

from tests.support.authored_checkpoint_public_fixture import set_checkpoint_environment


from tests.support.authored_checkpoint_process_harness import (
    _CONDITION_TIMEOUT,
    _CRASHED_LEASE_SECONDS,
    _events,
    _kill_process_tree,
    _query,
    _request,
    _start_server,
    _wait_until,
)


def test_real_process_refuses_a_new_authored_generation(tmp_path):
    """A real server process refuses authored generation (removed 2026-11-09).

    This used to drive an authored job through a hard process kill and a
    checkpoint resume in a second process. No authored job can be admitted
    any more, so the same process harness now proves the refusal: a 422 with
    ``authored_strategy_removed`` and no durable job, checkpoint or model call.
    """

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

    server = None
    output = ""
    try:
        server, url = _start_server(
            python=python,
            env=common_env | {
                "AUTHORED_SUBPROCESS_LEASE_SECONDS": _CRASHED_LEASE_SECONDS,
                "WORKSPACE_OPERATION_LEASE_SECONDS": _CRASHED_LEASE_SECONDS,
                "WORKSPACE_OPERATION_HEARTBEAT_SECONDS": "1",
            },
            description="the runtime",
        )
        _wait_until(
            lambda: any(event["event"] == "startup" for event in _events(event_log)),
            timeout=_CONDITION_TIMEOUT,
            description="the runtime to log its startup",
        )
        response = _request(
            "POST",
            f"{url}/api/v1/ppt/presentation/generate/async",
            json=payload,
            headers=headers,
            description="the authored generate request",
        )
        assert response.status_code == 422, response.text
        assert "authored_strategy_removed" in response.text

        output = _kill_process_tree(server)
        server = None
        assert _query(database_path, "SELECT COUNT(*) FROM presentation_generation_jobs") == [(0,)]
        assert _query(database_path, "SELECT COUNT(*) FROM authored_slide_checkpoints") == [(0,)]
        assert [event["event"] for event in _events(event_log)] == ["startup"]
    except Exception as error:
        # Preserve subprocess diagnostics on any failure.
        if server is not None:
            output = _kill_process_tree(server)
            server = None
        raise AssertionError(f"authored refusal failed; output={output!r}") from error
    finally:
        if server is not None:
            _kill_process_tree(server)
