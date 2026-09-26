"""Whole-DOM structural repair scope selection."""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from typing import Any

from utils.authored_patch_engine import _DocumentSpanParser
from utils.llm_calls.author_slide_patch_contracts import (
    StructuralRepairScope,
    _LAYOUT_DISPLAYS,
    _MAX_SCOPE_EDITABLE_QIDS,
    _PROTECTED_ROLE_TOKENS,
    _QID_PATTERN,
    _bounded_string,
)
from utils.llm_calls.author_slide_patch_dom import build_full_dom_graph


def _critique_issue_mappings(critique: Any) -> list[Mapping[str, Any]]:
    if hasattr(critique, "model_dump"):
        critique = critique.model_dump(mode="json")
    if not isinstance(critique, Mapping):
        return []
    raw_issues = critique.get("issues")
    if not isinstance(raw_issues, Sequence) or isinstance(raw_issues, (str, bytes)):
        return []
    return [issue for issue in raw_issues if isinstance(issue, Mapping)]


def build_structural_repair_scope(
    compact_dom_map: Sequence[Mapping[str, Any]],
    critique: Any = None,
    *,
    issue_qids: Collection[str] = (),
    html: str | None = None,
) -> StructuralRepairScope | None:
    """Select one complete existing layout group for a bounded structural patch.

    Exact issue identities should be supplied through ``issue_qids`` (normally from
    deterministic DOM findings).  Structured issue mappings may also provide ``qids``.
    Free-form critique text is used only for a bounded reason, never to guess targets.
    """

    graph = build_full_dom_graph(compact_dom_map, html=html)
    normalized, available_qids = graph.nodes, graph.qids
    by_qid = {item["qid"]: item for item in normalized}
    targets = {str(qid).strip() for qid in issue_qids if _QID_PATTERN.fullmatch(str(qid).strip())}
    reasons: list[str] = []
    for issue in _critique_issue_mappings(critique):
        raw_qids = issue.get("qids")
        if isinstance(raw_qids, Sequence) and not isinstance(raw_qids, (str, bytes)):
            targets.update(str(qid).strip() for qid in raw_qids if _QID_PATTERN.fullmatch(str(qid).strip()))
        reason = _bounded_string(issue.get("detail") or issue.get("message") or issue.get("type"), 240).strip()
        if reason and reason not in reasons:
            reasons.append(reason)

    if not targets or not targets <= available_qids:
        return None

    children_by_parent: dict[str, list[str]] = {}
    for item in normalized:
        parent_qid = str(item.get("parentQid") or "")
        if parent_qid:
            children_by_parent.setdefault(parent_qid, []).append(item["qid"])

    # Absolutely positioned empty ornaments own their geometry independently
    # of the text-bearing cover. Do not grant the whole cover editing rights
    # merely to move its two rings back inside the canvas. Coupled ornament
    # targets form one atomic group; every non-target stays protected.
    if html is not None and len(targets) <= _MAX_SCOPE_EDITABLE_QIDS:
        locator = _DocumentSpanParser(html)
        locator.feed(html)
        locator.close()
        isolated_ornaments = True
        for qid in targets:
            item = by_qid[qid]
            style = item.get("computedStyle", {})
            span = locator.spans.get(qid)
            parent = str(item.get("parentQid") or "")
            if (
                qid in graph.system_root_qids
                or item.get("editable") is False
                or item.get("tag") != "div"
                or item.get("semanticRole") != "decoration"
                or item.get("sourceBlockId")
                or children_by_parent.get(qid)
                or not parent
                or parent not in by_qid
                or style.get("position") != "absolute"
                or str(item.get("text") or "").strip()
                or span is None
                or span.content_end is None
                or html[span.content_start : span.content_end].strip()
            ):
                isolated_ornaments = False
                break
        if isolated_ornaments:
            ordered = tuple(sorted(targets))
            return StructuralRepairScope(
                layout_owner_qid=ordered[0],
                editable_qids=ordered,
                affected_qids=ordered,
                protected_qids=tuple(sorted(available_qids - targets)),
                reasons=("isolated_absolute_decoration_geometry",),
            )

    def has_cross_branch_target_overlap(qid: str) -> bool:
        """A static block can own reflow when overflowing sibling branches collide."""
        target_boxes = []
        for target in targets:
            branch = target
            while branch != qid:
                parent = str(by_qid[branch].get("parentQid") or "")
                if parent == qid:
                    break
                if not parent:
                    branch = qid
                    break
                branch = parent
            if branch == qid:
                continue
            branch_style = by_qid[branch].get("computedStyle", {})
            if branch_style.get("position") not in {"static", "relative"}:
                continue
            box = by_qid[target].get("bbox", {})
            x, y = box.get("x", box.get("left")), box.get("y", box.get("top"))
            width, height = box.get("width"), box.get("height")
            if any(value is None for value in (x, y, width, height)) or width <= 0 or height <= 0:
                continue
            target_boxes.append((branch, x, y, x + width, y + height))
        return any(
            first[0] != second[0]
            and min(first[3], second[3]) > max(first[1], second[1])
            and min(first[4], second[4]) > max(first[2], second[2])
            for index, first in enumerate(target_boxes)
            for second in target_boxes[index + 1 :]
        )

    def is_layout_owner(qid: str) -> bool:
        item = by_qid.get(qid)
        parent_qid = str(item.get("parentQid") or "") if item is not None else ""
        if (
            item is None
            or qid in graph.system_root_qids
            or (not parent_qid and qid not in graph.verified_content_qids)
            or (parent_qid and parent_qid not in by_qid)
            or item.get("editable") is False
        ):
            return False
        style = item.get("computedStyle")
        display = str(style.get("display") or "").lower() if isinstance(style, Mapping) else ""
        position = str(style.get("position") or "").lower() if isinstance(style, Mapping) else ""
        children = children_by_parent.get(qid, ())
        escaping_target = False
        owner_box = item.get("bbox", {})
        if display == "block" and str(style.get("backgroundColor", "")).startswith("rgb("):
            for target in targets:
                ancestor = str(by_qid[target].get("parentQid") or "")
                while ancestor and ancestor != qid:
                    ancestor = str(by_qid[ancestor].get("parentQid") or "")
                target_box = by_qid[target].get("bbox", {})
                if ancestor == qid and all(key in owner_box and key in target_box for key in ("y", "height")):
                    if target_box["y"] + target_box["height"] > owner_box["y"] + owner_box["height"] + 2:
                        escaping_target = True
                        break
        constrained_block = (
            display == "block"
            and isinstance(style, Mapping)
            and (
                str(style.get("overflow") or "") in {"hidden", "clip", "auto", "scroll"}
                or any(
                    str(style.get(key) or "") in {"hidden", "clip", "auto", "scroll"}
                    for key in ("overflowX", "overflowY")
                )
                or (
                    qid in targets
                    and not item.get("sourceBlockId")
                    and any(
                        str(style.get(key) or "") not in {"", "auto", "none", "0px"}
                        for key in ("height", "maxHeight", "width", "maxWidth")
                    )
                )
                or (len(children) > 1 and has_cross_branch_target_overlap(qid))
                or escaping_target
                or item.get("verifiedHeaderBand") is True
            )
        )
        return bool(children) and (
            display in _LAYOUT_DISPLAYS
            or constrained_block
            or (position in {"relative", "absolute", "fixed"} and len(children) > 1)
        )

    def layout_owner_chain(qid: str) -> tuple[str, ...]:
        visited: set[str] = set()
        current = by_qid.get(qid)
        owners: list[str] = []
        while current is not None:
            current_qid = str(current.get("qid") or "")
            if is_layout_owner(current_qid):
                owners.append(current_qid)
            parent_qid = str(current.get("parentQid") or "")
            if not parent_qid or parent_qid in visited:
                break
            visited.add(parent_qid)
            current = by_qid.get(parent_qid)
        return tuple(owners)

    def descendants(qid: str) -> set[str]:
        result: set[str] = set()
        pending = list(children_by_parent.get(qid, ()))
        while pending:
            child_qid = pending.pop()
            if child_qid in result:
                continue
            result.add(child_qid)
            pending.extend(children_by_parent.get(child_qid, ()))
        return result

    owner_chains = {qid: layout_owner_chain(qid) for qid in sorted(targets)}
    if any(not chain for chain in owner_chains.values()):
        return None
    common_owners = set.intersection(*(set(chain) for chain in owner_chains.values()))
    if not common_owners:
        return None

    # Start with the nearest shared owner. A higher owner is considered only when
    # it remains inside the same semantic layout region; slide/root is never eligible.
    first_chain = next(iter(owner_chains.values()))
    owner = next((qid for qid in first_chain if qid in common_owners), None)
    if owner is None:
        return None
    direct_children = children_by_parent.get(owner, [])
    if not direct_children:
        return None

    affected = {owner, *descendants(owner)}
    # A child reflow can resize auto-sized ancestors even when no action targets
    # them. Classify those ancestors as affected so unchanged parent geometry is
    # not required by the protected-area guard. Their siblings remain protected.
    ancestor_qid = str(by_qid[owner].get("parentQid") or "")
    while ancestor_qid and ancestor_qid not in graph.system_root_qids:
        affected.add(ancestor_qid)
        ancestor_qid = str(by_qid[ancestor_qid].get("parentQid") or "")
    target_branches = {
        child_qid for child_qid in direct_children if child_qid in targets or bool(descendants(child_qid) & targets)
    }
    for child_qid in direct_children:
        role = str(by_qid[child_qid].get("semanticRole") or "").lower()
        role_tokens = {token for token in re.split(r"[^a-z0-9_-]+", role) if token}
        if child_qid not in target_branches and role_tokens & _PROTECTED_ROLE_TOKENS:
            return None

    # A parent-only edit can reflow arbitrarily many descendants. Preserve their
    # complete affected inventory without granting every descendant direct edits.
    editable = {owner, *direct_children, *targets}
    editable = {qid for qid in editable if by_qid[qid].get("editable") is not False}
    if len(editable) > _MAX_SCOPE_EDITABLE_QIDS:
        # Defect leaves need not be editable to repair their parent cards. Keep
        # the complete direct-child layout action group when it fits the budget;
        # do not let many text findings remove all card geometry permissions.
        direct_editable = {qid for qid in {owner, *direct_children} if by_qid[qid].get("editable") is not False}
        editable = direct_editable if len(direct_editable) <= _MAX_SCOPE_EDITABLE_QIDS else {owner}

    affected_blocks = sorted(
        {str(by_qid[qid].get("sourceBlockId") or "") for qid in affected if str(by_qid[qid].get("sourceBlockId") or "")}
    )
    protected = tuple(sorted(available_qids - affected))
    return StructuralRepairScope(
        layout_owner_qid=owner,
        editable_qids=tuple(sorted(editable)),
        affected_qids=tuple(sorted(affected)),
        affected_source_block_ids=tuple(affected_blocks),
        protected_qids=protected,
        reasons=tuple(reasons[:8]),
    )


