"""Focused contracts for authored STYLE_REFERENCE baseline comparisons."""

import asyncio

import pytest

from services.authored_quality_acceptance import (
    style_comparison_improvement_decision,
    style_comparison_quality_gain,
)
from utils.llm_calls import author_vision_qa as author_vision_qa_mod
from utils.llm_calls import critique_slide as critique_slide_mod
from utils.llm_calls.author_slide import EXACT_RECONSTRUCTION, STYLE_REFERENCE
from utils.llm_calls.critique_slide import (
    SlideCritique,
    StyleComparisonCritique,
)


def _comparison_payload(*, preference="candidate", target_improved=True, confidence=0.99):
    return {
        "needs_fix": False,
        "issues": [],
        "preference": preference,
        "target_improved": target_improved,
        "confidence": confidence,
        "dimensions": [
            {
                "dimension": dimension,
                "verdict": "better" if dimension == "target-defect" else "same",
                "detail": "paired comparison",
            }
            for dimension in (
                "target-defect",
                "content",
                "readability",
                "hierarchy",
                "alignment",
                "spacing",
                "style",
            )
        ],
    }


def _run(coro):
    return asyncio.run(coro)


def _reset_comparison_cache():
    author_vision_qa_mod._style_comparison_vqa_cache.clear()
    author_vision_qa_mod._style_comparison_vqa_inflight.clear()
    author_vision_qa_mod._style_comparison_vqa_failures.clear()
    author_vision_qa_mod._vqa_sem = None


def test_style_comparison_prompt_is_non_authoritative_and_orders_images():
    baseline = b"baseline"
    candidate = b"candidate"

    messages = critique_slide_mod._build_style_comparison_messages(
        baseline,
        candidate,
        slide_context="preserve the quarterly totals",
    )

    system = messages[0].content
    assert "baseline is not ground truth" in system.lower()
    assert "fixes and visual changes are allowed" in system.lower()
    for defect in (
        "clipping",
        "overflow",
        "overlap",
        "content loss",
        "readability problems",
        "alignment errors",
        "aspect ratio distortion",
    ):
        assert defect in system
    assert "'REGRESSION:'" in system
    assert "'INDETERMINATE:'" in system

    content = messages[1].content
    assert len(content) == 4
    assert "not ground truth" in content[0].text.lower()
    assert content[1].data == baseline
    assert "post-fix candidate" in content[2].text.lower()
    assert "quarterly totals" in content[2].text
    assert content[3].data == candidate


def test_low_level_style_comparison_makes_one_structured_model_call(monkeypatch):
    calls = []

    async def fake_generate(client, model, **kwargs):
        calls.append((client, model, kwargs))
        return _comparison_payload()

    client = object()
    monkeypatch.setattr(critique_slide_mod, "get_llm_config", lambda: "config")
    monkeypatch.setattr(
        critique_slide_mod,
        "get_client",
        lambda *, config: client if config == "config" else None,
    )
    monkeypatch.setattr(critique_slide_mod, "get_model", lambda: "review-model")
    monkeypatch.setattr(
        critique_slide_mod,
        "generate_structured_with_schema_retries",
        fake_generate,
    )

    critique = _run(
        critique_slide_mod.critique_slide_style_comparison(
            b"baseline",
            b"candidate",
            slide_context="context",
        )
    )

    assert isinstance(critique, StyleComparisonCritique)
    assert critique.preference == "candidate"
    assert critique.target_improved is True
    assert len(calls) == 1
    assert calls[0][0:2] == (client, "review-model")
    content = calls[0][2]["messages"][1].content
    assert [content[1].data, content[3].data] == [b"baseline", b"candidate"]


