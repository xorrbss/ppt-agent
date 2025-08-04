from typing import Dict, Any


def register_continue_workflow(mcp, orchestrator):
    """Register all workflow-related tools for chat-based interaction"""

    @mcp.tool("continue_workflow")
    async def continue_workflow(
        session_id: str,
        action: str = "continue"
    ) -> Dict[str, Any]:
        """
        ⏭️ Move to the next step in creating your presentation.
        
        This tool automatically determines what should happen next based on where
        you are in the process:
        - After starting: Generates your presentation outline
        - After outline: Shows available layouts to choose from
        - After layout: Creates your complete presentation
        
        Just call this when you're ready to proceed to the next step!
        
        Args:
            session_id: Your presentation session ID
            action: What to do next (usually just "continue")
        """
        try:
            # Validate session_id
            if not session_id or not isinstance(session_id, str):
                return {
                    "status": "error", 
                    "error": "Valid session_id is required",
                    "suggestion": "Use the same session_id from start_presentation"
                }
            
            session_id = session_id.strip()
            fsm = orchestrator.get_session(session_id)
            if not fsm:
                return {
                    "status": "error", 
                    "error": "Session not found. Please start a new presentation first.",
                    "suggestion": "Call start_presentation to begin"
                }
            
            current_state = fsm.state.name
            
            if current_state in ["FILES_UPLOADED", "SUMMARY_GENERATED", "INIT"]:
                # Generate outline
                prompt = fsm.context.metadata.get("original_prompt", "")
                n_slides = fsm.context.metadata.get("n_slides", 8)
                language = fsm.context.metadata.get("language", "English")
                
                if not prompt:
                    return {
                        "status": "error",
                        "error": "No prompt found in session. Please start over.",
                        "suggestion": "Call start_presentation with a valid prompt"
                    }

                result = await orchestrator.execute_generate_outline(
                    session_id, prompt, n_slides=n_slides, language=language
                )
                
                if result["status"] == "success":
                    return {
                        "status": "success",
                        "session_id": session_id,
                        "message": "Here's your presentation outline:",
                        "title": result["result"]["title"],
                        "outlines": result["result"]["outlines"],
                        "suggestion": "Take a look at the outline. If it looks good, use 'continue_workflow' again to proceed to layout selection.",
                        "next_step": "Call continue_workflow again to choose layouts, or use regenerate_outline to try different approach"
                    }
                return result
            
            elif current_state == "OUTLINE_GENERATED":
                # Auto-approve and move to layouts
                await orchestrator.approve_outline(session_id)
                layouts = await orchestrator.get_available_layouts()

                return {
                    "status": "success",
                    "session_id": session_id,
                    "message": "Great! Now let's choose a visual style for your presentation.",
                    "available_layouts": layouts,
                    "suggestion": "Choose a layout that fits your content and audience. Use 'choose_layout' with the layout name.",
                    "next_step": "Call choose_layout with your preferred layout name"
                }
            
            elif current_state == "LAYOUT_SELECTED":
                # Generate presentation
                result = await orchestrator.execute_presentation_generation(session_id)

                if result["status"] == "success":
                    return {
                        "status": "success",
                        "session_id": session_id,
                        "message": "🎉 Your presentation is ready!",
                        "title": result["result"]["title"],
                        "presentation_id": result["result"]["presentation_id"],
                        "suggestion": "Your presentation has been generated successfully! Use 'export_presentation' to download it.",
                        "next_step": "Call export_presentation with format 'pptx' or 'pdf'"
                    }
                return result
            
            else:
                return {
                    "status": "info",
                    "message": f"Currently in {current_state} state.",
                    "suggestion": "Use get_status to see what actions are available.",
                    "next_step": "Call get_status for guidance"
                }
                
        except Exception as e:
            return {
                "status": "error",
                "error": f"Workflow error: {str(e)}",
                "session_id": session_id if 'session_id' in locals() else "unknown",
                "suggestion": "Use get_status to check your current progress"
            }
    
    return continue_workflow