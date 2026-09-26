# ruff: noqa: F821
from __future__ import annotations


def _deck_plan_checkpoint_input(
    *,
    generation_job: PresentationGenerationJob | None,
    request: GeneratePresentationRequest,
    outline: PresentationOutlineModel,
    brand: object,
    style: object,
    stage: str,
) -> dict[str, object]:
    """Build a digest-only input contract; the payload itself is never stored."""

    return {
        "schemaVersion": "authored-deck-plan-input/v1",
        "stage": stage,
        "requestSha256": (
            generation_job.request_sha256 if generation_job is not None else "legacy"
        ),
        "outline": outline.model_dump(mode="json"),
        "brand": {
            "topic": getattr(brand, "topic", None),
            "language": getattr(brand, "language", None),
            "primary": getattr(brand, "primary", None),
            "fonts": getattr(brand, "fonts", None),
        },
        "styleId": getattr(style, "id", None),
        "referenceMode": getattr(style, "reference_mode", None),
        "includeTableOfContents": request.include_table_of_contents,
        "hasFixedSlidesMarkdown": bool(request.slides_markdown),
        "instructionsDigest": canonical_checkpoint_digest(
            request.instructions or ""
        ),
    }

async def _provider_stage_observability_metrics(
    session: AsyncSession,
    operation_id: uuid.UUID,
) -> dict[str, dict[str, object]]:
    admission = await session.scalar(
        select(PublicOperationAdmission).where(
            PublicOperationAdmission.operation_id == operation_id
        )
    )
    if admission is None:
        return {
            name: _unavailable_metric("public_admission_not_found")
            for name in (
                "queuePosition",
                "stageRetryCalls",
                "stageEstimatedCost",
                "stageActualCost",
            )
        }
    calls = list(
        (
            await session.execute(
                select(PublicProviderLogicalCall).where(
                    PublicProviderLogicalCall.admission_id == admission.id
                )
            )
        ).scalars()
    )
    queue_ahead = int(
        await session.scalar(
            select(func.count())
            .select_from(PublicOperationAdmission)
            .where(PublicOperationAdmission.id != admission.id)
            .where(PublicOperationAdmission.policy_version == admission.policy_version)
            .where(PublicOperationAdmission.tenant_id == admission.tenant_id)
            .where(PublicOperationAdmission.operation_kind == admission.operation_kind)
            .where(PublicOperationAdmission.queued_at < admission.queued_at)
            .where(
                or_(
                    PublicOperationAdmission.activated_at.is_(None),
                    PublicOperationAdmission.activated_at > admission.queued_at,
                )
            )
        )
        or 0
    )
    if not calls:
        return {
            "queuePosition": _observed_metric(queue_ahead + 1),
            **{
            name: _not_applicable_metric("provider_not_used")
            for name in (
                "stageRetryCalls",
                "stageEstimatedCost",
                "stageActualCost",
            )
            },
        }
    retry_by_stage: dict[str, int] = {}
    estimated_by_stage: dict[str, int] = {}
    actual_by_stage: dict[str, int] = {}
    actual_available = True
    for call in calls:
        stage = str(call.stage).strip().lower()
        retry_by_stage.setdefault(stage, 0)
        estimated_by_stage.setdefault(stage, 0)
        actual_by_stage.setdefault(stage, 0)
        if call.retry_class != "initial":
            retry_by_stage[stage] += 1
        estimated_by_stage[stage] += max(0, int(call.estimated_cost_microunits))
        if call.actual_cost_microunits is None:
            actual_available = False
        else:
            actual_by_stage[stage] += max(0, int(call.actual_cost_microunits))
    return {
        "queuePosition": _observed_metric(queue_ahead + 1),
        "stageRetryCalls": _observed_metric(retry_by_stage),
        "stageEstimatedCost": _observed_metric(estimated_by_stage),
        "stageActualCost": (
            _observed_metric(actual_by_stage)
            if actual_available
            else _unavailable_metric("provider_actual_cost_not_reported")
        ),
    }

