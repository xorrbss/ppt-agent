import errno
import json
import os
import stat
import subprocess
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

import utils.user_config_store as user_config_store
from utils.user_config_store import read_user_config_file, update_user_config_file


def assert_private_config_file(config_path):
    """Assert the OS-native credential-file privacy contract.

    POSIX exposes owner-only permissions through ``stat``.  Windows uses ACLs,
    whose effective protection cannot be represented by those mode bits.
    """
    if os.name == "nt":
        return
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600


def test_reads_backup_when_primary_json_is_malformed(tmp_path):
    config_path = tmp_path / "userConfig.json"
    config_path.write_text('{"LLM"', encoding="utf-8")
    config_path.with_suffix(config_path.suffix + ".bak").write_text(
        json.dumps({"LLM": "openai"}),
        encoding="utf-8",
    )

    assert read_user_config_file(str(config_path)) == {"LLM": "openai"}


def test_read_secures_existing_config_files_without_changing_contents(
    monkeypatch, tmp_path
):
    config_path = tmp_path / "userConfig.json"
    backup_path = config_path.with_suffix(config_path.suffix + ".bak")
    primary_content = json.dumps({"AUTH_PASSWORD_HASH": "preserved"})
    backup_content = json.dumps({"AUTH_PASSWORD_HASH": "backup-preserved"})
    config_path.write_text(primary_content, encoding="utf-8")
    backup_path.write_text(backup_content, encoding="utf-8")
    config_path.chmod(0o644)
    backup_path.chmod(0o644)

    assert read_user_config_file(str(config_path)) == {
        "AUTH_PASSWORD_HASH": "preserved"
    }
    assert config_path.read_text(encoding="utf-8") == primary_content
    assert backup_path.read_text(encoding="utf-8") == backup_content
    assert_private_config_file(config_path)
    assert_private_config_file(backup_path)


def test_windows_hardening_uses_current_user_sid_acl(monkeypatch, tmp_path):
    """Windows privacy is an ACL contract, not a lossy POSIX mode assertion."""
    config_path = tmp_path / "userConfig.json"
    calls = []

    monkeypatch.setattr(user_config_store.os, "name", "nt")
    monkeypatch.setattr(
        user_config_store,
        "_windows_current_user_sid",
        lambda: "S-1-5-21-1000",
    )

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(user_config_store.subprocess, "run", run)

    assert user_config_store._secure_config_file(str(config_path)) is True
    assert len(calls) == 1
    command, kwargs = calls[0]
    assert command == [
        "icacls",
        str(config_path),
        "/inheritance:r",
        "/grant:r",
        "*S-1-5-21-1000:(M)",
        "/grant:r",
        "*S-1-5-18:(F)",
    ]
    assert kwargs == {
        "check": False,
        "capture_output": True,
        "timeout": 10,
        **user_config_store._subprocess_text_kwargs(),
    }


def test_windows_sid_command_replaces_undecodable_localized_output(monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(
            stdout='"DESKTOP\\\\user","S-1-5-21-1000"\n',
            returncode=0,
        )

    monkeypatch.setattr(user_config_store.subprocess, "run", run)

    assert user_config_store._windows_current_user_sid() == "S-1-5-21-1000"
    assert calls[0][1]["errors"] == "replace"
    assert calls[0][1]["encoding"]


def test_windows_acl_timeout_fails_closed(monkeypatch, tmp_path):
    config_path = tmp_path / "userConfig.json"
    monkeypatch.setattr(user_config_store.os, "name", "nt")
    monkeypatch.setattr(
        user_config_store,
        "_windows_current_user_sid",
        lambda: "S-1-5-21-1000",
    )

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("icacls", 10)

    monkeypatch.setattr(user_config_store.subprocess, "run", timeout)

    assert user_config_store._secure_config_file(str(config_path)) is False


def test_windows_hardening_accepts_verified_private_legacy_acl(
    monkeypatch, tmp_path
):
    """An admin-owned file need not be mutated when its DACL is already safe."""
    config_path = tmp_path / "userConfig.json"
    config_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(user_config_store.os, "name", "nt")
    monkeypatch.setattr(
        user_config_store,
        "_windows_current_user_sid",
        lambda: "S-1-5-21-1000",
    )
    monkeypatch.setattr(
        user_config_store,
        "_windows_acl_snapshot",
        lambda _path: {
            "owner": "BUILTIN\\Administrators",
            "protected": True,
            "rules": [
                {
                    "sid": "S-1-5-21-1000",
                    "type": "Allow",
                    "inherited": False,
                },
                {"sid": "S-1-5-18", "type": "Allow", "inherited": False},
            ],
        },
    )
    monkeypatch.setattr(
        user_config_store.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=5),
    )

    assert user_config_store._secure_config_file(str(config_path)) is True


