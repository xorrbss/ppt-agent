import sys
import os
import argparse
import asyncio

from app_mcp.server import create_mcp_server, uvicorn_config

async def main():
    parser = argparse.ArgumentParser(description="Run the FastAPI server")
    parser.add_argument(
        "--port", type=int, default=8001, help="Port number to run the server on"
    )
    args = parser.parse_args()

    mcp = create_mcp_server()
    await mcp.run_async(
        transport="http",
        host="0.0.0.0",
        port=args.port,
        uvicorn_config=uvicorn_config
    )

if __name__ == "__main__":
    asyncio.run(main())
