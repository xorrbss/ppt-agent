import csv
import errno
import json
import os
import shutil
import subprocess
import time
import uuid
from contextlib import contextmanager
from typing import Callable, Iterator


# ACL hardening on Windows invokes icacls for the primary, backup, and atomic
# replacement.  It can take several seconds under endpoint protection, so a
# short lock timeout caused otherwise-safe concurrent updates to fail.
LOCK_TIMEOUT_SECONDS = 30.0
LOCK_STALE_SECONDS = 30.0
RETRY_DELAY_SECONDS = 0.05
MAX_IO_ATTEMPTS = 6


def _windows_current_user_sid() -> str | None:
    """Return the current Windows principal SID without depending on its name."""
    try:
        result = subprocess.run(
            ["whoami", "/user", "/fo", "csv", "/nh"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        rows = list(csv.reader(result.stdout.splitlines()))
        if rows and len(rows[0]) >= 2 and rows[0][1].startswith("S-"):
            return rows[0][1]
    except (OSError, subprocess.SubprocessError):
        return None
    return None


def _secure_config_file(file_path: str) -> bool:
    """Restrict a credential file to the app user on each supported OS.

    Windows does not expose POSIX group/other mode bits through ``stat``.  A
    successful ``chmod(0o600)`` there only toggles the read-only attribute, so
    use an explicit ACL instead.  SYSTEM retains full access for normal OS
    servicing; the current user's SID is used rather than a localized account
    name.
    """
    if os.name != "nt":
        os.chmod(file_path, 0o600)
        return True

    sid = _windows_current_user_sid()
    if not sid:
        return False
    try:
        result = subprocess.run(
            [
                "icacls",
                file_path,
                "/inheritance:r",
                "/grant:r",
                f"*{sid}:(R,W)",
                "/grant:r",
                "*S-1-5-18:(F)",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _backup_path(config_path: str) -> str:
    return f"{config_path}.bak"


def _lock_path(config_path: str) -> str:
    return f"{config_path}.lock"


def _harden_existing_config_permissions(config_path: str) -> None:
    for file_path in (config_path, _backup_path(config_path)):
        try:
            if not _secure_config_file(file_path):
                raise OSError("could not secure the Windows user config ACL")
        except FileNotFoundError:
            continue
        except OSError as error:
            # Permission hardening must never make a readable credential file
            # appear empty to callers and trigger destructive re-initialization.
            print(
                f"[Presenton] Failed to secure "
                f"{os.path.basename(file_path)} permissions: {error}"
            )


def _is_retryable_os_error(error: BaseException) -> bool:
    return isinstance(error, OSError) and error.errno in {
        errno.EACCES,
        errno.EBUSY,
        errno.EPERM,
    }


def _retry(label: str, operation: Callable[[], object]) -> object:
    last_error: Exception | None = None

    for attempt in range(MAX_IO_ATTEMPTS):
        try:
            return operation()
        except Exception as error:
            last_error = error
            if not _is_retryable_os_error(error):
                raise
            if attempt == MAX_IO_ATTEMPTS - 1:
                break
            time.sleep(RETRY_DELAY_SECONDS * (attempt + 1))

    raise RuntimeError(f"Failed to {label}: {last_error}") from last_error


def _ensure_parent_directory(config_path: str) -> None:
    directory = os.path.dirname(config_path)
    if directory:
        _retry(
            "create user config directory",
            lambda: os.makedirs(directory, exist_ok=True),
        )


def _read_json_if_valid(file_path: str) -> dict | None:
    def read_text() -> str:
        with open(file_path, "r", encoding="utf-8") as file:
            return file.read()

    try:
        content = str(_retry(f"read {os.path.basename(file_path)}", read_text)).strip()
        if not content:
            return {}
        parsed = json.loads(content)
        return parsed if isinstance(parsed, dict) else None
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _read_snapshot(config_path: str) -> tuple[dict, bool]:
    primary = _read_json_if_valid(config_path)
    if primary is not None:
        return primary, True

    backup = _read_json_if_valid(_backup_path(config_path))
    return backup or {}, False


def _remove_stale_lock(lock_file_path: str) -> None:
    try:
        stat = os.stat(lock_file_path)
    except FileNotFoundError:
        return

    if time.time() - stat.st_mtime >= LOCK_STALE_SECONDS:
        _retry("remove stale user config lock", lambda: os.unlink(lock_file_path))


@contextmanager
def _user_config_lock(config_path: str) -> Iterator[None]:
    _ensure_parent_directory(config_path)
    lock_file_path = _lock_path(config_path)
    started_at = time.monotonic()

    while True:
        fd: int | None = None
        try:
            fd = os.open(lock_file_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(
                fd,
                json.dumps(
                    {
                        "pid": os.getpid(),
                        "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    }
                ).encode("utf-8"),
            )
            os.close(fd)
            fd = None
            break
        except FileExistsError:
            _remove_stale_lock(lock_file_path)
        except OSError as error:
            if not _is_retryable_os_error(error):
                raise
        finally:
            if fd is not None:
                os.close(fd)

        if time.monotonic() - started_at >= LOCK_TIMEOUT_SECONDS:
            raise TimeoutError(f"Timed out waiting for user config lock: {lock_file_path}")
        time.sleep(RETRY_DELAY_SECONDS)

    try:
        yield
    finally:
        try:
            _retry("release user config lock", lambda: os.unlink(lock_file_path))
        except FileNotFoundError:
            pass
        except Exception as error:
            print(f"[Presenton] Failed to release user config lock: {error}")


def _copy_backup_if_possible(config_path: str, primary_valid: bool) -> None:
    config_backup_path = _backup_path(config_path)
    try:
        if primary_valid and os.path.exists(config_path):
            _retry(
                "write user config backup",
                lambda: shutil.copy2(config_path, config_backup_path),
            )
        elif not os.path.exists(config_backup_path) and os.path.exists(config_path):
            _retry(
                "initialize user config backup",
                lambda: shutil.copy2(config_path, config_backup_path),
            )
        if os.path.exists(config_backup_path) and not _secure_config_file(
            config_backup_path
        ):
            # Never retain an unprotected credential-bearing recovery copy.
            os.unlink(config_backup_path)
            raise OSError("could not secure user config backup")
    except Exception as error:
        print(f"[Presenton] Failed to update user config backup: {error}")


def _write_atomic_json(config_path: str, config: dict, primary_valid: bool) -> None:
    _ensure_parent_directory(config_path)
    _copy_backup_if_possible(config_path, primary_valid)

    temp_path = (
        f"{config_path}.{os.getpid()}.{int(time.time() * 1000)}."
        f"{uuid.uuid4().hex}.tmp"
    )
    try:
        descriptor = os.open(
            temp_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        # A newly-created file inherits its parent ACL on Windows.  Secure it
        # before serializing credential material, not only after replace.
        if not _secure_config_file(temp_path):
            os.close(descriptor)
            descriptor = -1
            raise OSError("could not secure the Windows user config ACL")
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            json.dump(config, file, separators=(",", ":"))
            file.flush()
            os.fsync(file.fileno())
        _retry("replace user config", lambda: os.replace(temp_path, config_path))
        if not _secure_config_file(config_path):
            raise OSError("could not secure the Windows user config ACL")
        _copy_backup_if_possible(config_path, False)
    except Exception:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass
        raise


def read_user_config_file(config_path: str) -> dict:
    try:
        _ensure_parent_directory(config_path)
        _harden_existing_config_permissions(config_path)
        config, _ = _read_snapshot(config_path)
        return dict(config)
    except Exception:
        return {}


def update_user_config_file(config_path: str, update: Callable[[dict], dict]) -> dict:
    with _user_config_lock(config_path):
        existing_config, primary_valid = _read_snapshot(config_path)
        next_config = update(dict(existing_config))
        if not isinstance(next_config, dict):
            raise TypeError("User config updater must return a dict")
        _write_atomic_json(config_path, next_config, primary_valid)
        return dict(next_config)
