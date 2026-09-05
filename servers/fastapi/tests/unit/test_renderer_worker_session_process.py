"""One worker subprocess serves many framed renders (parent side).

R2-B, authored-quality-speed programme design section 10 phase 3. Runs a real
subprocess -- a stub worker that speaks the framed protocol and returns a
synthetic PNG -- so the parent's spawn/exchange/close lifecycle, ordinal
binding, digest binding, and retire-on-error are exercised end to end without
Chromium or Docker.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import sys

import pytest

from utils import slide_capture  # noqa: F401  (import order: see test_render_with_lease)
from utils.renderer_worker_response import RendererIsolationError
from utils.slide_capture_worker_session import RendererWorkerSession

HTML = "<main>framed</main>"
PNG = b"\x89PNG\r\n\x1a\n" + b"stub-body"

STUB_WORKER = '''
import base64, hashlib, json, os, sys

HEADER = 8
log_path = sys.argv[1]
mode = sys.argv[2]
ordinal = 0
with open(log_path, "a", encoding="utf-8") as log:
    log.write(json.dumps({"event": "start", "pid": os.getpid()}) + "\\n")
while True:
    header = sys.stdin.buffer.read(HEADER)
    if len(header) < HEADER:
        break
    length = int.from_bytes(header, "big")
    request = json.loads(sys.stdin.buffer.read(length))
    ordinal += 1
    with open(log_path, "a", encoding="utf-8") as log:
        log.write(json.dumps({"event": "render", "pid": os.getpid(), "ordinal": ordinal}) + "\\n")
    digest = "sha256:" + hashlib.sha256(request["html"].encode("utf-8")).hexdigest()
    if mode == "wrong-digest":
        digest = "sha256:" + "0" * 64
    response = {
        "schema_version": "authored-render-response/v1",
        "ok": True,
        "png_base64": base64.b64encode(PNG).decode("ascii"),
        "metrics": {"ordinal": ordinal},
        "source_digest": digest,
        "asset_digests": [],
        "request_ordinal": 99 if mode == "wrong-ordinal" else ordinal,
    }
    if mode == "fail-second" and ordinal == 2:
        response = {
            "schema_version": "authored-render-response/v1",
            "ok": False,
            "error": "stub render failed",
            "error_type": "RuntimeError",
            "request_ordinal": ordinal,
        }
    body = json.dumps(response, separators=(",", ":")).encode("utf-8")
    sys.stdout.buffer.write(len(body).to_bytes(HEADER, "big") + body)
    sys.stdout.buffer.flush()
'''


@pytest.fixture
def stub_worker(tmp_path, monkeypatch):
    log = tmp_path / "worker.log"

    def configure(mode: str = "ok") -> "os.PathLike[str]":
        script = tmp_path / "stub_worker.py"
        script.write_text(f"PNG = {PNG!r}\n" + STUB_WORKER, encoding="utf-8")
        monkeypatch.setenv(
            "AUTHORED_RENDERER_WORKER_COMMAND_JSON",
            json.dumps([sys.executable, str(script), str(log), mode]),
        )
        monkeypatch.delenv("AUTHORED_RENDERER_PRODUCTION", raising=False)
        monkeypatch.delenv("AUTHORED_RENDERER_PRODUCTION_STRICT", raising=False)
        monkeypatch.delenv("AUTHORED_RENDERER_REQUIRE_EXTERNAL", raising=False)
        monkeypatch.delenv("TEMPLATE_V2_DEPLOYMENT_TIER", raising=False)
        return log

    return configure


def _events(log) -> list[dict]:
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line]


def test_one_subprocess_serves_every_render_of_a_session(stub_worker):
    log = stub_worker()

    async def scenario():
        session = RendererWorkerSession(max_requests=10)
        pngs = [await session.render(f"<main>{index}</main>", 10) for index in range(3)]
        assert session.render_count == 3
        assert not session.is_closed
        await session.close()
        return pngs

    pngs = asyncio.run(scenario())

    assert [png for png, _metrics in pngs] == [PNG] * 3
    events = _events(log)
    assert [event["event"] for event in events] == ["start", "render", "render", "render"]
    assert len({event["pid"] for event in events}) == 1, "three renders, one process"
    assert [event["ordinal"] for event in events if event["event"] == "render"] == [1, 2, 3]


def test_the_session_closes_itself_at_its_request_ceiling(stub_worker):
    log = stub_worker()

    async def scenario():
        session = RendererWorkerSession(max_requests=2)
        await session.render(HTML, 10)
        await session.render("<main>second</main>", 10)
        assert session.is_closed
        with pytest.raises(RendererIsolationError):
            await session.render("<main>third</main>", 10)

    asyncio.run(scenario())

    assert len([event for event in _events(log) if event["event"] == "render"]) == 2


def test_a_response_bound_to_the_wrong_request_is_refused(stub_worker):
    stub_worker("wrong-digest")

    async def scenario():
        session = RendererWorkerSession()
        with pytest.raises(RendererIsolationError):
            await session.render(HTML, 10)
        assert session.is_closed

    asyncio.run(scenario())


def test_a_response_with_the_wrong_ordinal_is_refused(stub_worker):
    stub_worker("wrong-ordinal")

    async def scenario():
        session = RendererWorkerSession()
        with pytest.raises(RendererIsolationError):
            await session.render(HTML, 10)
        assert session.is_closed

    asyncio.run(scenario())


def test_a_worker_failure_retires_the_session(stub_worker):
    stub_worker("fail-second")

    async def scenario():
        session = RendererWorkerSession(max_requests=10)
        await session.render(HTML, 10)
        with pytest.raises(RuntimeError):
            await session.render("<main>second</main>", 10)
        assert session.is_closed

    asyncio.run(scenario())


def test_the_first_request_carries_the_process_ceiling(stub_worker, monkeypatch):
    log = stub_worker()
    captured: list[dict] = []
    from utils import renderer_frame_codec

    original = renderer_frame_codec.encode_framed_request

    def spy(html, timeout, *, max_requests=None):
        captured.append({"max_requests": max_requests})
        return original(html, timeout, max_requests=max_requests)

    monkeypatch.setattr("utils.slide_capture_worker_session.encode_framed_request", spy)

    async def scenario():
        session = RendererWorkerSession(max_requests=7)
        await session.render(HTML, 10)
        await session.render("<main>second</main>", 10)
        await session.close()

    asyncio.run(scenario())

    assert captured == [{"max_requests": 7}, {"max_requests": None}]
    assert hashlib.sha256(HTML.encode("utf-8")).hexdigest()
    assert base64.b64encode(PNG)
    assert len(_events(log)) == 3
