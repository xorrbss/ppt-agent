"""S1/Q3: concurrent remakes, severity order and the fragmentation budget.

``docs/authored-sol-quality-first-design-20260908.md``. Every test is a sync
function driving the coroutines with ``asyncio.run``; the provider and
Chromium are fakes that record ordering and overlap.
"""

from __future__ import annotations

import asyncio
import re
from types import SimpleNamespace

from services import authored_remake_schedule as schedule_mod
from services import authored_repair_round_order as order_mod
from services import authored_terminal_slide_fallback as terminal_mod
from services.authored_remake_schedule import (
    MAX_FRAGMENTATION_REMAKES_PER_DECK,
    RemakeLaunch,
    order_remake_candidates,
    plan_remakes,
    run_bounded_remakes,
)
from services.authored_terminal_slide_fallback import MAX_TERMINAL_REMAKES_PER_DECK

_LOG_LINE = re.compile(
    r"\[authored\] remake-first flagged=\[[^\]]*\] text_scaled=\[[^\]]*\] "
    r"remade=\[[^\]]*\] to_patch_round=\[[^\]]*\]$"
)


def _run(coro):
    return asyncio.run(coro)


def _issue(category: str, detail: str = "measured") -> dict[str, object]:
    return {"category": category, "detail": detail, "severity": "high"}


def _overflow(index: int) -> dict[str, object]:
    return {"slide_index": index, "issues": [_issue("overflow", "text escapes")]}


def _overlap(index: int) -> dict[str, object]:
    return {"slide_index": index, "issues": [_issue("overlap", "boxes overlap")]}


def _fragmentation(index: int) -> dict[str, object]:
    return {
        "slide_index": index,
        "issues": [_issue("fragmentation", "merge the 61 text owners into fewer boxes")],
    }


def _capacity(index: int) -> dict[str, object]:
    return {"slide_index": index, "issues": [_issue("capacity", "too many bullets")]}


class _Profile:
    profile_id = "ordinary"


def _round_kwargs(*, flagged, gate_failures, count):
    return {
        "flagged": list(flagged),
        "gate_failures": list(gate_failures),
        "htmls": [f"<div>slide {i}</div>" for i in range(count)],
        "pngs": [b"png" for _ in range(count)],
        "contents": [f"content {i}" for i in range(count)],
        "roles": ["CONTENT"] * count,
        "brand": object(),
        "design_system": "ds",
        "style": None,
        "capacity_plan": None,
        "profile": _Profile(),
        "reference_mode": "style_reference",
        "scene_envelope": "envelope-0",
        "metrics_by_slide": {},
    }


class _FakeRemakes:
    """A ``remake_slide_once`` double that records overlap and finish order.

    ``outcomes`` maps a slide index to ``"remade"``, ``"fail"`` (not admitted)
    or ``"raise"`` (the call blows up); unlisted slides are remade. ``delays``
    gives each slide its own sleep so completion order can differ from launch
    order.
    """

    def __init__(self, *, outcomes=None, delays=None):
        self.outcomes = dict(outcomes or {})
        self.delays = dict(delays or {})
        self.started: list[int] = []
        self.finished: list[int] = []
        self.applied: list[int] = []
        self.in_flight = 0
        self.max_in_flight = 0

    async def remake(self, **kwargs):
        index = kwargs["index"]
        self.started.append(index)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(self.delays.get(index, 0.001))
            outcome = self.outcomes.get(index, "remade")
            if outcome == "raise":
                raise RuntimeError(f"boom {index}")
            if outcome == "fail":
                return None, "remake_gate_failed"
            return (f"<div>remade {index}</div>", b"remade", {"ok": True}), "remade"
        finally:
            self.in_flight -= 1
            self.finished.append(index)

    async def apply(self, *, index, admitted, htmls, pngs, metrics_by_slide, scene_envelope, generation_job=None):
        html, png, metrics = admitted
        htmls[index] = html
        pngs[index] = png
        metrics_by_slide[index] = metrics
        self.applied.append(index)
        return f"envelope-after-{index}"


