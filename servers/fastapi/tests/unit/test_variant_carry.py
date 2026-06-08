"""B-3: the optional `variant` field is carried into the rendered content dict only
when set (so variant-less decks stay byte-identical), and the per-archetype enums
are enforced."""

import pytest
from pydantic import ValidationError

from models.slide_spec_model import (
    CoverSpec,
    SectionDividerSpec,
    StatHeroSpec,
    StatItem,
    spec_to_blocks,
)


def test_variant_omitted_when_unset_backward_compat():
    out = spec_to_blocks(CoverSpec(archetype="cover", title="Hello"))
    assert "variant" not in out
    assert set(out.keys()) == {"archetype", "blocks"}


def test_variant_carried_when_set():
    out = spec_to_blocks(
        SectionDividerSpec(archetype="section-divider", variant="bold", title="Part 2")
    )
    assert out["variant"] == "bold"


def test_stat_hero_featured_variant_carried():
    out = spec_to_blocks(
        StatHeroSpec(
            archetype="stat-hero",
            variant="featured",
            title="Outcomes",
            stats=[StatItem(value="+12%", label="OEE"), StatItem(value="-30%", label="Downtime")],
        )
    )
    assert out["variant"] == "featured"


def test_invalid_variant_rejected():
    with pytest.raises(ValidationError):
        SectionDividerSpec(archetype="section-divider", variant="rainbow", title="x")


def test_composer_prompt_lists_variant_menu():
    from utils.llm_calls.compose_slides import get_system_prompt

    prompt = get_system_prompt()
    assert "Composition Variants" in prompt
    assert '"left"' in prompt and '"bold"' in prompt and '"featured"' in prompt
