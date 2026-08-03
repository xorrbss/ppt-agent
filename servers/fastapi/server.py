import uvicorn
import argparse
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from api.main import app


LOG_LEVELS = ("critical", "error", "warning", "info", "debug", "trace")
LOGGER = logging.getLogger(__name__)
BACKEND_LOG_MAX_BYTES = 10 * 1024 * 1024
BACKEND_LOG_BACKUP_COUNT = 5


def configure_file_logging(log_level: str) -> RotatingFileHandler | None:
    app_data_directory = (os.getenv("APP_DATA_DIRECTORY") or "").strip()
    if not app_data_directory:
        return None

    log_directory = Path(app_data_directory).resolve() / "logs"
    log_directory.mkdir(parents=True, exist_ok=True)
    log_path = (log_directory / "backend.log").resolve()
    root_logger = logging.getLogger()
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)

    for existing_handler in root_logger.handlers:
        if not isinstance(existing_handler, RotatingFileHandler):
            continue
        if Path(existing_handler.baseFilename).resolve() == log_path:
            existing_handler.setLevel(numeric_level)
            return existing_handler

    handler = RotatingFileHandler(
        log_path,
        maxBytes=BACKEND_LOG_MAX_BYTES,
        backupCount=BACKEND_LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setLevel(numeric_level)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    root_logger.addHandler(handler)
    if root_logger.level > numeric_level:
        root_logger.setLevel(numeric_level)
    return handler


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
    file_log_handler = configure_file_logging(args.log_level)
    if file_log_handler is not None:
        LOGGER.info(
            "backend_file_logging_enabled path=%s max_bytes=%s backup_count=%s",
            file_log_handler.baseFilename,
            BACKEND_LOG_MAX_BYTES,
            BACKEND_LOG_BACKUP_COUNT,
        )

    uvicorn.run(
        "api.main:app",
        host=host,
        port=args.port,
        log_level=args.log_level,
        reload=reload,
    )


if __name__ == "__main__":
    run_server(parse_args())
