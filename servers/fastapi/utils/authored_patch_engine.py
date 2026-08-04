"""Safe, atomic local patches for authored slide HTML.

The model-facing contract deliberately has no selector or raw-HTML operation.  Every
action addresses one stable ``data-qid`` and this module validates the contract again
at the trust boundary before touching the document.  A rejected plan returns the
original HTML plus a machine-readable fallback reason so callers can use the existing
full re-author path.
"""

from __future__ import annotations

import html as html_lib
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from utils.authored_dom_quality import ensure_authored_qids, is_valid_authored_qid

STYLE_REFERENCE = "style_reference"
EXACT_RECONSTRUCTION = "exact_reconstruction"
ReferenceMode = Literal["style_reference", "exact_reconstruction"]

_TAG_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9:-]*$")
_VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)
_NON_EDITABLE_TAGS = frozenset(
    {"script", "style", "link", "meta", "base", "iframe", "object", "embed"}
)
_TEXT_TAGS = frozenset(
    {
        "div",
        "figcaption",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "label",
        "li",
        "p",
        "span",
    }
)
_CSS_DANGER = re.compile(
    r"(?:url\s*\(|expression\s*\(|javascript\s*:|@import|behavior\s*:|"
    r"-moz-binding|/\*|\\)",
    re.IGNORECASE,
)
_LENGTH = re.compile(r"^[+-]?(?:\d+(?:\.\d+)?|\.\d+)(?:px|pt|em|rem|%)$", re.IGNORECASE)
_NUMBER = re.compile(r"^[+-]?(?:\d+(?:\.\d+)?|\.\d+)$")
_COLOR_FUNCTION = re.compile(r"^(?:rgb|rgba|hsl|hsla)\([\d\s.,%+-]+\)$", re.IGNORECASE)
_FONT_FAMILY = re.compile(r"^[\w\s,'\".-]+$", re.UNICODE)
_CAMEL_CASE_STYLE_KEY = re.compile(r"(?<=[a-z0-9])([A-Z])")

_STYLE_PROPERTIES = frozenset(
    {
        "align-items",
        "background-color",
        "border-color",
        "border-radius",
        "border-style",
        "border-width",
        "color",
        "column-gap",
        "font-family",
        "font-size",
        "font-style",
        "font-weight",
        "gap",
        "grid-column",
        "grid-row",
        "grid-template-columns",
        "justify-content",
        "letter-spacing",
        "line-height",
        "margin",
        "margin-bottom",
        "margin-left",
        "margin-right",
        "margin-top",
        "opacity",
        "overflow",
        "overflow-wrap",
        "padding",
        "padding-bottom",
        "padding-left",
        "padding-right",
        "padding-top",
        "row-gap",
        "text-align",
        "text-overflow",
        "transform",
        "white-space",
        "word-break",
        "z-index",
    }
)


class _ActionBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    qid: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_qid(self):
        if not is_valid_authored_qid(self.qid):
            raise ValueError("qid must be a stable data-qid token, not a selector")
        return self


class SetTextAction(_ActionBase):
    action: Literal["setText"]
    text: str = Field(max_length=4000)

    @model_validator(mode="after")
    def validate_text(self):
        if any(ord(char) < 32 and char not in "\n\r\t" for char in self.text):
            raise ValueError("text contains unsupported control characters")
        return self


class SetStyleAction(_ActionBase):
    action: Literal["setStyle"]
    styles: dict[str, str] = Field(min_length=1, max_length=16)


class MoveAction(_ActionBase):
    action: Literal["move"]
    left: str | None = Field(default=None, max_length=32)
    top: str | None = Field(default=None, max_length=32)

    @model_validator(mode="after")
    def require_axis(self):
        if self.left is None and self.top is None:
            raise ValueError("move requires left and/or top")
        return self


class ResizeAction(_ActionBase):
    action: Literal["resize"]
    width: str | None = Field(default=None, max_length=32)
    height: str | None = Field(default=None, max_length=32)

    @model_validator(mode="after")
    def require_dimension(self):
        if self.width is None and self.height is None:
            raise ValueError("resize requires width and/or height")
        return self


class CropAction(_ActionBase):
    action: Literal["crop"]
    fit: Literal["cover", "contain", "fill", "none", "scale-down"] = "cover"
    position: str = Field(default="50% 50%", min_length=1, max_length=64)


