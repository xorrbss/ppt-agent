import uvicorn
import argparse
import logging
import os
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

REPOSITORY_DIRECTORY = Path(__file__).resolve().parents[2]
ENVIRONMENT_ASSIGNMENT = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


def load_repository_environment(
    repository_directory: Path = REPOSITORY_DIRECTORY,
) -> dict[str, str]:
    """Load the repository-root ``.env`` before the application is imported.

    This repository keeps one ``.env`` at the root for the backend, the proxy
    and the frontend together, but nothing hands it to a directly launched
    ``server.py``.  A backend started without it answers the chat-first
    capability probe with ``chat_first_runtime_profile_required``, and the UI
    falls back to the legacy surface - the same class of failure that
    ``run-next-build.mjs`` fixes for the client bundle, so it is fixed the same
    way here.

    Values already present in the environment win, so Docker, Electron and
    explicit overrides keep precedence and a missing file is not an error.
    """

    try:
        contents = (repository_directory / ".env").read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}

    loaded: dict[str, str] = {}
    for line in contents.splitlines():
        if line.lstrip().startswith("#"):
            continue
        match = ENVIRONMENT_ASSIGNMENT.match(line)
        if match is None:
            continue
        name, raw_value = match.groups()
        if name in os.environ:
            continue
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ[name] = value
        loaded[name] = value
    return loaded




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

    # Establish the normal console sink before adding the file sink. The
    # lifespan later calls logging.basicConfig(), but that is a no-op once any
    # root handler exists. Without this ordering, application records only
    # reach backend.log while uvicorn's own handlers make the console appear
    # healthy.
    logging.basicConfig(level=numeric_level)

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
    # Configuration first: application modules read the environment while they
    # are being imported, so the file has to be in place before that. Keeping
    # both inside the entry point means importing this module stays free of
    # side effects.
    load_repository_environment()
    from api.main import app  # noqa: F401  fail fast on an unimportable app

    run_server(parse_args())
