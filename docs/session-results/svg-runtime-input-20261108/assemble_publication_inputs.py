"""Assemble immutable provider-free replay inputs; never publish or render.

Execution requires the separately reviewed actual nine-capture input index.
See publication-assembler.md for its strict file-reference contract.
"""
from __future__ import annotations

import argparse
import base64
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "servers" / "fastapi"
HASH = re.compile(r"[0-9a-f]{64}\Z")
SCHEMA = "svg-publication-assembly-input/v1"
DERIVED = frozenset({"verified", "classification", "requestedNativeObjects"})
CASE_ORDER = (
    "column", "coincident-line", "stacked-column", "donut", "fixed-column",
    "separator-pie", "marker-line", "horizontal-bar", "signed-zero-multiseries-area",
)
ADDITIONAL_CODE = {
    "servers/fastapi/services/generation_core/drivers/svg/quality.py",
    "servers/fastapi/services/generation_core/drivers/svg/native_declarations.py",
    "servers/fastapi/services/generation_core/drivers/svg/native_publish.py",
    "servers/fastapi/services/generation_core/quality/page_outcome.py",
    "servers/fastapi/services/svg_contract.py",
    "servers/fastapi/services/document_canvas_generation.py",
    "servers/fastapi/services/svg_authoring_profile.py",
}


