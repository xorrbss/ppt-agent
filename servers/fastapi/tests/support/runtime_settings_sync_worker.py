from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from services.provider_settings import save_provider_settings_record
from services.runtime_settings_coordinator import RuntimeSettingsCoordinator
from utils.runtime_settings import (
    get_process_runtime_settings_envelope,
    publish_process_runtime_settings,
)


class _QueueLogHandler(logging.Handler):
    def __init__(self, queue) -> None:
        super().__init__()
        self._queue = queue

    def emit(self, record: logging.LogRecord) -> None:
        self._queue.put(self.format(record))


def run_worker(database_path: str, commands, responses, logs) -> None:
    os.environ.pop("USER_CONFIG_PATH", None)
    os.environ["CAN_CHANGE_KEYS"] = "true"
    handler = _QueueLogHandler(logs)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    sync_logger = logging.getLogger("services.runtime_settings_coordinator")
    sync_logger.handlers = [handler]
    sync_logger.propagate = False
    sync_logger.setLevel(logging.INFO)

    async def serve() -> None:
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{database_path}",
            connect_args={"timeout": 30.0},
        )
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as connection:
            await connection.execute(text("PRAGMA busy_timeout=30000"))
        coordinator = RuntimeSettingsCoordinator(
            session_factory=maker,
            poll_interval_seconds=0.05,
        )
        try:
            await coordinator.initialize()
            coordinator.start()
            responses.put({"kind": "ready"})
            while True:
                command: dict[str, Any] = await asyncio.to_thread(commands.get)
                kind = command["kind"]
                if kind == "stop":
                    break
                if kind == "update":
                    async with maker() as session:
                        committed = await save_provider_settings_record(
                            session,
                            command["patch"],
                        )
                    publish_process_runtime_settings(
                        committed.revision,
                        coordinator._settings(committed),
                    )
                    responses.put(
                        {"kind": "updated", "revision": committed.revision}
                    )
                    continue
                if kind == "status":
                    envelope = get_process_runtime_settings_envelope()
                    responses.put(
                        {
                            "kind": "status",
                            "revision": (
                                envelope.revision if envelope is not None else -1
                            ),
                            "model_matches": bool(
                                envelope is not None
                                and envelope.settings.get("OPENAI_MODEL")
                                == command["model"]
                            ),
                            "secret_matches": bool(
                                envelope is not None
                                and envelope.settings.get("OPENAI_API_KEY")
                                == command["secret"]
                            ),
                        }
                    )
        except Exception as error:
            responses.put({"kind": "error", "error": type(error).__name__})
        finally:
            await coordinator.stop()
            await engine.dispose()

    asyncio.run(serve())
