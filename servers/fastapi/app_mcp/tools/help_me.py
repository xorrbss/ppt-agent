from typing import Dict, Any, Optional, List


def register_help_me(mcp, orchestrator):
    """Register all workflow-related tools for chat-based interaction"""

    @mcp.tool("help")
    def help() -> Dict[str, Any]:
        """
        ❓ Get help and guidance for creating presentations.

        Shows you:
        - Step-by-step workflow guide
        - Available commands and what they do
        - Example usage to get you started
        - Tips for best results

        Perfect for first-time users or when you need a refresher!
        """
        return {
            "status": "info",
            "message": "🎯 Complete Guide to Creating Presentations",
            "workflow": {
                "step_1": "🚀 start_presentation - Begin with your topic and optional files",
                "step_2": "📋 continue_workflow - Generate and review your outline",
                "step_3": "🎨 choose_layout - Pick a visual style that fits your content",
                "step_4": "⚡ continue_workflow - Generate your complete presentation",
                "step_5": "📁 export_presentation - Download as PowerPoint or PDF"
            },
            "helpful_commands": {
                "get_status": "📊 Check your current progress anytime",
                "show_layouts": "👀 Browse available themes and styles",
                "help": "❓ Show this helpful guide"
            },
            "quick_start": {
                "with_files": "start_presentation(session_id='my-session', prompt='Your topic', files=[uploaded_files])",
                "text_only": "start_presentation(session_id='my-session', prompt='Create a presentation about sustainable energy')",
                "custom": "start_presentation(session_id='my-session', prompt='Your topic', n_slides=10, language='Spanish')"
            },
            "tips": [
                "💡 Be specific in your prompt for better results",
                "📎 Upload relevant files to enhance your content",
                "🎨 Choose layouts that match your audience and purpose",
                "📊 Use get_status anytime to see what's next"
            ]
        }

    return help