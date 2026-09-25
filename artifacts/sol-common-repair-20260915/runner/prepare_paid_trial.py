from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]
PLAN = ROOT / "paid-trial-plan.json"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare() -> dict[str, Any]:
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    driver_path = ROOT / "runner/paid_trial.py"
    compile(driver_path.read_text(encoding="utf-8"), str(driver_path), "exec")
    sample_path = REPO / plan["sampleSource"]
    sample_document = json.loads(sample_path.read_text(encoding="utf-8"))
    samples = sample_document.get("samples") or sample_document.get("slides") or []
    if len(samples) != plan["sampleCount"]:
        raise ValueError("frozen sample count does not match paid trial plan")
    prompt_inputs: list[dict[str, Any]] = []
    for arm in plan["promptArms"]:
        base = REPO / arm["basePrompt"]
        supplement = REPO / arm["supplement"] if arm["supplement"] else None
        prompt_inputs.append(
            {
                "arm": arm["id"],
                "baseSha256": _sha(base),
                "supplementSha256": _sha(supplement) if supplement else None,
                "effectivePromptAssembly": "base verbatim" if supplement is None else "base + newline + supplement",
            }
        )
    if plan["authorCallsScheduled"] != plan["sampleCount"] * len(plan["promptArms"]):
        raise ValueError("author call accounting mismatch")
    if plan["outputComparisonsScheduled"] != plan["authorCallsScheduled"] * 2:
        raise ValueError("shared off/on output comparison accounting mismatch")
    receipt = {
        "schemaVersion": "sol-common-repair-paid-trial-preflight/v1",
        "status": "ready_for_gates_not_execution",
        "providerCalls": 0,
        "sampleSha256": _sha(sample_path),
        "driverSha256": _sha(driver_path),
        "promptInputs": prompt_inputs,
        "accounting": {
            "authorCallCap": plan["authorCallCap"],
            "authorCallsScheduled": plan["authorCallsScheduled"],
            "outputComparisonsScheduled": plan["outputComparisonsScheduled"],
        },
        "reuse": {
            "runnerModule": "artifacts/sol-speed-quality-20260915/runner/experiment.py",
            "authorEntry": "author_one",
            "modelPlanCalls": 0,
            "modelRepairCalls": 0,
        },
        "executionGates": plan["executionGates"],
    }
    output = ROOT / "paid-trial-preflight.json"
    output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return receipt


if __name__ == "__main__":
    prepare()
