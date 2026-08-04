"""Persistence and read-back checks for immutable SlideSpec artifact bundles."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any
import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models.slidespec_v1 import (
    AcceptanceDecision,
    RenderRequest,
    RepairPlan,
    SlideSpec,
)
from models.sql.slide import SlideModel
from models.sql.slidespec_artifact_bundle import (
    SLIDESPEC_BUNDLE_ROUTES,
    SLIDESPEC_BUNDLE_STATUSES,
    SlideSpecArtifactBundle,
)
from models.sql.slidespec_publication_attempt import (
    PUBLICATION_ATTEMPT_STATES,
    SlideSpecPublicationAttempt,
)
from utils.slidespec_core import digest, render_slidespec, validate_slidespec


ACTIVE_POINTER_KEY = "slidespec_active"


class SlideSpecArtifactError(RuntimeError):
    """Fail-closed artifact/pointer error with a stable reason code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _json(value: Any) -> Any:
    return value.model_dump(mode="json", by_alias=True) if value is not None else None


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def build_slidespec_artifact_bundle(
    *,
    presentation_id: uuid.UUID,
    slide: SlideModel,
    route: str,
    status: str,
    render_request: RenderRequest,
    repair_plan: RepairPlan | None = None,
    acceptance: AcceptanceDecision | None = None,
    evidence: Mapping[str, Any] | None = None,
    publication_receipt: Mapping[str, Any] | None = None,
    image_bytes: bytes | None = None,
    bundle_id: uuid.UUID | None = None,
) -> SlideSpecArtifactBundle:
    """Build a self-verifying row without touching the database."""

    if route not in SLIDESPEC_BUNDLE_ROUTES:
        raise SlideSpecArtifactError("artifact.route.invalid")
    if status not in SLIDESPEC_BUNDLE_STATUSES:
        raise SlideSpecArtifactError("artifact.status.invalid")
    if slide.presentation != presentation_id:
        raise SlideSpecArtifactError("artifact.presentation.mismatch")
    spec = render_request.spec
    validate_slidespec(spec)
    if str(slide.id) != spec.slideId:
        raise SlideSpecArtifactError("artifact.slide_id.mismatch")
    if repair_plan is not None:
        if repair_plan.slide_id != spec.slideId:
            raise SlideSpecArtifactError("artifact.plan.slide_id_mismatch")
        if repair_plan.base_spec_revision + 1 != spec.specRevision:
            raise SlideSpecArtifactError("artifact.plan.revision_mismatch")
    if acceptance is not None:
        if acceptance.candidate_spec_hash != spec.digests.spec:
            raise SlideSpecArtifactError("artifact.acceptance.hash_mismatch")
        if status == "published" and acceptance.outcome != "accepted":
            raise SlideSpecArtifactError("artifact.acceptance.not_accepted")
    if status == "published" and acceptance is None:
        raise SlideSpecArtifactError("artifact.acceptance.required")
    if status == "published" and not publication_receipt:
        raise SlideSpecArtifactError("artifact.publication_receipt.required")

    rendered = render_slidespec(render_request)
    manifest = {
        "schema": "urn:ppt-agent:slidespec-artifact-bundle:1.0",
        "renderer": _json(render_request.renderer),
        "fonts": _json(render_request.fonts),
        "assets": _json(render_request.assets),
        "source": _json(render_request.source),
        "artifact_hashes": rendered.artifactHashes,
    }
    return SlideSpecArtifactBundle(
        id=bundle_id or uuid.uuid4(),
        presentation_id=presentation_id,
        slide_id=slide.id,
        slide_index=slide.index,
        spec_revision=spec.specRevision,
        route=route,
        status=status,
        base_spec_hash=repair_plan.base_spec_hash if repair_plan is not None else None,
        candidate_spec_hash=spec.digests.spec,
        render_input_hash=rendered.renderInputHash,
        html_hash=rendered.htmlHash,
        render_hash=rendered.semanticDomHash,
        image_hash=_sha256_bytes(image_bytes) if image_bytes is not None else None,
        spec=_json(spec),
        repair_plan=_json(repair_plan),
        acceptance=_json(acceptance),
        evidence=dict(evidence or {}),
        manifest=manifest,
        publication_receipt=dict(publication_receipt) if publication_receipt else None,
        html=rendered.html,
    )


def active_pointer(bundle: SlideSpecArtifactBundle) -> dict[str, Any]:
    if bundle.status not in {"imported", "published"}:
        raise SlideSpecArtifactError("artifact.pointer.status_invalid")
    return {
        "bundle_id": str(bundle.id),
        "spec_hash": bundle.candidate_spec_hash,
        "spec_revision": bundle.spec_revision,
        "html_hash": bundle.html_hash,
        "render_hash": bundle.render_hash,
    }


