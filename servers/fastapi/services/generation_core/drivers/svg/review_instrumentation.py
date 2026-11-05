"""What the deck's vision review cost and changed, for the deck quality receipt.

The quality-first review (``quality._quality_first_deck_advisory``) and its
redesign rounds (``quality_recovery.recover_deck``) already leave every fact
this needs on the pages' checkpoint payloads:

* page 0's ``quality_recovery_execution`` holds each review round's group
  markers -- one marker per logical review call, with every superseded read
  attempt -- and each round's sealed advisory; a final selected review keeps
  the same shape under ``final_selected_review``;
* each page's ``quality_recovery`` marker says whether a redesign was bought
  (``candidate_ready``/``selected``/``retained``), adopted (``selected``) or
  not attempted for lack of budget (``unresolved``).

So the receipt is derived, never a second ledger, and a resumed attempt reads
the calls an earlier attempt bought. Cost is the one thing the checkpoint does
not hold: it comes from the operation's provider usage counters (stage
``vqa``), which cover the current worker attempt only and say so
(``usage.scope``). Nothing here sees a prompt, a page or a provider response
body; the receipt holds counts, axis names, page indices and status words.
"""
from __future__ import annotations

from collections.abc import Mapping

SCHEMA = "svg-deck-vision-review/v1"
#: The provider usage stage every deck vision review call is booked under.
REVIEW_USAGE_STAGE = "vqa"
#: A marker in one of these states was handed to the provider (its outcome may
#: be unknown); ``review_not_dispatched`` was reserved and never sent.
_DISPATCHED_STATUSES = frozenset({"completed", "review_failed", "review_unknown", "review_inflight"})
#: ``quality_recovery`` states of a page whose redesign was dispatched.
_REDESIGN_ATTEMPTED = frozenset({"candidate_ready", "selected", "retained"})


def _group_calls(marker: Mapping) -> dict[str, int]:
    attempts = [item for item in marker.get("superseded_attempts") or [] if isinstance(item, Mapping)]
    attempts.append(marker)
    dispatched = [item for item in attempts if item.get("status") in _DISPATCHED_STATUSES]
    return {
        "logical": int(bool(dispatched)),
        "physical": len(dispatched),
        "completed": int(marker.get("status") == "completed"),
        "failed": int(marker.get("status") == "review_failed"),
        "unknown": int(marker.get("status") in {"review_unknown", "review_inflight"}),
    }


def _gate(advisory: object) -> Mapping | None:
    if not isinstance(advisory, Mapping):
        return None
    vision = advisory.get("vision")
    gate = vision.get("designGate") if isinstance(vision, Mapping) else None
    return gate if isinstance(gate, Mapping) else None


def _failed_axes(gate: Mapping | None) -> dict[str, int]:
    """Pages each design axis failed on, as the sealed gate decided it."""

    counts: dict[str, int] = {}
    for page in (gate or {}).get("pages") or []:
        if not isinstance(page, Mapping):
            continue
        for axis in page.get("failedAxes") or []:
            if isinstance(axis, str):
                counts[axis] = counts.get(axis, 0) + 1
    return dict(sorted(counts.items()))


def _round(name: str, marker: Mapping) -> dict[str, object]:
    groups = marker.get("groups") if isinstance(marker.get("groups"), Mapping) else {}
    calls = {"logical": 0, "physical": 0, "completed": 0, "failed": 0, "unknown": 0}
    scopes: dict[str, int] = {}
    for group in groups.values():
        if not isinstance(group, Mapping):
            continue
        group_calls = _group_calls(group)
        for key, value in group_calls.items():
            calls[key] += value
        if group_calls["logical"]:
            scope = str(group.get("review_scope") or "unknown")
            scopes[scope] = scopes.get(scope, 0) + 1
    advisory = marker.get("advisory")
    gate = _gate(advisory)
    vision = advisory.get("vision") if isinstance(advisory, Mapping) else None
    return {
        "round": name, "status": marker.get("status"), "calls": calls,
        "callsByScope": dict(sorted(scopes.items())),
        "gateStatus": gate.get("status") if gate else None,
        "skipped": vision.get("skipped") if isinstance(vision, Mapping) else None,
        "failedAxes": _failed_axes(gate),
        "blockingPages": list(gate.get("blockingPages") or []) if gate else [],
        "unreviewedPages": list(gate.get("unreviewedPages") or []) if gate else [],
    }


