"""Deck master and SlideSpec v2 contracts."""

from .contract import DeckMasterContract, VisualDirection
from .operations import (
    CreateDeckRequest,
    CreateDraftRequest,
    MasterCasRequest,
    PreviewRequest,
    RegenerateBodyRequest,
)
from .slide import DeckSnapshot, SlideDraft, SlideSpecV2

__all__ = [
    "CreateDeckRequest",
    "CreateDraftRequest",
    "DeckMasterContract",
    "DeckSnapshot",
    "MasterCasRequest",
    "PreviewRequest",
    "RegenerateBodyRequest",
    "SlideDraft",
    "SlideSpecV2",
    "VisualDirection",
]
