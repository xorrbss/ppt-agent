from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import logging
from time import perf_counter
import uuid

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from models.sql.template_v2 import TemplateV2
from models.sql.template_v2_local_state import TemplateV2LocalState
from models.sql.template_v2_pptx_import import TemplateV2PptxImport
from services.database import async_session_maker
from services.export_task_service import EXPORT_TASK_SERVICE
from services.template_v2_pptx_retention_service import (
    maybe_cleanup_expired_private_sources,
    terminal_source_retention,
)
from services.template_v2_pptx_observability import (
    log_pptx_analysis_observation,
)
from services.template_v2_pptx_queue_observability import (
    log_pptx_queue_observation,
)
from services.template_v2_pptx_storage import (
    get_private_source_retention_ttl,
    relocate_runtime_assets,
    verify_private_source,
)
from templates.v2.constants import TEMPLATE_V2_VERSION
from templates.v2.models.layouts import RawSlideLayouts, SlideLayouts
from templates.v2.generation import build_generated_slide
from templates.v2.pptx.assembler import (
    AssembledTemplateV2Draft,
    assemble_template_v2_draft,
)
from templates.v2.pptx.analyzer import analyze_ooxml_candidates
from templates.v2.pptx.models import PresentationCandidates
from templates.v2.pptx.ooxml_parser import parse_presentation_candidates
from templates.v2.pptx.runtime_layouts import build_runtime_slide_layouts
from templates.v2.pptx.package_reader import PptxPackageReader, UnsafePptxPackage
from templates.v2.pptx.repeat_suggestions import (
    build_repeat_block_suggestions,
)
from templates.v2.pptx.repeat_application import (
    resolve_repeat_suggestion_decisions,
)
from templates.v2.pptx.source_inventory import (
    HashedInventoryItem,
    SecretFreeSourceMetadata,
    SourceInventory,
    candidate_inventory_item,
)
from templates.v2.policy import get_structured_template_policy
from templates.v2.strategies import (
    TEMPLATE_V2_STRATEGIES,
    resolve_presentation_strategies,
)
from utils.datetime_utils import get_current_utc_datetime


logger = logging.getLogger(__name__)
IMPORT_TASK_KIND = "template-v2-pptx-import"
IMPORT_LEASE_DURATION = timedelta(minutes=5)
IMPORT_HEARTBEAT_INTERVAL_SECONDS = 30
IMPORT_DISPATCH_INTERVAL_SECONDS = 5
IMPORT_DISPATCH_BATCH_SIZE = 20


class AttemptOwnershipLost(RuntimeError):
    pass


def _now() -> datetime:
    return get_current_utc_datetime()


def _task_timestamp(value: datetime) -> datetime:
    """Match the legacy task table's timezone-naive UTC timestamp columns."""

    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _task_data(
    import_id: uuid.UUID,
    *,
    state: str,
    attempt_number: int,
    draft_template_id: str | None = None,
) -> dict:
    data = {
        "kind": IMPORT_TASK_KIND,
        "import_id": str(import_id),
        "state": state,
        "attempt_number": attempt_number,
    }
    if draft_template_id is not None:
        data["draft_template_id"] = draft_template_id
    return data


