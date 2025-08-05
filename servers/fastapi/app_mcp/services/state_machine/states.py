from enum import Enum, auto

class PresentationState(Enum):
    """
    Represents the various states in the presentation workflow.
    """
    INIT = auto()
    
    # Outline generation phase (now includes file processing)
    OUTLINE_REQUESTED = auto()
    OUTLINE_GENERATED = auto()
    OUTLINE_APPROVED = auto()

    # Layout selection phase
    LAYOUT_REQUESTED = auto()
    LAYOUT_SELECTED = auto()

    # Presentation generation phase
    GENERATION_IN_PROGRESS = auto()
    PRESENTATION_READY = auto()

    # Export phase
    EXPORT_REQUESTED = auto()
    EXPORT_IN_PROGRESS = auto()
    EXPORT_COMPLETE = auto()

    # Edit and revision loops
    EDIT_REQUESTED = auto()
    TEMPLATE_EDITING = auto()

    # Error states
    OUTLINE_FAILED = auto()
    GENERATION_FAILED = auto()
    EXPORT_FAILED = auto()
    EDIT_FAILED = auto()
