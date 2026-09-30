"""Input selection and pipeline-state rebuild for the authored fit replay.

M3 sections 5.1 and 6 of
``docs/authored-fit-magnitude-measurement-design-20260904.md``.

Everything the gate needs is rebuilt with the pipeline's own functions from
durable rows only — never re-derived by hand:

* ``GeneratePresentationRequest`` from ``presentation_generation_jobs.request_payload``
  through the worker's own ``_request_from_durable_payload``;
* the composition profile through ``resolve_composition_profile``;
* the outline/roles/chapter plan from the ``deck_plan`` row of
  ``authored_deck_plan_checkpoints`` (decoded by the generation service's own
  ``_decode_deck_plans``), then ``plan_deck_roles`` and
  ``build_authored_capacity_plan`` exactly as ``prepare_authored_deck`` calls
  them;
* the reference mode through ``normalize_reference_mode`` over the request's
  authored style.

Anything that cannot be rebuilt is recorded in ``JobContext.deviations`` and
reported; no value is ever invented.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

#: ``src="/app_data/..."`` or an absolute http(s) image reference. Stored HTML
#: must be self-contained (icons inlined as ``data:`` URIs) or the replay would
#: measure a broken page (design 5.1).
_EXTERNAL_IMAGE_RE = re.compile(
    r"""(?:src|href)\s*=\s*(?P<quote>["'])(?P<ref>(?:/app_data|https?://)[^"']*)(?P=quote)""",
    re.IGNORECASE,
)
_ARCHETYPE_RE = re.compile(r"""class\s*=\s*["']ig\s+ig-([a-z0-9_]+)""", re.IGNORECASE)

CHECKPOINT_ILLUSTRATED = "checkpoint_illustrated"
CHECKPOINT_AUTHOR = "checkpoint_author"
PUBLISHED_FINAL = "published_final"


def external_image_refs(html: str) -> tuple[str, ...]:
    """Return every non-self-contained image reference in ``html``."""

    return tuple(
        match.group("ref")
        for match in _EXTERNAL_IMAGE_RE.finditer(html or "")
        if _is_image_reference(match)
    )


def _is_image_reference(match: re.Match[str]) -> bool:
    # ``href`` on a stylesheet/link is not an image; only ``src`` and image
    # ``href`` (SVG <image>) can pull pixels into the render.
    return match.group(0).lower().lstrip().startswith("src")


def archetype_of(html: str) -> str | None:
    """The infographic archetype of a figure slide, from its library root."""

    match = _ARCHETYPE_RE.search(html or "")
    return match.group(1) if match else None


def is_current_code(htmls: Sequence[str]) -> bool:
    """A job is current code when any slide carries the library marker (6.4)."""

    from utils.authored_infographic.library import LIBRARY_MARKER

    return any(LIBRARY_MARKER in (html or "") for html in htmls)


@dataclass
class JobContext:
    """One job's rebuilt gate inputs."""

    job_id: uuid.UUID
    presentation_id: uuid.UUID
    state: str
    created_at: datetime | None
    profile: Any
    capacity_plan: Any
    reference_mode: str
    roles: tuple[str, ...]
    strict_structural_off_canvas: bool
    last_error_code: str | None = None
    code_version: str = "legacy"
    deviations: list[str] = field(default_factory=list)

    @property
    def short(self) -> str:
        return self.job_id.hex[:8]


@dataclass(frozen=True)
class SlideInput:
    """One HTML document to replay, with the job context it belongs to."""

    job: JobContext
    slide_index: int
    role: str
    source: str
    html: str
    archetype: str | None


class ReplayInputError(RuntimeError):
    """A job cannot be replayed from durable rows alone."""


async def select_jobs(
    session: Any,
    *,
    job_ids: Sequence[str],
    since: datetime | None,
) -> list[Any]:
    from sqlmodel import select

    from models.sql.presentation_generation_job import PresentationGenerationJob

    query = select(PresentationGenerationJob)
    if job_ids:
        wanted = [uuid.UUID(value) for value in job_ids]
        query = query.where(PresentationGenerationJob.id.in_(wanted))
    elif since is not None:
        query = query.where(PresentationGenerationJob.created_at >= since)
    else:
        raise ReplayInputError("select jobs with --job-id or --since")
    query = query.order_by(PresentationGenerationJob.created_at)
    return list((await session.execute(query)).scalars().all())


async def _deck_plan_payload(session: Any, job_id: uuid.UUID, stage: str) -> dict | None:
    from sqlmodel import select

    from models.sql.public_admission import AuthoredDeckPlanCheckpoint

    row = (
        await session.execute(
            select(AuthoredDeckPlanCheckpoint).where(
                AuthoredDeckPlanCheckpoint.generation_job_id == job_id,
                AuthoredDeckPlanCheckpoint.stage == stage,
            )
        )
    ).scalars().first()
    return dict(row.payload) if row is not None else None


def _error_code(last_error: Any) -> str | None:
    """The job's terminal error code, when the durable row carries one."""

    if isinstance(last_error, str):
        try:
            last_error = json.loads(last_error)
        except ValueError:
            return None
    if isinstance(last_error, dict):
        code = last_error.get("code")
        return str(code) if code else None
    return None


def _source_inputs_present(request: Any) -> bool:
    return bool(
        getattr(request, "files", None)
        or getattr(request, "reference_snapshot_ids", None)
        or getattr(request, "source_mode", None)
    )


async def build_job_context(session: Any, job: Any) -> JobContext:
    """Rebuild the gate inputs the pipeline used for one job."""

    from services.presentation_generation_staging import (
        _request_from_durable_payload,
    )
    from services.authored_composition_policy import (
        build_authored_capacity_plan,
        resolve_composition_profile,
    )
    from services.authored_presentation_generation import _decode_deck_plans
    from utils.authored_styles import resolve_authored_style
    from services.generation_core.authoring.author_deck import plan_deck_roles
    from services.generation_core.authoring.author_slide import STYLE_REFERENCE, normalize_reference_mode

    deviations: list[str] = []
    request = _request_from_durable_payload(dict(job.request_payload))
    request._request_id = job.request_id
    profile = resolve_composition_profile(request)

    style = getattr(request, "_authored_style_snapshot", None)
    if style is None:
        style = resolve_authored_style(request.authored_style)
        deviations.append("style_resolved_from_request_not_snapshot")
    reference_mode = normalize_reference_mode(
        getattr(style, "reference_mode", STYLE_REFERENCE)
    )

    payload = await _deck_plan_payload(session, job.id, "deck_plan")
    if payload is None:
        raise ReplayInputError(
            f"job {job.id}: deck_plan checkpoint missing; outline and roles "
            "cannot be rebuilt from durable rows"
        )
    deck_plans = _decode_deck_plans(payload)
    roles = tuple(plan_deck_roles(deck_plans.outline, deck_plans.chapter_plan))
    if roles != tuple(deck_plans.roles):
        deviations.append("recomputed_roles_differ_from_deck_plan_checkpoint")
    contents = tuple(slide.content for slide in deck_plans.outline.slides)
    capacity_plan = build_authored_capacity_plan(contents, roles, profile)

    if await _deck_plan_payload(session, job.id, "header") is None:
        # Not a gate input (the header contract only shapes authoring), but a
        # missing row means the deck plan is incomplete; say so.
        deviations.append("header_contract_checkpoint_missing")
    if _source_inputs_present(request):
        # ``with_source_images`` rebinds the envelope once the offer is known
        # and needs the deck's uploaded assets, which the replay does not load.
        deviations.append("source_image_capacity_binding_not_replayed")

    return JobContext(
        job_id=job.id,
        presentation_id=job.presentation_id,
        state=str(job.state),
        created_at=getattr(job, "created_at", None),
        profile=profile,
        capacity_plan=capacity_plan,
        reference_mode=reference_mode,
        roles=roles,
        strict_structural_off_canvas=(profile.profile_id != "ordinary"),
        last_error_code=_error_code(job.last_error),
        deviations=deviations,
    )


async def load_checkpoint_slides(session: Any, context: JobContext) -> list[SlideInput]:
    from sqlmodel import select

    from models.sql.authored_slide_checkpoint import AuthoredSlideCheckpoint

    rows = (
        await session.execute(
            select(AuthoredSlideCheckpoint)
            .where(AuthoredSlideCheckpoint.job_id == context.job_id)
            .order_by(AuthoredSlideCheckpoint.slide_index)
        )
    ).scalars().all()
    slides: list[SlideInput] = []
    for row in rows:
        html = row.illustrated_html or row.author_html
        if not html:
            continue
        source = (
            CHECKPOINT_ILLUSTRATED if row.illustrated_html else CHECKPOINT_AUTHOR
        )
        slides.append(
            SlideInput(
                job=context,
                slide_index=int(row.slide_index),
                role=str(row.role or _role_at(context, int(row.slide_index))),
                source=source,
                html=html,
                archetype=archetype_of(html),
            )
        )
    return slides


async def load_published_slides(session: Any, context: JobContext) -> list[SlideInput]:
    from sqlmodel import select

    from models.sql.slide import SlideModel

    rows = (
        await session.execute(
            select(SlideModel)
            .where(SlideModel.presentation == context.presentation_id)
            .order_by(SlideModel.index)
        )
    ).scalars().all()
    slides: list[SlideInput] = []
    for row in rows:
        if not row.html_content:
            continue
        slides.append(
            SlideInput(
                job=context,
                slide_index=int(row.index),
                role=_role_at(context, int(row.index)),
                source=PUBLISHED_FINAL,
                html=row.html_content,
                archetype=archetype_of(row.html_content),
            )
        )
    return slides


def _role_at(context: JobContext, index: int) -> str:
    if 0 <= index < len(context.roles):
        return context.roles[index]
    return "UNKNOWN"


def prepare_for_render(html: str, *, minimum_font_px: float) -> str:
    """Apply the pipeline's pre-render normalisation to stored HTML.

    ``services/authored_presentation_pipeline.py`` clamps the frozen font floor
    and canonicalises qids/semantic roles immediately before the first
    Chromium render, so a checkpoint written at the illustrate stage is not
    yet the document the gate measured. Both steps are idempotent, so applying
    them to a render-stage or published document is a no-op.
    """

    from services.authored_composition_policy import ensure_authored_semantic_roles
    from utils.authored_dom_quality import ensure_authored_qids
    from utils.authored_font_policy import enforce_authored_font_floor

    floored = enforce_authored_font_floor(html, minimum_px=minimum_font_px)
    return ensure_authored_semantic_roles(ensure_authored_qids(floored))


__all__ = [
    "CHECKPOINT_AUTHOR",
    "CHECKPOINT_ILLUSTRATED",
    "PUBLISHED_FINAL",
    "JobContext",
    "ReplayInputError",
    "SlideInput",
    "archetype_of",
    "build_job_context",
    "external_image_refs",
    "is_current_code",
    "load_checkpoint_slides",
    "load_published_slides",
    "prepare_for_render",
    "select_jobs",
]
