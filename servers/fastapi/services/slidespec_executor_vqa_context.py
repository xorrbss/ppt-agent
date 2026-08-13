"""Build paired original/candidate evidence for bounded VQA."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from models.slidespec_v1 import RenderRequest, RepairPlan

def _paired_vqa_context(
    request: RenderRequest,
    plan: RepairPlan,
    *,
    verified_geometry: Mapping[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Describe the bounded repair intent without copying authored text."""

    codes = sorted(
        {
            ref.removeprefix("code:")
            for ref in plan.diagnostic_refs
            if ref.startswith("code:")
        }
    )
    qids = sorted(
        {
            ref.removeprefix("qid:")
            for ref in plan.diagnostic_refs
            if ref.startswith("qid:")
        }
    )
    qid_by_layout = {
        binding.layoutId: binding.qid
        for binding in (request.source.bindings if request.source is not None else ())
    }
    payload: dict[str, Any] = {
        "schema": "slidespec-paired-vqa-context/1",
        "targetDefects": [
            {
                "category": code.removeprefix("dom."),
                "qid": qid,
            }
            for code in codes
            for qid in qids
        ],
        "operations": [
            {
                "operation": operation.op,
                "targetLayoutId": operation.target,
                "targetQid": qid_by_layout.get(operation.target),
            }
            for operation in plan.operations
        ],
        "verifiedGeometry": dict(verified_geometry or {}),
    }
    context = (
        "Evaluate whether the POST-FIX candidate visibly resolves the listed "
        "target defects without regressing protected dimensions. The identifiers "
        "are opaque and contain no authored text. Chrome geometry facts are "
        "authoritative for clipping, canvas containment, and glyph clipping; do "
        "not claim those defects when the supplied facts prove them false. Still "
        "reject real spacing, hierarchy, readability, or style regressions.\n"
        + json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    )
    return context, payload
