"""The header contract carries the canvas it was designed for.

The 16:9 fixtures under ``tests/fixtures/header_16x9_baseline/`` were captured
before the contract learned its canvas; every 1280x720 output must still match
them byte for byte.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

import services.authored_header_contract_service as contract_service
import utils.llm_calls.author_header_contract as contract_call
from models.authored_header_contract import (
    AuthoredHeaderContract,
    HeaderEyebrowSpec,
    HeaderRequirementSpec,
    HeaderRightSlotSpec,
    HeaderSublineSpec,
    HeaderTickSpec,
    HeaderTitleSpec,
    default_header_contract,
)
from models.authored_header_row_fit import fit_requirement_row
from models.presentation_outline_model import PresentationOutlineModel
from services.authored_author_stage_placeholder import author_placeholder_html
from services.authored_header_contract_service import load_header_contract
from utils.authored_header_prompt import render_header_contract
from utils.llm_calls.author_header_contract import (
    build_header_contract_messages,
    build_header_revision_messages,
    coerce_header_contract,
)

FIXTURE_DIR = (
    Path(__file__).resolve().parents[1] / "fixtures" / "header_16x9_baseline"
)

A4 = {"canvas_width_px": 1188, "canvas_height_px": 840}

_MESSAGE_ARGS = dict(
    topic="클라우드 전환 제안",
    language="Korean",
    primary="#1D4ED8",
    fonts="Pretendard",
    style_brief="Quiet editorial deck.",
    instructions="헤더를 간결하게, 페이지 번호 표시",
    content_sample=("Ⅰ. 사업 개요\n추진 배경", "REQ-001 성능 요구사항"),
    allow_page_numbers=True,
    chapter_breadcrumb=True,
    allow_tick=True,
)

_FONT_STACK = "'Noto Sans KR', sans-serif"
_PLACEHOLDER_CONTENT = (
    "클라우드 전환의 단계별 추진 전략과 기대 효과\n- 1단계 진단\n- 2단계 전환"
)


def _slotted_contract(**canvas: int) -> AuthoredHeaderContract:
    return AuthoredHeaderContract(
        **canvas,
        eyebrow=HeaderEyebrowSpec(tick=HeaderTickSpec(enabled=True)),
        title=HeaderTitleSpec(max_width_px=640),
        subline=HeaderSublineSpec(enabled=True),
        right_slot=HeaderRightSlotSpec(mode="requirementsAndSummary", width_px=400),
        requirements=HeaderRequirementSpec(
            enabled=True, label="요구사항", overflow="plusN"
        ),
    )


def _placeholder(contract: AuthoredHeaderContract | None, **canvas: int) -> str:
    return author_placeholder_html(
        content=_PLACEHOLDER_CONTENT,
        brand=SimpleNamespace(primary="#1D4ED8"),
        role="problem",
        contract=contract,
        font_stack=_FONT_STACK,
        **canvas,
    )


def baseline_cases() -> dict[str, str]:
    """Every 16:9 output frozen before the contract carried its canvas."""
    default = default_header_contract()
    slotted = _slotted_contract()
    return {
        "contract_messages_user.txt": build_header_contract_messages(
            **_MESSAGE_ARGS
        )[1].content,
        "revision_messages_user.txt": build_header_revision_messages(
            slotted, "제목을 더 크게"
        )[1].content,
        "clause_default.txt": render_header_contract(default, language="Korean"),
        "clause_slotted.txt": render_header_contract(slotted, language="Korean"),
        "placeholder_default.html": _placeholder(default),
        "placeholder_slotted.html": _placeholder(slotted),
        "placeholder_uncontracted.html": _placeholder(None),
        "contract_default.json": json.dumps(
            default.model_dump(mode="json"), ensure_ascii=False, indent=2
        ),
        "contract_slotted.json": json.dumps(
            slotted.model_dump(mode="json"), ensure_ascii=False, indent=2
        ),
        "contract_schema.json": json.dumps(
            AuthoredHeaderContract.model_json_schema(), ensure_ascii=False, indent=2
        ),
    }


@pytest.mark.parametrize("name", sorted(baseline_cases()))
def test_16x9_output_is_byte_identical_to_the_frozen_baseline(name: str) -> None:
    expected = (FIXTURE_DIR / name).read_text(encoding="utf-8")
    assert baseline_cases()[name] == expected


def test_a_stored_payload_without_canvas_loads_as_1280x720() -> None:
    stored = json.loads((FIXTURE_DIR / "contract_slotted.json").read_text("utf-8"))
    assert "canvas_width_px" not in stored

    loaded = load_header_contract({"headerContract": stored})

    assert loaded is not None
    assert (loaded.canvas_width_px, loaded.canvas_height_px) == (1280, 720)
    assert loaded == _slotted_contract()


def test_a_non_default_canvas_round_trips_through_persistence() -> None:
    contract = default_header_contract(**A4)
    payload = contract.model_dump(mode="json")

    assert (payload["canvas_width_px"], payload["canvas_height_px"]) == (1188, 840)
    assert load_header_contract({"headerContract": payload}) == contract


@pytest.mark.parametrize(
    "field, value",
    [("canvas_width_px", 319), ("canvas_width_px", 7681), ("canvas_height_px", 179)],
)
def test_the_canvas_is_bounded(field: str, value: int) -> None:
    with pytest.raises(ValidationError):
        AuthoredHeaderContract(**{field: value})


def test_right_slot_position_reads_the_contract_width() -> None:
    contract = AuthoredHeaderContract(**A4, title=HeaderTitleSpec(max_width_px=700))

    assert contract.right_slot_left_px == 1188 - 64 - 336


def test_title_overlap_is_judged_against_the_contract_width() -> None:
    AuthoredHeaderContract(title=HeaderTitleSpec(max_width_px=780))
    with pytest.raises(ValidationError, match="overlaps the right slot"):
        AuthoredHeaderContract(**A4, title=HeaderTitleSpec(max_width_px=780))


def test_band_room_is_judged_against_the_contract_height() -> None:
    AuthoredHeaderContract(canvas_height_px=400)
    with pytest.raises(ValidationError, match="no room for slide content"):
        AuthoredHeaderContract(canvas_height_px=300)


@pytest.mark.parametrize(
    "canvas, slot, title",
    [
        ((1280, 720), 336, 780),
        ((1188, 840), 312, 748),
        ((840, 1188), 221, 491),
        ((720, 1280), 189, 403),
        ((1680, 720), 441, 780),
    ],
)
def test_the_default_contract_fits_its_canvas(canvas, slot, title) -> None:
    width, height = canvas
    contract = default_header_contract(canvas_width_px=width, canvas_height_px=height)

    assert (contract.canvas_width_px, contract.canvas_height_px) == canvas
    assert contract.right_slot.width_px == slot
    assert contract.title.max_width_px == title
    assert contract.title.max_width_px <= contract.right_slot_left_px - 64


def _requirement_row_contract(**canvas: int) -> AuthoredHeaderContract:
    return AuthoredHeaderContract(
        **canvas,
        title=HeaderTitleSpec(max_width_px=560),
        right_slot=HeaderRightSlotSpec(mode="requirements"),
        requirements=HeaderRequirementSpec(
            enabled=True, max_visible=6, padding_x_px=12
        ),
    )


def test_requirement_row_widening_is_bounded_by_the_contract_width() -> None:
    wide = fit_requirement_row(_requirement_row_contract())
    assert wide.right_slot.width_px == 504
    assert wide.requirements.max_visible == 6

    a4 = fit_requirement_row(_requirement_row_contract(**A4))
    assert a4.right_slot.width_px == 336
    assert a4.requirements.max_visible == 4
    assert (a4.canvas_width_px, a4.canvas_height_px) == (1188, 840)


def _payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "title": {"max_width_px": 800},
        "right_slot": {"mode": "none", "width_px": 336},
        "margin_x_px": 64,
    }
    payload.update(overrides)
    return payload


def test_title_narrowing_uses_the_deck_canvas() -> None:
    assert coerce_header_contract(_payload()).title.max_width_px == 800
    assert coerce_header_contract(_payload(), **A4).title.max_width_px == 1188 - 128 - 336


def test_an_omitted_title_width_is_narrowed_as_its_default() -> None:
    contract = coerce_header_contract({}, **A4)

    assert contract.title.max_width_px == 1188 - 128 - 336


def test_a_slot_too_wide_for_a_narrow_canvas_is_narrowed_to_keep_the_title() -> None:
    contract = coerce_header_contract(
        _payload(title={"max_width_px": 700}),
        canvas_width_px=720,
        canvas_height_px=1280,
    )

    assert contract.title.max_width_px == 320
    assert contract.right_slot.width_px == 720 - 128 - 320


def test_the_model_cannot_change_the_canvas() -> None:
    hostile = _payload(canvas_width_px=1920, canvas_height_px=1080, canvasWidthPx=640)

    assert coerce_header_contract(dict(hostile)).canvas_width_px == 1280
    forced = coerce_header_contract(dict(hostile), **A4)
    assert (forced.canvas_width_px, forced.canvas_height_px) == (1188, 840)


def test_the_schema_offers_the_model_no_canvas_field() -> None:
    properties = AuthoredHeaderContract.model_json_schema()["properties"]

    assert "canvas_width_px" not in properties
    assert "canvas_height_px" not in properties


def test_prompts_state_the_contract_canvas() -> None:
    contract = default_header_contract(**A4)
    clause = render_header_contract(contract, language="Korean")
    user = build_header_contract_messages(**_MESSAGE_ARGS, **A4)[1].content

    assert "the top of the 1188x840 canvas" in clause
    assert f"x={1188 - 64}px" in clause
    assert "1280" not in clause
    assert "The canvas is exactly 1188x840px." in user
    assert "title.max_width_px <= 1188 - 2 x margin_x_px" in user
    assert "1280" not in user


def test_the_placeholder_page_takes_the_contract_canvas() -> None:
    contract = default_header_contract(**A4)
    page = _placeholder(contract)
    band = max(contract.band_min_px, min(contract.band_max_px, contract.minimum_band_px))

    assert "width:1188px;height:840px;" in page
    assert f"height:{840 - band - contract.content_gap_px - 48}px;" in page
    assert "1280" not in page


def test_an_uncontracted_placeholder_takes_the_canvas_it_is_given() -> None:
    page = _placeholder(None, **A4)

    assert "width:1188px;height:840px;" in page
    assert "padding:64px;height:840px;" in page


def _fake_llm(monkeypatch: pytest.MonkeyPatch, answer: object) -> None:
    async def fake_generate(*_args, **_kwargs):
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(contract_call, "get_client", lambda **_kwargs: None)
    monkeypatch.setattr(contract_call, "get_llm_config", lambda: None)
    monkeypatch.setattr(contract_call, "get_model", lambda: "test-model")
    monkeypatch.setattr(
        contract_call, "generate_structured_with_schema_retries", fake_generate
    )


def _generate(**canvas: int) -> AuthoredHeaderContract:
    return asyncio.run(
        contract_call.generate_header_contract(
            topic="t", language="Korean", primary="#1D4ED8", fonts="Pretendard", **canvas
        )
    )


def test_a_generated_contract_keeps_the_deck_canvas(monkeypatch) -> None:
    _fake_llm(monkeypatch, _payload(canvas_width_px=1920, canvas_height_px=1080))

    contract = _generate(**A4)

    assert (contract.canvas_width_px, contract.canvas_height_px) == (1188, 840)
    assert contract.title.max_width_px == 1188 - 128 - 336


def test_the_fallback_contract_keeps_the_deck_canvas(monkeypatch) -> None:
    _fake_llm(monkeypatch, RuntimeError("provider down"))

    contract = _generate(**A4)

    assert contract == default_header_contract(primary="#1D4ED8", **A4)


def test_a_revision_keeps_the_current_canvas(monkeypatch) -> None:
    current = default_header_contract(**A4)
    answer = current.model_dump(mode="json")
    answer.update(canvas_width_px=1280, canvas_height_px=720)
    answer["title"]["color"] = "#111111"
    _fake_llm(monkeypatch, answer)

    revised = asyncio.run(contract_call.revise_header_contract(current, "제목 색"))

    assert revised.title.color == "#111111"
    assert (revised.canvas_width_px, revised.canvas_height_px) == (1188, 840)


def test_the_revision_prompt_never_shows_the_canvas() -> None:
    user = build_header_revision_messages(
        default_header_contract(**A4), "제목 색"
    )[1].content

    assert "canvas_width_px" not in user


def test_resolve_passes_the_deck_canvas(monkeypatch) -> None:
    seen: dict[str, object] = {}

    async def fake_generate(**kwargs):
        seen.update(kwargs)
        return default_header_contract(
            canvas_width_px=kwargs["canvas_width_px"],
            canvas_height_px=kwargs["canvas_height_px"],
        )

    monkeypatch.setattr(contract_service, "generate_header_contract", fake_generate)
    outline = PresentationOutlineModel(slides=[])

    contract = asyncio.run(
        contract_service.resolve_header_contract(
            outline, SimpleNamespace(primary="#1D4ED8"), **A4
        )
    )

    assert (seen["canvas_width_px"], seen["canvas_height_px"]) == (1188, 840)
    assert contract.canvas_width_px == 1188
