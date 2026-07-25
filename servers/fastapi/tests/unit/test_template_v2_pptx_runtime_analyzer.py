"""Wiring between the analyzer selector and the two extraction backends.

`TEMPLATE_V2_PPTX_ANALYZER` is read in exactly one place -- the branch inside
`run_template_v2_pptx_import` -- so the selector is covered by driving that
function with both backends replaced by recorders, not by calling them directly.

The two paths deliberately converge at the confirmed draft rather than at the
analysis: the deterministic one replays parser candidates through the assembler,
while the runtime one stores already-validated layouts.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import uuid

import pytest

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from types import SimpleNamespace

from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.template_v2_pptx_import import TemplateV2PptxImport
from services import template_v2_pptx_ingestion_service as ingestion
from services.export_task_service import PptxToJsonDocument


RUNTIME_URL = "http://host/app_data/pptx-to-json/abc/images/x.png"
PRIVATE_REFERENCE = "/api/v1/ppt/structured-templates/imports/i/assets/x.png"
PPTX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.presentationml.presentation"
)


def _runtime_layout(
    data_url: str,
    *,
    layout_id: str = "slide_1",
    elements: int = 1,
) -> dict:
    return {
        "id": layout_id,
        "elements": [
            {
                "type": "image",
                "position": {"x": 10.0, "y": 20.0},
                "size": {"width": 100.0, "height": 50.0},
                "data": data_url,
                "fit": "fill",
                "decorative": True,
                "name": f"picture_{index + 1}",
                "is_icon": False,
            }
            for index in range(elements)
        ],
    }


def _runtime_placeholder_layout() -> dict:
    return {
        "id": "slide_placeholders",
        "elements": [
            {
                "type": "text",
                "position": {"x": 80.0, "y": 40.0},
                "size": {"width": 800.0, "height": 80.0},
                "runs": [{"text": "Quarterly results"}],
                "decorative": True,
                "name": "title_1",
                "max_length": 68,
                "min_length": 0,
            },
            {
                "type": "image",
                "position": {"x": 80.0, "y": 160.0},
                "size": {"width": 560.0, "height": 320.0},
                "data": RUNTIME_URL,
                "fit": "fill",
                "decorative": True,
                "name": "picture_placeholder_2",
                "is_icon": False,
            },
            {
                "type": "image",
                "position": {"x": 680.0, "y": 160.0},
                "size": {"width": 120.0, "height": 60.0},
                "data": RUNTIME_URL,
                "fit": "contain",
                "decorative": True,
                "name": "logo_3",
                "is_icon": False,
            },
        ],
    }


class _Relocated:
    """Stands in for the storage layer's relocation result."""

    def __init__(self, mapping: dict[str, str]):
        self._mapping = mapping

    def reference_for(self, url: str) -> str | None:
        return self._mapping.get(url)


async def _analyze_runtime_import(
    monkeypatch,
    tmp_path: Path,
    layouts: list[dict],
    *,
    import_id: uuid.UUID,
) -> tuple[dict, list[dict], dict]:
    """Run the real runtime analyzer with only the converter and storage stubbed."""

    source = tmp_path / "source.pptx"
    source.write_bytes(b"PK\x03\x04")

    async def fake_convert(_path, *, session_id):
        assert session_id == str(import_id)
        # The converter really returns this model, so the analyzer sees the
        # production payload shape rather than an invented stand-in.
        return PptxToJsonDocument(layouts=layouts, output_dir=str(tmp_path))

    monkeypatch.setattr(ingestion, "verify_private_source", lambda *a, **k: source)
    monkeypatch.setattr(
        ingestion.EXPORT_TASK_SERVICE, "convert_pptx_to_json", fake_convert
    )
    monkeypatch.setattr(
        ingestion,
        "relocate_runtime_assets",
        lambda *a, **k: _Relocated({RUNTIME_URL: PRIVATE_REFERENCE}),
    )
    return await ingestion._analyze_import_source_via_runtime(
        "key",
        "a" * 64,
        import_id=import_id,
        source_filename="deck.pptx",
        source_media_type=PPTX_MEDIA_TYPE,
        source_size_bytes=4,
    )


