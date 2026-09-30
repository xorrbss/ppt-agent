import asyncio
import hashlib
import io
import json
import logging
from types import SimpleNamespace

import pytest
from PIL import Image

from services import authored_quality_review_service as quality_service
from utils.authored_dom_quality import STYLE_REFERENCE, DomQualityIssue, DomQualityReport
from services.generation_core.authoring import author_vision_qa
from services.generation_core.authoring.author_slide import Brand
from utils.llm_calls.critique_slide import CritiqueIssue, SlideCritique
from utils.renderer_isolation import RendererPolicyError


def _png(color: tuple[int, int, int]) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (320, 180), color).save(output, format="PNG")
    return output.getvalue()


def test_hybrid_review_escalates_a_failed_clean_audit_to_full_batch(monkeypatch):
    calls: list[list[str]] = []
    clean = SlideCritique(needs_fix=False, issues=[])
    defect = SlideCritique(
        needs_fix=True,
        issues=[
            CritiqueIssue(
                type="overflow", severity="high", detail="audited text is clipped"
            )
        ],
    )

    async def fake_critique(_images, *, contexts, **_kwargs):
        calls.append(list(contexts))
        if contexts[0].startswith("Review this labelled"):
            return [clean]
        if len(calls) == 2:
            return [defect, clean]
        return [clean for _ in contexts]

    monkeypatch.setattr(quality_service, "critique_authored", fake_critique)
    monkeypatch.setattr(
        quality_service,
        "_observe_critique_cache",
        lambda images, contexts, **kwargs: (0, len(images)),
    )

    result = asyncio.run(
        quality_service._hybrid_style_initial_review(
            pngs=[_png((index * 20, 20, 40)) for index in range(8)],
            contexts=[f"slide {index}" for index in range(8)],
            positions=list(range(8)),
            slide_count=8,
            local_reports=[
                DomQualityReport(STYLE_REFERENCE, (), (), 0) for _ in range(8)
            ],
            sampling_seed="audit-escalation",
        )
    )

    critiques, routing, cache_hits, cache_misses, _montage_ms, _detail_ms = result
    assert [len(call) for call in calls] == [1, 2, 6]
    assert routing["escalation"]["level"] == "batch"
    assert routing["detail_vqa_count"] == 8
    assert routing["montage_vqa_batch_count"] == 1
    assert routing["detail_vqa_batch_count"] == 2
    assert routing["vqa_batch_count"] == 3
    assert cache_hits == 0
    assert cache_misses == 9
    assert any(critique.needs_fix for critique in critiques)


def test_slide_result_contract_exposes_patch_target_and_issue_delta():
    before = SlideCritique(
        needs_fix=True,
        issues=[
            CritiqueIssue(type="overflow", severity="high", detail="title is clipped")
        ],
    )
    after = SlideCritique(needs_fix=False, issues=[])

    results = quality_service._build_slide_results(
        positions=[4],
        initial_critiques=[before],
        final_critiques=[after],
        repair_details=[
            {
                "local_index": 0,
                "path": "local_patch",
                "element_changes": [
                    {
                        "qid": "q-h1-title",
                        "action": "setStyle",
                        "before": {"styles": {"font-size": "42px"}},
                        "after": {"styles": {"font-size": "38px"}},
                    }
                ],
            }
        ],
        candidate_decisions=[
            {
                "slide_index": 4,
                "accepted": True,
                "reason": "improved",
                "attempted": True,
                "applied": True,
                "rejection_reason": None,
                "path": "local_patch",
            }
        ],
    )

    assert results == [
        {
            "slide_index": 4,
                "path": "local_patch",
                "attempted": True,
                "accepted": True,
                "applied": True,
                "apply_status": "not_started",
                "publish_status": "not_started",
                "post_verify_status": "not_started",
                "rolled_back": False,
                "rejection_reason": None,
            "resolved_issues": ["title is clipped"],
            "remaining_issues": [],
            "slide_level_resolved_issues": [],
            "slide_level_remaining_issues": [],
            "element_changes": [
                {
                    "element_id": "q-h1-title",
                    "before": '{"styles": {"font-size": "42px"}}',
                    "after": '{"styles": {"font-size": "38px"}}',
                    "path": "local_patch",
                    "attempted": True,
                    "applied": True,
                    "rejection_reason": None,
                    "resolved_issues": ["title is clipped"],
                    "remaining_issues": [],
                }
            ],
        }
    ]


