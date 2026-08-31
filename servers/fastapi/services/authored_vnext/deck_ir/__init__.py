"""Strict Deck IR v1 contract boundary."""

from .canonical import canonical_digest, canonical_json, derive_qid, derive_qid_registry, seal_contract
from .contracts import *  # noqa: F403
from .validation import (
    DeckIRContractError,
    parse_contract,
    validate_critic_patch,
    validate_deck_ir,
    validate_requirement_coverage,
    validate_revision_immutability,
)

__all__ = [
    "DeckIRContractError",
    "canonical_digest",
    "canonical_json",
    "derive_qid",
    "derive_qid_registry",
    "parse_contract",
    "seal_contract",
    "validate_critic_patch",
    "validate_deck_ir",
    "validate_requirement_coverage",
    "validate_revision_immutability",
]
