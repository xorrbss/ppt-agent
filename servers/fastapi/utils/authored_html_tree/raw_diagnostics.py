"""Raw authored-HTML checks that HTML5 tree construction would erase.

This mirrors only existing limits and balance/duplicate predicates from
``utils.renderer_isolation``. It is representation-independent and is called
before either legacy or tree normalization by the admission gate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser

from utils.renderer_isolation import (
    MAX_ATTRIBUTES_PER_NODE,
    MAX_DOM_DEPTH,
    MAX_DOM_NODES,
    MAX_HTML_BYTES,
    MAX_TEXT_BYTES,
    duplicate_attribute_counts,
)
from utils.renderer_markup_policy import VOID_TAGS


@dataclass(frozen=True, slots=True)
class RawDiagnosticFinding:
    code: str
    detail: str


@dataclass(frozen=True, slots=True)
class RawDiagnostics:
    byte_size: int
    text_bytes_estimate: int
    has_nul: bool
    duplicate_attribute_findings: list[tuple[str, str, int]] = field(default_factory=list)
    violations: list[RawDiagnosticFinding] = field(default_factory=list)


class RawAuthoredHtmlViolation(ValueError):
    """Raised when tree normalization is attempted on raw-input violations."""

    def __init__(self, diagnostics: RawDiagnostics) -> None:
        self.diagnostics = diagnostics
        codes = ",".join(finding.code for finding in diagnostics.violations)
        super().__init__(codes or "authored_html_raw_diagnostics_failed")


class _RawDiagnosticParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.nodes = 0
        self.depth = 0
        self.text_bytes = 0
        self.stack: list[str] = []
        self.duplicates: list[tuple[str, str, int]] = []
        self.violations: list[RawDiagnosticFinding] = []

    def _add(self, code: str, detail: str) -> None:
        finding = RawDiagnosticFinding(code, detail[:120])
        if finding not in self.violations:
            self.violations.append(finding)

    def _start(self, tag: str, attrs: list[tuple[str, str | None]], *, void: bool) -> None:
        tag = tag.lower()
        self.nodes += 1
        if self.nodes > MAX_DOM_NODES:
            self._add("dom_node_limit_exceeded", f"max={MAX_DOM_NODES}")
        if len(attrs) > MAX_ATTRIBUTES_PER_NODE:
            self._add("attribute_per_node_limit_exceeded", f"tag={tag}")
        for name, count in duplicate_attribute_counts(attrs):
            self.duplicates.append((tag, name, count))
            self._add("duplicate_html_attribute", f"tag={tag};attr={name};count={count}")
        for name, value in attrs:
            decoded = value or ""
            if len(decoded.encode("utf-8")) > MAX_HTML_BYTES:
                self._add("html_attribute_source_limit_exceeded", f"tag={tag};attr={name.lower()}")
            if "\x00" in decoded:
                self._add("html_attribute_contains_nul", f"tag={tag};attr={name.lower()}")
        if not void:
            self.stack.append(tag)
            self.depth += 1
            if self.depth > MAX_DOM_DEPTH:
                self._add("dom_depth_limit_exceeded", f"max={MAX_DOM_DEPTH}")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(tag, attrs, void=tag.lower() in VOID_TAGS)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(tag, attrs, void=True)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in VOID_TAGS:
            return
        if not self.stack or self.stack[-1] != tag:
            self._add("html_closing_tag_unbalanced", f"tag={tag}")
            return
        self.stack.pop()
        self.depth -= 1

    def handle_data(self, data: str) -> None:
        self.text_bytes += len(data.encode("utf-8"))
        if self.text_bytes > MAX_TEXT_BYTES:
            self._add("html_text_limit_exceeded", f"max={MAX_TEXT_BYTES}")


def diagnose_raw_authored_html(html: str) -> RawDiagnostics:
    """Return existing raw renderer-limit findings without rewriting input."""

    if not isinstance(html, str):
        raise TypeError("authored HTML must be a string")
    byte_size = len(html.encode("utf-8"))
    has_nul = "\x00" in html
    violations: list[RawDiagnosticFinding] = []
    if not byte_size or byte_size > MAX_HTML_BYTES:
        violations.append(
            RawDiagnosticFinding("authored_html_source_size_limit_exceeded", f"bytes={byte_size}")
        )
    if has_nul:
        violations.append(RawDiagnosticFinding("authored_html_contains_nul", "raw-input"))
    if violations:
        return RawDiagnostics(byte_size, 0, has_nul, [], violations)

    parser = _RawDiagnosticParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception as exc:
        parser._add("authored_html_could_not_be_parsed_safely", type(exc).__name__)
    return RawDiagnostics(
        byte_size=byte_size,
        text_bytes_estimate=parser.text_bytes,
        has_nul=has_nul,
        duplicate_attribute_findings=parser.duplicates,
        violations=parser.violations,
    )


__all__ = [
    "RawAuthoredHtmlViolation",
    "RawDiagnosticFinding",
    "RawDiagnostics",
    "diagnose_raw_authored_html",
]