def test_slide_result_rejected_candidate_is_attempted_but_never_applied():
    before = SlideCritique(
        needs_fix=True,
        issues=[
            CritiqueIssue(
                type="overflow",
                severity="high",
                detail="q-title text is clipped near the right edge",
            )
        ],
    )
    after = SlideCritique(
        needs_fix=True,
        issues=[
            CritiqueIssue(
                type="overflow",
                severity="high",
                detail="q-title still overflows the right boundary",
            )
        ],
    )

    result = quality_service._build_slide_results(
        positions=[2],
        initial_critiques=[before],
        final_critiques=[after],
        repair_details=[
            {
                "local_index": 0,
                "path": "local_patch",
                "attempted": True,
                "succeeded": True,
                "element_changes": [
                    {
                        "qid": "q-title",
                        "action": "resize",
                        "before": {"width": 500},
                        "after": {"width": 460},
                    }
                ],
            }
        ],
        candidate_decisions=[
            {
                "slide_index": 2,
                "accepted": False,
                "reason": "unchanged",
                "attempted": True,
                "applied": False,
                "rejection_reason": "unchanged",
                "path": "local_patch",
            }
        ],
    )[0]

    assert result["attempted"] is True
    assert result["applied"] is False
    assert result["rejection_reason"] == "unchanged"
    assert result["resolved_issues"] == []
    assert result["remaining_issues"] == ["q-title still overflows the right boundary"]
    assert result["element_changes"][0]["applied"] is False
    assert result["element_changes"][0]["rejection_reason"] == "unchanged"
    assert result["element_changes"][0]["remaining_issues"] == [
        "q-title still overflows the right boundary"
    ]


_VALID_HTML = (
    "<!DOCTYPE html><html lang='ko'><head><meta charset='utf-8'></head>"
    "<body><h1 data-qid='q-title'>Title</h1></body></html>"
)


def test_forced_full_reauthor_is_bounded_and_reports_success(monkeypatch):
    author_calls = []

    async def fake_author(*args, reasoning_effort=None, **kwargs):
        author_calls.append(reasoning_effort)
        return _VALID_HTML.replace("Title", "Improved")

    async def fake_render(_html, timeout=60):
        return b"fallback-png"

    monkeypatch.setattr(author_vision_qa, "author_slide_html", fake_author)
    monkeypatch.setattr(author_vision_qa, "render_html_to_png", fake_render)

    htmls, pngs, fixed, details = asyncio.run(
        author_vision_qa.revise_authored_deck(
            [_VALID_HTML],
            [b"original-png"],
            ["content"],
            ["CONTENT"],
            Brand(topic="topic"),
            "design",
            initial_critiques=[
                SlideCritique(
                    needs_fix=True,
                    issues=[
                        CritiqueIssue(
                            type="overflow",
                            severity="high",
                            detail="title is clipped",
                        )
                    ],
                )
            ],
            return_details=True,
            force_full_reauthor_indices=[0],
        )
    )

    # The request-scoped durable generation preset owns reasoning effort.
    assert author_calls == [None]
    assert fixed == [0]
    assert "Improved" in htmls[0]
    assert pngs == [b"fallback-png"]
    assert details[0]["path"] == "full_fallback"
    assert details[0]["patch"]["attempted"] is False
    assert details[0]["fallback"] == {
        "attempted": True,
        "succeeded": True,
        "failure_detail": None,
    }


def test_full_reauthor_does_not_exceed_consumed_provider_budget(monkeypatch):
    author_calls = 0

    async def fake_author(*_args, **_kwargs):
        nonlocal author_calls
        author_calls += 1
        return _VALID_HTML.replace("Title", "Unexpected")

    monkeypatch.setattr(author_vision_qa, "author_slide_html", fake_author)

    htmls, pngs, fixed, details = asyncio.run(
        author_vision_qa.revise_authored_deck(
            [_VALID_HTML],
            [b"original-png"],
            ["content"],
            ["CONTENT"],
            Brand(topic="topic"),
            "design",
            initial_critiques=[
                SlideCritique(
                    needs_fix=True,
                    issues=[
                        CritiqueIssue(
                            type="overflow",
                            severity="high",
                            detail="aggregate body budget exceeded",
                        )
                    ],
                )
            ],
            return_details=True,
            force_full_reauthor_indices=[0],
            provider_recovery_allowed=[False],
        )
    )

    assert author_calls == 0
    assert htmls == [_VALID_HTML]
    assert pngs == [b"original-png"]
    assert fixed == []
    assert details[0]["fallback"]["attempted"] is False


