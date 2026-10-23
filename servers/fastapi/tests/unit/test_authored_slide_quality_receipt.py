"""Per-slide infographic quality receipts."""

from __future__ import annotations

import json

import pytest

from models.authored_infographic_plan import (
    AuthoredInfographicPlan,
    AuthoredSlideInfographic,
    InfographicNode,
)
from services import authored_slide_quality_receipt as receipts
from services.authored_slide_quality_receipt import (
    AuthoredSlideQualityRecord,
    build_slide_quality_records,
    merge_slide_quality_fallbacks,
    record_slide_quality_receipt,
    slide_quality_receipt_path,
)


def load_slide_quality_receipt(presentation_id: object) -> dict:
    """Read one presentation's receipt back, or ``{}`` when it is absent or rejected."""

    try:
        return receipts._read_manifest(slide_quality_receipt_path(presentation_id))
    except Exception:
        return {}


@pytest.fixture(autouse=True)
def receipt_root(tmp_path, monkeypatch):
    root = tmp_path / "authored-quality-receipts"
    root.mkdir()
    monkeypatch.setattr(receipts, "quality_receipt_root", lambda: root)
    return root


def _plan() -> AuthoredInfographicPlan:
    nodes = tuple(
        InfographicNode(label=f"단계{index}") for index in range(1, 5)
    )
    return AuthoredInfographicPlan(
        slides=(
            AuthoredSlideInfographic(index=0, archetype="none", weight="quiet"),
            AuthoredSlideInfographic(
                index=1,
                archetype="process",
                weight="hero",
                density="high",
                nodes=nodes,
            ),
        )
    )


def test_records_carry_the_planned_archetype_and_density():
    records = build_slide_quality_records(slide_count=2, infographic_plan=_plan())

    assert [record.archetype for record in records] == ["none", "process"]
    assert records[1].density == "high"
    assert records[1].weight == "hero"
    assert records[1].node_count == 4


def test_unmeasured_variant_and_critic_score_are_absent_not_zero():
    records = build_slide_quality_records(slide_count=1, infographic_plan=_plan())

    assert records[0].variant_chosen is None
    assert records[0].critic_score is None
    assert records[0].to_dict()["criticScore"] is None


def test_a_slide_the_plan_does_not_cover_still_gets_a_record():
    records = build_slide_quality_records(slide_count=4, infographic_plan=_plan())

    assert len(records) == 4
    assert records[3].archetype == "none"
    assert records[3].node_count is None


def test_a_missing_plan_degrades_to_a_receipt_that_says_no_figure():
    records = build_slide_quality_records(slide_count=2, infographic_plan=None)

    assert [record.archetype for record in records] == ["none", "none"]


def test_the_receipt_round_trips_through_the_sidecar():
    path = record_slide_quality_receipt(
        "deck-1",
        build_slide_quality_records(slide_count=2, infographic_plan=_plan()),
        attempt_number=2,
    )

    assert path == slide_quality_receipt_path("deck-1")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schemaVersion"] == "authored-slide-quality-manifest/v1"
    assert payload["attemptNumber"] == 2
    assert payload["slides"]["1"]["archetype"] == "process"
    assert load_slide_quality_receipt("deck-1")["slides"]["1"]["density"] == "high"


def test_export_fallback_counts_merge_without_erasing_the_archetype():
    record_slide_quality_receipt(
        "deck-2", build_slide_quality_records(slide_count=2, infographic_plan=_plan())
    )

    merge_slide_quality_fallbacks("deck-2", {0: 0, 1: 4})

    slides = load_slide_quality_receipt("deck-2")["slides"]
    assert slides["1"]["archetype"] == "process"
    assert slides["1"]["fallbackCount"] == 4
    assert slides["0"]["fallbackCount"] == 0


def test_a_later_write_does_not_clear_a_field_it_does_not_know():
    record_slide_quality_receipt(
        "deck-3",
        (
            AuthoredSlideQualityRecord(
                slide_index=0, archetype="kpi", critic_score=4.5
            ),
        ),
    )

    record_slide_quality_receipt(
        "deck-3", (AuthoredSlideQualityRecord(slide_index=0, fallback_count=1),)
    )

    slide = load_slide_quality_receipt("deck-3")["slides"]["0"]
    assert slide["archetype"] == "kpi"
    assert slide["criticScore"] == 4.5
    assert slide["fallbackCount"] == 1


def test_a_tampered_manifest_is_discarded_rather_than_trusted():
    path = record_slide_quality_receipt(
        "deck-4", (AuthoredSlideQualityRecord(slide_index=0, archetype="kpi"),)
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["slides"]["0"]["archetype"] = "scene"
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert load_slide_quality_receipt("deck-4") == {}


def test_a_write_failure_never_raises_into_the_pipeline(monkeypatch):
    monkeypatch.setattr(
        receipts, "_write_manifest", lambda *a, **k: (_ for _ in ()).throw(OSError())
    )

    assert (
        record_slide_quality_receipt(
            "deck-5", (AuthoredSlideQualityRecord(slide_index=0),)
        )
        is None
    )


def test_an_empty_presentation_id_is_refused():
    with pytest.raises(ValueError):
        slide_quality_receipt_path("")


def test_a_negative_fallback_count_is_not_recorded():
    assert merge_slide_quality_fallbacks("deck-6", {0: -1}) is None


# --- E4 (docs/generation-programme-master-design-20260904.md section 8):
# fired A2 admission normalisations, merged in per slide. --------------------


def test_unmeasured_normalizations_are_absent_not_an_empty_list():
    records = build_slide_quality_records(slide_count=1, infographic_plan=_plan())

    assert records[0].normalizations is None
    assert records[0].to_dict()["normalizations"] is None


def test_normalizations_round_trip_through_the_sidecar_including_empty():
    records = build_slide_quality_records(
        slide_count=2,
        infographic_plan=_plan(),
        normalizations={
            0: [
                {
                    "rule": "hidden_element_without_bound_text_removed",
                    "detail": "div",
                    "count": 1,
                }
            ],
            1: [],
        },
    )

    assert records[0].normalizations == (
        {
            "rule": "hidden_element_without_bound_text_removed",
            "detail": "div",
            "count": 1,
        },
    )
    assert records[1].normalizations == ()

    path = record_slide_quality_receipt("deck-7", records)
    slides = json.loads(path.read_text(encoding="utf-8"))["slides"]
    assert slides["0"]["normalizations"] == [
        {
            "rule": "hidden_element_without_bound_text_removed",
            "detail": "div",
            "count": 1,
        }
    ]
    # Measured-but-empty is recorded as ``[]``, distinct from "not measured".
    assert slides["1"]["normalizations"] == []


def test_a_later_write_without_normalizations_does_not_clear_them():
    record_slide_quality_receipt(
        "deck-8",
        (
            AuthoredSlideQualityRecord(
                slide_index=0,
                normalizations=(
                    {"rule": "css_break_control_property_stripped", "detail": "x", "count": 1},
                ),
            ),
        ),
    )

    record_slide_quality_receipt(
        "deck-8", (AuthoredSlideQualityRecord(slide_index=0, fallback_count=2),)
    )

    slide = load_slide_quality_receipt("deck-8")["slides"]["0"]
    assert slide["normalizations"] == [
        {"rule": "css_break_control_property_stripped", "detail": "x", "count": 1}
    ]
    assert slide["fallbackCount"] == 2
