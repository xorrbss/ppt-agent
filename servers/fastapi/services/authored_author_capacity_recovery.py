"""Bounded author-response title recovery and content-free capacity evidence."""

from __future__ import annotations

import html as html_lib
import re
from hashlib import sha256
from typing import Any, Mapping, Sequence
from services.authored_title_recovery_contract import (
    TITLE_CAPACITY_UNRESOLVABLE,
    build_title_recovery_contract,
    protected_title_token_differences,
    resolve_title_capacity_recovery,
    title_budget_prompt,
    title_recovery_budget,
    title_token_diagnostic,
)

from services.authored_composition_policy import (
    SlideCapacity,
    _AuthoredStartTagCollector,
    _CapacityReducerParser,
    _attribute_map,
    _is_body_role,
    _is_supporting_candidate,
    _is_supporting_role,
    _is_title_role,
    canonicalize_authored_capacity_html,
)

DIAGNOSTIC_SCHEMA = "authored-owner-capacity-diagnostic/v1"
DIAGNOSTIC_OWNER_LIMIT = 32
DIAGNOSTIC_ROLES = frozenset({"title", "body", "supporting", "image", "decoration", "other"})
DIAGNOSTIC_TAGS = frozenset({
    "h1", "h2", "h3", "h4", "h5", "h6", "p", "span", "div", "section",
    "li", "td", "th", "img", "text", "other",
})
CAPACITY_KEYS = ("owner_qids", "owner_title", "owner_body", "owner_visible_text", "owner_images")
_FIXED_HEADER_PARTS = frozenset({"eyebrow", "right", "requirements", "subline"})
_FIXED_TEXT_MARKERS = frozenset({"data-source-block-id", "data-text-fixed", "data-title-fixed", "data-content-fixed"})


def owner_capacity_measurements(
    html: str, *, include_diagnostic: bool = False,
) -> tuple[str, dict[str, int], list[dict[str, Any]]]:
    """Use the existing nearest-QID admission rules, including supporting hints."""

    normalized = canonicalize_authored_capacity_html(html)
    parser = _AuthoredStartTagCollector()
    parser.feed(normalized)
    parser.close()
    measured = dict.fromkeys(CAPACITY_KEYS, 0)
    owners: list[dict[str, Any]] = []
    for index, (tag, raw_attrs, _raw_tag) in enumerate(parser.tags):
        attrs = _attribute_map(raw_attrs)
        qid = attrs.get("data-qid", "").strip()
        if not qid:
            continue
        role = attrs.get("data-semantic-role", "").strip().lower()
        text = re.sub(r"\s+", " ", "".join(parser.owned_text_parts[index])).strip()
        supporting = _is_supporting_role(role) or _is_supporting_candidate(
            attrs, owned_text=text, capacity_only=True,
        )
        if include_diagnostic and (text or tag == "img"):
            role_class = (
                "image" if tag == "img" else "title" if _is_title_role(role)
                else "body" if _is_body_role(role) else "supporting"
                if _is_supporting_role(role) else "decoration" if role == "decoration"
                else "other"
            )
            owners.append({
                "ordinal": index,
                "qidDigest": sha256(qid.encode("utf-8")).hexdigest(),
                "role": role_class,
                "tag": tag if tag in DIAGNOSTIC_TAGS else "other",
                "charCount": len(text),
                "textDigest": sha256(text.encode("utf-8")).hexdigest(),
            })
        if tag == "img":
            measured["owner_images"] += 1
            measured["owner_qids"] += 1
        elif text and role != "decoration":
            measured["owner_visible_text"] += len(text)
            if _is_title_role(role):
                measured["owner_title"] += len(text)
                measured["owner_qids"] += int(not supporting)
            elif _is_body_role(role):
                measured["owner_body"] += len(text)
                measured["owner_qids"] += int(not supporting)
    return normalized, measured, owners


def authored_owner_capacity_diagnostic(html: str, capacity: SlideCapacity) -> dict[str, Any]:
    normalized, measured, owners = owner_capacity_measurements(html, include_diagnostic=True)
    # Prioritize title composition, retaining original ordinals for reconstruction.
    ordered = sorted(owners, key=lambda owner: (owner["role"] != "title", owner["ordinal"]))
    return {
        "schemaVersion": DIAGNOSTIC_SCHEMA,
        "htmlDigest": sha256(normalized.encode("utf-8")).hexdigest(),
        "ownerCount": len(owners),
        "truncated": len(owners) > DIAGNOSTIC_OWNER_LIMIT,
        "measured": measured,
        "limits": dict(zip(CAPACITY_KEYS, (
            capacity.max_qids, capacity.title_max_chars, capacity.body_max_chars,
            capacity.max_visible_text_chars, capacity.max_images,
        ), strict=True)),
        "owners": ordered[:DIAGNOSTIC_OWNER_LIMIT],
    }