def test_full_reauthor_canonicalizes_time_once_and_keeps_active_tags_blocked(
    monkeypatch,
):
    author_calls = 0
    rendered: list[str] = []
    candidate = (
        "<!DOCTYPE html><html lang='ko'><head><meta charset='utf-8'></head>"
        "<body><time datetime='2026-08-29T09:30:00+09:00' "
        "data-qid='release.timestamp' class='stamp' style='color:#123456'>"
        "2026년 8월 29일 09:30</time></body></html>"
    )

    async def fake_author(*_args, **_kwargs):
        nonlocal author_calls
        author_calls += 1
        return candidate

    async def fake_render(html, timeout=60):
        rendered.append(html)
        return b"canonicalized-png"

    monkeypatch.setattr(author_vision_qa, "author_slide_html", fake_author)
    monkeypatch.setattr(author_vision_qa, "render_html_to_png", fake_render)

    htmls, pngs, fixed, _details = asyncio.run(
        author_vision_qa.revise_authored_deck(
            [_VALID_HTML],
            [b"original-png"],
            ["content"],
            ["CONTENT"],
            Brand(topic="topic"),
            "design",
            initial_critiques=[
                SlideCritique(
                    needs_fix=True,
                    issues=[
                        CritiqueIssue(
                            type="overflow",
                            severity="high",
                            detail="timestamp is clipped",
                        )
                    ],
                )
            ],
            return_details=True,
            force_full_reauthor_indices=[0],
        )
    )

    admitted = htmls[0]
    assert author_calls == 1
    assert rendered == [admitted]
    assert pngs == [b"canonicalized-png"]
    assert fixed == [0]
    assert "<time" not in admitted and "</time>" not in admitted
    assert "<span" in admitted and "</span>" in admitted
    assert 'title="2026-08-29T09:30:00+09:00"' in admitted
    assert "data-qid='release.timestamp'" in admitted
    assert "class='stamp'" in admitted
    assert "style='color:#123456'" in admitted
    assert "2026년 8월 29일 09:30" in admitted

    dangerous_documents = (
        _VALID_HTML.replace("<h1", "<script").replace("</h1>", "</script>"),
        _VALID_HTML.replace("<h1", "<iframe").replace("</h1>", "</iframe>"),
    )
    for dangerous in dangerous_documents:
        canonical = author_vision_qa._canonicalize_full_reauthor_semantic_aliases(
            dangerous
        )
        assert canonical == dangerous
        with pytest.raises(RendererPolicyError):
            author_vision_qa.validate_authored_slide_html(
                canonical, finalized=True
            )

    # H1 (docs/authored-quality-speed-programme-design-20260905.md section 6)
    # removes every non-hoistable at-rule before admission, so a remote
    # ``@import`` no longer reaches the gate to be rejected: it is deleted
    # whole, and the admitted document can never fetch the stylesheet.
    remote_import = _VALID_HTML.replace(
        "<h1 data-qid='q-title'>Title</h1>",
        "<style>@import url(https://example.invalid/x.css)</style>",
    )
    canonical = author_vision_qa._canonicalize_full_reauthor_semantic_aliases(
        remote_import
    )
    assert canonical == remote_import
    stripped = author_vision_qa.validate_authored_slide_html(
        canonical, finalized=True
    )
    assert "@import" not in stripped and "example.invalid" not in stripped

    assert author_calls == 1


