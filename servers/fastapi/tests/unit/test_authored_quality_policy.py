"""Focused regression tests for exact-reconstruction review policy."""

import json
import logging

import pytest

from services import authored_quality_review_service as quality_service
from utils.llm_calls.critique_slide import (
    EXACT_RECONSTRUCTION,
    STYLE_REFERENCE,
    CritiqueIssue,
    ImageContentPart,
    SlideCritique,
    _build_critique_messages,
)


def _critique(severity: str, detail: str = "visible mismatch") -> SlideCritique:
    return SlideCritique(
        needs_fix=True,
        issues=[CritiqueIssue(type="other", severity=severity, detail=detail)],
    )


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [
        (_critique("high"), _critique("medium"), (True, "improved")),
        (_critique("medium"), _critique("medium"), (False, "unchanged")),
        (_critique("low"), _critique("high"), (False, "worsened")),
        (_critique("high"), None, (False, "final_unavailable")),
        (
            _critique("high"),
            _critique("high", "INDETERMINATE: source logo is unreadable"),
            (False, "final_indeterminate"),
        ),
        (
            _critique("high", "INDETERMINATE: exact font cannot be compared"),
            _critique("low"),
            (False, "initial_indeterminate"),
        ),
    ],
)
def test_exact_acceptance_is_strict_and_conservative(before, after, expected):
    assert quality_service._acceptance_decision(before, after) == expected


def test_exact_acceptance_rejects_inconsistent_structured_result():
    inconsistent = SlideCritique(
        needs_fix=False,
        issues=[CritiqueIssue(type="other", severity="low", detail="contradiction")],
    )

    assert quality_service._acceptance_decision(
        _critique("high"), inconsistent
    ) == (False, "final_inconsistent")
    assert quality_service._critique_score(inconsistent) is None


def test_exact_prompt_avoids_overlap_crop_photo_logo_and_font_false_positives():
    source = b"\x89PNG\r\n\x1a\nsource"
    render = b"render"

    messages = _build_critique_messages(
        render,
        source_image=source,
        reference_mode=EXACT_RECONSTRUCTION,
    )
    system_prompt = messages[0].content
    parts = messages[1].content
    image_parts = [part for part in parts if isinstance(part, ImageContentPart)]

    assert "already exists in the source is intentional and is not a defect" in system_prompt
    assert "photos, logos, and complex raster artwork" in system_prompt
    assert "harmless compression, color-profile, or subpixel antialiasing" in system_prompt
    assert "You do not need to identify the font by name" in system_prompt
    assert "INDETERMINATE:" in system_prompt
    assert [part.data for part in image_parts] == [source, render]


def test_style_reference_prompt_and_single_render_contract_are_unchanged():
    render = b"render-only"

    messages = _build_critique_messages(
        render,
        slide_context="expected context",
        reference_mode=STYLE_REFERENCE,
    )
    parts = messages[1].content
    image_parts = [part for part in parts if isinstance(part, ImageContentPart)]

    assert messages[0].content == (
        "You are a meticulous presentation design reviewer. Inspect the rendered slide "
        "IMAGE and report only REAL, visible problems: text overflow or clipping, element "
        "overlap, low contrast, leftover placeholder text, misalignment, or large/unbalanced "
        "empty space. Do not invent issues. If the slide looks clean and readable, return "
        "needs_fix=false with an empty issues list. Be specific and concise; ignore the small "
        "navigation badge that may appear in a corner of the capture."
    )
    assert parts[0].text == (
        "Review this slide image. Intended content: expected context"
    )
    assert [(part.data, part.mime_type) for part in image_parts] == [
        (render, "image/png")
    ]


