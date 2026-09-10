"""Route strict source visibility failures into bounded structural repair.

Composition deliberately has its own noise thresholds. A source readback
failure must still reach repair when no composition issue crosses that gate.
"""

from collections.abc import Mapping
from typing import Any

from services.authored_structural_repair import _digest, _plain_copy
from services.source_content.browser_readback import inspect_browser_source_readback
from services.source_content.source_runs import parse_source_binding_id
from utils.authored_dom_quality import DomQualityIssue
from utils.authored_patch_engine import apply_authored_patch


def _is_root_page_number(item: Mapping[str, Any], metrics: Mapping[str, Any]) -> bool:
    """Recognize a measured bottom page label that repair must not move."""
    box, style = item.get("bbox", {}), item.get("computed_style", {})
    try:
        width, height = float(metrics["width"]), float(metrics["height"])
        return bool(
            item.get("source_block_id") and not item.get("parent_qid")
            and str(item.get("rendered_text", "")).strip().isdecimal()
            and item.get("has_owned_text") is True
            and style.get("position") == "absolute" and style.get("transform") == "none"
            and float(box["x"]) == 0 and float(box["width"]) == width
            and height * .9 <= float(box["y"]) < height
            and 0 < float(box["height"]) <= 32
        )
    except (KeyError, TypeError, ValueError):
        return False


def source_visibility_issues(
    source_report: Mapping[str, Any], metrics: Mapping[str, Any],
) -> tuple[DomQualityIssue, ...]:
    """Keep strict visibility targets and measured occluders in repair scope."""
    failed = set(source_report.get("visibilityFailures", ()))
    fixed_footers = {str(item.get("qid")) for item in metrics.get("elements", ())
                     if _is_root_page_number(item, metrics)}
    grouped: dict[str, list[str]] = {block: [] for block in failed}
    for item in metrics.get("elements", ()):
        raw_id = item.get("source_block_id", item.get("sourceBlockId"))
        if not raw_id:
            continue
        block = parse_source_binding_id(str(raw_id))[0]
        if block not in failed:
            continue
        qids = grouped[block]
        if item.get("qid"):
            qids.append(str(item["qid"]))
        qids.extend(str(qid) for qid in item.get("occlusion_occluder_qids", ())
                    if qid and str(qid) not in fixed_footers)
    return tuple(
        DomQualityIssue(
            "overlap", "high", f"Strict source visibility failed for {block}; preserve text and repair its measured occluder or clipping owner",
            tuple(dict.fromkeys(qids)),
        )
        for block, qids in sorted(grouped.items()) if qids
    )


def inspect_slide_source_visibility(
    *, manifest: Any, outline: Any, slide_index: int,
    html: str, metrics: Mapping[str, Any],
) -> tuple[DomQualityIssue, ...]:
    """Inspect the same pinned source unit used by the final strict gate."""
    manifest = manifest.model_dump(mode="json", by_alias=True) if hasattr(manifest, "model_dump") else manifest
    outline = outline.model_dump(mode="json", by_alias=True) if hasattr(outline, "model_dump") else outline
    slide = outline["slides"][slide_index]
    unit_id = str(slide.get("sourceUnitId") or slide.get("source_unit_id") or "")
    units = [unit for unit in manifest.get("units", ()) if str(unit.get("sourceUnitId") or unit.get("source_unit_id") or "") == unit_id]
    if len(units) != 1:
        return ()  # Final integrity verification remains authoritative.
    report = inspect_browser_source_readback(
        manifest=_plain_copy({"units": units}), outline=_plain_copy({"slides": [slide]}),
        browser_metrics=[_plain_copy(metrics)], htmls=[html],
    )
    return source_visibility_issues(report.as_dict(), metrics)


def repair_source_decoration_paint_order(html: str, metrics: Any) -> str:
    """Repair empty-decoration paint order without changing geometry.

    A static flex/grid item supports z-index without becoming a containing
    block or changing layout. The unchanged source/style/composition gates
    must admit the resulting recapture; this function grants no admission.
    """
    if not isinstance(metrics, Mapping) or metrics.get("source_digest") != _digest(html) or metrics.get("fonts_ready") is not True:
        return html
    items = {str(item["qid"]): item for item in metrics.get("elements", ()) if item.get("qid")}
    parents = {str(item.get("parent_qid")) for item in items.values() if item.get("parent_qid")}
    actions = []
    for qid, target in items.items():
        if not target.get("source_block_id") or target.get("has_owned_text") is not True:
            continue
        occluders = target.get("occlusion_occluder_qids", ())
        style = target.get("computed_style", {})
        parent = items.get(str(target.get("parent_qid")), {})
        measurement = target.get("occlusion_measurement_evidence", {})
        if (
            not occluders or len(occluders) > 4 or qid in parents
            or target.get("css_visible") is not True
            or target.get("occlusion_unknown_fraction") != 0
            or target.get("occlusion_unbound_count") != 0
            or measurement.get("status") != "measured"
            or not (target.get("occluded_fraction", 0) or target.get("continuous_occluded_fraction", 0))
            or style.get("zIndex") != "auto"
            or style.get("transform") != "none"
            or not (
                style.get("position") in {"absolute", "relative"}
                or (style.get("position") == "static"
                    and parent.get("computed_style", {}).get("display") in {"flex", "inline-flex", "grid", "inline-grid"})
            )
        ):
            continue
        safe = True
        for occluder_qid in occluders:
            shape = items.get(str(occluder_qid), {})
            shape_style = shape.get("computed_style", {})
            safe = safe and (
                str(occluder_qid) not in parents and shape.get("tag") == "div"
                and shape.get("semantic_role") == "decoration"
                and not shape.get("source_block_id") and not str(shape.get("rendered_text") or "").strip()
                and shape.get("has_owned_text") is False and shape.get("has_owned_paint") is True
                and shape_style.get("position") == "absolute" and shape_style.get("zIndex") == "auto"
                and shape_style.get("transform") == "none" and shape_style.get("backgroundImage") == "none"
            )
        if safe:
            actions.append({"action": "setStyle", "qid": qid, "styles": {"z-index": "1"}})
    if not actions or len(actions) > 16:
        return html
    applied = apply_authored_patch(
        html, {"strategy": "patch", "actions": actions}, allow_source_reflow_safe_styles=True,
    )
    return applied.html if applied.ok else html
