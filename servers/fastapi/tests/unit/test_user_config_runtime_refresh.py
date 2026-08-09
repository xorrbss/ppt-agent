import threading

from utils import user_config
from utils.runtime_settings import RuntimeSettings, get_process_runtime_settings


def _settings(model: str) -> RuntimeSettings:
    return RuntimeSettings.from_sources(
        environ={},
        user_config={
            "LLM": "openai",
            "OPENAI_API_KEY": "test-key",
            "OPENAI_MODEL": model,
        },
    )


def test_concurrent_refreshes_cannot_publish_an_older_snapshot_last(monkeypatch):
    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()
    call_lock = threading.Lock()
    errors = []
    call_count = 0
    settings_a = _settings("model-a")
    settings_b = _settings("model-b")

    def controlled_load(*_args, **_kwargs):
        nonlocal call_count
        with call_lock:
            call_index = call_count
            call_count += 1
        if call_index == 0:
            first_entered.set()
            if not release_first.wait(timeout=5):
                raise TimeoutError("first refresh was never released")
            return settings_a
        second_entered.set()
        return settings_b

    def refresh():
        try:
            user_config.refresh_process_runtime_settings()
        except Exception as error:  # pragma: no cover - asserted below
            errors.append(error)

    monkeypatch.setattr(user_config, "load_runtime_settings", controlled_load)
    first = threading.Thread(target=refresh)
    second = threading.Thread(target=refresh)
    first.start()
    assert first_entered.wait(timeout=5)
    second.start()

    second_loaded_while_first_was_blocked = second_entered.wait(timeout=0.2)
    release_first.set()
    first.join(timeout=5)
    second.join(timeout=5)

    assert not first.is_alive()
    assert not second.is_alive()
    assert errors == []
    assert second_loaded_while_first_was_blocked is False
    assert get_process_runtime_settings().get("OPENAI_MODEL") == "model-b"
