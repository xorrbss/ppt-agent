"""Phase-3 per-call model tier seam (design doc section 10): get_model_for.

No caller adopts this yet; these tests exercise the seam itself so a future
call-site change can rely on its behaviour without re-deriving it.
"""

from utils.llm_provider import get_model_for
from utils.runtime_settings import RuntimeSettings


def _settings(**overrides) -> RuntimeSettings:
    return RuntimeSettings.from_sources(
        environ={},
        user_config={
            "LLM": "openai",
            "OPENAI_API_KEY": "key",
            "OPENAI_MODEL": "gpt-6-astra",
            **overrides,
        },
    )


def test_light_model_unset_keeps_every_task_on_the_main_model():
    settings = _settings()
    assert get_model_for("header_contract", settings) == "gpt-6-astra"
    assert get_model_for("content_brief", settings) == "gpt-6-astra"
    assert get_model_for("chapter_plan", settings) == "gpt-6-astra"
    assert get_model_for("outline_expansion", settings) == "gpt-6-astra"
    assert get_model_for("vision_advisory", settings) == "gpt-6-astra"
    assert get_model_for("author_slide", settings) == "gpt-6-astra"


def test_light_model_applies_only_to_the_registered_tasks():
    settings = _settings(LLM_LIGHT_MODEL="gpt-4.1-mini")
    for task in (
        "header_contract",
        "content_brief",
        "chapter_plan",
        "outline_expansion",
        "vision_advisory",
    ):
        assert get_model_for(task, settings) == "gpt-4.1-mini"

    # Not in the light-model task set: unaffected by the knob.
    assert get_model_for("author_slide", settings) == "gpt-6-astra"
    assert get_model_for("unknown_task", settings) == "gpt-6-astra"
