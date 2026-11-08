"""Prepare genuine deterministic pin inputs; never render, publish or call providers."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "servers" / "fastapi"
sys.path.insert(0, str(BACKEND))
APPROVED_OUTPUT = Path(__file__).resolve().parent
CASE_NAMES = (
    "column", "coincident-line", "stacked-column", "donut", "fixed-column",
    "separator-pie", "marker-line", "horizontal-bar", "signed-zero-multiseries-area",
)
PIN_CODE = (
    "services/svg_generation_admission.py", "utils/get_env_concurrency.py",
    "models/generate_presentation_request.py", "models/canvas_defaults.py",
    "services/svg_authoring_profile.py", "services/svg_common_header_design_plan.py",
    "services/generation_core/drivers/svg/svg_style_adapter.py",
    "services/generation_core/drivers/svg/project_style.py",
    "services/generation_core/style/build.py", "services/authored_canvas.py",
    "services/document_canvas_generation.py", "services/svg_contract.py",
    "services/generation_core/drivers/svg/native_requirements.py", "utils/authored_styles.py",
    "services/svg_trusted_fonts.py", "services/svg_default_fonts.py", "utils/svg_template_fonts.py",
    "services/svg_presentation_prepare.py", "services/generation_core/drivers/svg/render.py",
)


def encoded(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def sha(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def code_digests() -> dict:
    return {"servers/fastapi/" + name: sha((BACKEND / name).read_bytes()) for name in PIN_CODE}


def write_new(directory: Path, name: str, value) -> dict:
    payload = encoded(value) + b"\n"
    with (directory / name).open("xb") as handle:
        handle.write(payload)
    return {"path": name, "sha256": sha(payload)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", "--requested-inputs", dest="source_input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if not output.is_relative_to(APPROVED_OUTPUT) or output == APPROVED_OUTPUT:
        raise ValueError("new_scoped_output_subdirectory_required")
    if output.exists():
        raise ValueError("new_output_directory_required")
    fixture_bytes = args.source_input.resolve().read_bytes()
    fixture = json.loads(fixture_bytes)
    if fixture.get("schema") == "svg-chart-support-graph-examples/v1":
        examples = {example["name"]: example for example in fixture["examples"]}
        source_count = len(fixture["examples"])
    elif fixture.get("schema") == "normal-profile-chart-requested-inputs/v1":
        if fixture.get("caseCount") != len(CASE_NAMES) or fixture.get("common_header_mode") != "free":
            raise ValueError("nine_source_request_context_mismatch")
        examples = {example["id"]: example for example in fixture["cases"]}
        source_count = len(fixture["cases"])
    else:
        raise ValueError("actual_fixture_schema_required")
    if len(examples) != source_count or set(examples) - set(CASE_NAMES):
        raise ValueError("unexpected_or_duplicate_fixture_cases")
    requested_sources = {}
    for name, example in examples.items():
        source = example["requestedSource"]
        if (source["schema"] != "synthetic-chart-request/v1"
                or source["sourceDigest"] != sha(encoded({"content": source["content"], "payload": source["payload"]}))
                or source["provenance"].get("authoredBeforeAcceptanceCapture") is not True
                or source["provenance"].get("generationHttpMeasured") is not False):
            raise ValueError("independent_pre_capture_requested_source_required")
        requested_sources[name] = source
    missing = [name for name in CASE_NAMES if name not in requested_sources]
    before_code = code_digests()
    previous_opt_in = os.environ.get("SVG_NATIVE_CHART_INTEGRITY_V5")
    os.environ["SVG_NATIVE_CHART_INTEGRITY_V5"] = "true"
    try:
        from models.generate_presentation_request import GeneratePresentationRequest
        from models.presentation_outline_model import PresentationOutlineModel
        from services.authored_canvas import request_canvas_metadata
        from services.document_canvas import parse_canvas_metadata
        from services.document_canvas_generation import (
            generation_font_floor, svg_quality_font_floor, svg_readability_font_floor,
            validate_svg_readability_policy,
        )
        from services.generation_core.drivers.svg.native_requirements import (
            ADMITTED_REQUIREMENTS_REVISION, REQUIREMENTS_VERSION_V5, requirements_revision,
        )
        from services.svg_default_fonts import FONT_PROFILE
        from services.svg_generation_admission import pin_svg_style
        from services.svg_trusted_fonts import read_trusted_pretendard_faces
        from utils.get_env_concurrency import get_svg_native_chart_integrity_v5_env
        from utils.svg_template_fonts import FONT_FAMILY, font_material

        if not get_svg_native_chart_integrity_v5_env():
            raise ValueError("process_local_fresh_v5_opt_in_not_effective")
        # This ordinary public free-page request deliberately contains no
        # invented source values or placeholder pages. Facts live in the
        # independent requestedSource records and eventual complete outline.
        request = GeneratePresentationRequest.model_validate({
            "content": "Synthetic provider-free native-chart replay of nine independently supplied chart cases.",
            "strategy": "svg", "n_slides": len(CASE_NAMES), "language": "English",
            "aspect_ratio": "16:9", "common_header_mode": "free",
            "include_title_slide": False, "include_table_of_contents": False,
        })
        snapshot = pin_svg_style(request)  # Actual existing admission pin; no replacement fields.
        if requirements_revision(snapshot) != REQUIREMENTS_VERSION_V5:
            raise ValueError("actual_pin_did_not_select_v5")
        canvas_wire = request_canvas_metadata(request)
        canvas = parse_canvas_metadata(canvas_wire)
        actual_document_canvas = getattr(request, "_document_canvas", None)
        readability = validate_svg_readability_policy(snapshot["readability_policy"])
        request_wire = request.model_dump(mode="json", by_alias=True)
        font_digest, font_bytes = font_material(FONT_FAMILY)
        faces = [{"family": FONT_FAMILY, "weights": "100-900", "sha256": font_digest,
                  "sizeBytes": len(font_bytes)}]
        faces.extend({"family": "Pretendard", "weight": weight, "sha256": digest,
                      "sizeBytes": len(payload)}
                     for weight, digest, payload in read_trusted_pretendard_faces())
        context = {
            "nativeRequirementsRevision": requirements_revision(snapshot),
            "unchangedGlobalAdmissionRevision": ADMITTED_REQUIREMENTS_REVISION,
            "authoringProfile": snapshot.get("authoring_profile"),
            "qualityRecoveryPolicy": snapshot.get("quality_recovery_policy"),
            "qualityRecoveryPlan": snapshot.get("quality_recovery_plan"),
            "styleId": snapshot.get("style_id"), "tokens": snapshot["tokens"],
            "publicCommonHeaderMode": request.common_header_mode,
            "canvas": canvas_wire, "widthPx": canvas.width_px, "heightPx": canvas.height_px,
            "physicalWidthEmu": readability["width_emu"], "physicalHeightEmu": readability["height_emu"],
            "readabilityPolicy": readability,
            "normalGenerationFontFloorPx": generation_font_floor(request.instructions, actual_document_canvas),
            "readabilityHardFloorPx": svg_readability_font_floor(readability),
            "readabilityQualityTargetPx": svg_quality_font_floor(readability),
            "defaultBundledFontProfile": FONT_PROFILE, "bundledFontSources": faces,
            "actualRequestDocumentCanvasPresent": actual_document_canvas is not None,
            "actualRequestDocumentCanvasType": type(actual_document_canvas).__name__ if actual_document_canvas is not None else None,
            "captureRequest": {
                "function": "services.svg_renderer.render_svg_with_metrics",
                "kwargs": {"width": canvas.width_px, "height": canvas.height_px,
                           "instructions": request.instructions, "readability_policy": readability},
                "typedKwargs": ({"physical_canvas": {
                    "constructor": "services.document_canvas.parse_canvas_metadata",
                    "wire": actual_document_canvas.model_dump(mode="json", by_alias=True)}}
                    if actual_document_canvas is not None else {}),
                "omittedKwargs": ["minimum_font_px", "timeout", "source_font_profile"]
                                 + ([] if actual_document_canvas is not None else ["physical_canvas"]),
                "physicalKwargsAuthority": "services.svg_presentation_prepare._prepare_generation context physical_kwargs",
                "perPageKwargsFunction": "services.generation_core.drivers.svg.native_chart_evidence.chart_witness_render_kwargs",
                "perPageKwargsRequiresFinalSvgAndBoundDeclarations": True,
            },
            "finalization": {
                "function": "services.svg_contract.finalize_svg", "count": len(CASE_NAMES),
                "indices": list(range(len(CASE_NAMES))), "styleSnapshotDigest": snapshot["digest"],
                "beforeDeclarationBindingManifestAndCapture": True,
                "normalSystemPageNumber": {"fontFamily": snapshot["tokens"]["body_font"], "fontSizePx": 12,
                                           "contentReadabilityExclusion": "existing renderer PAGE_NUMBER_ID rule"},
                "noArialOrOldCapturePatching": True,
            },
        }
    finally:
        if previous_opt_in is None:
            os.environ.pop("SVG_NATIVE_CHART_INTEGRITY_V5", None)
        else:
            os.environ["SVG_NATIVE_CHART_INTEGRITY_V5"] = previous_opt_in
    after_code = code_digests()
    if before_code != after_code:
        raise ValueError("pin_source_changed_during_execution")
    output.mkdir(parents=True)
    refs = {
        "request": write_new(output, "request.json", request_wire),
        "styleSnapshot": write_new(output, "style-snapshot.json", snapshot),
        "captureContext": write_new(output, "production-capture-context.json", context),
        "requestedSources": write_new(output, "available-requested-sources.json", requested_sources),
    }
    if not missing:
        outline = PresentationOutlineModel.model_validate({
            "title": "Synthetic native chart replay",
            "slides": [{"content": requested_sources[name]["content"]} for name in CASE_NAMES],
        })
        refs["outline"] = write_new(output, "outline.json", outline.model_dump(mode="json"))
    provenance = {
        "schema": "svg-runtime-genuine-pin-input/v1", "synthetic": True,
        "function": "services.svg_generation_admission.pin_svg_style",
        "requestDigest": sha(encoded(request_wire)), "snapshotDigest": snapshot["digest"],
        "processLocalFreshOptIn": {"SVG_NATIVE_CHART_INTEGRITY_V5": True, "restoredAfterPin": True},
        "managedEnvironmentModified": False, "codeDigests": after_code,
        "preparationHelperDigest": sha(Path(__file__).read_bytes()),
        "sourceInput": {"schema": fixture["schema"], "path": str(args.source_input.resolve()),
                        "sha256": sha(fixture_bytes)},
        "caseOrder": list(CASE_NAMES), "availableRequestedSourceCount": len(requested_sources),
        "pendingRequestedSourceCases": missing, "independentRequestedSourcesComplete": not missing,
        "publicationReady": False,
        "generationHttpMeasured": False, "capturesExecuted": False, "files": refs,
    }
    pin_ref = write_new(output, "pin-provenance.json", provenance)
    print(json.dumps({"status": "actual-profile-pinned" if missing else "actual-nine-page-input-pinned",
                      "pendingCaseCount": len(missing), "profile": context["authoringProfile"],
                      "canvas": [canvas.width_px, canvas.height_px],
                      "hardFloorPx": context["readabilityHardFloorPx"],
                      "qualityTargetPx": context["readabilityQualityTargetPx"],
                      "fonts": {key: context["tokens"][key] for key in ("heading_font", "body_font", "mono_font")},
                      "snapshotDigest": snapshot["digest"], "pinProvenance": pin_ref}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({"status": "preparation-failed", "errorType": type(error).__name__}), file=sys.stderr)
        raise SystemExit(1)
