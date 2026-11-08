"""Author synthetic normal-profile sources; never capture, publish, or access a DB."""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys
from xml.etree import ElementTree as ET

ROOT = Path("C:/project/PPT-agent/ppt-agent")
OWNER = Path("C:/Users/ibiz/orca/workspaces/ppt-agent/svg-chart-integrity-1107")
HERE = Path(__file__).resolve().parent


def digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pin-dir", type=Path, required=True)
    parser.add_argument("--requested-inputs", type=Path, default=HERE / "requested-inputs.json")
    parser.add_argument("--output", type=Path, default=HERE / "authored")
    args = parser.parse_args()
    pin_dir, output = args.pin_dir.resolve(), args.output.resolve()
    if not output.is_relative_to(HERE):
        raise ValueError("authoring_output_outside_owned_fixture_directory")
    output.mkdir(parents=True, exist_ok=True)
    pin_provenance = json.loads((pin_dir / "pin-provenance.json").read_text(encoding="utf-8"))
    for artifact in pin_provenance["files"].values():
        path = (pin_dir / artifact["path"]).resolve()
        if not path.is_relative_to(pin_dir) or digest(path.read_bytes()) != artifact["sha256"]:
            raise ValueError("genuine_pin_artifact_digest_mismatch")
    snapshot = json.loads((pin_dir / "style-snapshot.json").read_text(encoding="utf-8"))
    context = json.loads((pin_dir / "production-capture-context.json").read_text(encoding="utf-8"))
    requested = json.loads(args.requested_inputs.read_text(encoding="utf-8"))
    if snapshot["digest"] != pin_provenance["snapshotDigest"]:
        raise ValueError("genuine_pin_snapshot_identity_mismatch")
    if [case["id"] for case in requested["cases"]] != pin_provenance["caseOrder"]:
        raise ValueError("genuine_pin_case_order_mismatch")
    if requested["caseCount"] != 9 or requested["common_header_mode"] != "free":
        raise ValueError("normal_nine_requested_context_invalid")
    sys.path.insert(0, str(ROOT / "servers/fastapi"))
    from services.svg_contract import finalize_svg
    from services.svg_master_furniture import master_furniture_receipt
    from services.svg_renderer import svg_preview_html
    from services.generation_core.drivers.svg.native_declarations import (
        PAYLOAD_KEY, bind_native_declarations, bound_declarations,
    )
    from services.generation_core.drivers.svg.native_chart_evidence import build_chart_witness_manifest
    from services.generation_core.drivers.svg.native_requirements import REQUIREMENTS_VERSION_V5, requirements_revision

    if requirements_revision(snapshot) != REQUIREMENTS_VERSION_V5:
        raise ValueError("genuine_pin_is_not_v5")
    body_font = snapshot["tokens"]["body_font"]
    if body_font != context["tokens"]["body_font"]:
        raise ValueError("frozen_font_context_mismatch")
    floor = max(context["normalGenerationFontFloorPx"], context["readabilityQualityTargetPx"])
    # New fixture authorship, before finalization/capture; no old capture is modified.
    authored_size = max(16, math.ceil(floor))
    source_fixture = OWNER / "servers/fastapi/tests/unit/test_svg_native_chart_retention_v5.py"
    spec = importlib.util.spec_from_file_location("frozen_chart_source_helpers", source_fixture)
    helpers = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(helpers)
    static_path = OWNER / "docs/session-results/svg-chart-support-graph-examples-20261108.json"
    static = json.loads(static_path.read_text(encoding="utf-8"))
    static_cases = {case["name"]: case for case in static["examples"]}
    factories = {
        "column": lambda: helpers._page(),
        "coincident-line": lambda: helpers._cartesian_page("line", [[10, 20, 30], [10, 20, 30]]),
        "stacked-column": lambda: helpers._stack_labels_page(same_color=True),
        "donut": lambda: helpers._polar_page("donut"),
        "marker-line": lambda: helpers._marker_page(),
        "horizontal-bar": lambda: helpers._page(horizontal=True),
        "signed-zero-multiseries-area": lambda: helpers._cartesian_page("area", [[-10, 0, 20], [10, -5, 30]]),
    }
    ET.register_namespace("", "http://www.w3.org/2000/svg")
    records = []
    try:
        for index, requested_case in enumerate(requested["cases"]):
            identifier = requested_case["id"]
            source_truth = requested_case["requestedSource"]
            if digest(canonical({key: source_truth[key] for key in ("content", "payload")})) != source_truth["sourceDigest"]:
                raise ValueError("requested_truth_digest_mismatch")
            if identifier in factories:
                original = factories[identifier]()
                structural_svg = original["svg_content"]
                declarations = original[PAYLOAD_KEY]
                structural_origin = str(source_fixture)
            else:
                original = static_cases[identifier]
                structural_svg = original["canonicalSvg"]
                declarations = original["nativeDeclarations"]
                structural_origin = str(static_path)
            tree = ET.fromstring(structural_svg)
            tree.set("width", str(context["widthPx"]))
            tree.set("height", str(context["heightPx"]))
            for node in tree.iter():
                tag = node.tag.rsplit("}", 1)[-1]
                if tag in {"text", "tspan"}:
                    node.set("font-family", body_font)
                    size = node.get("font-size")
                    if size is not None and float(size) < authored_size:
                        node.set("font-size", str(authored_size))
                    elif size is None:
                        node.set("font-size", str(authored_size))
                else:
                    node.attrib.pop("font-family", None)
            if identifier == "donut":
                # This ratio is independently requested, distinct from hidden value labels.
                note = ET.SubElement(tree, "{http://www.w3.org/2000/svg}text", {
                    "x": "800", "y": "630", "font-family": body_font,
                    "font-size": str(authored_size), "fill": "#111111",
                })
                note.text = "Hole ratio 0.7"
            authored = ET.tostring(tree, encoding="unicode", short_empty_elements=True)
            final = finalize_svg(authored, index=index, n=9, width=context["widthPx"],
                                 height=context["heightPx"], style_snapshot=snapshot)
            # Actual canonical admission precedes any declaration or witness authority.
            svg_preview_html(final, width=context["widthPx"], height=context["heightPx"])
            payload = {"svg_content": final, "readability_policy": snapshot["readability_policy"]}
            bind_native_declarations(payload, final, {"objects": copy.deepcopy(declarations["objects"]),
                                                     "dropped": copy.deepcopy(declarations["dropped"])})
            bound, current = bound_declarations(payload, final)
            if not current:
                raise ValueError("new_final_source_declaration_not_bound")
            furniture = master_furniture_receipt(final, snapshot, width=context["widthPx"],
                                                 height=context["heightPx"], index=index, n=9)
            witness = build_chart_witness_manifest(payload, final, revision=REQUIREMENTS_VERSION_V5)
            case_dir = output / identifier
            case_dir.mkdir(exist_ok=True)
            (case_dir / "canonical.svg").write_text(final, encoding="utf-8")
            record = {"id": identifier, "index": index, "requestedSource": source_truth,
                      "canonicalSvg": final, "nativeDeclarations": bound, "manifest": witness,
                      "systemFurniture": furniture, "readabilityPolicy": snapshot["readability_policy"],
                      "authoringProvenance": {"structuralFixture": structural_origin,
                          "structuralSvgDigest": digest(structural_svg.encode("utf-8")),
                          "newFrozenFontFamily": body_font, "newMinimumAuthoredTextPx": authored_size,
                          "finalizedBeforeDeclarationAndWitness": True, "generationHttpMeasured": False}}
            write_json(case_dir / "case.json", record)
            records.append(record)
            if witness is None:
                raise ValueError("final_static_builder_did_not_admit_" + identifier)
        result = {"schema": "normal-profile-nine-chart-fixtures/v1", "caseCount": 9,
                  "pinDirectory": str(pin_dir), "snapshotDigest": snapshot["digest"],
                  "requestedInputsDigest": digest(args.requested_inputs.read_bytes()),
                  "staticHelperDigest": digest(source_fixture.read_bytes()),
                  "ownerStaticGraphDigest": digest(static_path.read_bytes()),
                  "publicationAccepted": False, "capturesExecuted": False, "cases": records}
        write_json(output / "fixtures.json", result)
        write_json(output / "preparation-receipt.json", {"status": "prepared", "cases": len(records),
                   "fixtureDigest": digest((output / "fixtures.json").read_bytes()), "capturesExecuted": False})
        print(json.dumps({"status": "prepared", "cases": len(records),
                          "fixtureDigest": digest((output / "fixtures.json").read_bytes())}))
        return 0
    except Exception as error:
        write_json(output / "preparation-failure.json", {"status": "failed", "preparedCases": len(records),
                   "errorClass": type(error).__name__, "reason": str(error), "capturesExecuted": False})
        print(json.dumps({"status": "failed", "preparedCases": len(records), "reason": str(error)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
