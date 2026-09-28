"""Parse the frozen demo topic file for the sample-deck generator.

Mirrors the prompt-slicing rule in ``utils.authored_style_samples.topic_prompt_digest``
(same ``## Prompt`` / ``## Fixed`` markers) so the digest recorded in a manifest
always matches the prompt text this module hands to ``ppt-agent.mjs``. It also
reads the two bullets the design and plan documents promise are parsed, not
hardcoded: the manifest slide titles and the order-guard keywords
(``docs/authored-style-samples-generation-plan-20260928.md`` §2).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Tuple

_PROMPT_START = "## Prompt"
_PROMPT_END = "## Fixed"
_TITLES_LABEL = "Manifest slide titles, in order"
_GUARD_LABEL = "Order guard (PDF text must contain)"
_GUARD_ENTRY_RE = re.compile(r"slide\s+(\d+)\s*`([^`]+)`")


@dataclass(frozen=True)
class FixedParams:
    """The topic file's ``## Fixed request parameters`` bullets this generator needs."""

    titles: Tuple[str, ...]
    guard_keywords: Mapping[int, Tuple[str, ...]]


def extract_prompt(topic_text: str) -> str:
    """Return the prompt text between ``## Prompt`` and ``## Fixed``.

    Identical slicing to ``utils.authored_style_samples.topic_prompt_digest``,
    so ``sha256(extract_prompt(text).encode()) == topic_prompt_digest(...)``'s
    inner hash for the same file (parity asserted in the unit tests).
    """
    text = topic_text.replace("\r\n", "\n")
    start = text.find(_PROMPT_START)
    end = text.find(_PROMPT_END, start + 1 if start >= 0 else 0)
    if start < 0 or end < 0:
        raise ValueError("topic file: '## Prompt' / '## Fixed' section markers not found")
    prompt = text[text.index("\n", start) + 1 : end].strip()
    if not prompt:
        raise ValueError("topic file: prompt section is empty")
    return prompt


def _bullet_value(section_text: str, label: str) -> str:
    pattern = re.compile(rf"^- {re.escape(label)}:\s*(.+)$", re.MULTILINE)
    match = pattern.search(section_text)
    if not match:
        raise ValueError(f"topic file: missing '- {label}:' bullet in the Fixed section")
    return match.group(1).strip()


def parse_fixed_params(topic_text: str) -> FixedParams:
    """Parse the manifest titles and order-guard keywords from the Fixed section."""
    text = topic_text.replace("\r\n", "\n")
    end_idx = text.find(_PROMPT_END)
    if end_idx < 0:
        raise ValueError("topic file: '## Fixed' section not found")
    section = text[end_idx:]

    titles_raw = _bullet_value(section, _TITLES_LABEL)
    titles = tuple(t.strip() for t in titles_raw.split(",") if t.strip())
    if not titles:
        raise ValueError("topic file: no manifest slide titles found")

    guard_raw = _bullet_value(section, _GUARD_LABEL)
    guard_keywords: dict[int, list[str]] = {}
    for slide_no_text, keyword in _GUARD_ENTRY_RE.findall(guard_raw):
        guard_keywords.setdefault(int(slide_no_text), []).append(keyword)
    if not guard_keywords:
        raise ValueError("topic file: no order-guard keywords found")

    frozen_guards = {slide_no: tuple(keywords) for slide_no, keywords in guard_keywords.items()}
    return FixedParams(titles=titles, guard_keywords=frozen_guards)


def load_topic(topic_file: Path) -> tuple[str, FixedParams]:
    """Read the topic file and return ``(prompt_text, FixedParams)``."""
    text = topic_file.read_text(encoding="utf-8")
    return extract_prompt(text), parse_fixed_params(text)
