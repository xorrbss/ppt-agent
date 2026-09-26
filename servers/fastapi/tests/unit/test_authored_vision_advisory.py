"""H6: the advisory vision pass over the deck contact sheet.

See ``docs/authored-quality-speed-programme-design-20260905.md`` section 6, H6.
The contract these tests hold is that the advisory is closed-vocabulary,
defensive about whatever the model returns, and incapable of raising: it is
written into the receipt as ``advisory.vision`` and read by nothing.
"""

from __future__ import annotations

import asyncio
import io
import json
from types import SimpleNamespace

import pytest

from services import authored_vision_advisory as advisory_mod
from services import authored_vision_advisory_prompt as prompt_mod
from services import authored_vision_contact_sheet as sheet_mod


def _run(coro):
    return asyncio.run(coro)


def _png(width: int = 1280, height: int = 720, colour: str = "white") -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(buffer, format="PNG")
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Contact sheet
# ---------------------------------------------------------------------------


def test_the_sheet_is_four_columns_within_the_pixel_envelope():
    from PIL import Image

    sheet = sheet_mod.compose_contact_sheet_sync([_png() for _ in range(9)])

    with Image.open(io.BytesIO(sheet)) as image:
        assert image.format == "JPEG"
        assert image.width <= sheet_mod.SHEET_MAX_WIDTH_PX
        assert image.height <= sheet_mod.SHEET_MAX_HEIGHT_PX
        # 9 slides at 4 per row is 3 rows, each a 16:9 thumbnail plus its label.
        assert image.width == (sheet_mod.SHEET_MAX_WIDTH_PX // 4) * 4


def test_a_very_long_deck_shrinks_rather_than_exceeding_the_height_cap():
    from PIL import Image

    sheet = sheet_mod.compose_contact_sheet_sync([_png() for _ in range(120)])

    with Image.open(io.BytesIO(sheet)) as image:
        assert image.height <= sheet_mod.SHEET_MAX_HEIGHT_PX


def test_no_renders_compose_to_nothing():
    assert sheet_mod.compose_contact_sheet_sync([]) is None
    assert sheet_mod.compose_contact_sheet_sync([b"", None]) is None


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


def test_the_vocabulary_is_closed_and_indices_become_zero_based():
    parsed = advisory_mod.parse_advisory_payload(
        {
            "slides": [
                {"index": 1, "issues": ["empty_band", "made_up_word"]},
                {"index": 3, "issues": ["off-palette", "Broken Figure"]},
            ]
        },
        slide_count=4,
    )

    assert parsed == [
        {"index": 0, "issues": ["empty_band"]},
        {"index": 2, "issues": ["off_palette", "broken_figure"]},
    ]


def test_a_slide_whose_every_word_was_invented_is_dropped():
    parsed = advisory_mod.parse_advisory_payload(
        {"slides": [{"index": 2, "issues": ["vibes", ""]}]}, slide_count=3
    )

    assert parsed == []


def test_an_out_of_range_index_is_clamped_not_dropped():
    parsed = advisory_mod.parse_advisory_payload(
        {"slides": [{"index": 99, "issues": ["cramped"]}, {"index": 0, "issues": ["cramped"]}]},
        slide_count=3,
    )

    assert [item["index"] for item in parsed] == [0, 2]


@pytest.mark.parametrize(
    "payload",
    [None, "text", {}, {"slides": "no"}, {"slides": [1, None, {"index": "x"}]}],
)
def test_a_malformed_payload_parses_to_nothing(payload):
    assert advisory_mod.parse_advisory_payload(payload, slide_count=3) == []


# ---------------------------------------------------------------------------
# run_vision_advisory: every failure is a skip, never a raise
# ---------------------------------------------------------------------------


def test_a_deck_with_no_renders_is_skipped():
    assert _run(advisory_mod.run_vision_advisory([], slide_count=0)) == {
        "skipped": "no_renders"
    }


def test_a_provider_failure_is_a_skip(monkeypatch):
    async def boom(sheet, *, slide_count):
        raise RuntimeError("provider down")

    monkeypatch.setattr(advisory_mod, "_request_advisory", boom)

    assert _run(advisory_mod.run_vision_advisory([_png()], slide_count=1)) == {
        "skipped": "provider_failed"
    }


def test_an_unparseable_response_is_a_skip(monkeypatch):
    async def unparseable(sheet, *, slide_count):
        return "some-model", None

    monkeypatch.setattr(advisory_mod, "_request_advisory", unparseable)

    assert _run(advisory_mod.run_vision_advisory([_png()], slide_count=1)) == {
        "skipped": "unparseable_response"
    }


def test_a_successful_advisory_reports_the_model_and_the_findings(monkeypatch):
    seen: dict[str, object] = {}

    async def respond(sheet, *, slide_count):
        seen["sheet_bytes"] = len(sheet)
        seen["slide_count"] = slide_count
        return "gpt-6-astra", {"slides": [{"index": 2, "issues": ["empty_band"]}]}

    monkeypatch.setattr(advisory_mod, "_request_advisory", respond)

    result = _run(advisory_mod.run_vision_advisory([_png(), _png()], slide_count=2))

    assert result["model"] == "gpt-6-astra"
    assert result["slideCount"] == 2
    assert result["slides"] == [{"index": 1, "issues": ["empty_band"]}]
    assert isinstance(result["latencyMs"], int)
    assert seen["slide_count"] == 2
    assert seen["sheet_bytes"] > 0


# ---------------------------------------------------------------------------
# The request: the same shape as the vision-QA critique path, which works
# ---------------------------------------------------------------------------


def _part_types(message):
    return [type(part).__name__ for part in message.content]


def test_the_advisory_request_has_the_same_message_shape_as_the_critique_call():
    """Bench run 30 came back empty. The shape was not the difference, and it
    must never become one, so it is pinned to the call that does work."""

    from llmai.shared import SystemMessage, UserMessage

    from utils.llm_calls import critique_slide

    advisory = prompt_mod.build_advisory_messages(b"sheet", slide_count=12)
    critique = critique_slide._build_critique_messages(b"png")

    assert isinstance(advisory[0], SystemMessage)
    assert isinstance(advisory[1], UserMessage)
    assert len(advisory) == len(critique) == 2
    # Same content-part types, in the same order: text first, then the image.
    assert _part_types(advisory[1]) == _part_types(critique[1])
    assert _part_types(advisory[1]) == ["TextContentPart", "ImageContentPart"]
    assert advisory[1].content[1].data == b"sheet"
    assert advisory[1].content[1].mime_type == "image/jpeg"
    assert "12" in advisory[1].content[0].text


def test_the_system_prompt_shows_the_model_the_json_it_must_answer_with():
    assert prompt_mod.ADVISORY_EXAMPLE in prompt_mod.SYSTEM_PROMPT
    assert all(issue in prompt_mod.SYSTEM_PROMPT for issue in advisory_mod.ISSUE_VOCABULARY)
    assert advisory_mod.parse_advisory_payload(
        json.loads(prompt_mod.ADVISORY_EXAMPLE), slide_count=24
    ) == [
        {"index": 2, "issues": ["empty_band", "unbalanced"]},
        {"index": 10, "issues": ["broken_figure"]},
    ]


def test_the_output_budget_covers_reasoning_as_well_as_the_answer():
    # Inert on codex (llmai drops max_tokens there) but read by every other
    # provider, where 1200 would truncate a long deck's answer.
    assert prompt_mod.ADVISORY_MAX_OUTPUT_TOKENS >= 1200
    assert advisory_mod.ADVISORY_MAX_OUTPUT_TOKENS is prompt_mod.ADVISORY_MAX_OUTPUT_TOKENS


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.content = text


class _FakeClient:
    """Stands in for the llmai client; records the kwargs it is handed."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.kwargs: dict = {}

    def generate(self, **kwargs):
        self.kwargs = kwargs
        return _FakeResponse(self.text)


def _install_fake_client(monkeypatch, client):
    import llmai

    monkeypatch.setattr(llmai, "get_client", lambda config=None: client)
    monkeypatch.setattr("utils.llm_config.get_llm_config", lambda *a, **k: None)
    # get_generate_kwargs resolves the configured provider for extra_body; this
    # test has no configured provider and none is needed to pin the shape.
    monkeypatch.setattr("utils.llm_utils.get_extra_body", lambda *a, **k: None)
    monkeypatch.setattr("utils.llm_config.enforce_llm_egress_policy", lambda: None)
    monkeypatch.setattr("utils.llm_provider.get_model", lambda *a, **k: "gpt-6-astra")
    monkeypatch.setattr("utils.llm_provider.is_codex_selected", lambda *a, **k: False)
    monkeypatch.setattr(
        "utils.llm_provider.get_llm_provider",
        lambda *a, **k: SimpleNamespace(value="codex"),
    )


def test_the_advisory_call_sends_the_sheet_and_the_budget(monkeypatch):
    client = _FakeClient('{"slides":[{"index":2,"issues":["cramped"]}]}')
    _install_fake_client(monkeypatch, client)

    model, payload = _run(advisory_mod._request_advisory(b"sheet", slide_count=6))

    assert model == "gpt-6-astra"
    assert payload == {"slides": [{"index": 2, "issues": ["cramped"]}]}
    assert client.kwargs["max_tokens"] == prompt_mod.ADVISORY_MAX_OUTPUT_TOKENS
    assert _part_types(client.kwargs["messages"][1]) == [
        "TextContentPart",
        "ImageContentPart",
    ]
    assert client.kwargs["messages"][1].content[1].data == b"sheet"


def test_the_advisory_accepts_a_provider_parsed_json_object(monkeypatch):
    from services.generation_core.quality import vision_advisory as shared_advisory

    client = _FakeClient({"slides": []})
    _install_fake_client(monkeypatch, client)

    model, payload = _run(shared_advisory._request_advisory(b"sheet", slide_count=1))

    assert model == "gpt-6-astra"
    assert payload == {"slides": []}


def test_an_empty_model_answer_is_a_named_skip_not_a_raise(monkeypatch):
    """``public_provider_empty_output`` is our own admission wrapper refusing a
    text-free response; the advisory records which outcome it was."""

    _install_fake_client(monkeypatch, _FakeClient("   "))

    result = _run(advisory_mod.run_vision_advisory([_png()], slide_count=1))

    assert result == {"skipped": "provider_outcome:public_provider_empty_output"}


# ---------------------------------------------------------------------------
# Sheet budget
# ---------------------------------------------------------------------------


def test_the_sheet_stays_inside_the_byte_budget_a_vision_request_allows():
    from PIL import Image

    def noisy(index: int) -> bytes:
        image = Image.effect_noise((1280, 720), 96).convert("RGB")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    sheet = sheet_mod.compose_contact_sheet_sync([noisy(index) for index in range(40)])

    assert len(sheet) <= sheet_mod.SHEET_MAX_BYTES
    with Image.open(io.BytesIO(sheet)) as image:
        assert image.width <= sheet_mod.SHEET_MAX_WIDTH_PX <= 1600


def test_a_sheet_over_the_budget_spends_quality_and_then_pixels(monkeypatch):
    from PIL import Image

    def noisy() -> bytes:
        buffer = io.BytesIO()
        Image.effect_noise((1280, 720), 96).convert("RGB").save(buffer, format="PNG")
        return buffer.getvalue()

    monkeypatch.setattr(sheet_mod, "SHEET_MAX_BYTES", 250_000)

    sheet = sheet_mod.compose_contact_sheet_sync([noisy() for _ in range(40)])

    assert len(sheet) <= 250_000
    with Image.open(io.BytesIO(sheet)) as image:
        # Thumbnails survive: the numbering the model answers with is drawn on
        # them, so the sheet must never shrink past legibility.
        assert image.width >= 640
