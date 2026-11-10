"""G8: one cache lane per deck, and the invariant block in the system message.

The two halves are independent knobs: ``AUTHORED_PROMPT_CACHE_KEY_ENABLED``
names the lane, ``AUTHORED_PROMPT_SYSTEM_HOIST_ENABLED`` makes a lane exist by
giving every slide of the deck a byte-identical prefix.
"""


from utils.llm_calls.author_prompt_cache_key import (
    apply_prompt_cache_key,
    current_prompt_cache_key,
    deck_prompt_cache_key,
    set_prompt_cache_key,
)
from utils.llm_calls.author_prompt_split import (
    split_deck_invariant,
)


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


def test_a_prompt_that_does_not_open_with_the_block_is_left_alone():
    assert split_deck_invariant("no design system here", _DESIGN) == (
        "",
        "no design system here",
    )
    assert split_deck_invariant("anything", "") == ("", "anything")
    assert split_deck_invariant("anything", None) == ("", "anything")


