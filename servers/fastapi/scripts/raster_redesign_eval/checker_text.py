"""Text-fidelity primitives shared by checker.py and ground_truth_checker.py:
char recall/precision, numeric-token recall, raw slide text extraction, and a
narrow "suspicious fragment" scan for known corruption signatures.

Split out of the original checker.py (2026-09-25 eval suite) to keep each
module under the repo's file-size limit; scoring logic is unchanged.
"""
from __future__ import annotations

import re
from collections import Counter

NUMBER_RE = re.compile(r"\d[\d,.]*%?")

# Known corruption signatures observed in prior runs (2026-09-25 final-verification
# series): a percent sign rendered/exported as a literal "96" glued onto the
# number (e.g. source "83%" -> output "8396", "50%" -> "5096"), and assorted
# symbol-glued text fragments where a leading character got mangled into a
# stray backslash/bracket/paren, or a bare garbled numeric fragment leaked into
# body text. These regexes are deliberately narrow (checked against the actual
# defects found) to avoid false-positives on legitimate numbers.
_SUSPICIOUS_PATTERNS = [
    ("percent_as_96", re.compile(r"(?<![0-9])\d{2,3}96(?![0-9])")),
    ("backslash_glued_fragment", re.compile(r"\\[A-Za-z가-힣]")),
    ("bracket_glued_fragment", re.compile(r"\[[가-힣]")),
    ("paren_dash_fragment", re.compile(r"-\(\)-")),
    ("bare_trailing_bracket_number", re.compile(r"(?<![0-9A-Za-z])\d{2,4}\]")),
    ("bare_trailing_paren_number", re.compile(r"(?<![0-9A-Za-z])\d{1,3}\)(?!\w)")),
]


def scan_suspicious_fragments(text: str) -> list[dict]:
    """Flag symbol-glued / corrupted-number fragments in editable text.

    Returns a list of {kind, match, context} -- never raises, and an empty
    source text yields an empty list."""
    hits = []
    for kind, pattern in _SUSPICIOUS_PATTERNS:
        for m in pattern.finditer(text or ""):
            start, end = max(0, m.start() - 8), min(len(text), m.end() + 8)
            hits.append({"kind": kind, "match": m.group(0), "context": text[start:end]})
    return hits


def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def char_recall(source_text: str, candidate_text: str) -> dict:
    src = normalize_whitespace(source_text).replace(" ", "")
    cand = normalize_whitespace(candidate_text).replace(" ", "")
    src_counter = Counter(src)
    cand_counter = Counter(cand)
    total = sum(src_counter.values())
    if total == 0:
        return {"recall": None, "source_chars": 0, "matched_chars": 0}
    matched = sum(min(c, cand_counter.get(ch, 0)) for ch, c in src_counter.items())
    return {"recall": matched / total, "source_chars": total, "matched_chars": matched}


def numeric_recall(source_tokens: list[str], candidate_text: str) -> dict:
    cand_tokens = Counter(NUMBER_RE.findall(candidate_text or ""))
    src_counter = Counter(source_tokens)
    total = sum(src_counter.values())
    if total == 0:
        return {"recall": None, "source_numbers": 0, "matched_numbers": 0, "missing": []}
    matched = 0
    missing = []
    remaining_cand = Counter(cand_tokens)
    for token, count in src_counter.items():
        have = remaining_cand.get(token, 0)
        take = min(have, count)
        matched += take
        remaining_cand[token] = have - take
        if take < count:
            missing.extend([token] * (count - take))
    return {
        "recall": matched / total,
        "source_numbers": total,
        "matched_numbers": matched,
        "missing": missing[:50],
    }


def slide_text(slide) -> str:
    parts = []
    for shape in slide.shapes:
        if shape.has_text_frame:
            for para in shape.text_frame.paragraphs:
                for run in para.runs:
                    if run.text:
                        parts.append(run.text)
        if getattr(shape, "has_table", False):
            for row in shape.table.rows:
                for cell in row.cells:
                    if cell.text:
                        parts.append(cell.text)
    return " ".join(parts)
