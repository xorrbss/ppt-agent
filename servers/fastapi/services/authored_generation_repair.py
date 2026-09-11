"""Extracted implementation behind the authored generation facade."""

from __future__ import annotations


def _repair_authored_intrinsic_grid_overlap(
    html: str,
    browser_metrics: object,
) -> str:
    """Release intrinsic grid minimums only for measured collisions."""
    if not isinstance(browser_metrics, Mapping): return html
    raw_elements = browser_metrics.get("elements")
    if not isinstance(raw_elements, Sequence): return html
    elements = [item for item in raw_elements if isinstance(item, Mapping)]
    by_qid = {str(item["qid"]): item for item in elements if item.get("qid")}
    children: dict[str, list[str]] = {}
    for qid, item in by_qid.items():
        parent = str(item.get("parent_qid") or item.get("parentQid") or "")
        if parent: children.setdefault(parent, []).append(qid)

    def number(value: object) -> float | None:
        try:
            parsed = float(str(value).removesuffix("px"))
        except (TypeError, ValueError):
            return None
        return parsed if math.isfinite(parsed) else None

    def descendants(qid: str) -> set[str]:
        found: set[str] = set()
        pending = list(children.get(qid, ()))
        while pending:
            child = pending.pop()
            if child in found: continue
            found.add(child)
            pending.extend(children.get(child, ()))
        return found

    actions: list[dict[str, object]] = []
    for grid_qid, grid in sorted(by_qid.items()):
        style = grid.get("computed_style") or grid.get("computedStyle") or {}
        if not isinstance(style, Mapping) or style.get("display") != "grid":
            continue
        client_width = number(grid.get("clientWidth", grid.get("client_width")))
        scroll_width = number(grid.get("scrollWidth", grid.get("scroll_width")))
        if (
            client_width is None
            or scroll_width is None
            or scroll_width <= client_width + 1.0
        ):
            continue
        grid_descendants = descendants(grid_qid)
        if not grid_descendants: continue
        external_collision = any(
            str(item.get("qid") or "") not in grid_descendants
            and str(item.get("qid") or "") != grid_qid
            and number(
                item.get(
                    "continuous_occluded_fraction",
                    item.get("continuousOccludedFraction"),
                )
            ) not in (None, 0.0)
            and bool(
                grid_descendants.intersection(
                    str(qid)
                    for qid in (
                        item.get("occlusion_occluder_qids")
                        or item.get("occlusionOccluderQids")
                        or ()
                    )
                )
            )
            for item in elements
        )
        grid_box = grid.get("bbox") if isinstance(grid.get("bbox"), Mapping) else {}
        grid_left = number(grid_box.get("x", grid_box.get("left")))
        grid_width = number(grid_box.get("width"))
        direct_child_escape = any(
            (
                number(by_qid[qid].get("continuous_off_canvas_fraction")) or 0.0
            ) > 0.0
            or (
                grid_left is not None
                and grid_width is not None
                and isinstance(by_qid[qid].get("bbox"), Mapping)
                and number(by_qid[qid]["bbox"].get("x")) is not None
                and number(by_qid[qid]["bbox"].get("width")) is not None
                and number(by_qid[qid]["bbox"].get("x"))
                + number(by_qid[qid]["bbox"].get("width"))
                > grid_left + grid_width + 1.0
            )
            for qid in children.get(grid_qid, ())
            if qid in by_qid
        )
        if not external_collision and not direct_child_escape: continue
        for child_qid in sorted(children.get(grid_qid, ())):
            child = by_qid[child_qid]
            child_style = child.get("computed_style") or child.get("computedStyle") or {}
            if isinstance(child_style, Mapping) and str(child_style.get("minWidth", child_style.get("min-width", ""))) in {"0", "0px"}:
                continue
            actions.append({
                "action": "setStyle",
                "qid": child_qid,
                "styles": {"min-width": "0px"},
            })
    if not actions or len(actions) > 16:
        return html
    applied = apply_authored_patch(
        html,
        {
            "strategy": "patch",
            "actions": actions,
            "rationale": "release measured intrinsic grid minimum overflow",
        },
        allow_source_reflow_safe_styles=True,
    )
    return applied.html if applied.ok else html


