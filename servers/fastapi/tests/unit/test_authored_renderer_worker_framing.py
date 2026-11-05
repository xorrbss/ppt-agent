"""Framed multi-request protocol of the isolated authored renderer worker.

R2-B, authored-quality-speed programme design section 10 phase 3 "production
renderer batch pipe". These drive ``utils.authored_renderer_worker.main`` over
in-memory stdin/stdout with a fake Chromium session, so they exercise the wire
format and the process lifecycle without a browser or a container.
"""

# ruff: noqa: ASYNC109 - stubs mirror the renderer API, which takes a timeout

from __future__ import annotations

import io
import json
import types

import pytest

from utils import authored_renderer_session, authored_renderer_worker as worker
from utils import slide_capture  # noqa: F401  (import order: see test_render_with_lease)
from utils.renderer_frame_codec import (
    FRAME_HEADER_BYTES,
    encode_framed_request,
    header_is_frame,
)

SAFE_HTML = (
    '<!doctype html><html><head><meta charset="utf-8"><style>'
    "html,body{margin:0;width:1280px;height:720px;background:#fff}"
    "</style></head><body><main><h1>Safe authored</h1></main></body></html>"
)
PNG = b"\x89PNG\r\n\x1a\n" + b"body"


class _FakeSession:
    """Stands in for one worker process's long-lived Chromium."""

    instances: list["_FakeSession"] = []

    def __init__(self) -> None:
        self.renders: list[tuple[str, int]] = []
        self.closed = False
        _FakeSession.instances.append(self)

    def render(self, admitted, timeout, **_kwargs):
        self.renders.append((admitted.source_digest, timeout))
        return PNG, {"contentBoxes": len(self.renders)}

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _worker_boundary(monkeypatch):
    _FakeSession.instances = []
    monkeypatch.setenv("AUTHORED_RENDERER_WORKER", "1")
    # Provider-secret screening reads the real process environment, which a
    # developer machine may legitimately populate; it has its own tests.
    monkeypatch.setattr(worker, "assert_no_provider_secrets", lambda environment: None)
    monkeypatch.setattr(authored_renderer_session, "RendererChromiumSession", _FakeSession)
    yield


def _run(stdin_bytes: bytes, monkeypatch) -> tuple[int, bytes]:
    stdout = io.BytesIO()
    monkeypatch.setattr(worker.sys, "stdin", types.SimpleNamespace(buffer=io.BytesIO(stdin_bytes)))
    monkeypatch.setattr(worker.sys, "stdout", types.SimpleNamespace(buffer=stdout))
    return worker.main([]), stdout.getvalue()


def _read_frames(payload: bytes) -> list[dict]:
    frames: list[dict] = []
    offset = 0
    while offset < len(payload):
        header = payload[offset : offset + FRAME_HEADER_BYTES]
        assert header_is_frame(header)
        length = int.from_bytes(header, "big")
        offset += FRAME_HEADER_BYTES
        frames.append(json.loads(payload[offset : offset + length]))
        offset += length
    return frames


def test_three_framed_requests_are_served_by_one_process_and_one_session(monkeypatch):
    stdin = b"".join(
        [
            encode_framed_request(SAFE_HTML, 10, max_requests=50),
            encode_framed_request(SAFE_HTML, 11),
            encode_framed_request(SAFE_HTML, 12),
        ]
    )

    code, stdout = _run(stdin, monkeypatch)
    frames = _read_frames(stdout)

    assert code == 0
    assert len(frames) == 3
    assert len(_FakeSession.instances) == 1, "one Chromium session serves the whole process"
    assert [timeout for _digest, timeout in _FakeSession.instances[0].renders] == [10, 11, 12]
    assert _FakeSession.instances[0].closed is True


def test_every_framed_response_carries_the_isolation_attestation_and_its_ordinal(monkeypatch):
    stdin = encode_framed_request(SAFE_HTML, 10) + encode_framed_request(SAFE_HTML, 10)

    _code, stdout = _run(stdin, monkeypatch)
    frames = _read_frames(stdout)

    for ordinal, frame in enumerate(frames, start=1):
        assert frame["schema_version"] == "authored-render-response/v1"
        assert frame["ok"] is True
        assert frame["request_ordinal"] == ordinal
        assert frame["source_digest"].startswith("sha256:")
        assert frame["asset_digests"] == []
        assert frame["metrics"]["capabilityRegistry"]
    # Identity is per request, not per process.
    assert frames[0]["source_digest"] == frames[1]["source_digest"]


