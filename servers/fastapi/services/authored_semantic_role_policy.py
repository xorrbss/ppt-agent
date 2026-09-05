"""Deterministic semantic-role policy for authored slide HTML.

Extracted from :mod:`services.authored_composition_policy` so that the rule
"exactly one ``data-semantic-role="title"`` owner per slide" lives inside the
stamper instead of inside a normaliser only one call site runs.

Before this module existed, ``_is_primary_title_candidate`` answered "is this
element *allowed* to be a title?" and the stamper promoted **every** element
that answered yes -- so a slide authored as one ``h1`` plus six ``h2`` headings
reached the gate with seven title owners.  The pipeline's pre-gate
``canonicalize_authored_roles`` demoted the extras, but every later call of the
bare stamper (bounded-repair evidence binding, VQA candidate canonicalisation,
the terminal-slide remake, checkpoint hydration) promoted them straight back,
so the HTML that was checkpointed, gated, and published disagreed with the
document the demotion had produced.

The policy here answers a different question: *which one* owner is the title.
Selection is deterministic and costs no provider call:

1. a ``data-header-part="title"`` text owner, when the slide declares one;
2. else the text-owning ``h1`` with the largest effective font size;
3. else the largest heading (``h1``-``h3``) by font, then by heading rank;
4. else the first remaining title candidate in document order.

Every other title candidate that owns text is filed ``supporting`` -- but only
when the document does not already declare a different role for it, so the body
promotion in :mod:`utils.authored_semantic_role_repair` is a fixed point of the
stamper rather than something the next stamper call undoes.
"""

from __future__ import annotations

import re
from typing import Container, Mapping, Sequence

#: Tags that never open a scope, mirrored by the composition policy's parser.
VOID_TAGS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
})

_HEADING_RANK = {"h1": 1, "h2": 2, "h3": 3}
_FONT_SIZE_PX = re.compile(
    r"(?i)(?:^|[;{])\s*font-size\s*:\s*([0-9]*\.?[0-9]+)\s*px"
)
_PRIMARY_TITLE_QID_PREFIXES = ("cover.title", "header.title", "slide.title")


def inline_font_px(style: str) -> float | None:
    """The element's own declared ``font-size`` in px, when it declares one."""

    match = _FONT_SIZE_PX.search(style or "")
    return float(match.group(1)) if match else None


def is_primary_title_candidate(tag: str, attrs: Mapping[str, str]) -> bool:
    """Whether this element is *allowed* to own the slide's title role."""

    if tag in {"h1", "h2"}:
        return True
    if attrs.get("data-header-part", "").strip().lower() == "title":
        return True
    qid = attrs.get("data-qid", "").strip().lower()
    return qid in {
        "title", "text.title", *_PRIMARY_TITLE_QID_PREFIXES
    } or qid.endswith(".title.text") or any(
        qid.startswith(f"{prefix}.") for prefix in _PRIMARY_TITLE_QID_PREFIXES
    )


def is_supporting_candidate(
    attrs: Mapping[str, str], *, owned_text: str = "", capacity_only: bool = False
) -> bool:
    """Whether the QID names an annotation rather than title or body copy."""

    qid = attrs.get("data-qid", "").strip().lower()
    hints = " ".join(
        (
            qid,
            attrs.get("id", ""),
            attrs.get("class", ""),
            attrs.get("data-header-part", ""),
        )
    ).lower()
    tokens = tuple(token for token in re.split(r"[^a-z0-9]+", hints) if token)
    exact = {
        "eyebrow",
        "kicker",
        "label",
        "num",
        "number",
        "date",
        "presenter",
        "metric",
        "caption",
        "index",
        "badge",
        "meta",
    }
    chart_annotation = qid.startswith("chart.") and any(
        token in {"axis", "unit", "scale", "source", "zero", "mid", "max", "min"}
        for token in tokens
    )
    normalized_text = re.sub(r"\s+", " ", owned_text).strip()
    qid_suffix = next(
        reversed(tuple(token for token in re.split(r"[._:-]+", qid) if token)),
        "",
    )
    short_number_label = qid_suffix == "no" and bool(
        re.fullmatch(r"0?[0-9]{1,2}[.)]?", normalized_text)
    )
    short_symbol_marker = (
        qid_suffix == "mark"
        and 0 < len(normalized_text) <= 3
        and not any(character.isalnum() for character in normalized_text)
    )
    week_tick_match = (
        re.fullmatch(r".+\.chart\.week(?P<tick>[0-9]{2})", qid)
        if capacity_only
        else None
    )
    numeric_tick = (
        re.fullmatch(r"[0-9]{1,2}", normalized_text)
        if week_tick_match is not None
        else None
    )
    capacity_chart_week_tick = bool(
        week_tick_match is not None
        and numeric_tick is not None
        and int(week_tick_match.group("tick")) == int(normalized_text)
    )
    return (
        chart_annotation
        or short_number_label
        or short_symbol_marker
        or capacity_chart_week_tick
        or any(
            token in exact
            or token.startswith("metric")
            or token.startswith("label")
            or token.startswith("num")
            for token in tokens
        )
    )