def _deterministic_repair_candidate(
    *,
    html: str,
    browser_metrics: object,
    capacity,
    header_contract: AuthoredHeaderContract | None,
    role: str,
    profile_id: str,
    allow_immutable_checkpoint_geometry: bool,
    repair_intrinsic_grid_overlap: bool = False,
) -> str:
    """Compose every applicable local repair before a single recapture."""

    candidate = html
    source_visibility_candidate = (
        repair_source_decoration_paint_order(candidate, browser_metrics)
        if repair_intrinsic_grid_overlap
        else candidate
    )
    candidate = repair_authored_owned_text_paint_order(
        source_visibility_candidate,
        browser_metrics,
        profile_id=profile_id,
    )
    stale_geometry_metrics = (
        source_visibility_candidate != html or candidate != source_visibility_candidate
    )
    # A wrapper inset from the edge that was given the full canvas width runs
    # past the right edge by exactly its inset, taking its children with it.
    # Clamping the measured overhang costs no provider call and leaves the
    # slide otherwise untouched; the gate re-measures the result.
    # If an intrinsic grid repair changed the candidate, these baseline metrics
    # are stale.  Both reducers append source-visible style rules and can also
    # collapse the just-released grid item to its old escaped width.  Let the
    # single recapture validate the intrinsic repair instead.
    if repair_intrinsic_grid_overlap:
        intrinsic_candidate = _repair_authored_intrinsic_grid_overlap(
            candidate, browser_metrics
        )
        candidate = intrinsic_candidate
        stale_geometry_metrics = stale_geometry_metrics or intrinsic_candidate != html
    if not stale_geometry_metrics:
        candidate = repair_authored_offcanvas_width(candidate, browser_metrics)
        # A source blank field is a run of underscores the deck must reproduce
        # literally, so when the author's size makes it wider than the box drawn
        # for it, the size is the only thing left to give.
        candidate = repair_authored_blank_field_clipping(candidate, browser_metrics)
    candidate = reduce_authored_owner_text_capacity(
        candidate,
        capacity,
        profile_id=profile_id,
        allow_summarization=(profile_id == "ordinary"),
    )
    reduced = reduce_authored_closing_capacity(
        candidate,
        browser_metrics,
        capacity,
        profile_id=profile_id,
    )
    candidate = reduced
    if capacity_violations(
        candidate,
        browser_metrics,
        capacity,
    ) == ("browser_text_not_visible:header.eyebrow",):
        candidate = repair_authored_header_eyebrow_clip(
            candidate,
            browser_metrics,
            header_contract,
            role,
            allow_immutable_checkpoint_geometry=allow_immutable_checkpoint_geometry,
        )
    if profile_id == "ordinary":
        candidate = repair_authored_layout_alignment(candidate, role=role)
    return canonicalize_authored_roles(candidate, role=role)

def _admit_vqa_capacity_candidate(
    *,
    slide_index: int,
    original_html: str,
    original_png: bytes,
    original_metrics: object | None,
    candidate_html: str,
    candidate_png: bytes,
    candidate_metrics: object | None,
    capacity: object,
    allow_original_fallback: bool = True,
    terminal_fallback_enabled: bool = False,
) -> tuple[str, bytes, object | None, bool]:
    """Admit a VQA rewrite only when it stays inside the frozen envelope.

    VQA has no authority to expand capacity or delete QIDs to make a candidate
    pass. An unmeasured or over-capacity candidate is discarded intact and the
    pre-VQA bytes are restored; the ordinary final gate still verifies that
    restored source before publication.
    """

    candidate_violations = (
        capacity_violations(candidate_html, candidate_metrics, capacity)
        if candidate_metrics is not None
        else ("browser_metrics_unavailable",)
    )
    if not candidate_violations:
        return candidate_html, candidate_png, candidate_metrics, True

    original_violations = (
        capacity_violations(original_html, original_metrics, capacity)
        if original_metrics is not None
        else ("browser_metrics_unavailable",)
    )
    LOGGER.warning(
        "[authored] rejected post-VQA capacity candidate slide=%d candidate_digest=%s "
        "candidate_violations=%s restored_digest=%s restored_violations=%s",
        slide_index + 1, digest_html(candidate_html), list(candidate_violations),
        digest_html(original_html), list(original_violations),
    )
    if not allow_original_fallback and not gate_repair_rejection_is_terminal_slide(
        enabled=terminal_fallback_enabled, candidate_violations=candidate_violations,
        original_violations=original_violations,
    ):
        raise AuthoredStageError(
            slide_index=slide_index,
            stage="render",
            code="authored_slide_capacity_exceeded",
            retryable=False,
            message=(
                f"Slide {slide_index + 1} bounded gate-repair candidate exceeded "
                "its frozen capacity envelope: "
                + ", ".join(candidate_violations)
            ),
        )
    return original_html, original_png, original_metrics, False

def _admit_gate_repair_capacity_candidate(
    *,
    allow_primary_title_shortening: bool = False,
    **kwargs,
):
    """Gate-repair originals remain failed, so an invalid rewrite is terminal."""

    semantic_violations = semantic_repair_boundary_violations(
        kwargs["original_html"],
        kwargs["candidate_html"],
        allow_primary_title_shortening=allow_primary_title_shortening,
    )
    if semantic_violations:
        LOGGER.warning(
            "[authored] rejected bounded gate repair semantic boundary slide=%d "
            "violations=%s",
            int(kwargs["slide_index"]) + 1,
            list(semantic_violations),
        )
        raise AuthoredStageError(
            slide_index=int(kwargs["slide_index"]),
            stage="render",
            code="authored_composition_safety_gate_failed",
            retryable=False,
            message="bounded repair changed protected semantic ownership",
        )
    return _admit_vqa_capacity_candidate(
        **kwargs,
        allow_original_fallback=False,
    )

