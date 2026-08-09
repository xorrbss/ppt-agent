"""Test-only FastAPI app for hard-process authored recovery tests.

The parent pytest process supplies every path through environment variables.
This module is imported by a separate ``python -m uvicorn`` process so none of
the in-memory worker registry or SQLAlchemy state can survive a restart.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from PIL import Image
from sqlmodel import SQLModel

from api.v1.ppt.endpoints import presentation_generate as endpoint
from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.authored_slide_checkpoint import AuthoredSlideCheckpoint
from models.sql.presentation import PresentationModel
from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.slide import SlideModel
from models.sql.workspace import WorkspaceOperation
from services import authored_presentation_service as authored
from services import database
from services import presentation_generation_job_service as jobs
from utils import llm_provider


EVENT_LOG = Path(os.environ["AUTHORED_SUBPROCESS_EVENT_LOG"])
SLIDE_COUNT = int(os.getenv("AUTHORED_SUBPROCESS_SLIDES", "6"))
BLOCK_RENDER_SLIDE = int(
    os.getenv("AUTHORED_SUBPROCESS_BLOCK_RENDER_SLIDE", "-1")
)


def _record(event: str, slide_index: int | None = None) -> None:
    EVENT_LOG.parent.mkdir(parents=True, exist_ok=True)
    payload = {"pid": os.getpid(), "event": event}
    if slide_index is not None:
        payload["slide_index"] = slide_index
    with EVENT_LOG.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(payload, sort_keys=True) + "\n")
        stream.flush()


def _png() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (160, 90), (35, 100, 180)).save(output, format="PNG")
    return output.getvalue()


PLAN = SimpleNamespace(
    contents=tuple(f"Slide {index + 1}" for index in range(SLIDE_COUNT)),
    design_system="SUBPROCESS CHECKPOINT RECOVERY",
)
RENDERED_PNG = _png()


async def _fake_brief(*_args, **_kwargs):
    _record("brief_success")
    return SimpleNamespace(to_prompt_context=lambda: "subprocess content brief")


async def _fake_outline(*_args, **_kwargs):
    _record("outline_success")
    yield json.dumps(
        {
            "slides": [
                {"content": f"Slide {index + 1}"}
                for index in range(SLIDE_COUNT)
            ]
        }
    )


async def _fake_author(_selected_plan, index, **_options):
    await asyncio.sleep(0.02)
    _record("author_success", index)
    return (
        "<!DOCTYPE html><html><body>"
        f"<h1 data-index='{index}'>Slide {index + 1}</h1>"
        "</body></html>"
    )


async def _fake_render(html):
    index = int(html.split("data-index='", 1)[1].split("'", 1)[0])
    _record("render_start", index)
    if index == BLOCK_RENDER_SLIDE:
        _record("render_blocked", index)
        await asyncio.Event().wait()
    await asyncio.sleep(0.02)
    _record("render_success", index)
    return RENDERED_PNG


async def _no_op(*_args, **_kwargs):
    return None


endpoint.generate_content_brief = _fake_brief
endpoint.generate_ppt_outline = _fake_outline
endpoint.MEM0_PRESENTATION_MEMORY_SERVICE.store_generation_context = _no_op
endpoint.MEM0_PRESENTATION_MEMORY_SERVICE.store_generated_outlines = _no_op
authored.prepare_authored_deck = lambda *_args, **_kwargs: PLAN
authored.author_planned_slide = _fake_author
authored.is_fallback_slide_html = lambda _html: False
authored.render_html_to_png = _fake_render
authored._AUTHORED_SLIDE_WORKERS = 1
authored._AUTHORED_JOB_SEMAPHORE = asyncio.Semaphore(1)
llm_provider.get_llm_provider = lambda: SimpleNamespace(
    value="subprocess-checkpoint-test"
)
llm_provider.get_model = lambda: "stub-model"

# The lease is what lets a different process take over a hard-killed attempt,
# so the parent test sizes it per runtime: short in the runtime it is about to
# kill (the expiry it polls for arrives quickly) and long in the runtime that
# has to finish the deck (a contended host must not evict a healthy attempt
# mid-resume; the production default is 300 seconds). The heartbeat stays well
# inside the shorter of the two so a live worker keeps renewing its own lease.
jobs.GENERATION_JOB_LEASE_SECONDS = float(
    os.getenv("AUTHORED_SUBPROCESS_LEASE_SECONDS", "8")
)
jobs.GENERATION_JOB_HEARTBEAT_SECONDS = 0.5
# Both jobs in this test require an export cookie, so the dispatcher never
# launches them; its only work here is the expired-lease sweep. Sweeping every
# 50ms opened a write transaction twenty times a second on the same SQLite
# file the worker uses for checkpoints and heartbeats, which is exactly the
# contention that could starve a heartbeat under suite load.
jobs.GENERATION_JOB_DISPATCH_SECONDS = 0.5


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    async with database.sql_engine.begin() as connection:
        await connection.exec_driver_sql("PRAGMA journal_mode=WAL")
        await connection.run_sync(
            SQLModel.metadata.create_all,
            tables=[
                AsyncPresentationGenerationTaskModel.__table__,
                PresentationGenerationJob.__table__,
                AuthoredSlideCheckpoint.__table__,
                PresentationModel.__table__,
                SlideModel.__table__,
                # PresentationGenerationJob has an optional FK to this table.
                # SQLite still requires the referenced table to exist when a
                # legacy job with a NULL workspace_operation_id is inserted.
                WorkspaceOperation.__table__,
            ],
        )
    await jobs.start_presentation_generation_dispatcher()
    _record("startup")
    try:
        yield
    finally:
        await jobs.stop_presentation_generation_dispatcher()
        await database.dispose_engines()


app = FastAPI(lifespan=_lifespan)
app.include_router(endpoint.PRESENTATION_GENERATE_ROUTER, prefix="/api/v1/ppt")
