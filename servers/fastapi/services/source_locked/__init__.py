"""Versioned, fail-closed contracts for source-locked generation."""

from services.source_locked.analysis import analyze_source
from services.source_locked.contracts import (
    AdmissionContractV1,
    GenerationMode,
    PageCountPolicy,
    SourceLockedAnalysisResponseV1,
)

__all__ = [
    "AdmissionContractV1",
    "GenerationMode",
    "PageCountPolicy",
    "SourceLockedAnalysisResponseV1",
    "analyze_source",
]
