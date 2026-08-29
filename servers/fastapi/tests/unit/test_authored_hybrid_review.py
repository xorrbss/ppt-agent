import asyncio
import io
import logging

import pytest
from PIL import Image

from services import authored_quality_review_service as quality_service
from utils.authored_dom_quality import STYLE_REFERENCE, DomQualityReport
from utils.llm_calls import author_vision_qa
from utils.llm_calls.author_slide import Brand
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

    assert author_calls == ["high"]
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
        _VALID_HTML.replace(
            "<h1 data-qid='q-title'>Title</h1>",
            "<style>@import url(https://example.invalid/x.css)</style>",
        ),
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
