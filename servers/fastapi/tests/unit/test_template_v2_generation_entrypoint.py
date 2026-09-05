import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
import uuid
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError
import pytest

from models.generate_presentation_request import GeneratePresentationRequest
from models.presentation_outline_model import (
    PresentationOutlineModel,
    SlideOutlineModel,
)
from models.presentation_structure_model import PresentationStructureModel
from models.presentation_layout import SlideLayoutModel
from models.sql.slide import SlideModel
from services.template_v2_generation_service import (
    TemplateV2GenerationError,
    TemplateV2GenerationLayout,
    TemplateV2GenerationTarget,
    build_template_v2_slides,
    load_template_v2_generation_target,
    preflight_template_v2_native_pptx,
)
from templates.v2.models.layouts import SlideLayout
from utils.llm_calls.generate_slide_content import _prepare_response_schema


def _layout(
    layout_id: str = "title-slide",
    *,
    decorative: bool = False,
) -> SlideLayout:
    return SlideLayout.model_validate(
        {
            "id": layout_id,
            "description": f"Native editable {layout_id}",
            "components": [
                {
                    "id": "hero",
                    "description": "Editable hero title component",
                    "position": {"x": 0, "y": 0},
                    "elements": [
                        {
                            "type": "text",
                            "position": {"x": 1, "y": 1},
                            "size": {"width": 8, "height": 1},
                            "runs": [{"text": "Original"}],
                            "decorative": decorative,
                            "name": "title",
                            "min_length": 1,
                            "max_length": 80,
                        }
                    ],
                }
            ],
        }
    )


def _layouts_payload(*layouts: SlideLayout) -> dict:
    return {
        "layouts": [layout.model_dump(mode="json") for layout in layouts],
    }


def _revision_entry(*layouts: SlideLayout) -> SimpleNamespace:
    layout_source_parts = {
        layout.id: f"ppt/slides/slide{index}.xml"
        for index, layout in enumerate(layouts, start=1)
    }
    return SimpleNamespace(
        name="Native Template",
        description="Revision snapshot",
        merged_components=None,
        layouts=_layouts_payload(*layouts),
        assets={
            "native_pptx": {
                "schema_version": "presenton.template-v2-native-source/v1",
                "storage_key": "template-v2/native-template/source.pptx",
                "source_sha256": "a" * 64,
                "source_size_bytes": 2048,
                "layout_source_parts": layout_source_parts,
                "capabilities": {
                    "native_clone": True,
                    "text_patch": True,
                    "table_patch": True,
                    "master_layout_theme": True,
                    "relationships_media": True,
                },
                "preservation_mode": "native_clone",
                "publication_status": "publishable",
            }
        },
        is_default=False,
    )


def _reconstructed_revision_entry(*layouts: SlideLayout) -> SimpleNamespace:
    entry = _revision_entry(*layouts)
    entry.assets = {
        "native_pptx": {
            "schema_version": "presenton.template-v2-publication/v1",
            "preservation_mode": "reconstructed",
            "publication_status": "publishable",
            "publishable": True,
            "native_pptx_preflight": {
                "schema_version": "presenton.template-v2-native-pptx/v1",
                "compiler_name": "presenton-template-v2-native-ooxml",
                "compiler_version": "1",
                "preservation_mode": "reconstructed",
                "status": "compiled",
                "unsupported": None,
                "structural_sha256": f"sha256:{'b' * 64}",
                "package_sha256": f"sha256:{'c' * 64}",
            },
        }
    }
    return entry


def _target() -> TemplateV2GenerationTarget:
    layout = _layout()
    return TemplateV2GenerationTarget(
        template_id="native-template",
        revision=7,
        name="Native Template",
        snapshot_sha256="a" * 64,
        layouts=(
            TemplateV2GenerationLayout(
                layout=layout,
                content_schema={
                    "type": "object",
                    "properties": {
                        "hero": {
                            "type": "object",
                            "properties": {
                                "title": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": 80,
                                }
                            },
                            "required": ["title"],
                            "additionalProperties": False,
                        }
                    },
                    "required": ["hero"],
                    "additionalProperties": False,
                },
            ),
        ),
    )


