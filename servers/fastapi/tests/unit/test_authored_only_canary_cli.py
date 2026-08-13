from __future__ import annotations

from scripts.authored_only_canary import evaluate_payload


def _payload(**changes):
    value = {
        "schema_version": 1,
        "run_id": "authored-only-r1",
        "release_id": "release-1",
        "stage": "5",
        "read": {"attempts": 1000, "successes": 1000, "p95_latency_ms": 100},
        "export": {
            "attempts": 1000,
            "successes": 1000,
            "p95_latency_ms": 1000,
        },
    }
    value.update(changes)
    return value


def test_canary_evidence_advances_only_from_green_content_free_evidence():
    result = evaluate_payload(_payload())

    assert result["decision"] == {
        "action": "advance",
        "current_stage": "5",
        "recommended_stage": "25",
        "may_expand": True,
        "reason_codes": ["canary_slo_passed"],
    }
    assert len(result["evidence_sha256"]) == 64


def test_canary_evidence_rolls_back_on_integrity_violation():
    result = evaluate_payload(_payload(source_or_archive_digest_mismatches=1))

    assert result["decision"]["action"] == "rollback"
    assert result["decision"]["may_expand"] is False
    assert result["decision"]["reason_codes"] == ["digest_mismatch"]
