"""Subprocess wrapper around ``scripts/ppt-agent.mjs`` for one style's deck.

One call per attempt: ``--async --content <prompt> --slides 12 --strategy svg
--style <id> --export pdf --out <scratch>/<id>/attempt-N --timeout <seconds>``
(plan §2). Arguments are passed as a list (no shell), so the Korean prompt
text needs no manual quoting on Windows.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

#: Matches ppt-agent.mjs's own ``reportResult`` output lines (scripts/lib/ppt-agent-client.mjs).
_PRESENTATION_ID_RE = re.compile(r"^\s*presentation_id:\s*(\S+)\s*$", re.MULTILINE)
_DOWNLOADED_TO_RE = re.compile(r"^\s*downloaded to:\s*(.+?)\s*$", re.MULTILINE)

DECK_SLIDES = 12
STRATEGY = "svg"
EXPORT_FORMAT = "pdf"


@dataclass(frozen=True)
class PptAgentResult:
    returncode: int
    stdout: str
    stderr: str
    presentation_id: Optional[str]
    downloaded_path: Optional[Path]


def build_ppt_agent_command(
    repo_root: Path,
    prompt: str,
    style_id: str,
    out_dir: Path,
    base: str,
    timeout_seconds: int,
) -> list[str]:
    """Build the ``node scripts/ppt-agent.mjs ...`` argv (list form, no shell)."""
    script = repo_root / "scripts" / "ppt-agent.mjs"
    return [
        "node",
        str(script),
        "--async",
        "--content",
        prompt,
        "--slides",
        str(DECK_SLIDES),
        "--strategy",
        STRATEGY,
        "--style",
        style_id,
        "--export",
        EXPORT_FORMAT,
        "--base",
        base,
        "--out",
        str(out_dir),
        "--timeout",
        str(timeout_seconds),
    ]


def parse_ppt_agent_output(stdout: str) -> tuple[Optional[str], Optional[Path]]:
    """Extract ``presentation_id`` and the downloaded artifact path from stdout."""
    id_match = _PRESENTATION_ID_RE.search(stdout)
    path_match = _DOWNLOADED_TO_RE.search(stdout)
    presentation_id = id_match.group(1) if id_match else None
    downloaded_path = Path(path_match.group(1)) if path_match else None
    return presentation_id, downloaded_path


def run_ppt_agent(
    repo_root: Path,
    prompt: str,
    style_id: str,
    out_dir: Path,
    base: str,
    timeout_seconds: int,
) -> PptAgentResult:
    """Run one ppt-agent.mjs attempt. Never raises on a non-zero process exit."""
    out_dir.mkdir(parents=True, exist_ok=True)
    command = build_ppt_agent_command(repo_root, prompt, style_id, out_dir, base, timeout_seconds)
    completed = subprocess.run(
        command,
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        # Generous outer bound; ppt-agent.mjs enforces --timeout itself and
        # exits cleanly, this only guards against it hanging outright.
        timeout=timeout_seconds + 60,
        check=False,
    )
    presentation_id, downloaded_path = parse_ppt_agent_output(completed.stdout)
    return PptAgentResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        presentation_id=presentation_id,
        downloaded_path=downloaded_path,
    )


def find_deck_pdf(out_dir: Path, downloaded_path: Optional[Path]) -> Optional[Path]:
    """The exported PDF: the parsed 'downloaded to:' path, or a PDF glob fallback."""
    if downloaded_path is not None and downloaded_path.is_file():
        return downloaded_path
    candidates = sorted(out_dir.glob("*.pdf"))
    return candidates[0] if candidates else None
