"""Content identity of installed fonts for durable authored render evidence."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from utils.hashing import sha256_hex, sha256_prefixed


@lru_cache(maxsize=4096)
def _content(path: str, size: int, modified: int) -> str:
    return sha256_hex(Path(path).read_bytes())


def installed_font_identity() -> str:
    if os.name == "nt":
        roots = [Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"]
        if os.environ.get("LOCALAPPDATA"):
            roots.append(Path(os.environ["LOCALAPPDATA"]) / "Microsoft/Windows/Fonts")
    else:
        roots = [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"),
                 Path.home() / ".fonts", Path.home() / ".local/share/fonts",
                 Path("/System/Library/Fonts"), Path("/Library/Fonts")]
    entries = []
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.suffix.lower() in {".ttf", ".otf", ".ttc", ".woff", ".woff2"}:
                stat = path.stat()
                entries.append(str(path) + ":" + _content(str(path), stat.st_size, stat.st_mtime_ns))
    if not entries:
        raise ValueError("authored_font_inventory_unavailable")
    return sha256_prefixed("\n".join(entries).encode())
