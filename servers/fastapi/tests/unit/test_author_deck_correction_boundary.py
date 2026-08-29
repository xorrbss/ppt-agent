"""Narrow contract test for the authored slide correction boundary."""

import asyncio

import pytest

from utils.llm_calls import author_deck as author_deck_mod
from utils.llm_calls.author_slide import Brand


_VALID_HTML = (
    "<!DOCTYPE html><html lang='ko'><head><meta charset='utf-8'></head>"
    "<body><h1 data-qid='title'>제목</h1></body></html>"
)


def test_invalid_initial_gets_one_audited_correction_then_succeeds_or_terminates(
    monkeypatch,
):
    brand = Brand(
        topic="주제",
        language="Korean",
        primary="#2563EB",
        fonts="Noto Sans KR",
    )
    successful_calls: list[dict[str, object]] = []

    async def succeeds_on_correction(*_args, **kwargs):
        successful_calls.append(kwargs)
        if len(successful_calls) == 1:
            raise ValueError("authored_semantic_qid_missing:section")
        return _VALID_HTML

    monkeypatch.setattr(
        author_deck_mod,
        "author_slide_html",
        succeeds_on_correction,
    )

    html = asyncio.run(
        author_deck_mod._author_one_resilient(
            "slide",
            "design",
            brand,
            "COVER",
            0,
            1,
            raise_on_failure=True,
        )
    )

    assert html == _VALID_HTML
    assert len(successful_calls) == 2
    assert [call["receipt_retry_class"] for call in successful_calls] == [
        "initial",
        "recovery",
    ]
    assert [call["receipt_attempt_ordinal"] for call in successful_calls] == [1, 2]
    assert all(call["allow_provider_fallback"] is False for call in successful_calls)
    assert successful_calls[0].get("correction_hint") is None
    assert "data-qid" in str(successful_calls[1]["correction_hint"])

    terminal_calls: list[dict[str, object]] = []

    async def always_invalid(*_args, **kwargs):
        terminal_calls.append(kwargs)
        raise ValueError("authored_semantic_qid_missing:section")

    monkeypatch.setattr(author_deck_mod, "author_slide_html", always_invalid)

    with pytest.raises(ValueError, match="authored_semantic_qid_missing"):
        asyncio.run(
            author_deck_mod._author_one_resilient(
                "slide",
                "design",
                brand,
                "COVER",
                0,
                1,
                raise_on_failure=True,
            )
        )

    assert len(terminal_calls) == 2
    assert [call["receipt_retry_class"] for call in terminal_calls] == [
        "initial",
        "recovery",
    ]
    assert [call["receipt_attempt_ordinal"] for call in terminal_calls] == [1, 2]
    assert all(call["allow_provider_fallback"] is False for call in terminal_calls)
