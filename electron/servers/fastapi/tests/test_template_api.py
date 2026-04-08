import asyncio
import base64
import re
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.sql import Delete, Select

from api.v1.ppt.router import API_V1_PPT_ROUTER
from enums.llm_provider import LLMProvider
from models.sql.presentation_layout_code import PresentationLayoutCodeModel
from models.sql.template import TemplateModel
from models.sql.template_create_info import TemplateCreateInfoModel
from templates.handler import (
    CloneSlideLayoutRequest,
    CreateSlideLayoutRequest,
    EditSlideLayoutRequest,
    EditSlideLayoutSectionRequest,
    SaveSlideLayoutRequest,
    SaveTemplateLayoutData,
    SaveTemplateRequest,
    UpdateTemplateRequest,
    clone_slide_layout,
    create_slide_layout,
    edit_slide_layout,
    edit_slide_layout_section,
    init_create_template,
    save_slide_layout,
    save_template,
    update_template,
    upload_fonts_and_slides_preview,
)
from templates.pptx_html_stub import BASIC_TEMPLATE_HTML
from templates.preview import (
    FontCheckResponse,
    FontsUploadAndSlidesPreviewResponse,
    check_fonts_in_pptx_handler,
)
from templates.providers import (
    ANTHROPIC_TEMPLATE_MODEL,
    CODEX_TEMPLATE_MODEL,
    GOOGLE_TEMPLATE_MODEL,
    OPENAI_TEMPLATE_MODEL,
    get_template_provider_spec,
)


PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9s5WzxQAAAAASUVORK5CYII="
)


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path / "app_data"))

    app = FastAPI()
    app.include_router(API_V1_PPT_ROUTER)
    client = TestClient(app)
    yield client, tmp_path
    client.close()


class FakeScalarResult:
    def __init__(self, items):
        self._items = list(items)

    def all(self):
        return list(self._items)


class FakeExecuteResult:
    def __init__(self, items):
        self._items = list(items)

    def scalars(self):
        return FakeScalarResult(self._items)

    def all(self):
        return list(self._items)


class FakeAsyncSession:
    def __init__(self):
        self.template_infos: dict = {}
        self.templates: dict = {}
        self.layouts: list[PresentationLayoutCodeModel] = []
        self._next_layout_row_id = 1

    async def get(self, model, key):
        if model is TemplateCreateInfoModel:
            return self.template_infos.get(key)
        if model is TemplateModel:
            return self.templates.get(key)
        return None

    def add(self, obj):
        if isinstance(obj, TemplateCreateInfoModel):
            if getattr(obj, "created_at", None) is None:
                obj.created_at = datetime.now(timezone.utc)
            self.template_infos[obj.id] = obj
        elif isinstance(obj, TemplateModel):
            if getattr(obj, "created_at", None) is None:
                obj.created_at = datetime.now(timezone.utc)
            self.templates[obj.id] = obj
        elif isinstance(obj, PresentationLayoutCodeModel):
            if obj.id is None:
                obj.id = self._next_layout_row_id
                self._next_layout_row_id += 1
            if getattr(obj, "created_at", None) is None:
                obj.created_at = datetime.now(timezone.utc)
            if getattr(obj, "updated_at", None) is None:
                obj.updated_at = datetime.now(timezone.utc)
            self.layouts.append(obj)

    def add_all(self, objects):
        for obj in objects:
            self.add(obj)

    async def commit(self):
        return None

    async def refresh(self, _obj):
        return None

    async def scalar(self, statement):
        items = self._execute_select(statement)
        return items[0] if items else None

    async def execute(self, statement):
        if isinstance(statement, Delete):
            self._execute_delete(statement)
            return FakeExecuteResult([])
        return FakeExecuteResult(self._execute_select(statement))

    def _execute_select(self, statement):
        if not isinstance(statement, Select):
            return []
        entity = statement.column_descriptions[0].get("entity")
        if entity is not PresentationLayoutCodeModel:
            return []
        return [
            layout
            for layout in self.layouts
            if all(self._matches_clause(layout, clause) for clause in statement._where_criteria)
        ]

    def _execute_delete(self, statement):
        if statement.table.name != PresentationLayoutCodeModel.__tablename__:
            return
        self.layouts = [
            layout
            for layout in self.layouts
            if not all(self._matches_clause(layout, clause) for clause in statement._where_criteria)
        ]

    def _matches_clause(self, obj, clause):
        if hasattr(clause, "clauses"):
            return all(self._matches_clause(obj, child) for child in clause.clauses)
        left = getattr(clause.left, "key", None) or getattr(clause.left, "name", None)
        right = getattr(clause.right, "value", None)
        return getattr(obj, left) == right