PatchAction = Annotated[
    SetTextAction | SetStyleAction | MoveAction | ResizeAction | CropAction,
    Field(discriminator="action"),
]


class AuthoredPatchPlan(BaseModel):
    """Structured model output; ``full_reauthor`` never carries patch actions."""

    model_config = ConfigDict(extra="forbid")

    strategy: Literal["patch", "full_reauthor"] = "patch"
    actions: list[PatchAction] = Field(default_factory=list, max_length=16)
    rationale: str = Field(default="", max_length=500)
    fallback_reason: str | None = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def validate_strategy(self):
        if self.strategy == "patch" and not self.actions:
            raise ValueError("patch strategy requires at least one action")
        if self.strategy == "full_reauthor":
            if self.actions:
                raise ValueError("full_reauthor strategy cannot include actions")
            if not self.fallback_reason:
                raise ValueError("full_reauthor strategy requires fallback_reason")
        return self


class PatchChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["setText", "setStyle", "move", "resize", "crop"]
    qid: str
    before: dict[str, Any]
    after: dict[str, Any]


class PatchApplicationResult(BaseModel):
    """Result consumed by QA orchestration and the review-details API/UI."""

    model_config = ConfigDict(extra="forbid")

    ok: bool
    html: str
    changes: list[PatchChange] = Field(default_factory=list)
    failure_reason: str | None = None
    fallback_reason: str | None = None
    path: Literal["local_patch", "full_fallback"]


@dataclass
class _RawNode:
    value: str

    def render(self) -> str:
        return self.value


@dataclass
class _TextNode:
    value: str

    def render(self) -> str:
        return html_lib.escape(self.value, quote=False)


@dataclass
class _Element:
    tag: str
    attrs: list[tuple[str, str | None]]
    children: list[Any] = field(default_factory=list)
    self_closing: bool = False

    def get_attr(self, name: str) -> str | None:
        lowered = name.lower()
        for attr_name, value in self.attrs:
            if attr_name.lower() == lowered:
                return value
        return None

    def set_attr(self, name: str, value: str) -> None:
        lowered = name.lower()
        self.attrs = [
            (attr_name, attr_value)
            for attr_name, attr_value in self.attrs
            if attr_name.lower() != lowered
        ]
        self.attrs.append((name, value))

    def render(self) -> str:
        rendered_attrs = "".join(
            f" {name}"
            if value is None
            else f' {name}="{html_lib.escape(value, quote=True)}"'
            for name, value in self.attrs
        )
        if self.self_closing:
            return f"<{self.tag}{rendered_attrs} />"
        if self.tag in _VOID_TAGS:
            return f"<{self.tag}{rendered_attrs}>"
        body = "".join(child.render() for child in self.children)
        return f"<{self.tag}{rendered_attrs}>{body}</{self.tag}>"


class _DocumentParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.root: list[Any] = []
        self.stack: list[_Element] = []
        self.errors: list[str] = []

    def _append(self, node: Any) -> None:
        (self.stack[-1].children if self.stack else self.root).append(node)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        if not _TAG_PATTERN.fullmatch(lowered):
            self.errors.append(f"invalid tag {tag!r}")
            return
        node = _Element(lowered, list(attrs))
        self._append(node)
        if lowered not in _VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        if not _TAG_PATTERN.fullmatch(lowered):
            self.errors.append(f"invalid tag {tag!r}")
            return
        self._append(_Element(lowered, list(attrs), self_closing=True))

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if not self.stack or self.stack[-1].tag != lowered:
            expected = self.stack[-1].tag if self.stack else "none"
            self.errors.append(
                f"mismatched closing tag {lowered!r}; expected {expected!r}"
            )
            return
        self.stack.pop()

    def handle_data(self, data: str) -> None:
        self._append(_RawNode(data))

    def handle_entityref(self, name: str) -> None:
        self._append(_RawNode(f"&{name};"))

    def handle_charref(self, name: str) -> None:
        self._append(_RawNode(f"&#{name};"))

    def handle_comment(self, data: str) -> None:
        self._append(_RawNode(f"<!--{data}-->"))

    def handle_decl(self, decl: str) -> None:
        self._append(_RawNode(f"<!{decl}>"))

    def handle_pi(self, data: str) -> None:
        self._append(_RawNode(f"<?{data}>"))

    def unknown_decl(self, data: str) -> None:
        self._append(_RawNode(f"<![{data}]>"))

    def finish(self) -> None:
        self.close()
        if self.stack:
            self.errors.append(
                "unclosed tag(s): " + ", ".join(node.tag for node in self.stack)
            )