def safe_owner_capacity_diagnostic(value: object) -> dict[str, Any] | None:
    """Accept only the closed content-free schema at the persistence boundary."""

    def count(item: object) -> bool:
        return type(item) is int and 0 <= item <= 2**31 - 1

    def digest(item: object) -> bool:
        return isinstance(item, str) and re.fullmatch(r"[0-9a-f]{64}", item) is not None

    if not isinstance(value, dict) or set(value) != {
        "schemaVersion", "htmlDigest", "ownerCount", "truncated", "measured", "limits", "owners",
    }:
        return None
    if value["schemaVersion"] != DIAGNOSTIC_SCHEMA or not digest(value["htmlDigest"]):
        return None
    if not count(value["ownerCount"]) or type(value["truncated"]) is not bool:
        return None
    if value["truncated"] != (value["ownerCount"] > DIAGNOSTIC_OWNER_LIMIT):
        return None
    for key in ("measured", "limits"):
        metrics = value[key]
        if not isinstance(metrics, dict) or set(metrics) != set(CAPACITY_KEYS) or not all(count(item) for item in metrics.values()):
            return None
    owners = value["owners"]
    if not isinstance(owners, list) or len(owners) != min(value["ownerCount"], DIAGNOSTIC_OWNER_LIMIT):
        return None
    for owner in owners:
        if not isinstance(owner, dict) or set(owner) != {
            "ordinal", "qidDigest", "role", "tag", "charCount", "textDigest",
        }:
            return None
        if (
            not count(owner["ordinal"]) or not count(owner["charCount"])
            or not isinstance(owner["role"], str) or owner["role"] not in DIAGNOSTIC_ROLES
            or not isinstance(owner["tag"], str) or owner["tag"] not in DIAGNOSTIC_TAGS
            or not digest(owner["qidDigest"]) or not digest(owner["textDigest"])
        ):
            return None
    return {**value, "measured": dict(value["measured"]), "limits": dict(value["limits"]), "owners": [dict(owner) for owner in owners]}


def title_capacity_recovery_qids(html: str, *, instructions: str | None = None) -> tuple[str, ...]:
    """Select authored titles only; the caller must establish ordinary mode."""

    if "data-source-block-id" in html.lower():
        return ()
    # Free-text exact-copy requests have no structured literal authority here.
    # Conservatively retain them instead of guessing which phrase is protected.
    if instructions and re.search(
        r"verbatim|unchanged|(?:exact|fixed)\s+(?:text|wording|title)|do not (?:change|alter|rewrite)|고정|원문|그대로|변경\s*금지|수정하지|바꾸지",
        instructions, re.IGNORECASE,
    ):
        return ()
    parser = _CapacityReducerParser(html)
    parser.feed(html)
    parser.close()
    by_qid = {node.qid: node for node in parser.nodes}
    if len(by_qid) != len(parser.nodes):
        return ()
    eligible: list[str] = []
    for node in parser.nodes:
        if not _is_title_role(node.attrs.get("data-semantic-role", "")):
            continue
        ancestor = node
        protected = False
        while ancestor is not None:
            attrs = ancestor.attrs
            if (
                attrs.get("data-header-part", "").lower() in _FIXED_HEADER_PARTS
                or any(key in attrs for key in _FIXED_TEXT_MARKERS)
                or attrs.get("contenteditable", "").lower() == "false"
            ):
                protected = True
                break
            ancestor = by_qid.get(ancestor.parent_qid)
        if not protected:
            eligible.append(node.qid)
    return tuple(eligible) if len(eligible) <= DIAGNOSTIC_OWNER_LIMIT else ()


class _TitleTextBoundaryParser(_AuthoredStartTagCollector):
    """Compare markup/CSS/non-target text while masking only selected owner text."""

    def __init__(self, qids: Sequence[str]) -> None:
        super().__init__()
        self.convert_charrefs = True
        self.qids = frozenset(qids)
        self.structure: list[tuple[Any, ...]] = []

    def handle_starttag(self, tag, attrs) -> None:
        self.structure.append(("start", tag, tuple(sorted(attrs, key=lambda attr: attr[0]))))
        super().handle_starttag(tag, attrs)

    def handle_startendtag(self, tag, attrs) -> None:
        self.structure.append(("empty", tag, tuple(sorted(attrs, key=lambda attr: attr[0]))))
        super().handle_startendtag(tag, attrs)

    def handle_endtag(self, tag) -> None:
        self.structure.append(("end", tag))
        super().handle_endtag(tag)

    def handle_data(self, data) -> None:
        owner = next((
            _attribute_map(self.tags[index][1]).get("data-qid", "")
            for _tag, index in reversed(self._stack)
            if _attribute_map(self.tags[index][1]).get("data-qid", "")
        ), "")
        if owner in self.qids and not any(tag in {"style", "script", "template"} for tag, _ in self._stack):
            self.structure.append(("title-text", owner))
        else:
            self.structure.append(("data", data))
        super().handle_data(data)

    def handle_comment(self, data) -> None:
        self.structure.append(("comment", data))

    def handle_decl(self, decl) -> None:
        self.structure.append(("decl", decl.lower()))

    def handle_pi(self, data) -> None:
        self.structure.append(("pi", data))

    def title_text(self) -> dict[str, str]:
        return {
            _attribute_map(attrs).get("data-qid", ""): re.sub(r"\s+", " ", "".join(self.owned_text_parts[index])).strip()
            for index, (_tag, attrs, _raw) in enumerate(self.tags)
            if _attribute_map(attrs).get("data-qid", "") in self.qids
        }


