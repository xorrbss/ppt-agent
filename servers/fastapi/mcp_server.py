import sys
import argparse
import asyncio
import json
import traceback

import httpx
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_headers

with open("openai_spec.json", "r") as f:
    openapi_spec = json.load(f)


async def attach_request_auth_headers(request: httpx.Request) -> None:
    """Forward the nginx-verified MCP caller identity to FastAPI tools."""
    incoming_headers = get_http_headers(include={"authorization", "cookie"})
    for name in ("authorization", "cookie"):
        value = incoming_headers.get(name)
        if value and name not in request.headers:
            request.headers[name] = value


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the MCP server (from OpenAPI)"
    )
    parser.add_argument(
        "--port", type=int, default=8001, help="Port for the MCP HTTP server"
    )
    parser.add_argument(
        "--name",
        type=str,
        default="Presenton API (OpenAPI)",
        help="Display name for the generated MCP server",
    )
    parser.add_argument(
        "--reload",
        choices=("true", "false"),
        default="false",
        help="Reload on source changes (default: false; development only)",
    )
    return parser.parse_args(argv)


async def run_mcp_server(args: argparse.Namespace) -> None:
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:8000",
        timeout=60.0,
        event_hooks={"request": [attach_request_auth_headers]},
    ) as api_client:
        print("DEBUG: Creating FastMCP server from OpenAPI spec...")
        mcp = FastMCP.from_openapi(
            openapi_spec=openapi_spec,
            client=api_client,
            name=args.name,
        )
        print("DEBUG: MCP server created from OpenAPI successfully")

        reload_enabled = args.reload == "true"
        print(
            "DEBUG: Starting MCP server on "
            f"host=127.0.0.1, port={args.port}, reload={reload_enabled}"
        )
        await mcp.run_async(
            transport="http",
            host="127.0.0.1",
            port=args.port,
            uvicorn_config={"reload": reload_enabled},
        )
        print("DEBUG: MCP server run_async completed")


async def main(argv: list[str] | None = None) -> None:
    try:
        print("DEBUG: MCP (OpenAPI) Server startup initiated")
        args = parse_args(argv)
        print(
            f"DEBUG: Parsed args - port={args.port}, reload={args.reload}"
        )
        await run_mcp_server(args)
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