@dataclass
class _ElementSpan:
    """Character offsets used to mutate only an explicitly addressed element."""

    qid: str
    start: int
    start_end: int
    content_start: int
    content_end: int | None = None


class _DocumentSpanParser(HTMLParser):
    """Locate QID elements without reserializing unrelated authored markup."""

    def __init__(self, source: str) -> None:
        super().__init__(convert_charrefs=False)
        self.source = source
        self.line_starts = [0]
        self.line_starts.extend(match.end() for match in re.finditer(r"\n", source))
        self.spans: dict[str, _ElementSpan] = {}
        self.stack: list[tuple[str, _ElementSpan | None]] = []

    def _offset(self) -> int:
        line, column = self.getpos()
        return self.line_starts[line - 1] + column

    @staticmethod
    def _qid(attrs: list[tuple[str, str | None]]) -> str | None:
        return next(
            (value for name, value in attrs if name.lower() == "data-qid"),
            None,
        )

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        start = self._offset()
        raw = self.get_starttag_text()
        start_end = start + len(raw)
        qid = self._qid(attrs)
        span = (
            _ElementSpan(qid, start, start_end, start_end) if qid is not None else None
        )
        if span is not None:
            self.spans[qid] = span
        if tag.lower() not in _VOID_TAGS:
            self.stack.append((tag.lower(), span))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        start = self._offset()
        raw = self.get_starttag_text()
        qid = self._qid(attrs)
        if qid is not None:
            end = start + len(raw)
            self.spans[qid] = _ElementSpan(qid, start, end, end, end)

    def handle_endtag(self, tag: str) -> None:
        if not self.stack:
            return
        _open_tag, span = self.stack.pop()
        if span is not None:
            span.content_end = self._offset()


_QUOTED_STYLE_ATTR = re.compile(r"(?is)(\sstyle\s*=\s*)([\"'])(.*?)\2")
_UNQUOTED_STYLE_ATTR = re.compile(r"(?is)(\sstyle\s*=\s*)([^\s>]+)")


def _replace_start_tag_style(start_tag: str, style: str) -> str:
    """Replace only the style attribute while preserving every other source byte."""

    escaped = html_lib.escape(style, quote=True)
    quoted = _QUOTED_STYLE_ATTR.search(start_tag)
    if quoted is not None:
        return start_tag[: quoted.start(3)] + escaped + start_tag[quoted.end(3) :]
    unquoted = _UNQUOTED_STYLE_ATTR.search(start_tag)
    if unquoted is not None:
        return (
            start_tag[: unquoted.start(2)]
            + f'"{escaped}"'
            + start_tag[unquoted.end(2) :]
        )
    close_at = start_tag.rfind("/>")
    if close_at < 0:
        close_at = start_tag.rfind(">")
    if close_at < 0:
        raise ValueError("malformed target start tag")
    return start_tag[:close_at] + f' style="{escaped}"' + start_tag[close_at:]


def _apply_surgical_edits(
    source: str,
    *,
    targets: Mapping[str, _Element],
    style_qids: set[str],
    text_qids: set[str],
) -> str:
    locator = _DocumentSpanParser(source)
    locator.feed(source)
    locator.close()
    replacements: list[tuple[int, int, str]] = []
    for qid in style_qids | text_qids:
        span = locator.spans.get(qid)
        target = targets.get(qid)
        if span is None or target is None:
            raise PermissionError(f"data-qid span {qid!r} was not found")
        if qid in style_qids:
            start_tag = source[span.start : span.start_end]
            replacements.append(
                (
                    span.start,
                    span.start_end,
                    _replace_start_tag_style(
                        start_tag,
                        target.get_attr("style") or "",
                    ),
                )
            )
        if qid in text_qids:
            if span.content_end is None:
                raise PermissionError(f"data-qid text span {qid!r} is not closed")
            replacements.append(
                (
                    span.content_start,
                    span.content_end,
                    "".join(child.render() for child in target.children),
                )
            )

    candidate = source
    next_start = len(source)
    for start, end, replacement in sorted(replacements, reverse=True):
        if end > next_start:
            raise PermissionError("patch spans overlap")
        candidate = candidate[:start] + replacement + candidate[end:]
        next_start = start
    return candidate


