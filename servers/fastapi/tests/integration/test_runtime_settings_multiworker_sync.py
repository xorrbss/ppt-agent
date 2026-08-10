import multiprocessing
import queue
import time

import pytest
from sqlalchemy import create_engine

from models.sql.provider_settings import ProviderSettings
from tests.support.runtime_settings_sync_worker import run_worker


def _receive(response_queue, timeout=10):
    response = response_queue.get(timeout=timeout)
    assert response.get("kind") != "error", response
    return response


@pytest.mark.parametrize("run_number", range(3))
def test_two_processes_converge_through_shared_sqlite_without_secret_events(
    tmp_path,
    run_number,
):
    database_path = tmp_path / f"multiworker-{run_number}.db"
    engine = create_engine(f"sqlite:///{database_path}")
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA busy_timeout=30000")
        mode = connection.exec_driver_sql(
            "PRAGMA journal_mode=WAL"
        ).scalar_one()
        assert str(mode).lower() == "wal"
    ProviderSettings.__table__.create(engine)
    engine.dispose()

    context = multiprocessing.get_context("spawn")
    command_queues = [context.Queue(), context.Queue()]
    response_queues = [context.Queue(), context.Queue()]
    log_queue = context.Queue()
    workers = [
        context.Process(
            target=run_worker,
            args=(
                str(database_path),
                command_queues[index],
                response_queues[index],
                log_queue,
            ),
        )
        for index in range(2)
    ]
    for worker in workers:
        worker.start()

    secret = "multiworker-secret-value"
    control_token = "control-token-must-not-appear"
    observed_responses = []
    try:
        observed_responses.extend(_receive(item) for item in response_queues)
        command_queues[0].put(
            {
                "kind": "update",
                "patch": {
                    "LLM": "openai",
                    "OPENAI_MODEL": "converged-model",
                    "OPENAI_API_KEY": secret,
                },
            }
        )
        committed = _receive(response_queues[0])
        observed_responses.append(committed)

        deadline = time.monotonic() + 8
        converged = [False, False]
        while time.monotonic() < deadline and not all(converged):
            for index in range(2):
                command_queues[index].put(
                    {
                        "kind": "status",
                        "model": "converged-model",
                        "secret": secret,
                    }
                )
                status = _receive(response_queues[index])
                observed_responses.append(status)
                converged[index] = (
                    status["revision"] >= committed["revision"]
                    and status["model_matches"]
                    and status["secret_matches"]
                )
            if not all(converged):
                time.sleep(0.05)

        assert converged == [True, True]
    finally:
        for commands in command_queues:
            commands.put({"kind": "stop"})
        for worker in workers:
            worker.join(timeout=10)
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=5)
            assert worker.exitcode == 0

    log_messages = []
    while True:
        try:
            log_messages.append(log_queue.get_nowait())
        except queue.Empty:
            break
    observable = repr(observed_responses) + "\n" + "\n".join(log_messages)
    assert secret not in observable
    assert control_token not in observable
    assert str(database_path) not in observable
    assert "__configured__" not in observable
    for process_queue in [*command_queues, *response_queues, log_queue]:
        process_queue.close()
        process_queue.join_thread()
    for worker in workers:
        worker.close()
