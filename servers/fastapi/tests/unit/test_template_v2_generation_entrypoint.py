import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
import uuid
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
import pytest

from api.v1.ppt.endpoints.presentation_generate import (
    check_if_api_request_is_valid,
    generate_presentation_handler,
)
from models.generate_presentation_request import GeneratePresentationRequest
from models.presentation_and_path import PresentationAndPath
from models.presentation_outline_model import (
    PresentationOutlineModel,
    SlideOutlineModel,
)
from models.presentation_structure_model import PresentationStructureModel
from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from services.template_v2_generation_service import (
    TemplateV2GenerationError,
    TemplateV2GenerationLayout,
    TemplateV2GenerationTarget,
    build_template_v2_slides,
    load_template_v2_generation_target,
    preflight_template_v2_native_pptx,
)
from templates.v2.constants import TEMPLATE_V2_VERSION
from templates.v2.models.layouts import SlideLayout


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
    return SimpleNamespace(
        name="Native Template",
        description="Revision snapshot",
        merged_components=None,
        layouts=_layouts_payload(*layouts),
        assets=None,
        is_default=False,
    )


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

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1

    async def execute(self, statement):
        self.executed.append(statement)


def test_template_v2_generation_is_default_off(monkeypatch):
    monkeypatch.delenv("ENABLE_TEMPLATE_V2", raising=False)
    monkeypatch.delenv("TEMPLATE_V2_TEMPLATE_ALLOWLIST", raising=False)
    request = GeneratePresentationRequest(
        content="Topic",
        strategy="template_v2",
        template_v2_id="native-template",
        template_v2_revision=7,
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(check_if_api_request_is_valid(request, RecordingSession()))

    assert exc.value.status_code == 403
    assert exc.value.detail == "template_v2_creation_disabled"


def test_template_v2_requires_explicit_target_fields():
    request = GeneratePresentationRequest(content="Topic", strategy="template_v2")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(check_if_api_request_is_valid(request, RecordingSession()))

    assert exc.value.status_code == 400
    assert exc.value.detail == "template_v2_id_required"


def test_template_v2_admission_stores_the_exact_snapshot(monkeypatch):
    monkeypatch.setenv("ENABLE_TEMPLATE_V2", "true")
    monkeypatch.setenv("TEMPLATE_V2_TEMPLATE_ALLOWLIST", "native-template")
    request = GeneratePresentationRequest(
        content="Topic",
        strategy="template_v2",
        template_v2_id="native-template",
        template_v2_revision=7,
    )
    admitted = _target()
    loader = AsyncMock(return_value=admitted)

    with patch(
        "api.v1.ppt.endpoints.presentation_generate."
        "load_template_v2_generation_target",
        new=loader,
    ):
        asyncio.run(check_if_api_request_is_valid(request, RecordingSession()))

    loader.assert_awaited_once()
    assert request._template_v2_generation_target is admitted


def test_template_v2_fields_are_rejected_without_discriminator():
    request = GeneratePresentationRequest(
        content="Topic",
        template_v2_id="native-template",
        template_v2_revision=7,
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(check_if_api_request_is_valid(request, RecordingSession()))

    assert exc.value.status_code == 400
    assert exc.value.detail == "generation_strategy_conflict"


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


def test_handler_persists_v2_identity_provenance_and_native_ui():
    presentation_id = uuid.uuid4()
    request = GeneratePresentationRequest(
        content="Topic",
        slides_markdown=["A title"],
        strategy="template_v2",
        template_v2_id="native-template",
        template_v2_revision=7,
    )
    outlines = PresentationOutlineModel(
        slides=[SlideOutlineModel(content="A title")]
    )
    session = RecordingSession()
    request._template_v2_generation_target = _target()
    observations: list[dict[str, object]] = []

    with (
        patch(
            "api.v1.ppt.endpoints.presentation_generate."
            "log_template_v2_generation_observation",
            new=lambda **event: observations.append(event),
        ),
        patch(
            "api.v1.ppt.endpoints.presentation_generate."
            "MEM0_PRESENTATION_MEMORY_SERVICE.store_generation_context",
            new=AsyncMock(),
        ),
        patch(
            "api.v1.ppt.endpoints.presentation_generate."
            "MEM0_PRESENTATION_MEMORY_SERVICE.store_generated_outlines",
            new=AsyncMock(),
        ),
        patch(
            "api.v1.ppt.endpoints.presentation_generate.build_template_structure",
            new=AsyncMock(
                return_value=(outlines, PresentationStructureModel(slides=[0]))
            ),
        ),
        patch(
            "services.template_v2_generation_service."
            "get_slide_content_from_type_and_outline",
            new=AsyncMock(return_value={"hero": {"title": "Generated title"}}),
        ),
        patch(
            "api.v1.ppt.endpoints.presentation_generate.export_presentation",
            new=AsyncMock(
                return_value=PresentationAndPath(
                    presentation_id=presentation_id,
                    path="/tmp/native-template.pptx",
                )
            ),
        ),
    ):
        response = asyncio.run(
            generate_presentation_handler(
                request,
                presentation_id,
                async_status=None,
                sql_session=session,
            )
        )

    presentation = next(
        value for value in session.added if isinstance(value, PresentationModel)
    )
    slide = next(value for value in session.added_many if isinstance(value, SlideModel))
    assert response.presentation_id == presentation_id
    assert presentation.version == TEMPLATE_V2_VERSION
    assert presentation.mode == "template"
    assert presentation.lifecycle_status == "published"
    assert presentation.layout is None
    assert presentation.structure is None
    assert presentation.theme["template_v2"]["request_strategy"] == "template_v2"
    assert presentation.theme["template_v2"]["generation_strategy"] == "template-v2"
    assert presentation.theme["template_v2"]["template_v2_id"] == "native-template"
    assert presentation.theme["template_v2"]["template_v2_revision"] == 7
    assert presentation.theme["template_v2"]["request_id"] == str(presentation_id)
    assert presentation.theme["template_v2"]["job_id"] == f"sync:{presentation_id}"
    assert presentation.theme["template_v2"]["compiler_version"] == "1"
    native_preflight = presentation.theme["template_v2"][
        "native_pptx_preflight"
    ]
    assert native_preflight["schema_version"] == (
        "presenton.template-v2-native-pptx/v1"
    )
    assert native_preflight["compiler_name"] == (
        "presenton-template-v2-native-ooxml"
    )
    assert native_preflight["compiler_version"] == "1"
    assert native_preflight["status"] == "compiled"
    assert native_preflight["selected_for_export"] is False
    assert native_preflight["structural_sha256"].startswith("sha256:")
    assert native_preflight["package_sha256"].startswith("sha256:")
    assert native_preflight["unsupported"] is None
    assert presentation.theme["template_v2"]["source_content_sha256"].startswith(
        "sha256:"
    )
    assert "Topic" not in str(presentation.theme["template_v2"])
    assert slide.layout_group == "native"
    assert slide.ui["id"] == "title-slide"
    assert slide.content == {"hero": {"title": "Generated title"}}
    assert [event["operation"] for event in observations] == [
        "generate",
        "export",
    ]
    assert all(event["outcome"] == "success" for event in observations)
    assert all(
        isinstance(event["duration_ms"], float) and event["duration_ms"] >= 0
        for event in observations
    )
    assert observations[1]["export_type"] == "pptx"
    assert all("code" not in event for event in observations)


def test_async_export_failure_removes_partial_deck_and_rolls_back_session():
    presentation_id = uuid.uuid4()
    request = GeneratePresentationRequest(
        content="Topic",
        slides_markdown=["A title"],
        strategy="template_v2",
        template_v2_id="native-template",
        template_v2_revision=7,
    )
    request._template_v2_generation_target = _target()
    outlines = PresentationOutlineModel(
        slides=[SlideOutlineModel(content="A title")]
    )
    async_status = AsyncPresentationGenerationTaskModel(
        status="pending",
        message="Queued for generation",
    )
    session = RecordingSession()
    observations: list[dict[str, object]] = []

    with (
        patch(
            "api.v1.ppt.endpoints.presentation_generate."
            "log_template_v2_generation_observation",
            new=lambda **event: observations.append(event),
        ),
        patch(
            "api.v1.ppt.endpoints.presentation_generate."
            "MEM0_PRESENTATION_MEMORY_SERVICE.store_generation_context",
            new=AsyncMock(),
        ),
        patch(
            "api.v1.ppt.endpoints.presentation_generate."
            "MEM0_PRESENTATION_MEMORY_SERVICE.store_generated_outlines",
            new=AsyncMock(),
        ),
        patch(
            "api.v1.ppt.endpoints.presentation_generate.build_template_structure",
            new=AsyncMock(
                return_value=(outlines, PresentationStructureModel(slides=[0]))
            ),
        ),
        patch(
            "services.template_v2_generation_service."
            "get_slide_content_from_type_and_outline",
            new=AsyncMock(return_value={"hero": {"title": "Generated title"}}),
        ),
        patch(
            "api.v1.ppt.endpoints.presentation_generate.export_presentation",
            new=AsyncMock(side_effect=RuntimeError("export failed")),
        ),
    ):
        result = asyncio.run(
            generate_presentation_handler(
                request,
                presentation_id,
                async_status=async_status,
                sql_session=session,
            )
        )

    assert result is None
    assert async_status.status == "error"
    assert async_status.error["detail"] == "Presentation generation failed"
    assert session.rollbacks >= 2
    assert len(session.executed) == 2
    assert "slides" in str(session.executed[0]).lower()
    assert "presentations" in str(session.executed[1]).lower()
    assert [event["operation"] for event in observations] == [
        "generate",
        "export",
    ]
    assert observations[0]["outcome"] == "success"
    assert observations[1]["outcome"] == "failure"
    assert observations[1]["export_type"] == "pptx"
    assert observations[1]["code"] == "template_v2_export_failed"
    assert all(
        isinstance(event["duration_ms"], float) and event["duration_ms"] >= 0
        for event in observations
    )