def _install(monkeypatch, fakes: _FakeRemakes, *, concurrency: int):
    monkeypatch.setenv("AUTHORED_TEXT_SCALE_RUNG_ENABLED", "false")
    monkeypatch.setattr(order_mod, "remake_slide_once", fakes.remake)
    monkeypatch.setattr(order_mod, "apply_admitted_remake", fakes.apply)
    monkeypatch.setattr(order_mod, "remake_concurrency", lambda: concurrency)


# ---------------------------------------------------------------------------
# S1: bounded concurrency, deterministic application, isolation of failures
# ---------------------------------------------------------------------------


def test_the_concurrency_bound_is_honoured_and_actually_used():
    launches = [RemakeLaunch(index=i, defects=(), budget="overflow") for i in range(6)]
    in_flight = 0
    peak = 0

    async def run_one(launch):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.005)
        in_flight -= 1
        return launch.index

    results = _run(run_bounded_remakes(launches, concurrency=2, run_one=run_one))

    assert peak == 2
    assert results == {i: i for i in range(6)}


def test_a_bound_of_one_is_the_serial_path():
    launches = [RemakeLaunch(index=i, defects=(), budget="overflow") for i in range(3)]
    order: list[int] = []

    async def run_one(launch):
        order.append(launch.index)
        await asyncio.sleep(0.001)
        return launch.index

    _run(run_bounded_remakes(launches, concurrency=1, run_one=run_one))
    assert order == [0, 1, 2]


def test_admitted_remakes_apply_in_slide_order_whatever_finished_first(monkeypatch):
    # Slide 0 is the slowest, slide 3 the fastest: completion order is 3,2,1,0.
    fakes = _FakeRemakes(delays={0: 0.02, 1: 0.015, 2: 0.01, 3: 0.005})
    _install(monkeypatch, fakes, concurrency=4)
    kwargs = _round_kwargs(
        flagged=[0, 1, 2, 3],
        gate_failures=[_overflow(0), _overflow(1), _overflow(2), _overflow(3)],
        count=4,
    )

    outcome = _run(order_mod.remake_first_flagged_slides(**kwargs))

    assert fakes.max_in_flight == 4
    assert fakes.finished == [3, 2, 1, 0]
    assert fakes.applied == [0, 1, 2, 3]
    assert outcome.remade_slide_indices == (0, 1, 2, 3)
    # The envelope is resealed last for the highest index, so the deck state
    # is the one the serial path would have produced.
    assert outcome.scene_envelope == "envelope-after-3"
    assert kwargs["htmls"] == [f"<div>remade {i}</div>" for i in range(4)]


def test_one_failed_remake_does_not_cancel_the_others(monkeypatch, caplog):
    fakes = _FakeRemakes(outcomes={1: "raise", 2: "fail"}, delays={1: 0.001, 0: 0.01})
    _install(monkeypatch, fakes, concurrency=3)
    kwargs = _round_kwargs(
        flagged=[0, 1, 2, 3],
        gate_failures=[_overflow(0), _overflow(1), _overflow(2), _overflow(3)],
        count=4,
    )

    with caplog.at_level("INFO", logger=order_mod.LOGGER.name):
        outcome = _run(order_mod.remake_first_flagged_slides(**kwargs))

    assert sorted(fakes.finished) == [0, 1, 2, 3]
    assert outcome.remade_slide_indices == (0, 3)
    # Both an exception and a rejected candidate hand the slide, untouched, to
    # the patch round -- in the order the gate gave them.
    assert outcome.remaining_flagged == [1, 2]
    assert kwargs["htmls"][1] == "<div>slide 1</div>"
    assert "remake-first launch failed slide=2" in caplog.text
    assert "remake-first not admitted slide=3 outcome=remake_gate_failed" in caplog.text


def test_budgets_are_decided_before_any_call_is_launched(monkeypatch):
    count = MAX_TERMINAL_REMAKES_PER_DECK + 3
    fakes = _FakeRemakes()
    _install(monkeypatch, fakes, concurrency=4)
    kwargs = _round_kwargs(
        flagged=list(range(count)),
        gate_failures=[_overflow(i) for i in range(count)],
        count=count,
    )

    outcome = _run(order_mod.remake_first_flagged_slides(**kwargs))

    # Exactly the ceiling was launched, even with four lanes: the plan fixed
    # the spend before the first call, so no lane could overspend it.
    assert len(fakes.started) == MAX_TERMINAL_REMAKES_PER_DECK
    assert outcome.remake_counts == {i: 1 for i in range(MAX_TERMINAL_REMAKES_PER_DECK)}
    assert outcome.remaining_flagged == list(range(MAX_TERMINAL_REMAKES_PER_DECK, count))
    assert sum(outcome.remake_counts.values()) == MAX_TERMINAL_REMAKES_PER_DECK


