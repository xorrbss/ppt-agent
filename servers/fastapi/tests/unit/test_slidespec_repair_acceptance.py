"""Contracts for deterministic SlideSpec repair and fail-closed acceptance."""

import asyncio

from services.slidespec_acceptance import (
    AcceptanceEvidence,
    GateEvidence,
    decide_acceptance,
)
from services.slidespec_repair import (
    build_deterministic_cascade,
    next_cascade_action,
    repair_cost,
    select_candidate,
    sort_candidates,
)
from utils.llm_calls import propose_slidespec_repair as proposer


_BASE_HASH = "sha256:" + "a" * 64
_CANDIDATE_HASH = "sha256:" + "b" * 64
_CONTENT_HASH = "sha256:" + "c" * 64


def _gate(status="passed", **values):
    return GateEvidence(status=status, **values)


def _evidence(**replacements):
    values = {
        "schema": _gate(),
        "invariant": _gate(),
        "render": _gate(),
        "dom": _gate(
            coverage=1.0,
            new_high_medium_defects=0,
            non_regression=True,
            target_resolved=True,
            improved=True,
        ),
        "ocr": _gate(
            important_token_recall=1.0,
            impacted_recall=1.0,
            non_regression=True,
            improved=True,
        ),
        "vqa": _gate(non_regression=True, improved=True),
    }
    values.update(replacements)
    return AcceptanceEvidence(**values)


def _decide(trigger_type, evidence):
    return decide_acceptance(
        trigger_type,
        evidence,
        base_spec_hash=_BASE_HASH,
        candidate_spec_hash=_CANDIDATE_HASH,
        plan_id="rp_01",
        base_spec_revision=17,
    )


def _plan(plan_id, operations):
    return {"planId": plan_id, "operations": operations}


def test_geometry_acceptance_requires_dom_resolution_and_all_non_regressions():
    accepted = _decide("geometry", _evidence())
    unresolved = _decide(
        "geometry", _evidence(dom=_gate(
            coverage=1.0,
            new_high_medium_defects=0,
            non_regression=True,
            target_resolved=False,
            improved=True,
            reason_codes=("dom.clipping",),
        ))
    )

    assert accepted.outcome == "accepted"
    assert accepted.reason_codes == ("acceptance.all_required_gates_passed",)
    assert accepted.apply_status == "not_started"
    assert unresolved.outcome == "rejected"
    assert unresolved.reason_codes == (
        "dom.clipping",
        "acceptance.required_gate_failed",
    )


def test_vqa_improvement_cannot_override_dom_or_ocr_hard_failure():
    dom_failure = _decide(
        "visual_quality",
        _evidence(dom=_gate(
            coverage=1.0,
            new_high_medium_defects=1,
            non_regression=False,
            reason_codes=("dom.unintended_overlap",),
        )),
    )
    token_failure = _decide(
        "visual_quality",
        _evidence(ocr=_gate(
            important_token_recall=0.99,
            impacted_recall=1.0,
            non_regression=True,
            improved=True,
        )),
    )

    assert dom_failure.outcome == "rejected"
    assert dom_failure.reason_codes[0] == "dom.unintended_overlap"
    assert {stage.stage: stage.status for stage in dom_failure.stages}["vqa"] == "not_started"
    assert token_failure.outcome == "rejected"
    assert "ocr.important_token_missing" in token_failure.reason_codes


def test_required_sensor_timeout_and_incomplete_dom_fail_closed():
    timed_out = _decide(
        "geometry", _evidence(ocr=_gate(status="timeout"))
    )
    incomplete = _decide(
        "geometry",
        _evidence(dom=_gate(
            status="passed", coverage=0.9, new_high_medium_defects=0,
            non_regression=True, target_resolved=True, improved=True,
        )),
    )

    assert timed_out.outcome == "inconclusive"
    assert timed_out.reason_codes == ("ocr.timeout", "acceptance.inconclusive")
    assert incomplete.outcome == "inconclusive"
    assert "dom.telemetry_incomplete" in incomplete.reason_codes


def test_trigger_specific_improvement_rules_are_strict():
    geometry_tie = _decide(
        "geometry", _evidence(dom=_gate(
            coverage=1.0,
            new_high_medium_defects=0,
            non_regression=True,
            target_resolved=True,
            improved=False,
        ))
    )
    content_tie = _decide(
        "content_visibility", _evidence(ocr=_gate(
            important_token_recall=1.0,
            impacted_recall=0.99,
            non_regression=True,
            improved=False,
        ))
    )
    visual_tie = _decide(
        "visual_quality", _evidence(vqa=_gate(non_regression=True, improved=False))
    )

    assert geometry_tie.outcome == "rejected"
    assert "dom.not_improved" in geometry_tie.reason_codes
    assert content_tie.outcome == "rejected"
    assert visual_tie.outcome == "rejected"
    assert "vqa.not_improved" in visual_tie.reason_codes