def _parse_document(html: str) -> _DocumentParser:
    parser = _DocumentParser()
    try:
        parser.feed(html)
        parser.finish()
    except Exception as error:  # HTMLParser can surface malformed declarations.
        raise ValueError(f"HTML parse failed: {error}") from error
    if parser.errors:
        raise ValueError("; ".join(parser.errors))
    return parser


def _walk(nodes: list[Any]):
    for node in nodes:
        if isinstance(node, _Element):
            yield node
            yield from _walk(node.children)


def _qid_index(parser: _DocumentParser) -> tuple[dict[str, _Element], tuple[str, ...]]:
    index: dict[str, _Element] = {}
    ordered: list[str] = []
    for element in _walk(parser.root):
        qid = element.get_attr("data-qid")
        if qid is None:
            continue
        if not is_valid_authored_qid(qid):
            raise ValueError(f"invalid existing data-qid {qid!r}")
        if qid in index:
            raise ValueError(f"duplicate data-qid {qid!r}")
        index[qid] = element
        ordered.append(qid)
    return index, tuple(ordered)


def _text_content(node: _Element) -> str:
    pieces: list[str] = []

    def collect(children: list[Any]) -> None:
        for child in children:
            if isinstance(child, (_RawNode, _TextNode)):
                pieces.append(html_lib.unescape(child.value))
            elif isinstance(child, _Element):
                collect(child.children)

    collect(node.children)
    return "".join(pieces)


def _descendant_elements(node: _Element):
    yield from _walk(node.children)


def _split_declarations(style: str) -> list[tuple[str, str]]:
    declarations: list[str] = []
    current: list[str] = []
    quote: str | None = None
    depth = 0
    for char in style:
        if quote:
            current.append(char)
            if char == quote:
                quote = None
            continue
        if char in {'"', "'"}:
            quote = char
            current.append(char)
        elif char == "(":
            depth += 1
            current.append(char)
        elif char == ")":
            depth -= 1
            if depth < 0:
                raise ValueError("malformed inline style")
            current.append(char)
        elif char == ";" and depth == 0:
            declarations.append("".join(current))
            current = []
        else:
            current.append(char)
    if quote or depth:
        raise ValueError("malformed inline style")
    declarations.append("".join(current))

    parsed: list[tuple[str, str]] = []
    for declaration in declarations:
        declaration = declaration.strip()
        if not declaration:
            continue
        if ":" not in declaration:
            raise ValueError("malformed inline style declaration")
        name, value = declaration.split(":", 1)
        name = name.strip().lower()
        if not re.fullmatch(r"--?[a-zA-Z][a-zA-Z0-9-]*|[a-zA-Z][a-zA-Z0-9-]*", name):
            raise ValueError(f"malformed inline style property {name!r}")
        parsed.append((name, value.strip()))
    return parsed


def _style_map(element: _Element) -> dict[str, str]:
    return dict(_split_declarations(element.get_attr("style") or ""))


def _set_style_values(element: _Element, changes: Mapping[str, str]) -> None:
    current = _split_declarations(element.get_attr("style") or "")
    changed_names = {name.lower() for name in changes}
    kept = [(name, value) for name, value in current if name not in changed_names]
    kept.extend((name.lower(), value) for name, value in changes.items())
    element.set_attr("style", "; ".join(f"{name}: {value}" for name, value in kept))


def _is_css_color(value: str) -> bool:
    return bool(
        re.fullmatch(r"#[0-9a-fA-F]{3,8}", value)
        or _COLOR_FUNCTION.fullmatch(value)
        or re.fullmatch(r"[A-Za-z]+", value)
    )


def _is_length(value: str, *, positive: bool = False, allow_auto: bool = False) -> bool:
    value = value.strip()
    if allow_auto and value == "auto":
        return True
    if value == "0":
        return not positive
    if not _LENGTH.fullmatch(value):
        return False
    try:
        number = float(re.match(r"^[+-]?(?:\d+(?:\.\d+)?|\.\d+)", value).group())
    except (AttributeError, ValueError):
        return False
    return number > 0 if positive else True


