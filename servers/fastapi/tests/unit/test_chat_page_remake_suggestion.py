"""A chat turn that cannot edit an authored page must still lead somewhere.

The chat tools refuse a model-authored page: they can delete it but have no
layout to rebuild it from.  The refusal reaches the model as prose, which then
answers by naming the "페이지 다시 만들기" flow - leaving the user to go find it.

These tests pin the machine-readable half of that refusal travelling out with
the turn, so the workspace client can offer the flow on the refused page
instead of describing it.  The provider is stubbed: the behaviour under test is
which page the loop reports, and a live model would make that nondeterministic
without exercising any of it.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from constants.presentation import AUTHORED_SLIDE_CHAT_EDIT_REFUSAL_CODE
from services.chat.service import (
    ChatTurnResult,
    PresentationChatService,
    _refused_authored_page_index,
)
from services.workspace_chat_operation_handler import chat_turn_result_payload


REFUSAL = {
    "saved": False,
    "message": "…페이지 다시 만들기…",
    "validation_errors": [AUTHORED_SLIDE_CHAT_EDIT_REFUSAL_CODE],
    "index": 4,
}


class _ToolCall:
    def __init__(self, name: str, call_id: str) -> None:
        self.name = name
        self.id = call_id


class _Response:
    def __init__(self, tool_calls: list[_ToolCall], text: str = "") -> None:
        self.tool_calls = tool_calls
        self.content = text
        self.messages = []


class _Client:
    """Answer with one tool round, then settle - the shape of a refused turn."""

    def __init__(self, rounds: list[_Response]) -> None:
        self._rounds = list(rounds)

    def generate(self, **_kwargs: Any) -> _Response:
        return self._rounds.pop(0)


def _service(results: list[dict[str, Any]], rounds: list[_Response]):
    service = PresentationChatService.__new__(PresentationChatService)
    pending = list(results)

    class _Tools:
        def get_tool_definitions(self) -> list[Any]:
            return []

        async def execute_tool_call(self, _call: _ToolCall) -> dict[str, Any]:
            return pending.pop(0)

    service._tools = _Tools()  # type: ignore[attr-defined]
    return service, _Client(rounds)


def _run_loop(monkeypatch, results, rounds) -> tuple[str, list[str], int | None]:
    service, client = _service(results, rounds)
    monkeypatch.setattr("services.chat.service.get_client", lambda **_: client)
    monkeypatch.setattr("services.chat.service.get_model", lambda: "stub-model")
    monkeypatch.setattr("services.chat.service.get_llm_config", lambda: None)
    monkeypatch.setattr(
        "services.chat.service.build_chat_llm_tools", lambda _definitions: []
    )
    # Assembling real request kwargs resolves a provider and an API key, which
    # this loop never uses: the stub client ignores them.
    monkeypatch.setattr(
        "services.chat.service.get_generate_kwargs", lambda **kwargs: kwargs
    )
    return asyncio.run(service._run_llm_with_tools([]))


def test_a_refused_authored_page_travels_out_with_the_turn(monkeypatch):
    _text, _tools, refused_index = _run_loop(
        monkeypatch,
        [REFUSAL],
        [_Response([_ToolCall("save_slide", "1")]), _Response([], "다시 만들기를 써 주세요")],
    )
    assert refused_index == 4


def test_an_ordinary_turn_suggests_nothing(monkeypatch):
    _text, _tools, refused_index = _run_loop(
        monkeypatch,
        [{"saved": True, "index": 2}],
        [_Response([_ToolCall("save_slide", "1")]), _Response([], "고쳤습니다")],
    )
    assert refused_index is None


def test_the_first_refused_page_wins(monkeypatch):
    """A later round may retry the same page or move to one it can edit.

    Neither should move the offer off the page the user actually asked about.
    """

    second = dict(REFUSAL, index=9)
    _text, _tools, refused_index = _run_loop(
        monkeypatch,
        [REFUSAL, second],
        [
            _Response([_ToolCall("save_slide", "1")]),
            _Response([_ToolCall("save_slide", "2")]),
            _Response([], "다시 만들기를 써 주세요"),
        ],
    )
    assert refused_index == 4


def test_the_insert_refusal_names_no_page():
    """Adding a page to an authored deck is refused with no page to remake."""

    assert (
        _refused_authored_page_index(
            {"error": AUTHORED_SLIDE_CHAT_EDIT_REFUSAL_CODE, "found": True}
        )
        is None
    )


def test_a_boolean_index_is_not_a_page_number():
    assert _refused_authored_page_index(dict(REFUSAL, index=True)) is None


def test_an_unrelated_validation_failure_is_not_a_remake_offer():
    assert (
        _refused_authored_page_index(
            {"saved": False, "validation_errors": ["unknown_layout"], "index": 4}
        )
        is None
    )


def test_the_payload_carries_the_refused_page():
    payload = chat_turn_result_payload(
        ChatTurnResult(
            conversation_id=uuid.uuid4(),
            response_text="다시 만들기를 써 주세요",
            tool_calls=["save_slide"],
            page_remake_slide_index=4,
        ),
        {},
    )
    assert payload["page_remake_slide_index"] == 4


def test_the_payload_is_unchanged_when_nothing_was_refused():
    payload = chat_turn_result_payload(
        ChatTurnResult(
            conversation_id=uuid.uuid4(),
            response_text="고쳤습니다",
            tool_calls=["save_slide"],
        ),
        {},
    )
    assert "page_remake_slide_index" not in payload
    assert set(payload) == {"conversation_id", "summary", "tool_calls"}
