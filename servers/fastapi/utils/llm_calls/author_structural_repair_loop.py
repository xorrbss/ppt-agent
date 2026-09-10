"""Bounded measured repair loop for source-preserving authored slides."""

from __future__ import annotations

import asyncio
import hashlib
import os
import time
from collections.abc import Collection, Mapping, Sequence
from dataclasses import replace
from typing import Any, Awaitable, Callable, Optional

from services.authored_structural_repair import RepairAssessment, SlideRepairResult
from utils.llm_calls.critique_slide import CritiqueIssue, SlideCritique

async def revise_structural_candidates(
    *,
    htmls: Sequence[str],
    pngs: Sequence[bytes],
    absolute_indices: Sequence[int],
    max_cycles: int,
    candidate_evaluator: Callable[[int, str, Mapping[str, Any]], Awaitable[RepairAssessment]] | None,
    candidate_evaluators: Mapping[
        int, Callable[[int, str, Mapping[str, Any]], Awaitable[RepairAssessment]]
    ] | None,
    initial_browser_metrics: Optional[Sequence[Mapping[str, Any] | None]],
    initial_assessments: Optional[Sequence[RepairAssessment | None]],
    initial_repair_history: Optional[Sequence[Sequence[Mapping[str, Any]]]],
    initial_critiques: Optional[Sequence[SlideCritique | None]],
    provider_recovery_allowed: Optional[Sequence[bool]],
    blocked_recovery_issue_types: Collection[str],
    fail_on_residual_dom_issues: bool,
    return_details: bool,
    repair_details_sink: Optional[list[dict[str, Any]]],
    structural_results_sink: Optional[list[SlideRepairResult]],
    review_stats_sink: Optional[dict[str, Any]],
    structural_candidate_capture: Optional[
        Callable[[str, int], Awaitable[tuple[str, bytes, Mapping[str, Any]]]]
    ],
    structural_budgets: Mapping[int, StructuralRepairBudget] | None,
    runtime: Any,
) -> Any:
    """Keep provisional CSS repairs in memory and export only admitted pairs.

    This measured source-content lane never calls a visual critique or a full
    author. The proposal boundary permits one provider transmission per cycle.
    The ordinary VQA implementation below retains its existing return contract.
    """
    AuthoredVqaResidualDomIssuesError = runtime.AuthoredVqaResidualDomIssuesError
    StructuralRepairBudget = runtime.StructuralRepairBudget
    _advance_repair_evaluator = runtime._advance_repair_evaluator
    _get_vqa_sem = runtime._get_vqa_sem
    _measured_structural_issue_qids = runtime._measured_structural_issue_qids
    _repair_evaluator_for = runtime._repair_evaluator_for
    _structural_compact_dom = runtime._structural_compact_dom
    apply_authored_patch = runtime.apply_authored_patch
    bind_repair_candidate = runtime.bind_repair_candidate
    build_structural_repair_scopes = runtime.build_structural_repair_scopes
    deterministic_dom_critique = runtime.deterministic_dom_critique
    ensure_browser_metrics = runtime.ensure_browser_metrics
    get_model = runtime.get_model
    inspect_authored_dom = runtime.inspect_authored_dom
    is_valid_slide_html = runtime.is_valid_slide_html
    LOGGER = runtime.LOGGER
    propose_authored_slide_patch = runtime.propose_authored_slide_patch
    protected_scope_violations = runtime.protected_scope_violations
    render_html_to_png_with_metrics = runtime.render_html_to_png_with_metrics
    RESIDUAL_BLOCKING_CATEGORIES = runtime.RESIDUAL_BLOCKING_CATEGORIES
    source_repair_html_violations = runtime.source_repair_html_violations
    source_visibility_issues = runtime.source_visibility_issues

    async def repair(i: int) -> SlideRepairResult:
        absolute_index = absolute_indices[i]
        evaluator = _repair_evaluator_for(
            candidate_evaluator, candidate_evaluators, absolute_index
        )
        budget = (
            structural_budgets[absolute_index]
            if structural_budgets is not None
            else StructuralRepairBudget(
                max_provider_transmissions=min(2, max(0, max_cycles))
            )
        )
        current_html, current_png = htmls[i], pngs[i]
        metrics = initial_browser_metrics[i] if initial_browser_metrics is not None else None
        if metrics is None:
            metrics = await ensure_browser_metrics(current_html)
        metrics = dict(metrics or {})
        assessment = (
            initial_assessments[i]
            if initial_assessments is not None and initial_assessments[i] is not None
            else await evaluator(absolute_index, current_html, metrics)
        )
        baseline_digest = str(assessment.evidence.get("baseline_html_digest") or "sha256:" + hashlib.sha256(
            htmls[i].encode("utf-8")
        ).hexdigest())
        history = (
            [dict(item) for item in initial_repair_history[i]]
            if initial_repair_history is not None
            else []
        )
        seen_digests = {hashlib.sha256(current_html.encode("utf-8")).hexdigest()}
        rejected_reasons: tuple[str, ...] = ()
        provider_allowed = (
            provider_recovery_allowed is None or provider_recovery_allowed[i]
        )
        cycle_count = min(2, max(0, max_cycles)) if provider_allowed else 0
        for cycle in range(cycle_count):
            if assessment.admitted:
                break
            report = inspect_authored_dom(
                current_html, metrics, strict_structural_off_canvas=True,
            )
            structural_issues = tuple(
                issue for issue in report.issues
                if issue.category in RESIDUAL_BLOCKING_CATEGORIES
                and issue.severity in {"medium", "high"}
            ) + source_visibility_issues(assessment.source_integrity, metrics)
            critique = deterministic_dom_critique(replace(report, issues=structural_issues))
            if critique is None and cycle == 0 and initial_critiques is not None:
                critique = initial_critiques[i]
            if critique is None or not critique.needs_fix:
                history.append({"cycle": cycle, "stop_reason": "repair_measurements_unavailable"})
                break
            if any(issue.type in blocked_recovery_issue_types for issue in critique.issues):
                history.append({"cycle": cycle, "stop_reason": "provider_recovery_blocked"})
                break
            compact_dom = _structural_compact_dom(current_html, metrics)
            try:
                scopes = build_structural_repair_scopes(
                    compact_dom,
                    critique,
                    issue_qids=tuple(dict.fromkeys((
                        *(qid for issue in structural_issues for qid in issue.qids),
                        *_measured_structural_issue_qids(
                            metrics,
                            issue_qids=tuple(qid for issue in structural_issues for qid in issue.qids),
                        ),
                    ))),
                    html=current_html,
                )
            except ValueError as error:
                history.append({
                    "cycle": cycle,
                    "stop_reason": "repair_scope_unavailable",
                    "scope_failure": str(error),
                    "budget": budget.receipt(),
                })
                break
            if not scopes:
                history.append({
                    "cycle": cycle,
                    "stop_reason": "repair_scope_unavailable",
                    "budget": budget.receipt(),
                })
                break
            # Groups are returned in stable owner-QID order. A provisional
            # winner changes selected, so the next iteration discards this
            # tuple and reconstructs it from the newly measured DOM.
            scope = scopes[0]
            if rejected_reasons:
                critique = SlideCritique(
                    needs_fix=True,
                    issues=[*critique.issues[:7], CritiqueIssue(
                        type="overflow", severity="high",
                        detail=("Previous candidate rejected: " + "; ".join(rejected_reasons))[:200],
                    )],
                )
            started = time.monotonic()
            receipt: dict[str, Any] = {
                "cycle": cycle, "proposal_calls": 0, "capture_calls": 0,
                "critique_calls": 0,
                "selected_html_digest": "sha256:" + hashlib.sha256(
                    current_html.encode("utf-8")
                ).hexdigest(),
                "scope": {
                    "layout_owner_qid": scope.layout_owner_qid,
                    "affected_qids": list(scope.affected_qids),
                },
                "configured_model": None,
                "reasoning_effort": os.getenv("CODEX_REASONING_EFFORT") or None,
            }
            try:
                if not budget.claim_provider_transmission():
                    receipt["stop_reason"] = (
                        "candidate_capture_budget_exhausted"
                        if not budget.can_capture()
                        else "provider_budget_exhausted"
                    )
                    break
                receipt["proposal_calls"] = 1
                try:
                    receipt["configured_model"] = str(get_model() or "") or None
                except Exception:
                    pass
                async with _get_vqa_sem():
                    proposal = await propose_authored_slide_patch(
                        current_png, compact_dom, critique,
                        slide_index=absolute_index, slot_run=f"cycle-{cycle}",
                        retry_invalid_model_patch=False,
                        allow_source_reflow_safe_styles=True,
                        repair_scope=scope,
                        html=current_html,
                    )
                if not proposal.ok or proposal.plan is None:
                    reason = (
                        proposal.failure_reason
                        or proposal.fallback_reason
                        or "repair_search_exhausted"
                    )
                    receipt["decision"] = "reject"
                    receipt["reasons"] = [reason]
                    rejected_reasons = (reason,)
                    if budget.can_propose_model_candidate():
                        continue
                    receipt["stop_reason"] = reason
                    break
                applied = apply_authored_patch(
                    current_html, proposal.plan, allow_source_reflow_safe_styles=True,
                )
                if not applied.ok or not is_valid_slide_html(applied.html):
                    reason = applied.failure_reason or "patch_application_failed"
                    receipt["decision"] = "reject"
                    receipt["reasons"] = [reason]
                    rejected_reasons = (reason,)
                    if budget.can_propose_model_candidate():
                        continue
                    receipt["stop_reason"] = reason
                    break
                violations = source_repair_html_violations(current_html, applied.html)
                if violations:
                    receipt["stop_reason"] = "source_integrity_rejected"
                    receipt["reasons"] = list(violations)
                    break
                digest = hashlib.sha256(applied.html.encode("utf-8")).hexdigest()
                if digest in seen_digests:
                    receipt["stop_reason"] = "duplicate_candidate_digest"
                    break
                seen_digests.add(digest)
                if not budget.claim_capture():
                    receipt["stop_reason"] = "candidate_capture_budget_exhausted"
                    break
                receipt["capture_calls"] = 1
                candidate_html = applied.html
                if structural_candidate_capture is not None:
                    candidate_html, candidate_png, candidate_metrics = await structural_candidate_capture(
                        candidate_html, absolute_indices[i]
                    )
                else:
                    candidate_png, candidate_metrics = await render_html_to_png_with_metrics(candidate_html)
                final_digest = hashlib.sha256(candidate_html.encode("utf-8")).hexdigest()
                if final_digest != digest and final_digest in seen_digests:
                    receipt["stop_reason"] = "duplicate_candidate_digest"
                    break
                seen_digests.add(final_digest)
                scope_violations = protected_scope_violations(
                    current_html, candidate_html, metrics, candidate_metrics,
                    scope.protected_qids,
                )
                if scope_violations:
                    receipt["decision"] = "reject"
                    receipt["reasons"] = list(scope_violations)
                    if tuple(scope_violations) == rejected_reasons:
                        receipt["stop_reason"] = "repair_search_exhausted"
                        break
                    rejected_reasons = tuple(scope_violations)
                    continue
                candidate_assessment = await evaluator(
                    absolute_index, candidate_html, candidate_metrics,
                )
                receipt["decision"] = candidate_assessment.decision
                receipt["reasons"] = list(candidate_assessment.reasons)
                receipt["html_digest"] = final_digest
                if candidate_assessment.decision not in {"provisional", "admitted"}:
                    reasons = tuple(candidate_assessment.reasons)
                    if not reasons or reasons == rejected_reasons:
                        receipt["stop_reason"] = "repair_search_exhausted"
                        break
                    rejected_reasons = reasons
                    continue
                assessment = candidate_assessment
                current_html, current_png = candidate_html, candidate_png
                metrics = dict(candidate_metrics)
                rejected_reasons = ()
                next_context = None
                if assessment.decision == "provisional":
                    next_report = inspect_authored_dom(
                        current_html, metrics, strict_structural_off_canvas=True,
                    )
                    next_issues = tuple(
                        issue for issue in next_report.issues
                        if issue.category in RESIDUAL_BLOCKING_CATEGORIES
                        and issue.severity in {"medium", "high"}
                    ) + source_visibility_issues(candidate_assessment.source_integrity, candidate_metrics)
                    try:
                        next_scopes = build_structural_repair_scopes(
                            _structural_compact_dom(current_html, metrics),
                            deterministic_dom_critique(
                                replace(next_report, issues=next_issues)
                            ),
                            issue_qids=tuple(dict.fromkeys((
                                *(qid for issue in next_issues for qid in issue.qids),
                                *_measured_structural_issue_qids(
                                    metrics,
                                    issue_qids=tuple(qid for issue in next_issues for qid in issue.qids),
                                ),
                            ))),
                            html=current_html,
                        )
                    except ValueError:
                        next_scopes = ()
                    if next_scopes:
                        next_context = {
                            "protected_qids": tuple(sorted(set.intersection(
                                *(set(scope.protected_qids) for scope in next_scopes)
                            )))
                        }
                _advance_repair_evaluator(
                    evaluator,
                    assessment,
                    current_html,
                    metrics,
                    context=next_context,
                )
                receipt["element_changes"] = [
                    {"element_id": change.qid, "action": change.action,
                     "before": change.before, "after": change.after, "path": "local_patch"}
                    for change in applied.changes
                ]
                if assessment.admitted:
                    receipt["stop_reason"] = "admitted"
                    break
            except Exception as error:
                LOGGER.error(
                    "[authored-structural-repair] slide_index=%d cycle=%d failed",
                    absolute_indices[i], cycle, exc_info=True,
                )
                receipt["stop_reason"] = "repair_candidate_failed"
                receipt["error_type"] = type(error).__name__
                break
            finally:
                receipt["elapsed_ms"] = round((time.monotonic() - started) * 1000, 3)
                receipt["budget"] = budget.receipt()
                history.append(receipt)
        if cycle_count == 0 and not assessment.admitted:
            history.append({
                "cycle": 0,
                "stop_reason": "provider_recovery_blocked",
                "budget": budget.receipt(),
            })
        elif not history:
            history.append({
                "cycle": 0,
                "stop_reason": (
                    "admitted" if assessment.admitted else "provider_recovery_blocked"
                ),
                "budget": budget.receipt(),
            })
        elif not history[-1].get("stop_reason"):
            history[-1]["stop_reason"] = (
                "provider_budget_exhausted"
                if budget.provider_transmissions >= budget.max_provider_transmissions
                else "candidate_capture_budget_exhausted"
                if budget.candidate_captures >= budget.max_candidate_captures
                else "repair_search_exhausted"
            )
            history[-1]["budget"] = budget.receipt()
        assessment = bind_repair_candidate(assessment, current_html, current_png, metrics)
        return SlideRepairResult(
            local_index=i, slide_index=absolute_indices[i],
            baseline_html_digest=baseline_digest,
            html=current_html, png=current_png, metrics=metrics, assessment=assessment,
            changed="sha256:" + hashlib.sha256(current_html.encode("utf-8")).hexdigest() != baseline_digest,
            cycle_history=history,
        )

    results = list(await asyncio.gather(*(repair(i) for i in range(len(htmls)))))
    final_htmls, final_pngs = list(htmls), list(pngs)
    fixed: list[int] = []
    details = []
    for result in results:
        admitted = result.assessment is not None and result.assessment.admitted
        if admitted and result.changed:
            final_htmls[result.local_index] = result.html
            final_pngs[result.local_index] = result.png
            fixed.append(result.local_index)
        details.append({
            "local_index": result.local_index, "slide_index": result.slide_index,
            "path": "local_patch", "attempted": bool(result.cycle_history),
            "succeeded": admitted, "critique_source": "deterministic",
            "candidate_status": result.assessment.decision if result.assessment else "reject",
            "cycle_history": result.cycle_history,
            "element_changes": [
                change for cycle in result.cycle_history for change in cycle.get("element_changes", [])
            ] if admitted else [],
        })
    if repair_details_sink is not None:
        repair_details_sink.extend(details)
    if structural_results_sink is not None:
        structural_results_sink.extend(results)
    if review_stats_sink is not None:
        review_stats_sink.update(vqaReviewedSlideCount=0, deterministicOnlySlideCount=len(results))
    unresolved = [result for result in results if not result.assessment or not result.assessment.admitted]
    if unresolved and fail_on_residual_dom_issues:
        raise AuthoredVqaResidualDomIssuesError(
            ",".join(str(result.slide_index + 1) for result in unresolved),
            htmls=final_htmls, pngs=final_pngs, fixed=fixed,
            repair_details=details, repair_results=results,
        )
    if return_details:
        return final_htmls, final_pngs, fixed, details
    return final_htmls, final_pngs, fixed
