from typing import Dict, Any, Optional, List
from app_mcp.tools.continue_workflow import register_continue_workflow
from app_mcp.services.state_machine.states import PresentationState


def register_regenerate_outline(mcp, orchestrator):
    """Register all workflow-related tools for chat-based interaction"""

    @mcp.tool("regenerate_outline")
    async def regenerate_outline(
        session_id: str,
        new_prompt: Optional[str] = None,
        n_slides: Optional[int] = None,
        language: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        🔄 Create a new outline with different requirements.

        Not happy with the generated outline? Use this to:
        - Try a different angle or focus for your topic
        - Change the number of slides
        - Adjust the language or tone
        - Incorporate new requirements

        Args:
            session_id: Your presentation session ID
            new_prompt: New description of what you want (optional)
            n_slides: Different number of slides (optional)
            language: Different language (optional)
        """
        try:
            fsm = orchestrator.get_session(session_id)
            if not fsm:
                return {"status": "error", "error": "Session not found"}

            # Update parameters if provided
            if new_prompt:
                fsm.context.metadata["original_prompt"] = new_prompt
            if n_slides:
                fsm.context.metadata["n_slides"] = n_slides
            if language:
                fsm.context.metadata["language"] = language

            # Reset to outline generation
            if fsm.can_transition_to(PresentationState.OUTLINE_REQUESTED):
                fsm.transition(PresentationState.OUTLINE_REQUESTED)

            # Generate new outline
            continue_workflow = register_continue_workflow(mcp, orchestrator)
            result = await continue_workflow(session_id=session_id, action="continue")

            if result["status"] == "success":
                result["message"] = "I've created a new outline for you:"

            return result
        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
                "session_id": session_id
            }

    return regenerate_outline