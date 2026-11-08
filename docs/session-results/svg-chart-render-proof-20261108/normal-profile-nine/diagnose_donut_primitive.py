"""Separate eight-plane observation; never returns or upgrades a paint proof."""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
from pathlib import Path
import re
import sys
import threading
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "servers/fastapi"))
MODES = ("alpha", "owned-alpha", "reference-original", "reference-white", "reference-black",
         "current-white", "current-black", "current-absent")


def digest(data):
    return hashlib.sha256(data).hexdigest()


class DiagnosticComplete(Exception):
    pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--case-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    from PIL import Image
    from services.svg_renderer import materialize_trusted_svg_fonts
    from utils import slide_capture, slide_capture_chart_paint as paint
    from utils.renderer_isolation import admit_authored_html, admit_static_output_html, RendererPolicyError
    from utils.slide_capture_chart_adapter import render_admitted_html_over_pipe
    from utils.slide_capture_pipe import _kill_process_tree

    case = args.case_dir.resolve()
    aggregate_path, helper_path = case.parent / "capture-manifest.json", Path(__file__).resolve()
    aggregate_bytes, helper_bytes = aggregate_path.read_bytes(), helper_path.read_bytes()
    aggregate = json.loads(aggregate_bytes)
    row = next(r for r in aggregate["records"] if r["id"] == "donut")
    assert row["status"] == "failed" and row["reason"] == "producer_paint_unproven"
    inputs = {name: (case / name).read_bytes() for name in (
        "actual-svg-render-result.json", "wrapper.html", "canonical.svg", "witness-manifest.json",
        "paint-proof.json", "case-fixture.json", "requested-source.json", "final.png")}
    for name, data in inputs.items():
        assert digest(data) == row["artifacts"][name]["sha256"]
    code = aggregate["codeDigests"]
    def stable():
        assert aggregate_path.read_bytes() == aggregate_bytes and helper_path.read_bytes() == helper_bytes
        assert all(digest((ROOT / path).read_bytes()) == sha for path, sha in code.items())
        assert all((case / name).read_bytes() == data for name, data in inputs.items())
    stable()
    wire = json.loads(inputs["actual-svg-render-result.json"])
    source = wire["wrapper_html"]
    assert source.encode("utf-8") == inputs["wrapper.html"]
    manifest = json.loads(inputs["witness-manifest.json"])
    old_proof = json.loads(inputs["paint-proof.json"])
    try:
        admitted = admit_authored_html(source)
    except RendererPolicyError:
        admitted = admit_static_output_html(source)
    admitted = materialize_trusted_svg_fonts(source, admitted)
    fonts = {"sha256:" + digest(base64.b64decode(data, validate=True))
             for mime, data in re.findall(r"data:([^;,\s]+);base64,([A-Za-z0-9+/=]+)", admitted.html)
             if mime.startswith("font/")}
    assert fonts == {r["digest"] for r in old_proof["materialIdentities"]["embeddedResources"]
                     if r["mimeType"].startswith("font/")}
    targets = [t for t in paint._targets(manifest)
               if t["id"] == "native-chart-sales/primitive/svg-4/native-chart-sales/s0/c0"]
    assert len(targets) == 1 and targets[0]["qid"] == "svg-4"
    target = targets[0]
    old_target = next(t for t in old_proof["targets"] if t["id"] == target["id"])
    assert all(old_target[k] == value for k, value in target.items())
    assert not manifest["charts"][0]["compositionGroup"].get("equivalenceClasses")
    assert not any(a.get("kind") == "shared-stack-interface" for d in manifest["charts"][0]["datums"]
                   for o in d["geometrySupport"]["visibilityObligations"] for a in o.get("alternatives", []))
    args.output.mkdir(parents=True, exist_ok=False)
    original = Image.open(io.BytesIO(inputs["final.png"])).convert("RGBA")
    shots, observations, transports, final_primary_shots = [], [], [], []
    primary_command = None
    expired = threading.Event()
    def stop_owned():
        expired.set()
        for transport in transports:
            _kill_process_tree(transport.process)
    timer = threading.Timer(80, stop_owned)
    timer.daemon = True
    def factory(*factory_args):
        if expired.is_set():
            raise TimeoutError("diagnostic_deadline")
        transport = slide_capture._ChromePipe(*factory_args)
        transports.append(transport)
        if expired.is_set():
            _kill_process_tree(transport.process)
            raise TimeoutError("diagnostic_deadline")
        return transport
    real_shot, real_collect = paint._shot, paint.collect_chart_paint_proof
    def shot(command):
        if command is primary_command:
            assert len(shots) == 16 and len(observations) == 8 and not final_primary_shots and not expired.is_set()
            image = real_shot(command)
            assert image.mode == "RGBA" and image.size == original.size and image.tobytes() == original.tobytes()
            final_primary_shots.append(image.copy())
            return image
        assert len(shots) < 16 and not expired.is_set()
        image = real_shot(command)
        if len(shots) % 2 == 0:
            assert image.mode == "RGBA" and image.size == original.size and image.tobytes() == original.tobytes()
        shots.append(image.copy())
        return image
    def collect(**kwargs):
        nonlocal primary_command
        primary_command = kwargs["command"]
        assert kwargs["final_png"] == inputs["final.png"]
        delegate = kwargs["diagnostic_capture"]
        def plane(callback):
            assert len(observations) < 8 and not expired.is_set()
            result = delegate(callback)
            observations.append(result[1])
            return result
        kwargs["diagnostic_capture"] = plane
        real_collect(**kwargs)  # Its deliberately incomplete proof is discarded.
        assert len(shots) == 16 and len(observations) == 8 and len(final_primary_shots) == 1
        raise DiagnosticComplete()
    timer.start()
    try:
        with patch.object(paint, "_targets", return_value=targets), patch.object(paint, "_shot", side_effect=shot), patch.object(paint, "collect_chart_paint_proof", side_effect=collect):
            try:
                render_admitted_html_over_pipe(admitted, 60, chart_witness_manifest=manifest,
                    width_px=1280, height_px=720, blocked_urls=slide_capture._BLOCKED_RENDERER_URL_PATTERNS,
                    chrome_pipe=factory, find_chrome=slide_capture.find_chrome,
                    instrument_html=slide_capture._instrument_authored_html, runtime_value=slide_capture._runtime_value)
            except DiagnosticComplete:
                pass
            else:
                raise AssertionError("diagnostic_must_not_return_capture_or_proof")
    finally:
        timer.cancel()
        timer.join()
        for transport in transports:
            if transport.process.poll() is None:
                _kill_process_tree(transport.process)
    assert not expired.is_set()
    stable()
    artifacts = {}
    for i, image in enumerate(shots):
        name = f"{i // 2:02d}-{MODES[i // 2]}-{'all-on' if i % 2 == 0 else 'mutated'}.png"
        path = args.output / name
        image.save(path, format="PNG")
        artifacts[name] = digest(path.read_bytes())
    path = args.output / "immutable-primary-final.png"
    final_primary_shots[0].save(path, format="PNG")
    artifacts[path.name] = digest(path.read_bytes())
    receipt = {"schema": "separate-donut-primitive-diagnostic/v1", "acceptanceAttempt": False,
        "originalProofModified": False, "filteredProofDiscarded": True, "paintPlanes": 8,
        "screenshots": 18, "planeScreenshots": 16, "initialPrimaryScreenshots": 1,
        "finalPrimaryScreenshots": 1, "savedScreenshots": 17,
        "allOnMatchesOriginalRgba": True, "primaryPngMatchesOriginalBytes": True,
        "cdpPngEncodingNote": "All-on CDP screenshots are decoded RGBA; the frozen adapter emits a re-encoded RGB final PNG.",
        "target": target, "inputDigests": {name: digest(data) for name, data in inputs.items()},
        "captureManifestSha256": digest(aggregate_bytes), "diagnosticHelperSha256": digest(helper_bytes),
        "codeDigests": code, "fontMaterialDigests": sorted(fonts),
        "originalAdmittedRenderDigest": old_proof["admittedRenderDigest"],
        "diagnosticAdmittedRenderDigest": "sha256:" + digest(admitted.html.encode("utf-8")),
        "observations": [{k: observation[k] for k in ("bbox", "canvasMatrix", "foreignOccluderQids")}
                         for observation in observations], "artifacts": artifacts}
    (args.output / "diagnostic-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "diagnostic-only-complete", "paintPlanes": 8, "planeScreenshots": 16,
                      "primaryScreenshots": 2, "savedScreenshots": 17}))


if __name__ == "__main__":
    main()
