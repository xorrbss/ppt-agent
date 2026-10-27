"""Versioned instructions and content-free diagnostics for slide display text.

Callers supply text that will actually be visible. This module does not parse
SVG/HTML or edit source material. Safe local replacements live with their rules.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from enum import Enum
from typing import Sequence


from utils.ppt_writing_prompt import (
    WRITING_POLICY_ID as WRITING_POLICY_ID,
    writing_policy_prompt as writing_policy_prompt,
)


class SymbolCategory(str, Enum):
    MIDDLE_DOT = "middle_dot"
    INLINE_BULLET = "inline_bullet"
    LONG_DASH = "long_dash"
    TEXT_ARROW = "text_arrow"
    PIPE_SEPARATOR = "pipe_separator"
    LIST_SLASH = "list_slash"
    DECORATIVE_MARK = "decorative_mark"
    DECORATIVE_CHECK = "decorative_check"
    ELLIPSIS = "ellipsis"
    REPEATED_PUNCTUATION = "repeated_punctuation"
    EMOJI = "emoji"
    MARKDOWN_EMPHASIS = "markdown_emphasis"


class StyleSignal(str, Enum):
    ABSTRACT_BENEFIT = "abstract_benefit"
    STOCK_OPENING = "stock_opening"
    STOCK_CONCLUSION = "stock_conclusion"
    UNSUPPORTED_CONTRAST = "unsupported_contrast"


@dataclass(frozen=True)
class ProtectedSpan:
    """Offsets in visible text, backed by a caller-owned source or native value.

    ``source_id`` is an opaque identifier, never source wording. The caller must
    establish provenance; a generated paragraph must not be self-protected.
    """

    start: int
    end: int
    source_id: str


@dataclass(frozen=True)
class SymbolFinding:
    category: SymbolCategory
    start: int
    end: int
    source_id: str | None = None


@dataclass(frozen=True)
class StyleFinding:
    signal: StyleSignal
    start: int
    end: int


@dataclass(frozen=True)
class WritingScan:
    policy_id: str
    violations: tuple[SymbolFinding, ...]
    protected_symbols: tuple[SymbolFinding, ...]
    style_signals: tuple[StyleFinding, ...]

    @property
    def violation_counts(self) -> dict[str, int]:
        return dict(Counter(hit.category.value for hit in self.violations))

    @property
    def protected_counts(self) -> dict[str, int]:
        return dict(Counter(hit.category.value for hit in self.protected_symbols))


@dataclass(frozen=True)
class SymbolRule:
    category: SymbolCategory
    characters: str
    replacement: str | None = None
    context: str | None = None
    # The writing prompt prefers ordinary punctuation for newly written lists;
    # this is a style rule, not a font/editability repair. Each mapping names
    # its bounded Korean prose context; semantic values are protected first.
    left_context: str = r"(?<![A-Za-z0-9가-힣])[가-힣]+[ \t]*$"
    right_context: str = r"^[ \t]*[가-힣]+(?![A-Za-z0-9가-힣])"


_SINGLE_SYMBOLS: tuple[SymbolRule, ...] = (
    SymbolRule(SymbolCategory.MIDDLE_DOT, "·ㆍ‧∙⋅", ", ", "korean_list"),
    # These can encode grouping, relation, omission or emphasis. No mechanical
    # replacement proves the intended meaning; retain the ordinary correction.
    SymbolRule(SymbolCategory.INLINE_BULLET, "•●◦▪‣"),
    SymbolRule(SymbolCategory.LONG_DASH, "–—―"),
    SymbolRule(SymbolCategory.TEXT_ARROW, "→⇒➜➔⟶←↔↑↓⇢⟵"),
    SymbolRule(SymbolCategory.PIPE_SEPARATOR, "|｜│", ", ", "complete_korean_phrases"),
    SymbolRule(SymbolCategory.DECORATIVE_MARK, "※★☆◆◇▶"),
    SymbolRule(SymbolCategory.DECORATIVE_CHECK, "✓✔☑✅"),
)
# List tokens are complete Korean phrases or quantities with a Korean unit/
# counter or percent sign. A bare numeric pair is deliberately excluded; the
# scanner also protects known numeric and Latin/SI products before mapping.
_KOREAN_LIST_LEFT = re.compile(r"(?<![A-Za-z0-9가-힣])[가-힣0-9.,% \t]*[가-힣%][가-힣0-9.,% \t]*$")
_KOREAN_LIST_RIGHT = re.compile(r"^[가-힣0-9.,% \t]*[가-힣%][가-힣0-9.,% \t]*(?![A-Za-z0-9가-힣])")
_KOREAN_PHRASE_LEFT = re.compile(r"(?<![A-Za-z0-9가-힣])[가-힣]+(?:[ \t]+[가-힣]+)+[ \t]*$")
_KOREAN_PHRASE_RIGHT = re.compile(r"^[ \t]*[가-힣]+(?:[ \t]+[가-힣]+)+(?![A-Za-z0-9가-힣])")
_MULTI_SYMBOLS: tuple[tuple[SymbolCategory, re.Pattern[str]], ...] = (
    (SymbolCategory.TEXT_ARROW, re.compile(r"->|=>")),
    (SymbolCategory.ELLIPSIS, re.compile(r"…|\.{3,}")),
    (SymbolCategory.REPEATED_PUNCTUATION, re.compile(r"[!?]{2,}|(?<!\w)--(?!\w)")),
    (SymbolCategory.MARKDOWN_EMPHASIS, re.compile(r"\*\*(?=\S)|(?<=\S)\*\*|__(?=\S)|(?<=\S)__")),
)
_STYLE_PATTERNS: tuple[tuple[StyleSignal, re.Pattern[str]], ...] = (
    (StyleSignal.ABSTRACT_BENEFIT, re.compile(r"혁신|최적화|시너지|극대화|고객 중심|지속 가능한 성장|새로운 패러다임")),
    (StyleSignal.STOCK_OPENING, re.compile(r"(?m)(?:^|[.!?]\s+)(?:핵심은|중요한 것은|궁극적으로)")),
    (StyleSignal.STOCK_CONCLUSION, re.compile(r"이를 통해.{0,50}실현합니다")),
    (StyleSignal.UNSUPPORTED_CONTRAST, re.compile(r"단순히.{1,60}넘어|.{1,40}가 아니라.{1,40}")),
)
_URL_OR_ADDRESS = re.compile(r"(?:https?://|www\.)\S+|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
_FILE_PATH = re.compile(r"(?:[A-Za-z]:\\|\\\\)[^\s,;]+|(?<!\w)/(?:[\w.-]+/)+[\w.-]+")
_NUMERIC_SLASH = re.compile(r"(?<!\w)\d+(?:[.,]\d+)?/\d+(?:[.,]\d+)?(?!\w)")
# Month and quarter stages use the dash as part of a value, not as prose punctuation.
# Require the same M/Q prefix and digits on both sides; word-to-word dashes remain reportable.
_PERIOD_RANGE = re.compile(r"(?<![A-Za-z0-9])([MQ])\d+[ \t]*[–—―][ \t]*\1\d+(?![A-Za-z0-9])")
_UNIT_SLASH = re.compile(r"(?i)\b(?:km|m|cm|mm|kg|g|mg|l|ml|gb|mb|tb|kwh|wh|mbps|gbps)/(?:h|s|d|m|km|cm|mm|kg|g|mg|l|ml|gb|mb|tb)\b")
# Exact, conventional unit products only; arbitrary word pairs stay reportable.
_UNIT_PRODUCT = re.compile(
    r"(?<![A-Za-z0-9가-힣])(?:kN[·⋅]m|N[·⋅]m|kg[·⋅]m|Pa[·⋅]s|J[·⋅]s|"
    r"W[·⋅]s|V[·⋅]A|A[·⋅]s|m[·⋅]s)(?![A-Za-z0-9가-힣])"
)
# Korean vehicle-month and similar measures use a quantity followed by a
# counter/unit, then a bare time/unit word. Right-side tokens stay explicit:
# these common calendar, duration and count units are meaningful products,
# while arbitrary Korean words after a dot remain eligible for list review.
_KOREAN_QUANTITY_SUFFIXES = ("시간", "분", "대", "건", "명", "인", "회", "월", "일", "년", "주", "km")
_KOREAN_BARE_UNIT_WORDS = ("시간", "분", "월", "일", "년", "주", "회", "명", "인", "대", "건", "km")
_KOREAN_UNIT_PRODUCT = re.compile(
    r"(?<![A-Za-z0-9가-힣])\d+(?:[.,]\d+)?(?:"
    + "|".join(map(re.escape, _KOREAN_QUANTITY_SUFFIXES))
    + r")[·ㆍ‧∙⋅](?:"
    + "|".join(map(re.escape, _KOREAN_BARE_UNIT_WORDS))
    + r")(?![A-Za-z0-9가-힣])"
)
_NUMERIC_PRODUCT = re.compile(
    r"(?<![A-Za-z0-9가-힣])\d+(?:[.,]\d+)?\s*[·ㆍ‧∙⋅]\s*\d+(?:[.,]\d+)?(?![A-Za-z0-9가-힣])"
)
# The mathematical dot protects single-letter variables, not word lists.
_VARIABLE_PRODUCT = re.compile(
    r"(?<![A-Za-z0-9가-힣])(?:"
    r"[A-Za-zα-ωΑ-Ω]\s*⋅\s*(?:[A-Za-zα-ωΑ-Ω]|\d+(?:\.\d+)?)|"
    r"\d+(?:\.\d+)?\s*⋅\s*[A-Za-zα-ωΑ-Ω]"
    r")(?![A-Za-z0-9가-힣])"
)
_EMOJI = re.compile(r"[\u2600-\u27BF\U0001F300-\U0001FAFF]")
_SLASH_LIST = re.compile(r"(?<=[^\s/])\s*/\s*(?=[^\s/])|(?<=\s)/(?=\s)|(?<=\s)\\(?=\s)")


def _contains(ranges: Sequence[tuple[int, int]], start: int, end: int) -> bool:
    return any(a <= start and end <= b for a, b in ranges)


def _is_inline_bullet(text: str, index: int) -> bool:
    return bool(text[text.rfind("\n", 0, index) + 1 : index].strip())


def _validate_spans(text: str, spans: Sequence[ProtectedSpan]) -> None:
    previous_end = 0
    for span in sorted(spans, key=lambda item: item.start):
        if (
            not isinstance(span.start, int)
            or not isinstance(span.end, int)
            or span.start < previous_end
            or span.start < 0
            or span.end <= span.start
            or span.end > len(text)
            or not span.source_id.strip()
        ):
            raise ValueError("invalid protected span")
        if span.start == 0 and span.end == len(text):
            raise ValueError("whole-text protection requires matching source_text")
        previous_end = span.end


def scan_visible_text(
    text: str,
    *,
    protected_spans: Sequence[ProtectedSpan] = (),
    source_text: str | None = None,
) -> WritingScan:
    """Report visible symbol violations and subjective style signals.

    ``source_text`` may protect the entire field only when it exactly equals
    ``text``. Partial protection needs source-backed ``ProtectedSpan`` offsets.
    Neither path edits text or claims semantic or human-style validation.
    """
    if not isinstance(text, str):
        raise TypeError("visible text must be a string")
    if source_text is not None and source_text != text:
        raise ValueError("source_text must exactly match visible text")
    _validate_spans(text, protected_spans)
    semantic_ranges = [
        match.span()
        for pattern in (
            _URL_OR_ADDRESS,
            _FILE_PATH,
            _NUMERIC_SLASH,
            _PERIOD_RANGE,
            _UNIT_SLASH,
            _UNIT_PRODUCT,
            _KOREAN_UNIT_PRODUCT,
            _NUMERIC_PRODUCT,
            _VARIABLE_PRODUCT,
        )
        for match in pattern.finditer(text)
    ]
    candidates: list[tuple[int, int, SymbolCategory]] = []
    for rule in _SINGLE_SYMBOLS:
        candidates.extend(
            (index, index + 1, rule.category)
            for index, char in enumerate(text)
            if char in rule.characters
            and (rule.category is not SymbolCategory.INLINE_BULLET or _is_inline_bullet(text, index))
        )
    for category, pattern in _MULTI_SYMBOLS:
        candidates.extend((match.start(), match.end(), category) for match in pattern.finditer(text))
    candidates.extend((match.start(), match.end(), SymbolCategory.EMOJI) for match in _EMOJI.finditer(text))
    candidates.extend((match.start(), match.end(), SymbolCategory.LIST_SLASH) for match in _SLASH_LIST.finditer(text))
    candidates.sort(key=lambda item: (item[0], -(item[1] - item[0])))

    violations: list[SymbolFinding] = []
    protected: list[SymbolFinding] = []
    covered_end = 0
    for start, end, category in candidates:
        if start < covered_end:
            continue
        covered_end = end
        if category is SymbolCategory.LONG_DASH and start > 0 and end < len(text) and text[start - 1].isdigit() and text[end].isdigit():
            protected.append(SymbolFinding(category, start, end, "numeric_range"))
            continue
        if _contains(semantic_ranges, start, end):
            protected.append(SymbolFinding(category, start, end, "semantic_value"))
            continue
        if source_text is not None:
            protected.append(SymbolFinding(category, start, end, "verbatim_source"))
            continue
        source_id = next((span.source_id for span in protected_spans if span.start <= start and end <= span.end), None)
        finding = SymbolFinding(category, start, end, source_id)
        (protected if source_id else violations).append(finding)

    signals = tuple(
        StyleFinding(signal, match.start(), match.end())
        for signal, pattern in _STYLE_PATTERNS
        for match in pattern.finditer(text)
    )
    return WritingScan(
        policy_id=WRITING_POLICY_ID,
        violations=tuple(violations),
        protected_symbols=tuple(protected),
        style_signals=tuple(sorted(signals, key=lambda item: item.start)),
    )


def visible_symbol_substitutions(text: str, scan: WritingScan) -> tuple[tuple[int, int, str], ...]:
    """Return edits only for unprotected findings with a policy-owned safe mapping."""
    edits = []
    for hit in scan.violations:
        rule = next((rule for rule in _SINGLE_SYMBOLS if rule.category == hit.category), None)
        left, right = text[:hit.start], text[hit.end:]
        if rule is not None and rule.context == "korean_list":
            safe_context = bool(_KOREAN_LIST_LEFT.search(left) and _KOREAN_LIST_RIGHT.search(right))
        elif rule is not None and rule.context == "complete_korean_phrases":
            # Pipes are prose-style violations, but only complete Korean
            # multiword phrases provide enough context for comma substitution.
            safe_context = bool(_KOREAN_PHRASE_LEFT.search(left) and _KOREAN_PHRASE_RIGHT.search(right))
        else:
            safe_context = bool(rule is not None and re.search(rule.left_context, left)
                                and re.search(rule.right_context, right))
        if rule is not None and rule.replacement is not None and safe_context:
            start = len(text[:hit.start].rstrip(" \t"))
            end = hit.end + len(text[hit.end:]) - len(text[hit.end:].lstrip(" \t"))
            edits.append((start, end, rule.replacement))
    return tuple(edits)