def test_windows_hardening_rejects_unexpected_allowed_principal(
    monkeypatch, tmp_path
):
    config_path = tmp_path / "userConfig.json"
    config_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(user_config_store.os, "name", "nt")
    monkeypatch.setattr(
        user_config_store,
        "_windows_current_user_sid",
        lambda: "S-1-5-21-1000",
    )
    monkeypatch.setattr(
        user_config_store,
        "_windows_acl_snapshot",
        lambda _path: {
            "protected": True,
            "rules": [
                {
                    "sid": "S-1-5-21-1000",
                    "type": "Allow",
                    "inherited": False,
                },
                {"sid": "S-1-5-18", "type": "Allow", "inherited": False},
                {"sid": "S-1-1-0", "type": "Allow", "inherited": False},
            ],
        },
    )
    monkeypatch.setattr(
        user_config_store.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=5),
    )

    assert user_config_store._secure_config_file(str(config_path)) is False


def test_atomic_write_never_serializes_credentials_when_acl_hardening_fails(
    monkeypatch, tmp_path
):
    config_path = tmp_path / "userConfig.json"
    monkeypatch.setattr(user_config_store, "_secure_config_file", lambda _path: False)

    with pytest.raises(OSError, match="could not secure the Windows user config ACL"):
        user_config_store._write_atomic_json(
            str(config_path), {"AUTH_SECRET_KEY": "must-not-be-written"}, False
        )

    assert not config_path.exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_windows_atomic_write_falls_back_for_private_legacy_target(
    monkeypatch, tmp_path
):
    config_path = tmp_path / "userConfig.json"
    config_path.write_text('{"LLM":"old"}', encoding="utf-8")
    monkeypatch.setattr(user_config_store.os, "name", "nt")
    monkeypatch.setattr(user_config_store, "_secure_config_file", lambda _path: True)
    monkeypatch.setattr(user_config_store.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        user_config_store.os,
        "replace",
        lambda _source, _destination: (_ for _ in ()).throw(
            PermissionError(errno.EACCES, "legacy target cannot be replaced")
        ),
    )

    user_config_store._write_atomic_json(
        str(config_path),
        {"LLM": "codex", "CODEX_ACCESS_TOKEN": "fresh"},
        True,
    )

    assert json.loads(config_path.read_text(encoding="utf-8")) == {
        "LLM": "codex",
        "CODEX_ACCESS_TOKEN": "fresh",
    }
    assert not list(tmp_path.glob("*.tmp"))


def test_update_creates_parent_directory_and_writes_valid_json(tmp_path):
    config_path = tmp_path / "missing" / "userConfig.json"

    update_user_config_file(
        str(config_path),
        lambda existing: {**existing, "LLM": "openai"},
    )

    assert json.loads(config_path.read_text(encoding="utf-8")) == {"LLM": "openai"}
    backup_config = json.loads(
        config_path.with_suffix(config_path.suffix + ".bak").read_text(
            encoding="utf-8"
        )
    )
    assert backup_config == {"LLM": "openai"}


def test_concurrent_updates_are_serialized(tmp_path):
    config_path = tmp_path / "userConfig.json"
    update_user_config_file(str(config_path), lambda _: {"count": 0})

    def increment() -> None:
        def update(existing: dict) -> dict:
            existing["count"] = int(existing.get("count", 0)) + 1
            return existing

        update_user_config_file(str(config_path), update)

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda _: increment(), range(25)))

    assert read_user_config_file(str(config_path))["count"] == 25
