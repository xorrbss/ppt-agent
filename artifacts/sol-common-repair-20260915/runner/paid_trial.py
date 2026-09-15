from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import secrets
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]
SOURCE_RUNNER = REPO / "artifacts/sol-speed-quality-20260915/runner"
sys.path.insert(0, str(SOURCE_RUNNER))
import experiment as E  # noqa: E402


def _module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


MEASURE = _module(ROOT / "runner/offline_compare.py", "paid_trial_measurement")
PRODUCT_FIT = _module(ROOT / "runner/product_fit_replay.py", "paid_trial_product_fit")
PLAN = E.load(ROOT / "paid-trial-plan.json")
RUN_ROOT = ROOT / "paid-trial-runs"


def _execution_hashes() -> dict[str, str]:
    paths = {
        "samples": REPO / PLAN["sampleSource"],
        "baselinePrompt": REPO / PLAN["promptArms"][0]["basePrompt"],
        "supplement": REPO / PLAN["promptArms"][1]["supplement"],
        "driver": Path(__file__).resolve(),
        "productFitHelper": ROOT / "runner/product_fit_replay.py",
    }
    return {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in paths.items()}


class PaidLedger:
    def __init__(self) -> None:
        self.inner = E.BASE._Ledger(ROOT / "paid-trial-call-ledger.json", PLAN["authorCallCap"])

    def reserve(self, metadata: Mapping[str, Any]) -> int:
        return self.inner.reserve({"stage": "paid-trial", "attempt": True, **dict(metadata)})

    def finish(self, ordinal: int, status: str, detail: str | None = None) -> None:
        self.inner.finish(ordinal, status, detail)


def _execution_gate() -> None:
    path = ROOT / "paid-trial-execution-gate.json"
    if not path.is_file():
        raise RuntimeError("paid trial execution gate is absent")
    gate = E.load(path)
    expected = {
        "status": "authorized",
        "representative10CommonCorrectionPassed": True,
        "rootExplicitExecutionInstruction": True,
        "authorizedAuthorCalls": 12,
    }
    if any(gate.get(key) != value for key, value in expected.items()):
        raise RuntimeError("paid trial execution gate is not satisfied")
    ledger_path = ROOT / "paid-trial-call-ledger.json"
    calls = E.load(ledger_path).get("calls", []) if ledger_path.is_file() else []
    if len(calls) + PLAN["authorCallsScheduled"] > PLAN["authorCallCap"]:
        raise RuntimeError("call ledger cannot reserve all 12 attempts")


def _profiles() -> list[tuple[str, Any]]:
    baseline = REPO / PLAN["promptArms"][0]["basePrompt"]
    supplement = REPO / PLAN["promptArms"][1]["supplement"]
    combined = RUN_ROOT / "inputs/effective-dense-layout-v1.txt"
    combined.parent.mkdir(parents=True, exist_ok=True)
    combined.write_text(
        baseline.read_text(encoding="utf-8-sig").rstrip()
        + "\n\n"
        + supplement.read_text(encoding="utf-8-sig").strip()
        + "\n",
        encoding="utf-8",
    )
    return [
        ("baseline", E.BASE._profile("candidate-c", baseline, "current")),
        ("dense-layout-supplement-v1", E.BASE._profile("candidate-c", combined, "current")),
    ]


async def _fit_capture(html: str, sample_id: str, inputs: Mapping[str, Any], output: Path):
    final_html, capture, fit_timing = await PRODUCT_FIT.capture_product_fit(
        html,
        slide_index=inputs["request"].index,
        deck_scope=f"paid-trial-{sample_id}",
        width_px=inputs["width"],
        height_px=inputs["height"],
    )
    output.mkdir(parents=True, exist_ok=True)
    (output / "render.png").write_bytes(capture.png)
    E.write_json(output / "product-fit-timing.json", fit_timing)
    return {"capture": capture, "fitted_html": final_html}


