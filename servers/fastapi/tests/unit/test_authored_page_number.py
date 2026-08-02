import asyncio
import uuid
from types import SimpleNamespace

from models.sql.slide import SlideModel
from services import authored_page_number_service as response_service
from utils.authored_page_number import normalize_authored_page_number
from utils.llm_calls.author_slide import EXACT_RECONSTRUCTION, STYLE_REFERENCE


def _html(footer: str) -> str:
    return f"<!doctype html><html><body><main>content</main>{footer}</body></html>"


def test_normalizer_replaces_page_total_with_blue_two_digit_marker_idempotently():
    source = _html(
        '<footer><span>OSB proposal</span><span class="page">07 / 90</span></footer>'
    )

    normalized = normalize_authored_page_number(
        source,
        slide_index=6,
        primary="#2457D6",
        reference_mode=STYLE_REFERENCE,
    )

    assert normalized.count("<!-- presenton-page-number:start -->") == 1
    assert '<div data-presenton-page-number aria-hidden="true">07</div>' in normalized
    assert "color: #2457D6 !important" in normalized
    assert "footer .page" in normalized
    assert (
        normalize_authored_page_number(
            normalized,
            slide_index=6,
            primary="#2457D6",
            reference_mode=STYLE_REFERENCE,
        )
        == normalized
    )


def test_normalizer_hides_section_label_footer_variant():
    source = _html(
        "<footer><span>OSB proposal</span><span class='footer-line'></span>"
        "<span>제안사 역량</span></footer>"
    )

    normalized = normalize_authored_page_number(
        source,
        slide_index=47,
        primary="not-a-css-colour",
        reference_mode=STYLE_REFERENCE,
    )

    assert "footer .footer-line + :last-child" in normalized
    assert ">48</div>" in normalized
    assert "color: #2563EB !important" in normalized
    assert (
        normalize_authored_page_number(
            normalized,
            slide_index=47,
            primary="not-a-css-colour",
            reference_mode=STYLE_REFERENCE,
        )
        == normalized
    )


def test_normalizer_hides_bare_footer_page_total_token():
    source = _html(
        "<footer><span>OSB proposal</span><span>48 / 90</span></footer>"
    )

    normalized = normalize_authored_page_number(
        source,
        slide_index=47,
        primary="#2457D6",
        reference_mode=STYLE_REFERENCE,
    )

    assert "data-presenton-old-page-number" in normalized
    assert ">48</div>" in normalized


def test_normalizer_leaves_exact_reconstruction_byte_for_byte_unchanged():
    source = _html('<footer><span class="page">07 / 90</span></footer>')

    assert (
        normalize_authored_page_number(
            source,
            slide_index=6,
            primary="#2457D6",
            reference_mode=EXACT_RECONSTRUCTION,
        )
        == source
    )


def test_existing_style_reference_is_normalized_only_in_api_response(monkeypatch):
    presentation_id = uuid.uuid4()
    presentation = SimpleNamespace(
        id=presentation_id,
        theme={"mode": "authored", "style": "visual", "primary": "#2457D6"},
        is_authored=lambda: True,
    )
    source = _html('<footer><span class="page">07 / 90</span></footer>')
    slide = SlideModel(
        presentation=presentation_id,
        layout_group="authored",
        layout="authored:CONTENT",
        index=6,
        content={"__authored__": True},
        html_content=source,
        properties={},
    )

    class Session:
        async def get(self, _model, _key):
            return None

    monkeypatch.setattr(
        response_service,
        "load_authored_styles",
        lambda: [
            SimpleNamespace(
                id="visual",
                reference_mode=STYLE_REFERENCE,
                primary_color="#123456",
                preview_accent="#654321",
            )
        ],
    )

    [response_slide] = asyncio.run(
        response_service.authored_slides_with_standard_page_numbers(
            Session(), presentation, [slide]
        )
    )

    assert response_slide is not slide
    assert ">07</div>" in response_slide.html_content
    assert "color: #2457D6 !important" in response_slide.html_content
    assert slide.html_content == source


def test_existing_exact_provenance_bypasses_api_response_normalization():
    presentation_id = uuid.uuid4()
    presentation = SimpleNamespace(
        id=presentation_id,
        theme={"mode": "authored", "style": "private-exact"},
        is_authored=lambda: True,
    )
    source = _html('<footer><span class="page">07 / 90</span></footer>')
    slide = SlideModel(
        presentation=presentation_id,
        layout_group="authored",
        layout="authored:CONTENT",
        index=6,
        content={"__authored__": True},
        html_content=source,
        properties={},
    )

    class Session:
        async def get(self, _model, _key):
            return object()

    [response_slide] = asyncio.run(
        response_service.authored_slides_with_standard_page_numbers(
            Session(), presentation, [slide]
        )
    )

    assert response_slide is slide
    assert response_slide.html_content == source
