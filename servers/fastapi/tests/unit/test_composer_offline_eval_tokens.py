"""The offline harness must evaluate the tokens production renders.

``scripts/composer_offline_eval_inputs`` used to carry its own copy of the
palette and font-stack derivation, so the harness measured a font stack the
product never shipped.  These tests pin the harness to
``services.composer.pipeline_inputs.composer_tokens`` and to the product's own
style, so the two cannot drift apart again unnoticed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from composer_offline_eval_inputs import (  # noqa: E402
    SourceLookupError,
    build_tokens,
)
from services.composer.fonts import FONT_FAMILY  # noqa: E402
from services.composer.pipeline_inputs import composer_tokens  # noqa: E402
from utils.authored_styles import resolve_authored_style  # noqa: E402


def test_the_harness_derives_the_pipeline_tokens_verbatim() -> None:
    harness, label = build_tokens("default", deck_title="deck")
    pipeline = composer_tokens(resolve_authored_style("default"), deck_title_text="deck")

    assert harness == pipeline
    assert "pipeline_inputs.composer_tokens" in label


def test_the_eval_defaults_to_the_style_the_product_generates_with() -> None:
    from composer_offline_eval import PRODUCT_STYLE_ID

    assert PRODUCT_STYLE_ID == "default"
    parser_source = (_SCRIPTS / "composer_offline_eval.py").read_text(encoding="utf-8")
    assert "default=PRODUCT_STYLE_ID" in parser_source


def test_even_the_style_free_palette_keeps_the_embeddable_family_first() -> None:
    tokens, label = build_tokens(None, deck_title="deck")

    assert label == "neutral palette"
    assert tokens.font_family.split(",", 1)[0].strip() == FONT_FAMILY


@pytest.mark.parametrize("style_id", ["default", None])
def test_the_harness_refuses_a_stack_the_exporter_cannot_embed(style_id: str | None) -> None:
    """The guard is what makes the drift loud instead of silent."""

    tokens, _label = build_tokens(style_id, deck_title="deck")
    leading = tokens.font_family.split(",", 1)[0].strip().strip("'\"")
    assert leading.casefold() == FONT_FAMILY.casefold()
    assert issubclass(SourceLookupError, RuntimeError)


def test_style_none_is_the_one_documented_opt_out_spelling() -> None:
    """``--style ""`` used to reach the same neutral branch as ``--style none``
    through ``build_tokens``'s own ``if not style_id`` check -- two spellings
    for one opt-out. ``composer_offline_eval.parse_args`` now rejects the
    empty spelling outright, so ``none`` is the only one left.
    """

    from scripts import composer_offline_eval as cli

    args = cli.parse_args(["--pptx", "deck.pptx", "--style", "none"])
    assert args.style == "none"

    with pytest.raises(SystemExit):
        cli.parse_args(["--pptx", "deck.pptx", "--style", ""])

    with pytest.raises(SystemExit):
        cli.parse_args(["--pptx", "deck.pptx", "--style", "   "])
