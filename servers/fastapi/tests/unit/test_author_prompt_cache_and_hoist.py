"""G8: one cache lane per deck, and the invariant block in the system message.

The two halves are independent knobs: ``AUTHORED_PROMPT_CACHE_KEY_ENABLED``
names the lane, ``AUTHORED_PROMPT_SYSTEM_HOIST_ENABLED`` makes a lane exist by
giving every slide of the deck a byte-identical prefix.
"""

import pytest

from services.generation_core.authoring import author_batch as batch_mod
from utils.llm_calls.author_prompt_cache_key import (
    apply_prompt_cache_key,
    current_prompt_cache_key,
    deck_prompt_cache_key,
    set_prompt_cache_key,
)
from utils.llm_calls.author_prompt_split import (
    author_messages,
    split_deck_invariant,
)
from services.generation_core.authoring.author_slide import Brand
from services.generation_core.authoring.author_slide_prompt import author_prompt


def _brand():
    return Brand(topic="cache", language="Korean", primary="#2563EB", fonts="Inter")


# --- the cache key ------------------------------------------------------------


def test_deck_key_is_stable_per_design_system_and_absent_without_one():
    first = deck_prompt_cache_key("DECK DESIGN SYSTEM")
    assert first == deck_prompt_cache_key("DECK DESIGN SYSTEM")
    assert first != deck_prompt_cache_key("ANOTHER DESIGN SYSTEM")
    assert first.startswith("authored-") and len(first) == len("authored-") + 32
    assert deck_prompt_cache_key("") is None
    assert deck_prompt_cache_key(None) is None


def test_bound_key_reaches_extra_body_and_keeps_the_reasoning_dict(monkeypatch):
    monkeypatch.setenv("LLM", "codex")
    kwargs = {"model": "m", "extra_body": {"reasoning": {"effort": "medium"}}}
    with set_prompt_cache_key("authored-deck-1"):
        assert current_prompt_cache_key() == "authored-deck-1"
        apply_prompt_cache_key(kwargs)
    assert kwargs["extra_body"]["prompt_cache_key"] == "authored-deck-1"
    assert kwargs["extra_body"]["reasoning"] == {"effort": "medium"}
    # The binding is scoped: outside the block nothing is added.
    plain = {"model": "m"}
    apply_prompt_cache_key(plain)
    assert "extra_body" not in plain


def test_no_key_is_sent_when_the_knob_is_off(monkeypatch):
    monkeypatch.setenv("AUTHORED_PROMPT_CACHE_KEY_ENABLED", "false")
    kwargs = {"model": "m"}
    with set_prompt_cache_key("authored-deck-1"):
        assert current_prompt_cache_key() is None
        apply_prompt_cache_key(kwargs)
    assert kwargs == {"model": "m"}


def test_a_call_without_reasoning_gains_only_the_key(monkeypatch):
    monkeypatch.setenv("LLM", "openai")
    kwargs = {"model": "m"}
    with set_prompt_cache_key("authored-deck-2"):
        apply_prompt_cache_key(kwargs)
    assert kwargs["extra_body"] == {"prompt_cache_key": "authored-deck-2"}


def test_providers_that_reject_the_field_never_receive_it(monkeypatch):
    """Anthropic answers 400 'prompt_cache_key: Extra inputs are not permitted'."""
    monkeypatch.setenv("LLM", "anthropic")
    kwargs = {"model": "m"}
    with set_prompt_cache_key("authored-deck-3"):
        apply_prompt_cache_key(kwargs)
    assert kwargs == {"model": "m"}


# --- the system hoist ---------------------------------------------------------


_DESIGN = "DECK DESIGN SYSTEM: palette, type scale, spacing."


def test_single_path_prompt_splits_and_rejoins_exactly():
    prompt = author_prompt(
        "슬라이드 본문", _DESIGN, _brand(), "CONTENT", 2, 10
    )
    head, rest = split_deck_invariant(prompt, _DESIGN)

    assert head == _DESIGN
    assert head + "\n\n" + rest == prompt
    assert rest.startswith("Author slide 3 of 10.")


def test_batch_path_prompt_splits_and_rejoins_exactly():
    request = batch_mod.AuthoredBatchSlideRequest(
        index=1, content="슬라이드 본문", role="CONTENT"
    )
    prepared = batch_mod._prepare_slide(
        request, design_system=_DESIGN, brand=_brand(), slide_count=6
    )
    head, rest = split_deck_invariant(prepared.prompt, _DESIGN)

    assert head == _DESIGN
    assert head + "\n\n" + rest == prepared.prompt
    # The full user-side text stays on the prepared slide for its own readers.
    assert prepared.prompt.startswith(_DESIGN)


def test_a_prompt_that_does_not_open_with_the_block_is_left_alone():
    assert split_deck_invariant("no design system here", _DESIGN) == (
        "",
        "no design system here",
    )
    assert split_deck_invariant("anything", "") == ("", "anything")
    assert split_deck_invariant("anything", None) == ("", "anything")


def test_author_messages_move_the_block_without_changing_the_text():
    prompt = author_prompt("본문", _DESIGN, _brand(), "CONTENT", 0, 4)
    hoisted = author_messages("SYSTEM", prompt, _DESIGN, enabled=True)
    flat = author_messages("SYSTEM", prompt, _DESIGN, enabled=False)

    assert hoisted[0].content == "SYSTEM\n\n" + _DESIGN
    assert hoisted[1].content + "" == prompt[len(_DESIGN) + 2 :]
    assert hoisted[0].content + "\n\n" + hoisted[1].content == "SYSTEM\n\n" + prompt
    # Knob off: exactly today's two messages.
    assert flat[0].content == "SYSTEM"
    assert flat[1].content == prompt


def test_author_messages_keep_image_parts_on_the_user_message():
    from llmai.shared import TextContentPart

    prompt = author_prompt("본문", _DESIGN, _brand(), "CONTENT", 0, 4)
    part = TextContentPart(text="reference image stand-in")
    messages = author_messages(
        "SYSTEM", prompt, _DESIGN, image_parts=(part,), enabled=True
    )

    assert isinstance(messages[1].content, list)
    assert messages[1].content[-1].text == "reference image stand-in"
    assert messages[1].content[0].text == prompt[len(_DESIGN) + 2 :]


@pytest.mark.parametrize("enabled", [True, False])
def test_every_slide_of_a_deck_shares_the_hoisted_system_message(enabled):
    system = {
        author_messages(
            "SYSTEM",
            author_prompt(f"본문 {index}", _DESIGN, _brand(), "CONTENT", index, 4),
            _DESIGN,
            enabled=enabled,
        )[0].content
        for index in range(4)
    }
    assert len(system) == 1