async def load_active_slidespec_bundle(
    session: AsyncSession,
    slide: SlideModel,
) -> SlideSpecArtifactBundle | None:
    """Load only a fully matching active pointer; malformed pointers fail closed."""

    properties = slide.properties if isinstance(slide.properties, Mapping) else {}
    pointer = properties.get(ACTIVE_POINTER_KEY)
    if pointer is None:
        return None
    if not isinstance(pointer, Mapping):
        raise SlideSpecArtifactError("artifact.pointer.invalid")
    try:
        bundle_id = uuid.UUID(str(pointer["bundle_id"]))
    except (KeyError, TypeError, ValueError) as error:
        raise SlideSpecArtifactError("artifact.pointer.invalid") from error
    result = await session.execute(
        select(SlideSpecArtifactBundle).where(
            SlideSpecArtifactBundle.id == bundle_id,
            SlideSpecArtifactBundle.slide_id == slide.id,
            SlideSpecArtifactBundle.presentation_id == slide.presentation,
        )
    )
    bundle = result.scalar_one_or_none()
    if bundle is None:
        raise SlideSpecArtifactError("artifact.pointer.missing")
    expected = active_pointer(bundle)
    if any(pointer.get(key) != value for key, value in expected.items()):
        raise SlideSpecArtifactError("artifact.pointer.hash_mismatch")
    spec = SlideSpec.model_validate(bundle.spec)
    validate_slidespec(spec)
    if spec.digests.spec != bundle.candidate_spec_hash:
        raise SlideSpecArtifactError("artifact.spec.hash_mismatch")
    if digest(bundle.html) != bundle.html_hash:
        raise SlideSpecArtifactError("artifact.html.hash_mismatch")
    if slide.html_content != bundle.html:
        raise SlideSpecArtifactError("artifact.live_html.mismatch")
    return bundle


async def publish_slidespec_bundle_cas(
    session: AsyncSession,
    *,
    slide: SlideModel,
    bundle: SlideSpecArtifactBundle,
) -> None:
    """Insert evidence and activate it with one CAS, flush, and strict read-back.

    The caller owns the transaction. Any mismatch rolls it back so the candidate
    row and live slide remain invisible together.
    """

    if bundle.slide_id != slide.id or bundle.presentation_id != slide.presentation:
        raise SlideSpecArtifactError("artifact.publish.target_mismatch")
    if bundle.status not in {"imported", "published"}:
        raise SlideSpecArtifactError("artifact.publish.status_invalid")
    expected_html = slide.html_content
    expected_content = slide.content
    expected_properties = slide.properties
    next_properties = {
        **(expected_properties if isinstance(expected_properties, dict) else {}),
        ACTIVE_POINTER_KEY: active_pointer(bundle),
    }
    session.add(bundle)
    result = await session.execute(
        update(SlideModel)
        .where(
            SlideModel.id == slide.id,
            SlideModel.html_content == expected_html,
            SlideModel.content == expected_content,
            SlideModel.properties == expected_properties,
        )
        .values(html_content=bundle.html, properties=next_properties)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        await session.rollback()
        raise SlideSpecArtifactError("artifact.publish.cas_conflict")
    await session.flush()
    readback = await session.execute(
        select(SlideModel.html_content, SlideModel.properties).where(
            SlideModel.id == slide.id
        )
    )
    row = readback.one_or_none()
    if row is None or row.html_content != bundle.html or row.properties != next_properties:
        await session.rollback()
        raise SlideSpecArtifactError("artifact.publish.readback_mismatch")


async def record_publication_attempt(
    session: AsyncSession,
    *,
    attempt_id: uuid.UUID,
    bundle: SlideSpecArtifactBundle,
    state: str,
    reason_code: str | None = None,
    evidence: Mapping[str, Any] | None = None,
) -> SlideSpecPublicationAttempt:
    """Append and flush one immutable state transition owned by the caller."""

    if state not in PUBLICATION_ATTEMPT_STATES:
        raise SlideSpecArtifactError("artifact.publish.attempt_state_invalid")
    event = SlideSpecPublicationAttempt(
        attempt_id=attempt_id,
        artifact_bundle_id=bundle.id,
        presentation_id=bundle.presentation_id,
        slide_id=bundle.slide_id,
        state=state,
        reason_code=reason_code,
        evidence=dict(evidence or {}),
    )
    session.add(event)
    await session.flush()
    return event


async def rollback_active_slide_cas(
    session: AsyncSession,
    *,
    slide_id: uuid.UUID,
    expected_html: str,
    expected_properties: Any,
    restore_html: str,
    restore_properties: Any,
) -> bool:
    """Restore only one slide when it still points at the failed publication."""

    result = await session.execute(
        update(SlideModel)
        .where(
            SlideModel.id == slide_id,
            SlideModel.html_content == expected_html,
            SlideModel.properties == expected_properties,
        )
        .values(html_content=restore_html, properties=restore_properties)
        .execution_options(synchronize_session=False)
    )
    await session.flush()
    return result.rowcount == 1