def _redesigns(pages) -> dict[str, object]:
    attempted, adopted, retained, unresolved = [], [], [], []
    target_axes: dict[str, int] = {}
    for index, (payload, _) in enumerate(pages):
        marker = payload.get("quality_recovery") if isinstance(payload, Mapping) else None
        if not isinstance(marker, Mapping):
            continue
        status = marker.get("status")
        if status == "unresolved":
            unresolved.append(index)
            continue
        if status not in _REDESIGN_ATTEMPTED:
            continue
        attempted.append(index)
        if status == "selected":
            adopted.append(index)
        elif status == "retained":
            retained.append(index)
        decision = marker.get("decision") if isinstance(marker.get("decision"), Mapping) else {}
        for axis in decision.get("target_axes") or []:
            if isinstance(axis, str):
                target_axes[axis] = target_axes.get(axis, 0) + 1
    return {"attempted": len(attempted), "adopted": len(adopted), "retained": len(retained),
            "notAttempted": len(unresolved), "attemptedPages": attempted, "adoptedPages": adopted,
            "retainedPages": retained, "notAttemptedPages": unresolved,
            "targetAxes": dict(sorted(target_axes.items()))}


def _usage(operation_usage) -> dict[str, object]:
    if operation_usage is None:
        return {"scope": "unmeasured"}
    stage = operation_usage.snapshot()["stages"].get(REVIEW_USAGE_STAGE)
    return {"scope": "attempt", "stage": REVIEW_USAGE_STAGE, **(stage or {})}


def vision_review_receipt(pages, advisory, *, operation_usage=None) -> dict[str, object]:
    """The deck's vision review calls, cost, failed axes and redesigns.

    ``pages`` are the ``(payload, png)`` pairs the quality pass classified and
    ``advisory`` the review record publication binds. ``operation_usage`` is
    the attempt's :class:`ProviderOperationUsage`, or ``None`` outside one.
    """

    seed = pages[0][0] if pages and isinstance(pages[0][0], Mapping) else {}
    execution = seed.get("quality_recovery_execution")
    rounds = []
    if isinstance(execution, Mapping):
        for ordinal, marker in enumerate(execution.get("rounds") or []):
            if isinstance(marker, Mapping):
                rounds.append(_round("initial" if ordinal == 0 else "candidate", marker))
        final = execution.get("final_selected_review")
        if isinstance(final, Mapping):
            rounds.append(_round("final_selected", final))
    calls = {key: sum(item["calls"][key] for item in rounds)
             for key in ("logical", "physical", "completed", "failed", "unknown")}
    receipt: dict[str, object] = {
        "schema": SCHEMA, "path": "quality_first" if isinstance(execution, Mapping) else "advisory",
        "calls": calls, "rounds": rounds, "redesigns": _redesigns(pages),
        "usage": _usage(operation_usage),
    }
    gate = _gate(advisory)
    receipt["finalGateStatus"] = gate.get("status") if gate else None
    receipt["finalFailedAxes"] = _failed_axes(gate)
    if not isinstance(execution, Mapping):
        # The optional single-call advisory of a request without the
        # quality-first policy: no group markers, one record at most.
        from services.generation_core.quality.advisory_record import advisory_ran

        vision = advisory.get("vision") if isinstance(advisory, Mapping) else None
        receipt["advisory"] = {"ran": advisory_ran(advisory),
                               "skipped": vision.get("skipped") if isinstance(vision, Mapping) else None}
    return receipt


__all__ = ["REVIEW_USAGE_STAGE", "SCHEMA", "vision_review_receipt"]
