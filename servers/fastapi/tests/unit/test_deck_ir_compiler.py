from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from services.authored_vnext.deck_ir.browser_closure import async_close_ranked_candidates, close_ranked_candidates
from services.authored_vnext.deck_ir.candidates import enumerate_candidates, select_deck_candidates
from services.authored_vnext.deck_ir.compiler import compile_slide
from services.authored_vnext.deck_ir.grammar import DeckIrGrammarError, default_design_grammar
from services.authored_vnext.deck_ir.hybrid_admission import admit_validated_hybrid_subset


def _inputs():
    digest = "sha256:" + "1" * 64
    slide = {"slideKey": "s01", "compositionIntentGraphDigest": digest, "artDirectionIntentDigest": digest, "components": [{"elementKey": "title", "kind": "text", "semanticRole": "title", "payload": {"text": "검증된 컴파일"}}, {"elementKey": "metric.primary", "kind": "metric", "semanticRole": "evidence", "payload": {"value": "40%", "label": "개선"}}]}
    composition = {"slideKey": "s01", "zones": [{"zoneKey": "claim", "contentRefs": ["title"], "minimumColumnSpan": 4, "preferredColumnSpan": 5, "maximumColumnSpan": 7}, {"zoneKey": "evidence", "contentRefs": ["metric.primary"], "minimumColumnSpan": 5, "preferredColumnSpan": 7, "maximumColumnSpan": 8}], "relations": [{"from": "claim", "relation": "leftOf", "to": "evidence", "strength": "hard"}], "readingOrder": ["claim", "evidence"]}
    art = {"focalAnchor": {"zoneKey": "evidence", "strength": "strong"}, "negativeSpace": {"bias": "right", "amount": "generous"}, "asymmetry": "moderate", "overlapPolicy": {"mode": "decorative-layering", "allowedZonePairs": [["claim", "evidence"]], "textOcclusion": "forbidden", "sourceOcclusion": "forbidden"}, "imageTreatment": "edge-crop", "cropIntent": "subject-emphasis", "typographyRhythm": "editorial-contrast", "depthIntent": "layered", "visualEnergy": "progressive"}
    return slide, composition, art


