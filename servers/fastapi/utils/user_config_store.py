import csv
import errno
import json
import locale
import os
import shutil
import subprocess
import threading
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
_WINDOWS_PRIVATE_ACL_CACHE: dict[str, tuple[int, int, int, int]] = {}
_WINDOWS_PRIVATE_ACL_CACHE_LOCK = threading.Lock()


def _subprocess_text_kwargs() -> dict[str, object]:
    """Decode localized Windows command output without crashing reader threads."""
    return {
        "text": True,
        "encoding": locale.getpreferredencoding(False) or "utf-8",
        "errors": "replace",
    }


def _windows_current_user_sid() -> str | None:
    """Return the current Windows principal SID without depending on its name."""
    try:
        result = subprocess.run(
            ["whoami", "/user", "/fo", "csv", "/nh"],
            check=True,
            capture_output=True,
            timeout=5,
            **_subprocess_text_kwargs(),
        )
        rows = list(csv.reader(result.stdout.splitlines()))
        if rows and len(rows[0]) >= 2 and rows[0][1].startswith("S-"):
            return rows[0][1]
    except (OSError, subprocess.SubprocessError):
        return None
    return None


def _windows_acl_snapshot(file_path: str) -> dict | None:
    """Read a Windows DACL without relying on localized ``icacls`` output.

    A legacy config may already have the exact private ACL while being owned by
    the Administrators group.  A non-elevated process cannot rewrite that DACL,
    so an ``icacls`` access-denied result alone does not mean the file is
    exposed.  PowerShell's .NET ACL API gives us stable SID values that can be
    verified before accepting the existing permissions.
    """
    try:
        import win32security

        security = win32security.GetFileSecurity(
            file_path,
            win32security.OWNER_SECURITY_INFORMATION
            | win32security.DACL_SECURITY_INFORMATION,
        )
        control, _revision = security.GetSecurityDescriptorControl()
        dacl = security.GetSecurityDescriptorDacl()
        if dacl is None:
            return None
        rules = []
        for index in range(dacl.GetAceCount()):
            ace = dacl.GetAce(index)
            ace_type, ace_flags = ace[0]
            if ace_type == win32security.ACCESS_ALLOWED_ACE_TYPE:
                access_type = "Allow"
            elif ace_type == win32security.ACCESS_DENIED_ACE_TYPE:
                access_type = "Deny"
            else:
                # Unknown/object ACEs cannot broaden access unnoticed.  Mark
                # them explicitly so the private verifier rejects allow-like
                # entries it does not understand.
                access_type = f"Unknown:{ace_type}"
            rules.append(
                {
                    "sid": win32security.ConvertSidToStringSid(ace[-1]),
                    "type": access_type,
                    "inherited": bool(ace_flags & win32security.INHERITED_ACE),
                }
            )
        return {
            "owner": win32security.ConvertSidToStringSid(
                security.GetSecurityDescriptorOwner()
            ),
            "protected": bool(control & win32security.SE_DACL_PROTECTED),
            "rules": rules,
        }
    except (ImportError, OSError):
        # PyInstaller and unusually small Windows runtimes may omit pywin32.
        # Fall through to the built-in PowerShell/.NET security API.
        pass

    script = (
        "$ErrorActionPreference='Stop';"
        "$acl=Get-Acl -LiteralPath $env:PRESENTON_USER_CONFIG_ACL_PATH;"
        "$rules=@($acl.Access | ForEach-Object {"
        "try{$sid=$_.IdentityReference.Translate("
        "[System.Security.Principal.SecurityIdentifier]).Value}"
        "catch{$sid=$_.IdentityReference.Value};"
        "[pscustomobject]@{sid=$sid;type=$_.AccessControlType.ToString();"
        "inherited=$_.IsInherited}});"
        "[pscustomobject]@{owner=$acl.Owner;"
        "protected=$acl.AreAccessRulesProtected;rules=$rules} | "
        "ConvertTo-Json -Compress -Depth 4"
    )
    environment = dict(os.environ)
    environment["PRESENTON_USER_CONFIG_ACL_PATH"] = os.path.abspath(file_path)
    try:
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                script,
            ],
            check=True,
            capture_output=True,
            timeout=10,
            env=environment,
            **_subprocess_text_kwargs(),
        )
        snapshot = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return None
    return snapshot if isinstance(snapshot, dict) else None