def _image_target() -> TemplateV2GenerationTarget:
    layout = SlideLayout.model_validate(
        {
            "id": "image-slide",
            "description": "Native editable image slide",
            "components": [
                {
                    "id": "visual",
                    "description": "Editable image component",
                    "position": {"x": 0, "y": 0},
                    "elements": [
                        {
                            "type": "image",
                            "position": {"x": 1, "y": 1},
                            "size": {"width": 8, "height": 4},
                            "data": "/static/images/placeholder.jpg",
                            "decorative": False,
                            "name": "photo",
                            "is_icon": False,
                        }
                    ],
                }
            ],
        }
    )
    return TemplateV2GenerationTarget(
        template_id="image-template",
        revision=3,
        name="Image Template",
        snapshot_sha256="d" * 64,
        layouts=(
            TemplateV2GenerationLayout(
                layout=layout,
                content_schema={
                    "type": "object",
                    "properties": {
                        "visual": {
                            "type": "object",
                            "properties": {
                                "photo": {
                                    "type": "object",
                                    "properties": {
                                        "image_prompt": {"type": "string"},
                                        "image_url": {"type": "string"},
                                    },
                                    "required": ["image_prompt"],
                                    "additionalProperties": False,
                                }
                            },
                            "required": ["photo"],
                            "additionalProperties": False,
                        }
                    },
                    "required": ["visual"],
                    "additionalProperties": False,
                },
            ),
        ),
    )


def _unsupported_chart_target() -> TemplateV2GenerationTarget:
    layout = SlideLayout.model_validate(
        {
            "id": "chart-slide",
            "description": "Native compiler capability boundary",
            "components": [
                {
                    "id": "chart-panel",
                    "description": "Editable chart component",
                    "position": {"x": 0, "y": 0},
                    "elements": [
                        {
                            "type": "chart",
                            "position": {"x": 80, "y": 80},
                            "size": {"width": 640, "height": 360},
                            "chart_type": "bar",
                            "categories": ["A"],
                            "series": [{"name": "Series", "values": [1]}],
                            "decorative": False,
                            "name": "chart",
                        }
                    ],
                }
            ],
        }
    )
    return TemplateV2GenerationTarget(
        template_id="chart-template",
        revision=1,
        name="Chart Template",
        snapshot_sha256="c" * 64,
        layouts=(
            TemplateV2GenerationLayout(
                layout=layout,
                content_schema={"type": "object"},
            ),
        ),
    )


class RecordingSession:
    def __init__(self):
        self.added = []
        self.added_many = []
        self.commits = 0
        self.rollbacks = 0
        self.executed = []

    def add(self, value):
        self.added.append(value)

    def add_all(self, values):
        self.added_many.extend(values)

    async def get(self, model, key):
        for value in reversed([*self.added, *self.added_many]):
            if isinstance(value, model) and getattr(value, "id", None) == key:
                return value
        return None

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1

    async def execute(self, statement):
        self.executed.append(statement)

        class _Result:
            rowcount = 1

        return _Result()



def test_template_v2_is_no_longer_an_accepted_generation_strategy():
    """New generation from a Template V2 template has been retired.

    The studio, its PPTX import pipeline and export of decks that were
    already generated from a Template V2 template are unaffected.
    """

    with pytest.raises(ValidationError) as rejected:
        GeneratePresentationRequest(content="Topic", strategy="template_v2")

    assert rejected.value.errors()[0]["loc"] == ("strategy",)
    assert rejected.value.errors()[0]["type"] == "literal_error"


