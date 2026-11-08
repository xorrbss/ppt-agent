"""One authorized font-only CDP observation; no screenshot or paint probe."""
from __future__ import annotations
import base64
import hashlib
import io
import json
from pathlib import Path
import re
import sys
import time
import unicodedata

ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "servers/fastapi"))


def sha(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


def main():
    from fontTools.ttLib import TTFont
    from services.svg_renderer import materialize_trusted_svg_fonts
    from utils.renderer_isolation import admit_authored_html, admit_static_output_html, RendererPolicyError
    from utils import slide_capture
    from utils.slide_capture_pipe_render import drive_admitted_htmls_over_pipe
    from utils.slide_capture_chart_adapter import _wait_svg_materials

    original = HERE / "acceptance-column-final-pin-attempt-1/column"
    wire = json.loads((original / "actual-svg-render-result.json").read_text(encoding="utf-8"))
    proof = json.loads((original / "paint-proof.json").read_text(encoding="utf-8"))
    case = json.loads((original / "case-fixture.json").read_text(encoding="utf-8"))
    source = wire["wrapper_html"]
    try:
        admitted = admit_authored_html(source)
    except RendererPolicyError:
        admitted = admit_static_output_html(source)
    admitted = materialize_trusted_svg_fonts(source, admitted)
    faces = []
    for mime, encoded in re.findall(r"data:([^;,\s]+);base64,([A-Za-z0-9+/=]+)", admitted.html):
        if not mime.startswith("font/"):
            continue
        payload = base64.b64decode(encoded, validate=True)
        font = TTFont(io.BytesIO(payload))
        faces.append({"digest": sha(payload), "postScriptName": font["name"].getDebugName(6),
                      "coverage": set(font.getBestCmap())})
        font.close()
    actual = {r["digest"] for r in proof["materialIdentities"]["embeddedResources"] if r["mimeType"].startswith("font/")}
    if {f["digest"] for f in faces} != actual or actual != {"sha256:69221cf97f8255dbb7572f7c82bd1c363b2740ef4289e77e5503b53e12f50b09"}:
        raise ValueError("actual_material_digest_mismatch")
    targets = [{key: target[key] for key in ("id", "qid", "startUtf16", "endUtf16")}
               for target in proof["targets"] if target["kind"] == "glyph-range"]

    def observe(command, _frame, _admitted, _metrics):
        _wait_svg_materials(command)
        command("DOM.enable", {})
        command("CSS.enable", {})
        document = command("DOM.getDocument", {"depth": 0})["root"]["nodeId"]
        rows = []
        for index, target in enumerate(targets):
            config = json.dumps({"index": index, **target})
            expression = """(()=>{const t=__TARGET__,svg=document.querySelector('svg');
              const nodes=[svg,...svg.querySelectorAll('*')],node=nodes[Number(t.qid.slice(4))];
              if(!node)throw new Error('diagnostic_target_missing');
              node.setAttribute('data-font-diagnostic-index',String(t.index));
              const text=Array.from(node.childNodes).filter(n=>n.nodeType===Node.TEXT_NODE).map(n=>n.nodeValue).join('');
              return Array.from(text.slice(t.startUtf16,t.endUtf16)).map(c=>c.codePointAt(0));})()""".replace("__TARGET__", config)
            result = command("Runtime.evaluate", {"expression": expression, "returnByValue": True})
            if result.get("exceptionDetails"):
                raise ValueError("diagnostic_target_resolution_failed")
            codepoints = result["result"]["value"]
            required = {code for code in codepoints if not chr(code).isspace()
                        and unicodedata.category(chr(code)) != "Cf" and not 0xFE00 <= code <= 0xFE0F
                        and not 0xE0100 <= code <= 0xE01EF}
            node = command("DOM.querySelector", {"nodeId": document,
                "selector": f'[data-font-diagnostic-index="{index}"]'})["nodeId"]
            selected = command("CSS.getPlatformFontsForNode", {"nodeId": node}).get("fonts", [])
            matched = [face for font in selected for face in faces
                       if font.get("isCustomFont") is True and font.get("postScriptName") == face["postScriptName"]]
            coverage = set().union(*(face["coverage"] for face in matched)) if matched else set()
            all_coverage = set().union(*(face["coverage"] for face in faces))
            rows.append({"targetId": target["id"], "qid": target["qid"],
                "requiredCodepoints": sorted(required),
                "selectedFonts": [{key: font.get(key) for key in ("familyName", "postScriptName", "isCustomFont", "glyphCount")} for font in selected],
                "exactBaseNameMatchedResourceCount": len(matched),
                "missingUnderCurrentExactBaseJoin": sorted(required - coverage),
                "missingFromActualMaterialCmap": sorted(required - all_coverage)})
        return rows

    started = time.perf_counter_ns()
    rows = drive_admitted_htmls_over_pipe((admitted,), 60, width_px=1280, height_px=720,
        blocked_urls=slide_capture._BLOCKED_RENDERER_URL_PATTERNS,
        chrome_pipe=slide_capture._ChromePipe, find_chrome=slide_capture.find_chrome,
        instrument_html=slide_capture._instrument_authored_html, capture=observe)[0]
    record = {"schema": "separate-font-only-cdp-diagnosis/v1", "originalCaptureReplayed": False,
        "screenshots": 0, "paintPlanes": 0, "consumerCalls": 0,
        "sourceDigest": sha(case["canonicalSvg"].encode()), "wrapperDigest": sha(source.encode()),
        "originalAdmittedRenderDigest": proof["admittedRenderDigest"],
        "diagnosticAdmittedRenderDigest": sha(admitted.html.encode()),
        "sameAdmittedIdentity": sha(admitted.html.encode()) == proof["admittedRenderDigest"],
        "materialMatchesOriginal": True,
        "embeddedFaces": [{key: face[key] for key in ("digest", "postScriptName")} for face in faces],
        "elapsedSeconds": (time.perf_counter_ns() - started) / 1e9, "targets": rows}
    path = HERE / "font-only-cdp-diagnosis-attempt-1.json"
    if path.exists():
        raise ValueError("new_diagnostic_artifact_required")
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "font-only-observation-complete", "targets": len(rows), "artifactSha256": sha(path.read_bytes())}))


if __name__ == "__main__":
    main()