def test_paired_improvement_decision_rejects_ties_and_legacy_payloads():
    improved = StyleComparisonCritique.model_validate(_comparison_payload())
    tie = StyleComparisonCritique.model_validate(
        _comparison_payload(preference="tie", target_improved=False)
    )

    assert style_comparison_improvement_decision(improved) == (
        True,
        "vqa.improved",
        True,
    )
    assert style_comparison_improvement_decision(tie) == (
        False,
        "vqa.not_improved",
        True,
    )
    assert style_comparison_improvement_decision(SlideCritique(needs_fix=False)) == (
        False,
        "vqa.improvement_unavailable",
        False,
    )
    assert style_comparison_quality_gain(improved) == pytest.approx(0.396)
    assert style_comparison_quality_gain(tie) is None


def test_paired_improvement_decision_fails_closed_on_uncertainty_or_regression():
    low_confidence = StyleComparisonCritique.model_validate(
        _comparison_payload(confidence=0.5)
    )
    regressed_payload = _comparison_payload(preference="baseline")
    regressed_payload["dimensions"][2]["verdict"] = "worse"
    regressed = StyleComparisonCritique.model_validate(regressed_payload)

    assert style_comparison_improvement_decision(low_confidence) == (
        False,
        "vqa.indeterminate",
        False,
    )
    assert style_comparison_improvement_decision(regressed) == (
        False,
        "vqa.visual_regression",
        True,
    )


def test_style_comparison_batch_uses_dedicated_call_shape_and_cache(monkeypatch):
    calls = []
    existing_render_cache = dict(author_vision_qa_mod._vqa_cache)

    async def fake_compare(baseline, candidate, slide_context=None):
        calls.append((baseline, candidate, slide_context))
        return SlideCritique(needs_fix=False)

    _reset_comparison_cache()
    monkeypatch.setattr(author_vision_qa_mod, "VQA_CACHE_SIZE", 8)
    monkeypatch.setattr(author_vision_qa_mod, "get_model", lambda: "review-model")
    monkeypatch.setattr(
        author_vision_qa_mod,
        "critique_slide_style_comparison",
        fake_compare,
    )

    async def compare_twice():
        first = await author_vision_qa_mod.critique_authored_style_comparison(
            [b"before-0", b"before-1"],
            [b"after-0", b"after-1"],
            ["context-0", "context-1"],
        )
        second = await author_vision_qa_mod.critique_authored_style_comparison(
            [b"before-0", b"before-1"],
            [b"after-0", b"after-1"],
            ["context-0", "context-1"],
        )
        return first, second

    first, second = _run(compare_twice())

    assert calls == [
        (b"before-0", b"after-0", "context-0"),
        (b"before-1", b"after-1", "context-1"),
    ]
    assert all(result.needs_fix is False for result in first + second)
    assert len(author_vision_qa_mod._style_comparison_vqa_cache) == 2
    assert dict(author_vision_qa_mod._vqa_cache) == existing_render_cache


def test_style_comparison_cache_key_covers_all_inputs_and_is_separate(monkeypatch):
    monkeypatch.setattr(author_vision_qa_mod, "get_model", lambda: "model-a")
    common = author_vision_qa_mod._style_comparison_cache_key(
        b"baseline", b"candidate", "context"
    )

    assert common != author_vision_qa_mod._style_comparison_cache_key(
        b"changed-baseline", b"candidate", "context"
    )
    assert common != author_vision_qa_mod._style_comparison_cache_key(
        b"baseline", b"changed-candidate", "context"
    )
    assert common != author_vision_qa_mod._style_comparison_cache_key(
        b"baseline", b"candidate", "changed-context"
    )
    monkeypatch.setattr(author_vision_qa_mod, "get_model", lambda: "model-b")
    assert common != author_vision_qa_mod._style_comparison_cache_key(
        b"baseline", b"candidate", "context"
    )
    monkeypatch.setattr(author_vision_qa_mod, "get_model", lambda: "model-a")
    monkeypatch.setattr(
        author_vision_qa_mod,
        "STYLE_COMPARISON_VQA_PROMPT_VERSION",
        "changed-prompt-version",
    )
    assert common != author_vision_qa_mod._style_comparison_cache_key(
        b"baseline", b"candidate", "context"
    )
    assert common != author_vision_qa_mod._critique_cache_key(
        b"candidate",
        "context",
        source_image=b"baseline",
        reference_mode=STYLE_REFERENCE,
    )


