"""Bounded deterministic geometry alternatives for SlideSpec repairs.

The authored executor owns browser measurements and source projection.  This
module deliberately owns neither.  It consumes *proved*, same-coordinate-space
geometry issues and turns them into a small set of mutation-envelope compliant
operation groups.  Callers can therefore reject unsupported legacy structures
before rendering while native SlideSpecs retain the schema's default envelope.

``geometryIssues`` entries use the following closed bridge contract:

* ``code``, ``semanticId``, ``qid``, ``kind``, ``target`` and
  ``currentBounds`` identify the exact detector defect and bound layout node.
* ``constraintId`` is required for relational-constraint defects.
* ``detectorEvidence.confirmed`` must be true.  Continuous occlusion or another
  heuristic that has not confirmed a target defect is recorded as no-candidate
  evidence; this module never moves an element to hide a detector false positive.
  Non-overlap additionally requires ``independentElements=true`` so an
  ancestor/descendant card, label, or decoration pair is never separated.
* ``sameCoordinateSpace`` must be true.  All supplied bounds must be normalized
  in the target operation's parent coordinate system.
* ``otherTarget``/``otherBounds`` describe the peer for non-overlap, gap,
  alignment and order repairs.  ``containerBounds`` describes containment.
* ``mutationEnvelope`` and ``otherMutationEnvelope`` use ``LayoutConstraints``
  field names.  ``requiresEnvelope`` prevents imported HTML from falling back
  to native schema defaults.
* ``coordinatedAncestorMoves`` is an optional array of two-owner Chrome proofs.
  Each pair binds exact owner identities/bounds, signed normalized and pixel
  deltas, canvas capacities, and the separation it resolves.  When present it
  is authoritative: leaf and partial-owner alternatives are not substituted.
  A bound ancestor marked ``coordinatedOwnerOnly=true`` contributes ancestry
  evidence to this proof but is never emitted as a standalone move.

The generator never guesses browser geometry or expands an envelope.  Invalid
or unproved input produces a structured rejection instead of a candidate.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


_EPSILON = 1e-9
_DEFAULT_MAX_TRANSLATION = 0.08
_DEFAULT_MAX_RESIZE = 0.25


@dataclass(frozen=True, slots=True)
class GeometryCandidate:
    """One issue-scoped, schema-ready repair alternative."""

    issue_group_id: str
    issue_code: str
    semantic_id: str
    qid: str
    constraint_id: str | None
    kind: str
    target: str
    operations: tuple[dict[str, Any], ...]
    diagnostic_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GeometryCandidateRejection:
    """Exact no-candidate evidence retained for the orchestration boundary."""

    issue_code: str
    element: Mapping[str, str]
    constraint_id: str | None
    kind: str
    operation: str
    reason_code: str
    stage: str
    metric: str
    detector_evidence: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class GeometryCandidateBuildResult:
    candidates: tuple[GeometryCandidate, ...]
    rejections: tuple[GeometryCandidateRejection, ...] = ()


@dataclass(frozen=True, slots=True)
class _Rect:
    x: float
    y: float
    width: float
    height: float

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height

    def moved(self, dx: float, dy: float) -> _Rect:
        return _Rect(self.x + dx, self.y + dy, self.width, self.height)

    def as_dict(self) -> dict[str, float]:
        return {
            "x": round(self.x, 9),
            "y": round(self.y, 9),
            "width": round(self.width, 9),
            "height": round(self.height, 9),
        }


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: object) -> tuple[object, ...]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return tuple(value)
    return ()


def _number(value: object, default: float = math.nan) -> float:
    if isinstance(value, bool):
        return default
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _rect(value: object) -> _Rect | None:
    raw = _mapping(value)
    values = tuple(_number(raw.get(name)) for name in ("x", "y", "width", "height"))
    if not all(math.isfinite(item) for item in values):
        return None
    result = _Rect(*values)
    if (
        result.x < -_EPSILON
        or result.y < -_EPSILON
        or result.width <= _EPSILON
        or result.height <= _EPSILON
        or result.right > 1 + _EPSILON
        or result.bottom > 1 + _EPSILON
    ):
        return None
    return result


def _inside(rect: _Rect, container: _Rect) -> bool:
    return (
        rect.x >= container.x - _EPSILON
        and rect.y >= container.y - _EPSILON
        and rect.right <= container.right + _EPSILON
        and rect.bottom <= container.bottom + _EPSILON
    )


def _envelope(issue: Mapping[str, Any], name: str = "mutationEnvelope") -> Mapping[str, Any]:
    return _mapping(issue.get(name))


def _allowed(envelope: Mapping[str, Any], operation: str) -> bool:
    raw = envelope.get("allowedOperations")
    if raw is None:
        return True
    return operation in {str(value) for value in _sequence(raw)}


def _operation_for_bounds(
    *,
    target: str,
    current: _Rect,
    candidate: _Rect,
    envelope: Mapping[str, Any],
    expected_bounds: object | None = None,
) -> tuple[dict[str, Any], ...] | None:
    dx = candidate.x - current.x
    dy = candidate.y - current.y
    dw = candidate.width - current.width
    dh = candidate.height - current.height
    moved = abs(dx) > _EPSILON or abs(dy) > _EPSILON
    resized = abs(dw) > _EPSILON or abs(dh) > _EPSILON
    if not moved and not resized:
        return None
    if bool(envelope.get("locked", False)):
        return None
    if moved and bool(envelope.get("lockPosition", False)):
        return None
    if resized and bool(envelope.get("lockSize", False)):
        return None

    max_translation = _number(
        envelope.get("maxTranslation"), _DEFAULT_MAX_TRANSLATION
    )
    max_resize = _number(envelope.get("maxResizeDelta"), _DEFAULT_MAX_RESIZE)
    if moved and max(abs(dx), abs(dy)) > max_translation + _EPSILON:
        return None
    if resized and max(abs(dw), abs(dh)) > max_resize + _EPSILON:
        return None
    if resized:
        minimum_width = _number(envelope.get("minWidth"), _EPSILON)
        minimum_height = _number(envelope.get("minHeight"), _EPSILON)
        maximum_width = _number(envelope.get("maxWidth"), 1.0)
        maximum_height = _number(envelope.get("maxHeight"), 1.0)
        if not (
            minimum_width - _EPSILON <= candidate.width <= maximum_width + _EPSILON
            and minimum_height - _EPSILON <= candidate.height <= maximum_height + _EPSILON
        ):
            return None
        if bool(envelope.get("preserveAspectRatio", False)):
            before = current.width / current.height
            after = candidate.width / candidate.height
            if abs(before - after) > 1e-7:
                return None

    raw_expected = _mapping(expected_bounds)
    expected = (
        {name: raw_expected[name] for name in ("x", "y", "width", "height")}
        if all(name in raw_expected for name in ("x", "y", "width", "height"))
        else current.as_dict()
    )
    if moved and not resized and _allowed(envelope, "translateWithinParent"):
        return ({
            "op": "translateWithinParent",
            "target": target,
            "value": {"x": round(dx, 9), "y": round(dy, 9)},
            "precondition": {"expectedBounds": expected},
        },)
    if resized and not moved and _allowed(envelope, "resizeWithinBounds"):
        return ({
            "op": "resizeWithinBounds",
            "target": target,
            "width": round(candidate.width, 9),
            "height": round(candidate.height, 9),
            "precondition": {"expectedBounds": expected},
        },)
    if _allowed(envelope, "setBounds"):
        return ({
            "op": "setBounds",
            "target": target,
            "bounds": candidate.as_dict(),
            "precondition": {"expectedBounds": expected},
        },)
    return None


def _edge(rect: _Rect, name: str) -> float:
    return {
        "left": rect.x,
        "centerX": rect.x + rect.width / 2,
        "right": rect.right,
        "top": rect.y,
        "centerY": rect.y + rect.height / 2,
        "bottom": rect.bottom,
    }[name]


def _diagnostic_refs(
    issue_group_id: str,
    issue_code: str,
    semantic_id: str,
    qid: str,
    constraint_id: str | None,
    target: str,
    stage: str,
    metric: str,
) -> tuple[str, ...]:
    return tuple(
        value
        for value in (
            f"issue-group:{issue_group_id}",
            f"code:{issue_code}",
            f"semantic:{semantic_id}",
            f"qid:{qid}",
            f"constraint:{constraint_id}" if constraint_id else None,
            f"target:{target}",
            f"stage:{stage}",
            f"metric:{metric}",
        )
        if value is not None
    )


def _candidate(
    issue: Mapping[str, Any],
    operations: tuple[dict[str, Any], ...],
    *,
    promoted_ancestor: Mapping[str, Any] | None = None,
) -> GeometryCandidate:
    issue_group_id = str(issue.get("issueGroupId", "")) or ":".join(
        str(issue.get(name, ""))
        for name in ("constraintId", "code", "semanticId", "qid")
    )
    issue_code = str(issue.get("code", ""))
    semantic_id = str(issue.get("semanticId", ""))
    qid = str(issue.get("qid", ""))
    constraint_id = str(issue["constraintId"]) if issue.get("constraintId") else None
    target = str(issue.get("target", ""))
    detector = _mapping(issue.get("detectorEvidence"))
    promoted = _mapping(promoted_ancestor)
    refs = _diagnostic_refs(
        issue_group_id,
        issue_code,
        semantic_id,
        qid,
        constraint_id,
        target,
        str(detector.get("stage", "candidate_generation")),
        str(detector.get("metric", "unknown")),
    )
    if promoted:
        refs = tuple(
            dict.fromkeys(
                (
                    *refs,
                    f"owner-qid:{promoted.get('qid', '')}",
                    f"owner-semantic:{promoted.get('semanticId', '')}",
                    f"owner-target:{promoted.get('layoutId', '')}",
                    f"owner-role:{promoted.get('ownerRole', '')}",
                )
            )
        )
    return GeometryCandidate(
        issue_group_id,
        issue_code,
        semantic_id,
        qid,
        constraint_id,
        str(issue.get("kind", "")),
        target,
        operations,
        refs,
    )


def _movement_alternatives(
    issue: Mapping[str, Any],
    current: _Rect,
    other: _Rect,
    vectors: Sequence[tuple[float, float]],
) -> list[GeometryCandidate]:
    target = str(issue["target"])
    other_target = str(issue.get("otherTarget", ""))
    envelope = _envelope(issue)
    other_envelope = _envelope(issue, "otherMutationEnvelope")
    container = _rect(issue.get("containerBounds")) or _Rect(0, 0, 1, 1)
    result: list[GeometryCandidate] = []
    for dx, dy in vectors:
        moved = current.moved(dx, dy)
        if _inside(moved, container):
            operations = _operation_for_bounds(
                target=target,
                current=current,
                candidate=moved,
                envelope=envelope,
                expected_bounds=issue.get("currentBounds"),
            )
            if operations:
                result.append(_candidate(issue, operations))
        if not other_target:
            continue
        other_moved = other.moved(-dx, -dy)
        if _inside(other_moved, container):
            operations = _operation_for_bounds(
                target=other_target,
                current=other,
                candidate=other_moved,
                envelope=other_envelope,
                expected_bounds=issue.get("otherBounds"),
            )
            if operations:
                result.append(_candidate(issue, operations))
        target_half = current.moved(dx / 2, dy / 2)
        other_half = other.moved(-dx / 2, -dy / 2)
        if not (_inside(target_half, container) and _inside(other_half, container)):
            continue
        target_operations = _operation_for_bounds(
            target=target,
            current=current,
            candidate=target_half,
            envelope=envelope,
            expected_bounds=issue.get("currentBounds"),
        )
        other_operations = _operation_for_bounds(
            target=other_target,
            current=other,
            candidate=other_half,
            envelope=other_envelope,
            expected_bounds=issue.get("otherBounds"),
        )
        if target_operations and other_operations:
            result.append(_candidate(issue, (*target_operations, *other_operations)))
    return result


def _non_overlap(issue: Mapping[str, Any], current: _Rect, other: _Rect) -> list[GeometryCandidate]:
    clearance = max(0.0, _number(issue.get("clearance"), 0.0))
    vectors = (
        (other.x - clearance - current.right, 0.0),
        (other.right + clearance - current.x, 0.0),
        (0.0, other.y - clearance - current.bottom),
        (0.0, other.bottom + clearance - current.y),
    )
    return _movement_alternatives(issue, current, other, vectors)


def _containment(issue: Mapping[str, Any], current: _Rect) -> list[GeometryCandidate]:
    container = _rect(issue.get("containerBounds")) or _Rect(0, 0, 1, 1)
    insets = _mapping(issue.get("insets"))
    left = container.x + container.width * max(0.0, _number(insets.get("left"), 0.0))
    right = container.right - container.width * max(0.0, _number(insets.get("right"), 0.0))
    top = container.y + container.height * max(0.0, _number(insets.get("top"), 0.0))
    bottom = container.bottom - container.height * max(0.0, _number(insets.get("bottom"), 0.0))
    available_width = right - left
    available_height = bottom - top
    if available_width <= _EPSILON or available_height <= _EPSILON:
        return []
    width = min(current.width, available_width)
    height = min(current.height, available_height)
    if bool(_envelope(issue).get("preserveAspectRatio", False)):
        scale = min(1.0, available_width / current.width, available_height / current.height)
        width = current.width * scale
        height = current.height * scale
    candidate = _Rect(
        min(max(current.x, left), right - width),
        min(max(current.y, top), bottom - height),
        width,
        height,
    )
    operations = _operation_for_bounds(
        target=str(issue["target"]),
        current=current,
        candidate=candidate,
        envelope=_envelope(issue),
        expected_bounds=issue.get("currentBounds"),
    )
    return [_candidate(issue, operations)] if operations else []


def _gap_or_order(
    issue: Mapping[str, Any], current: _Rect, other: _Rect
) -> list[GeometryCandidate]:
    kind = str(issue.get("kind"))
    axis = str(issue.get("axis", issue.get("orderBy", "horizontal")))
    horizontal = axis in {"horizontal", "x"}
    role = str(issue.get("targetRole", "trailing" if kind == "gap" else "after"))
    minimum = max(
        0.0,
        _number(
            issue.get("minimum", issue.get("minimumSeparation")),
            0.0,
        ),
    )
    maximum_raw = _number(issue.get("maximum"))
    maximum = maximum_raw if math.isfinite(maximum_raw) else None
    preferred_raw = _number(issue.get("preferred"))
    preferred = preferred_raw if math.isfinite(preferred_raw) else None
    target_first = role in {"leading", "before"}
    first, second = (current, other) if target_first else (other, current)
    gap = (second.x - first.right) if horizontal else (second.y - first.bottom)
    desired = preferred if preferred is not None else minimum
    if maximum is not None and gap > maximum:
        desired = maximum
    elif gap >= minimum and (maximum is None or gap <= maximum) and preferred is None:
        return []
    delta = desired - gap
    if target_first:
        delta = -delta
    vectors = ((delta, 0.0),) if horizontal else ((0.0, delta),)
    return _movement_alternatives(issue, current, other, vectors)


def _alignment_or_anchor(
    issue: Mapping[str, Any], current: _Rect, other: _Rect
) -> list[GeometryCandidate]:
    target_edge = str(issue.get("edge", issue.get("targetEdge", "left")))
    other_edge = str(issue.get("containerEdge", target_edge))
    if target_edge not in {"left", "centerX", "right", "top", "centerY", "bottom"}:
        return []
    if other_edge not in {"left", "centerX", "right", "top", "centerY", "bottom"}:
        return []
    offset = _number(issue.get("offset"), 0.0)
    delta = _edge(other, other_edge) + offset - _edge(current, target_edge)
    vectors = ((delta, 0.0),) if target_edge in {"left", "centerX", "right"} else ((0.0, delta),)
    return _movement_alternatives(issue, current, other, vectors)


def _overflow(issue: Mapping[str, Any], current: _Rect) -> list[GeometryCandidate]:
    result: list[GeometryCandidate] = []
    required = _rect(issue.get("requiredBounds"))
    if required is not None:
        operations = _operation_for_bounds(
            target=str(issue["target"]),
            current=current,
            candidate=required,
            envelope=_envelope(issue),
            expected_bounds=issue.get("currentBounds"),
        )
        if operations:
            result.append(_candidate(issue, operations))

    font = _mapping(issue.get("fontScale"))
    current_scale = _number(font.get("current"), 1.0)
    required_scale = _number(font.get("required"))
    minimum_scale = _number(font.get("minimum"), 0.85)
    envelope = _envelope(issue)
    minimum_scale = max(minimum_scale, _number(envelope.get("minFontScale"), 0.5))
    maximum_shrink = _number(envelope.get("maxFontShrink"), 0.15)
    if (
        math.isfinite(required_scale)
        and minimum_scale - _EPSILON <= required_scale < current_scale - _EPSILON
        and 1 - required_scale / current_scale <= maximum_shrink + _EPSILON
        and not bool(envelope.get("locked", False))
        and _allowed(envelope, "setFontScale")
    ):
        result.append(
            _candidate(
                issue,
                ({
                    "op": "setFontScale",
                    "target": str(issue["target"]),
                    "scale": round(required_scale, 9),
                    "bounds": {"minScale": minimum_scale, "maxScale": current_scale},
                    "precondition": {"currentScale": current_scale},
                },),
            )
        )
    return result


def _ancestor_promotions(
    issue: Mapping[str, Any],
) -> tuple[list[GeometryCandidate], tuple[str, ...]]:
    """Promote a proved leaf repair to a browser-owned movable ancestor.

    Candidate deltas are explicit because a leaf's normalized coordinate system
    is generally different from its ancestor's immediate-parent coordinates.
    The generator never infers that conversion from DOM topology.
    """

    candidates: list[GeometryCandidate] = []
    reasons: list[str] = []
    detector = _mapping(issue.get("detectorEvidence"))
    sources = (
        ("boundAncestors", frozenset((str(issue.get("qid", "")),))),
        (
            "otherBoundAncestors",
            frozenset(
                str(value)
                for value in (
                    issue.get("otherQid"),
                    detector.get("occluderBindingQid"),
                    detector.get("occluderQid"),
                )
                if value
            ),
        ),
    )
    raw_ancestors = (
        (field, expected_qids, _mapping(value))
        for field, expected_qids in sources
        for value in _sequence(issue.get(field))
    )
    ordered = sorted(
        raw_ancestors,
        key=lambda item: (
            0 if item[0] == "boundAncestors" else 1,
            int(_number(item[2].get("depth"), 1_000_000)),
            str(item[2].get("layoutId", "")),
            str(item[2].get("qid", "")),
        ),
    )
    for field, expected_qids, ancestor in ordered:
        if ancestor.get("coordinatedOwnerOnly") is True:
            physical_owner_failure = _mapping(
                ancestor.get("physicalOwnerFailure")
            )
            if physical_owner_failure:
                reasons.append(
                    str(
                        physical_owner_failure.get("reasonCode")
                        or "repair.geometry.ancestor.physical_owner_proof_invalid"
                    )
                )
            continue
        layout_id = str(ancestor.get("layoutId", ""))
        qid = str(ancestor.get("qid", ""))
        proof = _mapping(ancestor.get("browserProof"))
        if not layout_id or not qid:
            reasons.append("repair.geometry.ancestor.identity_missing")
            continue
        if (
            ancestor.get("sameCoordinateSpace") is not True
            or ancestor.get("ownershipProven") is not True
            or proof.get("source") != "chrome"
            or proof.get("telemetryComplete") is not True
            or not expected_qids
            or str(proof.get("targetQid", "")) not in expected_qids
        ):
            reasons.append("repair.geometry.ancestor.ownership_unproven")
            continue
        current = _rect(ancestor.get("bounds"))
        if current is None:
            reasons.append("repair.geometry.ancestor.bounds_invalid")
            continue
        envelope = _mapping(ancestor.get("mutationEnvelope"))
        if issue.get("requiresEnvelope") is True and not envelope:
            reasons.append("repair.geometry.ancestor.envelope_missing")
            continue
        container = _rect(ancestor.get("containerBounds")) or _Rect(0, 0, 1, 1)
        raw_deltas = tuple(
            _mapping(value) for value in _sequence(ancestor.get("candidateDeltas"))
        )
        deltas = sorted(
            raw_deltas,
            key=lambda delta: (
                abs(_number(delta.get("x"), float("inf")))
                + abs(_number(delta.get("y"), float("inf"))),
                _number(delta.get("x"), float("inf")),
                _number(delta.get("y"), float("inf")),
            ),
        )
        valid_delta_seen = False
        for delta in deltas:
            if (
                delta.get("source") != "chrome"
                or delta.get("telemetryComplete") is not True
                or delta.get("resolvesIssue") is not True
            ):
                continue
            dx = _number(delta.get("x"))
            dy = _number(delta.get("y"))
            if (
                not math.isfinite(dx)
                or not math.isfinite(dy)
                or abs(dx) > 1
                or abs(dy) > 1
                or (abs(dx) <= _EPSILON and abs(dy) <= _EPSILON)
            ):
                continue
            physical_proof = _mapping(delta.get("physicalOwnerProof"))
            if physical_proof:
                owner_px = _pixel_bounds(physical_proof.get("ownerBoundsPx"))
                candidate_px = _pixel_bounds(
                    physical_proof.get("candidateOwnerBoundsPx")
                )
                delta_px = _mapping(delta.get("deltaPx"))
                blocker_bounds = _mapping(
                    physical_proof.get("blockerBoundsPx")
                )
                boundary_y = _number(
                    physical_proof.get("lowerBoundaryYPx"), float("nan")
                )
                exact_candidate = (
                    owner_px is not None
                    and candidate_px is not None
                    and abs(candidate_px[0] - owner_px[0] - _number(delta_px.get("x")))
                    <= 1e-6
                    and abs(candidate_px[1] - owner_px[1] - _number(delta_px.get("y")))
                    <= 1e-6
                    and abs(candidate_px[2] - owner_px[2]) <= 1e-6
                    and abs(candidate_px[3] - owner_px[3]) <= 1e-6
                )
                candidate_rect = (
                    _Rect(*candidate_px) if candidate_px is not None else None
                )
                blockers = tuple(
                    _Rect(*bounds)
                    for value in blocker_bounds.values()
                    if (bounds := _pixel_bounds(value)) is not None
                )
                physical_proof_valid = (
                    str(physical_proof.get("ownerQid", "")) == qid
                    and physical_proof.get("allTargetsOwned") is True
                    and physical_proof.get("candidateSeparatesBlockers") is True
                    and physical_proof.get("candidateContained") is True
                    and bool(_sequence(physical_proof.get("ownedTargetQids")))
                    and exact_candidate
                    and candidate_rect is not None
                    and bool(blockers)
                    and all(
                        _rects_do_not_overlap(candidate_rect, blocker)
                        for blocker in blockers
                    )
                    and math.isfinite(boundary_y)
                    and candidate_rect.bottom <= boundary_y + 1e-6
                )
                if not physical_proof_valid:
                    reasons.append(
                        "repair.geometry.ancestor.physical_owner_proof_invalid"
                    )
                    continue
            valid_delta_seen = True
            moved = current.moved(dx, dy)
            if not _inside(moved, container):
                continue
            operations = _operation_for_bounds(
                target=layout_id,
                current=current,
                candidate=moved,
                envelope=envelope,
                expected_bounds=ancestor.get("bounds"),
            )
            if operations:
                candidates.append(
                    _candidate(
                        issue,
                        operations,
                        promoted_ancestor={**ancestor, "ownerRole": field},
                    )
                )
        if not valid_delta_seen:
            reasons.append("repair.geometry.ancestor.delta_unproven")
    return candidates, tuple(dict.fromkeys(reasons))


def _rects_do_not_overlap(first: _Rect, second: _Rect) -> bool:
    return (
        first.right <= second.x + _EPSILON
        or second.right <= first.x + _EPSILON
        or first.bottom <= second.y + _EPSILON
        or second.bottom <= first.y + _EPSILON
    )


def _pixel_bounds(value: object) -> tuple[float, float, float, float] | None:
    raw = _mapping(value)
    values = tuple(_number(raw.get(name)) for name in ("x", "y", "width", "height"))
    if (
        not all(math.isfinite(item) for item in values)
        or values[2] <= _EPSILON
        or values[3] <= _EPSILON
    ):
        return None
    return values


def _expected_connected_issue_qids(
    context: Mapping[str, Any], seed_issue: Mapping[str, Any]
) -> frozenset[str]:
    """Independently close overlap endpoints through proved ancestry.

    Aggregate section↔section defects may be excluded from required operation
    groups as redundant, but their endpoints remain essential ownership
    evidence.  This closure prevents a leaf wrapper from claiming the same
    issue-group coverage as the section that owns its sibling paint.
    """

    issues = tuple(
        _mapping(value)
        for value in _sequence(context.get("geometryIssues"))
        if _mapping(value).get("kind") == "nonOverlap"
        and _mapping(_mapping(value).get("detectorEvidence")).get("confirmed")
        is True
    )
    endpoints_by_group: dict[str, frozenset[str]] = {}
    ancestors_by_qid: dict[str, set[str]] = {}
    seed_group = str(seed_issue.get("issueGroupId", ""))
    for item in issues:
        detector = _mapping(item.get("detectorEvidence"))
        group_id = str(item.get("issueGroupId", ""))
        endpoints = frozenset(
            str(value)
            for value in (
                item.get("qid"),
                detector.get("occluderQid"),
                detector.get("occluderBindingQid"),
            )
            if value
        )
        if group_id and endpoints:
            endpoints_by_group[group_id] = endpoints
        for field in ("boundAncestors", "otherBoundAncestors"):
            for raw_ancestor in _sequence(item.get(field)):
                proof = _mapping(_mapping(raw_ancestor).get("browserProof"))
                target_qid = str(proof.get("targetQid", ""))
                chain = tuple(
                    str(value)
                    for value in _sequence(proof.get("parentQidChain"))
                    if value
                )
                if target_qid and chain and chain[0] == target_qid:
                    ancestors_by_qid.setdefault(target_qid, set()).update(chain[1:])
        for raw_pair in _sequence(item.get("coordinatedAncestorMoves")):
            pair_proof = _mapping(_mapping(raw_pair).get("proof"))
            owner_coverage = _mapping(pair_proof.get("ownerCoverage"))
            for role in ("target", "peer"):
                role_coverage = _mapping(owner_coverage.get(role))
                chains = _mapping(role_coverage.get("ancestorQidChains"))
                for qid, raw_chain in chains.items():
                    chain = tuple(
                        str(value) for value in _sequence(raw_chain) if value
                    )
                    qid = str(qid)
                    if qid and chain and chain[0] == qid:
                        ancestors_by_qid.setdefault(qid, set()).update(chain[1:])
    if not seed_group or seed_group not in endpoints_by_group:
        return frozenset()

    def related(first: str, second: str) -> bool:
        return (
            first == second
            or first in ancestors_by_qid.get(second, set())
            or second in ancestors_by_qid.get(first, set())
        )

    remaining = set(endpoints_by_group) - {seed_group}
    connected = {seed_group}
    frontier = [seed_group]
    while frontier:
        current = frontier.pop()
        neighbors = {
            candidate
            for candidate in remaining
            if any(
                related(left, right)
                for left in endpoints_by_group[current]
                for right in endpoints_by_group[candidate]
            )
        }
        remaining.difference_update(neighbors)
        connected.update(neighbors)
        frontier.extend(sorted(neighbors))
    return frozenset(
        qid for group_id in connected for qid in endpoints_by_group[group_id]
    )


def _coordinated_ancestor_moves(
    issue: Mapping[str, Any],
    context: Mapping[str, Any],
) -> tuple[list[GeometryCandidate], tuple[str, ...]]:
    """Consume a Chrome-proved two-owner separation as one atomic candidate.

    A partial owner move is not a repair for an aggregate overlap.  The executor
    therefore supplies both signed deltas, their independently measured canvas
    capacities, and the required target/peer separation.  This function checks
    that closed proof and never derives a split from leaf geometry.
    """

    raw_pairs = tuple(
        _mapping(value)
        for value in _sequence(issue.get("coordinatedAncestorMoves"))
    )
    if not raw_pairs:
        return [], ()
    limits = _mapping(context.get("limits"))
    max_operations = int(max(1, min(32, _number(limits.get("maxOperations"), 8))))
    max_affected = int(
        max(
            1,
            min(
                32,
                _number(
                    limits.get(
                        "maxAffectedBlocks", limits.get("maxTouchedLayoutNodes")
                    ),
                    4,
                ),
            ),
        )
    )
    if max_operations < 2 or max_affected < 2:
        return [], ("repair.geometry.coordinated_ancestor.limits_exceeded",)

    valid: list[tuple[int, int, float, float, GeometryCandidate]] = []
    reasons: list[str] = []
    ordered_pairs = sorted(
        raw_pairs,
        key=lambda pair: json.dumps(pair, sort_keys=True, separators=(",", ":")),
    )
    for pair in ordered_pairs:
        proof = _mapping(pair.get("proof"))
        moves = tuple(_mapping(value) for value in _sequence(pair.get("moves")))
        axis = str(proof.get("axis", ""))
        direction = _number(proof.get("direction"))
        separation_px = _number(proof.get("separationPx"))
        capacities = _mapping(proof.get("capacityPx"))
        target_capacity = _number(capacities.get("target"))
        peer_capacity = _number(capacities.get("peer"))
        if (
            len(moves) != 2
            or issue.get("kind") != "nonOverlap"
            or proof.get("source") != "chrome"
            or proof.get("telemetryComplete") is not True
            or proof.get("pairResolvesIssue") is not True
            or axis not in {"x", "y"}
            or direction not in {-1.0, 1.0}
            or not math.isfinite(separation_px)
            or separation_px <= _EPSILON
            or not math.isfinite(target_capacity)
            or not math.isfinite(peer_capacity)
            or target_capacity <= _EPSILON
            or peer_capacity <= _EPSILON
        ):
            reasons.append("repair.geometry.coordinated_ancestor.proof_invalid")
            continue

        target_move, peer_move = moves
        if (
            str(proof.get("targetQid", "")) != str(target_move.get("qid", ""))
            or str(proof.get("peerQid", "")) != str(peer_move.get("qid", ""))
            or not str(target_move.get("layoutId", ""))
            or not str(peer_move.get("layoutId", ""))
            or str(target_move.get("layoutId", ""))
            == str(peer_move.get("layoutId", ""))
        ):
            reasons.append("repair.geometry.coordinated_ancestor.identity_mismatch")
            continue

        coverage = _mapping(proof.get("ownerCoverage"))
        target_coverage = _mapping(coverage.get("target"))
        peer_coverage = _mapping(coverage.get("peer"))
        target_owned = tuple(
            str(value) for value in _sequence(target_coverage.get("ownedQids"))
        )
        peer_owned = tuple(
            str(value) for value in _sequence(peer_coverage.get("ownedQids"))
        )
        target_chains = _mapping(target_coverage.get("ancestorQidChains"))
        peer_chains = _mapping(peer_coverage.get("ancestorQidChains"))
        blocked_raw = coverage.get("blockedPaintOwnerQids")
        blocked = tuple(str(value) for value in _sequence(blocked_raw))
        connected_raw = coverage.get("connectedIssueQids")
        connected_qids = tuple(
            dict.fromkeys(str(value) for value in _sequence(connected_raw) if value)
        )
        unowned_raw = coverage.get("unownedConnectedIssueQids")
        unowned_qids = tuple(
            str(value) for value in _sequence(unowned_raw) if value
        )
        expected_connected_qids = _expected_connected_issue_qids(context, issue)
        covered_group_ids = tuple(
            dict.fromkeys(
                str(value)
                for value in _sequence(proof.get("coveredIssueGroupIds"))
                if value
            )
        )
        owner_depth = _mapping(proof.get("ownerDepth"))
        target_depth = _number(owner_depth.get("target"))
        peer_depth = _number(owner_depth.get("peer"))

        def chain_is_exact(
            qid: str, owner_qid: str, chains: Mapping[str, Any]
        ) -> bool:
            chain = tuple(str(value) for value in _sequence(chains.get(qid)))
            return (
                len(chain) >= 1
                and chain[0] == qid
                and chain[-1] == owner_qid
                and len(chain) == len(set(chain))
            )

        target_owner_qid = str(target_move.get("qid", ""))
        peer_owner_qid = str(peer_move.get("qid", ""))
        detector = _mapping(issue.get("detectorEvidence"))
        occluder_qid = str(detector.get("occluderQid", ""))
        exact_target_chains = all(
            chain_is_exact(qid, target_owner_qid, target_chains)
            for qid in target_owned
        )
        exact_peer_chains = all(
            chain_is_exact(qid, peer_owner_qid, peer_chains)
            for qid in peer_owned
        )
        all_target_chain_qids = {
            str(value)
            for qid in target_owned
            for value in _sequence(target_chains.get(qid))
        }
        all_peer_chain_qids = {
            str(value)
            for qid in peer_owned
            for value in _sequence(peer_chains.get(qid))
        }
        if (
            proof.get("coversBlockedPaint") is not True
            or "blockedPaintOwnerQids" not in coverage
            or not isinstance(blocked_raw, Sequence)
            or isinstance(blocked_raw, (str, bytes))
            or blocked
            or "connectedIssueQids" not in coverage
            or not isinstance(connected_raw, Sequence)
            or isinstance(connected_raw, (str, bytes))
            or not connected_qids
            or not expected_connected_qids
            or not expected_connected_qids.issubset(connected_qids)
            or set(connected_qids).difference(expected_connected_qids).difference(
                {target_owner_qid, peer_owner_qid}
            )
            or "unownedConnectedIssueQids" not in coverage
            or not isinstance(unowned_raw, Sequence)
            or isinstance(unowned_raw, (str, bytes))
            or unowned_qids
            or str(target_coverage.get("ownerQid", "")) != target_owner_qid
            or str(peer_coverage.get("ownerQid", "")) != peer_owner_qid
            or not target_owned
            or not peer_owned
            or set(target_owned).intersection(peer_owned)
            or set(target_owned).union(peer_owned) != set(connected_qids)
            or str(issue.get("qid", "")) not in target_owned
            or not occluder_qid
            or occluder_qid not in peer_owned
            or not exact_target_chains
            or not exact_peer_chains
            or target_owner_qid in all_peer_chain_qids
            or peer_owner_qid in all_target_chain_qids
            or not covered_group_ids
            or str(issue.get("issueGroupId", "")) not in covered_group_ids
            or not math.isfinite(target_depth)
            or not math.isfinite(peer_depth)
            or target_depth < 0
            or peer_depth < 0
        ):
            reasons.append(
                "repair.geometry.coordinated_ancestor.owner_coverage_unproven"
            )
            continue

        target_delta = _mapping(target_move.get("delta"))
        peer_delta = _mapping(peer_move.get("delta"))
        target_delta_px = _mapping(target_move.get("deltaPx"))
        peer_delta_px = _mapping(peer_move.get("deltaPx"))
        orthogonal = "y" if axis == "x" else "x"
        target_axis = _number(target_delta.get(axis))
        peer_axis = _number(peer_delta.get(axis))
        target_axis_px = _number(target_delta_px.get(axis))
        peer_axis_px = _number(peer_delta_px.get(axis))
        scalar_values = (
            target_axis,
            peer_axis,
            target_axis_px,
            peer_axis_px,
            _number(target_delta.get(orthogonal)),
            _number(peer_delta.get(orthogonal)),
            _number(target_delta_px.get(orthogonal)),
            _number(peer_delta_px.get(orthogonal)),
        )
        capacity_evidence = _mapping(proof.get("capacityEvidence"))

        def capacity_is_exact(
            role: str,
            move: Mapping[str, Any],
            normalized_axis_delta: float,
            pixel_axis_delta: float,
            role_direction: float,
            declared_capacity: float,
        ) -> bool:
            evidence = _mapping(capacity_evidence.get(role))
            boundary = _mapping(evidence.get("boundary"))
            boundary_bounds = _pixel_bounds(boundary.get("boundsPx"))
            subtree_bounds = _pixel_bounds(evidence.get("subtreeContentBoundsPx"))
            raw_content_qids = evidence.get("contentQids")
            content_qids = tuple(
                str(value) for value in _sequence(raw_content_qids) if value
            )
            content_kinds = _mapping(evidence.get("contentKinds"))
            normalized_kinds = {
                str(qid): str(kind) for qid, kind in content_kinds.items()
            }
            allowed_content_kinds = {
                "owned-text",
                "replaced-media",
                "owner-bbox-fallback",
            }
            envelope_px = _number(evidence.get("envelopeMaxTranslationPx"))
            evidence_capacity = _number(evidence.get("capacityPx"))
            boundary_kind = str(boundary.get("kind", ""))
            axis_overflow = str(
                boundary.get("overflowX" if axis == "x" else "overflowY", "")
            ).lower()
            clipping_values = {"hidden", "clip", "scroll", "auto"}
            if (
                str(evidence.get("ownerQid", "")) != str(move.get("qid", ""))
                or str(evidence.get("axis", "")) != axis
                or _number(evidence.get("direction")) != role_direction
                or boundary.get("source") != "chrome"
                or boundary_kind not in {"clip-ancestor", "canvas"}
                or boundary_bounds is None
                or subtree_bounds is None
                or not isinstance(raw_content_qids, Sequence)
                or isinstance(raw_content_qids, (str, bytes))
                or not content_qids
                or len(content_qids) != len(set(content_qids))
                or set(normalized_kinds) != set(content_qids)
                or not set(normalized_kinds.values()).issubset(allowed_content_kinds)
                or (
                    "owner-bbox-fallback" in normalized_kinds.values()
                    and (
                        len(content_qids) != 1
                        or content_qids[0] != str(move.get("qid", ""))
                        or normalized_kinds[content_qids[0]]
                        != "owner-bbox-fallback"
                    )
                )
                or not math.isfinite(envelope_px)
                or envelope_px <= _EPSILON
                or not math.isfinite(evidence_capacity)
                or evidence_capacity <= _EPSILON
                or abs(evidence_capacity - declared_capacity) > 1e-6
                or abs(pixel_axis_delta) > evidence_capacity + _EPSILON
            ):
                return False
            if boundary_kind == "clip-ancestor":
                if not str(boundary.get("qid", "")) or axis_overflow not in clipping_values:
                    return False
            elif (
                boundary.get("qid") not in {None, ""}
                or axis_overflow in clipping_values
                or abs(boundary_bounds[0]) > _EPSILON
                or abs(boundary_bounds[1]) > _EPSILON
            ):
                return False
            boundary_start = boundary_bounds[0 if axis == "x" else 1]
            boundary_extent = boundary_bounds[2 if axis == "x" else 3]
            subtree_start = subtree_bounds[0 if axis == "x" else 1]
            subtree_extent = subtree_bounds[2 if axis == "x" else 3]
            raw_capacity = (
                subtree_start - boundary_start
                if role_direction < 0
                else boundary_start
                + boundary_extent
                - subtree_start
                - subtree_extent
            )
            recomputed = max(0.0, min(raw_capacity, envelope_px))
            if abs(recomputed - evidence_capacity) > 1e-6:
                return False
            viewport_axis = (
                abs(pixel_axis_delta / normalized_axis_delta)
                if abs(normalized_axis_delta) > _EPSILON
                else math.nan
            )
            envelope = _mapping(move.get("mutationEnvelope"))
            envelope_normalized = _number(envelope.get("maxTranslation"))
            return (
                math.isfinite(viewport_axis)
                and math.isfinite(envelope_normalized)
                and abs(envelope_normalized * viewport_axis - envelope_px) <= 1e-6
            )

        if (
            not all(math.isfinite(value) for value in scalar_values)
            or target_axis * direction <= _EPSILON
            or peer_axis * direction >= -_EPSILON
            or target_axis_px * direction <= _EPSILON
            or peer_axis_px * direction >= -_EPSILON
            or any(abs(value) > _EPSILON for value in scalar_values[4:])
            or abs(target_axis_px - peer_axis_px) + _EPSILON < separation_px
        ):
            reasons.append("repair.geometry.coordinated_ancestor.split_invalid")
            continue
        if (
            abs(target_axis_px) > target_capacity + _EPSILON
            or abs(peer_axis_px) > peer_capacity + _EPSILON
            or not capacity_is_exact(
                "target",
                target_move,
                target_axis,
                target_axis_px,
                direction,
                target_capacity,
            )
            or not capacity_is_exact(
                "peer",
                peer_move,
                peer_axis,
                peer_axis_px,
                -direction,
                peer_capacity,
            )
        ):
            reasons.append("repair.geometry.coordinated_ancestor.capacity_unproven")
            continue

        target_bounds = _rect(target_move.get("bounds"))
        peer_bounds = _rect(peer_move.get("bounds"))
        if target_bounds is None or peer_bounds is None:
            reasons.append("repair.geometry.coordinated_ancestor.bounds_invalid")
            continue
        target_after = target_bounds.moved(
            _number(target_delta.get("x")), _number(target_delta.get("y"))
        )
        peer_after = peer_bounds.moved(
            _number(peer_delta.get("x")), _number(peer_delta.get("y"))
        )
        if (
            _rects_do_not_overlap(target_bounds, peer_bounds)
            or not _inside(target_after, _Rect(0, 0, 1, 1))
            or not _inside(peer_after, _Rect(0, 0, 1, 1))
            or not _rects_do_not_overlap(target_after, peer_after)
        ):
            reasons.append("repair.geometry.coordinated_ancestor.postcondition_failed")
            continue

        target_operations = _operation_for_bounds(
            target=str(target_move["layoutId"]),
            current=target_bounds,
            candidate=target_after,
            envelope=_mapping(target_move.get("mutationEnvelope")),
            expected_bounds=target_move.get("bounds"),
        )
        peer_operations = _operation_for_bounds(
            target=str(peer_move["layoutId"]),
            current=peer_bounds,
            candidate=peer_after,
            envelope=_mapping(peer_move.get("mutationEnvelope")),
            expected_bounds=peer_move.get("bounds"),
        )
        if (
            target_operations is None
            or peer_operations is None
            or len(target_operations) != 1
            or len(peer_operations) != 1
        ):
            reasons.append("repair.geometry.coordinated_ancestor.envelope_rejected")
            continue
        candidate = _candidate(issue, (*target_operations, *peer_operations))
        refs = tuple(
            dict.fromkeys(
                (
                    *candidate.diagnostic_refs,
                    *(f"issue-group:{group_id}" for group_id in covered_group_ids),
                    "coordinated-ancestor:true",
                    f"owner-qid:{target_move.get('qid', '')}",
                    f"owner-semantic:{target_move.get('semanticId', '')}",
                    f"owner-target:{target_move.get('layoutId', '')}",
                    f"peer-owner-qid:{peer_move.get('qid', '')}",
                    f"peer-owner-semantic:{peer_move.get('semanticId', '')}",
                    f"peer-owner-target:{peer_move.get('layoutId', '')}",
                )
            )
        )
        valid.append(
            (
                -len(covered_group_ids),
                -(len(target_owned) + len(peer_owned)),
                target_depth + peer_depth,
                abs(target_axis_px) + abs(peer_axis_px),
                GeometryCandidate(
                    candidate.issue_group_id,
                    candidate.issue_code,
                    candidate.semantic_id,
                    candidate.qid,
                    candidate.constraint_id,
                    "coordinatedAncestorMoves",
                    candidate.target,
                    candidate.operations,
                    refs,
                ),
            )
        )
    if not valid:
        return [], tuple(dict.fromkeys(reasons))
    valid.sort(
        key=lambda item: (
            item[0],
            item[1],
            round(item[2], 6),
            round(item[3], 6),
            _candidate_key(item[4]),
        )
    )
    return [valid[0][4]], tuple(dict.fromkeys(reasons))


def _relational_issue_present(
    issue: Mapping[str, Any], current: _Rect, other: _Rect | None
) -> bool | None:
    """Independently prove that the supplied baseline geometry violates the issue."""

    kind = str(issue.get("kind", ""))
    tolerance = max(0.0, _number(issue.get("tolerance"), 0.0))
    if kind == "nonOverlap" and other is not None:
        clearance = max(0.0, _number(issue.get("clearance"), 0.0))
        return not (
            current.right + clearance <= other.x + tolerance
            or other.right + clearance <= current.x + tolerance
            or current.bottom + clearance <= other.y + tolerance
            or other.bottom + clearance <= current.y + tolerance
        )
    if kind == "containment":
        container = _rect(issue.get("containerBounds")) or _Rect(0, 0, 1, 1)
        insets = _mapping(issue.get("insets"))
        allowed = _Rect(
            container.x
            + container.width * max(0.0, _number(insets.get("left"), 0.0)),
            container.y
            + container.height * max(0.0, _number(insets.get("top"), 0.0)),
            container.width
            * (
                1
                - max(0.0, _number(insets.get("left"), 0.0))
                - max(0.0, _number(insets.get("right"), 0.0))
            ),
            container.height
            * (
                1
                - max(0.0, _number(insets.get("top"), 0.0))
                - max(0.0, _number(insets.get("bottom"), 0.0))
            ),
        )
        return not _inside(current, allowed)
    if kind in {"gap", "order"} and other is not None:
        axis = str(issue.get("axis", issue.get("orderBy", "horizontal")))
        horizontal = axis in {"horizontal", "x"}
        role = str(
            issue.get("targetRole", "trailing" if kind == "gap" else "after")
        )
        target_first = role in {"leading", "before"}
        first, second = (current, other) if target_first else (other, current)
        gap = (second.x - first.right) if horizontal else (second.y - first.bottom)
        minimum = max(
            0.0,
            _number(issue.get("minimum", issue.get("minimumSeparation")), 0.0),
        )
        maximum = _number(issue.get("maximum"))
        preferred = _number(issue.get("preferred"))
        if gap < minimum - tolerance:
            return True
        if math.isfinite(maximum) and gap > maximum + tolerance:
            return True
        return math.isfinite(preferred) and abs(gap - preferred) > tolerance
    if kind in {"alignment", "anchor"} and other is not None:
        target_edge = str(issue.get("edge", issue.get("targetEdge", "left")))
        other_edge = str(issue.get("containerEdge", target_edge))
        if target_edge not in {
            "left", "centerX", "right", "top", "centerY", "bottom"
        } or other_edge not in {
            "left", "centerX", "right", "top", "centerY", "bottom"
        }:
            return None
        offset = _number(issue.get("offset"), 0.0)
        return abs(_edge(current, target_edge) - _edge(other, other_edge) - offset) > tolerance
    return None


def _glyph_composition_candidates(
    context: Mapping[str, Any],
    issues: Sequence[Mapping[str, Any]],
) -> tuple[list[GeometryCandidate], tuple[tuple[Mapping[str, Any], str], ...]]:
    """Consume an exact Chrome-proved glyph + nested-translation composition.

    The executor supplies the candidate geometry; this function only validates
    identities, envelopes, pixel/normalized deltas, visible-paint clearance,
    canvas capacity, source round-trip support, and the postconditions that the
    normal acceptance pipeline must observe after rendering.
    """

    raw_items = tuple(
        _mapping(value)
        for value in _sequence(context.get("geometryGlyphCompositions"))
    )
    if not raw_items:
        return [], ()
    limits = _mapping(context.get("limits"))
    max_operations = int(max(1, min(32, _number(limits.get("maxOperations"), 8))))
    max_affected = int(
        max(
            1,
            min(
                32,
                _number(
                    limits.get(
                        "maxAffectedBlocks", limits.get("maxTouchedLayoutNodes")
                    ),
                    4,
                ),
            ),
        )
    )
    required_groups = frozenset(
        str(value)
        for value in _sequence(context.get("requiredGeometryIssueGroups"))
    )
    candidates: list[GeometryCandidate] = []
    rejected: list[tuple[Mapping[str, Any], str]] = []

    def reject(issue: Mapping[str, Any], reason: str) -> None:
        rejected.append((issue, reason))

    for item in sorted(
        raw_items,
        key=lambda value: json.dumps(value, sort_keys=True, separators=(",", ":")),
    ):
        group_ids = tuple(
            dict.fromkeys(
                str(value)
                for value in _sequence(item.get("issueGroupIds"))
                if value
            )
        )
        # Optional glyph hints remain available to the ordinary repair builders;
        # this consumer is only for explicitly required closed compositions.
        if (
            item.get("required") is not True
            or not set(group_ids).intersection(required_groups)
        ):
            continue
        matching = tuple(
            issue
            for issue in issues
            if str(issue.get("issueGroupId", "")) in group_ids
            and issue.get("kind") == "nonOverlap"
            and _mapping(issue.get("detectorEvidence")).get("confirmed") is True
        )
        issue = matching[0] if matching else {}
        if (
            not str(item.get("compositionId", ""))
            or len(group_ids) != 1
            or len(matching) != 1
            or group_ids[0] not in required_groups
            or max_operations < 3
            or max_affected < 3
        ):
            reject(issue, "repair.geometry.glyph_composition.identity_or_limits_invalid")
            continue

        glyph = _mapping(item.get("glyph"))
        glyph_envelope = _mapping(glyph.get("mutationEnvelope"))
        glyph_precondition = _mapping(glyph.get("precondition"))
        glyph_measurement = _mapping(glyph.get("measurement"))
        glyph_roundtrip = _mapping(glyph.get("roundTrip"))
        glyph_target = str(glyph.get("target", ""))
        current_scale = _number(glyph.get("currentScale"))
        minimum_scale = _number(glyph.get("minimumScale"))
        candidate_scale = _number(glyph.get("candidateScale"))
        glyph_bounds = _rect(glyph_precondition.get("expectedBounds"))
        line_bounds = tuple(
            bounds
            for bounds in (
                _pixel_bounds(value)
                for value in _sequence(glyph_measurement.get("lineBoundsPx"))
            )
            if bounds is not None
        )
        text_bounds = _pixel_bounds(glyph_measurement.get("textBoundsPx"))
        if line_bounds:
            line_left = min(bounds[0] for bounds in line_bounds)
            line_top = min(bounds[1] for bounds in line_bounds)
            line_right = max(bounds[0] + bounds[2] for bounds in line_bounds)
            line_bottom = max(bounds[1] + bounds[3] for bounds in line_bounds)
            line_union = (
                line_left,
                line_top,
                line_right - line_left,
                line_bottom - line_top,
            )
        else:
            line_union = None
        allowed_glyph_ops = frozenset(
            str(value)
            for value in _sequence(glyph_envelope.get("allowedOperations"))
        )
        verified_glyph = frozenset(
            str(value)
            for value in _sequence(glyph_roundtrip.get("verifiedProperties"))
        )
        measured_width = _number(glyph_measurement.get("measuredWidthPx"))
        measured_height = _number(glyph_measurement.get("measuredHeightPx"))
        if (
            not glyph_target
            or glyph_target != str(glyph.get("layoutId", ""))
            or not str(glyph.get("semanticId", ""))
            or not str(glyph.get("qid", ""))
            or glyph_bounds is None
            or glyph_envelope.get("locked") is True
            or "setFontScale" not in allowed_glyph_ops
            or glyph_measurement.get("source") != "chrome"
            or glyph_measurement.get("telemetryComplete") is not True
            or glyph_measurement.get("fontsReady") is not True
            or glyph_measurement.get("kind") != "range-bounds"
            or text_bounds is None
            or line_union is None
            or len(line_bounds) < 1
            or any(value <= 0 for value in text_bounds[2:])
            or abs(line_union[0] - text_bounds[0]) > 1e-6
            or abs(line_union[1] - text_bounds[1]) > 1e-6
            or abs(line_union[2] - text_bounds[2]) > 1e-6
            or abs(line_union[3] - text_bounds[3]) > 1e-6
            or abs(measured_width - text_bounds[2]) > 1e-6
            or abs(measured_height - text_bounds[3]) > 1e-6
            or min(
                _number(glyph_measurement.get("fontSizePx")),
                _number(glyph_measurement.get("availableWidthPx")),
                _number(glyph_measurement.get("availableHeightPx")),
                current_scale,
                minimum_scale,
                candidate_scale,
            )
            <= 0
            or candidate_scale < minimum_scale - _EPSILON
            or candidate_scale >= current_scale - _EPSILON
            or 1 - candidate_scale / current_scale
            > _number(glyph_envelope.get("maxFontShrink"), 0.0) + _EPSILON
            or abs(_number(glyph_precondition.get("expectedScale")) - current_scale)
            > _EPSILON
            or glyph_roundtrip.get("supported") is not True
            or "font-size" not in verified_glyph
        ):
            reject(issue, "repair.geometry.glyph_composition.glyph_proof_invalid")
            continue

        moves = tuple(_mapping(value) for value in _sequence(item.get("moves")))
        by_role = {str(move.get("role", "")): move for move in moves}
        if len(moves) != 2 or set(by_role) != {"owner", "subtitle"}:
            reject(issue, "repair.geometry.glyph_composition.moves_invalid")
            continue
        owner_move = by_role["owner"]
        subtitle_move = by_role["subtitle"]
        detector = _mapping(issue.get("detectorEvidence"))
        ancestor = next(
            (
                _mapping(value)
                for value in _sequence(issue.get("boundAncestors"))
                if str(_mapping(value).get("qid", ""))
                == str(owner_move.get("qid", ""))
            ),
            {},
        )
        ancestor_proof = _mapping(ancestor.get("browserProof"))
        ancestor_chain = tuple(
            str(value)
            for value in _sequence(ancestor_proof.get("parentQidChain"))
        )
        owner_bounds = _rect(owner_move.get("currentBounds"))
        subtitle_bounds = _rect(subtitle_move.get("currentBounds"))
        owner_expected = _rect(
            _mapping(owner_move.get("precondition")).get("expectedBounds")
        )
        subtitle_expected = _rect(
            _mapping(subtitle_move.get("precondition")).get("expectedBounds")
        )
        issue_bounds = _rect(issue.get("currentBounds"))
        owner_delta = _mapping(owner_move.get("delta"))
        subtitle_delta = _mapping(subtitle_move.get("delta"))
        owner_delta_px = _mapping(owner_move.get("deltaPx"))
        subtitle_delta_px = _mapping(subtitle_move.get("deltaPx"))
        proof = _mapping(item.get("proof"))
        capacity = _mapping(proof.get("canvasCapacity"))
        viewport_height = _number(capacity.get("viewportHeightPx"))
        owner_envelope = _mapping(owner_move.get("mutationEnvelope"))
        subtitle_envelope = _mapping(subtitle_move.get("mutationEnvelope"))

        def valid_move(
            move: Mapping[str, Any],
            bounds: _Rect | None,
            expected: _Rect | None,
            delta: Mapping[str, Any],
            delta_px: Mapping[str, Any],
            envelope: Mapping[str, Any],
        ) -> bool:
            dx, dy = _number(delta.get("x")), _number(delta.get("y"))
            dx_px, dy_px = _number(delta_px.get("x")), _number(delta_px.get("y"))
            verified = frozenset(
                str(value)
                for value in _sequence(
                    _mapping(move.get("roundTrip")).get("verifiedProperties")
                )
            )
            allowed = frozenset(
                str(value)
                for value in _sequence(envelope.get("allowedOperations"))
            )
            return bool(
                str(move.get("target", ""))
                and str(move.get("target", "")) == str(move.get("layoutId", ""))
                and str(move.get("semanticId", ""))
                and str(move.get("qid", ""))
                and bounds is not None
                and expected == bounds
                and envelope.get("locked") is not True
                and "translateWithinParent" in allowed
                and move.get("staticTranslationSafe") is True
                and _mapping(move.get("roundTrip")).get("supported") is True
                and "bounds" in verified
                and all(math.isfinite(value) for value in (dx, dy, dx_px, dy_px))
                and abs(dx) <= _EPSILON
                and abs(dx_px) <= _EPSILON
                and dy < -_EPSILON
                and abs(dy * viewport_height - dy_px) <= 1e-6
                and abs(dy) <= _number(envelope.get("maxTranslation")) + _EPSILON
                and _inside(bounds.moved(dx, dy), _Rect(0, 0, 1, 1))
            )

        if (
            not math.isfinite(viewport_height)
            or viewport_height <= 0
            or not valid_move(
                owner_move,
                owner_bounds,
                owner_expected,
                owner_delta,
                owner_delta_px,
                owner_envelope,
            )
            or not valid_move(
                subtitle_move,
                subtitle_bounds,
                subtitle_expected,
                subtitle_delta,
                subtitle_delta_px,
                subtitle_envelope,
            )
            or str(owner_move.get("layoutId", ""))
            != str(ancestor.get("layoutId", ""))
            or owner_bounds != _rect(ancestor.get("bounds"))
            or owner_envelope != _mapping(ancestor.get("mutationEnvelope"))
            or ancestor.get("sameCoordinateSpace") is not True
            or ancestor.get("ownershipProven") is not True
            or ancestor_proof.get("source") != "chrome"
            or ancestor_proof.get("telemetryComplete") is not True
            or str(ancestor_proof.get("targetQid", ""))
            != str(issue.get("qid", ""))
            or str(ancestor_proof.get("ancestorQid", ""))
            != str(owner_move.get("qid", ""))
            or len(ancestor_chain) < 2
            or ancestor_chain[0] != str(issue.get("qid", ""))
            or ancestor_chain[-1] != str(owner_move.get("qid", ""))
            or str(subtitle_move.get("qid", "")) != str(issue.get("qid", ""))
            or str(subtitle_move.get("layoutId", "")) != str(issue.get("target", ""))
            or subtitle_bounds != issue_bounds
            or subtitle_envelope != _mapping(issue.get("mutationEnvelope"))
        ):
            reject(issue, "repair.geometry.glyph_composition.move_proof_invalid")
            continue

        baseline_text = _pixel_bounds(proof.get("baselineSubtitleTextBoundsPx"))
        candidate_text = _pixel_bounds(proof.get("candidateSubtitleTextBoundsPx"))
        occluder_text = _pixel_bounds(proof.get("occluderTextBoundsPx"))
        boundary = _mapping(proof.get("occluderPaintBoundaryPx"))
        owner_subtree = _mapping(proof.get("ownerSubtreeBoundsPx"))
        owner_subtree_baseline = _pixel_bounds(owner_subtree.get("baseline"))
        owner_subtree_candidate = _pixel_bounds(owner_subtree.get("candidate"))
        proof_roundtrip = _mapping(proof.get("roundTrip"))
        proof_verified = frozenset(
            str(value)
            for value in _sequence(proof_roundtrip.get("verifiedProperties"))
        )
        postconditions = _mapping(proof.get("requiredPostconditions"))
        soft_cost_maximum = _number(
            postconditions.get("softCostMaximum"), float("nan")
        )
        boundary_value = _number(boundary.get("value"))
        total_delta_px = _number(owner_delta_px.get("y")) + _number(
            subtitle_delta_px.get("y")
        )
        if (
            proof.get("source") != "chrome"
            or proof.get("telemetryComplete") is not True
            or proof.get("candidateResolvesIssue") is not True
            or proof.get("staticTranslationSafe") is not True
            or baseline_text is None
            or candidate_text is None
            or occluder_text is None
            or owner_subtree_baseline is None
            or owner_subtree_candidate is None
            or boundary.get("axis") != "y"
            or not math.isfinite(boundary_value)
            or abs(boundary_value - occluder_text[1]) > 1e-6
            or baseline_text[1] + baseline_text[3] <= boundary_value + 0.5
            or candidate_text[1] + candidate_text[3] > boundary_value - 0.5
            or abs(candidate_text[1] - baseline_text[1] - total_delta_px) > 1e-6
            or any(
                abs(candidate_text[index] - baseline_text[index]) > 1e-6
                for index in (0, 2, 3)
            )
            or abs(
                owner_subtree_candidate[1]
                - owner_subtree_baseline[1]
                - _number(owner_delta_px.get("y"))
            )
            > 1e-6
            or any(
                abs(owner_subtree_candidate[index] - owner_subtree_baseline[index])
                > 1e-6
                for index in (0, 2, 3)
            )
            or abs(_number(capacity.get("topClearancePx")) - owner_subtree_candidate[1])
            > 1e-6
            or abs(
                _number(capacity.get("bottomClearancePx"))
                - (
                    viewport_height
                    - owner_subtree_candidate[1]
                    - owner_subtree_candidate[3]
                )
            )
            > 1e-6
            or min(
                _number(capacity.get("topClearancePx")),
                _number(capacity.get("bottomClearancePx")),
            )
            < -_EPSILON
            or proof_roundtrip.get("supported") is not True
            or not {"bounds", "font-size"}.issubset(proof_verified)
            or postconditions.get("domNoNewIssue") is not True
            or postconditions.get("hardFailure") is not False
            or not math.isfinite(soft_cost_maximum)
            or soft_cost_maximum < 0
            or tuple(_sequence(postconditions.get("unknownHard")))
            or tuple(_sequence(postconditions.get("roundTripErrors")))
            or abs(baseline_text[0] - _number(detector.get("targetBoundsPx", {}).get("x")))
            > 0.5
            or abs(baseline_text[1] - _number(detector.get("targetBoundsPx", {}).get("y")))
            > 0.5
        ):
            reject(issue, "repair.geometry.glyph_composition.chrome_proof_invalid")
            continue

        owner_after = owner_bounds.moved(
            _number(owner_delta.get("x")), _number(owner_delta.get("y"))
        )
        subtitle_after = subtitle_bounds.moved(
            _number(subtitle_delta.get("x")), _number(subtitle_delta.get("y"))
        )
        owner_operations = _operation_for_bounds(
            target=str(owner_move["layoutId"]),
            current=owner_bounds,
            candidate=owner_after,
            envelope=owner_envelope,
            expected_bounds=owner_move.get("currentBounds"),
        )
        subtitle_operations = _operation_for_bounds(
            target=str(subtitle_move["layoutId"]),
            current=subtitle_bounds,
            candidate=subtitle_after,
            envelope=subtitle_envelope,
            expected_bounds=subtitle_move.get("currentBounds"),
        )
        if (
            owner_operations is None
            or subtitle_operations is None
            or len(owner_operations) != 1
            or len(subtitle_operations) != 1
        ):
            reject(issue, "repair.geometry.glyph_composition.envelope_rejected")
            continue
        base = _candidate(issue, ())
        operations = (
            {
                "op": "setFontScale",
                "target": glyph_target,
                "scale": round(candidate_scale, 9),
                "bounds": {
                    "minScale": minimum_scale,
                    "maxScale": current_scale,
                },
                "precondition": {"currentScale": current_scale},
            },
            *owner_operations,
            *subtitle_operations,
        )
        refs = tuple(
            dict.fromkeys(
                (
                    *base.diagnostic_refs,
                    f"composition:{item.get('compositionId', '')}",
                    f"issue-group:{group_ids[0]}",
                    "geometry-glyph-composition:true",
                    "chrome-candidate-proof:true",
                    f"glyph-qid:{glyph.get('qid', '')}",
                    f"glyph-target:{glyph_target}",
                    f"owner-qid:{owner_move.get('qid', '')}",
                    f"owner-target:{owner_move.get('layoutId', '')}",
                )
            )
        )
        candidates.append(
            GeometryCandidate(
                group_ids[0],
                base.issue_code,
                base.semantic_id,
                base.qid,
                base.constraint_id,
                "geometryGlyphComposition",
                base.target,
                operations,
                refs,
            )
        )
    return candidates, tuple(rejected)


def _exact_geometry_composition_candidates(
    context: Mapping[str, Any],
    issues: Sequence[Mapping[str, Any]],
) -> tuple[list[GeometryCandidate], tuple[tuple[Mapping[str, Any], str], ...]]:
    """Validate producer-supplied exact Chrome translation compositions."""

    raw_items = tuple(
        _mapping(value)
        for value in _sequence(context.get("geometryExactCompositions"))
    )
    if not raw_items:
        return [], ()
    limits = _mapping(context.get("limits"))
    max_operations = int(max(1, min(32, _number(limits.get("maxOperations"), 8))))
    max_affected = int(
        max(
            1,
            min(
                32,
                _number(
                    limits.get(
                        "maxAffectedBlocks", limits.get("maxTouchedLayoutNodes")
                    ),
                    4,
                ),
            ),
        )
    )
    required_groups = frozenset(
        str(value)
        for value in _sequence(context.get("requiredGeometryIssueGroups"))
    )
    candidates: list[GeometryCandidate] = []
    rejected: list[tuple[Mapping[str, Any], str]] = []

    for item in sorted(
        raw_items,
        key=lambda value: json.dumps(value, sort_keys=True, separators=(",", ":")),
    ):
        group_ids = tuple(
            dict.fromkeys(
                str(value)
                for value in _sequence(item.get("issueGroupIds"))
                if value
            )
        )
        diagnostic_refs = tuple(
            dict.fromkeys(
                str(value)
                for value in _sequence(item.get("diagnosticRefs"))
                if value
            )
        )
        referenced_groups = frozenset(
            value.removeprefix("issue-group:")
            for value in diagnostic_refs
            if value.startswith("issue-group:")
        )
        effective_groups = frozenset((*group_ids, *referenced_groups))

        def issue_is_bound(issue: Mapping[str, Any]) -> bool:
            group_id = str(issue.get("issueGroupId", ""))
            if group_id and group_id in effective_groups:
                return True
            qid_ref = f"qid:{issue.get('qid', '')}"
            target_ref = f"target:{issue.get('target', '')}"
            return qid_ref in diagnostic_refs and target_ref in diagnostic_refs

        matching = tuple(issue for issue in issues if issue_is_bound(issue))
        fallback_issue: Mapping[str, Any] = matching[0] if matching else {}
        operations_raw = tuple(
            _mapping(value) for value in _sequence(item.get("operations"))
        )
        if item.get("required") is not True:
            continue
        if (
            not str(item.get("compositionId", ""))
            or not diagnostic_refs
            or not matching
            or len(operations_raw) < 1
            or len(operations_raw) > 4
            or len(operations_raw) > max_operations
            or len(operations_raw) > max_affected
            or (group_ids and not set(group_ids).intersection(required_groups))
            or (
                not group_ids
                and effective_groups
                and not effective_groups.intersection(required_groups)
            )
        ):
            rejected.append(
                (
                    fallback_issue,
                    "repair.geometry.exact_composition.identity_or_limits_invalid",
                )
            )
            continue

        proof = _mapping(item.get("proof"))
        baseline_bounds_raw = _mapping(proof.get("baselineBoundsPx"))
        candidate_bounds_raw = _mapping(proof.get("candidateBoundsPx"))
        baseline_bounds_px = {
            str(qid): bounds
            for qid, value in baseline_bounds_raw.items()
            if (bounds := _pixel_bounds(value)) is not None
        }
        candidate_bounds_px = {
            str(qid): bounds
            for qid, value in candidate_bounds_raw.items()
            if (bounds := _pixel_bounds(value)) is not None
        }
        built_operations: list[dict[str, Any]] = []
        viewport_scales: list[float] = []
        roles: set[str] = set()
        targets: set[str] = set()
        qids: set[str] = set()
        operation_invalid = False
        for operation in operations_raw:
            op_kind = str(operation.get("op", ""))
            role = str(operation.get("role", ""))
            target = str(operation.get("target", ""))
            qid = str(operation.get("qid", ""))
            current = _rect(operation.get("currentBounds"))
            expected = _rect(
                _mapping(operation.get("precondition")).get("expectedBounds")
            )
            value = _mapping(operation.get("value"))
            value_px = _mapping(operation.get("valuePx"))
            dx, dy = _number(value.get("x")), _number(value.get("y"))
            dx_px, dy_px = _number(value_px.get("x")), _number(value_px.get("y"))
            candidate_bounds = _rect(operation.get("bounds"))
            candidate_operation_px = _pixel_bounds(operation.get("boundsPx"))
            envelope = _mapping(operation.get("mutationEnvelope"))
            allowed = frozenset(
                str(value)
                for value in _sequence(envelope.get("allowedOperations"))
            )
            projection = _mapping(operation.get("projectionProof"))
            mechanism = str(projection.get("mechanism", ""))
            roundtrip = _mapping(operation.get("roundTrip"))
            verified = frozenset(
                str(value)
                for value in _sequence(roundtrip.get("verifiedProperties"))
            )
            baseline_px = baseline_bounds_px.get(qid)
            candidate_px = candidate_bounds_px.get(qid)
            if op_kind == "translateWithinParent":
                if abs(dy) > _EPSILON:
                    viewport_scales.append(abs(dy_px / dy))
                elif abs(dx) > _EPSILON:
                    viewport_scales.append(abs(dx_px / dx))
                operation_specific_invalid = (
                    "translateWithinParent" not in allowed
                    or not all(math.isfinite(value) for value in (dx, dy, dx_px, dy_px))
                    or (abs(dx) <= _EPSILON and abs(dy) <= _EPSILON)
                    or abs(dx) > _number(envelope.get("maxTranslation")) + _EPSILON
                    or abs(dy) > _number(envelope.get("maxTranslation")) + _EPSILON
                    or not _inside(current.moved(dx, dy), _Rect(0, 0, 1, 1))
                    or baseline_px is None
                    or candidate_px is None
                    or abs(candidate_px[0] - baseline_px[0] - dx_px) > 1e-6
                    or abs(candidate_px[1] - baseline_px[1] - dy_px) > 1e-6
                    or abs(candidate_px[2] - baseline_px[2]) > 1e-6
                    or abs(candidate_px[3] - baseline_px[3]) > 1e-6
                )
                generated_candidate = current.moved(dx, dy)
            elif op_kind == "setBounds":
                if (
                    current is not None
                    and candidate_bounds is not None
                    and baseline_px is not None
                    and candidate_operation_px is not None
                ):
                    for normalized_delta, pixel_delta in (
                        (candidate_bounds.x - current.x, candidate_operation_px[0] - baseline_px[0]),
                        (candidate_bounds.y - current.y, candidate_operation_px[1] - baseline_px[1]),
                        (candidate_bounds.width - current.width, candidate_operation_px[2] - baseline_px[2]),
                        (candidate_bounds.height - current.height, candidate_operation_px[3] - baseline_px[3]),
                    ):
                        if abs(normalized_delta) > _EPSILON:
                            viewport_scales.append(abs(pixel_delta / normalized_delta))
                position_delta = (
                    max(
                        abs(candidate_bounds.x - current.x),
                        abs(candidate_bounds.y - current.y),
                    )
                    if current is not None and candidate_bounds is not None
                    else float("inf")
                )
                size_delta = (
                    max(
                        abs(candidate_bounds.width - current.width),
                        abs(candidate_bounds.height - current.height),
                    )
                    if current is not None and candidate_bounds is not None
                    else float("inf")
                )
                operation_specific_invalid = (
                    "setBounds" not in allowed
                    or envelope.get("lockSize") is True
                    or candidate_bounds is None
                    or candidate_operation_px is None
                    or baseline_px is None
                    or candidate_px is None
                    or position_delta
                    > _number(envelope.get("maxTranslation")) + _EPSILON
                    or size_delta > _number(envelope.get("maxResizeDelta")) + _EPSILON
                    or not _inside(candidate_bounds, _Rect(0, 0, 1, 1))
                    or any(
                        abs(actual - proved) > 1e-6
                        for actual, proved in zip(
                            candidate_px,
                            candidate_operation_px,
                            strict=True,
                        )
                    )
                )
                generated_candidate = candidate_bounds
            else:
                operation_specific_invalid = True
                generated_candidate = None
            if (
                not role
                or not target
                or target != str(operation.get("layoutId", ""))
                or not str(operation.get("semanticId", ""))
                or not qid
                or role in roles
                or target in targets
                or qid in qids
                or current is None
                or expected != current
                or envelope.get("locked") is True
                or operation_specific_invalid
                or projection.get("source") != "chrome"
                or projection.get("telemetryComplete") is not True
                or mechanism not in {"static-transform", "position-anchor"}
                or (
                    mechanism == "static-transform"
                    and projection.get("staticTranslationSafe") is not True
                )
                or (
                    mechanism == "position-anchor"
                    and (
                        str(projection.get("position", ""))
                        not in {"absolute", "fixed", "relative", "sticky"}
                        or str(projection.get("verticalAnchor", ""))
                        not in {"top", "bottom"}
                    )
                )
                or roundtrip.get("supported") is not True
                or "bounds" not in verified
            ):
                operation_invalid = True
                break
            generated = _operation_for_bounds(
                target=target,
                current=current,
                candidate=generated_candidate,
                envelope=envelope,
                expected_bounds=operation.get("currentBounds"),
            )
            if generated is None or len(generated) != 1:
                operation_invalid = True
                break
            built_operations.extend(generated)
            roles.add(role)
            targets.add(target)
            qids.add(qid)
        if (
            operation_invalid
            or len(built_operations) != len(operations_raw)
            or not viewport_scales
            or not all(math.isfinite(value) and value > 0 for value in viewport_scales)
            or max(viewport_scales) - min(viewport_scales) > 1e-6
        ):
            rejected.append(
                (
                    fallback_issue,
                    "repair.geometry.exact_composition.operation_proof_invalid",
                )
            )
            continue

        relationships = tuple(
            _mapping(value) for value in _sequence(proof.get("relationships"))
        )

        def vertical_gap(
            first: tuple[float, float, float, float],
            second: tuple[float, float, float, float],
        ) -> float:
            if first[1] <= second[1]:
                return second[1] - first[1] - first[3]
            return first[1] - second[1] - second[3]

        relationship_invalid = not relationships
        for relationship in relationships:
            kind = str(relationship.get("kind", ""))
            axis = str(relationship.get("axis", ""))
            first_qid = str(relationship.get("firstQid", ""))
            second_qid = str(relationship.get("secondQid", ""))
            baseline_first = baseline_bounds_px.get(first_qid)
            candidate_first = candidate_bounds_px.get(first_qid)
            if axis != "y" or baseline_first is None or candidate_first is None:
                relationship_invalid = True
                break
            if kind == "canvasContainment":
                boundary = _number(relationship.get("boundaryPx"), float("nan"))
                if not math.isfinite(boundary):
                    relationship_invalid = True
                    break
                if boundary <= candidate_first[1] + candidate_first[3] / 2:
                    contained = candidate_first[1] >= boundary - 1e-6
                else:
                    contained = (
                        candidate_first[1] + candidate_first[3]
                        <= boundary + 1e-6
                    )
                if not contained:
                    relationship_invalid = True
                    break
            elif kind in {"adjacency", "nonOverlap"}:
                baseline_second = baseline_bounds_px.get(second_qid)
                candidate_second = candidate_bounds_px.get(second_qid)
                if baseline_second is None or candidate_second is None:
                    relationship_invalid = True
                    break
                baseline_gap = vertical_gap(baseline_first, baseline_second)
                candidate_gap = vertical_gap(candidate_first, candidate_second)
                has_baseline_gap = "baselineGapPx" in relationship
                declared_baseline = _number(relationship.get("baselineGapPx"))
                declared_candidate = _number(
                    relationship.get("candidateGapPx"), float("nan")
                )
                if (
                    not math.isfinite(declared_candidate)
                    or (
                        has_baseline_gap
                        and (
                            not math.isfinite(declared_baseline)
                            or abs(declared_baseline - baseline_gap) > 1e-6
                        )
                    )
                    or abs(declared_candidate - candidate_gap) > 1e-6
                    or (kind == "nonOverlap" and candidate_gap < -1e-6)
                ):
                    relationship_invalid = True
                    break
            else:
                relationship_invalid = True
                break

        postconditions = _mapping(proof.get("requiredPostconditions"))
        soft_cost_maximum = _number(
            postconditions.get("softCostMaximum"), float("nan")
        )
        if (
            proof.get("source") != "chrome"
            or proof.get("telemetryComplete") is not True
            or proof.get("candidateResolvesIssue") is not True
            or set(baseline_bounds_px) != set(candidate_bounds_px)
            or not qids.issubset(baseline_bounds_px)
            or relationship_invalid
            or postconditions.get("domNoNewIssue") is not True
            or postconditions.get("hardFailure") is not False
            or not math.isfinite(soft_cost_maximum)
            or soft_cost_maximum < 0
            or tuple(_sequence(postconditions.get("unknownHard")))
            or tuple(_sequence(postconditions.get("roundTripErrors")))
        ):
            rejected.append(
                (
                    fallback_issue,
                    "repair.geometry.exact_composition.chrome_proof_invalid",
                )
            )
            continue

        base = _candidate(fallback_issue, ())
        refs = tuple(
            dict.fromkeys(
                (
                    *base.diagnostic_refs,
                    f"composition:{item.get('compositionId', '')}",
                    *(f"issue-group:{group_id}" for group_id in group_ids),
                    *diagnostic_refs,
                    "geometry-exact-composition:true",
                    "chrome-candidate-proof:true",
                )
            )
        )
        if len(refs) > 64:
            rejected.append(
                (
                    fallback_issue,
                    "repair.geometry.exact_composition.diagnostics_exceeded",
                )
            )
            continue
        candidates.append(
            GeometryCandidate(
                group_ids[0]
                if group_ids
                else str(fallback_issue.get("issueGroupId", ""))
                or f"composition:{item.get('compositionId', '')}",
                base.issue_code,
                base.semantic_id,
                base.qid,
                base.constraint_id,
                "geometryExactComposition",
                base.target,
                tuple(built_operations),
                refs,
            )
        )
    return candidates, tuple(rejected)


def _candidate_key(candidate: GeometryCandidate) -> tuple[int, float, int, str]:
    delta = 0.0
    for operation in candidate.operations:
        expected = _rect(_mapping(operation.get("precondition")).get("expectedBounds"))
        proposed = _rect(operation.get("bounds"))
        value = _mapping(operation.get("value"))
        if expected is not None and proposed is not None:
            delta += sum(
                abs(getattr(proposed, field) - getattr(expected, field))
                for field in ("x", "y", "width", "height")
            )
        elif operation.get("op") == "translateWithinParent":
            delta += abs(_number(value.get("x"), 0.0)) + abs(_number(value.get("y"), 0.0))
        elif operation.get("op") == "resizeWithinBounds" and expected is not None:
            delta += abs(_number(operation.get("width")) - expected.width)
            delta += abs(_number(operation.get("height")) - expected.height)
        elif operation.get("op") == "setFontScale":
            delta += max(0.0, 1 - _number(operation.get("scale"), 1.0))
    encoded = json.dumps(candidate.operations, sort_keys=True, separators=(",", ":"))
    issue_group_count = len(
        {
            reference
            for reference in candidate.diagnostic_refs
            if reference.startswith("issue-group:")
        }
    )
    has_proved_single_owner = any(
        reference.startswith("owner-role:")
        for reference in candidate.diagnostic_refs
    )
    if candidate.kind in {"geometryGlyphComposition", "geometryExactComposition"}:
        proof_priority = -1
    elif candidate.kind == "coordinatedAncestorMoves" and issue_group_count > 1:
        proof_priority = 0
    elif has_proved_single_owner:
        proof_priority = 1
    elif candidate.kind == "coordinatedAncestorMoves":
        proof_priority = 2
    else:
        proof_priority = 3
    return proof_priority, round(delta, 9), len(candidate.operations), encoded


def _compose_candidate(
    composition_id: str,
    members: Sequence[GeometryCandidate],
    *,
    max_operations: int,
    max_affected_blocks: int,
) -> GeometryCandidate | None:
    operations: list[dict[str, Any]] = []
    by_target: dict[str, str] = {}
    for member in members:
        for operation in member.operations:
            target = str(operation.get("target", ""))
            encoded = json.dumps(operation, sort_keys=True, separators=(",", ":"))
            if target in by_target:
                if by_target[target] == encoded:
                    continue
                return None
            by_target[target] = encoded
            operations.append(operation)
    if (
        not operations
        or len(operations) > max_operations
        or len(by_target) > max_affected_blocks
    ):
        return None
    refs = tuple(
        dict.fromkeys(
            (
                f"composition:{composition_id}",
                *(reference for member in members for reference in member.diagnostic_refs),
            )
        )
    )
    if len(refs) > 64:
        return None
    first = members[0]
    return GeometryCandidate(
        f"composition:{composition_id}",
        first.issue_code,
        first.semantic_id,
        first.qid,
        first.constraint_id,
        "composition",
        first.target,
        tuple(operations),
        refs,
    )


def _composition_candidates(
    context: Mapping[str, Any],
    groups: Mapping[str, Sequence[GeometryCandidate]],
) -> tuple[GeometryCandidate, ...]:
    """Bounded best-first products for explicitly authorized issue groups."""

    raw_compositions = _mapping(context.get("geometryCompositionGroups"))
    required_groups = frozenset(
        str(value)
        for value in _sequence(context.get("requiredGeometryIssueGroups"))
    )
    if not raw_compositions or not required_groups:
        return ()
    limits = _mapping(context.get("limits"))
    max_operations = int(max(1, min(32, _number(limits.get("maxOperations"), 8))))
    max_affected = int(
        max(
            1,
            min(
                32,
                _number(
                    limits.get(
                        "maxAffectedBlocks", limits.get("maxTouchedLayoutNodes")
                    ),
                    4,
                ),
            ),
        )
    )
    beam_width = int(
        max(1, min(32, _number(context.get("maxGeometryCompositions"), 16)))
    )
    result: list[GeometryCandidate] = []
    for composition_id, raw_group_ids in sorted(
        raw_compositions.items(), key=lambda item: str(item[0])
    ):
        group_ids = tuple(dict.fromkeys(str(value) for value in _sequence(raw_group_ids)))
        if (
            len(group_ids) < 2
            or not set(group_ids).issubset(required_groups)
            or any(group_id not in groups for group_id in group_ids)
        ):
            continue
        states: list[tuple[GeometryCandidate, ...]] = [()]
        for group_id in group_ids:
            options = sorted(groups[group_id], key=_candidate_key)[:beam_width]
            next_states: dict[str, tuple[GeometryCandidate, ...]] = {}
            for state in states:
                for option in options:
                    members = (*state, option)
                    combined = _compose_candidate(
                        str(composition_id),
                        members,
                        max_operations=max_operations,
                        max_affected_blocks=max_affected,
                    )
                    if combined is None:
                        continue
                    signature = json.dumps(
                        combined.operations, sort_keys=True, separators=(",", ":")
                    )
                    next_states.setdefault(signature, members)
            states = sorted(
                next_states.values(),
                key=lambda members: _candidate_key(
                    _compose_candidate(
                        str(composition_id),
                        members,
                        max_operations=max_operations,
                        max_affected_blocks=max_affected,
                    )
                    or members[0]
                ),
            )[:beam_width]
            if not states:
                break
        for members in states:
            combined = _compose_candidate(
                str(composition_id),
                members,
                max_operations=max_operations,
                max_affected_blocks=max_affected,
            )
            if combined is not None and len(members) == len(group_ids):
                result.append(combined)
    unique = {
        (
            candidate.issue_group_id,
            json.dumps(candidate.operations, sort_keys=True, separators=(",", ":")),
        ): candidate
        for candidate in result
    }
    ordered = sorted(
        unique.values(),
        key=lambda candidate: (
            _candidate_key(candidate),
            candidate.issue_group_id,
        ),
    )
    coverage: list[GeometryCandidate] = []
    covered: set[str] = set()
    for candidate in sorted(
        ordered,
        key=lambda item: (item.issue_group_id, _candidate_key(item)),
    ):
        if candidate.issue_group_id in covered:
            continue
        covered.add(candidate.issue_group_id)
        coverage.append(candidate)
    coverage_ids = {id(candidate) for candidate in coverage}
    return tuple(
        [
            *coverage,
            *(candidate for candidate in ordered if id(candidate) not in coverage_ids),
        ][:beam_width]
    )


def generate_geometry_candidates(
    context: Mapping[str, Any],
) -> GeometryCandidateBuildResult:
    """Generate bounded alternatives in stable minimum-change order."""

    raw_issues = context.get("geometryIssues", ())
    issues = tuple(_mapping(value) for value in _sequence(raw_issues))
    candidate_groups: dict[str, list[GeometryCandidate]] = {}
    rejections: list[GeometryCandidateRejection] = []
    maximum = int(max(1, min(32, _number(context.get("maxGeometryCandidates"), 16))))

    def reject(issue: Mapping[str, Any], operation: str, code: str) -> None:
        detector = _mapping(issue.get("detectorEvidence"))
        rejections.append(
            GeometryCandidateRejection(
                str(issue.get("code", "")),
                {
                    "semantic_id": str(issue.get("semanticId", "")),
                    "qid": str(issue.get("qid", "")),
                    "layout_id": str(issue.get("target", "")),
                },
                str(issue["constraintId"]) if issue.get("constraintId") else None,
                str(issue.get("kind", "")),
                operation,
                code,
                "candidate_generation",
                str(detector.get("metric", "unknown")),
                dict(detector),
            )
        )

    ordered = sorted(
        issues,
        key=lambda issue: (
            str(issue.get("constraintId", "")),
            str(issue.get("code", "")),
            str(issue.get("semanticId", "")),
            str(issue.get("qid", "")),
            str(issue.get("target", "")),
        ),
    )
    for issue in ordered:
        issue_code = str(issue.get("code", ""))
        semantic_id = str(issue.get("semanticId", ""))
        qid = str(issue.get("qid", ""))
        kind = str(issue.get("kind", ""))
        target = str(issue.get("target", ""))
        constraint_id = str(issue.get("constraintId", ""))
        if not issue_code or not semantic_id or not qid or not target:
            reject(issue, "candidate_generation", "repair.geometry.identity_missing")
            continue
        if _mapping(issue.get("detectorEvidence")).get("confirmed") is not True:
            reject(
                issue,
                "candidate_generation",
                "repair.geometry.detector_evidence_unconfirmed",
            )
            continue
        if (
            kind == "nonOverlap"
            and _mapping(issue.get("detectorEvidence")).get("independentElements")
            is not True
        ):
            reject(
                issue,
                "candidate_generation",
                "repair.geometry.non_overlap_independence_unproven",
            )
            continue
        relational_kinds = {
            "anchor", "alignment", "gap", "containment", "nonOverlap", "order"
        }
        if kind in relational_kinds and not constraint_id:
            reject(issue, "candidate_generation", "repair.geometry.constraint_id_missing")
            continue
        if issue.get("sameCoordinateSpace") is not True:
            reject(issue, "candidate_generation", "repair.geometry.coordinate_space_unproven")
            continue
        envelope = _envelope(issue)
        resize_isolation_unproven = (
            kind in {"overflow", "readability"}
            and issue.get("requiresEnvelope") is True
            and envelope.get("lockSize") is True
            and not {
                "setBounds",
                "resizeWithinBounds",
            }.intersection(str(value) for value in _sequence(envelope.get("allowedOperations")))
        )
        if resize_isolation_unproven:
            reject(
                issue,
                "candidate_generation",
                "repair.mutation_disallowed.resize_flow_unproven",
            )
            continue
        has_ancestor_input = bool(
            _sequence(issue.get("boundAncestors"))
            or _sequence(issue.get("otherBoundAncestors"))
        )
        has_coordinated_input = bool(
            _sequence(issue.get("coordinatedAncestorMoves"))
        )
        has_owner_input = has_ancestor_input or has_coordinated_input
        leaf_mutable = True
        if issue.get("requiresEnvelope") is True and not _envelope(issue):
            reject(issue, "candidate_generation", "repair.mutation_envelope.missing_or_locked")
            leaf_mutable = False
            if not has_owner_input:
                continue
        current = _rect(issue.get("currentBounds"))
        if current is None:
            reject(issue, "candidate_generation", "repair.geometry.current_bounds_invalid")
            continue
        if bool(_envelope(issue).get("locked", False)):
            reject(issue, "candidate_generation", "repair.mutation_envelope.missing_or_locked")
            leaf_mutable = False
            if not has_owner_input:
                continue

        generated: list[GeometryCandidate]
        other = _rect(issue.get("otherBounds"))
        if _relational_issue_present(issue, current, other) is False:
            reject(
                issue,
                "candidate_generation",
                "repair.geometry.baseline_issue_not_present",
            )
            continue
        coordinated, coordinated_reasons = _coordinated_ancestor_moves(issue, context)
        for reason in coordinated_reasons:
            reject(issue, "coordinated_ancestor", reason)
        if has_coordinated_input:
            # Presence of this closed proof means the aggregate owner overlap
            # may require both moves.  Never fall back to leaf geometry, but an
            # independently Chrome-proved ancestor delta with
            # ``resolvesIssue=true`` remains a valid exact-issue candidate.  In
            # particular, an unusable pair for one occluder must not suppress a
            # single owner move that clears the complete issue paint set.
            generated = coordinated
        elif kind == "nonOverlap" and other is not None:
            generated = _non_overlap(issue, current, other) if leaf_mutable else []
        elif kind == "containment":
            generated = _containment(issue, current) if leaf_mutable else []
        elif kind in {"gap", "order"} and other is not None:
            generated = _gap_or_order(issue, current, other) if leaf_mutable else []
        elif kind in {"alignment", "anchor"} and other is not None:
            generated = (
                _alignment_or_anchor(issue, current, other) if leaf_mutable else []
            )
        elif kind in {"overflow", "readability"}:
            generated = _overflow(issue, current) if leaf_mutable else []
        elif kind not in {
            "nonOverlap", "containment", "gap", "order", "alignment", "anchor",
            "overflow", "readability",
        }:
            reject(issue, "candidate_generation", "repair.geometry.kind_unsupported")
            continue
        elif not has_ancestor_input:
            reject(issue, "candidate_generation", "repair.geometry.peer_bounds_missing")
            continue
        else:
            generated = []

        promoted, promotion_reasons = _ancestor_promotions(issue)
        generated.extend(promoted)
        for reason in promotion_reasons:
            reject(issue, "ancestor_promotion", reason)

        unique: dict[str, GeometryCandidate] = {}
        for candidate in generated:
            key = json.dumps(candidate.operations, sort_keys=True, separators=(",", ":"))
            unique.setdefault(key, candidate)
        selected = sorted(unique.values(), key=_candidate_key)
        if not selected:
            reject(issue, "candidate_generation", "repair.geometry.no_envelope_compliant_candidate")
            continue
        group_key = selected[0].issue_group_id
        candidate_groups.setdefault(group_key, []).extend(selected)

    glyph_compositions, glyph_rejections = _glyph_composition_candidates(
        context, ordered
    )
    for candidate in glyph_compositions:
        candidate_groups.setdefault(candidate.issue_group_id, []).append(candidate)
    for issue, reason in glyph_rejections:
        reject(issue, "glyph_composition", reason)
    exact_compositions, exact_rejections = _exact_geometry_composition_candidates(
        context, ordered
    )
    for candidate in exact_compositions:
        candidate_groups.setdefault(candidate.issue_group_id, []).append(candidate)
    for issue, reason in exact_rejections:
        reject(issue, "exact_composition", reason)

    ordered_groups = tuple(
        sorted(
            (
                key,
                sorted(
                    {
                        json.dumps(
                            candidate.operations,
                            sort_keys=True,
                            separators=(",", ":"),
                        ): candidate
                        for candidate in values
                    }.values(),
                    key=_candidate_key,
                ),
            )
            for key, values in candidate_groups.items()
        )
    )
    # Preserve coverage first: every confirmed issue receives its cheapest
    # candidate before any issue consumes the bounded budget with alternatives.
    atomic_coverage = [values[0] for _, values in ordered_groups]
    atomic_remaining = sorted(
        (candidate for _, values in ordered_groups for candidate in values[1:]),
        key=lambda item: (
            _candidate_key(item),
            item.constraint_id or "",
            item.issue_code,
            item.semantic_id,
            item.qid,
        ),
    )
    compositions = _composition_candidates(context, candidate_groups)
    composition_coverage: list[GeometryCandidate] = []
    covered_compositions: set[str] = set()
    for candidate in sorted(
        compositions,
        key=lambda item: (item.issue_group_id, _candidate_key(item)),
    ):
        if candidate.issue_group_id in covered_compositions:
            continue
        covered_compositions.add(candidate.issue_group_id)
        composition_coverage.append(candidate)
    covered_candidate_ids = {id(candidate) for candidate in composition_coverage}
    composition_remaining = sorted(
        (
            candidate
            for candidate in compositions
            if id(candidate) not in covered_candidate_ids
        ),
        key=lambda item: (_candidate_key(item), item.issue_group_id),
    )
    # ``maxGeometryCandidates`` is a cap on the complete generator result, not
    # only on atomic alternatives.  Reserve one cheapest candidate per
    # explicitly required composition first: those are the only candidates
    # capable of cleaning all connected issue groups in a single apply cycle.
    candidates = [
        *composition_coverage,
        *atomic_coverage,
        *composition_remaining,
        *atomic_remaining,
    ][:maximum]
    return GeometryCandidateBuildResult(tuple(candidates), tuple(rejections))


__all__ = [
    "GeometryCandidate",
    "GeometryCandidateBuildResult",
    "GeometryCandidateRejection",
    "generate_geometry_candidates",
]
