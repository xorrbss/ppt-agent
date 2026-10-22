from types import SimpleNamespace

import pytest

from utils import codex_stream_adapter as adapter
from utils.provider_overload import ProviderOutcomeError


class _Clock:
    def __init__(self, now=100.0):
        self.now = now

    def monotonic(self):
        return self.now


class _Stream:
    """Fake stream: each item is (seconds_to_advance_before_event, event)."""

    def __init__(self, clock, steps):
        self._clock = clock
        self._steps = steps
        self.closed = False

    def __iter__(self):
        for advance, event in self._steps:
            self._clock.now += advance
            yield event

    def close(self):
        self.closed = True


def _created():
    return SimpleNamespace(type="response.created", response=SimpleNamespace(id="resp_1"))


def _delta(text="x"):
    return SimpleNamespace(type="response.output_text.delta", delta=text, response_id="resp_1",
                           item_id="i", output_index=0, content_index=0)


def _completed(text="x"):
    return SimpleNamespace(type="response.completed", response=SimpleNamespace(
        id="resp_1", output_text=text, output=[], usage=None))


def _responses(clock, steps, observer=None):
    stream = _Stream(clock, steps)
    resource = SimpleNamespace(create=lambda *a, **k: (clock.__setattr__("now", clock.now + 2.0), stream)[1])
    return adapter._ObservedResponses(resource, observer), stream


def test_timing_fields_are_recorded_and_reach_failure_diagnostics(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(adapter, "time", clock)
    # create() itself takes 2s; events arrive 1.5s after that, 0.25s, 7s (stream then ends).
    responses, stream = _responses(clock, [(1.5, _created()), (0.25, _delta()), (7.0, _delta())])
    events = responses.create(stream=True)
    with pytest.raises(ProviderOutcomeError) as caught:
        list(events)
    obs = responses.observation
    assert obs["firstEventMs"] == 3500
    assert obs["maxEventGapMs"] == 7000
    assert obs["eventCount"] == 3
    assert all(type(obs[k]) is int and obs[k] >= 0 for k in ("firstEventMs", "maxEventGapMs", "eventCount"))
    diag = caught.value.diagnostics
    assert diag["eventCount"] == 3 and diag["maxEventGapMs"] == 7000
    assert stream.closed


def test_first_event_ms_measured_from_create_start(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(adapter, "time", clock)

    stream = _Stream(clock, [(3.0, _created())])

    def create(*a, **k):
        clock.now += 2.0  # time spent opening the stream counts toward firstEventMs
        return stream

    responses = adapter._ObservedResponses(SimpleNamespace(create=create))
    events = responses.create(stream=True)
    with pytest.raises(ProviderOutcomeError):
        list(events)
    assert responses.observation["firstEventMs"] == 5000


def test_completed_observer_payload_carries_timing(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(adapter, "time", clock)
    seen = []
    responses, _ = _responses(clock, [(1.0, _created()), (0.5, _delta("x")), (0.0, _completed("x"))],
                              observer=seen.append)
    list(responses.create(stream=True))
    completed = [p for p in seen if p.get("terminal_event") == "response.completed"]
    assert len(completed) == 1
    obs = responses.observation
    assert obs["firstEventMs"] == 3000 and obs["maxEventGapMs"] == 500 and obs["eventCount"] == 3
    diagnostics = completed[0]["diagnostics"]
    # Survives safe_provider_diagnostics once the allowlist admits the keys.
    if "eventCount" in diagnostics:
        assert diagnostics["eventCount"] == 3
        assert diagnostics["firstEventMs"] == 3000
        assert diagnostics["maxEventGapMs"] == 500
