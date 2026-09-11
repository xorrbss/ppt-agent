"""Prompt switch, table semantics and the real 43-page size contract."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.source_content.placeholder import prompt, text_step
from services.source_content.placeholder.block_table import (
    build_block_table, encode_block_table, placeholder_prompt_contract, table_from_manifest,
)
from utils.get_env_programme import is_authored_verbatim_placeholder_authoring_enabled
from utils.runtime_settings import RuntimeSettings

FIXTURES = Path(__file__).resolve().parents[4] / "artifacts/verbatim-design-measurements-20260911/fixtures"


def test_block_table_projection_semantics():
    table = build_block_table({"a": "• 안녕\n↓\n질문 | 답변", "b": "e\u0301가"})
    row = table[0].prompt_row()
    assert row["fullText"] == "• 안녕\n↓\n질문 | 답변"
    assert row["text"] == "안녕 질문 | 답변"
    assert row["hardBreaks"] == [3]
    assert row["markers"] == [[0, "• "]]
    assert row["pipes"] == [8]
    assert table[1].prompt_row()["allowedBoundaries"] == [[0, 0], [2, 3]]
    contract = placeholder_prompt_contract(table)
    assert "whole token XOR" in contract and "PAGE MARKER" in contract


def test_table_rejects_duplicate_server_ids():
    with pytest.raises(ValueError, match="source_placeholder_duplicate"):
        build_block_table([("a", "one"), ("a", "two")])


@pytest.mark.parametrize("value, expected", [(None, False), (False, False), (True, True)])
def test_default_off_accessor(value, expected):
    settings = RuntimeSettings({"AUTHORED_VERBATIM_PLACEHOLDER_AUTHORING": value})
    assert is_authored_verbatim_placeholder_authoring_enabled(settings) is expected


def test_43_page_real_manifest_size_limit():
    directory = FIXTURES / "manifest-43p"
    readme = json.loads((directory / "README.json").read_text(encoding="utf-8"))
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    assert readme["textPayloadFingerprint"] == "d3839b142c32"
    assert len(manifest["units"]) == 43
    table = table_from_manifest(manifest)
    assert len(table) == 965
    assert sum(block.projection.run_count for block in table) == 1317
    assert len(encode_block_table(table)) > 64_000
    with pytest.raises(ValueError, match="split by source unit"):
        placeholder_prompt_contract(table)
    for unit in manifest["units"]:
        assert len(encode_block_table(table_from_manifest(unit))) < 64_000


# --- R4: prompt-construction rejections must not leak uncoded ---------------


def test_placeholder_prompt_boundary_recodes_a_duplicate_rejection(monkeypatch):
    """A ``reject()``ed ``BrowserSourceReadbackError`` from ``build_block_table``
    must not leak out of prompt construction as a readback-shaped exception:
    the boundary re-codes it into a bounded, explicitly non-retryable failure
    the author stage can map directly to a coded slide outcome."""

    import services.authored_admission_session as session_module
    from services.source_content.placeholder.prompt import (
        AuthoredPlaceholderPromptRejected,
        placeholder_prompt_for_slide,
    )

    context = SimpleNamespace(
        source_reflow=True,
        reference_mode="source_content_restyle",
        bindings=(("a", "one"), ("a", "two")),
    )
    monkeypatch.setattr(
        session_module, "current_session", lambda: SimpleNamespace(contexts=(context,))
    )
    monkeypatch.setattr(prompt, "is_authored_verbatim_placeholder_authoring_enabled", lambda: True)

    with pytest.raises(AuthoredPlaceholderPromptRejected) as excinfo:
        placeholder_prompt_for_slide(0)

    assert excinfo.value.code == "source_placeholder_duplicate"
    assert excinfo.value.retryable is False


def test_placeholder_prompt_boundary_recodes_the_size_bound(monkeypatch):
    """The 64,000-char table bound is a bare ``ValueError`` at the source
    (``placeholder_prompt_contract``); the prompt boundary must still recode
    it into a coded, non-retryable rejection instead of letting it escape as
    an uncaught, uncoded failure."""

    import services.authored_admission_session as session_module
    from services.source_content.placeholder.prompt import (
        AuthoredPlaceholderPromptRejected,
        placeholder_prompt_for_slide,
    )

    directory = FIXTURES / "manifest-43p"
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    bindings = tuple(
        (block["sourceBlockId"], block["payload"]["text"])
        for unit in manifest["units"]
        for block in unit.get("blocks", ())
        if block.get("kind") == "text"
    )
    context = SimpleNamespace(
        source_reflow=True, reference_mode="source_content_restyle", bindings=bindings,
    )
    monkeypatch.setattr(
        session_module, "current_session", lambda: SimpleNamespace(contexts=(context,))
    )
    monkeypatch.setattr(prompt, "is_authored_verbatim_placeholder_authoring_enabled", lambda: True)

    with pytest.raises(AuthoredPlaceholderPromptRejected) as excinfo:
        placeholder_prompt_for_slide(0)

    assert excinfo.value.code == "authored_placeholder_prompt_rejected"
    assert excinfo.value.retryable is False
    assert "split by source unit" in excinfo.value.detail


def test_prompt_switch_uses_pinned_slide_context(monkeypatch):
    import services.authored_admission_session as session_module
    from utils.llm_calls.author_slide_prompt import author_prompt

    context = SimpleNamespace(source_reflow=True, reference_mode="source_content_restyle", bindings=(("a", "안녕"),))
    monkeypatch.setattr(session_module, "current_session", lambda: SimpleNamespace(contexts=(context,)))
    monkeypatch.setattr(prompt, "is_authored_verbatim_placeholder_authoring_enabled", lambda: False)
    brand = SimpleNamespace(wordmark="", topic="topic")
    old = author_prompt("body", "design", brand, "BODY", 0, 2)
    assert "VERBATIM SOURCE PLACEHOLDERS" not in old
    monkeypatch.setattr(prompt, "is_authored_verbatim_placeholder_authoring_enabled", lambda: True)
    new = author_prompt("body", "design", brand, "BODY", 0, 2)
    assert placeholder_prompt_contract(build_block_table(context.bindings)) in new
    assert 'render exactly "01" once' in new


def test_text_step_switch_and_nonrecursive_expanded_lifecycle(monkeypatch):
    from services.authored_admission_context import AdmissionContext

    context = AdmissionContext(
        presentation_id="p", operation_id="o", slide_index=0, source_unit_id="u",
        manifest_digest="m", projection_digest="p", policy_revision="r", fenced_revision=1,
        attempt_token="t", reference_mode="source_content_restyle", source_reflow=True,
        terminal_fallback_eligible=False, bindings=(("a", "{{q:source-data}}"),), state="token",
    )
    monkeypatch.setattr(text_step, "is_authored_verbatim_placeholder_authoring_enabled", lambda: True)
    result = text_step.select_text_step(context, "<p>{{q:a}}</p>")
    assert "{{q:source-data}}" in result.html
    assert result.normalizations[0]["rule"] == "source_placeholder_expanded"
    assert text_step.select_text_step(context.for_state("expanded"), result.html).html == result.html
    assert text_step.select_text_step(context.for_state("captured"), result.html).html == result.html
    with pytest.raises(ValueError, match="source_placeholder_context"):
        text_step.check_placeholder_context(context, "<p hidden>{{q:a}}</p>")
    monkeypatch.setattr(text_step, "is_authored_verbatim_placeholder_authoring_enabled", lambda: False)
    legacy = text_step.select_text_step(context, "<p>{{q:source-data}}</p>")
    assert 'data-source-block-id="a"' in legacy.html
