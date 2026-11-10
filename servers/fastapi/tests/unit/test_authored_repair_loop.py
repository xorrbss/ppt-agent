"""One bounded repair loop: order, the N bound, rejection, knob, evidence.

Design ``docs/authored-verbatim-placeholder-authoring-design-20260911.md`` 2.5.
Everything here is offline: no provider call, no browser, no database.
"""

from __future__ import annotations

import asyncio
from functools import wraps

import pytest

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


# --- B9: evidence-only VQA receipts attribute outcomes to the right stage --
