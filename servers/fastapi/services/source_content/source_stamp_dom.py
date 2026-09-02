"""Token-preserving DOM parsing shared by source stamping and readback.

The parser keeps every original token so a stamp only inserts attributes on
one start tag (or repairs one text token) and never re-serializes authored
markup.  Text ownership is tracked per node so a match is made against the
element's complete rendered text, with hidden script/style text ignored.
"""

from __future__ import annotations

import hashlib
import html
from html.parser import HTMLParser
import re


class BrowserSourceReadbackError(ValueError):
    """Publication-blocking failure in browser-observed source readback."""

    def __init__(
        self,
        code: str,
        *,
        detail: str,
        block_ids: tuple[str, ...] = (),
        slide_indices: tuple[int, ...] = (),
        order_violations: int = 0,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail
        self.block_ids = block_ids
        self.slide_indices = slide_indices
        self.order_violations = order_violations


class _StampNode:
    __slots__ = (
        "start", "end", "text_indices", "hidden", "stamped", "source_id",
        "has_qid", "qid_count", "has_semantic_role",
    )

    def __init__(self, start: int, hidden: bool) -> None:
        self.start = start
        self.end: int | None = None
        self.text_indices: list[int] = []
        self.hidden = hidden
        self.stamped = False
        self.source_id: str | None = None
        self.has_qid = False
        self.qid_count = 0
        self.has_semantic_role = False


class _StampParser(HTMLParser):
    """Token-preserving parser used only to add source IDs before capture."""

    _HIDDEN = frozenset({"script", "style", "noscript", "template"})
    _VOID = frozenset(
        {
            "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
            "meta", "param", "source", "track", "wbr",
        }
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.tokens: list[str] = []
        self.stack: list[_StampNode] = []
        self.nodes: list[_StampNode] = []
        # Decoded rendered text per token index, plus which of those tokens
        # are plain character data that a bounded repair may rewrite.
        self.text_values: dict[int, str] = {}
        self.data_tokens: set[int] = set()

    def node_text(self, node: _StampNode) -> str:
        return "".join(self.text_values[index] for index in node.text_indices)

    def _append_text(self, index: int, value: str) -> None:
        self.text_values[index] = value
        for node in self.stack:
            if node.hidden:
                break
            node.text_indices.append(index)

    def _record_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
        *,
        closed: bool,
    ) -> None:
        raw = self.get_starttag_text() or f"<{tag}>"
        index = len(self.tokens)
        self.tokens.append(raw)
        if tag.lower() == "br":
            # HTMLParser reports ``br`` as an empty start tag, but its rendered
            # text contribution is a line break. Keep that separator in every
            # open ancestor so a source run split with ``<br>`` still matches
            # the browser's visible text.
            self._append_text(index, "\n")
        inherited_hidden = bool(self.stack and self.stack[-1].hidden)
        node = _StampNode(index, inherited_hidden or tag.lower() in self._HIDDEN)
        for name, value in attrs:
            lowered = name.lower()
            if lowered == "data-source-block-id":
                node.stamped = True
                node.source_id = value or ""
            elif lowered == "data-qid":
                node.has_qid = True
                node.qid_count += 1
            elif lowered in {"data-semantic-role", "data-role"} or lowered == "role":
                node.has_semantic_role = bool(value)
        self.nodes.append(node)
        if closed:
            node.end = index
        else:
            self.stack.append(node)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._record_starttag(tag, attrs, closed=tag.lower() in self._VOID)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._record_starttag(tag, attrs, closed=True)

    def handle_endtag(self, tag: str) -> None:
        self.tokens.append(f"</{tag}>")
        if self.stack:
            self.stack[-1].end = len(self.tokens) - 1
            self.stack.pop()

    def handle_data(self, data: str) -> None:
        index = len(self.tokens)
        self.tokens.append(data)
        self.data_tokens.add(index)
        self._append_text(index, data)

    def handle_entityref(self, name: str) -> None:
        index = len(self.tokens)
        self.tokens.append(f"&{name};")
        self._append_text(index, html.unescape(f"&{name};"))

    def handle_charref(self, name: str) -> None:
        index = len(self.tokens)
        self.tokens.append(f"&#{name};")
        self._append_text(index, html.unescape(f"&#{name};"))

    def handle_comment(self, data: str) -> None:
        self.tokens.append(f"<!--{data}-->")

    def handle_decl(self, decl: str) -> None:
        self.tokens.append(f"<!{decl}>")

    def handle_pi(self, data: str) -> None:
        self.tokens.append(f"<?{data}>")


def parse_stamp_dom(html_source: str) -> _StampParser:
    parser = _StampParser()
    try:
        parser.feed(str(html_source))
        parser.close()
    except Exception as error:  # pragma: no cover - HTMLParser is forgiving
        raise BrowserSourceReadbackError(
            "source_content_browser_readback_invalid",
            detail="authored slide HTML could not be parsed",
        ) from error
    return parser


def replace_source_qid(raw_tag: str, source_qid: str) -> str:
    """Replace the one authored QID on a source-owned start tag in place."""

    pattern = re.compile(
        r"(\sdata-qid)(?:\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s>]+))?(?=\s|/?>)",
        re.IGNORECASE,
    )
    replaced, count = pattern.subn(
        lambda match: f'{match.group(1)}="{source_qid}"', raw_tag, count=1
    )
    if count != 1:
        raise BrowserSourceReadbackError(
            "source_content_browser_readback_invalid",
            detail="source QID attribute could not be replaced safely",
        )
    return replaced


def insert_start_tag_attributes(
    raw_tag: str, attributes: str, *, block_id: str, label: str
) -> str:
    """Insert ``attributes`` before the closing bracket of one start tag."""

    close = raw_tag.rfind("/") if raw_tag.rstrip().endswith("/>") else raw_tag.rfind(">")
    if close < 0:
        raise BrowserSourceReadbackError(
            "source_content_browser_readback_invalid",
            detail=f"invalid {label} start tag",
            block_ids=(block_id,),
        )
    if not attributes:
        return raw_tag
    return raw_tag[:close].rstrip() + " " + attributes + raw_tag[close:]


def semantic_role_for_start_tag(raw_tag: str) -> str:
    """Heading tags are title targets; other visible source text is body."""

    tag_match = re.match(r"\s*<\s*([a-z0-9]+)", raw_tag, flags=re.IGNORECASE)
    return "title" if tag_match and tag_match.group(1).lower() in {"h1", "h2"} else "body"


def source_block_data_attribute(source_block_id: str) -> str:
    """Render the escaped attribute used by authored HTML instrumentation."""

    value = str(source_block_id).strip()
    if not value or any(char in value for char in "\r\n\t"):
        raise ValueError("source block id must be a non-empty single-line value")
    return f'data-source-block-id="{html.escape(value, quote=True)}"'


def deterministic_source_qid(source_block_id: str) -> str:
    """Stable qid used when source HTML has no editable identity."""

    return "source-block-" + hashlib.sha256(str(source_block_id).encode("utf-8")).hexdigest()[:16]


__all__ = [
    "BrowserSourceReadbackError",
    "deterministic_source_qid",
    "insert_start_tag_attributes",
    "parse_stamp_dom",
    "replace_source_qid",
    "semantic_role_for_start_tag",
    "source_block_data_attribute",
]