def test_all_triggers_require_explicit_visual_improvement_and_reject_ties():
    for trigger_type in ("geometry", "content_visibility", "visual_quality"):
        decision = _decide(
            trigger_type,
            _evidence(vqa=_gate(non_regression=True, improved=False)),
        )
        assert decision.outcome == "rejected"
        assert "vqa.not_improved" in decision.reason_codes


def test_missing_improvement_evidence_is_inconclusive_not_accepted():
    missing_dom = _decide(
        "geometry",
        _evidence(dom=_gate(
            coverage=1.0,
            new_high_medium_defects=0,
            non_regression=True,
            target_resolved=True,
        )),
    )
    missing_vqa = _decide(
        "geometry", _evidence(vqa=_gate(non_regression=True))
    )

    assert missing_dom.outcome == "inconclusive"
    assert "dom.improvement_unavailable" in missing_dom.reason_codes
    assert missing_vqa.outcome == "inconclusive"
    assert "vqa.improvement_unavailable" in missing_vqa.reason_codes


def test_deterministic_cascade_has_required_priority_and_bounded_font_scale():
    plans = build_deterministic_cascade({
        "slideId": "slide-11",
        "baseSpecRevision": 17,
        "baseSpecHash": _BASE_HASH,
        "expectedContentDigest": _CONTENT_HASH,
        "diagnosticRefs": ["dom:clipping:title"],
        "spacing": {"target": "sem:slide-11/title", "current": 20, "candidate": 12},
        "containerBounds": {
            "target": "sem:slide-11/title",
            "current": {"x": 0.1, "y": 0.1, "width": 0.5, "height": 0.1},
            "candidate": {"x": 0.1, "y": 0.1, "width": 0.5, "height": 0.12},
        },
        "fontScale": {"target": "sem:slide-11/title", "current": 1.0, "candidate": 0.94},
    })

    assert all(plan.strategy == "deterministic_cascade" for plan in plans)
    assert [plan.operations[0].op for plan in plans] == [
        "setSpacing",
        "setBounds",
        "setFontScale",
    ]
    assert len({plan.plan_id for plan in plans}) == 3


def test_deterministic_cascade_tries_target_scoped_bounds_before_combined_plan():
    plans = build_deterministic_cascade({
        "slideId": "slide-1",
        "baseSpecRevision": 1,
        "baseSpecHash": _BASE_HASH,
        "expectedContentDigest": _CONTENT_HASH,
        "diagnosticRefsByTarget": {
            "sem:platform": ["dom:overlap:flow:footer"],
            "sem:title": ["dom:overflow:title"],
        },
        "containerBounds": [
            {
                "target": "sem:platform",
                "current": {"x": 0.7, "y": 0.1, "width": 0.25, "height": 0.7},
                "candidate": {"x": 0.7, "y": 0.1, "width": 0.25, "height": 0.82},
            },
            {
                "target": "sem:title",
                "current": {"x": 0.72, "y": 0.18, "width": 0.2, "height": 0.12},
                "candidate": {"x": 0.72, "y": 0.18, "width": 0.2, "height": 0.18},
            },
        ],
    })

    assert [tuple(plan.scope) for plan in plans] == [
        ("sem:platform",),
        ("sem:title",),
        ("sem:platform", "sem:title"),
    ]
    assert [len(plan.operations) for plan in plans] == [1, 1, 2]
    assert plans[0].diagnostic_refs == ("dom:overlap:flow:footer",)
    assert plans[1].diagnostic_refs == ("dom:overflow:title",)


def test_cascade_stop_rules_do_not_route_inconclusive_to_llm():
    assert next_cascade_action("accepted", has_more=True) == "stop_accepted"
    assert next_cascade_action("rejected", has_more=True) == "next_deterministic"
    assert next_cascade_action("rejected", has_more=False) == "call_llm"
    assert next_cascade_action("inconclusive", has_more=False) == "stop_inconclusive"


