from typing import Dict, Any, Optional, List


def register_get_status(mcp, orchestrator):
    """Register all workflow-related tools for chat-based interaction"""

    @mcp.tool("get_status")
    def get_status(session_id: str) -> Dict[str, Any]:
        """
        📊 Check your presentation creation progress.

        See exactly where you are in the process:
        - What step you're currently on
        - How much progress you've made
        - What you can do next
        - Any issues that need attention

        Perfect for checking in if you're unsure what to do next!

        Args:
            session_id: Your presentation session ID
        """
        try:
            if not session_id or not isinstance(session_id, str):
                return {
                    "status": "error",
                    "error": "Valid session_id is required"
                }

            session_id = session_id.strip()
            status = orchestrator.get_workflow_status(session_id)

            if "error" in status:
                return {
                    "status": "error",
                    "error": "Session not found. Start a new presentation with 'start_presentation'.",
                    "available_sessions": list(orchestrator._active_sessions.keys())
                }

            state = status["current_state"]

            # Provide user-friendly status messages
            friendly_messages = {
                "INIT": "Ready to start! Use 'start_presentation' to begin.",
                "SUMMARY_GENERATED": "Files processed. Use 'continue_workflow' to generate outline.",
                "OUTLINE_GENERATED": "Outline created. Use 'continue_workflow' to proceed to layouts.",
                "OUTLINE_APPROVED": "Outline approved. Use 'choose_layout' to select a theme.",
                "LAYOUT_SELECTED": "Layout chosen. Use 'continue_workflow' to generate presentation.",
                "PRESENTATION_READY": "Presentation generated! Use 'export_presentation' to download.",
                "EXPORT_COMPLETE": "All done! Presentation exported successfully."
            }

            next_actions = {
                "INIT": "start_presentation",
                "SUMMARY_GENERATED": "continue_workflow",
                "OUTLINE_GENERATED": "continue_workflow (or regenerate_outline)",
                "OUTLINE_APPROVED": "choose_layout",
                "LAYOUT_SELECTED": "continue_workflow",
                "PRESENTATION_READY": "export_presentation",
                "EXPORT_COMPLETE": "Download file or start_presentation for new one"
            }

            return {
                "status": "success",
                "session_id": session_id,
                "current_step": state,
                "progress": f"{status['progress']:.0f}%",
                "message": friendly_messages.get(state, f"Currently in {state} state"),
                "next_action": next_actions.get(state, status["next_action"]),
                "context": {
                    "prompt": status["context"].get("metadata", {}).get("original_prompt"),
                    "n_slides": status["context"].get("metadata", {}).get("n_slides"),
                    "language": status["context"].get("metadata", {}).get("language")
                }
            }
        except Exception as e:
            return {
                "status": "error",
                "error": f"Status check failed: {str(e)}",
                "suggestion": "Try start_presentation to begin a new session"
            }

    return get_status
