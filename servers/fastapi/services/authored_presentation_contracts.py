from dataclasses import dataclass
from typing import Any, List

from utils.llm_calls.author_deck import AuthoredDeckResult
from services.authored_quality_receipt_v2 import public_attempt_id


AUTHORED_TEMPLATE = "authored"


@dataclass(frozen=True)
class AuthoredGenerationPipelineResult:
    deck: AuthoredDeckResult
    pngs: List[bytes]
    elapsed: float
    author_work: float
    illustration_work: float
    render_work: float
    quality_work: float = 0.0
    # Deterministic DOM safety findings are returned to the generation owner so
    # it can repair only the affected slides.  Capacity/telemetry admission
    # failures remain hard errors in the pipeline; geometry defects are bounded
    # repair candidates and must not force a whole-deck regeneration.
    quality_gate_reports: tuple[Any, ...] = ()
    quality_gate_failed_indices: tuple[int, ...] = ()
    # JSON-safe form consumed by the generation repair boundary.
    quality_gate_failures: tuple[dict[str, Any], ...] = ()
    # Attempt scoped evidence survives renderer cache eviction and is sealed
    # before the generation owner builds its publication receipt.
    evidence_ledger: Any | None = None
    evidence_attempt_id: str | None = None
    # One immutable authority sealed before Chromium. Publication/export may
    # derive geometry from its HTML projection, but may not recompile meaning
    # or QID ownership from a different source.
    scene_envelope: Any | None = None
    # Per-slide provider calls already consumed by the first-pass author. A
    # resumed checkpoint is conservatively treated as budget-exhausted because
    # its historical call count is not encoded in the checkpoint row.
    author_provider_attempt_counts: tuple[int, ...] = ()
    def __post_init__(self) -> None:
        # The result is a public boundary consumed by API/publication code.
        # Never propagate a worker lease or raw generation token through it.
        if self.evidence_attempt_id:
            object.__setattr__(self, "evidence_attempt_id", public_attempt_id(self.evidence_attempt_id))