def test_style_comparison_model_failure_is_best_effort_and_not_cached(monkeypatch):
    calls = 0

    async def failing_compare(baseline, candidate, slide_context=None):
        nonlocal calls
        calls += 1
        raise RuntimeError("model unavailable")

    _reset_comparison_cache()
    monkeypatch.setattr(author_vision_qa_mod, "VQA_CACHE_SIZE", 8)
    monkeypatch.setattr(author_vision_qa_mod, "get_model", lambda: "review-model")
    monkeypatch.setattr(
        author_vision_qa_mod,
        "critique_slide_style_comparison",
        failing_compare,
    )

    first = _run(
        author_vision_qa_mod.critique_authored_style_comparison(
            [b"baseline"], [b"candidate"]
        )
    )
    second = _run(
        author_vision_qa_mod.critique_authored_style_comparison(
            [b"baseline"], [b"candidate"]
        )
    )

    assert first == [None]
    assert second == [None]
    assert calls == 2
    assert author_vision_qa_mod._style_comparison_vqa_cache == {}
    assert author_vision_qa_mod.style_comparison_failure_diagnostic(
        b"baseline", b"candidate", None
    ) == {
        "error_type": "RuntimeError",
        "reason_code": "vqa.provider_failed",
        "status_code": None,
    }


def test_style_comparison_failure_diagnostic_survives_disabled_cache(monkeypatch):
    class ProviderFailure(RuntimeError):
        code = "vqa.provider_http_error"
        status_code = 400

    async def failing_compare(*_args, **_kwargs):
        raise ProviderFailure("credential rejected")

    _reset_comparison_cache()
    monkeypatch.setattr(author_vision_qa_mod, "VQA_CACHE_SIZE", 0)
    monkeypatch.setattr(author_vision_qa_mod, "get_model", lambda: "review-model")
    monkeypatch.setattr(
        author_vision_qa_mod,
        "critique_slide_style_comparison",
        failing_compare,
    )

    assert _run(
        author_vision_qa_mod.critique_authored_style_comparison(
            [b"baseline"], [b"candidate"], ["target-only-context"]
        )
    ) == [None]
    assert author_vision_qa_mod._style_comparison_vqa_cache == {}
    assert author_vision_qa_mod.style_comparison_failure_diagnostic(
        b"baseline", b"candidate", "target-only-context"
    ) == {
        "error_type": "ProviderFailure",
        "reason_code": "vqa.provider_http_error",
        "status_code": 400,
    }


def test_style_comparison_validates_mode_and_images_but_allows_empty_batch(monkeypatch):
    async def unexpected_call(*args, **kwargs):
        raise AssertionError("empty batch must not invoke the model")

    _reset_comparison_cache()
    monkeypatch.setattr(
        author_vision_qa_mod,
        "critique_slide_style_comparison",
        unexpected_call,
    )

    assert _run(author_vision_qa_mod.critique_authored_style_comparison([], [])) == []
    with pytest.raises(ValueError, match="only supports"):
        _run(
            author_vision_qa_mod.critique_authored_style_comparison(
                [b"baseline"],
                [b"candidate"],
                reference_mode=EXACT_RECONSTRUCTION,
            )
        )
    with pytest.raises(ValueError, match="counts must match"):
        _run(
            author_vision_qa_mod.critique_authored_style_comparison(
                [b"baseline"], []
            )
        )
    with pytest.raises(ValueError, match="non-empty bytes"):
        _run(
            author_vision_qa_mod.critique_authored_style_comparison(
                [b"baseline"], [b""]
            )
        )