def _neutral_packet(source: Any, required_images: Any, html_path: Path, png_path: Path, private: Mapping[str, Any]) -> None:
    item_id = f"item-{secrets.token_hex(8)}"
    packet_dir = ROOT / "neutral-packets" / item_id
    packet_dir.mkdir(parents=True, exist_ok=True)
    neutral_html = packet_dir / "slide.html"
    neutral_png = packet_dir / "render.png"
    shutil.copy2(html_path, neutral_html)
    shutil.copy2(png_path, neutral_png)
    packet = {
        "sourceContent": source,
        "requiredImages": required_images,
        "htmlPath": "slide.html",
        "pngPath": "render.png",
        "htmlSha256": E.sha_bytes(neutral_html.read_bytes()),
        "pngSha256": E.sha_bytes(neutral_png.read_bytes()),
    }
    E.write_json(packet_dir / "packet.json", packet)
    mapping_path = ROOT / "private/neutral-packet-map.json"
    mapping = E.load(mapping_path) if mapping_path.is_file() else {"items": {}}
    mapping.setdefault("items", {})[item_id] = dict(private)
    E.write_json(mapping_path, mapping)


async def _run_one(document: Mapping[str, Any], sample: Mapping[str, Any], arm: str, profile: Any, ledger: PaidLedger, ordinal: int, authored_stub: Any = None) -> None:
    sample_id = sample["_sample_id"]
    case_dir = RUN_ROOT / arm / sample_id
    trial_start = time.perf_counter_ns()
    failed_phase = "author-and-off-capture"
    try:
        authored = await authored_stub(document, sample, profile, case_dir / "off") if authored_stub else await E.author_one(document, sample, profile, ledger, strategy=arm, case_dir=case_dir / "off")
        author_off_end = time.perf_counter_ns()
        failed_phase = "on-fit-capture-inspection"
        inputs = authored["inputs"]

        async def capture(html: str):
            return await _fit_capture(html, sample_id, inputs, case_dir / "on")

        def inspect(html: str, capture_result: Any):
            return E.inspect_final(html, capture_result["capture"].browser_metrics, E.capacity_for(sample_id))[0]

        final_html, captured, inspection, on_receipt = await MEASURE.run_measured_pipeline(
            authored["html"], case_dir / "on", repair=lambda html: html, capture=capture, inspection=inspect
        )
        captured_result = captured["capture"]
        final_digest = E.sha_bytes(final_html.encode("utf-8"))
        if not isinstance(captured_result.html_digest, str) or captured_result.html_digest != final_digest:
            raise RuntimeError("captured fitted HTML and PNG binding diverged")
        E.write_json(case_dir / "on/render-evidence.json", {
            "htmlDigest": final_digest,
            "pngDigest": E.sha_bytes(captured_result.png),
            "fit": True,
            "fresh": True,
            "browser_metrics": E.BASE._plain(captured_result.browser_metrics),
            "inspection": inspection,
        })
        trial_end = time.perf_counter_ns()
        E.write_json(case_dir / "trial-timing.json", {
            "status": "completed",
            "clock": "time.perf_counter_ns",
            "trialStartNs": trial_start,
            "trialEndNs": trial_end,
            "actualTotalMs": E.ms(trial_start, trial_end),
            "sharedAuthorThroughOffInspectionMs": E.ms(trial_start, author_off_end),
            "offCaptureMs": authored["timeline"]["renderMs"],
            "offInspectionMs": authored["timeline"]["inspectionMs"],
            "onAdditionalMs": on_receipt["timing"]["elapsedMs"],
            "branchTimingRule": "Shared author time and each branch additional time remain separate; branch sums are not completion latency.",
        })
        source = inputs["request"].content
        request = inputs["request"]
        if not hasattr(request, "source_images") or not hasattr(request, "require_all_source_images") or not hasattr(request, "source_image_placement_plan"):
            raise RuntimeError("official source image fields are unavailable")
        required_images = E.BASE._plain({
            "candidates": request.source_images,
            "requireAll": request.require_all_source_images,
            "placementPlan": request.source_image_placement_plan,
        })
        _neutral_packet(source, required_images, case_dir / "off/slide.html", case_dir / "off/render.png", {"arm": arm, "sampleId": sample_id, "branch": "off"})
        _neutral_packet(source, required_images, case_dir / "on/final.html", case_dir / "on/render.png", {"arm": arm, "sampleId": sample_id, "branch": "on"})
    except BaseException as error:
        trial_end = time.perf_counter_ns()
        E.write_json(case_dir / "trial-failure.json", {
            "status": "failed",
            "failedPhase": failed_phase,
            "errorType": type(error).__name__,
            "trialStartNs": trial_start,
            "trialEndNs": trial_end,
            "elapsedMs": E.ms(trial_start, trial_end),
        })
        raise