def test_image_urls_are_repointed_at_the_relocated_private_assets():
    rewritten = ingestion._with_private_asset_references(
        [_runtime_layout(RUNTIME_URL)],
        _Relocated({RUNTIME_URL: PRIVATE_REFERENCE}),
    )

    assert rewritten[0]["elements"][0]["data"] == PRIVATE_REFERENCE


def test_unrelocated_urls_are_left_alone():
    """Only media the storage layer actually moved may be rewritten."""
    runtime_url = "http://host/app_data/images/shared.png"

    rewritten = ingestion._with_private_asset_references(
        [_runtime_layout(runtime_url)], _Relocated({})
    )

    assert rewritten[0]["elements"][0]["data"] == runtime_url


def test_runtime_analysis_is_marked_and_carries_both_projections(monkeypatch, tmp_path):
    analysis, suggestions, inventory = asyncio.run(
        _analyze_runtime_import(
            monkeypatch,
            tmp_path,
            [_runtime_layout(RUNTIME_URL)],
            import_id=uuid.uuid4(),
        )
    )

    assert analysis["analyzer"] == ingestion.RUNTIME_ANALYSIS_MARKER
    assert analysis["layouts"]["layouts"][0]["id"] == "slide_1"
    assert analysis["raw_layouts"]["layouts"][0]["id"] == "slide_1"
    # repeat blocks are candidate-derived, so this path has none
    assert suggestions == []
    assert "source_path" not in repr(inventory)


def test_runtime_analysis_classifies_and_seeds_clear_placeholders(
    monkeypatch, tmp_path
):
    analysis, _suggestions, _inventory = asyncio.run(
        _analyze_runtime_import(
            monkeypatch,
            tmp_path,
            [_runtime_placeholder_layout()],
            import_id=uuid.uuid4(),
        )
    )

    assert analysis["classification"] == {
        "version": 1,
        "strategy": "conservative-placeholder-name",
        "fillable_element_count": 2,
        "text_placeholder_count": 1,
        "image_placeholder_count": 1,
    }
    elements = analysis["raw_layouts"]["layouts"][0]["elements"]
    assert [element["decorative"] for element in elements] == [False, False, True]
    assert analysis["default_contents"] == [
        {
            "slide_placeholders_component_1": {
                "title_1": "Quarterly results",
            },
            "slide_placeholders_component_2": {
                "picture_placeholder_2": {"image_prompt": ""},
            },
        }
    ]

    draft = ingestion._runtime_confirmed_draft(analysis)
    generated = ingestion.build_generated_slide(
        draft.layouts.layouts[0], draft.contents[0]
    )
    assert generated.content == analysis["default_contents"][0]


def test_runtime_summary_counts_are_derived_from_the_converted_layouts(
    monkeypatch,
    tmp_path,
):
    """The reviewer's slides/shapes figures must come from the layouts themselves."""

    analysis, _suggestions, _inventory = asyncio.run(
        _analyze_runtime_import(
            monkeypatch,
            tmp_path,
            [
                _runtime_layout(RUNTIME_URL, layout_id="slide_1", elements=2),
                _runtime_layout(RUNTIME_URL, layout_id="slide_2", elements=1),
            ],
            import_id=uuid.uuid4(),
        )
    )

    assert analysis["summary"]["slide_count"] == 2
    assert analysis["summary"]["shape_count"] == 3
    # every runtime element is validated verbatim or the analysis raises
    assert analysis["summary"]["supported_shape_count"] == 3
    assert analysis["summary"]["unsupported_shape_count"] == 0
    assert analysis["summary"]["review_required"] is True


