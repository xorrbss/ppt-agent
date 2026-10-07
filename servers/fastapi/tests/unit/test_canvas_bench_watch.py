"""Deterministic replay of standby, polling and power protection (no live backend)."""
import argparse
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from scripts import canvas_bench as bench
from scripts import canvas_bench_epoch as epochs
from scripts import canvas_bench_live as live
from scripts import canvas_bench_matrix as matrix
from scripts import canvas_bench_watch as watch


def test_suspend_replay_uses_unbiased_clock():
    readings = iter([(100, 100), (200, 200), (3496, 325)])
    probe = watch.SuspendProbe(lambda: next(readings))
    assert not probe.sample()
    assert probe.sample()
    assert probe.slept_seconds == 3171
    unavailable = watch.SuspendProbe(lambda: None)
    assert not unavailable.sample() and unavailable.slept_seconds is None


def test_poll_gap_excludes_request_duration_and_detects_pause():
    now = [0.0]
    monitor = watch.PollGapMonitor(lambda: now[0])
    now[0] = 5
    assert not monitor.begin()
    now[0] = 125  # slow HTTP request is not a poll gap
    monitor.end()
    now[0] = 130
    assert not monitor.begin()
    monitor.end()
    now[0] = 3301
    assert monitor.begin() and monitor.max_gap_seconds == 3171


def test_clock_boundaries_and_backend_overrun_is_advisory():
    readings = iter([(0, 0), (60, 30), (61, 30)])
    probe = watch.SuspendProbe(lambda: next(readings))
    assert not probe.sample() and probe.sample()
    now = [0.0]
    monitor = watch.PollGapMonitor(lambda: now[0])
    now[0] = 60
    assert not monitor.begin()
    now[0] = 60.01
    assert monitor.begin()
    advisory = watch.backend_overrun({"stages": {"content_brief": {"wallMillis": 3226000}}},
                                     3396, [300, 310, 320])
    assert advisory["overrun"] and advisory["clientOverrun"]
    clock = watch.DeckWatch(watch.SuspendProbe(lambda: None), watch.PollGapMonitor(lambda: 0))
    clock.begin()
    assert not clock.detected  # an overrun does not invalidate a provider-slow deck
    assert clock.evidence("none", advisory)["overrun"]
    assert not watch.backend_overrun({"stages": {"custom": {"wallMillis": 600000, "timeoutSeconds": 600}}},
                                     900, [300])["overrun"]


class FakePower:
    def __init__(self, created=7, succeeds=True):
        self.created, self.succeeds, self.calls = created, succeeds, []

    def create(self, reason):
        self.calls.append(("create", reason))
        return self.created

    def set(self, handle):
        self.calls.append(("set", handle))
        return self.succeeds

    def clear(self, handle):
        self.calls.append(("clear", handle))

    def close(self, handle):
        self.calls.append(("close", handle))

    def execution(self, flags):
        self.calls.append(("execution", flags))
        return 1


@pytest.mark.parametrize("exception", [RuntimeError, KeyboardInterrupt])
def test_power_request_releases_on_exception(exception):
    api = FakePower()
    with pytest.raises(exception):
        with watch.power_guard("canvas bench: output", api=api, platform="win32") as mode:
            assert mode == "power_request"
            raise exception()
    assert api.calls[-2:] == [("clear", 7), ("close", 7)]


def test_power_request_falls_back_and_clears_thread_state():
    api = FakePower(succeeds=False)
    with watch.power_guard("bench", api=api, platform="win32") as mode:
        assert mode == "execution_state"
    assert api.calls[-1] == ("execution", watch.ES_CONTINUOUS)
    assert ("close", 7) in api.calls
    api = FakePower()
    with watch.power_guard("bench", api=api, platform="linux") as mode:
        assert mode == "none"
    assert not api.calls


def _presentation(*versions):
    return {"theme": {"svg": {"quality": [
        {"overlap_basis": {"version": version, "fragments": {"ink": 2}}} for version in versions]}}}


def test_page_object_versions_derive_epoch_and_mixed_is_unmeasured():
    presentation = _presentation("glyph-ink/v1", "glyph-ink/v1")
    assert epochs.find_overlap_basis(presentation) == "glyph-ink/v1"
    basis, reason = epochs.deck_overlap_basis({}, presentation)
    assert basis == {"version": "glyph-ink/v1", "pages": 2} and reason is None
    assert epochs.observed_epoch(basis) == epochs.INK_EPOCH
    basis, reason = epochs.deck_overlap_basis({}, _presentation("glyph-ink/v1", "line-box/v1"))
    assert reason == "mixed_overlap_basis" and basis["version"] is None
    basis, reason = epochs.deck_overlap_basis({}, _presentation("glyph-ink/v1", None))
    assert reason == "missing_overlap_basis"
    assert epochs.observed_epoch("line-box/v1") == epochs.LEGACY_EPOCH


def test_git_fallback_warns(monkeypatch):
    monkeypatch.setattr(epochs, "is_ancestor", lambda *a: True)
    with pytest.warns(RuntimeWarning, match="git ancestry fallback"):
        assert epochs.resolve_run_epoch(None, "head", "fix") == epochs.INK_EPOCH