def test_repair_cost_is_lexicographic_and_tie_break_is_deterministic():
    font = _plan("a", [{"op": "setFontScale", "target": "sem:title", "scale": 0.95}])
    spacing_b = _plan("b", [{"op": "setSpacing", "target": "sem:title", "gap": 10}])
    spacing_a = _plan("a", [{"op": "setSpacing", "target": "sem:title", "gap": 11}])
    layout = _plan("layout", [{"op": "setBounds", "target": "sem:title", "normalizedDelta": 0.01}])

    assert repair_cost(spacing_a).font_shrink_ratio == 0
    assert sort_candidates([layout, font, spacing_b, spacing_a]) == (
        spacing_a,
        spacing_b,
        font,
        layout,
    )


def test_selector_uses_cheap_gates_then_stops_on_expensive_inconclusive():
    first = _plan("a", [{"op": "setSpacing", "target": "sem:title", "gap": 10}])
    second = _plan("b", [{"op": "setFontScale", "target": "sem:title", "scale": 0.95}])
    expensive_calls = []

    def acceptance(candidate):
        expensive_calls.append(candidate["planId"])
        return {"outcome": "inconclusive", "reasonCodes": ["ocr.timeout"]}

    selection = select_candidate(
        [second, first], cheap_gate=lambda _: "passed", acceptance_gate=acceptance
    )

    assert selection.outcome == "inconclusive"
    assert expensive_calls == ["a"]
    assert selection.evaluated == (first,)


def _proposal(strategy="minimal_change", operation=None):
    return {
        "plans": [{
            "planId": "rp_01",
            "slideId": "slide-11",
            "baseSpecRevision": 17,
            "baseSpecHash": _BASE_HASH,
            "expectedContentDigest": _CONTENT_HASH,
            "strategy": strategy,
            "operations": [operation or {
                "op": "setSpacing", "target": "sem:slide-11/title", "gap": 12,
            }],
        }]
    }


def _validate(content):
    return proposer.validate_proposal(
        content,
        semantic_ids={"sem:slide-11/title"},
        slide_id="slide-11",
        base_spec_revision=17,
        base_spec_hash=_BASE_HASH,
        expected_content_digest=_CONTENT_HASH,
    )


def test_llm_contract_rejects_forbidden_content_ops_raw_css_and_more_than_two():
    for invalid in (
        _proposal(operation={"op": "setText", "target": "sem:slide-11/title", "value": "changed"}),
        _proposal(operation={"op": "setSpacing", "target": "sem:slide-11/title", "css": "gap:0"}),
        {"plans": _proposal()["plans"] * 3},
    ):
        try:
            _validate(invalid)
        except ValueError as error:
            assert str(error).startswith(("proposal.", "schema."))
        else:
            raise AssertionError("unsafe proposal was accepted")


def test_llm_adapter_makes_one_call_and_returns_at_most_two_candidates(monkeypatch):
    calls = []

    async def fake_generate(client, model, messages):
        calls.append((client, model, messages))
        return _proposal()

    monkeypatch.setattr(proposer, "get_client", lambda *, config: "client")
    monkeypatch.setattr(proposer, "get_llm_config", lambda: "config")
    monkeypatch.setattr(proposer, "get_model", lambda: "repair-model")
    monkeypatch.setattr(proposer, "_generate_once", fake_generate)

    result = asyncio.run(proposer.propose_slidespec_repair(
        {"content": [{"semanticId": "sem:slide-11/title", "text": "secret"}]},
        [{"category": "clipping", "semanticId": "sem:slide-11/title"}],
        slide_id="slide-11",
        base_spec_revision=17,
        base_spec_hash=_BASE_HASH,
        expected_content_digest=_CONTENT_HASH,
    ))

    assert result.ok is True
    assert len(result.plans) == 1
    assert result.plans[0].provenance.source == "llm"
    assert len(calls) == 1
    user_prompt = calls[0][2][1].content
    assert "secret" not in user_prompt
    assert "<html" not in user_prompt.lower()


def test_llm_adapter_does_not_retry_invalid_json(monkeypatch):
    calls = []

    async def invalid(*args):
        calls.append(args)
        return {"notPlans": []}

    monkeypatch.setattr(proposer, "get_client", lambda *, config: "client")
    monkeypatch.setattr(proposer, "get_llm_config", lambda: "config")
    monkeypatch.setattr(proposer, "get_model", lambda: "repair-model")
    monkeypatch.setattr(proposer, "_generate_once", invalid)

    result = asyncio.run(proposer.propose_slidespec_repair(
        {"content": [{"semanticId": "sem:slide-11/title"}]},
        [],
        slide_id="slide-11",
        base_spec_revision=17,
        base_spec_hash=_BASE_HASH,
        expected_content_digest=_CONTENT_HASH,
    ))

    assert result.ok is False
    assert result.reason_codes == ("proposal.invalid_json",)
    assert len(calls) == 1