def test_target_loader_pins_revision_and_projects_only_fillable_layouts(monkeypatch):
    monkeypatch.setenv("ENABLE_TEMPLATE_V2", "true")
    monkeypatch.setenv(
        "TEMPLATE_V2_TEMPLATE_ALLOWLIST",
        "native-template,other-template",
    )
    record = SimpleNamespace(
        revision=7,
        name="Native Template",
        layouts=_layouts_payload(
            _layout("decorative", decorative=True),
            _layout("fillable"),
        ),
    )

    with patch(
        "services.template_v2_generation_service.TemplateV2Service.get",
        new=AsyncMock(return_value=record),
    ), patch(
        "services.template_v2_generation_service.get_revision",
        new=AsyncMock(
            return_value=_revision_entry(
                _layout("decorative", decorative=True),
                _layout("fillable"),
            )
        ),
    ):
        target = asyncio.run(
            load_template_v2_generation_target(
                RecordingSession(),
                template_id="native-template",
                revision=7,
            )
        )

    assert target.template_id == "native-template"
    assert target.revision == 7
    assert [item.layout.id for item in target.layouts] == ["fillable"]
    assert [slide.id for slide in target.as_pipeline_layout().slides] == ["fillable"]
    provenance = target.provenance(
        source_sha256="b" * 64,
        request_id="request-id",
        job_id="job-id",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    assert provenance["request_strategy"] == "template_v2"
    assert provenance["generation_strategy"] == "template-v2"
    assert provenance["template_v2_revision"] == 7
    assert provenance["template_snapshot_sha256"].startswith("sha256:")
    assert provenance["source_content_sha256"] == f"sha256:{'b' * 64}"
    assert provenance["request_id"] == "request-id"
    assert provenance["job_id"] == "job-id"
    assert provenance["vision"] is None


def test_target_loader_fails_closed_when_no_layout_is_fillable(monkeypatch):
    monkeypatch.setenv("ENABLE_TEMPLATE_V2", "true")
    monkeypatch.setenv("TEMPLATE_V2_TEMPLATE_ALLOWLIST", "native-template")
    record = SimpleNamespace(
        revision=7,
        name="Native Template",
        layouts=_layouts_payload(_layout("decorative", decorative=True)),
    )

    with patch(
        "services.template_v2_generation_service.TemplateV2Service.get",
        new=AsyncMock(return_value=record),
    ), patch(
        "services.template_v2_generation_service.get_revision",
        new=AsyncMock(
            return_value=_revision_entry(_layout("decorative", decorative=True))
        ),
    ):
        with pytest.raises(TemplateV2GenerationError) as exc:
            asyncio.run(
                load_template_v2_generation_target(
                    RecordingSession(),
                    template_id="native-template",
                    revision=7,
                )
            )

    assert exc.value.code == "template_v2_fillable_layout_required"


def test_target_loader_rejects_catalog_template_without_release_admission(monkeypatch):
    monkeypatch.setenv("ENABLE_TEMPLATE_V2", "true")
    monkeypatch.setenv(
        "TEMPLATE_V2_TEMPLATE_ALLOWLIST",
        "ppt-master-swiss-grid-systems",
    )
    record = SimpleNamespace(
        revision=7,
        name="Swiss",
        layouts=_layouts_payload(_layout()),
    )

    with patch(
        "services.template_v2_generation_service.TemplateV2Service.get",
        new=AsyncMock(return_value=record),
    ), patch(
        "services.template_v2_generation_service.get_revision",
        new=AsyncMock(return_value=_revision_entry(_layout())),
    ), patch(
        "services.template_v2_generation_service.is_template_v2_release_ready",
        return_value=False,
    ):
        with pytest.raises(TemplateV2GenerationError) as exc:
            asyncio.run(
                load_template_v2_generation_target(
                    RecordingSession(),
                    template_id="ppt-master-swiss-grid-systems",
                    revision=7,
                )
            )

    assert exc.value.code == "template_v2_template_not_published"


def test_target_loader_preserves_private_non_catalog_generation(monkeypatch):
    monkeypatch.setenv("ENABLE_TEMPLATE_V2", "true")
    monkeypatch.setenv("TEMPLATE_V2_TEMPLATE_ALLOWLIST", "native-template")
    record = SimpleNamespace(
        revision=7,
        name="Private Template",
        layouts=_layouts_payload(_layout()),
    )
    private_revision = _revision_entry(_layout())
    private_revision.assets = None

    with patch(
        "services.template_v2_generation_service.TemplateV2Service.get",
        new=AsyncMock(return_value=record),
    ), patch(
        "services.template_v2_generation_service.get_revision",
        new=AsyncMock(return_value=private_revision),
    ), patch(
        "services.template_v2_generation_service.is_template_v2_release_ready",
        side_effect=AssertionError("private templates must not use the catalog gate"),
    ):
        target = asyncio.run(
            load_template_v2_generation_target(
                RecordingSession(),
                template_id="native-template",
                revision=7,
            )
        )

    assert target.template_id == "native-template"
    assert target.revision == 7
    assert target.preservation_mode == "reconstructed"


def test_target_loader_accepts_published_reconstructed_metadata(monkeypatch):
    monkeypatch.setenv("ENABLE_TEMPLATE_V2", "true")
    monkeypatch.setenv("TEMPLATE_V2_TEMPLATE_ALLOWLIST", "native-template")
    record = SimpleNamespace(
        revision=7,
        name="Reconstructed Template",
        layouts=_layouts_payload(_layout()),
    )

    with patch(
        "services.template_v2_generation_service.TemplateV2Service.get",
        new=AsyncMock(return_value=record),
    ), patch(
        "services.template_v2_generation_service.get_revision",
        new=AsyncMock(return_value=_reconstructed_revision_entry(_layout())),
    ):
        target = asyncio.run(
            load_template_v2_generation_target(
                RecordingSession(),
                template_id="native-template",
                revision=7,
            )
        )

    assert target.preservation_mode == "reconstructed"
    assert target.native_source is None


def test_target_loader_rejects_invalid_reconstructed_metadata(monkeypatch):
    monkeypatch.setenv("ENABLE_TEMPLATE_V2", "true")
    monkeypatch.setenv("TEMPLATE_V2_TEMPLATE_ALLOWLIST", "native-template")
    record = SimpleNamespace(
        revision=7,
        name="Reconstructed Template",
        layouts=_layouts_payload(_layout()),
    )
    revision = _reconstructed_revision_entry(_layout())
    revision.assets["native_pptx"]["native_pptx_preflight"]["status"] = (
        "unsupported"
    )

    with patch(
        "services.template_v2_generation_service.TemplateV2Service.get",
        new=AsyncMock(return_value=record),
    ), patch(
        "services.template_v2_generation_service.get_revision",
        new=AsyncMock(return_value=revision),
    ):
        with pytest.raises(TemplateV2GenerationError) as exc:
            asyncio.run(
                load_template_v2_generation_target(
                    RecordingSession(),
                    template_id="native-template",
                    revision=7,
                )
            )

    assert exc.value.code == "template_v2_native_source_invalid"


def test_target_loader_rejects_a_non_current_revision(monkeypatch):
    monkeypatch.setenv("ENABLE_TEMPLATE_V2", "true")
    monkeypatch.setenv("TEMPLATE_V2_TEMPLATE_ALLOWLIST", "native-template")
    record = SimpleNamespace(
        revision=8,
        name="Native Template",
        layouts=_layouts_payload(_layout()),
    )

    with patch(
        "services.template_v2_generation_service.TemplateV2Service.get",
        new=AsyncMock(return_value=record),
    ):
        with pytest.raises(TemplateV2GenerationError) as exc:
            asyncio.run(
                load_template_v2_generation_target(
                    RecordingSession(),
                    template_id="native-template",
                    revision=7,
                )
            )

    assert exc.value.code == "template_v2_revision_conflict"


def test_generated_content_is_json_schema_validated_before_slide_persistence():
    outlines = PresentationOutlineModel(
        slides=[SlideOutlineModel(content="A title")]
    )
    structure = PresentationStructureModel(slides=[0])

    with patch(
        "services.template_v2_generation_service."
        "get_slide_content_from_type_and_outline",
        new=AsyncMock(return_value={"hero": {"title": ""}}),
    ):
        with pytest.raises(TemplateV2GenerationError) as exc:
            asyncio.run(
                build_template_v2_slides(
                    target=_target(),
                    presentation_id=uuid.uuid4(),
                    outlines=outlines,
                    structure=structure,
                    language="English",
                    tone="professional",
                    verbosity="standard",
                    instructions=None,
                )
            )

    assert exc.value.code == "template_v2_generation_invalid"


def test_template_v2_slide_builder_batches_concurrently_and_preserves_order():
    outlines = PresentationOutlineModel(
        slides=[
            SlideOutlineModel(content=str(index)) for index in range(12)
        ]
    )
    structure = PresentationStructureModel(slides=[0] * 12)
    updates: list[tuple[int, int]] = []
    active = 0
    peak_active = 0
    first_batch_started = asyncio.Event()
    second_batch_started = asyncio.Event()
    started_by_batch = [0, 0]

    async def record_progress(completed: int, total: int) -> None:
        updates.append((completed, total))

    async def generate_content(_layout, outline, *_args):
        nonlocal active, peak_active
        index = int(outline.content)
        batch_index = 0 if index < 10 else 1
        batch_size = 10 if batch_index == 0 else 2
        active += 1
        peak_active = max(peak_active, active)
        started_by_batch[batch_index] += 1
        gate = first_batch_started if batch_index == 0 else second_batch_started
        if started_by_batch[batch_index] == batch_size:
            gate.set()
        try:
            await gate.wait()
            await asyncio.sleep((batch_size - (index % 10)) / 1000)
            return {"hero": {"title": f"Generated {index}"}}
        finally:
            active -= 1

    with patch(
        "services.template_v2_generation_service."
        "get_slide_content_from_type_and_outline",
        new=AsyncMock(side_effect=generate_content),
    ):
        slides = asyncio.run(
            build_template_v2_slides(
                target=_target(),
                presentation_id=uuid.uuid4(),
                outlines=outlines,
                structure=structure,
                language="English",
                tone="professional",
                verbosity="standard",
                instructions=None,
                on_progress=record_progress,
            )
        )

    assert peak_active == 10
    assert [slide.index for slide in slides] == list(range(12))
    assert [slide.content["hero"]["title"] for slide in slides] == [
        f"Generated {index}" for index in range(12)
    ]
    assert updates == [(10, 12), (12, 12)]


def test_template_v2_slide_builder_pipelines_assets_with_next_content_batch():
    outlines = PresentationOutlineModel(
        slides=[SlideOutlineModel(content=str(index)) for index in range(11)]
    )
    structure = PresentationStructureModel(slides=[0] * 11)
    first_batch_assets_started = asyncio.Event()
    second_batch_content_started = asyncio.Event()
    first_batch_assets_finished = asyncio.Event()
    started_assets = 0
    finished_assets = 0

    async def generate_content(_layout, outline, *_args):
        if outline.content == "10":
            second_batch_content_started.set()
            await first_batch_assets_started.wait()
        return {"hero": {"title": f"Generated {outline.content}"}}

    async def process_assets(slide: SlideModel) -> None:
        nonlocal started_assets, finished_assets
        if slide.index == 10:
            return
        started_assets += 1
        if started_assets == 10:
            first_batch_assets_started.set()
        await second_batch_content_started.wait()
        finished_assets += 1
        if finished_assets == 10:
            first_batch_assets_finished.set()

    with patch(
        "services.template_v2_generation_service."
        "get_slide_content_from_type_and_outline",
        new=AsyncMock(side_effect=generate_content),
    ):
        slides = asyncio.run(
            build_template_v2_slides(
                target=_target(),
                presentation_id=uuid.uuid4(),
                outlines=outlines,
                structure=structure,
                language="English",
                tone="professional",
                verbosity="standard",
                instructions=None,
                process_assets=process_assets,
            )
        )

    assert len(slides) == 11
    assert first_batch_assets_started.is_set()
    assert second_batch_content_started.is_set()
    assert first_batch_assets_finished.is_set()


def test_template_v2_slide_builder_rehydrates_native_ui_after_asset_resolution():
    async def process_assets(slide: SlideModel) -> None:
        slide.content["visual"]["photo"]["image_url"] = (
            "https://cdn.example/generated.jpg"
        )

    with patch(
        "services.template_v2_generation_service."
        "get_slide_content_from_type_and_outline",
        new=AsyncMock(
            return_value={
                "visual": {"photo": {"image_prompt": "quiet mountain lake"}}
            }
        ),
    ):
        slides = asyncio.run(
            build_template_v2_slides(
                target=_image_target(),
                presentation_id=uuid.uuid4(),
                outlines=PresentationOutlineModel(
                    slides=[SlideOutlineModel(content="Image slide")]
                ),
                structure=PresentationStructureModel(slides=[0]),
                language="English",
                tone="professional",
                verbosity="standard",
                instructions=None,
                process_assets=process_assets,
            )
        )

    assert slides[0].content["visual"]["photo"] == {
        "image_prompt": "quiet mountain lake",
        "image_url": "https://cdn.example/generated.jpg",
    }
    assert slides[0].ui["components"][0]["elements"][0]["data"] == (
        "https://cdn.example/generated.jpg"
    )


def test_template_v2_asset_urls_are_not_exposed_to_the_content_model():
    schema = _prepare_response_schema(
        SlideLayoutModel(
            id="image-slide",
            json_schema=_image_target().layouts[0].content_schema,
        )
    )

    image_properties = schema["properties"]["visual"]["properties"]["photo"][
        "properties"
    ]
    assert "image_prompt" in image_properties
    assert "image_url" not in image_properties


def test_template_v2_slide_builder_cancels_content_and_asset_tasks_on_failure():
    outlines = PresentationOutlineModel(
        slides=[SlideOutlineModel(content=str(index)) for index in range(12)]
    )
    structure = PresentationStructureModel(slides=[0] * 12)
    sibling_content_started = asyncio.Event()
    first_batch_assets_started = asyncio.Event()
    content_cancelled = asyncio.Event()
    started_assets = 0
    cancelled_assets = 0
    updates: list[tuple[int, int]] = []

    async def generate_content(_layout, outline, *_args):
        index = int(outline.content)
        if index < 10:
            return {"hero": {"title": f"Generated {index}"}}
        if index == 10:
            sibling_content_started.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                content_cancelled.set()
                raise
        await sibling_content_started.wait()
        await first_batch_assets_started.wait()
        raise RuntimeError("batch exploded")

    async def process_assets(_slide: SlideModel) -> None:
        nonlocal started_assets, cancelled_assets
        started_assets += 1
        if started_assets == 10:
            first_batch_assets_started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled_assets += 1
            raise

    async def record_progress(completed: int, total: int) -> None:
        updates.append((completed, total))

    with patch(
        "services.template_v2_generation_service."
        "get_slide_content_from_type_and_outline",
        new=AsyncMock(side_effect=generate_content),
    ):
        with pytest.raises(RuntimeError, match="batch exploded"):
            asyncio.run(
                build_template_v2_slides(
                    target=_target(),
                    presentation_id=uuid.uuid4(),
                    outlines=outlines,
                    structure=structure,
                    language="English",
                    tone="professional",
                    verbosity="standard",
                    instructions=None,
                    on_progress=record_progress,
                    process_assets=process_assets,
                )
            )

    assert updates == [(10, 12)]
    assert content_cancelled.is_set()
    assert cancelled_assets == 10


def test_template_v2_slide_builder_drains_content_tasks_on_outer_cancellation():
    outlines = PresentationOutlineModel(
        slides=[SlideOutlineModel(content=str(index)) for index in range(10)]
    )
    structure = PresentationStructureModel(slides=[0] * 10)
    all_content_started = asyncio.Event()
    started_content = 0
    cancelled_content = 0

    async def generate_content(_layout, _outline, *_args):
        nonlocal started_content, cancelled_content
        started_content += 1
        if started_content == 10:
            all_content_started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled_content += 1
            raise

    async def run_and_cancel() -> None:
        task = asyncio.create_task(
            build_template_v2_slides(
                target=_target(),
                presentation_id=uuid.uuid4(),
                outlines=outlines,
                structure=structure,
                language="English",
                tone="professional",
                verbosity="standard",
                instructions=None,
            )
        )
        await all_content_started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    with patch(
        "services.template_v2_generation_service."
        "get_slide_content_from_type_and_outline",
        new=AsyncMock(side_effect=generate_content),
    ):
        asyncio.run(run_and_cancel())

    assert cancelled_content == 10


def test_native_pptx_preflight_records_explicit_unsupported_capability():
    target = _unsupported_chart_target()
    preflight = preflight_template_v2_native_pptx(
        target=target,
        slides=[
            SlideModel(
                presentation=uuid.uuid4(),
                layout_group="native",
                layout="chart-slide",
                index=0,
                content={
                    "chart-panel": {
                        "chart": {
                            "chart_type": "bar",
                            "categories": ["A"],
                            "series": [{"name": "Series", "values": [1]}],
                        }
                    }
                },
            )
        ],
    )

    provenance = target.provenance(
        source_sha256="b" * 64,
        request_id="request-id",
        job_id="job-id",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        native_pptx_preflight=preflight,
    )
    capability = provenance["native_pptx_preflight"]
    assert capability["schema_version"] == "presenton.template-v2-native-pptx/v1"
    assert capability["compiler_name"] == "presenton-template-v2-native-ooxml"
    assert capability["compiler_version"] == "1"
    assert capability["status"] == "unsupported"
    assert capability["selected_for_export"] is False
    assert capability["structural_sha256"] is None
    assert capability["package_sha256"] is None
    assert capability["unsupported"]["code"] == (
        "template_v2_native_pptx_element_unsupported"
    )
    assert capability["unsupported"]["path"].endswith(".type=chart")
