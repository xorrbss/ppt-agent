"""Evaluate authored-only canary evidence without mutating deployment state."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

FASTAPI_ROOT = Path(__file__).resolve().parents[1]
if str(FASTAPI_ROOT) not in sys.path:
    sys.path.insert(0, str(FASTAPI_ROOT))

from services.compatibility_migration.rollout import (  # noqa: E402
    AvailabilitySample,
    CanarySLO,
    CanaryStage,
    CanaryWindow,
    evaluate_canary,
)


class AvailabilityEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    attempts: int = Field(ge=0)
    successes: int = Field(ge=0)
    p95_latency_ms: float = Field(ge=0)


class CanaryEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: int = Field(default=1, ge=1, le=1)
    run_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    release_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    stage: Literal["internal", "1", "5", "25", "50", "100"]
    read: AvailabilityEvidence
    export: AvailabilityEvidence
    owner_isolation_violations: int = Field(default=0, ge=0)
    source_or_archive_digest_mismatches: int = Field(default=0, ge=0)
    original_immutability_violations: int = Field(default=0, ge=0)
    legacy_admission_violations: int = Field(default=0, ge=0)
    rollback_probe_passed: bool = True


def evaluate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    evidence = CanaryEvidence.model_validate(payload)
    window = CanaryWindow(
        read=AvailabilitySample(**evidence.read.model_dump()),
        export=AvailabilitySample(**evidence.export.model_dump()),
        owner_isolation_violations=evidence.owner_isolation_violations,
        source_or_archive_digest_mismatches=(
            evidence.source_or_archive_digest_mismatches
        ),
        original_immutability_violations=evidence.original_immutability_violations,
        legacy_admission_violations=evidence.legacy_admission_violations,
        rollback_probe_passed=evidence.rollback_probe_passed,
    )
    decision = evaluate_canary(
        stage=CanaryStage(evidence.stage), window=window, slo=CanarySLO()
    )
    canonical_input = json.dumps(
        evidence.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "schema_version": 1,
        "read_only": True,
        "run_id": evidence.run_id,
        "release_id": evidence.release_id,
        "evidence_sha256": sha256(canonical_input).hexdigest(),
        "decision": {
            "action": decision.action,
            "current_stage": decision.current_stage.value,
            "recommended_stage": decision.recommended_stage.value,
            "may_expand": decision.may_expand,
            "reason_codes": list(decision.reason_codes),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only authored-only canary evidence evaluator"
    )
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    result = evaluate_payload(json.loads(args.input.read_text(encoding="utf-8")))
    rendered = json.dumps(
        result,
        ensure_ascii=False,
        indent=2 if args.pretty else None,
        sort_keys=True,
    ) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0 if result["decision"]["may_expand"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
