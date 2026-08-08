"""Application logging setup.

The rollout and PPTX-queue telemetry the canary runbook tells operators to monitor is
emitted at INFO. uvicorn's default LOGGING_CONFIG attaches handlers only to the
uvicorn.* loggers, so without a root handler those records are dropped by
logging.lastResort (WARNING) and the events are invisible in every shipped process.

The root handler list must be cleared inside each test body, not in a fixture: pytest's
logging plugin installs its own capture handler at the start of every test phase, so a
fixture that clears in setup is undone before the body runs.
"""

import logging
from contextlib import contextmanager

import pytest

from api.lifespan import _configure_application_logging


@contextmanager
def bare_root_logger():
    """Reproduce a freshly configured uvicorn process: level set, no handlers."""
    root = logging.getLogger()
    original_handlers = root.handlers[:]
    original_level = root.level
    root.handlers.clear()
    try:
        yield root
    finally:
        root.handlers[:] = original_handlers
        root.setLevel(original_level)


def test_configuring_logging_attaches_a_root_handler(monkeypatch):
    monkeypatch.delenv("LOG_LEVEL", raising=False)

    with bare_root_logger() as root:
        _configure_application_logging()

        assert root.handlers, "root needs a handler or application INFO records are dropped"
        assert root.level == logging.INFO


def test_telemetry_record_reaches_the_installed_handler(monkeypatch):
    """The end-to-end symptom: an INFO record must survive handler dispatch.

    Observes the handler the function itself installs -- adding one here instead would
    pass even with no root handler configured, proving nothing.
    """
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    emitted: list[str] = []

    with bare_root_logger() as root:
        _configure_application_logging()
        for handler in root.handlers:
            monkeypatch.setattr(
                handler, "emit", lambda record: emitted.append(record.getMessage())
            )
        logging.getLogger("services.template_v2_rollout").info("template_v2_rollout {}")

    assert emitted == ["template_v2_rollout {}"]


@pytest.mark.parametrize(
    ("raw_level", "expected"),
    [("warning", logging.WARNING), ("not-a-level", logging.INFO)],
)
def test_log_level_env_is_honored(monkeypatch, raw_level, expected):
    monkeypatch.setenv("LOG_LEVEL", raw_level)

    with bare_root_logger() as root:
        _configure_application_logging()

        assert root.level == expected


def test_repeated_configuration_does_not_stack_handlers(monkeypatch):
    monkeypatch.delenv("LOG_LEVEL", raising=False)

    with bare_root_logger() as root:
        _configure_application_logging()
        handler_count = len(root.handlers)
        _configure_application_logging()

        assert len(root.handlers) == handler_count
