"""Published-master-aware authored generation planning."""

from .outline import (
    BodySlotModelInput,
    GenerationOutlinePlan,
    OutlineItem,
    PlannedSlide,
    SlideRole,
    plan_outline,
)
from .title_fit import (
    PlannedTitle,
    TitleFitPlanningError,
    break_title,
    fit_and_promote_title,
    resolve_windows_font,
)
from .core_bridge import (
    AuthoringCoreBridgePort,
    CoreBridgeArtifact,
    CoreBridgeCheckpoint,
    DeterministicCoreBridge,
    OoxmlCoreBridgeAdapter,
)
from .repository import InMemoryGenerationRepository
from .pipeline import AuthoredVNextGenerationPipeline

__all__ = [
    "BodySlotModelInput",
    "GenerationOutlinePlan",
    "OutlineItem",
    "PlannedSlide",
    "PlannedTitle",
    "SlideRole",
    "TitleFitPlanningError",
    "break_title",
    "fit_and_promote_title",
    "plan_outline",
    "resolve_windows_font",
    "AuthoringCoreBridgePort",
    "AuthoredVNextGenerationPipeline",
    "CoreBridgeArtifact",
    "CoreBridgeCheckpoint",
    "DeterministicCoreBridge",
    "InMemoryGenerationRepository",
    "OoxmlCoreBridgeAdapter",
]
