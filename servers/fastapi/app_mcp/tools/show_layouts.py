from typing import Dict, Any


def register_show_layouts(mcp, orchestrator):
    """Register all workflow-related tools for chat-based interaction"""

    @mcp.tool("show_layouts")
    async def show_layouts(session_id: str) -> Dict[str, Any]:
        """
        👀 Browse all available presentation themes and layouts.

        See the complete list of professional layouts including:
        - Business and corporate themes
        - Creative and modern designs
        - Academic and educational styles
        - Technical and data-focused layouts

        Each layout comes with its own color scheme, fonts, and slide structures.

        Args:
            session_id: Your presentation session ID
        """
        try:
            layouts = await orchestrator.get_available_layouts()
            return {
                "status": "success",
                "session_id": session_id,
                "message": "Here are all the available presentation layouts:",
                "layouts": layouts,
                "suggestion": "Choose one using 'choose_layout' with the layout name."
            }
        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
                "session_id": session_id
            }

    return show_layouts