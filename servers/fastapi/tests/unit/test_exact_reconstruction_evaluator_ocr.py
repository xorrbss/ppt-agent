from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from utils import slide_capture

SCRIPT = (
    Path(__file__).resolve().parents[2] / "scripts" / "evaluate_exact_reconstruction.py"
)
SPEC = importlib.util.spec_from_file_location("exact_reconstruction_evaluator", SCRIPT)
assert SPEC and SPEC.loader
evaluator = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = evaluator
SPEC.loader.exec_module(evaluator)


def _capture(text: str) -> object:
    return evaluator.TextCapture("success", "fixture", text, 0.0)


def test_raw_and_normalized_cer_preserve_character_classes_and_line_breaks() -> None:
    source = _capture("한글 ABC 123 +#\n둘째 줄")
    render = _capture("한글 ＡＢＣ 123  +# \n둘째 줄")

    metrics = evaluator._text_metrics(source, render, "fixture")

    assert metrics["raw"]["cer"] > 0
    assert metrics["normalized"]["cer"] == 0
    # Newline alignment is sequence-based: earlier width/full-width edits must
    # not turn an otherwise preserved line boundary into a false OCR alert.
    assert metrics["raw"]["line_break_exact"] is True
    assert metrics["normalized"]["line_break_exact"] is True
    classes = metrics["raw"]["character_classes"]
    assert classes["korean"]["source_count"] == 5
    assert classes["english"]["source_count"] == 3
    assert classes["digits"]["source_count"] == 3
    assert classes["symbols"]["source_count"] == 2


def test_missing_rate_is_based_on_deletions_not_substitutions() -> None:
    metrics = evaluator._text_metrics(
        _capture("한글ABC123+#"),
        _capture("한글AB12+"),
        "fixture",
    )

    raw = metrics["raw"]
    assert raw["deletions"] == 3
    assert raw["substitutions"] == 0
    assert raw["missing_rate"] == pytest.approx(3 / 10)
    assert raw["character_classes"]["english"]["preservation_rate"] == pytest.approx(
        2 / 3
    )
    assert raw["character_classes"]["symbols"]["preservation_rate"] == pytest.approx(
        1 / 2
    )


def test_runtime_distinguishes_unavailable_and_missing_language(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unavailable = evaluator._inspect_tesseract(
        explicit_command=tmp_path / "missing-tesseract",
        tessdata_dir=None,
        language="kor+eng",
        timeout_seconds=1,
    )
    assert unavailable.status == "unavailable"
    assert unavailable.failure_reason == "tesseract_executable_not_found"

    monkeypatch.setattr(
        evaluator, "_find_tesseract", lambda _explicit: ("fake-tesseract",)
    )
    results = iter(
        [
            evaluator.ProcessResult("success", 0, b"tesseract 5.4.0\n", b"", 0.01),
            evaluator.ProcessResult(
                "success", 0, b"List of languages (1):\neng\n", b"", 0.01
            ),
        ]
    )
    monkeypatch.setattr(
        evaluator, "_run_process", lambda *_args, **_kwargs: next(results)
    )
    missing = evaluator._inspect_tesseract(
        explicit_command=None,
        tessdata_dir=tmp_path,
        language="kor+eng",
        timeout_seconds=1,
    )
    assert missing.status == "language_missing"
    assert missing.failure_reason == "missing_languages:kor"
    assert missing.available_languages == ("eng",)


@pytest.mark.parametrize(
    ("process_status", "capture_status", "reason"),
    [
        ("timeout", "timeout", "timeout_after_0.1_seconds"),
        ("failed", "failed", "exit_code_2"),
    ],
)
def test_ocr_distinguishes_timeout_and_process_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_status: str,
    capture_status: str,
    reason: str,
) -> None:
    runtime = evaluator.OcrRuntime(
        "ready", ("fake-tesseract",), "5.4", ("kor", "eng"), ("kor", "eng"), None, 0.0
    )
    monkeypatch.setattr(
        evaluator,
        "_run_process",
        lambda *_args, **_kwargs: evaluator.ProcessResult(
            process_status,
            -15 if process_status == "timeout" else 2,
            b"",
            b"not reported",
            0.1,
            reason,
        ),
    )

    capture = evaluator._ocr_image(
        tmp_path / "slide.png",
        runtime=runtime,
        language="kor+eng",
        psm=6,
        timeout_seconds=0.1,
    )

    assert capture.status == capture_status
    assert capture.failure_reason == reason
    assert capture.raw_text is None


