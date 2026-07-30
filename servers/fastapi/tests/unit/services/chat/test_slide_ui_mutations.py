from __future__ import annotations

import asyncio
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel, select

from api.v1.auth.context import reset_current_owner_id, set_current_owner_id
from models.sql.presentation import PresentationModel
from models.sql.presentation_version import PresentationVersionModel
from models.sql.slide import SlideModel
from services.chat.slide_ui_content import apply_content_update
from services.chat.slide_ui_mutations import (
    SlideUIAccessError,
    SlideUIMutationService,
    SlideUIRevisionConflict,
)
from services.chat.slide_ui_query import (
    compact_slide_ui,
    parse_element_path,
    slide_ui_revision,
)
from templates.v2.persistence import canonicalize_slide_ui


TEXT_PATH = "components[0].elements[0]"
CHART_PATH = "components[0].elements[1]"
TABLE_PATH = "components[0].elements[2]"


def _native_ui() -> dict[str, Any]:
    return {
        "id": "chat-editable",
        "description": "Native slide used for transactional Chat mutation tests",
        "components": [
            {
                "id": "main",
                "description": "Editable text chart and table component",
                "position": {"x": 0, "y": 0},
                "elements": [
                    {
                        "type": "text",
                        "runs": [
                            {
                                "text": "Revenue ",
                                "font": {"size": 18, "bold": True},
                            },
                            {
                                "text": "report",
                                "font": {"size": 18, "italic": True},
                            },
                        ],
                        "decorative": False,
                        "name": "title",
                        "min_length": 1,
                        "max_length": 80,
                    },
                    {
                        "type": "chart",
                        "chart_type": "bar",
                        "title": "Revenue",
                        "x_axis": True,
                        "y_axis": True,
                        "x_axis_title": "Quarter",
                        "y_axis_title": "USD",
                        "legend": True,
                        "data_labels": "top",
                        "categories": ["Q1", "Q2"],
                        "series": [{"name": "Actual", "values": [42, 51]}],
                        "decorative": False,
                        "name": "revenue-chart",
                    },
                    {
                        "type": "table",
                        "columns": [
                            {
                                "runs": [{"text": "Quarter"}],
                                "color": {"color": "#E0F2FE"},
                                "font": {"bold": True},
                                "alignment": "left",
                            },
                            {
                                "runs": [{"text": "Revenue"}],
                                "color": {"color": "#F1F5F9"},
                                "font": {"bold": True},
                                "alignment": "right",
                            },
                        ],
                        "rows": [
                            [
                                {
                                    "runs": [{"text": "Q1"}],
                                    "color": {"color": "#FFFFFF"},
                                    "alignment": "left",
                                },
                                {
                                    "runs": [{"text": "42"}],
                                    "color": {"color": "#FFFFFF"},
                                    "alignment": "right",
                                },
                            ]
                        ],
                        "decorative": False,
                        "name": "revenue-table",
                        "min_columns": 1,
                        "max_columns": 5,
                        "min_rows": 0,
                        "max_rows": 8,
                    },
                ],
            }
        ],
    }


def _slide(
    presentation_id: uuid.UUID,
    owner_id: uuid.UUID,
) -> SlideModel:
    return SlideModel(
        owner_id=owner_id,
        presentation=presentation_id,
        layout_group="native",
        layout="chat-editable",
        index=0,
        content={"title": "Revenue report"},
        html_content=None,
        ui=_native_ui(),
        properties={"source": "template-v2"},
    )


async def _database(
    path: Path,
    *,
    owner_id: uuid.UUID,
) -> tuple[Any, Any, uuid.UUID, uuid.UUID]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: SQLModel.metadata.create_all(
                sync_connection,
                tables=[
                    PresentationModel.__table__,
                    PresentationVersionModel.__table__,
                    SlideModel.__table__,
                ],
            )
        )
    presentation = PresentationModel(
        owner_id=owner_id,
        content="Chat mutation contract",
        n_slides=1,
        language="en",
        layout={"name": "native"},
    )
    slide = _slide(presentation.id, owner_id)
    async with maker() as session:
        session.add_all([presentation, slide])
        await session.commit()
    return engine, maker, presentation.id, slide.id


def test_query_paths_are_allowlisted_and_compact_results_are_detached() -> None:
    ui = _native_ui()

    assert parse_element_path(TEXT_PATH) == [
        ("components", 0),
        ("elements", 0),
    ]
    with pytest.raises(ValueError, match="segment_not_allowed"):
        parse_element_path("components[0].__class__[0]")
    with pytest.raises(ValueError, match="must_start_with_component"):
        parse_element_path("elements[0].children[0]")

    compact = compact_slide_ui(ui, include_full_json=True)
    compact["ui"]["components"][0]["elements"][0]["runs"][0]["text"] = "changed"
    assert ui["components"][0]["elements"][0]["runs"][0]["text"] == "Revenue "
    assert compact["revision"] == slide_ui_revision(ui)


