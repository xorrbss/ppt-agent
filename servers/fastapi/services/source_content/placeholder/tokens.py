"""Bounded token grammar; recognition receives original tree text slots only."""

from __future__ import annotations

from dataclasses import dataclass
import re

from services.source_content.source_stamp_dom import BrowserSourceReadbackError

ID_PATTERN = r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,191}"
TOKEN = re.compile(r"\{\{q:(" + ID_PATTERN + r")(?:@(0|[1-9][0-9]{0,6})-(0|[1-9][0-9]{0,6}))?\}\}")


def reject(kind: str, detail: str, block_ids=()) -> None:
    raise BrowserSourceReadbackError(
        "source_placeholder_" + kind, detail=detail[:240],
        block_ids=tuple(dict.fromkeys(block_ids))[:32],
    )


@dataclass(frozen=True, slots=True)
class PlaceholderToken:
    block_id: str
    start: int | None
    end: int | None
    begin: int
    finish: int


def parse_text_tokens(text: str) -> tuple[PlaceholderToken, ...]:
    """Parse one visible text slot, refusing malformed or nested q syntax."""
    matches = list(TOKEN.finditer(text))
    remainder = TOKEN.sub("", text)
    if "{{q:" in remainder or any(
        (match.start() and text[match.start() - 1] == "{")
        or (match.end() < len(text) and text[match.end()] == "}")
        for match in matches
    ):
        reject("context", "Malformed, nested, or split placeholder syntax")
    return tuple(PlaceholderToken(
        match[1], int(match[2]) if match[2] is not None else None,
        int(match[3]) if match[3] is not None else None, match.start(), match.end(),
    ) for match in matches)