def preauthor_title_recovery_contract_prompt(content: str, capacity: SlideCapacity) -> str:
    """Freeze a conservative planned-title contract before provider ordinal one."""

    lines = [
        re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", line)).strip()
        for line in str(content or "").splitlines()
    ]
    lines = [re.sub(r"^(?:#{1,6}|[-*+] |\d+[.)]\s+)", "", line).strip() for line in lines if line]
    title = lines[0] if len(lines) >= 2 else ""
    non_title = len(" ".join(lines[1:])) if title else len(" ".join(lines))
    eligible = (("planned.title", title),) if title else ()
    measured = {
        "owner_title": len(title),
        "owner_visible_text": len(title) + non_title,
    }
    contract = build_title_recovery_contract(
        measured,
        {
            "owner_title": capacity.title_max_chars,
            "owner_visible_text": capacity.max_visible_text_chars,
        },
        eligible,
        layout_id=f"role:{capacity.role.lower()}",
    )
    prompt = title_budget_prompt(contract, eligible)
    return (
        "PRE-PROVIDER TITLE CONTRACT (server-owned; applies to ordinal one): "
        + prompt
        + " Treat planned.title as the single primary title owner identity; if emitted, use that exact QID. "
    )


def title_capacity_recovery_titles(html: str, qids: Sequence[str]) -> dict[str, str]:
    parser = _TitleTextBoundaryParser(qids)
    parser.feed(html)
    parser.close()
    return parser.title_text()


def apply_title_capacity_recovery_titles(
    html: str, before: Mapping[str, str], replacements: Mapping[str, str],
) -> str | None:
    """Replace direct title-owner text without reserializing the document."""

    if before.keys() != replacements.keys():
        return None
    parser = _CapacityReducerParser(html)
    parser.feed(html)
    parser.close()
    by_qid = {node.qid: node for node in parser.nodes}
    edits: list[tuple[int, int, str]] = []
    for qid, replacement in replacements.items():
        node = by_qid.get(qid)
        if node is None or node.end is None or node.descendant_qids:
            return None
        start = html.find(">", node.start, node.end)
        end = html.rfind("<", start + 1, node.end)
        if start < 0 or end < 0:
            return None
        inner = html[start + 1:end]
        if "<" in inner or re.sub(r"\s+", " ", html_lib.unescape(inner)).strip() != before[qid]:
            return None
        edits.append((start + 1, end, html_lib.escape(replacement, quote=False)))
    recovered = html
    for start, end, replacement in sorted(edits, reverse=True):
        recovered = recovered[:start] + replacement + recovered[end:]
    return recovered


def resolve_authored_title_capacity_html(
    html: str, capacity: SlideCapacity, qids: Sequence[str],
) -> dict[str, Any]:
    """Run the closed title resolver and apply its deterministic text result."""

    normalized, measured, _owners = owner_capacity_measurements(html)
    titles = title_capacity_recovery_titles(normalized, qids)
    if tuple(titles) != tuple(qids):
        return {"status": "terminal", "code": TITLE_CAPACITY_UNRESOLVABLE, "html": normalized, "receipt": None}
    contract = build_title_recovery_contract(
        measured,
        {
            "owner_title": capacity.title_max_chars,
            "owner_visible_text": capacity.max_visible_text_chars,
        },
        tuple(titles.items()),
        layout_id=f"role:{capacity.role.lower()}",
    )
    result = resolve_title_capacity_recovery(contract, titles)
    if result["status"] == "terminal":
        return {**result, "html": normalized}
    recovered = apply_title_capacity_recovery_titles(normalized, titles, result["titles"])
    if recovered is None or title_capacity_recovery_boundary(normalized, recovered, qids):
        return {
            "status": "terminal", "code": TITLE_CAPACITY_UNRESOLVABLE,
            "html": normalized, "receipt": result["receipt"],
        }
    return {**result, "html": recovered}


