import sys
import argparse
import asyncio
import traceback
from urllib.parse import urljoin
import time

import httpx
from fastmcp import FastMCP
import json


async def main():
    try:
        print("DEBUG: MCP (OpenAPI) Server startup initiated")
        parser = argparse.ArgumentParser(description="Run the MCP server (from OpenAPI)")
        parser.add_argument(
            "--port", type=int, default=8001, help="Port for the MCP HTTP server"
        )
        parser.add_argument(
            "--api-base-url",
            type=str,
            default="http://127.0.0.1:8000",
            help="Base URL of the FastAPI server to wrap (e.g., http://127.0.0.1:8000)",
        )
        parser.add_argument(
            "--openapi-path",
            type=str,
            default="/openapi.json",
            help="Path to the OpenAPI JSON on the FastAPI server",
        )
        parser.add_argument(
            "--name",
            type=str,
            default="Presenton API (OpenAPI)",
            help="Display name for the generated MCP server",
        )
        args = parser.parse_args()
        print(
            f"DEBUG: Parsed args - port={args.port}, api_base_url={args.api_base_url}, openapi_path={args.openapi_path}"
        )

        with open("openai_spec.json", "r") as f:
            openapi_spec = json.load(f)

        # Create an HTTP client that the MCP server will use to call the API
        api_client = httpx.AsyncClient(base_url=args.api_base_url, timeout=60.0)

        # Build MCP server from OpenAPI
        print("DEBUG: Creating FastMCP server from OpenAPI spec...")
        mcp = FastMCP.from_openapi(
            openapi_spec=openapi_spec,
            client=api_client,
            name=args.name,
        )
        print("DEBUG: MCP server created from OpenAPI successfully")

        # Start the MCP server
        uvicorn_config = {"reload": True}
        print(f"DEBUG: Starting MCP server on host=0.0.0.0, port={args.port}")
        await mcp.run_async(
            transport="http",
            host="0.0.0.0",
            port=args.port,
            uvicorn_config=uvicorn_config,
        )
        print("DEBUG: MCP server run_async completed")
    except Exception as e:
        print(f"ERROR: MCP server startup failed: {e}")
        print(f"ERROR: Traceback: {traceback.format_exc()}")
        raise


if __name__ == "__main__":
    print("DEBUG: Starting MCP (OpenAPI) main function")
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"FATAL ERROR: {e}")
        print(f"FATAL TRACEBACK: {traceback.format_exc()}")
        sys.exit(1)