async def _fit_content_revision(
    html: str,
    *,
    slide_index: int,
    policy_revision: str | None = None,
    canvas: object | None = None,
    physical_canvas: object | None = None,
) -> AcceptedLocalFit:
    """Run a provider content correction through the same local-fit boundary.

    The provider result starts a new author revision.  A changed fitter result
    therefore receives its own one-shot, fit-disabled reconstruction instead
    of inheriting the receipt for the pre-VQA HTML.
    """

    original_digest = html_digest(html)
    remaining_operation_seconds = remaining_authored_local_fit_seconds(cap=120)
    if remaining_operation_seconds <= 0:
        raise AuthoredLocalFitRejected("deadline_exceeded")
    budget = slide_budget(60, remaining_operation_seconds)
    started_at = time.monotonic()
    slide_deadline = started_at + budget["total_seconds"]
    first_deadline = min(
        slide_deadline, started_at + budget["first_seconds"]
    )
    canvas_width_px = int(getattr(canvas, "width_px", 1280))
    canvas_height_px = int(getattr(canvas, "height_px", 720))
    render_kwargs = (
        {}
        if (canvas_width_px, canvas_height_px) == (1280, 720)
        else {"width_px": canvas_width_px, "height_px": canvas_height_px}
    )
    render_capture = partial(
        render_html_to_png_with_metrics,
        **render_kwargs,
    )
    from services.document_canvas_generation import export_size_points

    export_width_pt, export_height_pt = export_size_points(canvas, physical_canvas)
    policy = local_fit_policy(
        author_revision=original_digest,
        original_html_digest=original_digest,
        export_width_pt=export_width_pt,
        export_height_pt=export_height_pt,
        viewport_width=canvas_width_px,
        viewport_height=canvas_height_px,
        slide_index=slide_index,
        policy_revision=policy_revision or POLICY_REVISION,
    )
    first_remaining = max(0.001, first_deadline - time.monotonic())
    async with asyncio.timeout(first_remaining):
        fitted = await capture_once(
            html, index=slide_index, capture=render_capture,
            timeout=max(1, min(60, int(first_remaining))),
            fit=True,
            fit_policy=policy,
        )
    prepared_original_html = fitted.prepared_html
    if prepared_original_html is None:
        raise AuthoredLocalFitRejected("reconstruction_mismatch")
    policy = fitted.prepared_fit_policy or policy
    report = fitted.fit_report or {}
    changed = bool(report.get("changed")) or bool(
        fitted.fitted_html and fitted.fitted_html != prepared_original_html
    )
    if not changed:
        return accept_unchanged_capture(
            fitted,
            original_html=prepared_original_html,
            policy=policy,
        )
    verify_queued_at = time.monotonic()
    verify_started_at = time.monotonic()
    verify_deadline = min(
        slide_deadline,
        verify_queued_at + budget["verify_seconds"],
    )
    verify_remaining = max(0.001, verify_deadline - time.monotonic())
    async with asyncio.timeout(verify_remaining):
        verified = await capture_once(
            fitted.fitted_html, index=slide_index, capture=render_capture,
            timeout=max(1, min(60, int(verify_remaining))),
            fit=False,
            fit_policy={
                **dict(policy),
                "baseline": dict(report.get("baseline") or {}),
            },
            fresh=True,
        )
    verify_finished_at = time.monotonic()
    if verified.prepared_html is None:
        raise AuthoredLocalFitRejected("reconstruction_mismatch")
    policy = verified.prepared_fit_policy or policy
    return accept_verified_capture(
        fitted,
        verified,
        prepared_html=verified.prepared_html,
        original_html=prepared_original_html,
        policy=policy,
        verify_queued_at=verify_queued_at,
        verify_started_at=verify_started_at,
        verify_finished_at=verify_finished_at,
    )

