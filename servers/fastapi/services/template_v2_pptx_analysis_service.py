from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
import importlib
import sys
from time import perf_counter
from typing import Any
import uuid


_INGESTION_MODULE = "services.template_v2_pptx_ingestion_service"


def _runtime_dependencies() -> Any:
    """Resolve the public ingestion facade so its monkeypatch surface stays intact."""

    dependencies = sys.modules.get(_INGESTION_MODULE)
    if dependencies is None:
        dependencies = importlib.import_module(_INGESTION_MODULE)
    return dependencies


def with_private_asset_references(
    layouts: list[dict],
    relocated,
) -> list[dict]:
    """Point image elements at the relocated private assets before validation.

    The converter emits `/app_data` URLs for media it extracted. Those files have
    been moved into the import's private directory, so the URLs would 404 -- and an
    unreachable asset renders as a silently blank region.
    """

    rewritten: list[dict] = []
    for layout in layouts:
        raw_elements = layout.get("elements")
        if not isinstance(raw_elements, list):
            # Leave the layout untouched so `build_runtime_slide_layouts` can fail
            # closed on it. Defaulting to [] here would hand it a valid, empty
            # layout and turn a broken slide into a silently blank one.
            rewritten.append(layout)
            continue
        elements = []
        for element in raw_elements:
            data = element.get("data") if isinstance(element, dict) else None
            reference = relocated.reference_for(data) if isinstance(data, str) else None
            elements.append({**element, "data": reference} if reference else element)
        rewritten.append({**layout, "elements": elements})
    return rewritten


def analysis_source_inventory(
    marker: str,
    analysis_payload: dict,
    *,
    source_filename: str,
    source_media_type: str,
    source_size_bytes: int,
    source_sha256: str,
    artifacts: tuple[Any, ...] = (),
) -> dict:
    """Build the one inventory shape both analyzer paths record."""

    dependencies = _runtime_dependencies()
    return dependencies.SourceInventory(
        source=dependencies.SecretFreeSourceMetadata(
            display_filename=source_filename,
            media_type=source_media_type,
            size_bytes=source_size_bytes,
            sha256=source_sha256,
        ),
        artifacts=artifacts,
        candidates=(
            dependencies.candidate_inventory_item(marker, analysis_payload),
        ),
    ).to_manifest()


@contextmanager
def analysis_observation(provider: str) -> Iterator[dict[str, int]]:
    """Emit exactly one bounded metric per analysis attempt, from either path.

    The body reports the slide count it produced through ``observed["count"]``;
    ``provider`` must already be in the observability allowlist.
    """

    dependencies = _runtime_dependencies()
    started_at = perf_counter()
    observed = {"count": 0}
    try:
        yield observed
    except Exception:
        dependencies.log_pptx_analysis_observation(
            provider=provider,
            status="failed",
            duration_ms=(perf_counter() - started_at) * 1000,
            count=0,
        )
        raise
    dependencies.log_pptx_analysis_observation(
        provider=provider,
        status="completed",
        duration_ms=(perf_counter() - started_at) * 1000,
        count=observed["count"],
    )