def test_bounded_text_run_style_preserves_unselected_and_existing_run_fonts() -> None:
    text = deepcopy(_native_ui()["components"][0]["elements"][0])

    apply_content_update(
        text,
        {
            "run_selection": {
                "start": 2,
                "end": 11,
                "font": {"color": "#DC2626", "underline": True},
            }
        },
    )

    assert [run["text"] for run in text["runs"]] == [
        "Re",
        "venue ",
        "rep",
        "ort",
    ]
    assert text["runs"][0]["font"] == {"size": 18, "bold": True}
    assert text["runs"][1]["font"] == {
        "size": 18,
        "bold": True,
        "color": "#DC2626",
        "underline": True,
    }
    assert text["runs"][2]["font"] == {
        "size": 18,
        "italic": True,
        "color": "#DC2626",
        "underline": True,
    }
    assert text["runs"][3]["font"] == {"size": 18, "italic": True}

    before = deepcopy(text)
    with pytest.raises(ValueError, match="run_selection_out_of_bounds"):
        apply_content_update(
            text,
            {
                "run_selection": {
                    "start": -1,
                    "end": 2,
                    "font": {"bold": True},
                }
            },
        )
    assert text == before


def test_chart_update_preserves_axes_legend_and_unrelated_metadata() -> None:
    chart = deepcopy(_native_ui()["components"][0]["elements"][1])

    apply_content_update(
        chart,
        {
            "categories": ["Q2", "Q3"],
            "series": [{"name": "Forecast", "values": [51, 63]}],
        },
    )

    assert chart["categories"] == ["Q2", "Q3"]
    assert chart["series"] == [{"name": "Forecast", "values": [51, 63]}]
    assert chart["x_axis_title"] == "Quarter"
    assert chart["y_axis_title"] == "USD"
    assert chart["legend"] is True
    assert chart["data_labels"] == "top"


def test_table_row_and_column_operations_preserve_cell_objects() -> None:
    ui = _native_ui()
    table = ui["components"][0]["elements"][2]

    apply_content_update(
        table,
        {"action": "add-row", "index": 1, "values": ["Q2", "51"]},
    )
    assert table["rows"][1][0]["color"] == {"color": "#FFFFFF"}
    assert table["rows"][1][0]["alignment"] == "left"
    assert table["rows"][1][1]["alignment"] == "right"

    apply_content_update(
        table,
        {
            "action": "add-column",
            "index": 1,
            "header": "Status",
            "values": ["Actual", "Forecast"],
        },
    )
    assert table["columns"][1]["font"] == {"bold": True}
    assert table["columns"][1]["color"] == {"color": "#E0F2FE"}
    assert table["rows"][0][1]["color"] == {"color": "#FFFFFF"}

    expected_column = deepcopy(table["columns"][1])
    expected_cells = [deepcopy(row[1]) for row in table["rows"]]
    apply_content_update(table, {"action": "move-column", "from": 1, "to": 2})
    assert table["columns"][2] == expected_column
    assert [row[2] for row in table["rows"]] == expected_cells

    expected_row = deepcopy(table["rows"][1])
    apply_content_update(table, {"action": "move-row", "from": 1, "to": 0})
    assert table["rows"][0] == expected_row
    assert canonicalize_slide_ui(ui) is not None


@pytest.mark.parametrize("action", ["merge", "split"])
def test_table_merge_and_split_are_explicitly_unsupported_and_non_mutating(
    action: str,
) -> None:
    table = deepcopy(_native_ui()["components"][0]["elements"][2])
    before = deepcopy(table)

    with pytest.raises(
        ValueError,
        match="table_merge_split_not_supported_by_current_schema",
    ):
        apply_content_update(table, {"action": action})

    assert table == before


def test_owner_mismatch_is_reported_as_not_found_or_forbidden(tmp_path) -> None:
    async def runner() -> None:
        owner_id = uuid.uuid4()
        other_owner_id = uuid.uuid4()
        engine, maker, presentation_id, _ = await _database(
            tmp_path / "owner.db",
            owner_id=owner_id,
        )
        token = set_current_owner_id(other_owner_id)
        try:
            async with maker() as session:
                service = SlideUIMutationService(session, presentation_id)
                with pytest.raises(
                    SlideUIAccessError,
                    match="slide_not_found_or_forbidden",
                ):
                    await service.get_slide_ui(index=0)
        finally:
            reset_current_owner_id(token)
            await engine.dispose()

    asyncio.run(runner())