def require(condition, code):
    if not condition:
        raise ValueError(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def load(raw):
    return json.loads(raw.decode("utf-8"), parse_constant=lambda _: (_ for _ in ()).throw(
        ValueError("nonfinite_json_forbidden")))


def read(path):
    require(path.is_file() and path.stat().st_size <= 128 * 1024 * 1024,
            "missing_or_oversized_input")
    return path.read_bytes()


def reference(base, ref):
    require(isinstance(ref, dict) and set(ref) == {"path", "sha256"},
            "exact_input_reference_required")
    require(isinstance(ref["path"], str) and isinstance(ref["sha256"], str)
            and HASH.fullmatch(ref["sha256"]), "raw_sha256_reference_required")
    path = Path(ref["path"])
    path = (path if path.is_absolute() else base / path).resolve()
    raw = read(path)
    require(sha(raw) == ref["sha256"], "original_input_digest_mismatch")
    return path, raw


def replay_module(expected):
    path = ROOT / "scripts" / "svg_freeze_runtime_replay.py"
    require(sha(read(path)) == expected, "reviewed_runtime_harness_changed")
    spec = importlib.util.spec_from_file_location("_svg_replay_assembly_validator", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def unchanged_quality(producer, page):
    return canonical({k: v for k, v in producer.items() if k not in DERIVED}) == canonical(
        {k: v for k, v in page.items() if k not in DERIVED})


def assemble(index_path, output):
    """Only deterministic local reads, actual consumers, classification and file writes."""
    assembler_path = Path(__file__).resolve()
    assembler_digest = sha(read(assembler_path))
    index_raw = read(index_path)
    index = load(index_raw)
    require(index.get("schema") == SCHEMA and index.get("synthetic") is True,
            "synthetic_assembly_index_required")
    require(index.get("caseOrder") == list(CASE_ORDER), "exact_nine_case_order_required")
    require(index.get("capturesComplete") is True and index.get("pendingCases") == [],
            "all_actual_captures_required")
    base = index_path.parent
    replay = replay_module(index["runtimeHarnessSha256"])
    sys.path.insert(0, str(BACKEND))
    from models.generate_presentation_request import GeneratePresentationRequest
    from models.presentation_outline_model import PresentationOutlineModel
    from services.authored_canvas import request_canvas_metadata
    from services.document_canvas_generation import (
        validate_svg_readability_policy, validate_svg_readability_publication,
    )
    from services.generation_core.drivers.svg.chart_content_paint import validate_chart_content_paint
    from services.generation_core.drivers.svg.native_chart_evidence import build_chart_witness_manifest
    from services.generation_core.drivers.svg.native_declarations import bound_declarations
    from services.generation_core.drivers.svg.native_publish import canvas_point_per_px
    from services.generation_core.drivers.svg.native_requirements import apply_requested_native_quality
    from services.generation_core.drivers.svg.quality import reclassify_pages
    from services.svg_authoring_profile import validate_profile_agreement, validate_quality_recovery_agreement
    from services.svg_renderer import SvgRenderResult

    pin_path, pin_raw = reference(base, index["pinProvenance"])
    pin = load(pin_raw)
    require(pin.get("function") == "services.svg_generation_admission.pin_svg_style"
            and pin.get("independentRequestedSourcesComplete") is True
            and pin.get("availableRequestedSourceCount") == 9
            and pin.get("caseOrder") == list(CASE_ORDER), "actual_complete_nine_pin_required")
    pin_files = {}
    for key in ("request", "outline", "styleSnapshot", "captureContext", "requestedSources"):
        ref = dict(pin["files"][key])
        ref["sha256"] = ref["sha256"].removeprefix("sha256:")
        pin_files[key] = reference(pin_path.parent, ref)
    request_wire = load(pin_files["request"][1])
    outline_wire = load(pin_files["outline"][1])
    snapshot = load(pin_files["styleSnapshot"][1])
    requested_sources = load(pin_files["requestedSources"][1])
    require(pin["snapshotDigest"] == snapshot.get("digest")
            and pin["requestDigest"] == "sha256:" + sha(canonical(request_wire)),
            "actual_pin_request_snapshot_mismatch")
    request = GeneratePresentationRequest.model_validate(request_wire)
    outline = PresentationOutlineModel.model_validate(outline_wire)
    request._svg_style_snapshot = deepcopy(snapshot)
    validate_profile_agreement(request, snapshot)
    validate_quality_recovery_agreement(request, snapshot)
    readability = validate_svg_readability_policy(snapshot.get("readability_policy"))
    require(readability is not None, "actual_frozen_readability_required")
    pt_per_px = canvas_point_per_px(request_canvas_metadata(request))
    codes = index.get("codeDigests")
    require(isinstance(codes, dict) and replay.CODE_INPUTS <= set(codes),
            "actual_capture_code_provenance_required")
    for name, expected in {**pin["codeDigests"], **codes}.items():
        source = (ROOT / name).resolve()
        require(name.startswith("servers/fastapi/") and source.is_relative_to(BACKEND)
                and HASH.fullmatch(expected.removeprefix("sha256:"))
                and sha(read(source)) == expected.removeprefix("sha256:"),
                "pin_or_actual_capture_code_changed")
    # Check shared entries independently: merging must never conceal a stale pin hash.
    for name in pin["codeDigests"]:
        require(sha(read(ROOT / name)) == pin["codeDigests"][name].removeprefix("sha256:"),
                "actual_pin_code_changed")
    code_digests = {name: sha(read(ROOT / name))
                    for name in set(codes) | set(pin["codeDigests"]) | ADDITIONAL_CODE}
    fixture_path, fixture_raw = reference(base, index["fixture"])
    fixture = load(fixture_raw)
    require(fixture.get("schema") == "normal-profile-nine-chart-fixtures/v1"
            and fixture.get("caseCount") == 9
            and fixture.get("snapshotDigest") == snapshot["digest"]
            and Path(fixture["pinDirectory"]).resolve() == pin_path.parent,
            "actual_normal_profile_fixture_required")
    cases = fixture.get("cases")
    entries = index.get("pages")
    require(isinstance(cases, list) and [c.get("id") for c in cases] == list(CASE_ORDER)
            and isinstance(entries, list) and [e.get("fixtureName") for e in entries] == list(CASE_ORDER)
            and len(outline.slides) == request.n_slides == 9, "complete_ordered_nine_inputs_required")
    prepared, original_refs, mapped = [], [], []
    for i, (case, entry) in enumerate(zip(cases, entries, strict=True)):
        name = CASE_ORDER[i]
        require(case["index"] == i and case["requestedSource"] == requested_sources[name]
                and outline.slides[i].content == requested_sources[name]["content"]
                and case.get("readabilityPolicy") == readability
                and case.get("authoringProvenance", {}).get("finalizedBeforeDeclarationAndWitness") is True,
                "independent_source_or_frozen_fixture_mismatch")
        originals = {key: reference(base, entry[key]) for key in
                     ("svg", "payload", "capture", "producerQuality", "png", "consumer", "witnessManifest")}
        svg = originals["svg"][1].decode("utf-8")
        payload = load(originals["payload"][1])
        wire = load(originals["capture"][1])
        producer = wire.get("quality")
        require(wire.get("type") == "services.svg_renderer.SvgRenderResult"
                and set(wire) <= {"schema", "type", "pngBase64", "quality", "wrapper_html", "source_digest"}
                and {"pngBase64", "quality", "wrapper_html", "source_digest"} <= set(wire),
                "original_four_field_render_result_required")
        require(isinstance(producer, dict) and replay.PRODUCER_QUALITY_FIELDS <= set(producer)
                and producer.get("passed") is True and producer.get("geometry_passed") is True
                and producer.get("measurement_complete") is True and producer.get("failures") == []
                and producer == load(originals["producerQuality"][1]),
                "complete_passing_original_producer_required")
        png = base64.b64decode(wire["pngBase64"], validate=True)
        require(png == originals["png"][1] and png.startswith(b"\x89PNG\r\n\x1a\n"),
                "original_capture_png_mismatch")
        require(svg == case["canonicalSvg"] == payload.get("svg_content")
                and payload.get("readability_policy") == readability
                and isinstance(payload.get("quality"), dict)
                and unchanged_quality(producer, payload["quality"]),
                "original_payload_svg_policy_or_quality_mismatch")
        capture = SvgRenderResult(png=png, quality=deepcopy(producer),
                                  wrapper_html=wire["wrapper_html"], source_digest=wire["source_digest"])
        validate_svg_readability_publication(readability, producer)
        record, current = bound_declarations(payload, svg)
        require(current and record == case["nativeDeclarations"], "actual_declaration_binding_mismatch")
        witness = build_chart_witness_manifest(payload, svg, revision=replay.V5)
        require(witness == case["manifest"] == load(originals["witnessManifest"][1]),
                "actual_frozen_witness_mismatch")
        consumer = validate_chart_content_paint(witness, producer, capture=capture)
        require(consumer.get("passed") is True and consumer == load(originals["consumer"][1]),
                "actual_strict_consumer_missing_false_or_stale")
        quality = {k: deepcopy(v) for k, v in producer.items() if k not in DERIVED}
        apply_requested_native_quality(quality, payload, svg, outline.slides[i].content,
                                       pt_per_px=pt_per_px, revision=replay.V5,
                                       render_capture=capture, post_render=True)
        require(unchanged_quality(producer, quality) and quality.get("passed") is True
                and quality.get("failures") == []
                and quality.get("requestedNativeObjects", {}).get("requiredCharts", 0) > 0
                and quality["requestedNativeObjects"].get("contentRetentionProven") is True,
                "actual_native_derivation_not_positive_or_changed_producer_facts")
        page = deepcopy(payload)
        page["quality"] = quality
        prepared.append((page, png))
        original_refs.append(originals)
        mapped.append({"name": name, "canonicalSvg": svg,
                       "nativeDeclarations": deepcopy(record), "manifest": deepcopy(witness),
                       "requestedSource": deepcopy(case["requestedSource"])})
    summary = reclassify_pages(prepared)
    require(all(unchanged_quality(load(originals["capture"][1])["quality"], page["quality"])
                for originals, (page, _) in zip(original_refs, prepared, strict=True)),
            "classification_changed_original_producer_facts")
    # Never overwrite a previous attempt; partial output remains explicitly unready.
    output.mkdir(parents=True, exist_ok=False)
    def emit(name, raw):
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(raw)
        return {"path": name, "sha256": "sha256:" + sha(raw)}
    manifest = {"schema": replay.SCHEMA, "synthetic": True,
                "codeDigests": {name: "sha256:" + value for name, value in code_digests.items()},
                "pinnedInput": {"function": pin["function"], "snapshotDigest": snapshot["digest"],
                                "requestDigest": "sha256:" + sha(canonical(request_wire))}, "pages": []}
    for key, filename in (("request", "request.json"), ("outline", "outline.json"),
                          ("styleSnapshot", "style-snapshot.json")):
        manifest[key] = emit(filename, pin_files[key][1])
    manifest["fixture"] = emit("fixture.json", canonical({
        "schema": "svg-chart-support-graph-examples/v1", "evidenceScope": "actual-normal-profile-nine",
        "examples": mapped}))
    manifest["qualitySummary"] = emit("quality-summary.json", canonical(summary))
    emit("original-input-index.json", index_raw)
    emit("original-authored-fixture.json", fixture_raw)
    emit("actual-pin-provenance.json", pin_raw)
    for i, ((page, _), originals) in enumerate(zip(prepared, original_refs, strict=True)):
        folder = "pages/" + CASE_ORDER[i] + "/"
        entry = {"fixtureName": CASE_ORDER[i], "role": "content"}
        for key, filename in (("svg", "canonical.svg"), ("capture", "actual-render-result.json"),
                              ("consumer", "strict-consumer.json")):
            entry[key] = emit(folder + filename, originals[key][1])
        entry["payload"] = emit(folder + "payload.json", canonical(page))
        manifest["pages"].append(entry)
        for key, filename in (("producerQuality", "original-producer-quality.json"),
                              ("png", "original-capture.png"), ("payload", "original-page-payload.json"),
                              ("witnessManifest", "original-witness-manifest.json")):
            emit(folder + filename, originals[key][1])
    pending = output / "unvalidated-manifest.json"
    emit(pending.name, canonical(manifest))
    validated = replay.validate_inputs(pending)
    # Recheck exact code/input bytes across all local derivations.
    require(sha(read(index_path)) == sha(index_raw), "input_index_changed_during_assembly")
    for name, expected in code_digests.items():
        require(sha(read(ROOT / name)) == expected, "consumer_code_changed_during_assembly")
    audit = {"schema": "svg-publication-assembly-provenance/v1", "synthetic": True,
             "assemblyIndexDigest": sha(index_raw), "authoredFixtureDigest": sha(fixture_raw),
             "pinProvenanceDigest": sha(pin_raw), "runtimeHarnessDigest": index["runtimeHarnessSha256"],
             "assemblerDigest": assembler_digest, "derivedQualityFields": sorted(DERIVED),
             "codeDigests": code_digests, "validatedReplayProvenance": validated.provenance,
             "publicationExecuted": False, "generationHttpMeasured": False,
             "providerCallsExecuted": False, "captureExecuted": False}
    emit("assembly-provenance.json", canonical(audit))
    require(sha(read(assembler_path)) == assembler_digest
            and sha(read(ROOT / "scripts" / "svg_freeze_runtime_replay.py"))
            == index["runtimeHarnessSha256"], "assembly_tools_changed_during_derivation")
    require(read(pending) == canonical(manifest), "pending_manifest_changed_before_promotion")
    pending.rename(output / "replay-manifest.json")
    return {"assembled": True, "pageCount": 9, "manifestSha256": sha(canonical(manifest)),
            "publicationExecuted": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-index", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = assemble(args.capture_index.resolve(), args.output.resolve())
    except Exception:
        # Avoid logging source text, private absolute paths or exception payloads.
        print(json.dumps({"assembled": False, "error": "assembly_failed_closed"}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
