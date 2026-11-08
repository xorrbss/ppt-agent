"""Scoped actual normal-profile capture and strict ROOT consumer evidence.

This script does not publish, create jobs, call providers, or alter admission.
Execution requires explicit coordinator approval after its source review.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import copy
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from prepare_sources import ROOT, HERE, canonical, digest, write_json

WORKTREE = HERE.parents[3]
FIELDS = {"contract_version", "renderer_version", "source_digest", "png_digest", "wrapper_digest",
          "metrics_digest", "measurement_complete", "geometry_passed", "passed", "verified",
          "semantic_review", "failures", "body_font_floor_px", "browser_metrics", "overlap_basis",
          "trusted_font_materialization", "physical_readability"}


def reference(path: Path) -> dict:
    return {"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def require(condition, reason: str) -> None:
    if not condition:
        raise ValueError(reason)


def utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def verify_pin(pin_dir: Path) -> dict:
    provenance = json.loads((pin_dir / "pin-provenance.json").read_text(encoding="utf-8"))
    for record in provenance["files"].values():
        path = (pin_dir / record["path"]).resolve()
        require(path.is_relative_to(pin_dir) and digest(path.read_bytes()) == record["sha256"],
                "pin_artifact_digest_mismatch")
    return provenance


def parity_sources() -> tuple[dict, dict]:
    names = ("utils/slide_capture.py", "utils/authored_renderer_session.py", "utils/authored_renderer_worker.py",
             "utils/renderer_isolation.py", "utils/slide_capture_worker_client.py", "utils/slide_capture_pipe_render.py",
             "services/svg_renderer.py")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    sources = {name: subprocess.check_output(["git", "show", f"{head}:servers/fastapi/{name}"], cwd=ROOT)
               for name in names}
    test = WORKTREE / "servers/fastapi/tests/unit/test_svg_chart_paint_chromium.py"
    identity = {"checkout": str(ROOT), "dependencyBackend": str(ROOT / "servers/fastapi"),
                "head": head, "fixtureHelper": reference(test),
                "baselineSourceHashes": {name: hashlib.sha256(data).hexdigest() for name, data in sources.items()}}
    return identity, sources


def head_absent_parity(output: Path, identity: dict, sources: dict) -> dict:
    # Adapt the existing fixture with an explicit ROOT baseline and dependency base.
    test = WORKTREE / "servers/fastapi/tests/unit/test_svg_chart_paint_chromium.py"
    spec = importlib.util.spec_from_file_location("bounded_actual_head_parity", test)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    from utils import slide_capture
    from utils.renderer_isolation import admit_static_output_html
    from services.svg_renderer import _portable_metrics
    _, wrapper, _ = module._fixture('<rect id="mark" x="30" y="35" width="100" height="45" fill="#3b82f6"/>')
    admitted = admit_static_output_html(wrapper)
    started = time.perf_counter_ns()
    with tempfile.TemporaryDirectory(prefix="chart-head-absent-") as directory:
        baseline = Path(directory)
        for name, data in sources.items():
            path = baseline / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            if name == "utils/slide_capture_pipe_render.py":
                require((ROOT / "servers/fastapi" / name).read_bytes() == data, "original_pipe_bytes_changed")
        script = """import base64,json,sys
