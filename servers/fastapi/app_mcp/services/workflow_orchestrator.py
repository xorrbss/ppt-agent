from typing import Dict, Any, Optional, List
from dataclasses import asdict
from app_mcp.services.state_machine.machine import PresentationStateMachine
from app_mcp.services.state_machine.states import PresentationState
from utils.user_config import update_env_with_user_config
from app_mcp.wrapper.upload_and_generate_summary import upload_and_summarize_files
from app_mcp.wrapper.generate_outline import generate_outline
from app_mcp.wrapper.presentation_generation import process_post_outline_workflow
from app_mcp.wrapper.presentation_export import export_presentation_and_get_path
from app_mcp.wrapper.list_layout import list_layouts


class WorkflowOrchestrator:
    """
    Orchestrates the presentation generation workflow using FSM
    - Handles session management
    - Executes
        - file uploads
        - summary generation
        - outline generation
        - layout selection
        - presentation generation
        - export
    - Provides status and context management
    - Allows for session-based operations
    - Supports error handling and recovery
    """

    def __init__(self):
        """
        Initiating:
            - The environment with user configuration from the user config file.
            - The Finite State Machine (FSM) for presentation workflow.
            - Active sessions dictionary to manage multiple workflows.
        """
        try:
            update_env_with_user_config()
        except Exception as e:
            print(f"Error updating environment with user config: {e}")

        self.fsm = PresentationStateMachine()
        self._active_sessions: Dict[str, PresentationStateMachine] = {}

    def create_session(self, session_id: str) -> PresentationStateMachine:
        """
        Create a new workflow session with the given session ID.
        If a session with the same ID already exists, it will be replaced.
        Session will Remain for the lifetime of the application.
        Args:
            session_id (str): Unique identifier for the session.
        """
        if not session_id or not isinstance(session_id, str):
            raise ValueError("Session ID must be a non-empty string")

        session_id = session_id.strip()
        if not session_id:
            raise ValueError("Session ID cannot be empty")

        if session_id in self._active_sessions:
            self.remove_session(session_id)
            print(f"Session {session_id} already exists, replacing it.")

        self._active_sessions[session_id] = PresentationStateMachine()
        return self._active_sessions[session_id]

    def get_session(self, session_id: str) -> Optional[PresentationStateMachine]:
        """Get existing workflow session"""
        if not session_id or not isinstance(session_id, str):
            return None
        return self._active_sessions.get(session_id.strip())

    def remove_session(self, session_id: str) -> bool:
        """Remove workflow session"""
        return self._active_sessions.pop(session_id, None) is not None

    async def execute_upload_and_summarize(self, session_id: str, files: List[Any]) -> Dict[str, Any]:
        """
        Execute file upload and summary generation workflow step.
        Args:
            session_id (str): Unique identifier for the session.
            files (List[Any]): List of files to be uploaded and summarized.
        Returns:
            Dict[str, Any]: Result containing status, state, progress, next action, and any
        
        """
        fsm = self.get_session(session_id)
        if not fsm:
            raise ValueError(f"Session {session_id} not found")

        try:
            fsm.transition(PresentationState.FILES_UPLOADED)
            
            result = await upload_and_summarize_files(files)

            # Update context and transition to summary generated
            context_updates = {
                "summary": result["summary"],
                "file_paths": result["file_paths"]
            }
            fsm.transition(PresentationState.SUMMARY_GENERATED, context_updates)

            return {
                "status": "success",
                "state": fsm.state.name,
                "progress": fsm.get_workflow_progress(),
                "next_action": fsm.get_next_suggested_action(),
                "result": result
            }

        except Exception as e:
            fsm.transition(PresentationState.UPLOAD_FAILED, {"error_message": str(e)})
            print(f"There was an error uploading and summarizing files: {e}")
            return {
                "status": "error",
                "state": fsm.state.name,
                "error": str(e),
                "next_action": fsm.get_next_suggested_action()
            }

    async def execute_generate_outline(self, session_id: str, prompt: str, **kwargs) -> Dict[str, Any]:
        """
        Execute outline generation workflow step
        Args:
            session_id (str): Unique identifier for the session.
            prompt (str): The prompt to generate the outline.
            **kwargs: Additional parameters for outline generation.
        Returns:
            Dict[str, Any]: Result containing status, state, progress, next action, and generated outline.
        
        """
        fsm = self.get_session(session_id)
        if not fsm:
            raise ValueError(f"Session {session_id} not found")

        try:
            fsm.transition(PresentationState.OUTLINE_REQUESTED)

            
            result = await generate_outline(prompt, summary=fsm.context.summary, **kwargs)

            # Update the Context and transition to outline generated
            context_updates = {
                "title": result["title"],
                "outlines": result["outlines"]
            }
            fsm.transition(PresentationState.OUTLINE_GENERATED, context_updates)

            return {
                "status": "success",
                "state": fsm.state.name,
                "progress": fsm.get_workflow_progress(),
                "next_action": "Review outline and approve, or request regeneration",
                "result": result,
                "can_approve": True,
                "can_regenerate": True
            }

        except Exception as e:
            fsm.transition(PresentationState.OUTLINE_FAILED, {"error_message": str(e)})
            print(f"Error generating outline for session {session_id}: {e}")
            return {
                "status": "error",
                "state": fsm.state.name,
                "error": str(e),
                "next_action": fsm.get_next_suggested_action()
            }

    async def approve_outline(self, session_id: str) -> Dict[str, Any]:
        """
        Approve the generated outline
        Args:
            session_id (str): Unique identifier for the session.
        Returns:
            Dict[str, Any]: Result containing status, state, progress, next action.
        """
        fsm = self.get_session(session_id)
        if not fsm:
            raise ValueError(f"Session {session_id} not found")

        if fsm.state != PresentationState.OUTLINE_GENERATED:
            raise ValueError(f"Cannot approve outline in state {fsm.state.name}")

        fsm.transition(PresentationState.OUTLINE_APPROVED)

        return {
            "status": "success",
            "state": fsm.state.name,
            "progress": fsm.get_workflow_progress(),
            "next_action": fsm.get_next_suggested_action()
        }

    async def execute_layout_selection(self, session_id: str, layout: str) -> Dict[str, Any]:
        """
        Execute layout selection workflow step
        Args:
            session_id (str): Unique identifier for the session.
            layout (str): Selected layout for the presentation.
        Returns:
            Dict[str, Any]: Result containing status, state, progress, next action, and selected layout.
        """
        fsm = self.get_session(session_id)
        if not fsm:
            raise ValueError(f"Session {session_id} not found")

        try:
            fsm.transition(PresentationState.LAYOUT_REQUESTED)

            #Updating the context and transitioning to LAYOUT_SELECTED
            context_updates = {"layout": layout}
            fsm.transition(PresentationState.LAYOUT_SELECTED, context_updates)

            return {
                "status": "success",
                "state": fsm.state.name,
                "progress": fsm.get_workflow_progress(),
                "next_action": fsm.get_next_suggested_action(),
                "selected_layout": layout
            }

        except Exception as e:
            print(f"Error selecting layout for session {session_id}: {e}")
            return {
                "status": "error",
                "error": str(e),
                "next_action": "Please select a valid layout"
            }

    async def execute_presentation_generation(self, session_id: str, **kwargs) -> Dict[str, Any]:
        """
        Execute presentation generation workflow step
        Args:
            session_id (str): Unique identifier for the session.
            **kwargs: Additional parameters for presentation generation.
        Returns:
            Dict[str, Any]: Result containing status, state, progress, next action, and generated presentation.
        """
        fsm = self.get_session(session_id)
        if not fsm:
            raise ValueError(f"Session {session_id} not found")

        try:
            fsm.transition(PresentationState.GENERATION_IN_PROGRESS)

            
            notes = kwargs.get('notes', [])
            result = await process_post_outline_workflow(
                title=fsm.context.title,
                outlines=fsm.context.outlines,
                notes=notes,
                layout=fsm.context.layout,
                prompt=fsm.context.metadata.get('original_prompt', ""),
                sql_session=None,
                **kwargs
            )
            #Updating the Context and transitioning to PRESENTATION_READY
            context_updates = {"presentation_id": result["presentation_id"]}
            fsm.transition(PresentationState.PRESENTATION_READY, context_updates)

            return {
                "status": "success",
                "state": fsm.state.name,
                "progress": fsm.get_workflow_progress(),
                "next_action": fsm.get_next_suggested_action(),
                "result": result
            }

        except Exception as e:
            fsm.transition(PresentationState.GENERATION_FAILED, {"error_message": str(e)})
            print(f"Error generating presentation for session {session_id}: {e}")
            return {
                "status": "error",
                "state": fsm.state.name,
                "error": str(e),
                "next_action": fsm.get_next_suggested_action()
            }

    async def execute_export(self, session_id: str, export_format: str = "pptx") -> Dict[str, Any]:
        """
        Execute presentation export workflow step
        Args:
            session_id (str): Unique identifier for the session.
            export_format (str): Format to export the presentation (e.g., "pptx", "pdf").
        Returns:
            Dict[str, Any]: Result containing status, state, progress, next action, and export
        """
        fsm = self.get_session(session_id)
        if not fsm:
            raise ValueError(f"Session {session_id} not found")

        try:
            # Transition to EXPORT_REQUESTED state
            fsm.transition(PresentationState.EXPORT_REQUESTED, {"export_format": export_format})
            fsm.transition(PresentationState.EXPORT_IN_PROGRESS)

            result = await export_presentation_and_get_path(
                presentation_id=fsm.context.presentation_id,
                title=fsm.context.title,
                export_as=export_format
            )
            print("RResult of export:", result)

            #Updating the Context and transitioning to EXPORT_COMPLETE
            context_updates = {"export_path": result["path"]}
            fsm.transition(PresentationState.EXPORT_COMPLETE, context_updates)

            return {
                "status": "success",
                "state": fsm.state.name,
                "progress": fsm.get_workflow_progress(),
                "next_action": "Download your presentation or start a new one",
                "result": result
            }

        except Exception as e:
            fsm.transition(PresentationState.EXPORT_FAILED, {"error_message": str(e)})
            print(f"Error exporting presentation for session {session_id}: {e}")
            return {
                "status": "error",
                "state": fsm.state.name,
                "error": str(e),
                "next_action": fsm.get_next_suggested_action()
            }

    async def get_available_layouts(self) -> List[Any]:
        """
        Get available presentation layouts
        """
        return await list_layouts()

    def get_workflow_status(self, session_id: str) -> Dict[str, Any]:
        """Get current workflow status"""
        fsm = self.get_session(session_id)
        if not fsm:
            return {"error": "Session not found"}

        return {
            "session_id": session_id,
            "current_state": fsm.state.name,
            "progress": fsm.get_workflow_progress(),
            "next_action": fsm.get_next_suggested_action(),
            "available_transitions": [s.name for s in fsm.get_available_transitions()],
            "is_error_state": fsm.is_error_state(),
            "context": asdict(fsm.context),
            "state_history": [s.name for s in fsm.get_state_history()]
        }

    def get_all_sessions(self) -> Dict[str, Dict[str, Any]]:
        """
        Get status of all active sessions
        """
        return {
            session_id: self.get_workflow_status(session_id)
            for session_id in self._active_sessions.keys()
        }
