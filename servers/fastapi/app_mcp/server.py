from fastmcp import FastMCP
from app_mcp.tools import register_tools
from app_mcp.services.workflow_orchestrator import WorkflowOrchestrator

def create_mcp_server():
    mcp = FastMCP("PresentonMCP")
    orchestrator = WorkflowOrchestrator()
    register_tools(mcp, orchestrator)
    return mcp

uvicorn_config = {
    "reload": True,
}
