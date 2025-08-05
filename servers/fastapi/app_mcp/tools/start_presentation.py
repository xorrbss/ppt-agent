from typing import List, Dict, Any, Optional


def register_start_presentation(mcp, orchestrator):
    """Register all workflow-related tools for chat-based interaction"""

    @mcp.tool("start_presentation")
    async def start_presentation(
        session_id: str,
        prompt: str,
        files: Optional[List] = None,
        n_slides: int = 8,
        language: str = "English"
    ) -> Dict[str, Any]:
        """
        🚀 Start creating a new presentation with your idea!

        This is your entry point to create presentations. You can:
        - Start with just a text prompt describing what you want
        - Upload files (PDFs, docs, etc.) to base your presentation on
        - Specify how many slides you want (default: 8)
        - Choose the language for your presentation

        Examples:
        - "Create a presentation about climate change solutions"
        - "Make slides about our Q4 financial results" (with uploaded files)
        - "Build a training deck for new employees"

        Args:
            session_id: Unique identifier for your presentation session
            prompt: Describe what your presentation should be about
            files: Optional list of files to analyze and include
            n_slides: Number of slides to generate (default: 8)
            language: Presentation language (default: English)
        """
        try:
            if not session_id or not isinstance(session_id, str) or len(session_id.strip()) == 0:
                return {
                    "status": "error",
                    "error": "Session ID is required and must be a non-empty string",
                    "example": "Use something like: session_id='my_presentation_123'"
                }

            if not prompt or not isinstance(prompt, str) or len(prompt.strip()) == 0:
                return {
                    "status": "error",
                    "error": "Prompt is required and must be a non-empty string",
                    "example": "prompt='Create a presentation about AI in healthcare'"
                }

            # Clean session_id
            session_id = session_id.strip()

            # Create session
            orchestrator.create_session(session_id)

            # Store initial parameters
            fsm = orchestrator.get_session(session_id)
            if not fsm:
                return {
                    "status": "error",
                    "error": "Failed to create session",
                    "session_id": session_id
                }

            fsm.context.metadata.update({
                "original_prompt": prompt.strip(),
                "n_slides": max(1, min(50, n_slides)),  # Validate slide count
                "language": language.strip() if language else "English"
            })
            # Debug log to verify metadata update
            print("DEBUG: Metadata after update:", fsm.context.metadata)

            # Handle files if provided - store them in context for later use
            if files and len(files) > 0:
                # Store files in context for integrated processing during outline generation
                fsm.context.metadata.update({
                    "files": files
                })
                
                return {
                    "status": "success",
                    "session_id": session_id,
                    "message": "Great! I've received your files and will analyze them during presentation creation.",
                    "prompt": prompt,
                    "files_count": len(files),
                    "suggestion": f"Now I'll create a presentation outline based on your prompt '{prompt}' and analyze the uploaded files. Use 'continue_workflow' to proceed.",
                    "next_step": "Call continue_workflow to generate the outline with file analysis"
                }
            else:
                # Direct outline generation without files
                return {
                    "status": "success",
                    "session_id": session_id,
                    "message": f"Perfect! Let's create a presentation about: '{prompt}'",
                    "suggestion": "I'll generate an outline with the key topics and structure. Use 'continue_workflow' to proceed.",
                    "next_step": "Call continue_workflow to generate the outline",
                    "parameters": {
                    "n_slides": fsm.context.metadata.get("n_slides", 8),  # Ensure n_slides is retrieved correctly
                    "language": fsm.context.metadata.get("language", "English")  # Ensure language is retrieved correctly
                    }
                }
        except Exception as e:
            return {
                "status": "error",
                "error": f"Unexpected error: {str(e)}",
                "session_id": session_id if 'session_id' in locals() else "unknown",
                "suggestion": "Please try again with a valid session_id and prompt"
            }

    return start_presentation