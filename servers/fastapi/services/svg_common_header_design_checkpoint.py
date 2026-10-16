"""Fenced document and candidate checkpoints without changing admitted inputs.

The document state and candidate page states occupy separate branches so two
parallel candidates cannot overwrite each other. Every write holds the existing
job/operation fence until commit; candidate execution never owns a public page.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from sqlalchemy import update

from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.workspace import WorkspaceOperation
from utils.datetime_utils import get_current_utc_datetime
from utils.hashing import canonical_json_digest

VERSION = "svg-common-header-design-checkpoint/v2"


def _identity(owner: Any) -> dict:
    return {
        "owner_id": str(owner.id),
        "owner_type": "operation" if isinstance(owner, WorkspaceOperation) else "job",
        "request_digest": getattr(owner, "request_sha256", None) or getattr(owner, "request_hash", None),
    }


def _envelope(owner: Any, value: Any) -> dict:
    identity = _identity(owner)
    if not identity["request_digest"]:
        raise ValueError("svg_header_design_checkpoint_owner_invalid")
    if value is None:
        return {"version": VERSION, "identity": identity, "document": {}, "candidate_pages": {}}
    if (not isinstance(value, dict) or value.get("version") != VERSION
            or value.get("identity") != identity or not isinstance(value.get("document"), dict)
            or not isinstance(value.get("candidate_pages"), dict)):
        raise ValueError("svg_header_design_checkpoint_conflict")
    return deepcopy(value)


async def _owned(session, owner, owned_job):
    if not isinstance(owner, WorkspaceOperation):
        if owned_job is None:
            from services.authored_checkpoint_fence import owned_job
        current = await owned_job(session, owner.id, owner.attempt_token,
            fencing_token=getattr(owner, "_workspace_fencing_token", None))
    else:
        now = get_current_utc_datetime()
        result = await session.execute(update(WorkspaceOperation).where(
            WorkspaceOperation.id == owner.id,
            WorkspaceOperation.owner_scope == owner.owner_scope,
            WorkspaceOperation.request_hash == owner.request_hash,
            WorkspaceOperation.state == "running",
            WorkspaceOperation.attempt_token == owner.attempt_token,
            WorkspaceOperation.fencing_token == owner.fencing_token,
            WorkspaceOperation.cancel_requested_at.is_(None),
        ).values(heartbeat_at=now, updated_at=now))
        if result.rowcount != 1:
            raise ValueError("svg_header_design_operation_fence_lost")
        current = await session.get(WorkspaceOperation, owner.id)
    # The no-op fence can have waited behind another candidate's commit.
    await session.refresh(current)
    if _identity(current) != _identity(owner):
        raise ValueError("svg_header_design_checkpoint_lineage_conflict")
    return current


def _factory(session_maker):
    if session_maker is not None:
        return session_maker
    from services.database import async_session_maker
    return async_session_maker


def _preserve_terminal(previous: dict, replacement: dict) -> dict:
    """Never erase observed provider attempts when a local stage is replayed."""
    if previous.get("state") == "unknown" and replacement != previous:
        raise ValueError("svg_header_design_outcome_unresolved")
    for key in ("provider_attempts", "provider_responses", "terminal_responses"):
        if key in previous and key not in replacement:
            replacement[key] = deepcopy(previous[key])
    return replacement


async def read_update_header_design(owner, *, payload=None, session_maker=None, owned_job=None):
    async with _factory(session_maker)() as session:
        current = await _owned(session, owner, owned_job)
        envelope = _envelope(current, current.svg_header_design_checkpoint)
        if payload is not None:
            if not isinstance(payload, dict):
                raise ValueError("svg_header_design_checkpoint_payload_invalid")
            envelope["document"] = _preserve_terminal(envelope["document"], deepcopy(payload))
            current.svg_header_design_checkpoint = envelope
            session.add(current)
        result = deepcopy(envelope["document"])
        await session.commit()
        owner.svg_header_design_checkpoint = deepcopy(envelope)
        return result


def _candidate_key(index: int, identity: dict) -> str:
    required = ("candidate_revision", "candidate_id", "page_id")
    if type(index) is not int or index < 0 or not isinstance(identity, dict):
        raise ValueError("svg_header_candidate_identity_invalid")
    if any(not isinstance(identity.get(key), str) or not identity[key] for key in required):
        raise ValueError("svg_header_candidate_identity_invalid")
    return canonical_json_digest({"page_index": index, **identity})


async def candidate_page_checkpoint(owner, index, *, identity, payload=None,
                                    session_maker=None, owned_job=None):
    key = _candidate_key(index, identity)
    async with _factory(session_maker)() as session:
        current = await _owned(session, owner, owned_job)
        envelope = _envelope(current, current.svg_header_design_checkpoint)
        previous = envelope["candidate_pages"].get(key, {})
        if payload is not None:
            if not isinstance(payload, dict):
                raise ValueError("svg_header_candidate_checkpoint_payload_invalid")
            envelope["candidate_pages"][key] = _preserve_terminal(previous, deepcopy(payload))
            current.svg_header_design_checkpoint = envelope
            session.add(current)
        result = deepcopy(envelope["candidate_pages"].get(key, {}))
        await session.commit()
        owner.svg_header_design_checkpoint = deepcopy(envelope)
        return result


def _style_base(snapshot: dict) -> dict:
    return {key: value for key, value in snapshot.items()
            if key not in {"digest", "common_header", "common_header_selection"}}


async def publish_header_design(job, *, snapshot, outline, session_maker=None, owned_job=None):
    if isinstance(job, WorkspaceOperation):
        raise ValueError("svg_header_design_operation_requires_workspace_publication")
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("common_header"), dict):
        raise ValueError("svg_header_design_publication_invalid")
    if snapshot["common_header"].get("version") != "svg-common-header/v2":
        raise ValueError("svg_header_design_publication_invalid")
    raw_outline = outline.model_dump(mode="json") if hasattr(outline, "model_dump") else deepcopy(outline)
    if not isinstance(raw_outline, dict) or not isinstance(raw_outline.get("slides"), list):
        raise ValueError("svg_header_design_publication_invalid")
    async with _factory(session_maker)() as session:
        current = await _owned(session, job, owned_job)
        envelope = _envelope(current, current.svg_header_design_checkpoint)
        if _style_base(current.svg_style_snapshot or {}) != _style_base(snapshot):
            raise ValueError("svg_header_design_style_base_conflict")
        previous_outline = current.authored_outline or {}
        previous_slides = previous_outline.get("slides", [])
        if previous_outline:
            if len(previous_slides) != len(raw_outline["slides"]):
                raise ValueError("svg_header_design_source_conflict")
            for previous, proposed in zip(previous_slides, raw_outline["slides"], strict=True):
                if any(previous.get(key) != proposed.get(key)
                       for key in ("content", "title", "title_source_digest")):
                    raise ValueError("svg_header_design_source_conflict")
                if previous.get("page_id") is not None and previous["page_id"] != proposed.get("page_id"):
                    raise ValueError("svg_header_design_page_identity_conflict")
        from services.svg_common_header import validate_common_header_binding
        header = snapshot["common_header"]
        selection = snapshot.get("common_header_selection")
        document = envelope["document"]
        plan = snapshot.get("common_header_design_plan", {})
        if (not isinstance(selection, dict) or document.get("status") != "selected"
                or document.get("selection") != selection
                or selection.get("plan_digest") != plan.get("plan_digest")
                or selection.get("selected_candidate_id") != header.get("candidate_id")
                or selection.get("selected_revision") != header.get("revision")
                or document.get("selected_candidate_id") != header.get("candidate_id")
                or document.get("selection_input_digest") != selection.get("selection_input_digest")):
            raise ValueError("svg_header_design_selection_conflict")
        expected_snapshot_digest = canonical_json_digest(
            {key: value for key, value in snapshot.items() if key != "digest"}, allow_nan=True)
        if snapshot.get("digest") != expected_snapshot_digest:
            raise ValueError("svg_header_design_snapshot_digest_invalid")
        page_ids = [slide.get("page_id") for slide in raw_outline["slides"]]
        if any(not isinstance(value, str) or not value for value in page_ids) or len(set(page_ids)) != len(page_ids):
            raise ValueError("svg_header_design_page_identity_conflict")
        representative_id = selection.get("representative_page_id")
        if representative_id not in page_ids:
            raise ValueError("svg_header_design_representative_conflict")
        representative_index = page_ids.index(representative_id)
        candidate_key = _candidate_key(representative_index, {
            "candidate_revision": str(header["revision"]),
            "candidate_id": header["candidate_id"], "page_id": representative_id,
        })
        if not envelope["candidate_pages"].get(candidate_key, {}).get("author_body_svg"):
            raise ValueError("svg_header_design_selected_body_missing")
        for index, slide in enumerate(raw_outline["slides"]):
            binding = slide.get("common_header")
            if (not isinstance(binding, dict) or binding.get("page_index") != index
                    or binding.get("page_id") != slide["page_id"]):
                raise ValueError("svg_header_design_binding_incomplete")
            validate_common_header_binding(header, binding, index=index,
                count=len(raw_outline["slides"]), content=slide["content"], title=slide.get("title"),
                page_id=slide["page_id"])
        current.svg_style_snapshot = deepcopy(snapshot)
        current.authored_outline = raw_outline
        envelope["published_inputs"] = {"snapshot_digest": snapshot.get("digest"),
            "outline_digest": canonical_json_digest(raw_outline)}
        current.svg_header_design_checkpoint = envelope
        session.add(current)
        await session.commit()
        job.svg_style_snapshot = deepcopy(snapshot)
        job.authored_outline = deepcopy(raw_outline)
        job.svg_header_design_checkpoint = deepcopy(envelope)
    return snapshot
