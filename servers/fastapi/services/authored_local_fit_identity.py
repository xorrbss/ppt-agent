"""Immutable run and group identity checks for authored local-fit evidence."""
from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any


class LocalFitIdentityError(ValueError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def _fail(reason: str) -> None:
    raise LocalFitIdentityError(reason)


def _identity_number(value: Any) -> float:
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError
    return result


def _run_identities(value: Any, *, reason: str) -> dict[str, tuple[Any, ...]]:
    if not isinstance(value, (list, tuple)) or not value:
        _fail(reason)
    result: dict[str, tuple[Any, ...]] = {}
    for run in value:
        if not isinstance(run, Mapping):
            _fail(reason)
        run_id = run.get("run_id", run.get("id"))
        ordinal = run.get("text_node_ordinal", run.get("ordinal"))
        ancestors = run.get("ancestor_qids")
        string_fields = tuple(
            run.get(field) for field in (
                "owner_qid", "content_digest", "group_id", "role",
            )
        )
        offsets = (ordinal, run.get("start_offset"), run.get("end_offset"))
        if (
            not isinstance(run_id, str) or not run_id or run_id in result
            or any(not isinstance(item, str) or not item for item in string_fields)
            or any(not isinstance(item, int) or isinstance(item, bool) for item in offsets)
            or not isinstance(ancestors, (list, tuple))
            or any(not isinstance(item, str) or not item for item in ancestors)
            or len(set(ancestors)) != len(ancestors)
            or isinstance(run.get("original_pt"), bool)
        ):
            _fail(reason)
        try:
            identity = (
                run["owner_qid"], ordinal, run["start_offset"], run["end_offset"],
                run["content_digest"], run["group_id"], run["role"],
                tuple(ancestors), _identity_number(run["original_pt"]),
            )
        except (KeyError, TypeError, ValueError):
            _fail(reason)
        if identity[2] < 0 or identity[3] <= identity[2]:
            _fail(reason)
        result[run_id] = identity
    return result


def _group_identities(
    value: Any, runs: Mapping[str, tuple[Any, ...]], *, reason: str
) -> dict[str, tuple[Any, ...]]:
    if not isinstance(value, (list, tuple)) or not value:
        _fail(reason)
    result: dict[str, tuple[Any, ...]] = {}
    for group in value:
        if not isinstance(group, Mapping):
            _fail(reason)
        group_id, run_ids = group.get("group_id"), group.get("run_ids")
        role, card_qid = group.get("role"), group.get("card_qid")
        if (
            not isinstance(group_id, str) or not group_id or group_id in result
            or not isinstance(role, str) or not role
            or card_qid is not None and not isinstance(card_qid, str)
            or not isinstance(run_ids, (list, tuple))
            or any(not isinstance(run_id, str) or not run_id for run_id in run_ids)
            or isinstance(group.get("original_pt"), bool)
        ):
            _fail(reason)
        try:
            normalized_runs = tuple(sorted(run_ids))
            identity = (
                normalized_runs, role, card_qid,
                _identity_number(group["original_pt"]),
            )
        except (KeyError, TypeError, ValueError):
            _fail(reason)
        expected = tuple(sorted(
            run_id for run_id, run in runs.items() if run[5] == group_id
        ))
        if (
            not normalized_runs or len(set(normalized_runs)) != len(normalized_runs)
            or normalized_runs != expected
        ):
            _fail(reason)
        result[group_id] = identity
    if set(result) != {run[5] for run in runs.values()}:
        _fail(reason)
    return result


def validate_immutable_identity(
    baseline: Mapping[str, Any], report: Mapping[str, Any], *, reason: str
) -> None:
    originals = _run_identities(baseline.get("runs"), reason="baseline_unavailable")
    original_groups = _group_identities(
        baseline.get("groups"), originals, reason="baseline_unavailable",
    )
    observed = _run_identities(report.get("runs"), reason=reason)
    observed_groups = _group_identities(report.get("groups"), observed, reason=reason)
    if originals != observed or original_groups != observed_groups:
        _fail(reason)
