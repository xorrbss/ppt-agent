"""MCP Tools package for presentation generation."""

from app_mcp.tools.choose_layout import register_choose_layout
from app_mcp.tools.export_presentation import register_export_presentation
from app_mcp.tools.regenerate_outline import register_regenerate_outline
from app_mcp.tools.get_status import register_get_status
from app_mcp.tools.show_layouts import register_show_layouts
from app_mcp.tools.start_presentation import register_start_presentation
from app_mcp.tools.help_me import register_help_me
from app_mcp.tools.continue_workflow import register_continue_workflow


__all__ = [
    'register_choose_layout',
    'register_export_presentation',
    'register_regenerate_outline',
    'register_get_status',
    'register_show_layouts',
    'register_start_presentation',
    'register_help_me',
    'register_continue_workflow',
    'register_tools',
]

def register_tools(mcp, orchestrator):
    """Register all MCP tools in a fancy way."""
    tools = [
        register_choose_layout,
        register_export_presentation,
        register_regenerate_outline,
        register_get_status,
        register_show_layouts,
        register_start_presentation,
        register_help_me,
        register_continue_workflow
    ]
    for tool in tools:
        tool(mcp, orchestrator)
