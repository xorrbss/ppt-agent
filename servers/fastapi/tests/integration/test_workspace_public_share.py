"""Public-share privacy boundary for Chat-first and presentation authoring data."""

from __future__ import annotations

import uuid

from fastapi.routing import APIRoute

from api.v1.ppt.endpoints.presentation_share import PRESENTATION_SHARE_ROUTER
from models.presentation_with_slides import (
    PublicPresentationWithSlides,
    PublicSlide,
    _normalized_public_key,
)


_PRIVATE_PRESENTATION_FIELDS = {
    "content",
    "tone",
    "verbosity",
    "prompt",
    "attachments",
    "attachment_file_name",
    "attachment_url",
    "conversation",
    "conversations",
    "operation",
    "operations",
    "operation_events",
    "error",
    "trace",
    "stack_trace",
    "revision",
    "share_token",
}

_PRIVATE_SLIDE_FIELDS = {"owner_id", "presentation", "speaker_note"}


def _public_payload() -> dict:
    return {
        "id": uuid.uuid4(),
        "content": "TOP SECRET acquisition strategy prompt",
        "tone": "confidential board discussion",
        "verbosity": "internal-only",
        "n_slides": 1,
        "language": "ko",
        "title": "Rendered deck title",
        "slides": [
            {
                "id": uuid.uuid4(),
                "owner_id": uuid.uuid4(),
                "presentation": uuid.uuid4(),
                "layout_group": "general",
                "layout": "title",
                "index": 0,
                "content": {"title": "Safe rendered text"},
                "speaker_note": "Private presenter note",
            }
        ],
    }


def test_public_presentation_dto_is_an_explicit_allowlist():
    assert _PRIVATE_PRESENTATION_FIELDS.isdisjoint(
        PublicPresentationWithSlides.model_fields
    )
    assert _PRIVATE_SLIDE_FIELDS.isdisjoint(PublicSlide.model_fields)


def test_public_projection_drops_real_prompt_settings_and_speaker_notes():
    public_deck = PublicPresentationWithSlides.model_validate(_public_payload())
    serialized = public_deck.model_dump(mode="json")

    assert _PRIVATE_PRESENTATION_FIELDS.isdisjoint(serialized)
    assert _PRIVATE_SLIDE_FIELDS.isdisjoint(serialized["slides"][0])
    assert "TOP SECRET" not in str(serialized)
    assert "Private presenter note" not in str(serialized)


def test_public_projection_recursively_drops_private_keys_in_dicts_and_lists():
    payload = _public_payload()
    payload["theme"] = {
        "palette": [
            {"color": "blue", "requestPayload": "nested request secret"},
            {"operation-events": [{"trace": "nested trace secret"}]},
            {
                "receipt": "nested receipt secret",
                "artifactPointer": "nested artifact secret",
            },
            {
                "APIKey": "nested credential secret",
                "refreshToken": "nested token secret",
            },
        ],
        "ownerId": "nested owner secret",
    }
    payload["slides"][0]["content"] = {
        "title": "Safe rendered text",
        "groups": [
            {
                "children": [
                    {"speakerNote": "nested speaker secret"},
                    {"attachment_url": "https://private.invalid/file"},
                    {"src": "/app_data/images/users/owner/slide.png"},
                ]
            }
        ],
        "system-prompt": "nested prompt secret",
    }
    payload["slides"][0]["ui"] = {
        "messages": [{"messageBody": "nested message secret"}],
        "visible": True,
    }

    serialized = PublicPresentationWithSlides.model_validate(payload).model_dump(
        mode="json"
    )

    def walk(value):
        if isinstance(value, dict):
            for key, child in value.items():
                assert _normalized_public_key(key) not in {
                    "request_payload",
                    "operation_events",
                    "trace",
                    "receipt",
                    "artifact_pointer",
                    "owner_id",
                    "speaker_note",
                    "attachment_url",
                    "system_prompt",
                    "messages",
                    "message_body",
                    "api_key",
                    "refresh_token",
                }
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(serialized)
    assert "nested" not in str(serialized)
    assert serialized["slides"][0]["content"]["groups"][0]["children"][2][
        "src"
    ].endswith("slide.png")


def test_public_projection_sanitizes_authored_html_metadata_but_keeps_rendering():
    payload = _public_payload()
    payload["slides"][0]["html_content"] = (
        "<!-- illustration prompt: acquire confidential competitor -->"
        '<section data-owner-id="private-owner" data-qid="editor-only">'
        '<img data-illust-prompt="private generated illustration prompt" '
        'src="/app_data/images/authored/deck/slide.png" alt="Safe image">'
        "<p>Safe rendered text</p></section>"
    )

    serialized = PublicPresentationWithSlides.model_validate(payload).model_dump(
        mode="json"
    )
    html = serialized["slides"][0]["html_content"]

    assert "confidential competitor" not in html
    assert "private generated illustration prompt" not in html
    assert "data-illust-prompt" not in html
    assert "data-owner-id" not in html
    assert "data-qid" not in html
    assert '<img src="/app_data/images/authored/deck/slide.png" alt="Safe image">' in html
    assert "Safe rendered text" in html


def test_public_endpoint_uses_the_public_projection_not_the_private_dto():
    route = next(
        route
        for route in PRESENTATION_SHARE_ROUTER.routes
        if isinstance(route, APIRoute) and route.path.endswith("/public/{share_token}")
    )
    assert route.response_model is PublicPresentationWithSlides