async def _revise_gate_repair_candidates(
    *,
    flagged: list[int],
    htmls: list[str],
    pngs: list[bytes],
    contents: list[str],
    roles: list[str],
    deterministic_candidates: Mapping[int, str],
    brand: object,
    design_system: str,
    repair_kwargs: Mapping[str, object],
    terminal_fallback_enabled: bool = False,
) -> tuple[list[str], list[int]]:
    """Review unchanged inputs only; admit all candidates after one fresh bind.

    A changed deterministic candidate has no matching pixels/telemetry yet.
    Passing its failed original into strict VQA would reject that original
    before the candidate can be captured.  Unchanged originals, including
    provider-disabled slides, retain the existing strict residual gate.
    """

    if (
        repair_kwargs.get("candidate_evaluator") is not None
        or repair_kwargs.get("candidate_evaluators") is not None
    ):
        return await _revise_source_gate_repair_candidates(
            flagged=flagged, htmls=htmls, pngs=pngs, contents=contents,
            roles=roles, deterministic_candidates=deterministic_candidates,
            brand=brand, design_system=design_system, repair_kwargs=repair_kwargs,
        )

    revised_htmls = [deterministic_candidates[index] for index in flagged]
    reviewed_local_indices = [
        local_index
        for local_index, absolute_index in enumerate(flagged)
        if digest_html(revised_htmls[local_index]) == digest_html(htmls[absolute_index])
    ]
    if not reviewed_local_indices:
        return revised_htmls, []

    subset_kwargs = dict(repair_kwargs)
    for key in (
        "slide_indices",
        "initial_critiques",
        "capacity_briefs",
        "provider_recovery_allowed",
        "chapter_prefixes",
        "illustration_slots",
        "infographic_briefs",
        "reference_images_by_slide",
    ):
        values = subset_kwargs.get(key)
        if values is not None:
            if not isinstance(values, (list, tuple)) or len(values) != len(flagged):
                raise ValueError(f"gate repair {key} must match flagged slides")
            subset_kwargs[key] = [values[index] for index in reviewed_local_indices]
    accepted_kwargs = inspect.signature(revise_authored_deck).parameters
    subset_kwargs = {
        key: value for key, value in subset_kwargs.items() if key in accepted_kwargs
    }
    reviewed_absolute_indices = [flagged[index] for index in reviewed_local_indices]
    try:
        repaired = await revise_authored_deck(
            [htmls[index] for index in reviewed_absolute_indices],
            [pngs[index] for index in reviewed_absolute_indices],
            [contents[index] for index in reviewed_absolute_indices],
            [roles[index] for index in reviewed_absolute_indices],
            brand,
            design_system,
            **subset_kwargs,
        )
        reviewed_htmls, reviewed_pngs, fixed = repaired[:3]
    except AuthoredVqaResidualDomIssuesError as error:
        # D7: a residual DOM defect reported by the round is the same defect the
        # final gate reports, and it must reach the terminal-slide fallback the
        # same way. Recover the round's own bound state instead of throwing the
        # whole round away; every candidate below is still re-rendered into one
        # fresh (html, png, metrics) capture before admission.
        recovered = recover_residual_repair_round(
            error, enabled=terminal_fallback_enabled
        )
        if recovered is None:
            # The VQA call above may run against only the unchanged subset of
            # the flagged slides. Preserve that subset mapping so successful
            # repairs can be checkpointed at their real deck positions before
            # the residual failure is reported.
            error.checkpoint_absolute_indices = tuple(reviewed_absolute_indices)
            raise
        reviewed_htmls, reviewed_pngs, fixed = recovered
    if not (
        len(reviewed_htmls) == len(reviewed_pngs) == len(reviewed_local_indices)
    ):
        raise ValueError("gate repair result must match reviewed slides")
    for local_index, html in zip(reviewed_local_indices, reviewed_htmls, strict=True):
        revised_htmls[local_index] = html
    fixed_local_indices: list[int] = []
    for index in fixed:
        reviewed_index = int(index)
        if not 0 <= reviewed_index < len(reviewed_local_indices):
            raise ValueError("gate repair fixed index is outside reviewed slides")
        fixed_local_indices.append(reviewed_local_indices[reviewed_index])
    # Returned PNGs are intentionally not merged: the caller binds every final
    # candidate to fresh pixels and telemetry before semantic/capacity admission.
    return revised_htmls, fixed_local_indices

