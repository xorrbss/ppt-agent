"""Verify an immutable PostgreSQL base backup and emit a portable receipt."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import locale
from pathlib import Path
import subprocess
import sys


REQUIRED_RAW_FILES = ("PG_VERSION", "backup_label", "backup_manifest")
MUTATED_CLONE_MARKERS = ("backup_label.old", "postmaster.pid")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tree_receipt(root: Path) -> dict[str, object]:
    digest = hashlib.sha256()
    file_count = 0
    total_bytes = 0
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_symlink():
            raise RuntimeError(f"backup_symlink_not_allowed:{path.relative_to(root)}")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        size = path.stat().st_size
        file_digest = _sha256(path)
        digest.update(f"{relative}\0{size}\0{file_digest}\n".encode())
        file_count += 1
        total_bytes += size
    return {
        "file_count": file_count,
        "total_bytes": total_bytes,
        "tree_sha256": digest.hexdigest(),
    }


def _resolved_directory(value: str, *, name: str) -> Path:
    path = Path(value).resolve()
    if not path.is_dir():
        raise RuntimeError(f"{name}_directory_missing:{path}")
    return path


def _verify_command(pg_bin: Path, raw_backup: Path) -> tuple[list[str], subprocess.CompletedProcess[str]]:
    executable = pg_bin / ("pg_verifybackup.exe" if sys.platform == "win32" else "pg_verifybackup")
    if not executable.is_file():
        raise RuntimeError(f"pg_verifybackup_missing:{executable}")
    command = [str(executable), str(raw_backup)]
    return command, subprocess.run(
        command,
        check=False,
        capture_output=True,
        encoding="mbcs" if sys.platform == "win32" else locale.getpreferredencoding(False),
        errors="replace",
        text=True,
        timeout=300,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pg-bin", required=True)
    parser.add_argument("--raw-backup", required=True)
    parser.add_argument("--clone", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--evidence-file", action="append", default=[])
    args = parser.parse_args()

    raw_backup = _resolved_directory(args.raw_backup, name="raw_backup")
    clone = _resolved_directory(args.clone, name="clone")
    pg_bin = _resolved_directory(args.pg_bin, name="pg_bin")
    receipt_path = Path(args.receipt).resolve()
    if raw_backup == clone:
        raise RuntimeError("raw_backup_and_clone_must_differ")
    if raw_backup in receipt_path.parents:
        raise RuntimeError("receipt_must_not_mutate_raw_backup")

    evidence_files = [Path(value).resolve() for value in args.evidence_file]
    if any(raw_backup in path.parents for path in evidence_files):
        raise RuntimeError("evidence_must_not_mutate_raw_backup")
    missing_evidence = [str(path) for path in evidence_files if not path.is_file()]

    missing = [name for name in REQUIRED_RAW_FILES if not (raw_backup / name).is_file()]
    mutation_markers = [
        name for name in MUTATED_CLONE_MARKERS if (raw_backup / name).exists()
    ]
    command, verified = _verify_command(pg_bin, raw_backup)
    receipt = {
        "schema": "postgresql-physical-backup-verification/v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": (
            "pass"
            if (
                verified.returncode == 0
                and not missing
                and not mutation_markers
                and not missing_evidence
            )
            else "fail"
        ),
        "raw_backup": str(raw_backup),
        "clone": str(clone),
        "raw_backup_is_separate_from_clone": raw_backup != clone,
        "required_files_missing": missing,
        "mutation_markers_present": mutation_markers,
        "evidence_files_missing": missing_evidence,
        "evidence_files": [
            {
                "path": str(path),
                "size_bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
            for path in evidence_files
            if path.is_file()
        ],
        "pg_version": (raw_backup / "PG_VERSION").read_text().strip()
        if (raw_backup / "PG_VERSION").is_file()
        else None,
        "backup_manifest_sha256": _sha256(raw_backup / "backup_manifest")
        if (raw_backup / "backup_manifest").is_file()
        else None,
        "raw_tree": _tree_receipt(raw_backup),
        "verification": {
            "command": command,
            "exit_code": verified.returncode,
            "stdout": (verified.stdout or "").strip(),
            "stderr": (verified.stderr or "").strip(),
        },
    }
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = receipt_path.with_suffix(receipt_path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(receipt_path)
    print(json.dumps(receipt, sort_keys=True))
    return 0 if receipt["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
