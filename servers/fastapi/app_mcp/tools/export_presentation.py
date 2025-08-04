from typing import Dict, Any

def register_export_presentation(mcp, orchestrator):
    """Register all workflow-related tools for chat-based interaction"""

    @mcp.tool("export_presentation")
    async def export_presentation(
        session_id: str,
        format: str = "pptx",
        export_path: str = None
    ) -> Dict[str, Any]:
        """
        📁 Download your finished presentation in your preferred format.
        
        Export your completed presentation as:
        - "pptx" - PowerPoint format (editable, best for sharing and presenting)
        - "pdf" - PDF format (read-only, best for viewing and printing)
        
        The exported file will be ready for download immediately.
        
        Args:
            session_id: Your presentation session ID
            format: Export format - either "pptx" or "pdf"
        """
        try:
            if format.lower() not in ["pdf", "pptx"]:
                return {
                    "status": "error",
                    "error": "Please choose either 'pdf' or 'pptx' format",
                    "session_id": session_id
                }
            
            result = await orchestrator.execute_export(session_id, format.lower())
            print("Export result:", result)
            
            if result["status"] == "success":
                return {
                    "status": "success",
                    "session_id": session_id,
                    "message": f"🎉 Your presentation has been exported as {format.upper()}!",
                    "path": result["result"]["path"],
                    "suggestion": "You can download it now, or start creating another presentation.",
                    "available_actions": {
                        "download": "Download the presentation",
                        "new_presentation": "Create a new presentation",
                        "edit": "Make edits to this presentation"
                    }
                }
            return result
        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
                "session_id": session_id
            }
    return export_presentation