def _length_list(value: str, *, positive: bool = False, maximum: int = 4) -> bool:
    parts = value.split()
    return 1 <= len(parts) <= maximum and all(
        _is_length(part, positive=positive) for part in parts
    )


def _validate_css_value(property_name: str, value: str) -> str:
    prop = property_name.strip().lower()
    value = value.strip()
    if prop not in _STYLE_PROPERTIES:
        raise ValueError(f"CSS property {prop!r} is not allowlisted")
    if not value or len(value) > 200 or any(ord(char) < 32 for char in value):
        raise ValueError(f"invalid CSS value for {prop}")
    if any(char in value for char in ";{}<>") or _CSS_DANGER.search(value):
        raise ValueError(f"unsafe CSS value for {prop}")

    valid = False
    if prop in {"color", "background-color", "border-color"}:
        valid = _is_css_color(value)
    elif prop in {
        "font-size",
        "border-width",
        "border-radius",
        "padding",
        "padding-top",
        "padding-right",
        "padding-bottom",
        "padding-left",
        "gap",
        "row-gap",
        "column-gap",
    }:
        valid = _length_list(
            value, positive=False, maximum=4 if prop == "padding" else 1
        )
    elif prop.startswith("margin"):
        parts = value.split()
        valid = 1 <= len(parts) <= (4 if prop == "margin" else 1) and all(
            _is_length(part, allow_auto=True) for part in parts
        )
    elif prop == "font-family":
        valid = bool(_FONT_FAMILY.fullmatch(value))
    elif prop == "font-weight":
        valid = value in {"normal", "bold", "bolder", "lighter"} or (
            value.isdigit() and 100 <= int(value) <= 900 and int(value) % 100 == 0
        )
    elif prop == "font-style":
        valid = value in {"normal", "italic", "oblique"}
    elif prop == "line-height":
        valid = _is_length(value, positive=True) or (
            bool(_NUMBER.fullmatch(value)) and float(value) > 0
        )
    elif prop == "letter-spacing":
        valid = value == "normal" or _is_length(value)
    elif prop == "text-align":
        valid = value in {"left", "right", "center", "justify", "start", "end"}
    elif prop == "white-space":
        valid = value in {
            "normal",
            "nowrap",
            "pre",
            "pre-wrap",
            "pre-line",
            "break-spaces",
        }
    elif prop == "overflow":
        valid = value in {"visible", "hidden", "clip", "scroll", "auto"}
    elif prop == "overflow-wrap":
        valid = value in {"normal", "break-word", "anywhere"}
    elif prop == "word-break":
        valid = value in {"normal", "break-all", "keep-all", "break-word"}
    elif prop == "text-overflow":
        valid = value in {"clip", "ellipsis"}
    elif prop == "transform":
        # Keep this deliberately narrower than general CSS transforms.  The
        # SlideSpec source projector uses only a two-axis paint translation for
        # a measured, transform-free static element; matrices, functions,
        # percentages and chained transforms remain fail closed.
        match = re.fullmatch(
            r"translate\(\s*([^,()]+)\s*,\s*([^,()]+)\s*\)", value,
            re.IGNORECASE,
        )
        valid = bool(
            match
            and _is_length(match.group(1))
            and _is_length(match.group(2))
            and match.group(1).strip().lower().endswith("px")
            and match.group(2).strip().lower().endswith("px")
        )
    elif prop == "opacity":
        valid = bool(_NUMBER.fullmatch(value)) and 0 <= float(value) <= 1
    elif prop == "border-style":
        valid = value in {"none", "solid", "dashed", "dotted", "double"}
    elif prop == "z-index":
        valid = bool(re.fullmatch(r"[+-]?\d+", value)) and -100 <= int(value) <= 1000
    elif prop == "align-items":
        valid = value in {"stretch", "flex-start", "flex-end", "center", "baseline"}
    elif prop == "justify-content":
        valid = value in {
            "flex-start",
            "flex-end",
            "center",
            "space-between",
            "space-around",
            "space-evenly",
        }
    elif prop == "grid-template-columns":
        parts = value.split()
        valid = 1 <= len(parts) <= 16 and all(
            part == "auto"
            or (
                bool(re.fullmatch(r"(?:\d+(?:\.\d+)?|\.\d+)(?:fr|px|%)", part))
                and float(re.match(r"(?:\d+(?:\.\d+)?|\.\d+)", part).group()) > 0
            )
            for part in parts
        )
    elif prop in {"grid-column", "grid-row"}:
        valid = bool(re.fullmatch(r"\d+\s*/\s*span\s+\d+", value))
    if not valid:
        raise ValueError(f"unsupported CSS value {value!r} for {prop}")
    return value