def test_max_requests_from_the_first_request_stops_the_process(monkeypatch):
    stdin = b"".join(
        [
            encode_framed_request(SAFE_HTML, 10, max_requests=2),
            encode_framed_request(SAFE_HTML, 10),
            encode_framed_request(SAFE_HTML, 10),
        ]
    )

    code, stdout = _run(stdin, monkeypatch)

    assert code == 0
    assert len(_read_frames(stdout)) == 2
    assert len(_FakeSession.instances[0].renders) == 2


def test_a_failed_render_reports_once_and_retires_the_process(monkeypatch):
    class _PoisonedSession(_FakeSession):
        def render(self, admitted, timeout, **_kwargs):
            raise RuntimeError("chromium target crashed")

    monkeypatch.setattr(authored_renderer_session, "RendererChromiumSession", _PoisonedSession)
    stdin = encode_framed_request(SAFE_HTML, 10) + encode_framed_request(SAFE_HTML, 10)

    code, stdout = _run(stdin, monkeypatch)
    frames = _read_frames(stdout)

    assert code == 1
    assert len(frames) == 1
    assert frames[0]["ok"] is False
    assert frames[0]["error_type"] == "RuntimeError"
    assert frames[0]["request_ordinal"] == 1
    assert _FakeSession.instances[0].closed is True


def test_a_malformed_frame_length_is_rejected_without_rendering(monkeypatch):
    stdin = (4).to_bytes(FRAME_HEADER_BYTES, "big") + b"{}xx"

    code, stdout = _run(stdin, monkeypatch)
    frames = _read_frames(stdout)

    assert code == 1
    assert frames[0]["ok"] is False
    assert _FakeSession.instances[0].renders == []


def test_a_legacy_bare_payload_still_gets_one_response_and_no_session(monkeypatch):
    calls: list[int] = []

    async def fake_worker_render(admitted, *, timeout, **_kwargs):
        calls.append(timeout)
        return PNG, {"contentBoxes": 1}

    monkeypatch.setattr(slide_capture, "_render_admitted_html_in_worker", fake_worker_render)
    payload = json.dumps(
        {
            "schema_version": "authored-render-request/v1",
            "html": SAFE_HTML,
            "timeout": 30,
        },
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"

    code, stdout = _run(payload, monkeypatch)

    assert code == 0
    assert calls == [30]
    assert _FakeSession.instances == [], "legacy single-shot never opens a reusable session"
    assert stdout.endswith(b"\n")
    response = json.loads(stdout)
    assert response["ok"] is True
    assert "request_ordinal" not in response


def test_a_legacy_payload_may_not_carry_the_framed_max_requests_field(monkeypatch):
    payload = json.dumps(
        {
            "schema_version": "authored-render-request/v1",
            "html": SAFE_HTML,
            "timeout": 30,
            "max_requests": 10,
        },
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"

    code, stdout = _run(payload, monkeypatch)

    assert code == 1
    assert json.loads(stdout)["ok"] is False


def test_a_frame_header_is_never_confused_with_a_legacy_payload():
    legacy_prefix = b'{"schema'
    assert not header_is_frame(legacy_prefix)
    assert header_is_frame(encode_framed_request(SAFE_HTML, 10)[:FRAME_HEADER_BYTES])



def test_raster_furniture_proof_crosses_request_boundary_without_enabling_plain_html():
    from utils.renderer_isolation import encode_request
    from utils.renderer_worker_policy import RendererPolicyError

    proof = {"version":"svg-system-furniture/v1", "digest":"proof"}
    payload = json.loads(encode_request("<main>preview</main>", 30,
                                       preserved_raster_svg="<svg/>", system_furniture=proof))
    assert worker._validate_request(payload)["system_furniture"] == proof
    payload.pop("preserved_raster_svg")
    with pytest.raises(RendererPolicyError, match="furniture attestation"):
        worker._validate_request(payload)