class SimpleUploadFile:
    def __init__(self, filename: str, content_type: str, data: bytes):
        self.filename = filename
        self.content_type = content_type
        self._data = data

    async def read(self):
        return self._data


def test_router_registration_replaces_old_routes(api_client):
    client, _ = api_client
    paths = {
        route.path
        for route in client.app.routes
        if hasattr(route, "path") and route.path.startswith("/api/v1/ppt")
    }

    assert "/api/v1/ppt/template/all" in paths
    assert "/api/v1/ppt/template/create/init" in paths
    assert "/api/v1/ppt/template/slide-layout/create" in paths
    assert "/api/v1/ppt/template/fonts-upload-and-slides-preview" in paths
    assert "/api/v1/ppt/fonts/check" in paths

    assert "/api/v1/ppt/fonts/upload" in paths
    assert "/api/v1/ppt/fonts/list" in paths
    assert "/api/v1/ppt/fonts/uploaded" in paths

    assert "/api/v1/ppt/slide-to-html/" not in paths
    assert "/api/v1/ppt/html-to-react/" not in paths
    assert "/api/v1/ppt/html-edit/" not in paths
    assert "/api/v1/ppt/pptx-slides/process" not in paths
    assert "/api/v1/ppt/pdf-slides/process" not in paths
    assert "/api/v1/ppt/pptx-fonts/process" not in paths


def test_template_create_init_stores_stub_htmls():
    session = FakeAsyncSession()

    template_info_id = asyncio.run(
        init_create_template(
            request=type(
                "Request",
                (),
                {
                    "pptx_url": "/app_data/uploads/template-previews/test/presentation.pptx",
                    "slide_image_urls": [
                        "/app_data/images/a/slide_1.png",
                        "/app_data/images/a/slide_2.png",
                    ],
                    "fonts": {
                        "Inter": "https://fonts.googleapis.com/css2?family=Inter&display=swap"
                    },
                },
            )(),
            sql_session=session,
        )
    )

    template_info = session.template_infos[template_info_id]
    assert template_info.slide_htmls == [BASIC_TEMPLATE_HTML, BASIC_TEMPLATE_HTML]
    assert template_info.slide_image_urls == [
        "/app_data/images/a/slide_1.png",
        "/app_data/images/a/slide_2.png",
    ]


def test_fonts_check_endpoint(monkeypatch):
    async def fake_font_check(_pptx_path: str, _temp_dir: str):
        return [("Inter", "https://fonts.googleapis.com/css2?family=Inter&display=swap")], [("Custom Font", None)]

    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(
        "templates.preview.get_available_and_unavailable_fonts_for_pptx",
        fake_font_check,
    )
    monkeypatch.setattr("templates.preview.asyncio.to_thread", fake_to_thread)

    upload = SimpleUploadFile(
        filename="deck.pptx",
        content_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        data=b"fake-pptx",
    )
    response = asyncio.run(check_fonts_in_pptx_handler(pptx_file=upload))

    assert response == FontCheckResponse(
        available_fonts=[
            {
                "name": "Inter",
                "url": "https://fonts.googleapis.com/css2?family=Inter&display=swap",
            }
        ],
        unavailable_fonts=[{"name": "Custom Font", "url": None}],
    )


