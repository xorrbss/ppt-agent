"""The pinned launcher's framed serve protocol and its legacy compatibility.

R2-B, authored-quality-speed programme design section 10 phase 3. Nothing here
starts a container: every case is rejected by the launcher's own fail-closed
checks first, which is exactly the boundary being asserted -- a framed stream
must be screened before any container runtime the same way a single-shot line
is, and must answer in frames rather than in bare lines.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
LAUNCHER = REPOSITORY_ROOT / "contracts" / "authored-renderer" / "invoke.py"
FRAME_HEADER_BYTES = 8
KILL_SWITCH_ENVIRONMENT = {
    "AUTHORED_RENDERER_MODE": "isolated",
    "AUTHORED_RENDERER_KILL_SWITCH": "true",
    "AUTHORED_RENDERER_LEGACY_ENABLED": "false",
}


def _launcher():
    spec = importlib.util.spec_from_file_location("authored_renderer_launcher", LAUNCHER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _frame(value: dict) -> bytes:
    payload = json.dumps(value, separators=(",", ":")).encode("utf-8")
    return len(payload).to_bytes(FRAME_HEADER_BYTES, "big") + payload


def _request(**overrides) -> dict:
    request = {
        "schema_version": "authored-render-request/v1",
        "html": "<main>safe</main>",
        "timeout": 1,
    }
    request.update(overrides)
    return request


def _invoke(payload: bytes, *, environment: dict[str, str] | None = None, argv=()):
    return subprocess.run(
        [sys.executable, str(LAUNCHER), *argv],
        input=payload,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
        check=False,
    )


def _decode_frame(payload: bytes) -> dict:
    length = int.from_bytes(payload[:FRAME_HEADER_BYTES], "big")
    assert len(payload) == FRAME_HEADER_BYTES + length
    return json.loads(payload[FRAME_HEADER_BYTES:])


def test_a_framed_stream_is_detected_and_answered_in_frames() -> None:
    completed = _invoke(_frame(_request()), environment=KILL_SWITCH_ENVIRONMENT)

    assert completed.returncode == 1
    assert _decode_frame(completed.stdout) == {
        "schema_version": "authored-render-response/v1",
        "ok": False,
        "error": "renderer-kill-switch-active",
    }
    assert completed.stderr == b""


def test_a_legacy_line_is_still_answered_as_one_bare_line() -> None:
    payload = json.dumps(_request(), separators=(",", ":")).encode("utf-8") + b"\n"

    completed = _invoke(payload, environment=KILL_SWITCH_ENVIRONMENT)

    assert completed.returncode == 1
    assert completed.stdout.endswith(b"\n")
    assert json.loads(completed.stdout)["error"] == "renderer-kill-switch-active"


def test_the_serve_subcommand_selects_the_framed_protocol_with_empty_stdin() -> None:
    completed = _invoke(b"", environment=KILL_SWITCH_ENVIRONMENT, argv=("serve",))

    assert completed.returncode == 1
    assert _decode_frame(completed.stdout)["error"] == "renderer-kill-switch-active"


def test_an_unknown_subcommand_is_refused() -> None:
    completed = _invoke(b"", environment=KILL_SWITCH_ENVIRONMENT, argv=("run",))

    assert completed.returncode == 1
    assert json.loads(completed.stdout)["error"] == "unsupported-argument"


def test_frame_detection_never_misreads_a_legacy_payload() -> None:
    launcher = _launcher()

    assert not launcher._header_is_frame(b'{"schema')
    assert not launcher._header_is_frame(b"{}\n")
    assert launcher._header_is_frame(_frame(_request())[:FRAME_HEADER_BYTES])


def test_framed_requests_are_normalized_and_validated_by_the_launcher() -> None:
    launcher = _launcher()

    frame, timeout = launcher._normalize_framed_request(
        json.dumps(_request(timeout=7, max_requests=25)).encode("utf-8")
    )
    assert timeout == 7
    assert _decode_frame(frame) == {
        "schema_version": "authored-render-request/v1",
        "html": "<main>safe</main>",
        "timeout": 7,
        "max_requests": 25,
    }

    for invalid in (
        _request(timeout=0),
        _request(timeout=True),
        _request(max_requests=0),
        _request(schema_version="authored-render-request/v2"),
        _request(html=1),
        {"schema_version": "authored-render-request/v1", "html": "<main/>"},
    ):
        try:
            launcher._normalize_framed_request(json.dumps(invalid).encode("utf-8"))
        except launcher.RequestError:
            continue
        raise AssertionError(f"launcher admitted an invalid framed request: {invalid}")


def test_a_truncated_frame_is_rejected_by_the_launcher_reader() -> None:
    launcher = _launcher()
    import io

    with_short_payload = (16).to_bytes(FRAME_HEADER_BYTES, "big") + b"{}"
    try:
        launcher._read_frame(io.BytesIO(with_short_payload), launcher.MAX_REQUEST_BYTES)
    except launcher.RequestError as error:
        assert str(error) == "frame-payload-truncated"
    else:  # pragma: no cover - the reader must fail closed
        raise AssertionError("truncated frame was accepted")

    assert launcher._read_frame(io.BytesIO(b""), launcher.MAX_REQUEST_BYTES) is None