def build_structural_repair_scopes(
    compact_dom_map: Sequence[Mapping[str, Any]],
    critique: Any = None,
    *,
    issue_qids: Collection[str] = (),
    html: str | None = None,
) -> tuple[StructuralRepairScope, ...]:
    """Return disjoint repair groups; coupled reflow/overlap groups stay atomic.

    Callers must recompute after advancing selected HTML and share one slide budget.
    An unscopeable target fails closed rather than silently dropping a defect.
    """
    graph = build_full_dom_graph(compact_dom_map, html=html)
    targets = {str(qid).strip() for qid in issue_qids}
    for issue in _critique_issue_mappings(critique):
        values = issue.get("qids", ())
        if isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
            targets.update(str(qid).strip() for qid in values)
    if not targets or not targets <= graph.qids:
        return ()
    by_qid = {item["qid"]: item for item in graph.nodes}
    target_groups = [{target} for target in sorted(targets)]

    def join_targets(related: set[str]) -> None:
        related &= targets
        merged = set(related)
        for group in target_groups:
            if group & related:
                merged.update(group)
        if merged:
            target_groups[:] = [group for group in target_groups if not group & merged]
            target_groups.append(merged)

    # An overlap issue is already a coupled defect. Creating singleton scopes
    # first can reject the overlapping title as an unrelated protected sibling
    # before its paired target ever reaches the merge step.
    for issue in _critique_issue_mappings(critique):
        values = issue.get("qids", ())
        if isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
            join_targets({str(qid).strip() for qid in values})

    def target_rectangle(qid: str):
        box = by_qid[qid].get("bbox", {})
        x, y = box.get("x", box.get("left")), box.get("y", box.get("top"))
        width, height = box.get("width"), box.get("height")
        if any(value is None for value in (x, y, width, height)) or width <= 0 or height <= 0:
            return None
        return x, y, x + width, y + height

    ordered_targets = sorted(targets)
    for index, target in enumerate(ordered_targets):
        first = target_rectangle(target)
        if first is None:
            continue
        for other in ordered_targets[index + 1 :]:
            second = target_rectangle(other)
            if (
                second
                and min(first[2], second[2]) > max(first[0], second[0])
                and min(first[3], second[3]) > max(first[1], second[1])
            ):
                join_targets({target, other})
    groups: list[tuple[set[str], StructuralRepairScope]] = []
    for group_targets in sorted(target_groups, key=lambda group: tuple(sorted(group))):
        scope = build_structural_repair_scope(compact_dom_map, issue_qids=group_targets, html=html)
        if scope is None:
            return ()
        groups.append((group_targets, scope))

    def coupled(left: StructuralRepairScope, right: StructuralRepairScope) -> bool:
        a, b = set(left.affected_qids), set(right.affected_qids)
        if a & b:
            return True
        for qid in a | b:
            opposite = b if qid in a else a
            if any(
                by_qid[qid].get(key) in opposite
                for key in ("layoutOwnerQid", "clippingAncestorQid", "containingBlockQid", "occludingQid")
            ):
                return True

        def rectangle(qid):
            box = by_qid[qid].get("bbox", {})
            x, y = box.get("x", box.get("left")), box.get("y", box.get("top"))
            width, height = box.get("width"), box.get("height")
            if any(value is None for value in (x, y, width, height)):
                return None
            return x, y, x + width, y + height

        left_box, right_box = rectangle(left.layout_owner_qid), rectangle(right.layout_owner_qid)
        if (
            left_box
            and right_box
            and min(left_box[2], right_box[2]) > max(left_box[0], right_box[0])
            and min(left_box[3], right_box[3]) > max(left_box[1], right_box[1])
        ):
            return True
        parents = {by_qid[left.layout_owner_qid].get("parentQid"), by_qid[right.layout_owner_qid].get("parentQid")}
        if len(parents) == 1:
            parent = next(iter(parents))
            if parent and parent not in graph.system_root_qids:
                style = by_qid[parent].get("computedStyle", {})
                if style.get("display") in _LAYOUT_DISPLAYS or style.get("overflow") in {"clip", "hidden"}:
                    return True
        return False

    changed = True
    while changed:
        changed = False
        for index, (left_targets, left) in enumerate(groups):
            for other_index in range(index + 1, len(groups)):
                right_targets, right = groups[other_index]
                if not coupled(left, right):
                    continue
                merged_targets = left_targets | right_targets
                merged = build_structural_repair_scope(compact_dom_map, issue_qids=merged_targets, html=html)
                if merged is None:
                    return ()
                groups[index] = (merged_targets, merged)
                groups.pop(other_index)
                changed = True
                break
            if changed:
                break
    return tuple(scope for _, scope in sorted(groups, key=lambda item: item[1].layout_owner_qid))


def protected_qids_for_repair_scopes(
    compact_dom_map: Sequence[Mapping[str, Any]],
    scopes: Sequence[StructuralRepairScope],
) -> tuple[str, ...]:
    """Freeze slide-wide protection outside every original repair group.

    Per-candidate scope guards still protect the other groups while that candidate
    runs. A long-lived evaluator must allow any original group to be selected next.
    """
    available = build_full_dom_graph(compact_dom_map).qids
    affected: set[str] = set()
    for scope in scopes:
        if set(scope.affected_qids) | set(scope.protected_qids) != available:
            raise ValueError("repair scope does not classify the complete measured DOM")
        affected.update(scope.affected_qids)
    return tuple(sorted(available - affected))