def test_fonts_upload_and_preview_route_uses_new_handler(monkeypatch):
    async def fake_preview_handler(**kwargs):
        assert kwargs["pptx_file"].filename == "deck.pptx"
        return FontsUploadAndSlidesPreviewResponse(
            slide_image_urls=["/app_data/images/1/slide_1.png"],
            pptx_url="/app_data/uploads/template-previews/1/presentation.pptx",
            modified_pptx_url="/app_data/uploads/template-previews/1/presentation.pptx",
            fonts={"Inter": "https://fonts.googleapis.com/css2?family=Inter&display=swap"},
        )

    monkeypatch.setattr(
        "templates.handler.upload_fonts_and_slides_preview_handler",
        fake_preview_handler,
    )

    upload = SimpleUploadFile(
        filename="deck.pptx",
        content_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        data=b"fake-pptx",
    )
    response = asyncio.run(upload_fonts_and_slides_preview(pptx_file=upload))

    assert response == FontsUploadAndSlidesPreviewResponse(
        slide_image_urls=["/app_data/images/1/slide_1.png"],
        pptx_url="/app_data/uploads/template-previews/1/presentation.pptx",
        modified_pptx_url="/app_data/uploads/template-previews/1/presentation.pptx",
        fonts={"Inter": "https://fonts.googleapis.com/css2?family=Inter&display=swap"},
    )


def test_provider_spec_mapping_and_restrictions(monkeypatch):
    monkeypatch.setattr("templates.providers.get_llm_provider", lambda: LLMProvider.OPENAI)
    spec = get_template_provider_spec()
    assert spec.provider == LLMProvider.OPENAI
    assert spec.model == OPENAI_TEMPLATE_MODEL

    monkeypatch.setattr("templates.providers.get_llm_provider", lambda: LLMProvider.CODEX)
    spec = get_template_provider_spec()
    assert spec.provider == LLMProvider.CODEX
    assert spec.model == CODEX_TEMPLATE_MODEL

    monkeypatch.setattr("templates.providers.get_llm_provider", lambda: LLMProvider.GOOGLE)
    spec = get_template_provider_spec()
    assert spec.provider == LLMProvider.GOOGLE
    assert spec.model == GOOGLE_TEMPLATE_MODEL

    monkeypatch.setattr("templates.providers.get_llm_provider", lambda: LLMProvider.ANTHROPIC)
    spec = get_template_provider_spec()
    assert spec.provider == LLMProvider.ANTHROPIC
    assert spec.model == ANTHROPIC_TEMPLATE_MODEL

    monkeypatch.setattr("templates.providers.get_llm_provider", lambda: LLMProvider.OLLAMA)
    with pytest.raises(Exception) as exc:
        get_template_provider_spec()
    assert "Template generation only supports OpenAI, Codex, Google, or Anthropic." in str(exc.value)