async def run() -> int:
    _execution_gate()
    execution_hashes = _execution_hashes()
    E.write_json(RUN_ROOT / "execution-hashes-before.json", execution_hashes)
    document = E.load(REPO / PLAN["sampleSource"])
    samples = E.BASE._samples(document)
    if len(samples) != 6:
        raise RuntimeError("frozen six-sample input changed")
    ledger = PaidLedger()
    profiles = _profiles()
    ordinal = 0
    for sample_number, sample in enumerate(samples, 1):
        ordered_profiles = profiles if sample_number % 2 else list(reversed(profiles))
        for arm, profile in ordered_profiles:
            ordinal += 1
            await _run_one(document, sample, arm, profile, ledger, ordinal)
    after_hashes = _execution_hashes()
    E.write_json(RUN_ROOT / "execution-hashes-after.json", after_hashes)
    if after_hashes != execution_hashes:
        raise RuntimeError("paid trial inputs or driver changed during execution")
    return 0


async def run_provider_zero_dry_stub() -> int:
    from utils.authored_qid_namespace import ensure_authored_qids

    document = E.load(REPO / PLAN["sampleSource"])
    sample = E.BASE._samples(document)[0]
    profile = _profiles()[0][1]
    source_html = (REPO / "artifacts/sol-speed-quality-20260915/runs/stage1/case-01/A/slide.html").read_text(encoding="utf-8")

    async def authored_stub(document_value: Any, sample_value: Any, profile_value: Any, case_dir: Path):
        inputs = E.BASE._build_inputs(document_value, sample_value, profile_value)
        html = ensure_authored_qids(source_html)
        case_dir.mkdir(parents=True, exist_ok=True)
        E.write_text(case_dir / "slide.html", html)
        render_started = time.perf_counter_ns()
        render = await E.BASE._render(html, case_dir, sample_value["_sample_id"], inputs["width"], inputs["height"])
        render_ended = time.perf_counter_ns()
        inspection_started = time.perf_counter_ns()
        inspection = E.inspect_final(html, render["browser_metrics"], E.capacity_for(sample_value["_sample_id"]))[0]
        inspection_ended = time.perf_counter_ns()
        return {
            "inputs": inputs,
            "html": html,
            "render": render,
            "inspection": inspection,
            "timeline": {
                "renderMs": E.ms(render_started, render_ended),
                "inspectionMs": E.ms(inspection_started, inspection_ended),
            },
        }

    await _run_one(document, sample, "provider-zero-dryrun", profile, PaidLedger(), 0, authored_stub=authored_stub)
    E.write_json(RUN_ROOT / "provider-zero-dryrun/provider-zero-receipt.json", {"providerCalls": 0, "status": "completed"})
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Gated gpt-5.6-sol medium paid trial")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--provider-zero-dryrun", action="store_true")
    args = parser.parse_args()
    if args.provider_zero_dryrun:
        from utils.runtime_settings import runtime_settings_context
        with runtime_settings_context(E.BASE._managed_runtime_settings()):
            return asyncio.run(run_provider_zero_dry_stub())
    if not args.execute:
        raise RuntimeError("--execute and an authorized root gate are required")
    from utils.runtime_settings import runtime_settings_context

    with runtime_settings_context(E.BASE._managed_runtime_settings()):
        return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())
