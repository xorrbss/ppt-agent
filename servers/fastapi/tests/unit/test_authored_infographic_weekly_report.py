"""The weekly per-archetype report over receipts and editor events."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

FASTAPI_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = FASTAPI_ROOT / "scripts" / "authored_infographic_weekly_report.py"


def _module():
    spec = importlib.util.spec_from_file_location(
        "authored_infographic_weekly_report", SCRIPT
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


report = _module()


def _receipt(tmp_path: Path, name: str, slides: dict, updated_at: int = 10_000) -> None:
    (tmp_path / name).write_text(
        json.dumps(
            {
                "schemaVersion": "authored-slide-quality-manifest/v1",
                "presentationId": name,
                "updatedAt": updated_at,
                "slides": slides,
            }
        ),
        encoding="utf-8",
    )


def test_receipts_are_grouped_by_archetype(tmp_path):
    _receipt(
        tmp_path,
        "deck-a-slide-quality.json",
        {
            "0": {"archetype": "process", "density": "high", "fallbackCount": 1},
            "1": {"archetype": "process", "density": "low", "fallbackCount": 5},
            "2": {"archetype": "none", "fallbackCount": 0},
        },
    )
    slides = report.load_receipt_slides(tmp_path, since=None)
    rows = report.summarize(report.aggregate(slides, []))

    process = next(row for row in rows if row["archetype"] == "process")
    assert process["slides"] == 2
    assert process["fallbacksPerSlide"] == 3.0
    assert process["slidesOverFallbackBudget"] == 1
    assert process["topDensity"] in {"high(1)", "low(1)"}


def test_an_unmeasured_critic_score_is_absent_rather_than_zero(tmp_path):
    _receipt(
        tmp_path,
        "deck-b-slide-quality.json",
        {"0": {"archetype": "kpi", "criticScore": None, "variantChosen": None}},
    )
    rows = report.summarize(
        report.aggregate(report.load_receipt_slides(tmp_path, since=None), [])
    )

    kpi = next(row for row in rows if row["archetype"] == "kpi")
    assert kpi["criticScore"] is None
    assert kpi["criticScoreMeasured"] == 0
    assert kpi["topVariant"] is None


def test_editor_events_become_edit_and_regeneration_rates(tmp_path):
    _receipt(
        tmp_path,
        "deck-c-slide-quality.json",
        {str(index): {"archetype": "cycle"} for index in range(4)},
    )
    events = [("cycle", "slide_regenerated", 2), ("cycle", "slide_deleted", 1)]
    rows = report.summarize(
        report.aggregate(report.load_receipt_slides(tmp_path, since=None), events)
    )

    cycle = next(row for row in rows if row["archetype"] == "cycle")
    assert cycle["slides"] == 4
    assert cycle["regenerationRate"] == 0.5
    assert cycle["editRate"] == 0.75
    assert cycle["events"]["slide_deleted"] == 1


def test_a_receipt_outside_the_window_is_not_counted(tmp_path):
    _receipt(tmp_path, "deck-d-slide-quality.json", {"0": {"archetype": "venn"}}, 100)

    assert report.load_receipt_slides(tmp_path, since=None)
    assert report.load_receipt_slides(tmp_path, since=1_000) == []


def test_a_corrupt_receipt_is_skipped_rather_than_fatal(tmp_path):
    (tmp_path / "deck-e-slide-quality.json").write_text("{ not json", encoding="utf-8")
    _receipt(tmp_path, "deck-f-slide-quality.json", {"0": {"archetype": "matrix"}})

    slides = report.load_receipt_slides(tmp_path, since=None)
    assert [slide["archetype"] for slide in slides] == ["matrix"]


def test_a_missing_receipts_directory_reports_nothing(tmp_path):
    assert report.load_receipt_slides(tmp_path / "absent", since=None) == []


def test_the_report_runs_end_to_end_without_a_database(tmp_path, capsys):
    _receipt(
        tmp_path,
        "deck-g-slide-quality.json",
        {"0": {"archetype": "funnel", "fallbackCount": 0}},
    )
    output = tmp_path / "report.json"

    assert (
        report.main(
            [
                "--receipts-dir",
                str(tmp_path),
                "--no-database",
                "--days",
                "0",
                "--json",
                str(output),
            ]
        )
        == 0
    )

    printed = capsys.readouterr().out
    assert "funnel" in printed
    assert json.loads(output.read_text(encoding="utf-8"))["archetypes"][0][
        "archetype"
    ] == "funnel"


def test_receipts_dir_requires_app_data_when_not_given(monkeypatch):
    monkeypatch.delenv("AUTHORED_QUALITY_RECEIPT_DIRECTORY", raising=False)
    monkeypatch.setenv("APP_DATA_DIRECTORY", "")

    with pytest.raises(SystemExit):
        report.resolve_receipts_dir(None)