def test_text_sources_never_silently_fallback(tmp_path: Path) -> None:
    slide = {
        "source_image": str(tmp_path / "source.png"),
        "render_image": str(tmp_path / "render.png"),
        "source_text": "manifest source",
        "render_text": "manifest render",
        "render_html": "<p>html proxy</p>",
    }
    unavailable = evaluator.OcrRuntime(
        "unavailable", (), None, ("kor", "eng"), (), None, 0.0, "not_found"
    )
    ocr = evaluator._capture_slide_text(
        slide,
        side="render",
        text_source="tesseract",
        runtime=unavailable,
        ocr_language="kor+eng",
        ocr_psm=6,
        ocr_timeout_seconds=1,
    )
    manifest = evaluator._capture_slide_text(
        slide,
        side="render",
        text_source="manifest",
        runtime=unavailable,
        ocr_language="kor+eng",
        ocr_psm=6,
        ocr_timeout_seconds=1,
    )
    proxy = evaluator._capture_slide_text(
        slide,
        side="render",
        text_source="html_proxy",
        runtime=unavailable,
        ocr_language="kor+eng",
        ocr_psm=6,
        ocr_timeout_seconds=1,
    )

    assert (ocr.status, ocr.raw_text) == ("unavailable", None)
    assert (manifest.status, manifest.raw_text) == ("success", "manifest render")
    assert (proxy.status, proxy.method, proxy.raw_text) == (
        "success",
        "html_visible_text_proxy",
        "html proxy",
    )


def test_timed_out_child_is_reaped(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    child = (
        "import os,pathlib,time; "
        f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid())); "
        "time.sleep(30)"
    )

    # The child must start CPython and write the PID rendezvous file before the
    # timeout fires, or there is nothing to assert reaping against. Five seconds
    # keeps this deterministic on a contended Windows host while remaining well
    # below the child's own 30-second lifetime.
    result = evaluator._run_process((sys.executable, "-c", child), timeout_seconds=5)

    assert result.status == "timeout"
    assert result.returncode is not None
    assert 5 <= result.elapsed_seconds < 30
    pid = pid_file.read_text(encoding="utf-8")
    if sys.platform == "win32":
        listing = subprocess.run(
            ("tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"),
            check=True,
            capture_output=True,
            text=True,
            errors="replace",
        ).stdout
        assert f'"{pid}"' not in listing
    else:
        with pytest.raises(ProcessLookupError):
            os.kill(int(pid), 0)


def test_lock_is_exclusive_and_removed_after_failure(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    with (
        pytest.raises(RuntimeError, match="already locked"),
        evaluator._exclusive_output(report),
        evaluator._exclusive_output(report),
    ):
        pass
    assert not report.with_suffix(".json.lock").exists()

    with pytest.raises(ValueError), evaluator._exclusive_output(report):
        raise ValueError("fixture")
    assert not report.with_suffix(".json.lock").exists()


def test_live_cost_gate_fails_before_pipeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(evaluator.LIVE_GATE, raising=False)
    args = evaluator._parser().parse_args(
        ["--live", "--output", str(tmp_path / "must-not-run.json")]
    )
    called = False

    async def forbidden(*_args: object, **_kwargs: object) -> object:
        nonlocal called
        called = True
        raise AssertionError("model path must not be called")

    monkeypatch.setattr(evaluator, "_run_live", forbidden)
    with pytest.raises(RuntimeError, match="acknowledge-model-cost"):
        asyncio.run(evaluator._main(args))
    assert called is False
    assert not (tmp_path / "must-not-run.json").exists()


def test_local_render_benchmark_coalesces_ninety_logical_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The evaluator's 90-slide load check is local-only and starts one render."""
    calls = 0

    async def fake_uncached(html: str, timeout: int = 60) -> bytes:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return f"png:{html}:{timeout}".encode()

    slide_capture._render_cache.clear()
    slide_capture._render_inflight.clear()
    monkeypatch.setattr(slide_capture, "find_chrome", lambda: "fake-chrome")
    monkeypatch.setattr(slide_capture, "_render_html_to_png_uncached", fake_uncached)

    result = asyncio.run(evaluator._run_render_benchmark(90))

    assert calls == 1
    assert result["logical_requests"] == 90
    assert result["uncached_chrome_renders"] == 1
    assert result["unique_pngs"] == 1
    assert result["model_calls"] == result["ocr_calls"] == 0


def test_unavailable_ocr_writes_explicit_report_and_nonzero_status(
    tmp_path: Path,
) -> None:
    output = tmp_path / "unavailable.json"
    args = evaluator._parser().parse_args(
        [
            "--self-test",
            "--tesseract-command",
            str(tmp_path / "missing-tesseract"),
            "--output",
            str(output),
        ]
    )

    status = asyncio.run(evaluator._main(args))
    report = json.loads(output.read_text(encoding="utf-8"))

    assert status == 3
    assert report["ocr"]["status"] == "unavailable"
    assert report["ocr"]["failure_reason"] == "tesseract_executable_not_found"
    assert all(item["text"]["available"] is False for item in report["slides"])
    assert all("unavailable" in item["text"]["reason"] for item in report["slides"])