def test_create_and_edit_slide_layout_routes_use_provider_layer(tmp_path, monkeypatch):
    session = FakeAsyncSession()
    image_path = tmp_path / "slide.png"
    image_path.write_bytes(PNG_BYTES)

    template_info = TemplateCreateInfoModel(
        fonts={"Inter": "https://fonts.googleapis.com/css2?family=Inter&display=swap"},
        pptx_url="/app_data/uploads/template-previews/seed/presentation.pptx",
        slide_htmls=["<div>seed html</div>"],
        slide_image_urls=[str(image_path)],
    )
    session.add(template_info)

    create_calls = []
    edit_calls = []

    async def fake_generate_layout(**kwargs):
        create_calls.append(kwargs)
        return """```tsx
import { z } from "zod";
const layoutId = "title-image";
const layoutName = "Title Image";
const layoutDescription = "desc";
function dynamicSlideLayout() { return <div>{image_url}{icon_url}{image_prompt}{icon_query}</div>; }
export { layoutId };
```"""

    async def fake_edit_layout(**kwargs):
        edit_calls.append(kwargs)
        return "```tsx\nconst updatedLayout = true;\n```"

    monkeypatch.setattr("templates.handler.generate_slide_layout_code", fake_generate_layout)
    monkeypatch.setattr("templates.handler.edit_slide_layout_code", fake_edit_layout)

    create_response = asyncio.run(
        create_slide_layout(
            request=CreateSlideLayoutRequest(id=template_info.id, index=0),
            sql_session=session,
        )
    )
    assert "__image_url__" in create_response.react_component
    assert "__icon_url__" in create_response.react_component
    assert "__image_prompt__" in create_response.react_component
    assert "__icon_query__" in create_response.react_component
    assert "import " not in create_response.react_component
    assert "export " not in create_response.react_component
    assert re.search(r'layoutId\s*=\s*"title-image-\d{4}"', create_response.react_component)

    assert create_calls
    assert create_calls[0]["system_prompt"]
    assert "#SLIDE HTML REFERENCE" in create_calls[0]["user_text"]
    assert create_calls[0]["media_type"] == "image/png"
    assert create_calls[0]["image_bytes"] == PNG_BYTES

    edit_response = asyncio.run(
        edit_slide_layout(
            request=EditSlideLayoutRequest(
                react_component="const x = 1;",
                prompt="Move title up",
            )
        )
    )
    assert edit_response.react_component == "const updatedLayout = true;"

    section_response = asyncio.run(
        edit_slide_layout_section(
            request=EditSlideLayoutSectionRequest(
                react_component="const x = 1;",
                section="header",
                prompt="Change spacing",
            )
        )
    )
    assert section_response.react_component == "const updatedLayout = true;"

    assert len(edit_calls) == 2
    assert "#Prompt\nMove title up" in edit_calls[0]["user_text"]
    assert "#Section to make changes around\nheader" in edit_calls[1]["user_text"]


def test_save_update_and_clone_template_flow():
    session = FakeAsyncSession()
    template_info = TemplateCreateInfoModel(
        fonts={"Inter": "https://fonts.googleapis.com/css2?family=Inter&display=swap"},
        pptx_url="/app_data/uploads/template-previews/seed/presentation.pptx",
        slide_htmls=["<div>seed html</div>"],
        slide_image_urls=["/app_data/images/seed/slide_1.png"],
    )
    session.add(template_info)

    save_response = asyncio.run(
        save_template(
            request=SaveTemplateRequest(
                template_info_id=template_info.id,
                name="My Template",
                description="Saved from test",
                layouts=[
                    SaveTemplateLayoutData(
                        layout_id="title-image-1000",
                        layout_name="Title Image",
                        layout_code='const layoutId = "title-image-1000";',
                    )
                ],
            ),
            sql_session=session,
        )
    )
    template_id = save_response.id

    clone_layout_response = asyncio.run(
        clone_slide_layout(
            request=CloneSlideLayoutRequest(
                template_id=f"custom-{template_id}",
                layout_id="title-image-1000",
                layout_name="Cloned Layout",
            ),
            sql_session=session,
        )
    )
    assert clone_layout_response.layout_name == "Cloned Layout"
    assert clone_layout_response.layout_id != "title-image-1000"

    asyncio.run(
        save_slide_layout(
            request=SaveSlideLayoutRequest(
                template_id=template_id,
                layout_id="title-image-1000",
                layout_code='const layoutId = "title-image-1000"; const edited = true;',
            ),
            sql_session=session,
        )
    )
    assert any("edited = true" in layout.layout_code for layout in session.layouts)

    update_response = asyncio.run(
        update_template(
            request=UpdateTemplateRequest(
                id=template_id,
                layouts=[
                    SaveTemplateLayoutData(
                        layout_id="updated-layout-2000",
                        layout_name="Updated Layout",
                        layout_code='const layoutId = "updated-layout-2000";',
                    )
                ],
            ),
            sql_session=session,
        )
    )
    assert update_response.id == template_id

    layouts = [layout for layout in session.layouts if layout.presentation == template_id]
    assert session.templates[template_id] is not None
    assert len(layouts) == 1
    assert layouts[0].layout_id == "updated-layout-2000"
    assert layouts[0].fonts == {
        "Inter": "https://fonts.googleapis.com/css2?family=Inter&display=swap"
    }
