"""Bounded Chrome binary discovery for authored slide capture."""

from __future__ import annotations

import os
import pathlib
from typing import List, Optional

def _playwright_headless_shell_candidates() -> List[pathlib.Path]:
    """Return installed Playwright headless-shell executables, newest first.

    Desktop Chrome can hand a headless request to an already-running browser and
    exit successfully without writing a screenshot. Playwright's standalone
    headless shell does not share the user's browser profile/process, so it is the
    most reliable automatic choice for server-side slide rendering.
    """
    roots: List[pathlib.Path] = []
    configured_root = os.getenv("PLAYWRIGHT_BROWSERS_PATH")
    if configured_root and configured_root != "0":
        roots.append(pathlib.Path(configured_root).expanduser())

    local_app_data = os.getenv("LOCALAPPDATA")
    if local_app_data:
        roots.append(pathlib.Path(local_app_data) / "ms-playwright")
    roots.extend(
        [
            pathlib.Path.home() / ".cache" / "ms-playwright",
            pathlib.Path.home() / "Library" / "Caches" / "ms-playwright",
        ]
    )

    found: List[pathlib.Path] = []
    seen = set()
    for root in roots:
        try:
            resolved_root = root.resolve()
        except OSError:
            resolved_root = root
        if resolved_root in seen or not root.is_dir():
            continue
        seen.add(resolved_root)
        for candidate in root.glob(
            "chromium_headless_shell-*/*/chrome-headless-shell*"
        ):
            if candidate.name not in {
                "chrome-headless-shell",
                "chrome-headless-shell.exe",
            }:
                continue
            if candidate.is_file():
                found.append(candidate)

    def revision(candidate: pathlib.Path) -> int:
        for part in candidate.parts:
            prefix = "chromium_headless_shell-"
            if part.startswith(prefix):
                try:
                    return int(part[len(prefix) :])
                except ValueError:
                    return -1
        return -1

    return sorted(found, key=revision, reverse=True)


def find_chrome() -> Optional[str]:
    for env in ("CHROME_PATH", "PUPPETEER_EXECUTABLE_PATH", "CHROMIUM_PATH"):
        p = os.getenv(env)
        if p and os.path.isfile(p):
            return p
    headless_shells = _playwright_headless_shell_candidates()
    if headless_shells:
        return str(headless_shells[0])
    for cand in (
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
    ):
        if os.path.isfile(cand):
            return cand
    return None