def test_the_remake_width_is_the_authoring_waves_steady_width(monkeypatch):
    monkeypatch.setattr(
        schedule_mod,
        "resolve_authored_rate_profile",
        lambda: SimpleNamespace(steady_wave_concurrency=3),
    )
    assert schedule_mod.remake_concurrency() == 3


def test_the_remake_width_follows_the_wave_knob(monkeypatch):
    monkeypatch.setenv("AUTHORED_PROVIDER_RATE_PROFILE", "token")
    monkeypatch.delenv("AUTHORED_BATCH_WAVE_CONCURRENCY", raising=False)
    monkeypatch.delenv("AUTHORED_AUTHOR_CONCURRENCY", raising=False)
    assert schedule_mod.remake_concurrency() == 2
    monkeypatch.setenv("AUTHORED_BATCH_WAVE_CONCURRENCY", "4")
    assert schedule_mod.remake_concurrency() == 4


def test_the_bench_log_line_keeps_its_shape(monkeypatch, caplog):
    fakes = _FakeRemakes(outcomes={2: "fail"})
    _install(monkeypatch, fakes, concurrency=2)
    kwargs = _round_kwargs(
        flagged=[1, 2, 4],
        gate_failures=[_overflow(1), _overflow(2), _fragmentation(4)],
        count=5,
    )

    with caplog.at_level("INFO", logger=order_mod.LOGGER.name):
        _run(order_mod.remake_first_flagged_slides(**kwargs))

    lines = [record.getMessage() for record in caplog.records if "remake-first flagged=" in record.getMessage()]
    assert len(lines) == 1
    assert _LOG_LINE.match(lines[0]), lines[0]
    assert lines[0] == (
        "[authored] remake-first flagged=[1, 2, 4] text_scaled=[] remade=[1, 4] "
        "to_patch_round=[2]"
    )


# ---------------------------------------------------------------------------
# Q3: severity order, the fragmentation budget, the skipped patch round
# ---------------------------------------------------------------------------


def _defects(*failures):
    return order_mod.gate_defects_by_slide(list(failures))


def test_candidates_are_ordered_by_severity_then_slide_index():
    defects = _defects(_fragmentation(0), _capacity(1), _overlap(2), _overflow(3), _fragmentation(5))

    assert order_remake_candidates([5, 3, 0, 1, 2], defects) == [2, 3, 1, 0, 5]


def test_a_slide_with_both_kinds_of_defect_is_an_overflow_slide():
    defects = _defects(
        {"slide_index": 0, "issues": [_issue("fragmentation"), _issue("overflow")]},
        _fragmentation(1),
    )

    plan = plan_remakes([0, 1], defects, max_overflow_remakes=8)

    assert [(launch.index, launch.budget) for launch in plan.launches] == [
        (0, "overflow"),
        (1, "fragmentation"),
    ]
    assert plan.overflow_counts == {0: 1}
    assert plan.fragmentation_counts == {1: 1}


def test_fragmentation_remakes_do_not_draw_from_the_overflow_budget():
    overflow = list(range(MAX_TERMINAL_REMAKES_PER_DECK + 1))
    fragmentation = list(
        range(len(overflow), len(overflow) + MAX_FRAGMENTATION_REMAKES_PER_DECK + 1)
    )
    defects = _defects(
        *(_overflow(i) for i in overflow), *(_fragmentation(i) for i in fragmentation)
    )

    plan = plan_remakes(
        fragmentation + overflow, defects, max_overflow_remakes=MAX_TERMINAL_REMAKES_PER_DECK
    )

    launched_overflow = [item.index for item in plan.launches if item.budget == "overflow"]
    launched_fragmentation = [item.index for item in plan.launches if item.budget == "fragmentation"]
    assert launched_overflow == overflow[:MAX_TERMINAL_REMAKES_PER_DECK]
    assert launched_fragmentation == fragmentation[:MAX_FRAGMENTATION_REMAKES_PER_DECK]
    # Severity first: every overflow launch precedes every fragmentation launch.
    assert [item.budget for item in plan.launches] == (
        ["overflow"] * MAX_TERMINAL_REMAKES_PER_DECK
        + ["fragmentation"] * MAX_FRAGMENTATION_REMAKES_PER_DECK
    )
    # The overflow slide past its budget keeps the patch-first path; the
    # fragmentation slide past its budget is settled flagged, never patched.
    assert plan.to_patch_round == (overflow[-1],)
    assert plan.budget_exhausted == (
        {
            "index": fragmentation[-1],
            "reason": "composition_safety:fragmentation",
            "remakeAttempted": False,
            "remakeOutcome": "remake_budget_exhausted",
        },
    )


