"""Windows ACL hardening for the user config credential file.

Windows does not expose POSIX group/other mode bits through ``stat``, so a
successful ``chmod(0o600)`` there only toggles the read-only attribute.
``utils.user_config_store`` uses POSIX ``chmod`` on other platforms and this
module's ``icacls``/DACL machinery on Windows to enforce the same private
file contract. This module owns everything Windows-ACL specific: resolving
the current user's SID without depending on its localized name, reading and
verifying a file's DACL, and restricting a file to the current user +
SYSTEM.
"""

import csv
import json
import locale
import os
import subprocess
import threading


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