def test_forced_full_reauthor_failure_is_logged_and_machine_readable(
    monkeypatch, caplog
):
    async def fake_author(*_args, **_kwargs):
        raise RuntimeError("provider secret must not enter payload")

    monkeypatch.setattr(author_vision_qa, "author_slide_html", fake_author)

    with caplog.at_level(logging.ERROR):
        htmls, pngs, fixed, details = asyncio.run(
            author_vision_qa.revise_authored_deck(
                [_VALID_HTML],
                [b"original-png"],
                ["content"],
                ["CONTENT"],
                Brand(topic="topic"),
                "design",
                initial_critiques=[
                    SlideCritique(
                        needs_fix=True,
                        issues=[
                            CritiqueIssue(
                                type="overflow",
                                severity="high",
                                detail="title is clipped",
                            )
                        ],
                    )
                ],
                return_details=True,
                force_full_reauthor_indices=[0],
            )
        )

    assert htmls == [quality_service.ensure_authored_qids(_VALID_HTML)]
    assert pngs == [b"original-png"]
    assert fixed == []
    assert details[0]["succeeded"] is False
    assert details[0]["failure_detail"] == {
        "stage": "full_reauthor",
        "code": "full_reauthor_failed",
        "error_type": "RuntimeError",
    }
    assert "slide_index=0 stage=full_reauthor" in caplog.text


def _structural_repair_double(monkeypatch, *, final_decision="admitted", duplicate=False):
    from services.authored_structural_repair import RepairAssessment

    original = _VALID_HTML
    provisional = original.replace("data-qid='q-title'", "data-qid='q-title' style='padding:2px'")
    final = original.replace("data-qid='q-title'", "data-qid='q-title' style='padding:1px'")
    captures, proposals, assessed, inputs, advances = [], [], [], [], []
    metrics = {"elements": []}

    async def evaluator(slide_index, html, browser_metrics):
        assessed.append((slide_index, html))
        decision = "reject" if html == original else "provisional" if html == provisional else final_decision
        return RepairAssessment(
            decision=decision, reasons=("still_clipped",) if decision != "admitted" else (),
            evidence={
                "html_digest": "sha256:" + hashlib.sha256(html.encode()).hexdigest(),
                "metrics_digest": "sha256:" + hashlib.sha256(json.dumps(
                    browser_metrics, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                ).encode()).hexdigest(),
            },
        )

    evaluator.selected_digest = "sha256:" + hashlib.sha256(original.encode()).hexdigest()

    def advance(expected, assessment, html, browser_metrics, *, context=None):
        assert expected == evaluator.selected_digest
        advances.append((assessment.decision, html, context))
        evaluator.selected_digest = "sha256:" + hashlib.sha256(html.encode()).hexdigest()

    evaluator.advance = advance

    async def proposal(_png, _dom, _critique, **kwargs):
        proposals.append(kwargs)
        return SimpleNamespace(ok=True, plan=object())

    def apply(html, _plan, **_kwargs):
        inputs.append(html)
        candidate = provisional if html == original or duplicate else final
        return SimpleNamespace(ok=True, html=candidate, changes=[])

    async def capture(html, index):
        captures.append((html, index))
        return html, html.encode(), metrics

    async def unexpected(*_args, **_kwargs):
        raise AssertionError("Structural repair must not request critique, reauthor, or extra capture")

    monkeypatch.setattr(author_vision_qa, "propose_authored_slide_patch", proposal)
    monkeypatch.setattr(author_vision_qa, "apply_authored_patch", apply)
    monkeypatch.setattr(author_vision_qa, "critique_authored", unexpected)
    monkeypatch.setattr(author_vision_qa, "author_slide_html", unexpected)
    monkeypatch.setattr(author_vision_qa, "ensure_browser_metrics", unexpected)
    monkeypatch.setattr(author_vision_qa, "_structural_compact_dom", lambda *_args: [])
    monkeypatch.setattr(author_vision_qa, "build_structural_repair_scopes", lambda *_args, **_kwargs: (SimpleNamespace(
        layout_owner_qid="q-title", affected_qids=("q-title",), protected_qids=(),
    ),))
    monkeypatch.setattr(author_vision_qa, "inspect_authored_dom", lambda *_args, **_kwargs: DomQualityReport(
        STYLE_REFERENCE,
        (DomQualityIssue("clipping", "high", "title clipped", ("q-title",)),), (), 1,
    ))
    return SimpleNamespace(
        original=original, provisional=provisional, final=final,
        evaluator=evaluator, capture=capture, metrics=metrics,
        captures=captures, proposals=proposals, assessed=assessed, inputs=inputs,
        advances=advances,
    )