def test_a_slide_without_measured_defects_keeps_the_patch_first_path():
    plan = plan_remakes([4], {}, max_overflow_remakes=8)
    assert plan.launches == () and plan.to_patch_round == (4,)


def test_fragmentation_only_leftovers_skip_the_patch_round(monkeypatch):
    fakes = _FakeRemakes(outcomes={0: "fail", 2: "fail"})
    _install(monkeypatch, fakes, concurrency=2)
    kwargs = _round_kwargs(
        flagged=[0, 1, 2],
        gate_failures=[_fragmentation(0), _fragmentation(1), _overflow(2)],
        count=3,
    )

    outcome = _run(order_mod.remake_first_flagged_slides(**kwargs))

    # Every launch was one call; the overflow slide launched first.
    assert fakes.started[0] == 2 and sorted(fakes.started) == [0, 1, 2]
    assert outcome.remade_slide_indices == (1,)
    # Only the overflow leftover reaches the patch round.
    assert outcome.remaining_flagged == [2]
    assert outcome.remake_counts == {2: 1}
    assert outcome.fragmentation_counts == {0: 1, 1: 1}
    assert outcome.predeclared_terminal == (
        {
            "index": 0,
            "reason": "composition_safety:fragmentation",
            "remakeAttempted": True,
            "remakeOutcome": "remake_gate_failed",
            "remakeCount": 1,
        },
    )
    assert kwargs["htmls"][0] == "<div>slide 0</div>"


def test_the_terminal_round_publishes_a_settled_fragmentation_slide_without_a_call(monkeypatch):
    calls: list[int] = []

    async def must_not_remake(**kwargs):  # pragma: no cover - the assertion
        calls.append(kwargs["index"])
        raise AssertionError("a settled fragmentation slide must not be remade again")

    monkeypatch.setattr(terminal_mod, "remake_slide_once", must_not_remake)
    monkeypatch.setattr(
        terminal_mod, "_blocking_defects", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        terminal_mod, "get_cached_browser_metrics", lambda _html: {"telemetry": "ok"}
    )
    declaration = {
        "index": 0,
        "reason": "composition_safety:fragmentation",
        "remakeAttempted": True,
        "remakeOutcome": "remake_provider_failed",
        "remakeCount": 1,
    }

    outcome = _run(
        terminal_mod.resolve_terminal_gate_slides(
            htmls=["<div>a</div>", "<div>b</div>"], pngs=[b"a", b"b"],
            contents=["a", "b"], roles=["body", "body"],
            brand=SimpleNamespace(primary="#000"), design_system="DS",
            style=SimpleNamespace(id="editorial"), capacity_plan=None,
            profile=SimpleNamespace(profile_id="ordinary", strict_dom_gate=True, allow_summarization=True),
            reference_mode="style_reference", scene_envelope="sealed",
            metrics_by_slide={}, predeclared=[declaration],
        )
    )

    assert calls == []
    assert outcome.remake_calls == 0
    assert outcome.flagged_slides == (
        {
            "index": 0,
            "reason": "composition_safety:fragmentation",
            "remakeAttempted": True,
            "remakeOutcome": "remake_provider_failed",
        },
    )
    assert outcome.scene_envelope == "sealed"


def test_an_ordinary_declaration_still_takes_the_terminal_remake_path():
    entry = schedule_mod.settled_flag_entry(
        index=3, declaration={"index": 3, "reason": "x", "repeated": False},
        reason="x", repeated=False,
    )
    assert entry is None
