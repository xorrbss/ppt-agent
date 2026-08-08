import asyncio
import json
from types import SimpleNamespace
import uuid

import pytest

from api.v1.ppt.endpoints import template_v2_compat
from api.v1.ppt.endpoints.template_v2_compat import (
    GenerateTemplateLayoutRequest,
)
from models.sql.template_v2 import TemplateV2
from services.template_v2_service import TemplateV2Record
from templates.v2 import generation
from templates.v2.models.elements import ImageFit
from templates.v2.models.layouts import SlideLayout, SlideLayouts


PRESENTATION_ID = uuid.UUID("33333333-3333-4333-8333-333333333333")


def _layout(
    layout_id: str,
    *,
    image_data: str = "https://untrusted.invalid/content.png",
) -> SlideLayout:
    return SlideLayout.model_validate(
        {
            "id": layout_id,
            "description": "Editable image and title presentation layout",
            "components": [
                {
                    "id": "hero",
                    "description": "Editable hero content component",
                    "position": {"x": 0, "y": 0},
                    "elements": [
                        {
                            "type": "text",
                            "runs": [{"text": "Neutral heading"}],
                            "decorative": False,
                            "name": "title",
                            "min_length": 1,
                            "max_length": 80,
                        },
                        {
                            "type": "image",
                            "data": image_data,
                            "decorative": False,
                            "name": "hero_image",
                            "is_icon": False,
                        },
                        {
                            "type": "image",
                            "data": "https://untrusted.invalid/icon.svg",
                            "decorative": False,
                            "name": "hero_icon",
                            "is_icon": True,
                        },
                        {
                            "type": "image",
                            "data": "https://trusted.invalid/logo.png",
                            "decorative": True,
                            "name": "brand_logo",
                            "is_icon": False,
                        },
                    ],
                }
            ],
        }
    )


def test_prompted_layout_retries_and_normalizes_replaceable_assets(monkeypatch):
    source = SlideLayouts(layouts=[_layout("existing_layout")])
    duplicate = _layout("existing_layout").model_dump(mode="json")
    generated = _layout("new_image_layout").model_dump(mode="json")
    responses = iter([duplicate, generated])
    calls: list[dict] = []

    class FakeClient:
        def generate(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(content=json.dumps(next(responses)))

    monkeypatch.setattr(
        generation,
        "get_client",
        lambda **_kwargs: FakeClient(),
    )
    monkeypatch.setattr(generation, "get_model", lambda: "test-model")
    monkeypatch.setattr(generation, "get_llm_config", lambda: object())

    result = generation.generate_prompted_slide_layout(
        "Create an image-led hero",
        source,
        template_name="Blue template",
        fonts={"Inter": "https://example.invalid/inter.woff2"},
    )

    assert len(calls) == 2
    assert calls[0]["max_tokens"] == 16_000
    assert result.id == "new_image_layout"
    images = [
        element
        for element in result.components[0].elements
        if element.type == "image"
    ]
    assert images[0].data == (
        "/static/images/replaceable_template_image.png"
    )
    assert images[0].fit == ImageFit.COVER
    assert images[1].data == "/static/icons/placeholder.svg"
    assert images[2].data == "https://trusted.invalid/logo.png"
    assert source.layouts[0].components[0].elements[1].data == (
        "https://untrusted.invalid/content.png"
    )


def test_prompted_layout_normalizes_contained_content_images_to_cover():
    source = _layout("contained_layout")
    content_image = source.components[0].elements[1]
    content_image.fit = ImageFit.CONTAIN

    result = generation._replace_content_image_urls(source)

    normalized_image = result.components[0].elements[1]
    assert normalized_image.data == (
        "/static/images/replaceable_template_image.png"
    )
    assert normalized_image.fit == ImageFit.COVER
    assert content_image.fit == ImageFit.CONTAIN


@pytest.mark.parametrize(
    "payload",
    [
        {"template_id": " ", "prompt": "new layout"},
        {"template_id": "template", "prompt": " "},
        {"template_id": "template", "prompt": "x" * 8001},
    ],
)
def test_generate_request_rejects_empty_or_oversized_values(payload):
    with pytest.raises(ValueError):
        GenerateTemplateLayoutRequest.model_validate(payload)


def test_generate_request_accepts_upstream_id_alias():
    request = GenerateTemplateLayoutRequest.model_validate(
        {"id": " template ", "prompt": " new layout "}
    )

    assert request.template_id == "template"
    assert request.prompt == "new layout"


def test_generate_endpoint_is_owner_scoped_and_draft_only(monkeypatch):
    source_layout = _layout("existing_layout")
    template = TemplateV2(
        id="owned-template",
        presentation_id=PRESENTATION_ID,
        name="Owned",
        description="Owner-visible template",
        layouts={"layouts": [source_layout.model_dump(mode="json")]},
        assets={"fonts": {"Inter": "https://example.invalid/inter.woff2"}},
        revision=3,
    )
    record = TemplateV2Record(
        template=template,
        presentation_id=PRESENTATION_ID,
        revision=3,
    )
    observed: dict[str, object] = {}

    class RecordingService:
        def __init__(self, session):
            observed["session"] = session

        async def get(self, template_id):
            observed["template_id"] = template_id
            return record

        async def update(self, *_args, **_kwargs):
            pytest.fail("draft generation must not persist the layout")

    generated = _layout("generated_layout")

    def fake_generate(*args):
        observed["generate_args"] = args
        return generated

    monkeypatch.setattr(
        template_v2_compat,
        "TemplateV2Service",
        RecordingService,
    )
    monkeypatch.setattr(
        template_v2_compat,
        "generate_prompted_slide_layout",
        fake_generate,
    )

    response = asyncio.run(
        template_v2_compat.generate_template_layout_from_prompt(
            request=GenerateTemplateLayoutRequest(
                template_id=" owned-template ",
                prompt=" A hero layout ",
            ),
            sql_session=object(),
        )
    )

    assert observed["template_id"] == "owned-template"
    assert observed["generate_args"][0] == "A hero layout"
    assert response.layout.id == "generated_layout"
    assert response.response == "Created the generated layout template layout."
