"""H6: the advisory vision pass over the deck contact sheet.

See ``docs/authored-quality-speed-programme-design-20260905.md`` section 6, H6.
The contract these tests hold is that the advisory is closed-vocabulary,
defensive about whatever the model returns, and incapable of raising: it is
written into the receipt as ``advisory.vision`` and read by nothing.
"""

from __future__ import annotations

import asyncio
import io

import pytest

from services import authored_vision_advisory as advisory_mod
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
