import uvicorn
import argparse
import os
from api.main import app


LOG_LEVELS = ("critical", "error", "warning", "info", "debug", "trace")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the FastAPI server")
    parser.add_argument(
        "--port", type=int, required=True, help="Port number to run the server on"
    )
    parser.add_argument(
        "--reload", type=str, default="false", help="Reload the server on code changes"
    )
    parser.add_argument(
        "--log-level",
        choices=LOG_LEVELS,
        default=(os.getenv("LOG_LEVEL") or "info").strip().lower(),
        help="Uvicorn and application log level (default: LOG_LEVEL or info)",
    )
    return parser.parse_args(argv)


def run_server(args: argparse.Namespace) -> None:
    reload = args.reload == "true"
    host = "127.0.0.1"

    # Bind asset/base URL generation to the active runtime port (same env name as Next/Electron).
    os.environ["NEXT_PUBLIC_FAST_API"] = f"http://{host}:{args.port}"
    os.environ["LOG_LEVEL"] = args.log_level.upper()

    uvicorn.run(
        "api.main:app",
        host=host,
        port=args.port,
        log_level=args.log_level,
        reload=reload,
    )


if __name__ == "__main__":
    run_server(parse_args())