async def _revise_source_gate_repair_candidates(
    *, flagged, htmls, pngs, contents, roles, deterministic_candidates,
    brand, design_system, repair_kwargs,
) -> tuple[list[str], list[int]]:
    """Feed measured deterministic improvements into the bounded repair loop."""
    kwargs = dict(repair_kwargs)
    shared_evaluator = kwargs.get("candidate_evaluator")
    evaluators = kwargs.get("candidate_evaluators")
    capture = kwargs["structural_candidate_capture"]
    initial_metrics = list(kwargs["initial_browser_metrics"])
    initial_assessments = [None] * len(flagged)
    initial_repair_history: list[list[dict[str, object]]] = [
        [] for _ in flagged
    ]
    budgets = kwargs.get("structural_budgets") or {
        index: StructuralRepairBudget() for index in flagged
    }
    selected_htmls = [htmls[index] for index in flagged]
    selected_pngs = [pngs[index] for index in flagged]
    for local_index, absolute_index in enumerate(flagged):
        evaluator = (
            evaluators[absolute_index]
            if evaluators is not None
            else shared_evaluator
        )
        if evaluator is None:
            raise ValueError("candidate evaluator is required for structural repair")
        budget = budgets[absolute_index]
        candidate = deterministic_candidates[absolute_index]
        if digest_html(candidate) == digest_html(selected_htmls[local_index]):
            continue
        # Keep at least one capture for a provider candidate whenever that path
        # is authorized. A failed/cancelled capture is still charged here.
        recovery_allowed = kwargs.get("provider_recovery_allowed")
        provider_allowed = bool(
            True if recovery_allowed is None else recovery_allowed[local_index]
        )
        if not budget.claim_capture(reserve_for_model=1 if provider_allowed else 0):
            initial_repair_history[local_index].append({
                "cycle": -1,
                "path": "deterministic",
                "stop_reason": "model_capture_reserved",
                "selected_html_digest": digest_html(selected_htmls[local_index]),
                "budget": budget.receipt(),
            })
            continue
        candidate_html, candidate_png, metrics = await capture(
            candidate, absolute_index
        )
        assessment = await evaluator(absolute_index, candidate_html, metrics)
        accepted = assessment.decision in {"provisional", "admitted"}
        initial_repair_history[local_index].append({
            "cycle": -1,
            "path": "deterministic",
            "decision": assessment.decision,
            "reasons": list(getattr(assessment, "reasons", ())),
            "selected_html_digest": digest_html(selected_htmls[local_index]),
            "html_digest": digest_html(candidate_html),
            "capture_calls": 1,
            "proposal_calls": 0,
            "stop_reason": (
                "admitted" if assessment.decision == "admitted"
                else "provisional_selected" if accepted
                else "deterministic_rejected"
            ),
            "budget": budget.receipt(),
        })
        if accepted:
            _advance_repair_evaluator(
                evaluator, assessment, candidate_html, metrics
            )
            selected_htmls[local_index] = candidate_html
            selected_pngs[local_index] = candidate_png
            initial_metrics[local_index] = metrics
            initial_assessments[local_index] = assessment
    kwargs["initial_browser_metrics"] = initial_metrics
    kwargs["initial_assessments"] = initial_assessments
    kwargs["initial_repair_history"] = initial_repair_history
    kwargs["structural_budgets"] = budgets
    repaired = await revise_authored_deck(
        selected_htmls, selected_pngs,
        [contents[index] for index in flagged],
        [roles[index] for index in flagged], brand, design_system, **kwargs,
    )
    return repaired[0], repaired[2]

async def _bind_bounded_repair_evidence(
    htmls: list[str], indices: Sequence[int] | None = None,
) -> tuple[list[str], list[bytes], list[object]]:
    """Capture exact final HTML/pixels/metrics for bounded repair results."""

    bound_htmls: list[str] = []
    bound_pngs: list[bytes] = []
    bound_metrics: list[object] = []
    for html, index in zip(htmls, indices or [None] * len(htmls), strict=True):
        # Bind the reducer's exact document: role immutability already keeps a
        # title owner here.  ``index`` only attributes the capture to a slide.
        canonical_html = ensure_authored_semantic_roles(ensure_authored_qids(html))
        capture = await capture_once(canonical_html, index=index, capture=render_html_to_png_with_metrics)
        if capture.png == _placeholder_png() or capture.browser_metrics is None:
            raise RuntimeError("bounded repair browser evidence unavailable")
        bound_htmls.append(canonical_html)
        bound_pngs.append(capture.png)
        bound_metrics.append(capture.browser_metrics)
    return bound_htmls, bound_pngs, bound_metrics


_LOCAL_NAMES = frozenset(globals())

def bind_runtime(runtime) -> None:
    for name, value in vars(runtime).items():
        if name not in _LOCAL_NAMES and not name.startswith("__"):
            globals()[name] = value
