"""The Hybrid history has to recognise both lanes that record an attempt.

The isolated public lane is the one the product's export command uses. It
records the requested and delivered modes and names the worker as its compiler,
so a predicate written only against the direct lane's ``export_mode`` reported
"no Hybrid exports recorded" for a deck that had just produced one.
"""

from __future__ import annotations

from services.presentation_export_hybrid_attempt import provenance_is_hybrid_attempt


def _evidence(**fields: object) -> dict[str, object]:
    return {"capability_evidence": dict(fields)}


def test_direct_lane_export_mode_is_recognised() -> None:
    assert provenance_is_hybrid_attempt(_evidence(export_mode="hybrid"))
    assert not provenance_is_hybrid_attempt(_evidence(export_mode="fidelity"))


def test_isolated_lane_delivered_hybrid_is_recognised() -> None:
    # Exactly the provenance a successful workspace export writes.
    assert provenance_is_hybrid_attempt(
        _evidence(
            pptx_mode="hybrid",
            requested_pptx_mode="hybrid",
            delivered_export_mode="editable-hybrid",
        )
    )


def test_a_hybrid_request_that_fell_back_to_raster_still_belongs_in_the_history() -> None:
    # The dialog exists to show these: the request was Hybrid, the file is not.
    assert provenance_is_hybrid_attempt(
        _evidence(
            pptx_mode="fidelity",
            requested_pptx_mode="hybrid",
            delivered_export_mode="fidelity-raster",
        )
    )


def test_a_plain_fidelity_export_is_not_hybrid_history() -> None:
    assert not provenance_is_hybrid_attempt(
        _evidence(
            pptx_mode="fidelity",
            requested_pptx_mode="fidelity",
            delivered_export_mode="fidelity-raster",
        )
    )
    assert not provenance_is_hybrid_attempt({})
    assert not provenance_is_hybrid_attempt(None)


def test_compiler_and_coverage_manifest_remain_sufficient_on_their_own() -> None:
    assert provenance_is_hybrid_attempt(
        {"compiler_runtime": {"compiler": "authored-hybrid"}}
    )
    assert provenance_is_hybrid_attempt({"coverage_manifest": {"schemaVersion": "x"}})
    assert not provenance_is_hybrid_attempt(
        {"compiler_runtime": {"compiler": "public-isolated-export"}}
    )


def test_malformed_provenance_sections_do_not_raise() -> None:
    assert not provenance_is_hybrid_attempt({"capability_evidence": "hybrid"})
    assert not provenance_is_hybrid_attempt({"compiler_runtime": []})
