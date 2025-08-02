from typing import Dict, Any


def register_choose_layout(mcp, orchestrator):
    """Register all workflow-related tools for chat-based interaction"""

    @mcp.tool("choose_layout")
    async def choose_layout(session_id: str, layout_name: str) -> Dict[str, Any]:
        """
        🎨 Select a visual style and theme for your presentation.
        
        Choose from available professional layouts that determine:
        - Color scheme and visual design
        - Slide structure and layout patterns
        - Font choices and styling
        - Overall presentation aesthetic
        
        Use 'show_layouts' first to see all available options.
        
        Args:
            session_id: Your presentation session ID
            layout_name: Name of the layout you want to use
        """
        try:
            result = await orchestrator.execute_layout_selection(session_id, layout_name)
            
            if result["status"] == "success":
                return {
                    "status": "success",
                    "session_id": session_id,
                    "message": f"Perfect! I've selected the '{layout_name}' layout for your presentation.",
                    "suggestion": "Now I'll generate all the slides with content, images, and styling. This might take a minute or two.",
                    "available_actions": {
                        "continue": "Start generating the presentation",
                        "change_layout": "Actually, let me pick a different layout"
                    }
                }
            return result
        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
                "session_id": session_id
            }

    return choose_layout