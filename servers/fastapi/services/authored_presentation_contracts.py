from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, List, Mapping

from utils.llm_calls.author_deck import AuthoredDeckResult
from services.authored_quality_receipt_v2 import public_attempt_id

if TYPE_CHECKING:  # pragma: no cover - annotation only
    from services.authored_vnext.generation.scene_envelope import AuthoredSlideSceneEnvelope


AUTHORED_TEMPLATE = "authored"

#: One slide's result from the author/illustrate/render pipeline, read
#: positionally by ``_author_illustrate_render_pipeline``: index, HTML of
#: record, PNG, author/illustration/render/quality seconds, DOM quality report,
#: quality failure, scene envelope, author provider attempts, per-slide quality
#: receipt, fired normalization rules, in-render fit report.
SlideStageResult = tuple[
    int,
    str,
    bytes,
    float,
    float,
    float,
    float,
    Any | None,
    dict[str, Any] | None,
    "AuthoredSlideSceneEnvelope",
    int,
    Mapping[str, Any] | None,
    Any,
    Mapping[str, Any] | None,
]


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
    # What the Stage 4 quality loop chose for each figure slide, keyed by final
    # outline index. Empty when the loop is off or chose nothing to report; the
    # measurement stage reads ``variant_chosen`` and ``critic_score`` from it.
    slide_quality_receipts: Mapping[int, dict[str, Any]] = field(
        default_factory=dict
    )
    # Per-slide in-render-fit report (``docs/authored-in-render-fit-design-
    # 20260906.md`` section 4), keyed by final outline index. Only slides the
    # fitter actually touched during the draft render carry an entry; the
    # remake/rung admission path does not report here (v1 scope).
    slide_fit_reports: Mapping[int, dict[str, Any]] = field(default_factory=dict)
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
    # D7 at the author stage: slides that spent every author attempt and are
    # published with a deterministic placeholder instead of failing the deck.
    # Index-ordered, and merged into the deck's one ``flaggedSlides`` list next
    # to the gate-stage flags so every consumer sees one kind of flagged page.
    author_flagged_slides: tuple[dict[str, Any], ...] = ()
    # F3: seconds this attempt spent sleeping out a provider cooldown before an
    # authoring admission. Zero on a run that never met an overload. The attempt
    # timing receipt records it as ``cooldownWaitSeconds``.
    cooldown_wait_seconds: float = 0.0

    def __post_init__(self) -> None:
        # The result is a public boundary consumed by API/publication code.
        # Never propagate a worker lease or raw generation token through it.
        if self.evidence_attempt_id:
            object.__setattr__(self, "evidence_attempt_id", public_attempt_id(self.evidence_attempt_id))

