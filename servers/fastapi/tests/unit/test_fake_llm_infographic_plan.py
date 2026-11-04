"""The fake-LLM harness answers the deck-scoped infographic plan call."""
from __future__ import annotations

import importlib.util
from pathlib import Path

from llmai.shared import JSONSchemaResponse

from utils.llm_calls.author_infographic_plan import _response_schema, build_infographic_plan_messages
from models.authored_infographic_plan import coerce_infographic_plan

HARNESS = Path(__file__).resolve().parents[2] / "scripts/fake_llm_e2e/fake_llm_backend.py"


def _harness():
    spec = importlib.util.spec_from_file_location("fake_llm_backend_under_test", HARNESS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_harness_answers_the_infographic_plan_call_with_a_usable_plan():
    harness = _harness()
    roles = ["COVER", "CONTENT", "CONTENT", "CLOSING"]
    contents = [
        "# 검증 슬라이드 1\n- 첫째 요점 1\n- 둘째 요점 1",
        "# 검증 슬라이드 2\n- 첫째 요점 2\n- 둘째 요점 2",
        "# 검증 슬라이드 3\n- 첫째 요점 3\n- 둘째 요점 3",
        "# 검증 슬라이드 4\n- 첫째 요점 4\n- 둘째 요점 4",
    ]
    messages = build_infographic_plan_messages(
        topic="검증", language="Korean", slide_contents=contents, roles=roles,
    )
    schema = _response_schema()
    response = harness.FakeClient().generate(
        messages=messages,
        response_format=JSONSchemaResponse(name="authored_infographic_plan", json_schema=schema, strict=False),
    )
    assert "infographic_plan" in harness.CALLS
    plan = coerce_infographic_plan(response.content, slide_contents=contents, roles=roles)
    assert [slide.archetype == "none" for slide in plan.slides] == [True, False, False, True]
    assert all(len(slide.nodes) == 3 for slide in plan.slides[1:3])
    assert plan.figure_count == 2