def test_structural_proposal_uses_per_slide_repair_patch_scope(monkeypatch):
    import uuid

    from services.provider_usage_instrumentation import (
        active_usage_scope_slide_index,
        provider_usage_scope,
    )
    from services.generation_core.authoring import author_structural_repair_loop

    double = _structural_repair_double(monkeypatch)
    seen_slide_indices = []
    opened_scopes = []
    original_proposal = author_vision_qa.propose_authored_slide_patch
    original_scope = author_structural_repair_loop.provider_usage_scope

    async def observing_proposal(*args, **kwargs):
        seen_slide_indices.append(active_usage_scope_slide_index())
        return await original_proposal(*args, **kwargs)

    def observing_scope(**kwargs):
        opened_scopes.append(kwargs)
        return original_scope(**kwargs)

    monkeypatch.setattr(
        author_vision_qa, "propose_authored_slide_patch", observing_proposal
    )
    monkeypatch.setattr(
        author_structural_repair_loop, "provider_usage_scope", observing_scope
    )
    job_id = uuid.uuid4()

    async def run():
        with provider_usage_scope(
            job_id=job_id,
            operation="authored_generation",
            stage="repair",
            attempt_number=4,
        ):
            return await author_vision_qa.revise_authored_deck(
                [double.original], [b"original"], ["content"], ["CONTENT"],
                Brand(topic="topic"), "design", max_cycles=1,
                slide_indices=[33], total_slides=35,
                candidate_evaluator=double.evaluator,
                initial_browser_metrics=[double.metrics],
                structural_candidate_capture=double.capture,
            )

    asyncio.run(run())

    assert seen_slide_indices == [33]
    assert opened_scopes == [{
        "job_id": job_id,
        "operation": "authored_generation",
        "stage": "repair_patch",
        "attempt_number": 4,
        "slide_index": 33,
    }]


@pytest.mark.parametrize("final_decision", ["admitted", "provisional", "reject"])
def test_structural_repair_accumulates_only_in_memory_until_admitted(monkeypatch, final_decision):
    double = _structural_repair_double(monkeypatch, final_decision=final_decision)
    results, stats = [], {}

    async def run():
        return await author_vision_qa.revise_authored_deck(
            [double.original], [b"original"], ["content"], ["CONTENT"],
            Brand(topic="topic"), "design", max_cycles=9, slide_indices=[33],
            total_slides=35, candidate_evaluator=double.evaluator,
            initial_browser_metrics=[double.metrics],
            structural_candidate_capture=double.capture,
            structural_results_sink=results, review_stats_sink=stats,
            fail_on_residual_dom_issues=True, return_details=True,
        )

    if final_decision == "admitted":
        htmls, pngs, fixed, details = asyncio.run(run())
        assert htmls == [double.final]
        assert pngs == [double.final.encode()]
        assert fixed == [0]
        assert details[0]["succeeded"] is True
    else:
        with pytest.raises(author_vision_qa.AuthoredVqaResidualDomIssuesError) as caught:
            asyncio.run(run())
        assert caught.value.htmls == [double.original]
        assert caught.value.pngs == [b"original"]
        assert caught.value.fixed == []
        assert caught.value.repair_results == results
        assert caught.value.repair_details[0]["succeeded"] is False
    assert double.inputs == [double.original, double.provisional]
    assert len(double.proposals) == len(double.captures) == 2
    assert all(proposal["retry_invalid_model_patch"] is False for proposal in double.proposals)
    assert all(proposal["allow_source_reflow_safe_styles"] is True for proposal in double.proposals)
    assert all(index == 33 for index, _html in double.assessed)
    assert [decision for decision, _html, _context in double.advances] == {
        "admitted": ["provisional", "admitted"],
        "provisional": ["provisional", "provisional"],
        "reject": ["provisional"],
    }[final_decision]
    assert results[0].slide_index == 33 and results[0].local_index == 0
    assert stats == {"vqaReviewedSlideCount": 0, "deterministicOnlySlideCount": 1}