def test_matrix_loss_continues_requeues_and_keeps_previous_invalid(tmp_path):
    plan = [matrix.Deck("a", Path("one.txt"), 1), matrix.Deck("b", Path("two.txt"), 1)]
    calls, probes = [], []

    def run(deck):
        calls.append(deck.canvas)
        receipt = {"generation": {"ok": True}, "export": {"ok": True}}
        if len(calls) == 1:
            receipt.update(invalid={"reason": "infra_lost", "note": "slept 3171 s"},
                           clock={"sleptSeconds": 3171, "overrun": True})
        return receipt

    def write(path, receipt):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(receipt))

    assert matrix.run_matrix(plan, tmp_path, run, {}, write, log=lambda _: None,
                             health_probe=lambda: probes.append(True)) == 0
    assert calls == ["a", "b", "a"] and probes == [True]
    receipt = json.loads(matrix.receipt_path(tmp_path, plan[0]).read_text())
    assert receipt["previousInvalid"] == [{"reason": "infra_lost", "note": "slept 3171 s"}]


def test_health_probe_requires_two_successes_ten_seconds_apart(monkeypatch):
    calls = []
    monkeypatch.setattr(live.time, "sleep", lambda seconds: calls.append(seconds))
    client = live.BenchClient("http://bench", timeout_seconds=live.SETTLE_SECONDS)
    client.http = httpx.Client(transport=httpx.MockTransport(
        lambda request: (calls.append(request.url.path) or httpx.Response(200, json={"ok": True}))))
    try:
        client.health_probe()
    finally:
        client.close()
    assert calls == ["/health", 10, "/health"]


@pytest.mark.parametrize("mixed,explicit", [(False, None), (True, None), (False, "operator-epoch")])
def test_run_reads_presentation_basis_and_preserves_explicit_epoch(tmp_path, monkeypatch, mixed, explicit):
    client = SimpleNamespace(finished_seconds=[], watch=watch.DeckWatch(
        watch.SuspendProbe(lambda: None), watch.PollGapMonitor(lambda: 0)))
    client.generate = lambda body: {"status": "completed", "data": {"presentation_id": "p"}}
    client.presentation = lambda pid: {**_presentation("glyph-ink/v1", "line-box/v1" if mixed else "glyph-ink/v1"),
                                       "slides": [{"source_format": "svg"}] * 2}
    client.timeline = lambda pid: {"items": [{"anchor_type": "operation", "operation_type": "presentation.generate",
                                              "result": {"quality_pages": {"flagged": 1}}}]}
    monkeypatch.setattr(bench, "_export_evidence", lambda *a: {"ok": False})
    args = argparse.Namespace(slides=2, language="English", style="default", strategy="svg", canvas="wide-16x9",
                              backend_commit=None, run_epoch=epochs.LEGACY_EPOCH, metric_epoch=explicit, batch="b",
                              app_data=None, no_render=True)
    manuscript = tmp_path / "m.txt"
    manuscript.write_text("content")
    receipt = bench.run_one(client, manuscript, args, bench.rules.load_registry(), tmp_path)
    assert receipt["metricEpoch"] == (explicit or (epochs.LEGACY_EPOCH if mixed else epochs.INK_EPOCH))
    if mixed:
        assert receipt["primaryMetric"] == {"status": "unmeasured", "reason": "mixed_overlap_basis"}
        assert receipt["gate"]["flags"]["flaggedPages"] is None
    else:
        assert receipt["overlapBasis"] == {"version": "glyph-ink/v1", "pages": 2}
    assert receipt["clock"]["powerGuard"] == "none"


def test_live_poll_replay_recovers_terminal_deck_after_standby(monkeypatch):
    now, unbiased = [0.0], [0.0]
    probe = watch.SuspendProbe(lambda: (now[0], unbiased[0]))
    monitor = watch.DeckWatch(probe, watch.PollGapMonitor(lambda: now[0]))
    monkeypatch.setattr(live.watch, "DeckWatch", lambda: monitor)
    monkeypatch.setattr(live.time, "monotonic", lambda: now[0])

    def sleep(seconds):
        now[0] += 3176
        unbiased[0] += 5

    monkeypatch.setattr(live.time, "sleep", sleep)
    reads = []

    def handler(request):
        if request.url.path.endswith("/generate/async"):
            return httpx.Response(200, json={"id": "task", "operation_id": "gen"})
        if "/status/" in request.url.path:
            reads.append(True)
            return httpx.Response(200, json={"status": "running" if len(reads) == 1 else "completed",
                                             "data": {"presentation_id": "p"}})
        return httpx.Response(200, json={"items": [{"anchor_type": "operation", "operation_id": "gen",
                                                    "status": "succeeded"}]})

    client = live.BenchClient("http://bench", timeout_seconds=live.SETTLE_SECONDS)
    client.http = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        assert client.generate({})["status"] == "completed"
        assert client.watch.detected
        assert client.watch.suspend.slept_seconds == 3171
        assert client.watch.polls.max_gap_seconds == 3176
        assert client.recovery_deadline == 3176 + watch.LOST_TASK_SETTLE_SECONDS
    finally:
        client.close()


def test_repeated_clock_loss_stops_with_pending_receipt(tmp_path):
    deck = matrix.Deck("a", Path("one.txt"), 1)
    calls = []

    def write(path, receipt):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(receipt))

    def run(_):
        calls.append(True)
        return {"generation": {"ok": False}, "export": {"ok": False},
                "invalid": {"reason": "infra_lost", "note": "persistent pause"}}

    assert matrix.run_matrix([deck], tmp_path, run, {}, write, log=lambda _: None,
                             health_probe=lambda: None) == matrix.EXIT_INFRA_LOST
    assert len(calls) == 2 and matrix.pending([deck], tmp_path) == [deck]
