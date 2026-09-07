"""The outline SSE stream must stay warm across its long silent stages.

`stream_outlines` awaits the content brief and then the outline call's first
token without writing anything, and either gap can outlast the 300s idle
timeout of the proxies in front of it. This pins the endpoint's response body
to the heartbeat wrapper rather than to the raw generator, so a future edit
cannot quietly hand `StreamingResponse` an unheartbeated stream again.
"""

import asyncio
import uuid
from types import SimpleNamespace

import pytest

from api.v1.ppt.endpoints import outlines
from utils.sse_heartbeat import SSE_HEARTBEAT_FRAME


def _run(coro):
    return asyncio.run(coro)


class _Request:
    """A connected client whose disconnect check takes ``delay`` seconds.

    ``inner()`` awaits this first, so a slow answer stands in for the content
    brief's silent gap without reaching an LLM.
    """

    def __init__(self, delay: float = 0.0) -> None:
        self._delay = delay

    async def is_disconnected(self) -> bool:
        if self._delay:
            await asyncio.sleep(self._delay)
        return False


async def _drain(request: _Request) -> list[str]:
    response = await outlines.stream_outlines(
        id=uuid.uuid4(), request=request, sql_session=object()
    )
    assert response.media_type == "text/event-stream"
    # nginx buffers a proxied response by default, which would hold every
    # heartbeat back until the buffer filled.
    assert response.headers["x-accel-buffering"] == "no"
    return [frame async for frame in response.body_iterator]


@pytest.fixture
def stubbed_stream(monkeypatch):
    """Drive `stream_outlines` without a database, temp dir, or LLM."""

    outline_payload = {"slides": [{"content": "one"}]}
    presentation = SimpleNamespace(outlines=outline_payload)
    presentation.model_dump = lambda mode="json": {"outlines": outline_payload}

    async def _require_live_presentation(_session, _id):
        return presentation

    async def _require_write_path(_session, _presentation):
        return None

    monkeypatch.setattr(
        outlines, "require_live_presentation", _require_live_presentation
    )
    monkeypatch.setattr(
        outlines, "require_outline_bootstrap_write_path", _require_write_path
    )
    monkeypatch.setattr(
        outlines.TEMP_FILE_SERVICE, "create_temp_dir", lambda: "unused"
    )
    return presentation


def test_a_prompt_stream_carries_no_heartbeat(stubbed_stream):
    frames = _run(_drain(_Request()))

    # A replayed completion is instant, so no heartbeat is due; what matters is
    # that the persisted completion still reaches the client unchanged.
    assert SSE_HEARTBEAT_FRAME not in frames
    assert any('"type": "complete"' in frame for frame in frames)


def test_a_silent_stage_produces_a_heartbeat(stubbed_stream, monkeypatch):
    monkeypatch.setattr(
        outlines, "get_sse_heartbeat_interval_seconds", lambda: 0.02
    )

    frames = _run(_drain(_Request(delay=0.12)))

    assert SSE_HEARTBEAT_FRAME in frames
    assert any('"type": "complete"' in frame for frame in frames)