def test_quality_observation_log_contains_only_policy_metadata(caplog):
    caplog.set_level(logging.INFO, logger=quality_service.__name__)

    quality_service._log_review_event(
        reference_mode=EXACT_RECONSTRUCTION,
        stage="acceptance",
        slide_count=4,
        attempted=3,
        accepted=1,
        rejection_reasons={"unchanged": 1, "worsened": 1},
        model="gpt-vision-test",
        prompt_version="review-v7",
        cache_version="cache-v3",
        critique_batch_count=1,
        critique_slide_request_count=3,
        cache_hits=1,
        cache_misses=3,
        render_ms=12.3456,
        critique_ms=67.8912,
        revision_ms=4.5678,
        indeterminate_count=2,
    )

    message = caplog.records[-1].getMessage()
    assert message.startswith("authored_quality_review ")
    event = json.loads(message.partition(" ")[2])
    assert event == {
        "schema_version": 2,
        "pipeline": "authored_quality_review",
        "reference_mode": EXACT_RECONSTRUCTION,
        "stage": "acceptance",
        "slides": 4,
        "attempted": 3,
        "accepted": 1,
        "rejected": 2,
        "rejection_reasons": {"unchanged": 1, "worsened": 1},
        "model": "gpt-vision-test",
        "prompt_version": "review-v7",
        "cache_version": "cache-v3",
        "critique_batch_count": 1,
        "critique_slide_request_count": 3,
        "vqa_calls": 3,
        "vqa_cost_usd": None,
        "vqa_cost_status": "provider_usage_unavailable",
        "cache_hits": 1,
        "cache_misses": 3,
        "render_ms": 12.346,
        "local_inspection_ms": 0.0,
        "montage_ms": 0.0,
        "persist_ms": 0.0,
        "total_ms": 0.0,
        "ocr_ms": 0.0,
        "ocr_executed": False,
        "ocr_requests": 0,
        "ocr_calls": 0,
        "ocr_cache_hits": 0,
        "ocr_total_ms": 0.0,
        "critique_ms": 67.891,
        "revision_ms": 4.568,
        "patch_attempted": 0,
        "patch_accepted": 0,
        "patch_rejected": 0,
        "fallback_attempted": 0,
        "fallback_succeeded": 0,
        "fallback_failed": 0,
        "local_high_remaining": 0,
        "indeterminate_count": 2,
    }
    assert "raw html" not in message.lower()
    assert "raw prompt" not in message.lower()
    assert "raw image" not in message.lower()
    assert "sha256" not in event
    assert "digest" not in message.lower()


def test_cache_observation_counts_completed_cache_hits_without_vqa_call(monkeypatch):
    monkeypatch.setattr(quality_service.author_vision_qa, "VQA_CACHE_SIZE", 64)
    monkeypatch.setattr(
        quality_service.author_vision_qa,
        "_critique_cache_key",
        lambda image, context, **kwargs: f"{image!r}:{context}:{kwargs['reference_mode']}",
    )
    monkeypatch.setattr(
        quality_service.author_vision_qa,
        "_vqa_cache",
        {"b'cached':context:exact_reconstruction": object()},
    )

    hits, misses = quality_service._observe_critique_cache(
        [b"cached", b"not-cached"],
        ["context", "context"],
        source_images=[b"source-a", b"source-b"],
        reference_mode=EXACT_RECONSTRUCTION,
    )

    assert (hits, misses) == (1, 1)


def test_observation_model_token_cannot_leak_untrusted_configuration(monkeypatch):
    monkeypatch.setattr(
        quality_service.author_vision_qa,
        "get_model",
        lambda: "configured-model\nwith-untrusted-content",
    )

    assert quality_service._safe_review_model() == "unknown"


def test_observation_collapses_unknown_rejection_reason_without_content_leak(caplog):
    caplog.set_level(logging.INFO, logger=quality_service.__name__)

    quality_service._log_review_event(
        reference_mode=EXACT_RECONSTRUCTION,
        stage="acceptance",
        slide_count=1,
        rejection_reasons={"raw html from a user slide": 1},
    )

    message = caplog.records[-1].getMessage()
    event = json.loads(message.partition(" ")[2])
    assert event["rejection_reasons"] == {"other": 1}
    assert "user slide" not in message


def test_observation_rejects_paths_and_free_form_configuration(caplog):
    caplog.set_level(logging.INFO, logger=quality_service.__name__)
    secret_path = r"C:\private\api-key.txt"

    quality_service._log_review_event(
        reference_mode=secret_path,
        stage="operator token sk-secret",
        slide_count=-1,
        model="sk-secret-model-token",
        prompt_version="prompt\nsecret",
        cache_version="/private/cache",
        critique_slide_request_count=1,
    )

    message = caplog.records[-1].getMessage()
    event = json.loads(message.partition(" ")[2])
    assert event["reference_mode"] == "unknown"
    assert event["stage"] == "unknown"
    assert event["model"] == "unknown"
    assert event["prompt_version"] == "unknown"
    assert event["cache_version"] == "unknown"
    assert event["slides"] == 0
    assert secret_path not in message
    assert "sk-secret-model-token" not in message
    assert "sk-secret" not in message


def test_observation_rejects_nonfinite_numbers():
    event = quality_service.build_review_event(
        reference_mode=EXACT_RECONSTRUCTION,
        stage="completed",
        slide_count=1,
        critique_slide_request_count=1,
        render_ms=float("inf"),
        total_ms=float("nan"),
        vqa_cost_usd=float("inf"),
    )

    assert event["render_ms"] == 0.0
    assert event["total_ms"] == 0.0
    assert event["vqa_cost_usd"] is None
    assert event["vqa_cost_status"] == "provider_usage_unavailable"
