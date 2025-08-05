from app_mcp.services.state_machine.states import PresentationState

TRANSITIONS = {
    PresentationState.INIT: {
        PresentationState.OUTLINE_REQUESTED
    },

    # Outline generation flow (now includes file processing)
    PresentationState.OUTLINE_REQUESTED: {
        PresentationState.OUTLINE_GENERATED,
        PresentationState.OUTLINE_FAILED
    },
    PresentationState.OUTLINE_GENERATED: {
        PresentationState.OUTLINE_APPROVED,
        PresentationState.OUTLINE_REQUESTED,
        PresentationState.OUTLINE_FAILED
    },
    PresentationState.OUTLINE_APPROVED: {
        PresentationState.LAYOUT_REQUESTED
    },

    # Layout selection flow
    PresentationState.LAYOUT_REQUESTED: {
        PresentationState.LAYOUT_SELECTED
    },
    PresentationState.LAYOUT_SELECTED: {
        PresentationState.GENERATION_IN_PROGRESS,
        PresentationState.LAYOUT_REQUESTED
    },

    # Presentation generation flow
    PresentationState.GENERATION_IN_PROGRESS: {
        PresentationState.PRESENTATION_READY,
        PresentationState.GENERATION_FAILED
    },
    PresentationState.PRESENTATION_READY: {
        PresentationState.EXPORT_REQUESTED,
        PresentationState.EDIT_REQUESTED,
        PresentationState.OUTLINE_REQUESTED
    },

    # Export flow
    PresentationState.EXPORT_REQUESTED: {
        PresentationState.EXPORT_IN_PROGRESS
    },
    PresentationState.EXPORT_IN_PROGRESS: {
        PresentationState.EXPORT_COMPLETE,
        PresentationState.EXPORT_FAILED
    },
    PresentationState.EXPORT_COMPLETE: {
        PresentationState.EDIT_REQUESTED,
        PresentationState.EXPORT_REQUESTED,
        PresentationState.INIT
    },

    # Edit and revision flow
    PresentationState.EDIT_REQUESTED: {
        PresentationState.TEMPLATE_EDITING
    },
    PresentationState.TEMPLATE_EDITING: {
        PresentationState.PRESENTATION_READY,
        PresentationState.EDIT_FAILED
    },

    # Error recovery transitions
    PresentationState.OUTLINE_FAILED: {
        PresentationState.OUTLINE_REQUESTED,
        PresentationState.INIT
    },
    PresentationState.GENERATION_FAILED: {
        PresentationState.LAYOUT_SELECTED,
        PresentationState.OUTLINE_APPROVED
    },
    PresentationState.EXPORT_FAILED: {
        PresentationState.EXPORT_REQUESTED,
        PresentationState.PRESENTATION_READY
    },
    PresentationState.EDIT_FAILED: {
        PresentationState.EDIT_REQUESTED,
        PresentationState.PRESENTATION_READY
    }
}


SUGGESTIONS = {
    PresentationState.INIT: "Start with outline generation (files will be processed automatically if provided)",
    PresentationState.OUTLINE_REQUESTED: "Generating presentation outline with file analysis if applicable",
    PresentationState.OUTLINE_GENERATED: "Review and approve outline",
    PresentationState.OUTLINE_APPROVED: "Select presentation layout",
    PresentationState.LAYOUT_SELECTED: "Generate presentation",
    PresentationState.PRESENTATION_READY: "Export presentation or request edits",
    PresentationState.EXPORT_REQUESTED: "Choose export format and generate",
    PresentationState.EXPORT_COMPLETE: "Download presentation or start new one",
    PresentationState.EDIT_REQUESTED: "Make template-based edits",
}


PROGRESS_WEIGHTS = {
    PresentationState.INIT: 0,
    PresentationState.OUTLINE_REQUESTED: 20,
    PresentationState.OUTLINE_GENERATED: 35,
    PresentationState.OUTLINE_APPROVED: 40,
    PresentationState.LAYOUT_REQUESTED: 45,
    PresentationState.LAYOUT_SELECTED: 50,
    PresentationState.GENERATION_IN_PROGRESS: 70,
    PresentationState.PRESENTATION_READY: 85,
    PresentationState.EXPORT_REQUESTED: 90,
    PresentationState.EXPORT_IN_PROGRESS: 95,
    PresentationState.EXPORT_COMPLETE: 100,
    PresentationState.TEMPLATE_EDITING: 60,
}


ERROR_STATES = {
    PresentationState.OUTLINE_FAILED,
    PresentationState.GENERATION_FAILED,
    PresentationState.EXPORT_FAILED,
    PresentationState.EDIT_FAILED
}