def build_runtime_analysis(
    document,
    *,
    import_id: uuid.UUID,
    source_filename: str,
    source_media_type: str,
    source_size_bytes: int,
    source_sha256: str,
) -> tuple[dict, dict]:
    """Relocate the converter's media and validate its layouts. Blocking on purpose."""

    dependencies = _runtime_dependencies()
    relocated = dependencies.relocate_runtime_assets(
        document.output_dir,
        import_id=import_id,
    )
    private_layouts = dependencies._with_private_asset_references(
        document.layouts,
        relocated,
    )
    classified_layouts, classification = (
        dependencies.classify_runtime_fillable_layouts(private_layouts)
    )
    imported = dependencies.build_runtime_slide_layouts(classified_layouts)
    layouts = imported.raw_layouts.layouts
    # `build_runtime_slide_layouts` raises on any element it cannot represent, so a
    # payload that reaches here converted every shape the converter emitted.
    shape_count = sum(len(layout.elements) for layout in layouts)
    analysis_payload = {
        "analyzer": dependencies.RUNTIME_ANALYSIS_MARKER,
        # No `contract_version`: this is not the deterministic analyzer's
        # `CandidateAnalysis` document, and its own version is the marker above.
        # `provider.version`/`capability` are absent because the bundled converter
        # reports neither to this service.
        "provider": {
            "id": dependencies.RUNTIME_ANALYZER_PROVIDER,
            "execution": "local",
            "status": "available",
            "network_access": False,
            "external_ai": False,
        },
        "status": "completed",
        # The converter emits layouts only: no preview image, no render comparison.
        "preview": {
            "status": "not_provided",
            "reason": "not_emitted_by_converter",
        },
        "render": {
            "status": "not_run",
            "reason": "not_emitted_by_converter",
        },
        "summary": {
            "slide_count": len(layouts),
            "shape_count": shape_count,
            "supported_shape_count": shape_count,
            "unsupported_shape_count": 0,
            "visual_fidelity_status": "not_evaluated",
            "review_required": True,
        },
        "classification": classification.as_manifest(),
        "raw_layouts": imported.raw_layouts.model_dump(mode="json"),
        "layouts": imported.layouts.model_dump(mode="json"),
        "default_contents": dependencies.runtime_default_contents(imported.layouts),
    }
    inventory = dependencies._analysis_source_inventory(
        dependencies.RUNTIME_ANALYSIS_MARKER,
        analysis_payload,
        source_filename=source_filename,
        source_media_type=source_media_type,
        source_size_bytes=source_size_bytes,
        source_sha256=source_sha256,
    )
    return analysis_payload, inventory


async def analyze_import_source_via_runtime(
    storage_key: str,
    source_sha256: str,
    *,
    import_id: uuid.UUID,
    source_filename: str,
    source_media_type: str,
    source_size_bytes: int,
) -> tuple[dict, list[dict], dict]:
    """Extract with the bundled converter instead of the in-repo OOXML parser.

    Gains images, per-run text styling and vector shapes. Returns no repeat-block
    suggestions: those are derived from parser candidates, which this path does not
    produce.
    """

    dependencies = _runtime_dependencies()
    with dependencies._analysis_observation(
        dependencies.RUNTIME_ANALYZER_PROVIDER
    ) as observed:
        # Everything except the converter await is blocking -- hashing up to 100 MB,
        # copying media, validating every element, two model dumps -- so it is
        # offloaded like the deterministic path is. Leaving it inline stalls every
        # other request on the worker and delays the attempt's own heartbeat.
        source = await dependencies.asyncio.to_thread(
            dependencies.verify_private_source,
            storage_key,
            source_sha256,
            expected_import_id=import_id,
            expected_size_bytes=source_size_bytes,
        )
        document = await dependencies.EXPORT_TASK_SERVICE.convert_pptx_to_json(
            str(source),
            session_id=str(import_id),
        )
        analysis_payload, inventory = await dependencies.asyncio.to_thread(
            dependencies._build_runtime_analysis,
            document,
            import_id=import_id,
            source_filename=source_filename,
            source_media_type=source_media_type,
            source_size_bytes=source_size_bytes,
            source_sha256=source_sha256,
        )
        observed["count"] = analysis_payload["summary"]["slide_count"]
        return analysis_payload, [], inventory