def _rank(tag: str, font_px: float | None, index: int) -> tuple[float, int, int]:
    """Largest effective font first, then the strongest heading, then order.

    Deliberately the same ordering ``utils.authored_semantic_role_repair`` uses
    to pick the title it keeps, so the stamper and the repair never disagree
    about which owner the slide's title is.
    """

    return (-(font_px or 0.0), _HEADING_RANK.get(tag, 9), index)


def plan_title_roles(
    *,
    tags: Sequence[tuple[str, object, str]],
    attrs_by_index: Sequence[Mapping[str, str]],
    owns_text: Sequence[bool],
    font_px: Sequence[float | None],
    header_title_text_owners: Container[int],
) -> tuple[int | None, frozenset[int]]:
    """Return the single primary title owner and every title candidate.

    The second element is the set the stamper demotes: a candidate that owns
    text and is not the returned primary becomes ``supporting`` unless the
    document already declares a different role for it.
    """

    candidates: list[int] = []
    for index, (tag, _raw_attrs, _raw_tag) in enumerate(tags):
        attrs = attrs_by_index[index]
        if not owns_text[index] or not attrs.get("data-qid", "").strip():
            continue
        declared = attrs.get("data-semantic-role", "").strip().lower()
        if (
            is_primary_title_candidate(tag, attrs)
            or index in header_title_text_owners
            or declared == "title"
        ):
            candidates.append(index)
    if not candidates:
        return None, frozenset()
    header_owners = [
        index
        for index in candidates
        if attrs_by_index[index].get("data-header-part", "").strip().lower()
        == "title"
        or index in header_title_text_owners
    ]
    headings = [index for index in candidates if tags[index][0] == "h1"]
    pool = header_owners or headings or candidates
    primary = min(pool, key=lambda index: _rank(tags[index][0], font_px[index], index))
    return primary, frozenset(candidates)


def resolve_semantic_role(
    *,
    tag: str,
    attrs: Mapping[str, str],
    declared: str,
    owns_text: bool,
    owned_text: str,
    is_primary_title: bool,
    is_title_candidate: bool,
) -> str:
    """The role this QID owner carries after deterministic classification.

    Image, primary-title, and supporting-QID classifications take precedence
    over model-declared values. Other non-empty declarations remain
    authoritative unless they mark directly owned text as decoration or retain
    the legacy auto-stamped ``body`` role on a target with no directly owned
    text.
    """

    if tag == "img":
        return "image"
    lowered = declared.strip().lower()
    if owns_text and is_primary_title:
        return "title"
    if owns_text and (lowered == "title" or (not lowered and is_title_candidate)):
        # Exactly one owner keeps the title role. Demoting the rest here -- and
        # not only in the pre-gate normaliser -- is what makes the checkpointed,
        # gated, and published documents agree with one another.
        return "supporting"
    if is_supporting_candidate(attrs, owned_text=owned_text):
        return "supporting"
    if lowered == "decoration" and owns_text:
        return "body"
    if lowered == "body" and not owns_text:
        # Early semantic rollout checkpoints stamped every missing role as
        # body, including empty shapes and structural wrappers. The direct-text
        # ownership rule deterministically migrates them.
        return "decoration"
    if lowered == "title" and not owns_text:
        return "decoration"
    return declared or ("body" if owns_text else "decoration")


__all__ = [
    "VOID_TAGS",
    "inline_font_px",
    "is_primary_title_candidate",
    "is_supporting_candidate",
    "plan_title_roles",
    "resolve_semantic_role",
]
