"""Load authored slide HTML from the three richness-measurement sources.

Each loader returns a flat ``list[SourceSlide]``; grouping into decks and
rollups happens downstream in ``authored_deck_richness_report``.

Database access is deliberately lazy (imported inside the loader functions,
never at module import time) so that an ``--html-dir``-only invocation, and
the unit tests, never need ``services.database`` or a live database at all.

Owner/tenant scoping mirrors ``services/database.py``'s ``do_orm_execute``
hook: it only filters when ``utils.owner_context`` has an owner/tenant set,
so leaving ``--owner-id`` unset means "see every owner's rows" exactly as the
task requires. For PostgreSQL, row-level security additionally requires a
session-level ``presenton.tenant_id`` setting or owner-scoped tables read back
empty; ``services/database.py``'s ``after_begin`` hook issues that
``set_config`` automatically from the same context var the moment a session
opens a transaction, so setting ``utils.owner_context`` before running a
query is sufficient -- no raw SQL is needed here. Because that GUC has to
resolve to *something* for RLS to admit rows, the tenant defaults to
``"default"`` for a ``postgresql`` URL unless ``--tenant-id`` overrides it;
SQLite has no RLS and ignores the tenant entirely.

Each loader opens its OWN ``AsyncEngine`` for the URL it was given, rather
than importing ``services.database.sql_engine``: that module builds its
engine once, at first import, from whatever ``DATABASE_URL`` was set at that
moment, and Python caches the module -- a second loader call that mutates
``os.environ["DATABASE_URL"]`` and re-imports the module gets the *same*
already-constructed engine, silently pointed at the first URL. Since the
facade supports a PostgreSQL ``--database-url`` and a different SQLite
``--checkpoint-database-url`` in one process, that singleton would make the
second source silently query the first source's database (observed while
building this: the checkpoint loader returned zero rows with no error,
because the query ran against PostgreSQL, which has no rows for a SQLite
bench job id). ``services.database`` is still imported for its *side
effect* -- it registers the owner/tenant ``Session`` event listeners used
above -- but the engine executing the query is always the one built here
for that call's own URL.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True, slots=True)
class SourceSlide:
    deck_id: str
    deck_label: str
    source: str  # "database" | "checkpoint" | "html_dir"
    slide_index: int
    html: str


def _normalize_id(value: str) -> str:
    return value.strip().lower().replace("-", "")


def matches_id_prefix(full_id: str, wanted: str) -> bool:
    """A >=32-char ``wanted`` must match exactly; a short one is a prefix."""

    full_norm = _normalize_id(full_id)
    wanted_norm = _normalize_id(wanted)
    if not wanted_norm:
        return False
    if len(wanted_norm) >= 32:
        return full_norm == wanted_norm
    return full_norm.startswith(wanted_norm)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def load_html_dir_slides(directory: Path, *, limit: int | None) -> list[SourceSlide]:
    """One deck per directory: every ``*.html`` file, sorted by filename."""

    files = sorted(directory.glob("*.html"))
    if limit is not None:
        files = files[:limit]
    slides: list[SourceSlide] = []
    deck_id = str(directory.resolve())
    for index, path in enumerate(files):
        html = path.read_text(encoding="utf-8")
        slides.append(
            SourceSlide(
                deck_id=deck_id,
                deck_label=directory.name or deck_id,
                source="html_dir",
                slide_index=index,
                html=html,
            )
        )
    return slides


def _resolve_owner_tenant(
    database_url: str, owner_id: uuid.UUID | None, tenant_id: str | None
) -> tuple[uuid.UUID | None, str | None]:
    effective_tenant = tenant_id
    if effective_tenant is None and database_url.startswith("postgresql"):
        effective_tenant = "default"
    return owner_id, effective_tenant


def _connect_args_for(database_url: str) -> dict:
    if "sqlite" in database_url:
        return {"check_same_thread": False, "timeout": 30.0}
    return {}


@asynccontextmanager
async def _open_session(database_url: str):
    """One disposable ``AsyncSession`` bound to a fresh engine for this URL.

    See the module docstring: this deliberately does not reuse
    ``services.database.sql_engine`` (a process-wide singleton bound to
    whichever ``DATABASE_URL`` was set at first import), but still imports
    ``services.database`` so its owner/tenant ``Session`` event listeners are
    registered before the session is used.
    """

    os.environ["DATABASE_URL"] = database_url
    import services.database  # noqa: F401 - side effect: registers Session event listeners
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlmodel.ext.asyncio.session import AsyncSession

    engine = create_async_engine(database_url, connect_args=_connect_args_for(database_url))
    try:
        async with AsyncSession(engine) as session:
            yield session
    finally:
        await engine.dispose()


async def load_database_slides(
    *,
    database_url: str,
    presentation_ids: Sequence[str],
    since: datetime | None,
    owner_id: uuid.UUID | None,
    tenant_id: str | None,
    limit: int | None,
) -> list[SourceSlide]:
    """Read ``slides.html_content`` for presentations matched by id or date."""

    from sqlmodel import select

    from models.sql.presentation import PresentationModel
    from models.sql.slide import SlideModel
    from utils.owner_context import set_current_owner_id, set_current_tenant_id

    # Always set both (even to None), not just when non-None: a prior loader
    # call in the same process (e.g. a PostgreSQL source run before a SQLite
    # checkpoint source) may have left a stale owner/tenant contextvar behind
    # otherwise, since these context vars are process/task-wide, not per-call.
    effective_owner, effective_tenant = _resolve_owner_tenant(
        database_url, owner_id, tenant_id
    )
    set_current_owner_id(effective_owner)
    set_current_tenant_id(effective_tenant)

    since_utc = _as_utc(since)
    slides: list[SourceSlide] = []
    async with _open_session(database_url) as session:
        overview = (
            await session.execute(
                select(
                    PresentationModel.id,
                    PresentationModel.title,
                    PresentationModel.created_at,
                )
            )
        ).all()
        matched: dict[str, tuple[uuid.UUID, str]] = {}
        for presentation_id, title, created_at in overview:
            id_str = str(presentation_id)
            id_hit = any(
                matches_id_prefix(id_str, wanted) for wanted in presentation_ids
            )
            date_hit = since_utc is not None and _as_utc(created_at) is not None and (
                _as_utc(created_at) >= since_utc
            )
            if id_hit or date_hit:
                matched[id_str] = (presentation_id, title or id_str)

        for id_str in sorted(matched):
            presentation_id, title = matched[id_str]
            rows = (
                await session.execute(
                    select(SlideModel.index, SlideModel.html_content)
                    .where(SlideModel.presentation == presentation_id)
                    .order_by(SlideModel.index)
                )
            ).all()
            count = 0
            for index, html_content in rows:
                if not html_content:
                    continue
                if limit is not None and count >= limit:
                    break
                slides.append(
                    SourceSlide(
                        deck_id=id_str,
                        deck_label=title,
                        source="database",
                        slide_index=index,
                        html=html_content,
                    )
                )
                count += 1
    return slides


async def load_checkpoint_slides(
    *,
    database_url: str,
    job_ids: Sequence[str],
    owner_id: uuid.UUID | None,
    tenant_id: str | None,
    limit: int | None,
) -> list[SourceSlide]:
    """Read ``coalesce(illustrated_html, author_html)`` for matched job ids."""

    from sqlmodel import select

    from models.sql.authored_slide_checkpoint import AuthoredSlideCheckpoint
    from utils.owner_context import set_current_owner_id, set_current_tenant_id

    # Always set both (even to None), not just when non-None: a prior loader
    # call in the same process (e.g. a PostgreSQL source run before a SQLite
    # checkpoint source) may have left a stale owner/tenant contextvar behind
    # otherwise, since these context vars are process/task-wide, not per-call.
    effective_owner, effective_tenant = _resolve_owner_tenant(
        database_url, owner_id, tenant_id
    )
    set_current_owner_id(effective_owner)
    set_current_tenant_id(effective_tenant)

    slides: list[SourceSlide] = []
    async with _open_session(database_url) as session:
        job_rows = (
            await session.execute(select(AuthoredSlideCheckpoint.job_id).distinct())
        ).all()
        matched_jobs = sorted(
            {
                str(row[0])
                for row in job_rows
                if any(matches_id_prefix(str(row[0]), wanted) for wanted in job_ids)
            }
        )
        for job_id_str in matched_jobs:
            rows = (
                await session.execute(
                    select(
                        AuthoredSlideCheckpoint.slide_index,
                        AuthoredSlideCheckpoint.illustrated_html,
                        AuthoredSlideCheckpoint.author_html,
                    )
                    .where(AuthoredSlideCheckpoint.job_id == uuid.UUID(job_id_str))
                    .order_by(AuthoredSlideCheckpoint.slide_index)
                )
            ).all()
            count = 0
            for index, illustrated_html, author_html in rows:
                html = illustrated_html or author_html
                if not html:
                    continue
                if limit is not None and count >= limit:
                    break
                slides.append(
                    SourceSlide(
                        deck_id=job_id_str,
                        deck_label=f"checkpoint:{job_id_str}",
                        source="checkpoint",
                        slide_index=index,
                        html=html,
                    )
                )
                count += 1
    return slides