def test_confirm_rebuilds_the_draft_from_stored_runtime_layouts():
    analysis = {
        "analyzer": ingestion.RUNTIME_ANALYSIS_MARKER,
        "raw_layouts": {
            "layouts": [
                {
                    "id": "slide_1",
                    "description": "Runtime PPTX import of layout slide_1.",
                    "elements": [],
                }
            ]
        },
        "layouts": {
            "layouts": [
                {
                    "id": "slide_1",
                    "description": "Runtime PPTX import of layout slide_1.",
                    "components": [],
                }
            ]
        },
    }
    job = SimpleNamespace(analysis_result=analysis)

    draft = ingestion._assemble_confirmed_candidate(job, [])

    assert [layout.id for layout in draft.layouts.layouts] == ["slide_1"]
    # one empty mapping per layout: the converter marks everything decorative, so
    # the template schema is empty and the layout is taken as-is
    assert draft.contents == [{}]


def test_confirm_rejects_a_runtime_analysis_missing_its_layouts():
    job = SimpleNamespace(analysis_result={"analyzer": ingestion.RUNTIME_ANALYSIS_MARKER})

    with pytest.raises(ValueError, match="template_v2_import_runtime_layouts_missing"):
        ingestion._assemble_confirmed_candidate(job, [])


def test_confirm_rejects_malformed_runtime_default_contents(monkeypatch, tmp_path):
    analysis, _suggestions, _inventory = asyncio.run(
        _analyze_runtime_import(
            monkeypatch,
            tmp_path,
            [_runtime_placeholder_layout()],
            import_id=uuid.uuid4(),
        )
    )
    analysis["default_contents"] = {}

    with pytest.raises(
        ValueError, match="template_v2_import_runtime_contents_invalid"
    ):
        ingestion._runtime_confirmed_draft(analysis)


def test_deterministic_analysis_still_replays_candidates():
    """The default path must be untouched by the new branch."""
    job = SimpleNamespace(analysis_result={"summary": {}})

    with pytest.raises(ValueError, match="template_v2_import_candidate_missing"):
        ingestion._assemble_confirmed_candidate(job, [])


async def _database(path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: SQLModel.metadata.create_all(
                sync_connection,
                tables=[
                    AsyncPresentationGenerationTaskModel.__table__,
                    TemplateV2PptxImport.__table__,
                ],
            )
        )
    return engine, maker


async def _import_row(maker, *, state: str = "processing") -> tuple[uuid.UUID, str]:
    """One import row plus its task, with the attempt columns `state` implies.

    `processing` is a real claimed attempt, so `_persist_analysis` writes the
    reviewer's manifest; `queued` is what the dispatcher hands to
    `run_template_v2_pptx_import`, which claims it itself.
    """

    claimed = state == "processing"
    import_id = uuid.uuid4()
    task_id = f"task-{uuid.uuid4().hex}"
    async with maker() as session:
        session.add(
            AsyncPresentationGenerationTaskModel(
                id=task_id,
                status="running" if claimed else "pending",
                data={
                    "kind": ingestion.IMPORT_TASK_KIND,
                    "import_id": str(import_id),
                    "state": state,
                    "attempt_number": 1 if claimed else 0,
                },
            )
        )
        session.add(
            TemplateV2PptxImport(
                id=import_id,
                task_id=task_id,
                requested_template_id=f"template-{uuid.uuid4().hex}",
                state=state,
                attempt_number=1 if claimed else 0,
                attempt_token="current-owner" if claimed else None,
                lease_expires_at=(
                    datetime.now(timezone.utc) + timedelta(minutes=10)
                    if claimed
                    else None
                ),
                source_filename="deck.pptx",
                source_media_type=PPTX_MEDIA_TYPE,
                source_size_bytes=4,
                source_sha256="a" * 64,
                source_storage_key=f"private/{import_id}.pptx",
                manifest={"schema_version": 1},
            )
        )
        await session.commit()
    return import_id, task_id


