"""Connectivity, coverage, and bounded composition grouping."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from functools import partial
from typing import Any

from services.slidespec_pipeline_performance import digest_parts
from services.slidespec_repair_context.artifacts import RepairArtifacts
from services.slidespec_repair_context.owner_pairs import qid_chain_to_owner as _qid_chain_to_owner
from services.slidespec_repair_context.policy import MAX_GEOMETRY_COMPOSITIONS
from services.slidespec_repair_context.state import RepairContextState


def build_composition_groups(state: RepairContextState, artifacts: RepairArtifacts) -> None:
    source = state.source
    evidence = state.evidence
    finite_number = state.finite_number
    envelope_can_change = state.envelope_can_change
    metric_qids_related = state.metric_qids_related
    metric_qid_is_ancestor = state.metric_qid_is_ancestor
    qid_for_semantic = state.qid_for_semantic
    qid_chain_to_owner = partial(_qid_chain_to_owner, state)
    geometry_issues = artifacts.geometry_issues
    composition_qids = artifacts.composition_qids
    issue_endpoints = {
        str(issue["issueGroupId"]): tuple(
            dict.fromkeys(
                str(value)
                for value in (
                    issue.get("qid"),
                    (
                        issue.get("detectorEvidence", {}).get("occluderQid")
                        if isinstance(issue.get("detectorEvidence"), Mapping)
                        else None
                    ),
                    (
                        issue.get("detectorEvidence", {}).get("occluderBindingQid")
                        if isinstance(issue.get("detectorEvidence"), Mapping)
                        else None
                    ),
                )
                if value
            )
        )
        for issue in geometry_issues
    }
    coverage_signal_endpoints = tuple(
        tuple(
            dict.fromkeys(
                qid
                for qid in (
                    defect.qid or qid_for_semantic(defect.semantic_id),
                    *(qid_for_semantic(semantic_id) for semantic_id in defect.related_semantic_ids),
                )
                if qid
            )
        )
        for defect in evidence.defects
        if defect.code == "dom.unintended_overlap" and defect.related_semantic_ids
    )

    def connected_issue_groups(seed: str) -> tuple[str, ...]:
        remaining_groups = set(issue_endpoints) - {seed}
        connected = {seed}
        frontier = [seed]
        while frontier:
            current = frontier.pop()
            neighbors = {
                candidate
                for candidate in remaining_groups
                if any(
                    metric_qids_related(left, right)
                    for left in issue_endpoints.get(current, ())
                    for right in issue_endpoints.get(candidate, ())
                )
            }
            remaining_groups.difference_update(neighbors)
            connected.update(neighbors)
            frontier.extend(sorted(neighbors))
        return tuple(sorted(connected))

    for issue in geometry_issues:
        raw_pairs = issue.get("coordinatedAncestorMoves", ())
        if not isinstance(raw_pairs, Sequence) or isinstance(raw_pairs, (str, bytes)):
            continue
        for pair in raw_pairs:
            if not isinstance(pair, Mapping):
                continue
            proof = pair.get("proof")
            proof = proof if isinstance(proof, dict) else None
            if proof is None:
                continue
            coverage = proof.get("ownerCoverage")
            coverage = coverage if isinstance(coverage, dict) else None
            if coverage is None:
                continue
            target_coverage = coverage.get("target")
            peer_coverage = coverage.get("peer")
            target_coverage = target_coverage if isinstance(target_coverage, dict) else None
            peer_coverage = peer_coverage if isinstance(peer_coverage, dict) else None
            if target_coverage is None or peer_coverage is None:
                continue
            target_owner = str(target_coverage.get("ownerQid", ""))
            peer_owner = str(peer_coverage.get("ownerQid", ""))
            component = connected_issue_groups(str(issue["issueGroupId"]))
            connected_qids = tuple(
                dict.fromkeys(qid for group_id in component for qid in issue_endpoints.get(group_id, ()))
            )
            closure = set(connected_qids)
            changed = True
            while changed:
                changed = False
                for signal in coverage_signal_endpoints:
                    if not any(
                        metric_qids_related(signal_qid, connected_qid)
                        for signal_qid in signal
                        for connected_qid in closure
                    ):
                        continue
                    before = len(closure)
                    closure.update(signal)
                    changed = changed or len(closure) != before
            connected_qids = tuple(sorted(closure))
            target_owned = tuple(qid for qid in connected_qids if metric_qid_is_ancestor(target_owner, qid))
            peer_owned = tuple(qid for qid in connected_qids if metric_qid_is_ancestor(peer_owner, qid))
            unowned = tuple(qid for qid in connected_qids if qid not in target_owned and qid not in peer_owned)
            target_coverage["ownedQids"] = target_owned
            target_coverage["ancestorQidChains"] = {qid: qid_chain_to_owner(qid, target_owner) for qid in target_owned}
            peer_coverage["ownedQids"] = peer_owned
            peer_coverage["ancestorQidChains"] = {qid: qid_chain_to_owner(qid, peer_owner) for qid in peer_owned}
            coverage["connectedIssueQids"] = connected_qids
            coverage["unownedConnectedIssueQids"] = unowned
            covered: list[str] = []
            for candidate_issue in geometry_issues:
                candidate_detector = candidate_issue.get("detectorEvidence")
                candidate_detector = candidate_detector if isinstance(candidate_detector, Mapping) else {}
                candidate_target = str(candidate_issue.get("qid", ""))
                candidate_peer = str(
                    candidate_detector.get("occluderQid") or candidate_detector.get("occluderBindingQid") or ""
                )
                if not candidate_target or not candidate_peer:
                    continue
                split_across_owners = (
                    metric_qid_is_ancestor(target_owner, candidate_target)
                    and metric_qid_is_ancestor(peer_owner, candidate_peer)
                ) or (
                    metric_qid_is_ancestor(peer_owner, candidate_target)
                    and metric_qid_is_ancestor(target_owner, candidate_peer)
                )
                if split_across_owners:
                    covered.append(str(candidate_issue["issueGroupId"]))
            proof["coveredIssueGroupIds"] = tuple(dict.fromkeys(sorted(covered)))

    composable_groups = [
        str(issue["issueGroupId"])
        for issue in geometry_issues
        if issue.get("kind") == "nonOverlap"
        and isinstance(issue.get("detectorEvidence"), Mapping)
        and issue["detectorEvidence"].get("confirmed") is True
        and source is not None
    ]

    def redundant_aggregate_issue(issue: Mapping[str, Any]) -> bool:
        detector = issue.get("detectorEvidence")
        detector = detector if isinstance(detector, Mapping) else {}
        if int(finite_number(detector.get("browserIssueQidCount"), 1)) <= 1:
            return False
        endpoints = tuple(
            str(value)
            for value in (
                issue.get("qid"),
                detector.get("occluderBindingQid"),
            )
            if value
        )
        if len(endpoints) < 2:
            return False
        group_id = str(issue.get("issueGroupId", ""))
        other_targets = tuple(
            str(other.get("qid"))
            for other in geometry_issues
            if str(other.get("issueGroupId", "")) != group_id
            and other.get("kind") == "nonOverlap"
            and isinstance(other.get("detectorEvidence"), Mapping)
            and other["detectorEvidence"].get("confirmed") is True
            and other.get("qid")
        )
        return all(any(metric_qid_is_ancestor(endpoint, target) for target in other_targets) for endpoint in endpoints)

    required_geometry_issue_groups = tuple(
        sorted(
            {
                str(issue["issueGroupId"])
                for issue in geometry_issues
                if issue.get("kind") in {"nonOverlap", "containment"}
                and isinstance(issue.get("detectorEvidence"), Mapping)
                and issue["detectorEvidence"].get("confirmed") is True
                and issue.get("sameCoordinateSpace") is True
                and bool(issue.get("qid"))
                and bool(issue.get("target"))
                and (issue.get("code") == "dom.off_canvas" or not redundant_aggregate_issue(issue))
                and (
                    envelope_can_change(issue.get("mutationEnvelope"))
                    or envelope_can_change(issue.get("otherMutationEnvelope"))
                    or bool(issue.get("boundAncestors"))
                    or bool(issue.get("otherBoundAncestors"))
                )
            }
        )
    )
    remaining = set(composable_groups)
    composition_components: list[tuple[str, ...]] = []
    while remaining:
        seed = min(remaining)
        remaining.remove(seed)
        component = {seed}
        frontier = [seed]
        while frontier:
            current = frontier.pop()
            connected = {
                candidate
                for candidate in remaining
                if any(
                    metric_qids_related(left, right)
                    for left in composition_qids.get(current, ())
                    for right in composition_qids.get(candidate, ())
                )
            }
            remaining.difference_update(connected)
            component.update(connected)
            frontier.extend(sorted(connected))
        if len(component) > 1:
            composition_components.append(tuple(sorted(component)))
    geometry_composition_groups = {
        ("composition:" + digest_parts("geometry-composition/1", *component).removeprefix("sha256:")[:16]): component
        for component in sorted(composition_components)
    }
    if 2 <= len(required_geometry_issue_groups) <= MAX_GEOMETRY_COMPOSITIONS:
        required_composition_id = (
            "composition:required:"
            + digest_parts(
                "geometry-required-composition/1",
                *required_geometry_issue_groups,
            ).removeprefix("sha256:")[:16]
        )
        geometry_composition_groups[required_composition_id] = required_geometry_issue_groups
    artifacts.required_geometry_issue_groups = required_geometry_issue_groups
    artifacts.geometry_composition_groups = geometry_composition_groups