def test_deterministic_k6_compiler_closure_and_hybrid_admission_stay_bounded() -> None:
    grammar = default_design_grammar()
    slide, composition, art = _inputs()
    original = deepcopy(art)
    candidates = enumerate_candidates(slide=slide, composition=composition, art_direction=art, grammar=grammar)
    assert len(candidates) == 6
    assert len({item.layout_id.split("/")[0] for item in candidates}) == 6
    assert art == original
    assert all(item.resolution_receipt["pairDecisions"][0]["reason"] == "exclusive_content_overlap_forbidden" for item in candidates)
    assert all(item.resolved_art_intent["overlapPolicy"]["allowedZonePairs"] == [] for item in candidates)
    selection = select_deck_candidates(candidates_by_slide={"s01": candidates}, grammar=grammar)
    selected = selection.selected["s01"]
    def compile_candidate(candidate, revision, measurement_digest):
        return compile_slide(operation_id="00000000-0000-4000-8000-000000000001", deck_ir={"deckKey": "deck", "slides": [slide]}, slide=slide, grammar=grammar, candidate=candidate, selection_receipt=selection.receipt, closure_revision=revision, browser_measurement_digest=measurement_digest, strategy_decision_digest="sha256:" + "2" * 64)
    closure = close_ranked_candidates(ranked_candidates=selection.ranked["s01"], compile_candidate=compile_candidate, observe=lambda result: {"passed": True, "geometryByQid": {item["qid"]: {"x": 10, "y": 10, "width": 100, "height": 40} for item in result.semantic["elements"]}})
    assert closure.candidate == selected
    assert closure.result.receipt["htmlDigest"]
    assert closure.result.receipt["strategyDecisionDigest"] == "sha256:" + "2" * 64
    assert closure.result.receipt["browserMeasurementDigest"]
    assert "width:1280px" in closure.result.html
    assert "height:720px" in closure.result.html
    assert "font-size:max(15px,18px)" in closure.result.html
    assert f'layout-family-{selected.layout_id.split("/", 1)[0]}' in closure.result.html
    assert "data-renderer-owned" not in closure.result.html
    assert "data-slide-key" not in closure.result.html
    assert "data-asset-slot" not in closure.result.html
    repeat = compile_candidate(selected, 0, None)
    assert repeat.html == compile_candidate(selected, 0, None).html
    assert repeat.receipt["htmlDigest"] == compile_candidate(selected, 0, None).receipt["htmlDigest"]
    async def async_compile(candidate, revision, measurement_digest):
        return compile_candidate(candidate, revision, measurement_digest)
    async def async_observe(result):
        return {"passed": True, "geometryByQid": {item["qid"]: {"x": 10, "y": 10, "width": 100, "height": 40} for item in result.semantic["elements"]}}
    async_closure = asyncio.run(async_close_ranked_candidates(ranked_candidates=selection.ranked["s01"], compile_candidate=async_compile, observe=async_observe))
    assert async_closure.candidate == selected
    assert async_closure.receipt["receiptDigest"] == closure.receipt["receiptDigest"]
    assert [item["qid"] for item in closure.result.semantic["elements"]] == [
        "s01.root", "s01.zone.claim", "s01.title", "s01.zone.evidence", "s01.metric.primary",
        "s01.metric.primary.part01", "s01.metric.primary.part02"
    ]
    admission = admit_validated_hybrid_subset(semantic=closure.result.semantic, browser_observation={"passed": True, "geometryByQid": {item["qid"]: {"x": 0, "y": 0, "width": 100, "height": 40} for item in closure.result.semantic["elements"]}})
    assert admission.admitted and not admission.native_editability_ready and not admission.promotion_ready
    with pytest.raises(DeckIrGrammarError, match="raw_layout"):
        enumerate_candidates(slide=slide, composition={**composition, "x": 1}, art_direction=art, grammar=grammar)
    # Local exhaustion reaches the sixth pre-enumerated candidate; no new K,
    # provider call or content rewrite is introduced by a browser rejection.
    observed = []
    def late_pass(result):
        observed.append(result.receipt["candidateId"])
        return {"passed": len(observed) == 6, "checks": {"overlap": len(observed) == 6}}
    late = close_ranked_candidates(ranked_candidates=selection.ranked["s01"], compile_candidate=compile_candidate, observe=late_pass)
    assert late.receipt["candidateAttemptCount"] == 6
    assert late.candidate == selection.ranked["s01"][-1]
    assert late.receipt["providerCallsAdded"] == 0
    observed.clear()
    late_async = asyncio.run(async_close_ranked_candidates(ranked_candidates=selection.ranked["s01"], compile_candidate=async_compile, observe=late_pass))
    assert late_async.receipt == late.receipt
    with pytest.raises(DeckIrGrammarError, match="browser_measurement_closure_failed") as exhausted:
        close_ranked_candidates(ranked_candidates=selection.ranked["s01"], compile_candidate=compile_candidate, observe=lambda _: {"passed": False})
    assert exhausted.value.receipt["candidateAttemptCount"] == 6
    assert exhausted.value.receipt["status"] == "exhausted"
    tampered = replace(selected, resolved_art_intent={**selected.resolved_art_intent, "overlapPolicy": art["overlapPolicy"]})
    with pytest.raises(DeckIrGrammarError, match="art_intent_resolution_digest_mismatch"):
        close_ranked_candidates(ranked_candidates=[tampered], compile_candidate=compile_candidate, observe=lambda _: pytest.fail("integrity errors must not reach browser"))
    spoofed_slide = deepcopy(slide)
    spoofed_slide["components"][0]["semanticRole"] = "background"
    spoofed = enumerate_candidates(slide=spoofed_slide, composition=composition, art_direction=art, grammar=grammar)
    assert all(zone["capability"] == "exclusive-content" for zone in spoofed[0].resolved_art_intent["zoneCapabilities"])
    missing = deepcopy(art)
    missing["overlapPolicy"]["allowedZonePairs"] = [["claim", "missing"]]
    with pytest.raises(DeckIrGrammarError, match="art_direction_intent_invalid"):
        enumerate_candidates(slide=slide, composition=composition, art_direction=missing, grammar=grammar)
    # Structured values are rendered as real table/bar/process markup and
    # retained semantic leaves, not collapsed into the first title field.
    from services.authored_vnext.deck_ir.component_renderer import render_component
    from services.authored_vnext.deck_ir.compiler import assert_html_semantic_alignment
    payloads = [
        ("table", {"columns": ["연도", "점수"], "rows": [[2025, 82], [2026, 97]], "caption": "비교"}, "<table", ["연도", "점수", "2025", "82", "2026", "97"]),
        ("chart", {"chartType": "bar", "categories": ["기존", "신규"], "series": [{"name": "품질", "values": [82, 97]}]}, 'class="dir-bar"', ["품질", "기존", "82", "신규", "97"]),
        ("process", {"steps": [{"label": "분석", "detail": "CJK 보존"}, {"label": "검증", "detail": "실측"}]}, '<ol', ["분석", "CJK 보존", "검증", "실측"]),
    ]
    for kind, payload, markup, expected in payloads:
        html, elements = render_component("s01", {"elementKey": kind, "kind": kind, "semanticRole": "evidence", "payload": payload})
        assert markup in html
        assert set(expected).issubset({item["text"] for item in elements})
        assert_html_semantic_alignment(html, {"elements": elements})
    with pytest.raises(DeckIrGrammarError, match="payload_render_unsupported"):
        render_component("s01", {"elementKey": "image", "kind": "image", "semanticRole": "evidence", "payload": {"assetSlot": "hero", "altText": "원본"}})
    from services.authored_vnext.deck_ir.validation import requirement_coverage_totals
    graph = SimpleNamespace(requirements=[SimpleNamespace(requirement_id="req-001", priority="required", minimum_occurrences=2, maximum_occurrences=3)])
    occurrence = {"requirementId": "req-001", "slideKey": "s01", "elementKey": "claim"}
    assert requirement_coverage_totals(graph, [occurrence])["status"] == "failed"
    assert requirement_coverage_totals(graph, [occurrence, {**occurrence, "elementKey": "body"}])["status"] == "passed"
    over_limit = requirement_coverage_totals(graph, [{**occurrence, "elementKey": f"part{i}"} for i in range(4)])
    assert over_limit["status"] == "failed"
    assert over_limit["duplicateViolations"] == [{"requirementId": "req-001", "slideKeys": ["s01"], "reason": "maximum-occurrences-exceeded"}]
