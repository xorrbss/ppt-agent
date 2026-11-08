"""Controlled process-local original-foreground assignment observation only."""
import hashlib
import json
from pathlib import Path
import sys
from unittest.mock import patch

import diagnose_donut_primitive as base


def main():
    from utils import slide_capture_chart_paint as paint
    helper = Path(__file__).resolve()
    helper_bytes = helper.read_bytes()
    original_helper = Path(base.__file__).resolve()
    original_bytes = original_helper.read_bytes()
    assert base.digest(original_bytes) == "9c26426c38832cc0801c1e1ba0552a9e445a8e735815c167c9070e85b9873074"
    original_plane = paint._PLANE
    anchor = "  const selected=state.selectedRanges;"
    assert original_plane.count(anchor) == 1
    inserted = r"""
  const diagnosticSnapshot=()=>state.paints.map(([node])=>{
    const css=getComputedStyle(node);
    return {qid:node.getAttribute('data-qid'),fill:css.fill,stroke:css.stroke,
      opacity:css.opacity,fillOpacity:css.fillOpacity,strokeOpacity:css.strokeOpacity};});
  const diagnosticBefore=diagnosticSnapshot();
  const diagnosticApplied=mode==='alpha'||mode==='reference-original';
  if(diagnosticApplied)for(const [node,fill,stroke] of state.paints){
    const css=getComputedStyle(node);
    if(css.fill!==fill||css.stroke!==stroke)throw Error('chart_diagnostic_original_source_paint_changed');
    node.style.setProperty('fill',fill,'important');
    node.style.setProperty('stroke',stroke,'important');
  }
  const diagnosticAfter=diagnosticSnapshot();
  const diagnosticEqual=JSON.stringify(diagnosticBefore)===JSON.stringify(diagnosticAfter);
  state.chartDiagnosticOriginalPaintPinV1={mode,applied:diagnosticApplied,
    before:diagnosticBefore,after:diagnosticAfter,computedStyleEqual:diagnosticEqual};
  if(!diagnosticEqual)throw Error('chart_diagnostic_original_style_changed');
"""
    variant = original_plane.replace(anchor, anchor + inserted)
    real_evaluate = paint._evaluate
    styles = []
    def evaluate(command, script):
        result = real_evaluate(command, script)
        if "const diagnosticSnapshot=" in script:
            evidence = real_evaluate(command, "window.__chartPaint.chartDiagnosticOriginalPaintPinV1")
            assert evidence["computedStyleEqual"] is True
            styles.append(evidence)
        return result
    with patch.object(paint, "_PLANE", variant), patch.object(paint, "_evaluate", side_effect=evaluate):
        base.main()
    assert helper.read_bytes() == helper_bytes and original_helper.read_bytes() == original_bytes
    assert len(styles) == 8 and [s["mode"] for s in styles] == list(base.MODES)
    assert [s["mode"] for s in styles if s["applied"]] == ["alpha", "reference-original"]
    output = Path(sys.argv[sys.argv.index("--output") + 1])
    receipt = output / "diagnostic-receipt.json"
    record = {"schema": "controlled-original-foreground-diagnostic-variant/v1",
        "acceptanceAttempt": False, "productionFilesModified": False,
        "baseHelperSha256": base.digest(original_bytes), "variantHelperSha256": base.digest(helper_bytes),
        "originalPlaneScriptSha256": base.digest(original_plane.encode()),
        "variantPlaneScriptSha256": base.digest(variant.encode()),
        "baseDiagnosticReceiptSha256": base.digest(receipt.read_bytes()), "computedStyleEvidence": styles}
    path = output / "original-foreground-variant.json"
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(record, indent=2) + "\n")


if __name__ == "__main__":
    main()
