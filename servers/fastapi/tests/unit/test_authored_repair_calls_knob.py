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


KNOB = "AUTHORED_REPAIR_CALLS_ENABLED"


def _clear(monkeypatch) -> None:
    monkeypatch.delenv(KNOB, raising=False)


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
