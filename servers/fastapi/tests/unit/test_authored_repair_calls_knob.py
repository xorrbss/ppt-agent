"""``AUTHORED_REPAIR_CALLS_ENABLED`` off spends no provider call on repair.

Measured on 2026-09-08: a 20-slide Sol deck spent 2,183 s of its 2,910 s wall
in the repair stage, and the August reference deck that the owner rates well
published with no repair round at all. Turning the rounds off has to be a
setting, not a code edit, so a deck can be generated at first-pass speed and
published flagged.

The deterministic, render-only text-scale pass is deliberately still allowed:
it costs no provider call.
"""

from __future__ import annotations

import asyncio

from services.authored_remake_schedule import are_repair_calls_enabled
from services.authored_terminal_slide_fallback import resolve_terminal_gate_slides
from utils.get_env_programme import is_authored_repair_calls_enabled

KNOB = "AUTHORED_REPAIR_CALLS_ENABLED"


def _clear(monkeypatch) -> None:
    monkeypatch.delenv(KNOB, raising=False)


def test_the_knob_defaults_to_on_so_behaviour_is_unchanged(monkeypatch):
    _clear(monkeypatch)
    assert is_authored_repair_calls_enabled() is True
    assert are_repair_calls_enabled() is True


def test_the_knob_turns_the_predicate_off(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv(KNOB, "false")
    assert are_repair_calls_enabled() is False
    monkeypatch.setenv(KNOB, "true")
    assert are_repair_calls_enabled() is True


def test_the_knob_is_registered_beside_the_other_authored_quality_gates():
    import json
    from pathlib import Path

    registry = json.loads(
        (Path(__file__).resolve().parents[2] / "constants" / "settings_registry.json").read_text(
            encoding="utf-8"
        )
    )
    entries = registry["settings"] if isinstance(registry, dict) else registry
    entry = next(item for item in entries if item.get("key") == KNOB)
    assert entry["type"] == "boolean"
    assert entry["default"] is True
    assert entry["secret"] is False
    assert entry["targets"] == ["fastapi.runtime"]
    assert entry["group"] == "authored.quality"


def test_the_remake_first_round_plans_no_launch_when_the_knob_is_off(monkeypatch):
    """The planner is handed an empty candidate list, so no budget is charged."""

    import services.authored_repair_round_order as order

    _clear(monkeypatch)
    monkeypatch.setenv(KNOB, "false")
    seen: list[list[int]] = []

    real_plan = order.plan_remakes

    def fake_plan(candidates, defects_by_index, **kwargs):
        seen.append(list(candidates))
        return real_plan(list(candidates), defects_by_index, **kwargs)

    async def fake_text_scale(**kwargs):
        return kwargs["scene_envelope"], set()

    monkeypatch.setattr(order, "plan_remakes", fake_plan)
    monkeypatch.setattr(order, "_text_scale_pass", fake_text_scale)
    monkeypatch.setattr(
        order,
        "remake_slide_once",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("no provider call allowed")),
    )

    outcome = asyncio.run(
        order.remake_first_flagged_slides(
            flagged=[1, 2, 3],
            gate_failures=[{"slide_index": 1, "issues": [{"category": "overflow"}]}],
            htmls=["<html></html>"] * 4, pngs=[b""] * 4, contents=["c"] * 4,
            roles=["BODY"] * 4, brand=object(), design_system="", style=None,
            capacity_plan=None, profile=object(), reference_mode="ordinary",
            scene_envelope=None, metrics_by_slide={},
        )
    )
    assert seen == [[]]
    assert outcome.remade_slide_indices == ()


def test_the_terminal_round_flags_instead_of_remaking_when_the_knob_is_off(monkeypatch):
    import services.authored_terminal_slide_fallback as fallback

    _clear(monkeypatch)
    monkeypatch.setenv(KNOB, "false")
    monkeypatch.setattr(fallback, "terminal_fallback_active", lambda **kwargs: True)
    monkeypatch.setattr(fallback, "_slide_metrics", lambda index, html, metrics: {"elements": []})
    monkeypatch.setattr(fallback, "_blocking_defects", lambda **kwargs: ("overflow",))
    monkeypatch.setattr(
        fallback,
        "remake_slide_once",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("no provider call allowed")),
    )

    result = asyncio.run(
        resolve_terminal_gate_slides(
            htmls=["<html></html>"], pngs=[b""], contents=["c"], roles=["BODY"],
            brand=object(), design_system="", style=None, capacity_plan=None,
            profile=object(), reference_mode="ordinary", scene_envelope=None,
            metrics_by_slide={},
        )
    )
    assert [entry["remakeOutcome"] for entry in result.flagged_slides] == ["repair_calls_disabled"]
    assert result.remade_slide_indices == ()


def test_the_patch_round_makes_no_provider_call_when_the_knob_is_off(monkeypatch):
    import services.authored_presentation_generation as generation

    _clear(monkeypatch)
    monkeypatch.setenv(KNOB, "false")
    monkeypatch.setattr(
        generation,
        "revise_authored_deck",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("no provider call allowed")),
    )
    html = "<html><body><p>a</p></body></html>"
    revised, fixed = asyncio.run(
        generation._revise_gate_repair_candidates(
            flagged=[0], htmls=[html], pngs=[b""], contents=["c"], roles=["BODY"],
            deterministic_candidates={0: html}, brand=object(), design_system="",
            repair_kwargs={},
        )
    )
    assert revised == [html]
    assert fixed == []
