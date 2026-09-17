"""Optional ordinary-authored image stage; strict source preservation bypasses it."""
from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping, Sequence

from sqlalchemy import update
from sqlmodel import select

from models.sql.image_asset import ImageAsset
from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.workspace import WorkspaceOperation
from services.source_images.models import SourceImageCandidate
from services.source_images.placement import build_source_image_placement_plan, source_images_for_slide
from utils.authored_image_slots import source_slot_id_for_asset
from utils.datetime_utils import get_current_utc_datetime

_CACHE_KEY = "source_image_placement_plan"


def _digest(plan: Mapping) -> str:
    return hashlib.sha256(json.dumps(dict(plan), ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


async def _assert_lease(session, job_id, attempt_number, attempt_token, *, write=False):
    if job_id is None:
        return
    if not attempt_token or attempt_number < 1:
        raise RuntimeError("source_image_stage_attempt_required")
    conditions = (PresentationGenerationJob.id == job_id,
                  PresentationGenerationJob.state == "running",
                  PresentationGenerationJob.attempt_token == attempt_token,
                  PresentationGenerationJob.attempt_number == attempt_number)
    # Pending analysis cache writes must not flush before the final fence.
    with session.no_autoflush:
        if write:
            result = await session.execute(update(PresentationGenerationJob).where(*conditions)
                                           .values(updated_at=get_current_utc_datetime()))
            if result.rowcount != 1:
                raise RuntimeError("source_image_stage_stale_attempt")
        job = await session.scalar(select(PresentationGenerationJob.id).where(*conditions))
        if job is None:
            raise RuntimeError("source_image_stage_stale_attempt")
        operation_id = await session.scalar(select(PresentationGenerationJob.workspace_operation_id).where(*conditions))
        if operation_id is not None:
            operation_conditions = (WorkspaceOperation.id == operation_id, WorkspaceOperation.state == "running",
                                    WorkspaceOperation.attempt_token == attempt_token,
                                    WorkspaceOperation.attempt_number == attempt_number)
            if write:
                result = await session.execute(update(WorkspaceOperation).where(*operation_conditions)
                                               .values(version=WorkspaceOperation.version))
                if result.rowcount != 1:
                    raise RuntimeError("source_image_stage_workspace_fence_lost")
            elif await session.scalar(select(WorkspaceOperation.id).where(*operation_conditions)) is None:
                raise RuntimeError("source_image_stage_workspace_fence_lost")


async def prepare_source_image_stage(
    candidates: Sequence[SourceImageCandidate], *, session, contents: Sequence[str],
    instructions: str = "", template_context: Mapping | None = None,
    job_id=None, attempt_number: int = 0, attempt_token: str | None = None,
    repeated_asset_ids: Sequence[str] = (),
) -> tuple[dict, list[dict[str, SourceImageCandidate]], list[list[dict]]]:
    """Analyze, checkpoint, and adapt assignments for authoring and final verification.

    Caller supplies already admitted deck-owned candidates and a generation session.
    This stage commits its cache transaction. A stale lease rolls back all pending
    cache changes; provider content never appears in errors.
    """
    if not candidates:
        return ({"decisions": []}, [{} for _ in contents], [[] for _ in contents])
    from llmai import get_client
    from services.provider_usage_instrumentation import provider_usage_scope
    from services.source_images.analysis import analyze_source_images
    from utils.llm_config import enforce_llm_egress_policy, get_llm_config
    from utils.llm_provider import get_model

    async def assert_current():
        await _assert_lease(session, job_id, attempt_number, attempt_token)

    try:
        await assert_current()
        cache_id = uuid.UUID(min(c.asset_id for c in candidates))
        asset = await session.get(ImageAsset, cache_id)
        if asset is None:
            raise RuntimeError("source_image_stage_asset_missing")
        cached = (asset.extras or {}).get(_CACHE_KEY)
        previous = None
        if isinstance(cached, dict) and isinstance(cached.get("plan"), dict):
            if cached.get("output_digest") == _digest(cached["plan"]):
                previous = cached["plan"]
        enforce_llm_egress_policy()
        with provider_usage_scope(job_id=job_id, operation="source-image-analysis", stage="outline",
                                  attempt_number=attempt_number):
            analyzed = await analyze_source_images(
                session, candidates, assert_current=assert_current, attempt_number=attempt_number,
            )
        plan = await build_source_image_placement_plan(
            analyzed, contents=contents, client=get_client(config=get_llm_config()), model=get_model(),
            instructions=instructions, template_context=template_context, previous_plan=previous,
            repeated_asset_ids=repeated_asset_ids, job_id=job_id, attempt_number=attempt_number,
        )
        await _assert_lease(session, job_id, attempt_number, attempt_token, write=True)
        await session.flush()
        # Refresh after analysis writes, then merge under lock to preserve unrelated extras.
        asset = await session.scalar(select(ImageAsset).where(ImageAsset.id == cache_id)
                                     .with_for_update().execution_options(populate_existing=True))
        if asset is None:
            raise RuntimeError("source_image_stage_asset_missing")
        extras = dict(asset.extras or {})
        complete = all(d.get("status") in {"assigned", "no_relevant_slide"} for d in plan["decisions"])
        if complete:
            extras[_CACHE_KEY] = {"plan": plan, "output_digest": _digest(plan)}
        else:
            extras.pop(_CACHE_KEY, None)
        # Failed outcomes remain observable but never qualify as reusable plans.
        extras["source_image_placement_result"] = plan
        asset.extras = extras
        session.add(asset)
        await session.commit()
    except BaseException:
        await session.rollback()
        raise

    decisions = {d["asset_id"]: d for d in plan["decisions"]}
    per_slide_slots, per_slide_assignments = [], []
    for index in range(len(contents)):
        selected = source_images_for_slide(plan, analyzed, index)
        per_slide_slots.append({source_slot_id_for_asset(c.asset_id): c for c in selected})
        per_slide_assignments.append([{
            "asset_id": c.asset_id, "slot_id": source_slot_id_for_asset(c.asset_id),
            "group_id": decisions[c.asset_id]["group_id"], "purpose": decisions[c.asset_id]["purpose"],
            "allow_overlap": decisions[c.asset_id]["allow_overlap"],
            "critical_regions": list(c.protected_regions),
        } for c in selected])
    return plan, per_slide_slots, per_slide_assignments
