"""Rendered remake candidates remain reviewable despite DOM findings.
Generation, render, and integrity failures remain fatal."""

from tests.support.existing_authored_rows import add_existing_authored_presentation
import asyncio
import uuid
from functools import wraps
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel

from models.presentation_outline_model import (
    PresentationOutlineModel,
    SlideOutlineModel,
)
from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from services.workspace_operation_runner import sanitize_workspace_event_payload
from utils.authored_dom_quality import (
    STYLE_REFERENCE,
    DomQualityIssue,
    DomQualityReport,
)

SOURCE_HTML = "<html><body><main data-qid='source'>Source</main></body></html>"
CANDIDATE_HTML = "<html><body><main data-qid='candidate'>Candidate</main></body></html>"
CANDIDATE_IMAGE = "/app_data/images/remake-candidate.png"


def async_test(function):
    @wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))

    return run


async def _engine(path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
    return engine


def _presentation(slide_count: int = 1) -> PresentationModel:
    outline = PresentationOutlineModel(
        slides=[
            SlideOutlineModel(content=f"Keep all facts {index}")
            for index in range(slide_count)
        ]
    )
    return PresentationModel(
        content="Deck",
        n_slides=slide_count,
        language="Korean",
        title="Deck",
        mode="authored",
        outlines=outline.model_dump(mode="json"),
        theme={"style": "default"},
    )


def _slide(presentation_id, index: int = 0) -> SlideModel:
    return SlideModel(
        presentation=presentation_id,
        layout_group="authored",
        layout="authored:content",
        index=index,
        content={"__authored__": True, "role": "content", "image": f"old-{index}.png"},
        html_content=f"{SOURCE_HTML}<!--{index}-->",
        properties={
            "image": f"old-{index}.png",
            "slidespec_active": {"bundle_id": f"old-{index}"},
        },
    )


def _issue(category: str = "overlap", detail: str = "summary overlaps cards"):
    return DomQualityIssue(
        category=category,
        severity="high",
        detail=detail,
        qids=("summary", "card"),
    )


def _report(*, issues=(), telemetry: str = "available") -> DomQualityReport:
    return DomQualityReport(
        reference_mode=STYLE_REFERENCE,
        issues=tuple(issues),
        compact_dom=(),
        inspected_elements=2,
        telemetry_status=telemetry,
    )


class _BoundSessionMaker:
    """Run the service's background boundary on the open test session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def __call__(self) -> "_BoundSessionMaker":
        return self

    async def __aenter__(self) -> AsyncSession:
        return self._session

    async def __aexit__(self, *_exc) -> bool:
        return False


async def _seed(session: AsyncSession, slide_count: int = 1):
    presentation = _presentation(slide_count)
    await add_existing_authored_presentation(session, presentation)
    await session.flush()
    slides = [_slide(presentation.id, index) for index in range(slide_count)]
    task = AsyncPresentationGenerationTaskModel(status="pending")
    session.add_all([*slides, task])
    await session.commit()
    return presentation, slides, task


# --------------------------------------------------------------------------
# Preview: quality findings are advisory
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# Preview: real failures stay fatal
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# Index / page-number conversion
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# Publish
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# Workspace event leak surface
# --------------------------------------------------------------------------


def test_workspace_event_payload_drops_candidate_material():
    safe = sanitize_workspace_event_payload(
        {
            "kind": "authored_page_remake",
            "status": "published",
            "presentation_id": str(uuid.uuid4()),
            "slide_index": 0,
            "candidate_html": CANDIDATE_HTML,
            "candidate_image": CANDIDATE_IMAGE,
            "candidate_digest": "sha256:candidate",
            "source_digest": "sha256:source",
            "post_verify_status": "warning",
            "result_revision": 4,
        },
        operation_kind="slide.regenerate",
    )

    assert "candidate_html" not in safe
    assert "candidate_image" not in safe
    assert "candidate_digest" not in safe
    assert "source_digest" not in safe
    assert safe["status"] == "published"
    assert safe["result_revision"] == 4
