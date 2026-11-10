"""One bounded repair loop: order, the N bound, rejection, knob, evidence.

Design ``docs/authored-verbatim-placeholder-authoring-design-20260911.md`` 2.5.
Everything here is offline: no provider call, no browser, no database.
"""

from __future__ import annotations

import asyncio
from functools import wraps

import pytest

from services import authored_admission as gate
from services.authored_admission_context import context_from_source
from services.authored_rollout_profile import RolloutProfile, current_rollout_profile
from utils.get_env_programme import (
    get_authored_verbatim_repair_loop_model_calls,
)
from utils.runtime_settings import RuntimeSettings

ORIGINAL = "<!doctype html><html><body><h1>원문 제목</h1></body></html>"
DETERMINISTIC = "<!doctype html><html><body><h1 style=\"margin:0\">원문 제목</h1></body></html>"
MODEL = "<!doctype html><html><body><h2>원문 제목</h2></body></html>"


def sync_async(function):
    @wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))

    return run


# --- B6: the loop's own baseline capture and binding steps must not end the deck


@pytest.mark.parametrize(
    "value, expected", [(None, 1), ("2", 2), ("0", 0), ("9", 3), ("-4", 0)]
)
def test_the_model_call_budget_defaults_to_one_and_clamps(value, expected):
    settings = RuntimeSettings({"AUTHORED_VERBATIM_REPAIR_LOOP_MODEL_CALLS": value})
    assert get_authored_verbatim_repair_loop_model_calls(settings) == expected


MANIFEST = {
    "units": [
        {
            "sourceUnitId": "u1",
            "sourceIndex": 0,
            "blocks": [
                {
                    "sourceBlockId": "b1",
                    "kind": "text",
                    "order": 0,
                    "payload": {"text": "원문 제목"},
                }
            ],
        }
    ]
}
OUTLINE = {"slides": [{"sourceUnitId": "u1", "sourceIndex": 0, "readingOrder": ["b1"]}]}


def token_context():
    return context_from_source(
        manifest=MANIFEST,
        projection=OUTLINE,
        presentation_id="p1",
        operation_id="op1",
        slide_index=0,
        source_unit_id="u1",
        policy_revision=current_rollout_profile().policy_revision,
        fenced_revision=1,
        attempt_token="attempt-1",
        reference_mode="source_content_restyle",
        source_reflow=True,
        terminal_fallback_eligible=False,
        bindings=(("b1", "원문 제목"),),
        html_repr="token",
    )


def test_a_candidate_that_retypes_source_text_is_rejected_by_the_gate(monkeypatch):
    """The loop cannot emit source text: the gate's static checks refuse it."""

    from services.source_content.placeholder import text_step

    placeholder = RolloutProfile(placeholder_authoring=True)
    monkeypatch.setattr(gate, "current_rollout_profile", lambda: placeholder)
    monkeypatch.setattr(text_step, "current_rollout_profile", lambda: placeholder)
    retyped = gate.admit_slide_html(token_context(), ORIGINAL)
    assert retyped.verdict == "rejected"
    assert retyped.code == "source_placeholder_missing"
    tokenized = gate.admit_slide_html(
        token_context(), "<!doctype html><html><body><h1>{{q:b1}}</h1></body></html>"
    )
    assert tokenized.verdict == "admitted"
    assert "원문 제목" in tokenized.html


# --- B9: evidence-only VQA receipts attribute outcomes to the right stage --
