"""Checkpoint payload primitives for durable SVG generation.

Digesting page inputs, persisting/reading rendered PNGs by content hash, and
recovering a page's progress from its durable checkpoint row all live here.

Extracted out of ``svg_presentation_service.py`` (kept at/under the file-size
ratchet); the facade there re-exports every name importers use.
"""
from __future__ import annotations

import asyncio
from hashlib import sha256
import json
from pathlib import Path
import re

from sqlalchemy import select

from models.sql.authored_slide_checkpoint import AuthoredSlideCheckpoint
from services.authored_checkpoint_artifacts import atomic_write, checkpoint_root
from services.authored_checkpoint_fence import owned_job
from services.authored_presentation_artifacts import _save_slide_pngs
from services.database import async_session_maker
from utils.datetime_utils import get_current_utc_datetime


def _digest(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


_NUMERIC_CLAIM = re.compile(
    r"(?<![\w.])(?:\d{1,3}(?:,\d{3})+|\d+\.\d+|\d{2,})(?:\s?(?:%|년|월|일|억|만|개|명|건|원|배|점|GB|MB|KB))?"
    r"|(?<![\w.])\d+(?:\s?(?:%|년|월|일|억|만|개|명|건|원|배|점|GB|MB|KB))"
)


def _required_numeric_claims(content: str) -> list[str]:
    """Protect concrete figures without requiring verbatim prose or bullet labels."""
    return list(dict.fromkeys(match.group(0).strip() for match in _NUMERIC_CLAIM.finditer(content)))


def _provider_attempt_count(payload: dict) -> int:
    return sum(attempt.get("kind") != "local" for attempt in payload.get("attempts", []))


async def _collect_pages_or_fail(tasks):
    """Stop waiting on siblings after the first terminal page failure."""
    try:
        return await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            if not task.done():
                task.cancel()
        # Cancellation abandons waiting, not a proof that the provider stopped.
        # The pre-dispatch inflight checkpoint remains the recovery witness.
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


async def _checkpoint(job, index, *, payload=None):
    if job is None:
        return None
    async with async_session_maker() as session:
        active = await owned_job(session, job.id, job.attempt_token,
                                 fencing_token=getattr(job, "_workspace_fencing_token", None))
        row = await session.scalar(select(AuthoredSlideCheckpoint).where(
            AuthoredSlideCheckpoint.job_id == job.id,
            AuthoredSlideCheckpoint.slide_index == index,
        ))
        if row is None:
            raise RuntimeError("svg_checkpoint_missing")
        if payload is not None:
            row.svg_checkpoint = payload
            row.attempt_token = job.attempt_token
            row.author_status = "completed" if payload.get("svg_content") else "running"
            row.illustrate_status = "skipped"
            row.render_status = "completed" if payload.get("png_sha256") else "pending"
            row.author_completed_at = get_current_utc_datetime() if payload.get("svg_content") else None
            row.render_completed_at = get_current_utc_datetime() if payload.get("png_sha256") else None
            row.failed_stage = None
            row.error = None
            session.add(row)
            await session.flush()
            states = await session.scalars(select(AuthoredSlideCheckpoint.render_status).where(
                AuthoredSlideCheckpoint.job_id == job.id))
            active.completed_slides = sum(state == "completed" for state in states)
            active.current_stage = "render" if payload.get("svg_content") else "author"
            session.add(active)
        result = dict(row.svg_checkpoint or {})
        await session.commit()
        return result


def _write_png(png: bytes) -> dict:
    digest = sha256(png).hexdigest()
    relative = Path("svg") / digest[:2] / f"{digest}.png"
    atomic_write(checkpoint_root() / relative, png)
    return {"png_sha256": digest, "png_path": relative.as_posix()}


def _read_png(payload: dict) -> bytes | None:
    digest = payload.get("png_sha256", "")
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        return None
    path = checkpoint_root() / "svg" / digest[:2] / f"{digest}.png"
    if not path.is_file():
        return None
    png = path.read_bytes()
    return png if sha256(png).hexdigest() == digest else None


def _save_source_page_pngs(presentation_id, pages, publication_token):
    """Publish one verified checkpoint image at a time, not a deck of PNG bytes."""
    references = []
    for payload, _ in pages:
        png = _read_png(payload)
        if png is None:
            raise ValueError("svg_source_checkpoint_image_missing")
        references.extend(_save_slide_pngs(presentation_id, [png], publication_token))
    return references
