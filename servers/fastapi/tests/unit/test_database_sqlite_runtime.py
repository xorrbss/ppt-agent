import multiprocessing
import sqlite3
import time

import pytest

from services.database import _configure_sqlite_connection


class _Cursor:
    def __init__(self, *, wal_failures: int):
        self.wal_failures = wal_failures
        self.wal_attempts = 0
        self.statement = ""
        self.closed = False

    def execute(self, statement):
        self.statement = statement
        if statement == "PRAGMA journal_mode=WAL":
            self.wal_attempts += 1
            if self.wal_attempts <= self.wal_failures:
                raise RuntimeError("database-path-that-must-not-leak")

    def fetchone(self):
        if self.statement == "PRAGMA busy_timeout":
            return (30000,)
        if self.statement == "PRAGMA journal_mode=WAL":
            return ("wal",)
        if self.statement == "PRAGMA foreign_keys":
            return (1,)
        raise AssertionError(self.statement)

    def close(self):
        self.closed = True


class _Connection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


def _configure_fresh_sqlite_worker(database_path, barrier, result_queue):
    connection = None
    try:
        connection = sqlite3.connect(database_path, timeout=30.0)
        barrier.wait(timeout=15.0)
        _configure_sqlite_connection(connection)
        result_queue.put(
            {
                "ok": True,
                "journal": connection.execute(
                    "PRAGMA journal_mode"
                ).fetchone()[0],
                "busy_timeout": connection.execute(
                    "PRAGMA busy_timeout"
                ).fetchone()[0],
                "foreign_keys": connection.execute(
                    "PRAGMA foreign_keys"
                ).fetchone()[0],
            }
        )
    except Exception as error:
        result_queue.put({"ok": False, "error": type(error).__name__})
        raise SystemExit(1) from None
    finally:
        if connection is not None:
            connection.close()


def test_sqlite_wal_configuration_retries_and_verifies(monkeypatch):
    cursor = _Cursor(wal_failures=2)
    sleeps = []
    monkeypatch.setattr(
        "services.database.time.sleep",
        lambda delay: sleeps.append(delay),
    )

    _configure_sqlite_connection(_Connection(cursor))

    assert cursor.wal_attempts == 3
    assert sleeps == [0.01, 0.02]
    assert cursor.closed is True


def test_sqlite_wal_configuration_fails_closed_without_raw_error(monkeypatch):
    cursor = _Cursor(wal_failures=10)
    monkeypatch.setattr("services.database.time.sleep", lambda _delay: None)

    try:
        _configure_sqlite_connection(_Connection(cursor))
    except RuntimeError as error:
        assert str(error) == "sqlite_wal_configuration_failed"
        assert "database-path" not in str(error)
    else:
        raise AssertionError("WAL configuration failure must stop startup")

    assert cursor.wal_attempts == 6
    assert cursor.closed is True


def test_sqlite_in_memory_connection_does_not_require_wal():
    connection = sqlite3.connect(":memory:")
    try:
        _configure_sqlite_connection(connection)

        assert connection.execute("PRAGMA journal_mode").fetchone() == ("memory",)
        assert connection.execute("PRAGMA busy_timeout").fetchone() == (30000,)
        assert connection.execute("PRAGMA foreign_keys").fetchone() == (1,)
    finally:
        connection.close()


@pytest.mark.parametrize("run_number", range(3))
def test_fresh_sqlite_wal_transition_survives_spawned_first_writer_race(
    tmp_path,
    run_number,
):
    database_path = tmp_path / f"fresh-wal-race-{run_number}.db"
    lock_connection = sqlite3.connect(database_path, timeout=30.0)
    assert lock_connection.execute("PRAGMA journal_mode").fetchone() == ("delete",)
    lock_connection.execute("CREATE TABLE marker (id INTEGER PRIMARY KEY)")
    lock_connection.commit()
    lock_connection.execute("BEGIN EXCLUSIVE")

    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(5)
    result_queue = context.Queue()
    processes = [
        context.Process(
            target=_configure_fresh_sqlite_worker,
            args=(str(database_path), barrier, result_queue),
        )
        for _ in range(4)
    ]
    results = []
    exitcodes = []
    try:
        for process in processes:
            process.start()
        barrier.wait(timeout=15.0)
        time.sleep(0.35)
        lock_connection.rollback()

        for process in processes:
            process.join(timeout=15.0)
        exitcodes = [process.exitcode for process in processes]
        results = [result_queue.get(timeout=5.0) for _ in processes]
    finally:
        if lock_connection.in_transaction:
            lock_connection.rollback()
        lock_connection.close()
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5.0)
            process.close()
        result_queue.close()
        result_queue.join_thread()

    assert exitcodes == [0] * 4
    assert results == [
        {
            "ok": True,
            "journal": "wal",
            "busy_timeout": 30000,
            "foreign_keys": 1,
        }
    ] * 4
    assert str(database_path) not in repr(results)