async def claim_template_v2_pptx_import(
    session: AsyncSession,
    import_id: uuid.UUID,
    task_id: str,
    *,
    token: str | None = None,
    now: datetime | None = None,
) -> str | None:
    """Atomically claim one queued row; exactly one concurrent caller wins."""

    claimed_at = now or _now()
    attempt_token = token or uuid.uuid4().hex
    result = await session.execute(
        update(TemplateV2PptxImport)
        .where(
            TemplateV2PptxImport.id == import_id,
            TemplateV2PptxImport.task_id == task_id,
            TemplateV2PptxImport.state == "queued",
            TemplateV2PptxImport.attempt_token.is_(None),
        )
        .values(
            state="processing",
            attempt_token=attempt_token,
            attempt_number=TemplateV2PptxImport.attempt_number + 1,
            lease_expires_at=claimed_at + IMPORT_LEASE_DURATION,
            heartbeat_at=claimed_at,
            last_started_at=claimed_at,
            updated_at=claimed_at,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        await session.rollback()
        return None
    import_job = await session.get(TemplateV2PptxImport, import_id)
    if import_job is None:
        await session.rollback()
        return None
    task_result = await session.execute(
        update(AsyncPresentationGenerationTaskModel)
        .where(AsyncPresentationGenerationTaskModel.id == task_id)
        .values(
            status="running",
            message="Validating private PPTX package",
            error=None,
            data=_task_data(
                import_id,
                state="processing",
                attempt_number=import_job.attempt_number,
            ),
            updated_at=_task_timestamp(claimed_at),
        )
        .execution_options(synchronize_session=False)
    )
    if task_result.rowcount != 1:
        await session.rollback()
        return None
    await session.commit()
    return attempt_token


async def heartbeat_template_v2_pptx_import(
    import_id: uuid.UUID,
    task_id: str,
    attempt_token: str,
    *,
    now: datetime | None = None,
) -> bool:
    heartbeat_at = now or _now()
    async with async_session_maker() as session:
        result = await session.execute(
            update(TemplateV2PptxImport)
            .where(
                TemplateV2PptxImport.id == import_id,
                TemplateV2PptxImport.task_id == task_id,
                TemplateV2PptxImport.state.in_(("processing", "finalizing")),
                TemplateV2PptxImport.attempt_token == attempt_token,
                TemplateV2PptxImport.lease_expires_at > heartbeat_at,
            )
            .values(
                heartbeat_at=heartbeat_at,
                lease_expires_at=heartbeat_at + IMPORT_LEASE_DURATION,
                updated_at=heartbeat_at,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            await session.rollback()
            return False
        await session.commit()
        return True


async def _heartbeat_loop(
    import_id: uuid.UUID,
    task_id: str,
    attempt_token: str,
    stop: asyncio.Event,
    ownership_lost: asyncio.Event,
) -> None:
    while not stop.is_set():
        try:
            await asyncio.wait_for(
                stop.wait(),
                timeout=IMPORT_HEARTBEAT_INTERVAL_SECONDS,
            )
            return
        except TimeoutError:
            try:
                owned = await heartbeat_template_v2_pptx_import(
                    import_id,
                    task_id,
                    attempt_token,
                )
            except Exception:
                logger.exception(
                    "Template V2 PPTX import heartbeat failed for %s",
                    import_id,
                )
                continue
            if not owned:
                ownership_lost.set()
                return


RUNTIME_ANALYZER_PROVIDER = "runtime-pptx-to-json"
RUNTIME_ANALYSIS_MARKER = "runtime-pptx-to-json-v1"
DETERMINISTIC_ANALYZER_PROVIDER = "deterministic-ooxml-static"
DETERMINISTIC_ANALYSIS_MARKER = "deterministic-ooxml-static-analysis-v1"


def _with_private_asset_references(
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


def _analysis_source_inventory(
    marker: str,
    analysis_payload: dict,
    *,
    source_filename: str,
    source_media_type: str,
    source_size_bytes: int,
    source_sha256: str,
    artifacts: tuple[HashedInventoryItem, ...] = (),
) -> dict:
    """Build the one inventory shape both analyzer paths record."""

    return SourceInventory(
        source=SecretFreeSourceMetadata(
            display_filename=source_filename,
            media_type=source_media_type,
            size_bytes=source_size_bytes,
            sha256=source_sha256,
        ),
        artifacts=artifacts,
        candidates=(candidate_inventory_item(marker, analysis_payload),),
    ).to_manifest()


@contextmanager
def _analysis_observation(provider: str) -> Iterator[dict[str, int]]:
    """Emit exactly one bounded metric per analysis attempt, from either path.

    The body reports the slide count it produced through ``observed["count"]``;
    ``provider`` must already be in the observability allowlist.
    """

    started_at = perf_counter()
    observed = {"count": 0}
    try:
        yield observed
    except Exception:
        log_pptx_analysis_observation(
            provider=provider,
            status="failed",
            duration_ms=(perf_counter() - started_at) * 1000,
            count=0,
        )
        raise
    log_pptx_analysis_observation(
        provider=provider,
        status="completed",
        duration_ms=(perf_counter() - started_at) * 1000,
        count=observed["count"],
    )


def _build_runtime_analysis(
    document,
    *,
    import_id: uuid.UUID,
    source_filename: str,
    source_media_type: str,
    source_size_bytes: int,
    source_sha256: str,
) -> tuple[dict, dict]:
    """Relocate the converter's media and validate its layouts. Blocking on purpose."""

    relocated = relocate_runtime_assets(document.output_dir, import_id=import_id)
    imported = build_runtime_slide_layouts(
        _with_private_asset_references(document.layouts, relocated)
    )
    layouts = imported.raw_layouts.layouts
    # `build_runtime_slide_layouts` raises on any element it cannot represent, so a
    # payload that reaches here converted every shape the converter emitted.
    shape_count = sum(len(layout.elements) for layout in layouts)
    analysis_payload = {
        "analyzer": RUNTIME_ANALYSIS_MARKER,
        # No `contract_version`: this is not the deterministic analyzer's
        # `CandidateAnalysis` document, and its own version is the marker above.
        # `provider.version`/`capability` are absent because the bundled converter
        # reports neither to this service.
        "provider": {
            "id": RUNTIME_ANALYZER_PROVIDER,
            "execution": "local",
            "status": "available",
            "network_access": False,
            "external_ai": False,
        },
        "status": "completed",
        # The converter emits layouts only: no preview image, no render comparison.
        "preview": {"status": "not_provided", "reason": "not_emitted_by_converter"},
        "render": {"status": "not_run", "reason": "not_emitted_by_converter"},
        "summary": {
            "slide_count": len(layouts),
            "shape_count": shape_count,
            "supported_shape_count": shape_count,
            "unsupported_shape_count": 0,
            "visual_fidelity_status": "not_evaluated",
            "review_required": True,
        },
        "raw_layouts": imported.raw_layouts.model_dump(mode="json"),
        "layouts": imported.layouts.model_dump(mode="json"),
    }
    inventory = _analysis_source_inventory(
        RUNTIME_ANALYSIS_MARKER,
        analysis_payload,
        source_filename=source_filename,
        source_media_type=source_media_type,
        source_size_bytes=source_size_bytes,
        source_sha256=source_sha256,
    )
    return analysis_payload, inventory


async def _analyze_import_source_via_runtime(
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

    with _analysis_observation(RUNTIME_ANALYZER_PROVIDER) as observed:
        # Everything except the converter await is blocking -- hashing up to 100 MB,
        # copying media, validating every element, two model dumps -- so it is
        # offloaded like the deterministic path is. Leaving it inline stalls every
        # other request on the worker and delays the attempt's own heartbeat.
        source = await asyncio.to_thread(
            verify_private_source,
            storage_key,
            source_sha256,
            expected_import_id=import_id,
            expected_size_bytes=source_size_bytes,
        )
        document = await EXPORT_TASK_SERVICE.convert_pptx_to_json(
            str(source), session_id=str(import_id)
        )
        analysis_payload, inventory = await asyncio.to_thread(
            _build_runtime_analysis,
            document,
            import_id=import_id,
            source_filename=source_filename,
            source_media_type=source_media_type,
            source_size_bytes=source_size_bytes,
            source_sha256=source_sha256,
        )
        observed["count"] = analysis_payload["summary"]["slide_count"]
        return analysis_payload, [], inventory


def _analyze_import_source(
    storage_key: str,
    source_sha256: str,
    *,
    import_id: uuid.UUID,
    source_filename: str,
    source_media_type: str,
    source_size_bytes: int,
) -> tuple[dict, list[dict], dict]:
    with _analysis_observation(DETERMINISTIC_ANALYZER_PROVIDER) as observed:
        source = verify_private_source(
            storage_key,
            source_sha256,
            expected_import_id=import_id,
            expected_size_bytes=source_size_bytes,
        )
        reader = PptxPackageReader(source)
        candidates = parse_presentation_candidates(
            reader,
            source_sha256=source_sha256,
        )
        analysis = analyze_ooxml_candidates(candidates)
        analysis_payload = analysis.model_dump(mode="json")
        inventory = _analysis_source_inventory(
            DETERMINISTIC_ANALYSIS_MARKER,
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
            build_repeat_block_suggestions(candidates),
            inventory,
        )


def _apply_deprecated_template_v2_constructor_bridge(
    canonical_values: dict,
    *,
    presentation_id: uuid.UUID,
) -> dict:
    """Supply legacy non-null columns during the two-stage sidecar rollout.

    New import lifecycle code must read and write ``TemplateV2LocalState``.
    This bridge exists only until a later migration removes the transitional
    columns from ``template_v2``.
    """

    values = dict(canonical_values)
    if "presentation_id" in TemplateV2.model_fields:
        values["presentation_id"] = presentation_id
    return values


async def _persist_analysis(
    import_id: uuid.UUID,
    task_id: str,
    attempt_token: str,
    analysis_result: dict,
    repeat_suggestions: list[dict],
    source_inventory: dict | None = None,
) -> bool:
    analyzed_at = _now()
    async with async_session_maker() as session:
        gate = await session.execute(
            update(TemplateV2PptxImport)
            .where(
                TemplateV2PptxImport.id == import_id,
                TemplateV2PptxImport.task_id == task_id,
                TemplateV2PptxImport.state == "processing",
                TemplateV2PptxImport.attempt_token == attempt_token,
                TemplateV2PptxImport.lease_expires_at > analyzed_at,
            )
            .values(
                state="finalizing",
                lease_expires_at=analyzed_at + IMPORT_LEASE_DURATION,
                updated_at=analyzed_at,
            )
            .execution_options(synchronize_session=False)
        )
        if gate.rowcount != 1:
            await session.rollback()
            return False
        import_job = await session.get(TemplateV2PptxImport, import_id)
        task = await session.get(AsyncPresentationGenerationTaskModel, task_id)
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
        retention_expires_at, manifest = terminal_source_retention(
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
        task.data = _task_data(
            import_id,
            state=import_job.state,
            attempt_number=import_job.attempt_number,
        )
        task.updated_at = _task_timestamp(analyzed_at)
        session.add(import_job)
        session.add(task)
        await session.commit()
        return True


def _is_runtime_analysis(import_job: TemplateV2PptxImport) -> bool:
    """Only the runtime analyzer stores references to the relocated private media."""

    analysis_result = import_job.analysis_result
    return (
        isinstance(analysis_result, dict)
        and analysis_result.get("analyzer") == RUNTIME_ANALYSIS_MARKER
    )


def _runtime_assets_reclaimed(import_job: TemplateV2PptxImport) -> bool:
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

    return _is_runtime_analysis(import_job) and (
        import_job.source_deleted_at is not None
        or import_job.source_cleanup_token is not None
    )


def _runtime_confirmed_draft(analysis_result: dict) -> AssembledTemplateV2Draft:
    """Rebuild the draft the runtime analyzer already validated.

    The two analyzers converge here rather than at the analysis: the deterministic
    path replays parser candidates through the assembler, while this path stored
    validated layouts directly. `contents` is one empty mapping per layout because
    the converter marks every element decorative, so the template schema is empty
    and `build_generated_slide` takes the layout as-is.
    """

    raw_payload = analysis_result.get("raw_layouts")
    layouts_payload = analysis_result.get("layouts")
    if not isinstance(raw_payload, dict) or not isinstance(layouts_payload, dict):
        raise ValueError("template_v2_import_runtime_layouts_missing")
    layouts = SlideLayouts.model_validate(layouts_payload)
    return AssembledTemplateV2Draft(
        raw_layouts=RawSlideLayouts.model_validate(raw_payload),
        layouts=layouts,
        contents=[{} for _ in layouts.layouts],
        manifest={"analyzer": RUNTIME_ANALYSIS_MARKER},
    )


def _assemble_confirmed_candidate(
    import_job: TemplateV2PptxImport,
    accepted_repeat_suggestions: list[dict],
) -> AssembledTemplateV2Draft:
    analysis_result = import_job.analysis_result
    if not isinstance(analysis_result, dict):
        raise ValueError("template_v2_import_analysis_missing")
    if analysis_result.get("analyzer") == RUNTIME_ANALYSIS_MARKER:
        return _runtime_confirmed_draft(analysis_result)
    candidate_payload = analysis_result.get("candidates")
    if not isinstance(candidate_payload, dict):
        raise ValueError("template_v2_import_candidate_missing")
    candidates = PresentationCandidates.model_validate(candidate_payload)
    return assemble_template_v2_draft(
        candidates,
        accepted_repeat_suggestions=accepted_repeat_suggestions,
    )


async def confirm_template_v2_pptx_import(
    session: AsyncSession,
    import_id: uuid.UUID,
    task_id: str,
    *,
    owner_scope: str,
    expected_revision: int,
    accepted_repeat_suggestion_ids: tuple[str, ...] = (),
) -> str:
    """Create Template V2 once, only after an owner-scoped explicit confirm."""
    import_job = (
        await session.execute(
            select(TemplateV2PptxImport).where(
                TemplateV2PptxImport.id == import_id,
                TemplateV2PptxImport.task_id == task_id,
                TemplateV2PptxImport.owner_scope == owner_scope,
            )
        )
    ).scalar_one_or_none()
    if import_job is None:
        return "not_found"
    if import_job.state == "confirmed" and import_job.draft_template_id:
        return "already_confirmed"
    if import_job.state != "review_required":
        return "state_conflict"
    if import_job.revision != expected_revision:
        return "revision_conflict"
    if _runtime_assets_reclaimed(import_job):
        # Retention reclaims the source and the relocated media together, and
        # `review_required` is an eligible state, so an import can sit past its TTL
        # and still look confirmable. Confirming it would persist a template whose
        # every image reference points at a deleted file -- unrecoverable, since the
        # source deck is gone too, and silent, since the endpoint 404s per asset and
        # export still succeeds.
        return "assets_reclaimed"
    if await session.get(TemplateV2, import_job.requested_template_id) is not None:
        return "template_conflict"

    try:
        accepted_suggestions, repeat_decisions = (
            resolve_repeat_suggestion_decisions(
                import_job.repeat_suggestions or [],
                accepted_repeat_suggestion_ids,
            )
        )
        assembled = _assemble_confirmed_candidate(
            import_job,
            accepted_suggestions,
        )
    except ValueError as error:
        if str(error) in {
            "duplicate_repeat_suggestion_id",
            "unknown_repeat_suggestion_id",
            "invalid_repeat_suggestion",
            "repeat_suggestion_source_missing",
            "overlapping_repeat_suggestions",
        }:
            return "suggestion_conflict"
        raise
    confirmed_at = _now()
    gate_predicates = [
        TemplateV2PptxImport.id == import_id,
        TemplateV2PptxImport.task_id == task_id,
        TemplateV2PptxImport.owner_scope == owner_scope,
        TemplateV2PptxImport.state == "review_required",
        TemplateV2PptxImport.revision == expected_revision,
        TemplateV2PptxImport.draft_template_id.is_(None),
    ]
    if _is_runtime_analysis(import_job):
        # The guard above reads the row loaded when this request started; retention
        # claims and empties the private directory without touching `state` or
        # `revision`, so nothing else here would notice a cleanup that began after
        # that read. Only the runtime path is narrowed: a deterministic import is
        # still allowed to confirm once its source is gone.
        gate_predicates.append(TemplateV2PptxImport.source_deleted_at.is_(None))
        gate_predicates.append(TemplateV2PptxImport.source_cleanup_token.is_(None))
    gate = await session.execute(
        update(TemplateV2PptxImport)
        .where(*gate_predicates)
        .values(
            state="confirming",
            revision=TemplateV2PptxImport.revision + 1,
            updated_at=confirmed_at,
        )
        .execution_options(synchronize_session=False)
    )
    if gate.rowcount != 1:
        await session.rollback()
        current = (
            await session.execute(
                select(TemplateV2PptxImport).where(
                    TemplateV2PptxImport.id == import_id,
                    TemplateV2PptxImport.owner_scope == owner_scope,
                )
            )
        ).scalar_one_or_none()
        if current and current.state == "confirmed" and current.draft_template_id:
            return "already_confirmed"
        if current is not None and _runtime_assets_reclaimed(current):
            return "assets_reclaimed"
        return "revision_conflict"

    presentation = PresentationModel(
        content=f"Private PPTX import {import_job.source_filename}",
        n_slides=len(assembled.layouts.layouts),
        language="en",
        title=import_job.source_filename.rsplit(".", 1)[0],
        layout=None,
        structure=None,
        theme={"mode": "template"},
        mode="template",
        version=TEMPLATE_V2_VERSION,
    )
    slides: list[SlideModel] = []
    for index, layout in enumerate(assembled.layouts.layouts):
        generated = build_generated_slide(layout, assembled.contents[index])
        slides.append(
            SlideModel(
                presentation=presentation.id,
                layout_group="native",
                layout=generated.layout_id,
                index=index,
                content=generated.content,
                ui=generated.ui,
                html_content=None,
                properties=None,
            )
        )
    if resolve_presentation_strategies(presentation, slides) != TEMPLATE_V2_STRATEGIES:
        await session.rollback()
        raise RuntimeError("template_v2_strategy_boundary_violation")
    all_components = [
        component.model_dump(mode="json")
        for layout in assembled.layouts.layouts
        for component in layout.components
    ]
    template = TemplateV2(
        **_apply_deprecated_template_v2_constructor_bridge(
            {
                "id": import_job.requested_template_id,
                "name": presentation.title or "Imported PPTX",
                "description": (
                    "Confirmed deterministic OOXML import; visual review retained."
                ),
                "raw_layouts": assembled.raw_layouts.model_dump(mode="json"),
                "components": {"components": all_components},
                "merged_components": None,
                "layouts": assembled.layouts.model_dump(mode="json"),
                "assets": None,
                "is_default": False,
            },
            presentation_id=presentation.id,
        )
    )
    local_state = TemplateV2LocalState(
        template_id=template.id,
        presentation_id=presentation.id,
        revision=1,
    )
    manifest = {
        **deepcopy(import_job.manifest or {}),
        "confirmation": {
            "confirmed_at": confirmed_at.isoformat(),
            "repeat_suggestions_applied": bool(accepted_suggestions),
            "accepted_repeat_suggestion_ids": [
                suggestion["id"] for suggestion in accepted_suggestions
            ],
            "unapplied_repeat_suggestion_ids": [
                suggestion["id"]
                for suggestion in repeat_decisions
                if suggestion["status"] == "unapplied"
            ],
        },
        "review": {
            "required": False,
            "reason": "owner_confirmed_candidate",
        },
    }
    session.add(presentation)
    session.add_all(slides)
    session.add(template)
    session.add(local_state)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        return "template_conflict"
    await session.execute(
        update(TemplateV2PptxImport)
        .where(
            TemplateV2PptxImport.id == import_id,
            TemplateV2PptxImport.owner_scope == owner_scope,
            TemplateV2PptxImport.state == "confirming",
            TemplateV2PptxImport.revision == expected_revision + 1,
        )
        .values(
            state="confirmed",
            draft_template_id=template.id,
            confirmed_at=confirmed_at,
            manifest=manifest,
            repeat_suggestions=repeat_decisions,
            updated_at=confirmed_at,
        )
        .execution_options(synchronize_session=False)
    )
    task = await session.get(AsyncPresentationGenerationTaskModel, task_id)
    if task is None:
        await session.rollback()
        return "state_conflict"
    task.status = "completed"
    task.message = "Template V2 created after explicit confirmation"
    task.error = None
    task.data = _task_data(
        import_id,
        state="confirmed",
        attempt_number=import_job.attempt_number,
        draft_template_id=template.id,
    )
    task.updated_at = _task_timestamp(confirmed_at)
    session.add(task)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        current = (
            await session.execute(
                select(TemplateV2PptxImport).where(
                    TemplateV2PptxImport.id == import_id,
                    TemplateV2PptxImport.owner_scope == owner_scope,
                )
            )
        ).scalar_one_or_none()
        if current and current.state == "confirmed" and current.draft_template_id:
            return "already_confirmed"
        return "template_conflict"
    return "confirmed"


def _failure_code(error: Exception) -> str:
    if isinstance(error, IntegrityError):
        return "template_v2_import_analysis_persistence_conflict"
    if isinstance(error, UnsafePptxPackage):
        return error.code
    return getattr(error, "code", "template_v2_pptx_import_failed")


async def fail_template_v2_pptx_import(
    import_id: uuid.UUID,
    task_id: str,
    attempt_token: str,
    error: Exception,
) -> bool:
    """Record failure only while the caller still owns the active attempt."""

    failed_at = _now()
    code = _failure_code(error)
    async with async_session_maker() as session:
        import_job = await session.get(TemplateV2PptxImport, import_id)
        if import_job is None:
            return False
        retention_expires_at, manifest = terminal_source_retention(
            {
                **deepcopy(import_job.manifest or {}),
                "attempt_number": import_job.attempt_number,
                "failure": {"code": code},
            },
            terminal_at=failed_at,
        )
        result = await session.execute(
            update(TemplateV2PptxImport)
            .where(
                TemplateV2PptxImport.id == import_id,
                TemplateV2PptxImport.task_id == task_id,
                TemplateV2PptxImport.state.in_(("processing", "finalizing")),
                TemplateV2PptxImport.attempt_token == attempt_token,
                TemplateV2PptxImport.lease_expires_at > failed_at,
            )
            .values(
                state="failed",
                revision=TemplateV2PptxImport.revision + 1,
                attempt_token=None,
                lease_expires_at=None,
                source_retention_expires_at=retention_expires_at,
                source_cleanup_token=None,
                source_cleanup_lease_expires_at=None,
                source_cleanup_attempted_at=None,
                source_deleted_at=None,
                manifest=manifest,
                updated_at=failed_at,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            await session.rollback()
            return False
        task = await session.get(AsyncPresentationGenerationTaskModel, task_id)
        if task is None:
            await session.rollback()
            return False
        task.status = "error"
        task.message = "Template V2 PPTX import failed"
        task.error = {"code": code}
        task.data = _task_data(
            import_id,
            state="failed",
            attempt_number=import_job.attempt_number,
        )
        task.updated_at = _task_timestamp(failed_at)
        session.add(task)
        await session.commit()
        return True


async def requeue_failed_template_v2_pptx_import(
    session: AsyncSession,
    import_id: uuid.UUID,
    task_id: str,
    manifest: dict,
    *,
    expected_revision: int | None = None,
    now: datetime | None = None,
) -> bool:
    """Atomically move exactly one failed import back to the durable queue."""

    queued_at = now or _now()
    legacy_cutoff = queued_at - get_private_source_retention_ttl()
    retry_manifest = {
        **deepcopy(manifest),
        "private_source_retention": {
            **deepcopy(manifest.get("private_source_retention") or {}),
            "superseded_by_retry_at": queued_at.isoformat(),
        },
    }
    predicates = [
        TemplateV2PptxImport.id == import_id,
        TemplateV2PptxImport.task_id == task_id,
        TemplateV2PptxImport.state == "failed",
        TemplateV2PptxImport.attempt_token.is_(None),
        TemplateV2PptxImport.source_deleted_at.is_(None),
        TemplateV2PptxImport.source_cleanup_token.is_(None),
        or_(
            TemplateV2PptxImport.source_retention_expires_at > queued_at,
            and_(
                TemplateV2PptxImport.source_retention_expires_at.is_(None),
                TemplateV2PptxImport.updated_at > legacy_cutoff,
            ),
        ),
    ]
    if expected_revision is not None:
        predicates.append(TemplateV2PptxImport.revision == expected_revision)
    result = await session.execute(
        update(TemplateV2PptxImport)
        .where(*predicates)
        .values(
            state="queued",
            revision=TemplateV2PptxImport.revision + 1,
            attempt_token=None,
            lease_expires_at=None,
            source_retention_expires_at=None,
            source_cleanup_token=None,
            source_cleanup_lease_expires_at=None,
            source_cleanup_attempted_at=None,
            source_deleted_at=None,
            analysis_result=None,
            repeat_suggestions=[],
            manifest=retry_manifest,
            updated_at=queued_at,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        await session.rollback()
        return False
    attempt_number = (
        await session.execute(
            select(TemplateV2PptxImport.attempt_number).where(
                TemplateV2PptxImport.id == import_id
            )
        )
    ).scalar_one()
    task_result = await session.execute(
        update(AsyncPresentationGenerationTaskModel)
        .where(
            AsyncPresentationGenerationTaskModel.id == task_id,
            AsyncPresentationGenerationTaskModel.status == "error",
        )
        .values(
            status="pending",
            message="Queued for private PPTX validation retry",
            error=None,
            data=_task_data(
                import_id,
                state="queued",
                attempt_number=attempt_number,
            ),
            updated_at=_task_timestamp(queued_at),
        )
        .execution_options(synchronize_session=False)
    )
    if task_result.rowcount != 1:
        await session.rollback()
        return False
    await session.commit()
    notify_template_v2_pptx_dispatcher()
    return True


async def cancel_template_v2_pptx_import(
    session: AsyncSession,
    import_id: uuid.UUID,
    task_id: str,
    *,
    owner_scope: str,
    expected_revision: int,
    now: datetime | None = None,
) -> bool:
    """Cancel an owner-scoped import with optimistic concurrency."""
    cancelled_at = now or _now()
    import_job = (
        await session.execute(
            select(TemplateV2PptxImport).where(
                TemplateV2PptxImport.id == import_id,
                TemplateV2PptxImport.task_id == task_id,
                TemplateV2PptxImport.owner_scope == owner_scope,
            )
        )
    ).scalar_one_or_none()
    if import_job is None:
        return False
    if import_job.state == "cancelled":
        return True
    retention_expires_at, manifest = terminal_source_retention(
        {
            **deepcopy(import_job.manifest or {}),
            "cancelled_at": cancelled_at.isoformat(),
            "review": {
                "required": False,
                "reason": "owner_cancelled",
            },
        },
        terminal_at=cancelled_at,
    )
    result = await session.execute(
        update(TemplateV2PptxImport)
        .where(
            TemplateV2PptxImport.id == import_id,
            TemplateV2PptxImport.task_id == task_id,
            TemplateV2PptxImport.owner_scope == owner_scope,
            TemplateV2PptxImport.revision == expected_revision,
            TemplateV2PptxImport.state.in_(
                ("queued", "processing", "finalizing", "failed", "review_required")
            ),
        )
        .values(
            state="cancelled",
            revision=TemplateV2PptxImport.revision + 1,
            attempt_token=None,
            lease_expires_at=None,
            source_retention_expires_at=retention_expires_at,
            source_cleanup_token=None,
            source_cleanup_lease_expires_at=None,
            source_cleanup_attempted_at=None,
            source_deleted_at=None,
            cancelled_at=cancelled_at,
            manifest=manifest,
            updated_at=cancelled_at,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        await session.rollback()
        return False
    task_result = await session.execute(
        update(AsyncPresentationGenerationTaskModel)
        .where(AsyncPresentationGenerationTaskModel.id == task_id)
        .values(
            status="cancelled",
            message="Template V2 PPTX import cancelled",
            error=None,
            data=_task_data(
                import_id,
                state="cancelled",
                attempt_number=import_job.attempt_number,
            ),
            updated_at=_task_timestamp(cancelled_at),
        )
        .execution_options(synchronize_session=False)
    )
    if task_result.rowcount != 1:
        await session.rollback()
        return False
    await session.commit()
    return True


async def release_template_v2_pptx_import(
    import_id: uuid.UUID,
    task_id: str,
    attempt_token: str,
) -> bool:
    """Return an owned attempt to the durable queue during graceful shutdown."""

    released_at = _now()
    async with async_session_maker() as session:
        result = await session.execute(
            update(TemplateV2PptxImport)
            .where(
                TemplateV2PptxImport.id == import_id,
                TemplateV2PptxImport.task_id == task_id,
                TemplateV2PptxImport.state.in_(("processing", "finalizing")),
                TemplateV2PptxImport.attempt_token == attempt_token,
            )
            .values(
                state="queued",
                attempt_token=None,
                lease_expires_at=None,
                updated_at=released_at,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            await session.rollback()
            return False
        import_job = await session.get(TemplateV2PptxImport, import_id)
        task = await session.get(AsyncPresentationGenerationTaskModel, task_id)
        if import_job is None or task is None:
            await session.rollback()
            return False
        task.status = "pending"
        task.message = "Queued after worker shutdown"
        task.error = None
        task.data = _task_data(
            import_id,
            state="queued",
            attempt_number=import_job.attempt_number,
        )
        task.updated_at = _task_timestamp(released_at)
        session.add(task)
        await session.commit()
        notify_template_v2_pptx_dispatcher()
        return True


async def recover_stalled_template_v2_pptx_imports(
    session: AsyncSession,
    *,
    now: datetime | None = None,
) -> int:
    """CAS stale processing rows back to queued without trusting local memory."""

    recovered_at = now or _now()
    rows = (
        await session.execute(
            select(
                TemplateV2PptxImport.id,
                TemplateV2PptxImport.task_id,
                TemplateV2PptxImport.attempt_token,
                TemplateV2PptxImport.attempt_number,
            ).where(
                TemplateV2PptxImport.state.in_(("processing", "finalizing")),
                or_(
                    TemplateV2PptxImport.lease_expires_at.is_(None),
                    TemplateV2PptxImport.lease_expires_at <= recovered_at,
                ),
            )
        )
    ).all()
    recovered = 0
    for import_id, task_id, attempt_token, attempt_number in rows:
        predicates = [
            TemplateV2PptxImport.id == import_id,
            TemplateV2PptxImport.task_id == task_id,
            TemplateV2PptxImport.state.in_(("processing", "finalizing")),
        ]
        if attempt_token is None:
            predicates.append(TemplateV2PptxImport.attempt_token.is_(None))
        else:
            predicates.append(TemplateV2PptxImport.attempt_token == attempt_token)
        result = await session.execute(
            update(TemplateV2PptxImport)
            .where(
                *predicates,
                or_(
                    TemplateV2PptxImport.lease_expires_at.is_(None),
                    TemplateV2PptxImport.lease_expires_at <= recovered_at,
                ),
            )
            .values(
                state="queued",
                attempt_token=None,
                lease_expires_at=None,
                updated_at=recovered_at,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            continue
        await session.execute(
            update(AsyncPresentationGenerationTaskModel)
            .where(AsyncPresentationGenerationTaskModel.id == task_id)
            .values(
                status="pending",
                message="Recovered stalled Template V2 PPTX import",
                error=None,
                data=_task_data(
                    import_id,
                    state="queued",
                    attempt_number=attempt_number,
                ),
                updated_at=_task_timestamp(recovered_at),
            )
            .execution_options(synchronize_session=False)
        )
        recovered += 1
    await session.commit()
    return recovered


async def run_template_v2_pptx_import(import_id: uuid.UUID, task_id: str) -> None:
    """Claim, heartbeat, and complete a durable import attempt."""

    async with async_session_maker() as session:
        attempt_token = await claim_template_v2_pptx_import(
            session,
            import_id,
            task_id,
        )
    if attempt_token is None:
        return
    stop_heartbeat = asyncio.Event()
    ownership_lost = asyncio.Event()
    heartbeat_task = asyncio.create_task(
        _heartbeat_loop(
            import_id,
            task_id,
            attempt_token,
            stop_heartbeat,
            ownership_lost,
        )
    )
    try:
        async with async_session_maker() as session:
            import_job = await session.get(TemplateV2PptxImport, import_id)
            if (
                import_job is None
                or import_job.task_id != task_id
                or import_job.attempt_token != attempt_token
            ):
                raise AttemptOwnershipLost()
            storage_key = import_job.source_storage_key
            source_sha256 = import_job.source_sha256
            source_filename = import_job.source_filename
            source_media_type = import_job.source_media_type
            source_size_bytes = import_job.source_size_bytes
        # The runtime analyzer is async, so the branch lives here rather than inside
        # the threaded parser call.
        if get_structured_template_policy().pptx_analyzer == "runtime":
            analysis_source = _analyze_import_source_via_runtime(
                storage_key,
                source_sha256,
                import_id=import_id,
                source_filename=source_filename,
                source_media_type=source_media_type,
                source_size_bytes=source_size_bytes,
            )
        else:
            analysis_source = asyncio.to_thread(
                _analyze_import_source,
                storage_key,
                source_sha256,
                import_id=import_id,
                source_filename=source_filename,
                source_media_type=source_media_type,
                source_size_bytes=source_size_bytes,
            )
        analysis_result, repeat_suggestions, source_inventory = await analysis_source
        if ownership_lost.is_set():
            raise AttemptOwnershipLost()
        if not await _persist_analysis(
            import_id,
            task_id,
            attempt_token,
            analysis_result,
            repeat_suggestions,
            source_inventory,
        ):
            raise AttemptOwnershipLost()
    except asyncio.CancelledError:
        await release_template_v2_pptx_import(
            import_id,
            task_id,
            attempt_token,
        )
        raise
    except AttemptOwnershipLost:
        logger.warning(
            "Template V2 PPTX import attempt lost ownership for %s",
            import_id,
        )
    except Exception as error:
        recorded = await fail_template_v2_pptx_import(
            import_id,
            task_id,
            attempt_token,
            error,
        )
        if recorded:
            logger.exception(
                "Template V2 PPTX import failed: %s",
                _failure_code(error),
            )
    finally:
        stop_heartbeat.set()
        await heartbeat_task


_dispatcher_task: asyncio.Task | None = None
_dispatcher_stop: asyncio.Event | None = None
_dispatcher_wake: asyncio.Event | None = None
_inflight_tasks: set[asyncio.Task] = set()


def notify_template_v2_pptx_dispatcher() -> None:
    if _dispatcher_wake is not None:
        _dispatcher_wake.set()


def _track_import_task(task: asyncio.Task) -> None:
    _inflight_tasks.add(task)
    task.add_done_callback(_inflight_tasks.discard)


async def dispatch_template_v2_pptx_imports_once() -> int:
    async with async_session_maker() as session:
        recovered = await recover_stalled_template_v2_pptx_imports(session)
        queued = (
            await session.execute(
                select(
                    TemplateV2PptxImport.id,
                    TemplateV2PptxImport.task_id,
                )
                .where(TemplateV2PptxImport.state == "queued")
                .order_by(TemplateV2PptxImport.created_at)
                .limit(IMPORT_DISPATCH_BATCH_SIZE)
            )
        ).all()
    for import_id, task_id in queued:
        _track_import_task(
            asyncio.create_task(run_template_v2_pptx_import(import_id, task_id))
        )
    log_pptx_queue_observation(
        operation="recover",
        outcome="completed",
        count=recovered,
    )
    log_pptx_queue_observation(
        operation="dispatch",
        outcome="completed",
        count=len(queued),
    )
    if recovered:
        logger.warning("Recovered %s stalled Template V2 PPTX imports", recovered)
    return len(queued)


async def _dispatcher_loop() -> None:
    assert _dispatcher_stop is not None
    assert _dispatcher_wake is not None
    while not _dispatcher_stop.is_set():
        _dispatcher_wake.clear()
        try:
            await maybe_cleanup_expired_private_sources()
            await dispatch_template_v2_pptx_imports_once()
        except Exception:
            logger.exception("Template V2 PPTX durable dispatcher iteration failed")
        try:
            await asyncio.wait_for(
                _dispatcher_wake.wait(),
                timeout=IMPORT_DISPATCH_INTERVAL_SECONDS,
            )
        except TimeoutError:
            pass


async def start_template_v2_pptx_dispatcher() -> None:
    global _dispatcher_stop, _dispatcher_task, _dispatcher_wake
    policy = get_structured_template_policy()
    if not policy.creation_enabled or not policy.allowed_template_ids:
        logger.info(
            "Template V2 PPTX dispatcher remains disabled by rollout policy"
        )
        return
    if _dispatcher_task is not None and not _dispatcher_task.done():
        return
    _dispatcher_stop = asyncio.Event()
    _dispatcher_wake = asyncio.Event()
    await maybe_cleanup_expired_private_sources()
    await dispatch_template_v2_pptx_imports_once()
    _dispatcher_task = asyncio.create_task(_dispatcher_loop())


async def stop_template_v2_pptx_dispatcher() -> None:
    global _dispatcher_stop, _dispatcher_task, _dispatcher_wake
    if _dispatcher_stop is not None:
        _dispatcher_stop.set()
    if _dispatcher_wake is not None:
        _dispatcher_wake.set()
    if _dispatcher_task is not None:
        await _dispatcher_task
    active = list(_inflight_tasks)
    for task in active:
        task.cancel()
    if active:
        await asyncio.gather(*active, return_exceptions=True)
    _inflight_tasks.clear()
    _dispatcher_task = None
    _dispatcher_stop = None
    _dispatcher_wake = None