def _canonical_style_property_name(value: str) -> str:
    """Accept browser ``computedStyle`` camelCase keys at the patch boundary.

    Model prompts receive browser-native keys such as ``fontSize`` and
    ``marginTop``.  Store only kebab-case after validation so inline CSS output,
    duplicate detection, and the allowlist continue to use one representation.
    """
    name = value.strip()
    return _CAMEL_CASE_STYLE_KEY.sub(r"-\1", name).lower()


def _validate_position(value: str) -> str:
    value = value.strip().lower()
    if any(char in value for char in ";{}<>") or _CSS_DANGER.search(value):
        raise ValueError("unsafe crop position")
    parts = value.split()
    keywords = {"left", "right", "top", "bottom", "center"}
    if not 1 <= len(parts) <= 2 or not all(
        part in keywords or _is_length(part) for part in parts
    ):
        raise ValueError("crop position must use one or two keywords/lengths")
    return value


def validate_patch_plan(
    plan: AuthoredPatchPlan | Mapping[str, Any],
    *,
    reference_mode: ReferenceMode = STYLE_REFERENCE,
) -> AuthoredPatchPlan:
    """Validate schema and every CSS value without requiring the source document."""
    if reference_mode not in {STYLE_REFERENCE, EXACT_RECONSTRUCTION}:
        raise ValueError(f"unsupported reference mode {reference_mode!r}")
    parsed = (
        plan
        if isinstance(plan, AuthoredPatchPlan)
        else AuthoredPatchPlan.model_validate(plan)
    )
    for action in parsed.actions:
        if reference_mode == EXACT_RECONSTRUCTION and isinstance(action, SetTextAction):
            raise ValueError(
                "setText is unsafe in exact_reconstruction; use full re-author fallback"
            )
        if isinstance(action, SetStyleAction):
            normalized: dict[str, str] = {}
            for name, value in action.styles.items():
                canonical = _canonical_style_property_name(name)
                if canonical in normalized:
                    raise ValueError(f"duplicate CSS property {canonical!r}")
                normalized[canonical] = _validate_css_value(canonical, value)
            action.styles = normalized
        elif isinstance(action, MoveAction):
            for value in (action.left, action.top):
                if value is not None and not _is_length(value):
                    raise ValueError("move coordinates must be simple CSS lengths")
        elif isinstance(action, ResizeAction):
            for value in (action.width, action.height):
                if value is not None and not _is_length(value, positive=True):
                    raise ValueError("resize dimensions must be positive CSS lengths")
        elif isinstance(action, CropAction):
            action.position = _validate_position(action.position)
    return parsed


def _failure(html: str, reason: str, fallback_reason: str) -> PatchApplicationResult:
    return PatchApplicationResult(
        ok=False,
        html=html,
        changes=[],
        failure_reason=reason,
        fallback_reason=fallback_reason,
        path="full_fallback",
    )