def analyze_import_source(
    storage_key: str,
    source_sha256: str,
    *,
    import_id: uuid.UUID,
    source_filename: str,
    source_media_type: str,
    source_size_bytes: int,
) -> tuple[dict, list[dict], dict]:
    dependencies = _runtime_dependencies()
    with dependencies._analysis_observation(
        dependencies.DETERMINISTIC_ANALYZER_PROVIDER
    ) as observed:
        source = dependencies.verify_private_source(
            storage_key,
            source_sha256,
            expected_import_id=import_id,
            expected_size_bytes=source_size_bytes,
        )
        reader = dependencies.PptxPackageReader(source)
        candidates = dependencies.parse_presentation_candidates(
            reader,
            source_sha256=source_sha256,
        )
        analysis = dependencies.analyze_ooxml_candidates(candidates)
        analysis_payload = analysis.model_dump(mode="json")
        inventory = dependencies._analysis_source_inventory(
            dependencies.DETERMINISTIC_ANALYSIS_MARKER,
            analysis_payload,
            source_filename=source_filename,
            source_media_type=source_media_type,
            source_size_bytes=source_size_bytes,
            source_sha256=source_sha256,
            artifacts=reader.artifact_inventory(),
        )
        observed["count"] = analysis.summary.slide_count
        return (
            analysis_payload,
            dependencies.build_repeat_block_suggestions(candidates),
            inventory,
        )


def apply_deprecated_template_v2_constructor_bridge(
    canonical_values: dict,
    *,
    presentation_id: uuid.UUID,
) -> dict:
    """Supply legacy non-null columns during the two-stage sidecar rollout.

    New import lifecycle code must read and write ``TemplateV2LocalState``.
    This bridge exists only until a later migration removes the transitional
    columns from ``template_v2``.
    """

    dependencies = _runtime_dependencies()
    values = dict(canonical_values)
    if "presentation_id" in dependencies.TemplateV2.model_fields:
        values["presentation_id"] = presentation_id
    return values


async def persist_analysis(
    import_id: uuid.UUID,
    task_id: str,
    attempt_token: str,
    analysis_result: dict,
    repeat_suggestions: list[dict],
    source_inventory: dict | None = None,
) -> bool:
    dependencies = _runtime_dependencies()
    analyzed_at = dependencies._now()
    async with dependencies.async_session_maker() as session:
        gate = await session.execute(
            dependencies.update(dependencies.TemplateV2PptxImport)
            .where(
                dependencies.TemplateV2PptxImport.id == import_id,
                dependencies.TemplateV2PptxImport.task_id == task_id,
                dependencies.TemplateV2PptxImport.state == "processing",
                dependencies.TemplateV2PptxImport.attempt_token == attempt_token,
                dependencies.TemplateV2PptxImport.lease_expires_at > analyzed_at,
            )
            .values(
                state="finalizing",
                lease_expires_at=analyzed_at + dependencies.IMPORT_LEASE_DURATION,
                updated_at=analyzed_at,
            )
            .execution_options(synchronize_session=False)
        )
        if gate.rowcount != 1:
            await session.rollback()
            return False
        import_job = await session.get(dependencies.TemplateV2PptxImport, import_id)
        task = await session.get(
            dependencies.AsyncPresentationGenerationTaskModel,
            task_id,
        )
        if import_job is None or task is None:
            await session.rollback()
            return False
        import_job.state = "review_required"
        import_job.attempt_token = None
        import_job.lease_expires_at = None
        import_job.analysis_result = deepcopy(analysis_result)
        import_job.repeat_suggestions = deepcopy(repeat_suggestions)
        import_job.revision += 1
        manifest_updates = deepcopy(import_job.manifest or {})
        if source_inventory is not None:
            manifest_updates["source_inventory"] = deepcopy(source_inventory)
        retention_expires_at, manifest = dependencies.terminal_source_retention(
            {
                **manifest_updates,
                "attempt_number": import_job.attempt_number,
                "analysis": {
                    "contract_version": analysis_result.get("contract_version"),
                    "provider": deepcopy(analysis_result.get("provider")),
                    "status": analysis_result.get("status"),
                    "summary": deepcopy(analysis_result.get("summary")),
                },
                "review": {
                    "required": True,
                    "reason": "explicit_confirmation_required",
                },
            },
            terminal_at=analyzed_at,
        )
        import_job.manifest = manifest
        import_job.source_retention_expires_at = retention_expires_at
        import_job.source_cleanup_token = None
        import_job.source_cleanup_lease_expires_at = None
        import_job.source_cleanup_attempted_at = None
        import_job.source_deleted_at = None
        import_job.updated_at = analyzed_at
        task.status = "completed"
        task.message = "PPTX analysis complete; explicit confirmation required"
        task.error = None
        task.data = dependencies._task_data(
            import_id,
            state=import_job.state,
            attempt_number=import_job.attempt_number,
        )
        task.updated_at = dependencies._task_timestamp(analyzed_at)
        session.add(import_job)
        session.add(task)
        await session.commit()
        return True


