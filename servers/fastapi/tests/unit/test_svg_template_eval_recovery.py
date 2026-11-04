import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "svg_template_eval"))

from recovery import (  # noqa: E402
    atomic_write_json, load_existing, map_job_to_cell, newest_per_cell, plan_resume, template_id_of)

CELLS = [
    {"cell": "a__A", "input": "a", "payload": {"svgTemplatePin": {"templateId": "tA"}}},
    {"cell": "a__B", "input": "a", "payload": {"svgTemplatePin": {"templateId": "tB"}}},
    {"cell": "b__A", "input": "b", "payload": {"svgTemplatePin": {"templateId": "tA"}}},
]
INPUTS = {"a": "text a", "b": "text b"}


def test_template_id_accepts_both_spellings():
    assert template_id_of({"svg_template_pin": {"template_id": "x"}}) == "x"
    assert template_id_of({"svgTemplatePin": {"templateId": "y"}}) == "y"
    assert template_id_of({}) is None


def test_map_job_to_cell_by_template_and_content():
    assert map_job_to_cell({"svg_template_pin": {"template_id": "tB"}, "content": "text a"}, CELLS, INPUTS) == "a__B"
    assert map_job_to_cell({"svg_template_pin": {"template_id": "tA"}, "content": "text b"}, CELLS, INPUTS) == "b__A"


def test_map_job_to_cell_rejects_strangers():
    assert map_job_to_cell({"svg_template_pin": {"template_id": "tC"}, "content": "text a"}, CELLS, INPUTS) is None
    assert map_job_to_cell({"svg_template_pin": {"template_id": "tA"}, "content": "other"}, CELLS, INPUTS) is None
    assert map_job_to_cell({"content": "text a"}, CELLS, INPUTS) is None
    assert map_job_to_cell(None, CELLS, INPUTS) is None


def test_newest_per_cell_with_cutoff():
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    items = [{"cell": "a__A", "created_at": t0 + timedelta(hours=h), "id": h} for h in (1, 5, 3)]
    items += [{"cell": "a__B", "created_at": t0 + timedelta(hours=2), "id": 9}, {"cell": None, "created_at": t0}]
    assert newest_per_cell(items)["a__A"]["id"] == 5
    assert newest_per_cell(items, after=t0 + timedelta(hours=2)).keys() == {"a__A"}
    assert newest_per_cell(items, after=t0 + timedelta(hours=9)) == {}


def test_plan_resume_skips_complete_cells_and_drops_failures():
    runs = [{"cell": "a__A", "run": 1, "completed": True}, {"cell": "a__B", "run": 1, "completed": False},
            {"cell": "zzz", "run": 1, "completed": True}]
    kept, todo = plan_resume(runs, ["a__A", "a__B", "b__A"], 1)
    assert kept == [runs[0]]
    assert todo == {"a__B": (1, 1), "b__A": (1, 1)}


def test_plan_resume_partial_cell_continues_numbering():
    runs = [{"cell": "a__A", "run": 1, "completed": True}, {"cell": "a__A", "run": 2, "completed": False}]
    kept, todo = plan_resume(runs, ["a__A"], 3)
    assert len(kept) == 1 and todo == {"a__A": (2, 2)}
    assert plan_resume(runs, ["a__A"], 1)[1] == {}


def test_atomic_write_replaces_and_leaves_no_temp(tmp_path):
    target = tmp_path / "out" / "r.json"
    atomic_write_json(target, {"label": "x", "v": "한글"})
    atomic_write_json(target, {"label": "x", "v": 2})
    assert json.loads(target.read_text(encoding="utf-8")) == {"label": "x", "v": 2}
    assert [p.name for p in target.parent.iterdir()] == ["r.json"]


def test_atomic_write_failure_keeps_old_file(tmp_path):
    target = tmp_path / "r.json"
    atomic_write_json(target, {"label": "x"})
    with pytest.raises(TypeError):
        atomic_write_json(target, {"bad": object()})
    assert json.loads(target.read_text()) == {"label": "x"}
    assert [p.name for p in tmp_path.iterdir()] == ["r.json"]


def test_load_existing(tmp_path):
    target = tmp_path / "base.json"
    assert load_existing(target, "base") is None
    atomic_write_json(target, {"label": "base", "runs": []})
    assert load_existing(target, "base")["runs"] == []
    with pytest.raises(SystemExit):
        load_existing(target, "other")
