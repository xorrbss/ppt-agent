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
from .mutations import mutate_scene_element, reorder_deck_slides
from .scene_compiler import apply_scene_element_mutation, compile_deck_scene, compile_slide_scene

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
    "apply_scene_element_mutation",
    "compile_deck_scene",
    "compile_slide_scene",
    "mutate_scene_element",
    "reorder_deck_slides",
]
