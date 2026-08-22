"""Read-only census for the implementation checkout and evidence directory.

This intentionally reports dirty paths instead of changing or hiding them. It
is a handoff aid: generated caches, app data, and unrelated user edits remain
visible, while semantic fixture files and evidence freshness are counted
separately.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any


EXPECTED_BRANCH = "impl/authored-semantic-dual-output-20260822"
FIXTURE_ROOT = Path("contracts/authored_vnext/semantic/fixtures")


def is_source_file(path: Path) -> bool:
    return "__pycache__" not in path.parts and path.suffix != ".pyc"


def sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def git(cwd: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(cwd), *args], text=True).strip()


def git_status(cwd: Path) -> str:
    # Do not strip the leading porcelain status columns; the first path byte
    # is otherwise lost for ordinary `` M path`` entries.
    return subprocess.check_output(["git", "-C", str(cwd), "status", "--short"], text=True)


def census(repo: Path, evidence: Path | None) -> dict[str, Any]:
    status = git_status(repo)
    paths = [line[3:] for line in status.splitlines() if len(line) >= 4]
    fixture_prefix = str(FIXTURE_ROOT).replace("\\", "/")
    fixture_paths: list[str] = []
    for status_path in paths:
        candidate = repo / status_path
        if candidate.is_dir():
            fixture_paths.extend(
                str(file.relative_to(repo)).replace("\\", "/")
                for file in candidate.rglob("*")
                if file.is_file()
                and is_source_file(file)
                and str(file.relative_to(repo)).replace("\\", "/").startswith(fixture_prefix)
            )
        elif status_path.replace("\\", "/").startswith(fixture_prefix):
            fixture_paths.append(status_path)
    evidence_files = []
    if evidence is not None and evidence.is_dir():
        evidence_files = sorted(path for path in evidence.rglob("*") if path.is_file() and is_source_file(path))
    return {
        "repositoryRoot": str(repo.resolve()),
        "branch": git(repo, "branch", "--show-current"),
        "expectedBranch": EXPECTED_BRANCH,
        "branchMatches": git(repo, "branch", "--show-current") == EXPECTED_BRANCH,
        "head": git(repo, "rev-parse", "HEAD"),
        "dirtyPathCount": len(paths),
        "dirtyPaths": paths,
        "fixtureDirtyPathCount": len(fixture_paths),
        "fixtureDirtyPaths": fixture_paths,
        "evidenceFileCount": len(evidence_files),
        "evidenceFiles": [{"path": str(path.relative_to(evidence)), "sha256": sha256(path)} for path in evidence_files],
        "promotionReady": False,
        "fullNativeParityReady": False,
        "independentVnextGenerationReady": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--evidence", type=Path)
    args = parser.parse_args()
    print(json.dumps(census(args.repo.resolve(), args.evidence.resolve() if args.evidence else None), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