def _windows_acl_is_private(file_path: str, current_user_sid: str) -> bool:
    """Accept only a protected DACL whose allow-list is user + SYSTEM."""
    snapshot = _windows_acl_snapshot(file_path)
    if not snapshot or snapshot.get("protected") is not True:
        return False

    raw_rules = snapshot.get("rules")
    if isinstance(raw_rules, dict):
        rules = [raw_rules]
    elif isinstance(raw_rules, list):
        rules = raw_rules
    else:
        return False

    expected_sids = {current_user_sid.upper(), "S-1-5-18"}
    allowed_sids: set[str] = set()
    for rule in rules:
        if not isinstance(rule, dict) or str(rule.get("type", "")).lower() != "allow":
            continue
        sid = str(rule.get("sid", "")).upper()
        if rule.get("inherited") is True or sid not in expected_sids:
            return False
        allowed_sids.add(sid)
    return allowed_sids == expected_sids


def _windows_file_fingerprint(file_path: str) -> tuple[int, int, int, int] | None:
    """Fingerprint ACL-relevant file identity for the verified-private cache.

    NTFS updates change time when a security descriptor changes.  Atomic
    replacement also changes file identity, so either event invalidates the
    cache.  Reparse-point configs are never cached or accepted implicitly.
    """
    try:
        if os.path.islink(file_path):
            return None
        metadata = os.stat(file_path, follow_symlinks=False)
    except OSError:
        return None
    return (
        int(metadata.st_dev),
        int(metadata.st_ino),
        int(metadata.st_ctime_ns),
        int(metadata.st_size),
    )


def _windows_private_acl_is_cached(file_path: str) -> bool:
    fingerprint = _windows_file_fingerprint(file_path)
    if fingerprint is None:
        return False
    cache_key = os.path.abspath(file_path)
    with _WINDOWS_PRIVATE_ACL_CACHE_LOCK:
        return _WINDOWS_PRIVATE_ACL_CACHE.get(cache_key) == fingerprint


def _cache_windows_private_acl(file_path: str) -> None:
    fingerprint = _windows_file_fingerprint(file_path)
    if fingerprint is None:
        return
    with _WINDOWS_PRIVATE_ACL_CACHE_LOCK:
        _WINDOWS_PRIVATE_ACL_CACHE[os.path.abspath(file_path)] = fingerprint


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

    if _windows_private_acl_is_cached(file_path):
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
                # Atomic replacement and stale-temp cleanup require DELETE in
                # addition to read/write. Modify keeps the file private to the
                # current user while allowing those operations on Windows.
                f"*{sid}:(M)",
                "/grant:r",
                "*S-1-5-18:(F)",
            ],
            check=False,
            capture_output=True,
            timeout=10,
            **_subprocess_text_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if result.returncode == 0:
        _cache_windows_private_acl(file_path)
        return True
    # Do not weaken or take ownership of a legacy file just to silence an ACL
    # warning.  Accept it only when the effective allow-list is already the
    # exact private contract we would have written.
    if _windows_acl_is_private(file_path, sid):
        _cache_windows_private_acl(file_path)
        return True
    return False


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
        try:
            _retry("replace user config", lambda: os.replace(temp_path, config_path))
        except RuntimeError:
            # Some legacy Windows configs are owned by Administrators with a
            # protected read/write-only DACL. A non-elevated app can update the
            # file but cannot delete the destination, so os.replace is denied.
            # Keep the lock + recovery backup and use a fsynced in-place write
            # only after verifying that the existing credential file is still
            # private. New files continue to use the atomic path above.
            if (
                os.name != "nt"
                or not os.path.isfile(config_path)
                or os.path.islink(config_path)
                or not _secure_config_file(config_path)
            ):
                raise

            def overwrite_private_config() -> None:
                with open(temp_path, "rb") as source:
                    payload = source.read()
                with open(config_path, "wb") as destination:
                    destination.write(payload)
                    destination.flush()
                    os.fsync(destination.fileno())

            _retry("overwrite private user config", overwrite_private_config)
            _retry("remove replaced user config temp", lambda: os.unlink(temp_path))
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