def apply_authored_patch(
    html: str,
    plan: AuthoredPatchPlan | Mapping[str, Any],
    *,
    reference_mode: ReferenceMode = STYLE_REFERENCE,
) -> PatchApplicationResult:
    """Apply a fully validated plan atomically, or return the unchanged document.

    No exception from untrusted model output escapes this boundary.  ``failure_reason``
    is detailed for observability; ``fallback_reason`` is the stable routing category.
    """
    if not isinstance(html, str) or not html.strip():
        return _failure(
            html if isinstance(html, str) else "", "empty HTML", "invalid_html"
        )
    try:
        parsed_plan = validate_patch_plan(plan, reference_mode=reference_mode)
    except (ValidationError, TypeError, ValueError) as error:
        return _failure(html, str(error), "invalid_patch_schema")

    if parsed_plan.strategy == "full_reauthor":
        return _failure(
            html,
            parsed_plan.fallback_reason or "model requested full re-author",
            "model_requested_full_reauthor",
        )

    try:
        # Canonicalize the stable-id namespace before parsing so qid migration and
        # patch validation share one deterministic, idempotent contract.  Keep the
        # caller's original HTML for every failure return below (atomicity).
        canonical_html = ensure_authored_qids(html)
        parser = _parse_document(canonical_html)
        targets, original_qids = _qid_index(parser)
    except ValueError as error:
        return _failure(html, str(error), "invalid_html_structure")

    changes: list[PatchChange] = []
    style_qids: set[str] = set()
    text_qids: set[str] = set()
    try:
        for action in parsed_plan.actions:
            target = targets.get(action.qid)
            if target is None:
                raise LookupError(f"data-qid target {action.qid!r} was not found")
            if target.tag in _NON_EDITABLE_TAGS:
                raise PermissionError(f"tag {target.tag!r} cannot be patched")

            if isinstance(action, SetTextAction):
                if target.tag not in _TEXT_TAGS:
                    raise PermissionError(f"setText is not allowed on {target.tag!r}")
                descendants = list(_descendant_elements(target))
                if any(node.get_attr("data-qid") is not None for node in descendants):
                    raise PermissionError(
                        "setText would remove descendant data-qid values"
                    )
                if any(node.tag == "br" for node in descendants):
                    raise PermissionError("setText cannot remove hard line breaks")
                if descendants:
                    raise PermissionError("setText is restricted to leaf text elements")
                before = {"text": _text_content(target)}
                target.children = [_TextNode(action.text)]
                after = {"text": action.text}
                text_qids.add(action.qid)
            elif isinstance(action, SetStyleAction):
                existing = _style_map(target)
                before = {
                    "styles": {name: existing.get(name) for name in action.styles}
                }
                _set_style_values(target, action.styles)
                updated = _style_map(target)
                after = {"styles": {name: updated.get(name) for name in action.styles}}
                style_qids.add(action.qid)
            elif isinstance(action, MoveAction):
                style_changes = {
                    name: value
                    for name, value in {"left": action.left, "top": action.top}.items()
                    if value is not None
                }
                existing = _style_map(target)
                before = {
                    "styles": {name: existing.get(name) for name in style_changes}
                }
                _set_style_values(target, style_changes)
                after = {"styles": style_changes}
                style_qids.add(action.qid)
            elif isinstance(action, ResizeAction):
                style_changes = {
                    name: value
                    for name, value in {
                        "width": action.width,
                        "height": action.height,
                    }.items()
                    if value is not None
                }
                existing = _style_map(target)
                before = {
                    "styles": {name: existing.get(name) for name in style_changes}
                }
                _set_style_values(target, style_changes)
                after = {"styles": style_changes}
                style_qids.add(action.qid)
            else:
                if target.tag != "img":
                    raise PermissionError("crop is allowed only on img elements")
                style_changes = {
                    "object-fit": action.fit,
                    "object-position": action.position,
                }
                existing = _style_map(target)
                before = {
                    "styles": {name: existing.get(name) for name in style_changes}
                }
                _set_style_values(target, style_changes)
                after = {"styles": style_changes}
                style_qids.add(action.qid)

            if before == after:
                raise ValueError(f"{action.action} on {action.qid!r} has no effect")
            changes.append(
                PatchChange(
                    action=action.action,
                    qid=action.qid,
                    before=before,
                    after=after,
                )
            )

        candidate = _apply_surgical_edits(
            canonical_html,
            targets=targets,
            style_qids=style_qids,
            text_qids=text_qids,
        )
        verified = _parse_document(candidate)
        _, candidate_qids = _qid_index(verified)
        if candidate_qids != original_qids:
            raise PermissionError("patch did not preserve the ordered data-qid set")
    except LookupError as error:
        return _failure(html, str(error), "target_not_found")
    except PermissionError as error:
        return _failure(html, str(error), "unsafe_patch")
    except ValueError as error:
        return _failure(html, str(error), "patch_not_applicable")

    return PatchApplicationResult(
        ok=True,
        html=candidate,
        changes=changes,
        path="local_patch",
    )


__all__ = [
    "EXACT_RECONSTRUCTION",
    "STYLE_REFERENCE",
    "AuthoredPatchPlan",
    "CropAction",
    "MoveAction",
    "PatchAction",
    "PatchApplicationResult",
    "PatchChange",
    "ReferenceMode",
    "ResizeAction",
    "SetStyleAction",
    "SetTextAction",
    "apply_authored_patch",
    "validate_patch_plan",
]