def test_persisted_manifest_names_the_runtime_analyzer_for_the_reviewer(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """`review_required` is a human gate, so the stored analysis must identify itself."""

    async def scenario() -> None:
        engine, maker = await _database(tmp_path / "runtime-manifest.sqlite")
        monkeypatch.setattr(ingestion, "async_session_maker", maker)
        try:
            import_id, task_id = await _import_row(maker)
            analysis, suggestions, inventory = await _analyze_runtime_import(
                monkeypatch,
                tmp_path,
                [_runtime_layout(RUNTIME_URL)],
                import_id=import_id,
            )

            assert await ingestion._persist_analysis(
                import_id,
                task_id,
                "current-owner",
                analysis,
                suggestions,
                inventory,
            )

            async with maker() as session:
                job = await session.get(TemplateV2PptxImport, import_id)
                assert job is not None
                assert job.state == "review_required"
                recorded = job.manifest["analysis"]
                assert recorded["provider"]["id"] == ingestion.RUNTIME_ANALYZER_PROVIDER
                assert recorded["provider"]["external_ai"] is False
                assert recorded["status"] == "completed"
                assert recorded["summary"]["slide_count"] == 1
                assert recorded["summary"]["shape_count"] == 1
                # deliberately absent: this payload is not the deterministic
                # analyzer's `CandidateAnalysis` contract document
                assert recorded["contract_version"] is None
                # the panel reads the row's analysis_result, not the manifest
                assert (
                    job.analysis_result["provider"]["id"]
                    == ingestion.RUNTIME_ANALYZER_PROVIDER
                )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def _analyzer_recorders(monkeypatch) -> list[str]:
    """Both backends, replaced by recorders that keep the selector's contract.

    The runtime one is awaited directly and the deterministic one is handed to
    `asyncio.to_thread`, so they have to differ in exactly that way here too.
    """

    awaited: list[str] = []

    async def runtime(*_args, **_kwargs) -> tuple[dict, list[dict], dict]:
        awaited.append("runtime")
        return {"analyzer": ingestion.RUNTIME_ANALYSIS_MARKER}, [], {}

    def deterministic(*_args, **_kwargs) -> tuple[dict, list[dict], dict]:
        awaited.append("deterministic")
        return {"analyzer": ingestion.DETERMINISTIC_ANALYSIS_MARKER}, [], {}

    monkeypatch.setattr(ingestion, "_analyze_import_source_via_runtime", runtime)
    monkeypatch.setattr(ingestion, "_analyze_import_source", deterministic)
    return awaited


_ANALYSIS_MARKERS = {
    "runtime": ingestion.RUNTIME_ANALYSIS_MARKER,
    "deterministic": ingestion.DETERMINISTIC_ANALYSIS_MARKER,
}


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        ("runtime", "runtime"),
        ("deterministic", "deterministic"),
        (None, "deterministic"),
        # fail-closed: an unreadable value never silently promotes the runtime
        # backend, and never blocks the import either
        ("hybrid", "deterministic"),
    ],
)
def test_the_configured_analyzer_is_the_one_the_import_actually_runs(
    tmp_path: Path,
    monkeypatch,
    configured: str | None,
    expected: str,
) -> None:
    """`TEMPLATE_V2_PPTX_ANALYZER` has no other effect anywhere in the product.

    Losing this branch is silent: every import keeps succeeding, and an operator
    who switched the canary to the runtime backend still gets deterministic
    output -- no images, no per-run styling, no vectors.
    """

    async def scenario() -> None:
        engine, maker = await _database(tmp_path / "analyzer-selector.sqlite")
        monkeypatch.setattr(ingestion, "async_session_maker", maker)
        if configured is None:
            monkeypatch.delenv("TEMPLATE_V2_PPTX_ANALYZER", raising=False)
        else:
            monkeypatch.setenv("TEMPLATE_V2_PPTX_ANALYZER", configured)
        awaited = _analyzer_recorders(monkeypatch)
        try:
            import_id, task_id = await _import_row(maker, state="queued")

            await ingestion.run_template_v2_pptx_import(import_id, task_id)

            assert awaited == [expected], "exactly one backend may run per attempt"
            async with maker() as session:
                job = await session.get(TemplateV2PptxImport, import_id)
                assert job is not None
                # the selected backend's own payload is what the reviewer sees
                assert job.state == "review_required"
                assert job.analysis_result["analyzer"] == _ANALYSIS_MARKERS[expected]
        finally:
            await engine.dispose()

    asyncio.run(scenario())