def is_runtime_analysis(import_job: Any) -> bool:
    """Only the runtime analyzer stores references to the relocated private media."""

    dependencies = _runtime_dependencies()
    analysis_result = import_job.analysis_result
    return (
        isinstance(analysis_result, dict)
        and analysis_result.get("analyzer")
        == dependencies.RUNTIME_ANALYSIS_MARKER
    )


def runtime_assets_reclaimed(import_job: Any) -> bool:
    """True when a runtime import's layouts would reference reclaimed media.

    Only the runtime analyzer stores asset references; the deterministic analysis is
    self-contained, so losing its source after review costs nothing but the audit
    copy and must keep confirming.

    A held cleanup claim counts as reclaimed. Retention claims the row, deletes the
    source and the relocated media in a worker thread, and writes `source_deleted_at`
    only afterwards, so that flag alone leaves a window in which the files are already
    gone while the row still looks intact. The claim is only ever taken on a row whose
    retention deadline has already passed, and a cleaner that dies mid-run has its
    claim re-taken on lease expiry by the next cleanup pass -- which then either
    deletes (a permanent refusal, correctly) or clears the claim -- so refusing on the
    claim is bounded, not permanent.
    """

    dependencies = _runtime_dependencies()
    return dependencies._is_runtime_analysis(import_job) and (
        import_job.source_deleted_at is not None
        or import_job.source_cleanup_token is not None
    )


def runtime_confirmed_draft(analysis_result: dict) -> Any:
    """Rebuild the draft the runtime analyzer already validated.

    The two analyzers converge here rather than at the analysis: the deterministic
    path replays parser candidates through the assembler, while this path stored
    validated layouts directly. New analyses persist seed content only for
    conservative placeholder-name classifications. Analyses created before that
    classifier have no ``default_contents`` and retain legacy empty mappings.
    """

    dependencies = _runtime_dependencies()
    raw_payload = analysis_result.get("raw_layouts")
    layouts_payload = analysis_result.get("layouts")
    if not isinstance(raw_payload, dict) or not isinstance(layouts_payload, dict):
        raise ValueError("template_v2_import_runtime_layouts_missing")
    layouts = dependencies.SlideLayouts.model_validate(layouts_payload)
    contents = dependencies.restore_runtime_default_contents(
        layouts,
        analysis_result.get("default_contents"),
    )
    return dependencies.AssembledTemplateV2Draft(
        raw_layouts=dependencies.RawSlideLayouts.model_validate(raw_payload),
        layouts=layouts,
        contents=contents,
        manifest={"analyzer": dependencies.RUNTIME_ANALYSIS_MARKER},
    )


def assemble_confirmed_candidate(
    import_job: Any,
    accepted_repeat_suggestions: list[dict],
) -> Any:
    dependencies = _runtime_dependencies()
    analysis_result = import_job.analysis_result
    if not isinstance(analysis_result, dict):
        raise ValueError("template_v2_import_analysis_missing")
    if (
        analysis_result.get("analyzer")
        == dependencies.RUNTIME_ANALYSIS_MARKER
    ):
        return dependencies._runtime_confirmed_draft(analysis_result)
    candidate_payload = analysis_result.get("candidates")
    if not isinstance(candidate_payload, dict):
        raise ValueError("template_v2_import_candidate_missing")
    candidates = dependencies.PresentationCandidates.model_validate(
        candidate_payload
    )
    return dependencies.assemble_template_v2_draft(
        candidates,
        accepted_repeat_suggestions=accepted_repeat_suggestions,
    )
