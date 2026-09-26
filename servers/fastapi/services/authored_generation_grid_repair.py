"""Measured intrinsic-grid correction for authored repair."""

from __future__ import annotations


def _repair_authored_intrinsic_grid_overlap(
    html: str,
    browser_metrics: object,
) -> str:
    """Release intrinsic grid minimums only for measured collisions."""
    from services import authored_generation_repair as runtime

    if not isinstance(browser_metrics, runtime.Mapping):
        return html
    raw_elements = browser_metrics.get("elements")
    if not isinstance(raw_elements, runtime.Sequence):
        return html
    elements = [item for item in raw_elements if isinstance(item, runtime.Mapping)]
    by_qid = {str(item["qid"]): item for item in elements if item.get("qid")}
    children: dict[str, list[str]] = {}
    for qid, item in by_qid.items():
        parent = str(item.get("parent_qid") or item.get("parentQid") or "")
        if parent:
            children.setdefault(parent, []).append(qid)

    def number(value: object) -> float | None:
        try:
            parsed = float(str(value).removesuffix("px"))
        except (TypeError, ValueError):
            return None
        return parsed if runtime.math.isfinite(parsed) else None

    def descendants(qid: str) -> set[str]:
        found: set[str] = set()
        pending = list(children.get(qid, ()))
        while pending:
            child = pending.pop()
            if child in found:
                continue
            found.add(child)
            pending.extend(children.get(child, ()))
        return found

    actions: list[dict[str, object]] = []
    for grid_qid, grid in sorted(by_qid.items()):
        style = grid.get("computed_style") or grid.get("computedStyle") or {}
        if not isinstance(style, runtime.Mapping) or style.get("display") != "grid":
            continue
        client_width = number(grid.get("clientWidth", grid.get("client_width")))
        scroll_width = number(grid.get("scrollWidth", grid.get("scroll_width")))
        if client_width is None or scroll_width is None or scroll_width <= client_width + 1.0:
            continue
        grid_descendants = descendants(grid_qid)
        if not grid_descendants:
            continue
        external_collision = any(
            str(item.get("qid") or "") not in grid_descendants
            and str(item.get("qid") or "") != grid_qid
            and number(
                item.get(
                    "continuous_occluded_fraction",
                    item.get("continuousOccludedFraction"),
                )
            )
            not in (None, 0.0)
            and bool(
                grid_descendants.intersection(
                    str(qid) for qid in (item.get("occlusion_occluder_qids") or item.get("occlusionOccluderQids") or ())
                )
            )
            for item in elements
        )
        grid_box = grid.get("bbox") if isinstance(grid.get("bbox"), runtime.Mapping) else {}
        grid_left = number(grid_box.get("x", grid_box.get("left")))
        grid_width = number(grid_box.get("width"))
        direct_child_escape = any(
            (number(by_qid[qid].get("continuous_off_canvas_fraction")) or 0.0) > 0.0
            or (
                grid_left is not None
                and grid_width is not None
                and isinstance(by_qid[qid].get("bbox"), runtime.Mapping)
                and number(by_qid[qid]["bbox"].get("x")) is not None
                and number(by_qid[qid]["bbox"].get("width")) is not None
                and number(by_qid[qid]["bbox"].get("x")) + number(by_qid[qid]["bbox"].get("width"))
                > grid_left + grid_width + 1.0
            )
            for qid in children.get(grid_qid, ())
            if qid in by_qid
        )
        if not external_collision and not direct_child_escape:
            continue
        for child_qid in sorted(children.get(grid_qid, ())):
            child = by_qid[child_qid]
            child_style = child.get("computed_style") or child.get("computedStyle") or {}
            if isinstance(child_style, runtime.Mapping) and str(
                child_style.get("minWidth", child_style.get("min-width", ""))
            ) in {"0", "0px"}:
                continue
            actions.append(
                {
                    "action": "setStyle",
                    "qid": child_qid,
                    "styles": {"min-width": "0px"},
                }
            )
    if not actions or len(actions) > 16:
        return html
    applied = runtime.apply_authored_patch(
        html,
        {
            "strategy": "patch",
            "actions": actions,
            "rationale": "release measured intrinsic grid minimum overflow",
        },
        allow_source_reflow_safe_styles=True,
    )
    return applied.html if applied.ok else html
