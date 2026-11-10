"""Focused contracts for authored STYLE_REFERENCE baseline comparisons."""

import asyncio


from utils.llm_calls import critique_slide as critique_slide_mod
from utils.llm_calls.critique_slide import (
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
        "enforce_llm_egress_policy",
        lambda: None,
    )
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