async def _apply_final_checkpoint_header_eyebrow_fallback(
    *,
    pipeline,
    htmls: list[str],
    pngs: list[bytes],
    capacity_plan,
    header_contract: AuthoredHeaderContract | None,
    roles: list[str],
    metrics_override: dict[int, object],
    already_attempted: bool,
) -> tuple[bool, int | None]:
    """Try the strict local fallback once when the first gate missed recovery.

    The candidate metrics must be retained for the exact final HTML.  A caller
    that already entered the fallback after provider repair cannot enter again,
    even when its post-capture gate failed, so one deck attempt performs at most
    one deterministic eyebrow recapture.
    """

    if already_attempted:
        return True, None
    ledger = getattr(pipeline, "evidence_ledger", None)
    attempt_id = getattr(pipeline, "evidence_attempt_id", None)
    if ledger is None or not attempt_id:
        return False, None
    for index, html in enumerate(htmls):
        latest = ledger.latest(attempt_id, index)
        if (
            latest is None
            or latest.html != html
            or latest.browser_metrics is None
            or capacity_violations(
                html, latest.browser_metrics, capacity_plan.at(index)
            )
            != ("browser_text_not_visible:header.eyebrow",)
        ):
            continue
        fallback = await _apply_bounded_header_eyebrow_fallback(
            html=html,
            browser_metrics=latest.browser_metrics,
            capacity=capacity_plan.at(index),
            header_contract=header_contract,
            role=roles[index],
            slide_index=index,
            allow_immutable_checkpoint_geometry=(
                latest.capture_policy == "hydrated"
            ),
        )
        if fallback is None:
            return True, None
        htmls[index], pngs[index], metrics_override[index] = fallback
        return True, index
    return False, None

async def _checkpoint_source_reflow_vqa_repairs(
    generation_job: PresentationGenerationJob | None,
    error: AuthoredVqaResidualDomIssuesError,
    *,
    absolute_indices: Sequence[int] | None = None,
    persist_scene=None,
) -> None:
    """Persist successful local VQA patches before source-reflow fails closed."""

    if generation_job is None or not generation_job.attempt_token:
        return
    results = getattr(error, "repair_results", ())
    # Legacy `fixed` only means changed: it cannot authorize source checkpoints.
    if not results:
        return
    if absolute_indices is not None:
        expected = tuple(int(index) for index in absolute_indices)
        if len(expected) != len(set(expected)) or set(expected) != {
            result.slide_index for result in results
        }:
            raise ValueError("authored_vqa_checkpoint_index_mapping_invalid")
    seen: set[int] = set()
    for result in results:
        if result.slide_index < 0 or result.slide_index in seen:
            raise ValueError("authored_vqa_checkpoint_index_mapping_invalid")
        seen.add(result.slide_index)
    admitted_results = [
        result for result in results if result.final_status == "admitted" and result.changed
    ]
    for result in admitted_results:
        validate_repair_result(result, expected_slide_index=result.slide_index)
    fencing_token = getattr(generation_job, "_workspace_fencing_token", None)
    for result in admitted_results:
        absolute_index = result.slide_index
        if persist_scene is not None:
            persist_scene(result)
        # The original illustrate/render checkpoint is already complete here.
        # Updating its HTML first while leaving the old render evidence pointer
        # in place creates an intentionally inconsistent tuple, so the following
        # render CAS rejects its own repair as checkpoint corruption. Publish the
        # repaired HTML/PNG/metrics together through the render mutation instead.
        await _complete_stage_after_external_success(
            job_id=generation_job.id,
            slide_index=absolute_index,
            stage="render",
            attempt_token=generation_job.attempt_token,
            fencing_token=fencing_token,
            html=result.html,
            png=result.png,
            browser_metrics=structural_checkpoint_metrics(result),
        )

_LOCAL_NAMES = frozenset(globals())

def bind_runtime(runtime) -> None:
    for name, value in vars(runtime).items():
        if name not in _LOCAL_NAMES and not name.startswith("__"):
            globals()[name] = value