def test_revision_conflict_and_malformed_ui_leave_slide_and_versions_unchanged(
    tmp_path,
) -> None:
    async def runner() -> None:
        owner_id = uuid.uuid4()
        engine, maker, presentation_id, slide_id = await _database(
            tmp_path / "fail-closed.db",
            owner_id=owner_id,
        )
        expected_ui = canonicalize_slide_ui(_native_ui())
        token = set_current_owner_id(owner_id)
        try:
            async with maker() as session:
                service = SlideUIMutationService(session, presentation_id)
                with pytest.raises(
                    SlideUIRevisionConflict,
                    match="slide_ui_revision_conflict",
                ):
                    await service.update_element(
                        index=0,
                        path=TEXT_PATH,
                        expected_revision="sha256:stale",
                        content={"text": "Must not persist"},
                    )

            malformed = deepcopy(expected_ui)
            malformed["unexpected"] = True
            async with maker() as session:
                service = SlideUIMutationService(session, presentation_id)
                with pytest.raises(ValueError):
                    await service.update_slide_ui(
                        index=0,
                        expected_revision=slide_ui_revision(expected_ui),
                        ui=malformed,
                    )

            async with maker() as session:
                reopened = await session.get(SlideModel, slide_id)
                versions = list(
                    await session.scalars(
                        select(PresentationVersionModel).where(
                            PresentationVersionModel.presentation_id
                            == presentation_id
                        )
                    )
                )
                assert reopened is not None
                assert reopened.ui == expected_ui
                assert versions == []
        finally:
            reset_current_owner_id(token)
            await engine.dispose()

    asyncio.run(runner())


def test_successful_mutation_commits_snapshot_of_previous_slide(tmp_path) -> None:
    async def runner() -> None:
        owner_id = uuid.uuid4()
        engine, maker, presentation_id, slide_id = await _database(
            tmp_path / "snapshot.db",
            owner_id=owner_id,
        )
        before = canonicalize_slide_ui(_native_ui())
        token = set_current_owner_id(owner_id)
        try:
            async with maker() as session:
                result = await SlideUIMutationService(
                    session,
                    presentation_id,
                ).update_element(
                    index=0,
                    path=CHART_PATH,
                    expected_revision=slide_ui_revision(before),
                    content={
                        "categories": ["Q2", "Q3"],
                        "series": [{"name": "Forecast", "values": [51, 63]}],
                    },
                )
                assert result["updated"] is True

            async with maker() as session:
                reopened = await session.get(SlideModel, slide_id)
                versions = list(
                    await session.scalars(
                        select(PresentationVersionModel).where(
                            PresentationVersionModel.presentation_id
                            == presentation_id
                        )
                    )
                )
                assert reopened is not None
                chart = reopened.ui["components"][0]["elements"][1]
                assert chart["categories"] == ["Q2", "Q3"]
                assert chart["x_axis_title"] == "Quarter"
                assert len(versions) == 1
                assert versions[0].label == "chat:updateElement"
                assert versions[0].slides[0]["ui"] == before
        finally:
            reset_current_owner_id(token)
            await engine.dispose()

    asyncio.run(runner())


def test_commit_failure_rolls_back_slide_and_snapshot(tmp_path) -> None:
    class CommitFailingSession:
        def __init__(self, inner: Any) -> None:
            self.inner = inner
            self.rollback_count = 0

        def __getattr__(self, name: str) -> Any:
            return getattr(self.inner, name)

        async def commit(self) -> None:
            raise RuntimeError("injected commit failure")

        async def rollback(self) -> None:
            self.rollback_count += 1
            await self.inner.rollback()

    async def runner() -> None:
        owner_id = uuid.uuid4()
        engine, maker, presentation_id, slide_id = await _database(
            tmp_path / "rollback.db",
            owner_id=owner_id,
        )
        before = canonicalize_slide_ui(_native_ui())
        token = set_current_owner_id(owner_id)
        try:
            async with maker() as session:
                failing = CommitFailingSession(session)
                service = SlideUIMutationService(failing, presentation_id)
                with pytest.raises(RuntimeError, match="injected commit failure"):
                    await service.update_element(
                        index=0,
                        path=TEXT_PATH,
                        expected_revision=slide_ui_revision(before),
                        content={"text": "Must roll back"},
                    )
                assert failing.rollback_count == 1

            async with maker() as session:
                reopened = await session.get(SlideModel, slide_id)
                versions = list(
                    await session.scalars(
                        select(PresentationVersionModel).where(
                            PresentationVersionModel.presentation_id
                            == presentation_id
                        )
                    )
                )
                assert reopened is not None
                assert reopened.ui == before
                assert versions == []
        finally:
            reset_current_owner_id(token)
            await engine.dispose()

    asyncio.run(runner())
