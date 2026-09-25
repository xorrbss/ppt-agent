"""Extended-shrink-basis evidence for the authored local-fit policy.

Extracted from ``authored_local_fit_evidence``. When a committed candidate
shrinks a font past the policy's preferred (non-extended) limit, the fitter
must have recorded which sibling candidates it compared against and why they
lost, so the extended shrink can be granted only on visible, checkable
evidence. ``_extended_shrink_basis`` builds that evidence from a fit report;
``extended_shrink_basis_valid`` re-derives the same shape from a persisted
receipt to check it was not tampered with or dropped.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from services.authored_local_fit_evidence_types import AuthoredLocalFitRejected
from services.authored_local_fit_policy import font_policy, policy_floor_pt


UNCERTAIN_COMPARISON_REJECTIONS = frozenset(
    {
        "reject.budget_exhausted",
        "reject.font_extended_comparison_incomplete",
        "reject.font_unverifiable",
    }
)


def _extended_shrink_basis(
    report: Mapping[str, Any],
    runs: list[Mapping[str, Any]],
    *,
    policy_revision: str,
) -> list[dict[str, Any]]:
    preferred = float(font_policy(policy_revision).max_shrink)
    extended = any(
        float(run["final_pt"]) < float(run["original_pt"]) * (1 - preferred) - 1e-9
        for run in runs
    )
    if not extended:
        return []
    if policy_revision != "authored-local-fit/v1.5":
        raise AuthoredLocalFitRejected("shrink_limit")
    candidates = report.get("candidates")
    candidate_records = [
        item for item in candidates if isinstance(item, Mapping)
    ] if isinstance(candidates, list) else []
    ids = [str(item.get("candidate_id") or "") for item in candidate_records]
    if not ids or any(not value for value in ids) or len(ids) != len(set(ids)):
        raise AuthoredLocalFitRejected("extended_comparison_incomplete")
    by_id = dict(zip(ids, candidate_records, strict=True))
    committed = [
        item
        for item in candidate_records
        if item.get("outcome") == "committed"
        and item.get("extended_shrink") is True
    ]
    result: list[dict[str, Any]] = []
    for item in committed:
        basis = item.get("extended_shrink_basis")
        compared = basis.get("compared_candidate_ids") if isinstance(basis, Mapping) else None
        rejected = basis.get("rejected_candidate_ids") if isinstance(basis, Mapping) else None
        uncertain = basis.get("uncertain_candidate_ids") if isinstance(basis, Mapping) else None
        if (
            not isinstance(basis, Mapping)
            or basis.get("comparison_complete") is not True
            or basis.get("comparison_uncertain") is not False
            or not isinstance(compared, list)
            or not compared
            or not all(isinstance(value, str) and value for value in compared)
            or not isinstance(rejected, list)
            or not all(isinstance(value, str) and value for value in rejected)
            or not isinstance(uncertain, list)
            or uncertain
        ):
            raise AuthoredLocalFitRejected("extended_comparison_incomplete")
        linked_ids = [*compared, *rejected]
        if len(linked_ids) != len(set(linked_ids)) or any(
            candidate_id not in by_id for candidate_id in linked_ids
        ):
            raise AuthoredLocalFitRejected("extended_comparison_incomplete")
        root_qid = item.get("root_qid")
        selected_cost = item.get("cost")
        try:
            selected_total = float(selected_cost["total"])
        except (KeyError, TypeError, ValueError):
            raise AuthoredLocalFitRejected("extended_comparison_incomplete") from None
        if not math.isfinite(selected_total):
            raise AuthoredLocalFitRejected("extended_comparison_incomplete")
        related = [
            record
            for record in candidate_records
            if record.get("root_qid") == root_qid
            and record.get("extended_shrink") is not True
        ]
        passed_ids: set[str] = set()
        rejected_ids: set[str] = set()
        comparison_records: list[dict[str, Any]] = []
        for record in related:
            candidate_id = str(record.get("candidate_id"))
            outcome = str(record.get("outcome") or "")
            if outcome in {"passed", "committed"}:
                cost = record.get("cost")
                try:
                    total = float(cost["total"])
                except (KeyError, TypeError, ValueError):
                    raise AuthoredLocalFitRejected("extended_comparison_incomplete") from None
                if not math.isfinite(total) or total <= selected_total + 1e-10:
                    raise AuthoredLocalFitRejected("extended_comparison_incomplete")
                passed_ids.add(candidate_id)
                comparison_records.append(
                    {"candidateId": candidate_id, "outcome": outcome, "cost": total}
                )
                continue
            rejection = record.get("rejection")
            rejection_code = (
                str(rejection.get("code") or "")
                if isinstance(rejection, Mapping)
                else ""
            )
            if (
                not outcome.startswith(("rejected:", "removed:", "pruned:"))
                or not rejection_code.startswith("reject.")
                or rejection_code in UNCERTAIN_COMPARISON_REJECTIONS
            ):
                raise AuthoredLocalFitRejected("extended_comparison_incomplete")
            rejected_ids.add(candidate_id)
            comparison_records.append(
                {
                    "candidateId": candidate_id,
                    "outcome": outcome,
                    "rejectionCode": rejection_code,
                }
            )
        if set(compared) != passed_ids or set(rejected) != rejected_ids:
            raise AuthoredLocalFitRejected("extended_comparison_incomplete")
        result.append(
            {
                "candidateId": str(item.get("candidate_id") or ""),
                "comparisonComplete": True,
                "comparisonUncertain": False,
                "comparedCandidateIds": list(dict.fromkeys(compared))[:32],
                "rejectedCandidateIds": list(dict.fromkeys(rejected))[:64],
                "uncertainCandidateIds": [],
                "comparisonRecords": comparison_records[:96],
                "selectedCost": selected_total,
                "preferredMaxShrink": preferred,
                "floorPt": policy_floor_pt(policy_revision),
            }
        )
    if not result or any(not item["candidateId"] for item in result):
        raise AuthoredLocalFitRejected("extended_comparison_incomplete")
    return result


def extended_shrink_basis_valid(
    receipt: Mapping[str, Any], runs: list[Mapping[str, Any]], *, policy_revision: str
) -> bool:
    try:
        preferred = float(font_policy(policy_revision).max_shrink)
        extended = any(
            float(run["final_pt"])
            < float(run["original_pt"]) * (1 - preferred) - 1e-9
            for run in runs
        )
    except (KeyError, TypeError, ValueError):
        return False
    if not extended:
        return receipt.get("extendedShrinkBasis") in (None, [], ())
    basis = receipt.get("extendedShrinkBasis")
    if policy_revision != "authored-local-fit/v1.5" or not isinstance(basis, list) or not basis:
        return False
    for item in basis:
        if (
            not isinstance(item, Mapping)
            or not isinstance(item.get("candidateId"), str)
            or not item.get("candidateId")
            or item.get("comparisonComplete") is not True
            or item.get("comparisonUncertain") is not False
            or item.get("preferredMaxShrink") != preferred
            or item.get("floorPt") != policy_floor_pt(policy_revision)
        ):
            return False
        compared = item.get("comparedCandidateIds")
        rejected = item.get("rejectedCandidateIds")
        uncertain = item.get("uncertainCandidateIds")
        records = item.get("comparisonRecords")
        if (
            not isinstance(compared, list)
            or not compared
            or not isinstance(rejected, list)
            or not isinstance(uncertain, list)
            or uncertain
            or not isinstance(records, list)
        ):
            return False
        linked = [*compared, *rejected]
        if (
            any(not isinstance(value, str) or not value for value in linked)
            or len(linked) != len(set(linked))
        ):
            return False
        record_by_id = {
            str(record.get("candidateId")): record
            for record in records
            if isinstance(record, Mapping) and record.get("candidateId")
        }
        if set(record_by_id) != set(linked) or len(record_by_id) != len(records):
            return False
        try:
            selected_cost = float(item["selectedCost"])
        except (KeyError, TypeError, ValueError):
            return False
        if not math.isfinite(selected_cost):
            return False
        for candidate_id in compared:
            record = record_by_id[candidate_id]
            if record.get("outcome") not in {"passed", "committed"}:
                return False
            try:
                cost = float(record["cost"])
                if not math.isfinite(cost) or cost <= selected_cost + 1e-10:
                    return False
            except (KeyError, TypeError, ValueError):
                return False
        for candidate_id in rejected:
            record = record_by_id[candidate_id]
            if (
                not str(record.get("outcome") or "").startswith(
                    ("rejected:", "removed:", "pruned:")
                )
                or not str(record.get("rejectionCode") or "").startswith("reject.")
                or str(record.get("rejectionCode") or "")
                in UNCERTAIN_COMPARISON_REJECTIONS
            ):
                return False
    return True


__all__ = [
    "UNCERTAIN_COMPARISON_REJECTIONS",
    "_extended_shrink_basis",
    "extended_shrink_basis_valid",
]
