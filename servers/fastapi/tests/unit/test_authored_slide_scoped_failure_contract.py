"""A source-content rejection is one slide's failure, not the whole deck's.

Every slide is authored and checkpointed independently, and a retry re-authors
only the slides whose author stage is not complete.  For that to help, a slide
rejected after authoring -- source text the model dropped, a source picture it
did not place -- has to be recorded as a *retryable* author failure: the
handler in ``process_slide`` turns the raised ``AuthoredStageError`` into
``fail_stage(stage="author")``, which clears the slide's completed status and
stores the reason the next attempt replays as its correction.

Marked non-retryable those same failures ended the deck.  Operation 612d4b75
authored 35 of 35 slides and lost all of them because slide 28 omitted
"장기고객 만들기" from a header it was told to copy verbatim.

A behavioural test would have to drive the whole authoring pipeline, so this
reads the source and requires the contract at every such raise site.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PIPELINE = "services/authored_presentation_pipeline.py"

# Failures raised after a slide is authored, when its own rendered HTML does
# not carry the source it was given.  Re-authoring that one slide is the fix.
SLIDE_SCOPED_CODES = frozenset({
    "source_content_browser_readback_failed",
    "source_content_browser_readback_invalid",
})


def _service_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _source_binding_raises() -> list[ast.Call]:
    """Every ``AuthoredStageError`` raised from a source-binding handler."""

    source = (_service_root() / PIPELINE).read_text(encoding="utf-8")
    tree = ast.parse(source)
    found: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        handler_type = getattr(node.type, "id", None) or getattr(
            node.type, "attr", None
        )
        if handler_type != "BrowserSourceReadbackError":
            continue
        for inner in ast.walk(node):
            if (
                isinstance(inner, ast.Raise)
                and isinstance(inner.exc, ast.Call)
                and getattr(inner.exc.func, "id", None) == "AuthoredStageError"
            ):
                found.append(inner.exc)
    return found


def _keyword(call: ast.Call, name: str) -> ast.expr | None:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def test_a_source_binding_rejection_is_recorded_as_a_retryable_slide_failure() -> None:
    raises = _source_binding_raises()
    assert raises, "expected the pipeline to translate source-binding rejections"
    for call in raises:
        stage = _keyword(call, "stage")
        assert isinstance(stage, ast.Constant) and stage.value == "author", (
            "a source-binding rejection must fail the slide's author stage so "
            "the retry re-authors it"
        )
        retryable = _keyword(call, "retryable")
        assert isinstance(retryable, ast.Constant) and retryable.value is True, (
            "marking this non-retryable throws away every other authored slide"
        )
        assert _keyword(call, "slide_index") is not None
        assert _keyword(call, "cause") is not None, (
            "the cause carries the reason the next attempt corrects"
        )


@pytest.mark.parametrize("code", sorted(SLIDE_SCOPED_CODES))
def test_slide_scoped_codes_stay_named_in_the_readback_contract(code: str) -> None:
    readback = (
        _service_root() / "services/source_content/browser_readback.py"
    ).read_text(encoding="utf-8")
    stamping = (
        _service_root() / "services/source_content/text_stamping.py"
    ).read_text(encoding="utf-8")
    assert code in readback or code in stamping