class _TitleTextBoundaryParser(_AuthoredStartTagCollector):
    """Compare markup/CSS/non-target text while masking only selected owner text."""

    def __init__(self, qids: Sequence[str]) -> None:
        super().__init__()
        self.convert_charrefs = True
        self.qids = frozenset(qids)
        self.structure: list[tuple[Any, ...]] = []

    def handle_starttag(self, tag, attrs) -> None:
        self.structure.append(("start", tag, tuple(sorted(attrs, key=lambda attr: attr[0]))))
        super().handle_starttag(tag, attrs)

    def handle_startendtag(self, tag, attrs) -> None:
        self.structure.append(("empty", tag, tuple(sorted(attrs, key=lambda attr: attr[0]))))
        super().handle_startendtag(tag, attrs)

    def handle_endtag(self, tag) -> None:
        self.structure.append(("end", tag))
        super().handle_endtag(tag)

    def handle_data(self, data) -> None:
        owner = next((
            _attribute_map(self.tags[index][1]).get("data-qid", "")
            for _tag, index in reversed(self._stack)
            if _attribute_map(self.tags[index][1]).get("data-qid", "")
        ), "")
        if owner in self.qids and not any(tag in {"style", "script", "template"} for tag, _ in self._stack):
            self.structure.append(("title-text", owner))
        else:
            self.structure.append(("data", data))
        super().handle_data(data)

    def handle_comment(self, data) -> None:
        self.structure.append(("comment", data))

    def handle_decl(self, decl) -> None:
        self.structure.append(("decl", decl.lower()))

    def handle_pi(self, data) -> None:
        self.structure.append(("pi", data))

    def title_text(self) -> dict[str, str]:
        return {
            _attribute_map(attrs).get("data-qid", ""): re.sub(r"\s+", " ", "".join(self.owned_text_parts[index])).strip()
            for index, (_tag, attrs, _raw) in enumerate(self.tags)
            if _attribute_map(attrs).get("data-qid", "") in self.qids
        }


def title_capacity_recovery_boundary(original: str, candidate: str, qids: Sequence[str]) -> tuple[str, ...]:
    before, after = _TitleTextBoundaryParser(qids), _TitleTextBoundaryParser(qids)
    for parser, html in ((before, original), (after, candidate)):
        parser.feed(html)
        parser.close()
    if before.structure != after.structure:
        return ("non_title_document_change",)
    old_text, new_text = before.title_text(), after.title_text()
    if old_text.keys() != new_text.keys():
        return ("title_identity_change",)
    for qid, old in old_text.items():
        new = new_text[qid]
        if old != new and (not new or len(new) >= len(old)):
            return ("title_not_shorter",)
        if protected_title_token_differences(old, new):
            return ("title_protected_token_change",)
    return ()


def title_capacity_recovery_diagnostic(original: str, candidate: str, qids: Sequence[str]) -> dict[str, Any]:
    before, after = _TitleTextBoundaryParser(qids), _TitleTextBoundaryParser(qids)
    for parser, html in ((before, original), (after, candidate)):
        parser.feed(html)
        parser.close()
    return title_token_diagnostic(original, candidate, before.title_text(), after.title_text())


def title_capacity_recovery_hint(
    violations: Sequence[str], qids: Sequence[str], *,
    rejected_html: str | None = None, capacity: SlideCapacity | None = None,
) -> str:
    budget_hint = ""
    if rejected_html is not None and capacity is not None:
        normalized, measured, _owners = owner_capacity_measurements(rejected_html)
        parser = _TitleTextBoundaryParser(qids)
        parser.feed(normalized)
        parser.close()
        eligible = tuple(parser.title_text().items())
        plan = title_recovery_budget(measured, {
            "owner_title": capacity.title_max_chars,
            "owner_visible_text": capacity.max_visible_text_chars,
        }, eligible)
        budget_hint = title_budget_prompt(plan, eligible)
    return (
        f"The frozen aggregate owner envelope failed ({','.join(violations)}). "
        + budget_hint
        + f"This is the final author call. Only shorten authored title text owned by QIDs [{','.join(qids)}] "
        "to fit the combined title limit. Preserve the same subject, claim, numbers, units, named entities, "
        "negation and qualifiers; remove only redundant phrasing, never a fact or distinction. "
        "Do not truncate text mechanically. Keep every tag, attribute, QID, semantic role, CSS, geometry, "
        "font, non-target text and fixed/source/header wording unchanged. The dynamic title is not fixed "
        "wording; eyebrow/right/requirements/subline are fixed. Do not increase limits or relabel roles. "
        "If equivalent shorter wording is impossible, retain the rejected text and fail closed. "
        "Return the supplied full document with only those title text substitutions."
    )