def test_structural_repair_duplicate_digest_stops_before_second_capture(monkeypatch):
    double = _structural_repair_double(monkeypatch, duplicate=True)
    results = []
    htmls, pngs, fixed = asyncio.run(author_vision_qa.revise_authored_deck(
        [double.original], [b"original"], ["content"], ["CONTENT"],
        Brand(topic="topic"), "design", max_cycles=2,
        candidate_evaluator=double.evaluator, initial_browser_metrics=[double.metrics],
        structural_candidate_capture=double.capture, structural_results_sink=results,
    ))
    assert htmls == [double.original] and pngs == [b"original"] and fixed == []
    assert len(double.proposals) == 2 and len(double.captures) == 1
    assert results[0].cycle_history[-1]["stop_reason"] == "duplicate_candidate_digest"


def test_structural_repair_checks_shared_capture_budget_before_second_provider_call(monkeypatch):
    double = _structural_repair_double(monkeypatch)
    results = []
    budget = author_vision_qa.StructuralRepairBudget(
        max_candidate_captures=1, max_provider_transmissions=2,
    )
    htmls, pngs, fixed = asyncio.run(author_vision_qa.revise_authored_deck(
        [double.original], [b"original"], ["content"], ["CONTENT"],
        Brand(topic="topic"), "design", max_cycles=2,
        candidate_evaluator=double.evaluator, initial_browser_metrics=[double.metrics],
        structural_candidate_capture=double.capture, structural_results_sink=results,
        structural_budgets={0: budget},
    ))
    assert htmls == [double.original] and pngs == [b"original"] and fixed == []
    assert len(double.proposals) == len(double.captures) == 1
    assert budget.receipt() == {
        "candidate_capture_limit": 1,
        "candidate_captures": 1,
        "candidate_captures_remaining": 0,
        "provider_transmission_limit": 2,
        "provider_transmissions": 1,
        "provider_transmissions_remaining": 1,
    }
    assert results[0].cycle_history[-1]["stop_reason"] == "candidate_capture_budget_exhausted"


def test_structural_evaluator_and_legacy_validator_are_mutually_exclusive():
    async def validate(*_args):
        raise AssertionError("No candidate work is allowed for an ambiguous contract")

    with pytest.raises(ValueError, match="mutually exclusive"):
        asyncio.run(author_vision_qa.revise_authored_deck(
            [_VALID_HTML], [b"png"], ["content"], ["CONTENT"],
            Brand(topic="topic"), "design", candidate_validator=validate,
            candidate_evaluator=validate,
        ))


def test_structural_admitted_initial_candidate_uses_original_checkpoint_digest(monkeypatch):
    from dataclasses import replace

    double = _structural_repair_double(monkeypatch)
    results = []

    async def evaluator(index, html, metrics):
        assessment = await double.evaluator(index, html, metrics)
        return replace(assessment, evidence={
            **assessment.evidence,
            "baseline_html_digest": "sha256:" + hashlib.sha256(double.original.encode()).hexdigest(),
        })

    htmls, pngs, fixed = asyncio.run(author_vision_qa.revise_authored_deck(
        [double.final], [b"already-rendered"], ["content"], ["CONTENT"],
        Brand(topic="topic"), "design", candidate_evaluator=evaluator,
        initial_browser_metrics=[double.metrics], structural_results_sink=results,
        structural_candidate_capture=double.capture,
    ))
    assert htmls == [double.final] and pngs == [b"already-rendered"] and fixed == [0]
    assert results[0].changed is True
    assert double.proposals == [] and double.captures == []


def test_structural_scope_rejection_does_not_advance_evaluator_history(monkeypatch):
    double = _structural_repair_double(monkeypatch)
    monkeypatch.setattr(author_vision_qa, "protected_scope_violations", lambda *_args: (
        "repair_protected_geometry_changed:q-footer",
    ))
    htmls, pngs, fixed = asyncio.run(author_vision_qa.revise_authored_deck(
        [double.original], [b"original"], ["content"], ["CONTENT"],
        Brand(topic="topic"), "design", max_cycles=2,
        candidate_evaluator=double.evaluator, initial_browser_metrics=[double.metrics],
        structural_candidate_capture=double.capture,
    ))
    assert htmls == [double.original] and pngs == [b"original"] and fixed == []
    assert double.assessed == [(0, double.original)]
    assert double.inputs == [double.original, double.original]
    assert len(double.captures) == 1
