"""Canonical text-free evidence payloads for SlideSpec review replay."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from services.slidespec_acceptance import TargetIssueDelta
from services.slidespec_repair_pipeline import DomDefect, DomEvidence

def _target_evidence_payload(
    deltas: Sequence[TargetIssueDelta] | None,
) -> list[dict[str, Any]]:
    """Return the canonical, text-free evidence shape consumed by replay."""

    return [
        {
            "stage": delta.identity.stage,
            "code": delta.identity.code,
            "semantic_id": delta.identity.semantic_id,
            "qid": delta.identity.qid,
            "constraint_id": delta.identity.constraint_id,
            "before": delta.before,
            "after": delta.after,
            "resolution_threshold": delta.resolution_threshold,
            "tolerance": delta.tolerance,
            "direction": delta.direction,
        }
        for delta in (deltas or ())
    ]


def _confirmed_issue_payload(evidence: DomEvidence) -> list[dict[str, Any]]:
    """Project physical user issues while retaining every raw defect."""

    grouped: dict[str, list[DomDefect]] = {}
    for defect in evidence.defects:
        if defect.code == "constraint.evidence.missing" or defect.severity not in {
            "high",
            "medium",
        }:
            continue
        identity = defect.physical_issue_id or ":".join(
            (defect.code, defect.semantic_id, defect.qid or "unavailable")
        )
        grouped.setdefault(identity, []).append(defect)
    result: list[dict[str, Any]] = []
    for identity, raw_defects in grouped.items():
        representative = min(
            raw_defects,
            key=lambda item: (
                0 if item.code.startswith("dom.") else 1,
                item.code,
                item.semantic_id,
                item.qid or "",
            ),
        )
        result.append(
            {
                "code": representative.code,
                "severity": representative.severity,
                "semantic_id": representative.semantic_id,
                "qid": representative.qid,
                "related_semantic_ids": list(
                    representative.related_semantic_ids
                ),
                "physical_issue_id": identity,
                "diagnostics": dict(representative.diagnostics),
                "raw_defects": [
                    {
                        "code": defect.code,
                        "severity": defect.severity,
                        "semantic_id": defect.semantic_id,
                        "qid": defect.qid,
                        "related_semantic_ids": list(
                            defect.related_semantic_ids
                        ),
                        "diagnostics": dict(defect.diagnostics),
                    }
                    for defect in raw_defects
                ],
            }
        )
    return result


def _evidence_atom_payload(evidence: DomEvidence) -> list[dict[str, Any]]:
    """Expose actionable atom facts without flooding the review with clean probes."""

    return [
        {
            "sensor": atom.sensor,
            "metric": atom.metric,
            "stage": atom.stage,
            "state": atom.state,
            "qid": atom.qid,
            "semantic_id": atom.semantic_id,
            "constraint_id": atom.constraint_id,
            "reason": atom.reason_code,
            "reason_code": atom.reason_code,
            "dependency_refs": list(atom.dependency_refs),
            "diagnostics": dict(atom.diagnostics),
        }
        for atom in evidence.atoms
        if atom.state in {"proven_issue", "unknown"}
    ]


def _incomplete_measurement_payload(evidence: DomEvidence) -> list[dict[str, Any]]:
    """Project one row per missing physical measurement, retaining consumers."""

    result: list[dict[str, Any]] = []
    for (scope, metric), atoms in evidence.incomplete_measurement_groups:
        result.append(
            {
                "scope": scope,
                "metric": metric,
                "state": "unknown",
                "sensors": sorted({atom.sensor for atom in atoms}),
                "stages": sorted({atom.stage for atom in atoms}),
                "qids": sorted({atom.qid for atom in atoms if atom.qid}),
                "semantic_ids": sorted(
                    {atom.semantic_id for atom in atoms if atom.semantic_id}
                ),
                "constraint_ids": sorted(
                    {atom.constraint_id for atom in atoms if atom.constraint_id}
                ),
                "reason_codes": sorted(
                    {atom.reason_code for atom in atoms if atom.reason_code}
                ),
                "dependency_refs": sorted(
                    {
                        dependency
                        for atom in atoms
                        for dependency in atom.dependency_refs
                    }
                ),
                "evidence_atom_count": len(atoms),
            }
        )
    return result


def _dependency_blocker_payload(
    outcomes: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Lift scoped fail-closed blockers out of candidate diagnostics for the UI."""

    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for outcome in outcomes:
        diagnostics = outcome.get("diagnostics")
        if not isinstance(diagnostics, Mapping):
            continue
        telemetry = diagnostics.get("dom_telemetry")
        if not isinstance(telemetry, Mapping):
            continue
        blockers = telemetry.get("dependency_blockers")
        if not isinstance(blockers, Sequence) or isinstance(blockers, (str, bytes)):
            continue
        for blocker in blockers:
            if not isinstance(blocker, Mapping):
                continue
            reason = blocker.get("reason_code") or "target_dependency_unknown"
            payload = {
                "sensor": blocker.get("sensor"),
                "metric": blocker.get("metric"),
                "stage": blocker.get("stage"),
                "qid": blocker.get("qid"),
                "semantic_id": blocker.get("semantic_id"),
                "constraint_id": blocker.get("constraint_id"),
                "reason": reason,
                "reason_code": reason,
                "dependency_refs": list(blocker.get("dependency_refs") or ()),
                "diagnostics": dict(blocker.get("diagnostics") or {}),
            }
            identity = json.dumps(payload, sort_keys=True, default=str)
            if identity not in seen:
                seen.add(identity)
                result.append(payload)
    return result
