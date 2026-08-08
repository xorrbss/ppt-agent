"""Characterization contracts for SlideSpec-era authored repair boundaries."""

import re
from types import SimpleNamespace
import uuid

from services import authored_quality_review_service as quality_service
from utils.authored_dom_quality import (
    DomQualityIssue,
    DomQualityReport,
    STYLE_REFERENCE,
    build_compact_dom_map,
    ensure_authored_qids,
)
from utils.authored_patch_engine import apply_authored_patch


def _plan(*actions):
    return {"strategy": "patch", "actions": list(actions), "rationale": "test"}


def test_d1_d2_void_elements_never_receive_generated_qids_or_capture_later_text():
    source = '<p data-qid="copy">Before<br>After<img src="hero.png">Tail</p>'

    enriched = ensure_authored_qids(source)
    nodes = build_compact_dom_map(enriched)

    assert enriched == ensure_authored_qids(enriched)
    assert re.search(r"<br(?:\s[^>]*)?\s+data-qid=", enriched) is None
    assert re.search(r"<img(?:\s[^>]*)?\s+data-qid=", enriched) is None
    assert [(node.tag, node.qid, node.text) for node in nodes] == [
        ("p", "copy", "Before After Tail")
    ]


def test_d2_set_text_rejects_a_hard_break_instead_of_flattening_it():
    source = '<p data-qid="copy">First<br>Second</p>'

    result = apply_authored_patch(
        source,
        _plan({"action": "setText", "qid": "copy", "text": "Flattened"}),
    )

    assert not result.ok
    assert result.html == source
    assert result.fallback_reason == "unsafe_patch"
    assert "hard line breaks" in (result.failure_reason or "")


def test_d3_browser_camel_case_styles_normalize_to_allowlisted_kebab_case():
    source = '<p data-qid="copy" style="font-size: 20px">Copy</p>'

    result = apply_authored_patch(
        source,
        _plan(
            {
                "action": "setStyle",
                "qid": "copy",
                "styles": {"fontSize": "24px", "marginTop": "8px"},
            }
        ),
    )

    assert result.ok
    assert 'style="font-size: 24px; margin-top: 8px"' in result.html
    assert result.changes[0].after == {
        "styles": {"font-size": "24px", "margin-top": "8px"}
    }


def test_d3_camel_and_kebab_aliases_are_duplicate_properties():
    result = apply_authored_patch(
        '<p data-qid="copy">Copy</p>',
        _plan(
            {
                "action": "setStyle",
                "qid": "copy",
                "styles": {"font-size": "24px", "fontSize": "22px"},
            }
        ),
    )

    assert not result.ok
    assert result.fallback_reason == "invalid_patch_schema"
    assert "duplicate CSS property 'font-size'" in (result.failure_reason or "")


def test_d4_ocr_routing_is_a_closed_signal_from_local_content_risk():
    report = DomQualityReport(
        reference_mode=STYLE_REFERENCE,
        issues=(
            DomQualityIssue(
                category="content",
                severity="high",
                detail="rendered text differs from DOM text",
                qids=("copy",),
            ),
        ),
        compact_dom=(),
        inspected_elements=1,
    )

    assert quality_service._ocr_route_signals(report, None) == ["dom:content"]
    assert quality_service._ocr_route_signals(None, None) == []


def test_mixed_route_metrics_add_counts_and_each_stage_duration():
    merged = quality_service._merge_slidespec_review_data(
        presentation_id=uuid.uuid4(),
        request=SimpleNamespace(mode="analyze_and_fix", scope="all"),
        legacy={
            "metrics": {
                "total_ms": 11.0,
                "cache_hits": 2,
                "stage_timings_ms": {"render": 4.0, "legacy_only": 3.0},
            }
        },
        canonical={
            "metrics": {
                "total_ms": 7.0,
                "cache_hits": 1,
                "llm_calls": 0,
                "stage_timings_ms": {"render": 2.5, "canonical_only": 1.0},
            }
        },
    )

    assert merged["metrics"]["total_ms"] == 18.0
    assert merged["metrics"]["cache_hits"] == 3
    assert merged["metrics"]["llm_calls"] == 0
    assert merged["metrics"]["stage_timings_ms"] == {
        "render": 6.5,
        "legacy_only": 3.0,
        "canonical_only": 1.0,
    }


def test_canonical_metrics_are_aggregated_and_exposed_to_legacy_summary_fields():
    merged = quality_service._merge_slidespec_review_data(
        presentation_id=uuid.uuid4(),
        request=SimpleNamespace(mode="analyze_and_fix", scope="all"),
        legacy={
            "metrics": {
                "candidate_proposed": 4,
                "rollback_count": 1,
                "solver": {
                    "elapsed_ms": 3.0,
                    "branches": 5,
                    "unknown": 1,
                    "search_complete": True,
                },
                "candidates": {"proposed": 4, "accepted": 2, "rejected": 2},
                "transaction": {"rollback_count": 1},
                "rollout": {"kill_switch_enabled": False},
            }
        },
        canonical={
            "metrics": {
                "solver": {
                    "elapsed_ms": 7.0,
                    "branches": 8,
                    "unknown": 2,
                    "unsat_core_size": 3,
                    "search_complete": False,
                },
                "candidates": {
                    "proposed": 3,
                    "rendered": 3,
                    "accepted": 1,
                    "rejected": 2,
                },
                "transaction": {"rollback_count": 2},
                "rollout": {"kill_switch_enabled": True},
            }
        },
    )

    metrics = merged["metrics"]
    assert metrics["solver"] == {
        "elapsed_ms": 10.0,
        "branches": 13,
        "unknown": 3,
        "unsat_core_size": 3,
        "search_complete": False,
    }
    assert metrics["candidates"] == {
        "proposed": 7,
        "rendered": 3,
        "accepted": 3,
        "rejected": 4,
    }
    assert metrics["transaction"] == {"rollback_count": 3}
    assert metrics["rollout"] == {"kill_switch_enabled": True}
    assert metrics["candidate_proposed"] == 7
    assert metrics["candidate_rendered"] == 3
    assert metrics["candidate_accepted"] == 1
    assert metrics["candidate_rejected"] == 2
    assert metrics["solver_elapsed_ms"] == 7.0
    assert metrics["solver_search_complete"] is False
    assert metrics["rollback_count"] == 3
    assert metrics["kill_switch_enabled"] is True