import utils,services
from pathlib import Path
overlay=Path(sys.argv[1]);utils.__path__.insert(0,str(overlay/'utils'));services.__path__.insert(0,str(overlay/'services'))
from utils import slide_capture
from utils.renderer_isolation import AdmittedHtml
from services.svg_renderer import _portable_metrics
payload=json.load(sys.stdin)
record=payload['admitted'];record['asset_digests']=tuple(record['asset_digests'])
png,metrics=slide_capture._render_admitted_html_over_pipe(AdmittedHtml(**record),60,width_px=240,height_px=160)
print(json.dumps({'png':base64.b64encode(png).decode(),'metrics':_portable_metrics(metrics)},ensure_ascii=False))
"""
        child = subprocess.run([sys.executable, "-c", script, str(baseline)],
            input=json.dumps({"admitted": asdict(admitted)}), cwd=ROOT / "servers/fastapi",
            capture_output=True, text=True, encoding="utf-8", timeout=60, check=True)
        before = json.loads(child.stdout.strip().splitlines()[-1])
        png, metrics = slide_capture._render_admitted_html_over_pipe(admitted, 60, width_px=240, height_px=160)
        require(png == base64.b64decode(before["png"], validate=True), "head_absent_png_mismatch")
        require(_portable_metrics(metrics) == before["metrics"], "head_absent_portable_metrics_mismatch")
    result = {"status": "passed", "elapsedSeconds": (time.perf_counter_ns() - started) / 1e9,
              "identity": identity, "scope": "One deterministic ROOT HEAD/current absent PNG and all portable metrics with ROOT dependency base; not universal parity"}
    write_json(output / "head-current-absent-parity.json", result)
    return result


def actual_code_digests(inventory: dict, provenance: dict) -> dict:
    paths = {item["path"] for item in inventory["files"]}
    paths.update(provenance["codeDigests"])
    paths.update({"servers/fastapi/services/generation_core/drivers/svg/chart_content_paint.py",
                  "servers/fastapi/services/generation_core/drivers/svg/native_chart_evidence.py",
                  "servers/fastapi/services/generation_core/drivers/svg/chart_content_cartesian.py",
                  "servers/fastapi/services/generation_core/drivers/svg/chart_content_polar.py",
                  "servers/fastapi/services/generation_core/drivers/svg/native_chart.py",
                  "servers/fastapi/services/generation_core/drivers/svg/native_chart_visual.py",
                  "servers/fastapi/services/generation_core/drivers/svg/native_declarations.py",
                  "servers/fastapi/services/generation_core/drivers/svg/native_geometry.py",
                  "servers/fastapi/services/generation_core/drivers/svg/native_paint.py",
                  "servers/fastapi/services/generation_core/drivers/svg/fallback.py",
                  "servers/fastapi/services/svg_text_overlap.py",
                  "servers/fastapi/services/generation_core/drivers/svg/native_requirements.py",
                  "servers/fastapi/services/generation_core/drivers/svg/render.py",
                  "servers/fastapi/services/generation_core/drivers/svg/native_publish.py",
                  "servers/fastapi/services/generation_core/drivers/svg/export_projection.py",
                  "servers/fastapi/services/generation_core/native_objects/model.py",
                  "servers/fastapi/vendor/slide_master/svg_to_pptx/native_objects/chart_data.py",
                  "servers/fastapi/vendor/slide_master/svg_to_pptx/native_objects/chart_xml.py",
                  "servers/fastapi/services/svg_presentation_checkpoint.py",
                  "servers/fastapi/services/svg_master_furniture.py"})
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in sorted(paths)}


def manifest_counts(manifest: dict) -> dict:
    from utils.slide_capture_chart_paint import _targets
    targets = _targets(manifest)
    pairs = set()
    by_id = {target["id"]: target for target in targets}
    classes = 0
    for chart in manifest["charts"]:
        classes += len(chart["compositionGroup"].get("equivalenceClasses", []))
        for target in targets:
            if target["chartId"] != chart["id"]:
                continue
            for obligation in target.get("geometrySupport", {}).get("visibilityObligations", []):
                if obligation.get("primitiveQid") != target["qid"]:
                    continue
                for alternative in obligation.get("alternatives", []):
                    neighbor = by_id.get(alternative.get("neighborTargetId"))
                    if alternative["kind"] == "shared-stack-interface" and neighbor is not None:
                        pairs.add(tuple(sorted({target["qid"], neighbor["qid"]})))
    primitive = sum(target["kind"] == "primitive" for target in targets)
    return {"targetCount": len(targets), "primitiveTargetCount": primitive,
            "glyphRangeTargetCount": len(targets) - primitive,
            "equivalenceClassCount": classes, "sharedStackPairCount": len(pairs),
            "expectedManifestPlanes": 8 * len(targets) + 7 * classes + 2 * len(pairs),
            "runtimeGlyphClusterPlanes": "four-per-native-shaped-cluster; count unavailable from manifest alone",
            "basis": "authored-manifest-only; complete successful target/support paths; no runtime estimate"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--fixture-sha256", required=True)
    parser.add_argument("--pin-dir", type=Path, required=True)
    parser.add_argument("--runtime-inventory", type=Path, required=True)
    parser.add_argument("--consumer-lf-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--only", choices=['column', 'coincident-line', 'stacked-column', 'donut', 'fixed-column', 'separator-pie', 'marker-line', 'horizontal-bar', 'signed-zero-multiseries-area'])
    parser.add_argument("--reuse-manifest", type=Path)
    args = parser.parse_args()
    output, pin_dir = args.output.resolve(), args.pin_dir.resolve()
    require(output.is_relative_to(HERE), "capture_output_outside_owned_directory")
    output.mkdir(parents=True, exist_ok=False)
    require(hashlib.sha256(args.fixtures.read_bytes()).hexdigest() == args.fixture_sha256.removeprefix("sha256:"),
            "authored_fixture_digest_mismatch")
    fixtures = json.loads(args.fixtures.read_text(encoding="utf-8"))
    require(fixtures["caseCount"] == 9 and fixtures["capturesExecuted"] is False, "authored_nine_fixture_required")
    provenance = verify_pin(pin_dir)
    snapshot = json.loads((pin_dir / "style-snapshot.json").read_text(encoding="utf-8"))
    context = json.loads((pin_dir / "production-capture-context.json").read_text(encoding="utf-8"))
    outline = json.loads((pin_dir / "outline.json").read_text(encoding="utf-8"))
    requested_sources = json.loads((pin_dir / "available-requested-sources.json").read_text(encoding="utf-8"))
    inventory = json.loads(args.runtime_inventory.read_text(encoding="utf-8"))
    require(fixtures["snapshotDigest"] == snapshot["digest"] == provenance["snapshotDigest"], "snapshot_identity_mismatch")
    require(Path(fixtures["pinDirectory"]).resolve() == pin_dir, "fixture_requires_exact_authoring_pin")
    require([c["id"] for c in fixtures["cases"]] == provenance["caseOrder"], "actual_pin_case_order_mismatch")
    for item in inventory["files"]:
        actual = hashlib.sha256((ROOT / item["path"]).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        require(actual == item["lfSha256"].removeprefix("sha256:"), "reviewed_renderer_source_mismatch")
    code_digests = actual_code_digests(inventory, provenance)
    consumer_path = "servers/fastapi/services/generation_core/drivers/svg/chart_content_paint.py"
    require(hashlib.sha256((ROOT / consumer_path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
            == args.consumer_lf_sha256.removeprefix("sha256:"), "reviewed_root_consumer_mismatch")
    for name, expected in provenance["codeDigests"].items():
        require(code_digests[name] == expected.removeprefix("sha256:"), "frozen_pin_source_changed")
    sys.path.insert(0, str(ROOT / "servers/fastapi"))
    from models.generate_presentation_request import GeneratePresentationRequest
    from services.svg_authoring_profile import (
        validate_profile_agreement, validate_quality_recovery_agreement, read_quality_recovery_policy,
    )
    from services.authored_canvas import request_canvas_metadata
    from services.generation_core.drivers.svg.native_publish import canvas_point_per_px
    from services.generation_core.drivers.svg.native_declarations import PAYLOAD_KEY
    from services.generation_core.drivers.svg.native_chart_evidence import chart_drawing_evidence, chart_witness_render_kwargs
    from services.generation_core.drivers.svg.native_requirements import (
        apply_requested_native_quality, requirements_revision, REQUIREMENTS_VERSION_V5,
        required_numeric_claims_with_native_charts,
    )
    from services.generation_core.drivers.svg.render import _native_source_content
    from services.svg_renderer import render_svg_with_metrics
    from services.document_canvas_generation import validate_svg_readability_publication
    from utils.slide_capture_chart_timing import parse_timing_line
    import services.generation_core.drivers.svg.chart_content_paint as consumer_module
    require(Path(consumer_module.__file__).resolve() == (ROOT / consumer_path).resolve(), "stale_consumer_import")
    request = GeneratePresentationRequest.model_validate(json.loads((pin_dir / "request.json").read_text(encoding="utf-8")))
    request._svg_style_snapshot = copy.deepcopy(snapshot)
    validate_profile_agreement(request, snapshot)
    validate_quality_recovery_agreement(request, snapshot)
    require(requirements_revision(snapshot) == REQUIREMENTS_VERSION_V5, "genuine_v5_pin_required")
    require(context.get("actualRequestDocumentCanvasPresent") is False, "ordinary_profile_context_required")
    metadata = request_canvas_metadata(request)
    pt_per_px = canvas_point_per_px(metadata)
    cases = fixtures["cases"]
    require(len(outline["slides"]) == len(cases) and set(requested_sources) == {case["id"] for case in cases},
            "pinned_nine_source_set_mismatch")
    for index, case in enumerate(cases):
        require(case["index"] == index and case["requestedSource"] == requested_sources[case["id"]]
                and case["requestedSource"]["content"] == outline["slides"][index]["content"],
                "fixture_pinned_requested_source_mismatch")
        require(_native_source_content(request, index, len(cases), outline["slides"][index]["content"])
                == case["requestedSource"]["content"], "pre_post_native_source_mismatch")
    helpers = {str(path.resolve()): reference(path)["sha256"] for path in (
        Path(__file__), HERE / "prepare_sources.py",
        WORKTREE / "servers/fastapi/tests/unit/test_svg_chart_paint_chromium.py")}
    def helpers_unchanged() -> bool:
        return all(reference(Path(name))["sha256"] == expected for name, expected in helpers.items())
    parity_identity, baseline_sources = parity_sources()
    manifest = {"schema": "normal-profile-nine-capture-manifest/v1", "synthetic": True,
                "generationHttpMeasured": False, "pinDirectory": str(pin_dir), "fixture": reference(args.fixtures),
                "pinProvenance": reference(pin_dir / "pin-provenance.json"),
                "captureHarnessSha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "runtimeHarnessSha256": hashlib.sha256((ROOT / "scripts/svg_freeze_runtime_replay.py").read_bytes()).hexdigest(),
                "snapshotDigest": snapshot["digest"], "caseOrder": [c["id"] for c in cases],
                "helperDigests": helpers,
                "codeDigests": code_digests, "records": [{"id": c["id"], "manifestCounts": manifest_counts(c["manifest"]),
                    "status": "unrun-not-selected" if args.only and c["id"] != args.only else "unrun"} for c in cases]}
    manifest_path = output / "capture-manifest.json"
    write_json(manifest_path, manifest)
    failed = False
    try:
        reuse = {}
        if args.reuse_manifest:
            previous = json.loads(args.reuse_manifest.read_text(encoding="utf-8"))
            require(previous["snapshotDigest"] == manifest["snapshotDigest"] and previous["codeDigests"] == code_digests
                    and previous["fixture"] == manifest["fixture"] and previous["pinProvenance"] == manifest["pinProvenance"]
                    and previous["captureHarnessSha256"] == manifest["captureHarnessSha256"]
                    and previous["helperDigests"] == helpers,
                    "reuse_source_pin_mismatch")
            parity_ref = previous["headAbsentParityArtifact"]
            require(reference(Path(parity_ref["path"])) == parity_ref, "reuse_parity_artifact_digest_mismatch")
            parity = json.loads(Path(parity_ref["path"]).read_text(encoding="utf-8"))
            require(parity.get("status") == "passed" and parity.get("identity") == parity_identity
                    and previous["headAbsentParity"] == parity, "reuse_parity_identity_mismatch")
            manifest["headAbsentParity"] = copy.deepcopy(previous["headAbsentParity"])
            manifest["headAbsentParityArtifact"] = parity_ref
            manifest["headAbsentParityReusedFrom"] = reference(args.reuse_manifest)
            for record in previous["records"]:
                if record["status"] == "passed":
                    for ref in record["artifacts"].values():
                        require(reference(Path(ref["path"])) == ref, "reuse_artifact_digest_mismatch")
                    reuse[record["id"]] = record
        else:
            manifest["headAbsentParity"] = head_absent_parity(output, parity_identity, baseline_sources)
            manifest["headAbsentParityArtifact"] = reference(output / "head-current-absent-parity.json")
        for case, record in zip(cases, manifest["records"]):
            if args.only and case["id"] != args.only:
                continue
            if case["id"] in reuse:
                record.update(copy.deepcopy(reuse[case["id"]]), reusedUnchanged=True)
                continue
            if failed:
                record["status"] = "unrun-after-failure"
                continue
            directory = output / case["id"]
            directory.mkdir()
            record.update(status="started", startedUtc=utc(), publicRenderInvoked=False, phase="preflight")
            with (output / "events.jsonl").open("a", encoding="utf-8") as events:
                events.write(json.dumps({"id": case["id"], "status": "started", "utc": record["startedUtc"]}) + "\n")
                events.flush()
                os.fsync(events.fileno())
            write_json(manifest_path, manifest)
            timings = []
            class TimingHandler(logging.Handler):
                def emit(self, event):
                    try:
                        parsed = parse_timing_line(event.getMessage().encode("ascii"))
                        if parsed is not None:
                            timings.append(parsed)
                            del timings[:-8]
                    except Exception:
                        pass
            logger = logging.getLogger("utils.slide_capture_worker_client")
            old_level, old_propagate = logger.level, logger.propagate
            handler = TimingHandler()
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)
            logger.propagate = False
            capture_start = None
            capture_end = None
            try:
                require(actual_code_digests(inventory, provenance) == code_digests, "source_changed_before_capture")
                require(helpers_unchanged(), "helper_changed_before_capture")
                source, truth = case["canonicalSvg"], case["requestedSource"]
                native_source = _native_source_content(request, case["index"], len(cases), outline["slides"][case["index"]]["content"])
                require(native_source == truth["content"], "pre_post_native_source_mismatch")
                require(digest(canonical({key: truth[key] for key in ("content", "payload")})) == truth["sourceDigest"], "requested_truth_digest_mismatch")
                payload = {"svg_content": source, PAYLOAD_KEY: copy.deepcopy(case["nativeDeclarations"]),
                           "readability_policy": copy.deepcopy(snapshot["readability_policy"])}
                kwargs = chart_witness_render_kwargs(payload, source, revision=requirements_revision(snapshot))
                require(kwargs and canonical(kwargs["chart_witness_manifest"]) == canonical(case["manifest"]), "final_witness_rederivation_mismatch")
                # Keep this identical to the reviewed actual driver's required-text helper.
                required_text = required_numeric_claims_with_native_charts(
                    truth["content"], payload, source,
                    native_source=native_source,
                    pt_per_px=pt_per_px, revision=REQUIREMENTS_VERSION_V5,
                    quality_policy=read_quality_recovery_policy(snapshot))
                capture_start = time.perf_counter_ns()
                record["captureStartedUtc"] = utc()
                record.update(publicRenderInvoked=True, phase="capture")
                result = asyncio.run(render_svg_with_metrics(source, width=context["widthPx"], height=context["heightPx"],
                    instructions=request.instructions, readability_policy=snapshot["readability_policy"],
                    required_text=required_text, system_furniture=case["systemFurniture"], **kwargs))
                capture_end = time.perf_counter_ns()
                record["captureFinishedUtc"] = utc()
                record["phase"] = "producer-validation"
                producer = copy.deepcopy(result.quality)
                write_json(directory / "actual-svg-render-result.json", {"schema": "svg-render-result-artifact/v1",
                    "type": "services.svg_renderer.SvgRenderResult", "pngBase64": base64.b64encode(result.png).decode("ascii"),
                    "quality": producer, "wrapper_html": result.wrapper_html, "source_digest": result.source_digest})
                (directory / "final.png").write_bytes(result.png)
                (directory / "canonical.svg").write_bytes(source.encode("utf-8"))
                (directory / "wrapper.html").write_bytes(result.wrapper_html.encode("utf-8"))
                write_json(directory / "producer-quality.json", producer)
                write_json(directory / "requested-source.json", truth)
                write_json(directory / "case-fixture.json", case)
                write_json(directory / "witness-manifest.json", kwargs["chart_witness_manifest"])
                metrics = producer["browser_metrics"]
                write_json(directory / "browser-metrics.json", metrics)
                write_json(directory / "paint-proof.json", metrics["nativeChartPaintProof"])
                require(actual_code_digests(inventory, provenance) == code_digests, "source_changed_during_capture")
                require(helpers_unchanged(), "helper_changed_during_capture")
                require(FIELDS <= set(producer) and producer["passed"] is True and producer["geometry_passed"] is True
                        and producer["measurement_complete"] is True and producer["failures"] == [], "original_producer_quality_not_positive")
                require(isinstance(producer["physical_readability"], dict) and producer["physical_readability"], "actual_readability_missing")
                require(isinstance(producer["trusted_font_materialization"], dict)
                        and producer["trusted_font_materialization"].get("worker_materialized") is True, "actual_frozen_font_materialization_missing")
                require(producer["body_font_floor_px"] == context["normalGenerationFontFloorPx"], "actual_generation_font_floor_mismatch")
                validate_svg_readability_publication(snapshot["readability_policy"], producer)
                require(metrics["nativeChartPaintProof"].get("complete") is True
                        and metrics["nativeChartPaintProof"].get("paintRetained") is True, "producer_paint_unproven")
                consumer_start = time.perf_counter_ns()
                record["phase"] = "consumer"
                strict = consumer_module.validate_chart_content_paint(kwargs["chart_witness_manifest"], producer, capture=result)
                write_json(directory / "strict-consumer.json", strict)
                evidence = chart_drawing_evidence(payload, source, revision=REQUIREMENTS_VERSION_V5,
                    measurement=producer, render_capture=result, post_render=True)
                derived = copy.deepcopy(producer)
                apply_requested_native_quality(derived, payload, source, native_source, pt_per_px=pt_per_px,
                    revision=REQUIREMENTS_VERSION_V5, render_capture=result, post_render=True)
                positive = strict.get("passed") is True and bool(evidence) and all(e.get("contentGeometry", {}).get("passed") is True
                    and e.get("paintEvidence", {}).get("passed") is True for e in evidence.values())
                positive = positive and derived.get("passed") is True and derived.get("requestedNativeObjects", {}).get("contentRetentionProven") is True
                receipt = {"schema": "actual-normal-profile-v5-consumer/v1", "passed": positive,
                    "consumerLfSha256": args.consumer_lf_sha256.removeprefix("sha256:"), "evidence": evidence,
                    "requestedNativeObjects": derived.get("requestedNativeObjects"), "failures": derived.get("failures"),
                    "sourceDigest": result.source_digest, "pngDigest": producer["png_digest"]}
                write_json(directory / "semantic-retention-receipt.json", receipt)
                payload["quality"] = derived
                write_json(directory / "page-payload.json", payload)
                record["consumerElapsedSeconds"] = (time.perf_counter_ns() - consumer_start) / 1e9
                require(positive, "actual_strict_v5_consumer_rejected")
                require(actual_code_digests(inventory, provenance) == code_digests, "source_changed_during_consumer")
                require(helpers_unchanged(), "helper_changed_during_consumer")
                require(hashlib.sha256((ROOT / "scripts/svg_freeze_runtime_replay.py").read_bytes()).hexdigest()
                        == manifest["runtimeHarnessSha256"], "runtime_replay_source_changed")
                record["status"] = "passed"
            except Exception as error:
                failed = True
                record.update(status="failed", errorClass=type(error).__name__, reason=str(error)[:500])
            finally:
                if capture_start is not None:
                    capture_end = capture_end or time.perf_counter_ns()
                    record.update(captureElapsedSeconds=(capture_end - capture_start) / 1e9,
                                  captureStartedPerfCounterNs=capture_start, captureFinishedPerfCounterNs=capture_end,
                                  timeoutArgumentOmitted=True,
                                  requestTimeoutSeconds=60, clientHardDeadlineSeconds=85)
                    record.setdefault("captureFinishedUtc", utc())
                logger.removeHandler(handler)
                logger.setLevel(old_level)
                logger.propagate = old_propagate
                write_json(directory / "private-timing-snapshots.json", {"schema": "chart-private-timing-evidence/v1", "records": timings})
                record["finishedUtc"] = utc()
                record["artifacts"] = {path.name: reference(path) for path in directory.iterdir() if path.is_file()}
                write_json(manifest_path, manifest)
        manifest["capturesComplete"] = all(r["status"] == "passed" for r in manifest["records"])
        manifest["pendingCases"] = [r["id"] for r in manifest["records"] if r["status"] != "passed"]
        if manifest["capturesComplete"]:
            require(actual_code_digests(inventory, provenance) == code_digests and helpers_unchanged(),
                    "source_or_helper_changed_before_assembly_index")
            require(hashlib.sha256((ROOT / "scripts/svg_freeze_runtime_replay.py").read_bytes()).hexdigest()
                    == manifest["runtimeHarnessSha256"], "runtime_replay_changed_before_assembly_index")
            require(verify_pin(pin_dir) == provenance and reference(args.fixtures) == manifest["fixture"],
                    "pin_or_fixture_changed_before_assembly_index")
            names = {"svg": "canonical.svg", "payload": "page-payload.json", "capture": "actual-svg-render-result.json",
                     "producerQuality": "producer-quality.json", "png": "final.png", "consumer": "strict-consumer.json",
                     "witnessManifest": "witness-manifest.json"}
            assembly = {"schema": "svg-publication-assembly-input/v1", "synthetic": True, "capturesComplete": True,
                        "pendingCases": [], "caseOrder": manifest["caseOrder"], "runtimeHarnessSha256": manifest["runtimeHarnessSha256"],
                        "pinProvenance": manifest["pinProvenance"], "fixture": manifest["fixture"], "codeDigests": code_digests,
                        "pages": [{"fixtureName": r["id"], **{key: r["artifacts"][name] for key, name in names.items()}}
                                  for r in manifest["records"]]}
            write_json(output / "publication-assembly-input.json", assembly)
    except Exception as error:
        failed = True
        manifest["setupFailure"] = {"errorClass": type(error).__name__, "reason": str(error)[:500]}
    finally:
        manifest["finishedUtc"] = utc()
        write_json(manifest_path, manifest)
    print(json.dumps({"status": "failed" if failed else "selected-cases-passed", "records": [{"id": r["id"], "status": r["status"]} for r in manifest["records"]]}))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
