from __future__ import annotations

import asyncio
import sys
import threading
import time
from pathlib import Path

from utils import authored_ocr_quality as ocr


def test_comparison_aligns_linebreaks_after_inserted_text() -> None:
    metrics = ocr.comparison_metrics("제목\nABC 123", "긴 제목\nABC 123")
    assert metrics["line_break_exact"] is True
    assert metrics["line_break_f1"] == 1.0
    assert metrics["character_classes"]["digits"]["missing"] == 0


def test_expected_candidate_requires_expected_relative_improvement() -> None:
    preserved = ocr.compare_expected_candidate("ABC 123", "ABC 12", "ABC 12")
    improved = ocr.compare_expected_candidate("ABC 123", "ABC 12", "ABC 123")
    assert preserved.accepted and not preserved.strict_improvement
    assert preserved.reason == "content_preserved"
    assert improved.strict_improvement and improved.resolved["numbers"] == 1
    assert set(improved.remaining) >= {"text", "numbers", "symbols"}


def test_two_way_identical_text_is_preserved_not_strict_improvement() -> None:
    result = ocr.compare_authored_ocr("same OCR text", "same OCR text")
    assert result.accepted and result.no_regression
    assert result.reason == "content_preserved"
    assert result.strict_improvement is False


def test_two_way_ocr_modes_keep_identical_threshold_semantics() -> None:
    for mode in ("baseline", "expected"):
        accepted = ocr.compare_authored_ocr(
            "abcd", "abce", mode=mode, confidence_tolerance=0.0, metric_tolerance=0.25
        )
        rejected = ocr.compare_authored_ocr(
            "abcd", "abce", mode=mode, confidence_tolerance=0.0, metric_tolerance=0.24
        )
        assert accepted.accepted and accepted.reason == "within_tolerance"
        assert not rejected.accepted and rejected.reason == "text_regression"


def test_expected_browser_text_uses_visible_leaf_qids() -> None:
    metrics = {
        "elements": [
            {"qid": "parent", "rendered_text": "parent child", "child_qids": ["child"]},
            {"qid": "child", "rendered_text": "child"},
            {"qid": "hidden", "rendered_text": "secret", "aria_hidden": "true"},
        ]
    }
    assert ocr.expected_text_from_browser_metrics(metrics) == "child"


def test_expected_browser_text_uses_probe_parent_qid_shape() -> None:
    metrics = {
        "elements": [
            {"qid": "parent", "rendered_text": "parent child"},
            {"qid": "child", "parent_qid": "parent", "rendered_text": "child"},
        ]
    }
    assert ocr.expected_text_from_browser_metrics(metrics) == "child"


def test_start_failure_is_structured() -> None:
    result = ocr.run_process(("definitely-not-a-command-xyz",), timeout_seconds=1)
    assert result.status == "start_failed"


def test_timeout_reaps_child() -> None:
    result = ocr.run_process(
        (sys.executable, "-c", "import time; time.sleep(30)"), timeout_seconds=0.1
    )
    assert result.status == "timeout"
    assert result.returncode is not None


def test_async_unavailable_does_not_cache_failure() -> None:
    cache = ocr.AuthoredOcrCache()
    first = asyncio.run(
        ocr.inspect_authored_ocr(
            b"not an image",
            command=Path("missing-tesseract"),
            cache=cache,
            timeout_seconds=0.1,
        )
    )
    second = asyncio.run(
        ocr.inspect_authored_ocr(
            b"not an image",
            command=Path("missing-tesseract"),
            cache=cache,
            timeout_seconds=0.1,
        )
    )
    assert not first.available and not second.available
    assert cache.cache_hits == 0 and cache.calls == 0 and cache.requests == 2


def test_failed_runtime_is_reinspected_then_ready_runtime_is_cached(
    monkeypatch,
) -> None:
    cache = ocr.AuthoredOcrCache()
    missing = ocr.OcrRuntime(
        "language_missing",
        ("fake",),
        None,
        ("eng",),
        (),
        None,
        0,
        "missing_languages:eng",
    )
    ready = ocr.OcrRuntime("ready", ("fake",), None, ("eng",), ("eng",), None, 0)
    runtimes = iter((missing, ready))
    inspections = 0

    def inspect(**_kwargs):
        nonlocal inspections
        inspections += 1
        return next(runtimes)

    monkeypatch.setattr(ocr, "inspect_tesseract", inspect)
    monkeypatch.setattr(
        ocr,
        "run_process",
        lambda *_args, **_kwargs: ocr.ProcessResult(
            "failed", 2, b"", b"", 0, "exit_code_2"
        ),
    )
    first = ocr._ocr_bytes(
        b"fixture",
        language="eng",
        cache=cache,
        timeout_seconds=1,
        command=None,
        tessdata_dir=None,
        psm=6,
    )
    second = ocr._ocr_bytes(
        b"fixture",
        language="eng",
        cache=cache,
        timeout_seconds=1,
        command=None,
        tessdata_dir=None,
        psm=6,
    )
    assert first.status == "language_missing" and second.status == "failed"
    assert inspections == 2
    assert len(cache.runtimes) == 1


def test_cancellation_waits_for_worker_cleanup(monkeypatch) -> None:
    finished = threading.Event()

    def slow(*_args, **_kwargs):
        time.sleep(0.05)
        finished.set()
        return ocr.OcrQualityResult(False, "failed", "fixture", None, None, {}, 0, 0, 0)

    monkeypatch.setattr(ocr, "_ocr_bytes", slow)

    async def scenario() -> None:
        task = asyncio.create_task(ocr.inspect_authored_ocr(b"image"))
        await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(scenario())
    assert finished.is_set()
