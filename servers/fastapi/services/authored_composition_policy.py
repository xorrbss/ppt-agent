"""Deterministic composition envelopes for the Authored slide composer.

The model still owns the visual expression, but it no longer decides how much
content a 1280x720 slide can safely carry.  A profile is resolved once per
request and a small, immutable capacity envelope is handed to every author and
repair call.  This is intentionally a policy module, not a second renderer.
"""

from __future__ import annotations

import re
import html as html_lib
import math
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from html.parser import HTMLParser
from typing import Any, Literal, Sequence

from utils.authored_dom_quality import (
    build_compact_dom_map,
    ensure_authored_qids,
    normalize_browser_metrics,
)


CompositionProfileId = Literal["ordinary", "source-verbatim", "source-outline"]
QualityProfileId = Literal["development", "release"]
AUTHORED_CAPACITY_BOUNDED_REPAIR_REVISION = "authored-capacity-bounded-repair/v1"
_CAPACITY_REDUCER_MARKER = "authored-closing-capacity-reducer-v1"
_TITLE_PUNCTUATION_REDUCER_MARKER = "authored-closing-title-punctuation-reducer-v1"
_LAYOUT_ALIGNMENT_MARKER = "authored-layout-alignment-repair-v1"
_CONNECTOR_CLEARANCE_MARKER = "authored-connector-clearance-repair-v1"
_LOW_PRIORITY_OWNER_QID = re.compile(
    r"(?:^|[._-])(?:cue|footer|note|aside|detail|tagline|item\d+)$",
    flags=re.IGNORECASE,
)
_OWNER_TEXT_REDUCER_MARKER = "authored-owner-text-capacity-reducer-v1"
_OWNED_TEXT_PAINT_ORDER_MARKER = "authored-owned-text-paint-order-v1"


@dataclass(frozen=True, slots=True)
class CompositionProfile:
    """Request-level quality policy shared by first authoring and repair."""

    profile_id: CompositionProfileId
    text_mode: Literal["authored", "verbatim", "outline"]
    allow_summarization: bool
    strict_dom_gate: bool
    max_repair_cycles: int
    require_atomic_publication: bool = True
    quality_profile_id: QualityProfileId = "development"
    require_source_readback: bool = False
    require_visual_review: bool = False
    require_performance: bool = False
    require_native_coverage: bool = False
    minimum_native_coverage_ratio: float = 0.0


def _resolve_quality_profile_id(request: Any) -> QualityProfileId:
    raw = getattr(request, "_authored_quality_profile", None)
    if raw is None:
        raw = getattr(request, "quality_profile", None)
    if raw is None:
        raw = getattr(request, "qualityProfile", None)
    if isinstance(raw, Mapping):
        raw = raw.get("id", raw.get("profileId"))
    normalized = str(raw or "development").strip().lower().replace("-", "_")
    if normalized in {"development", "dev", "default"}:
        return "development"
    if normalized in {"release", "strict", "production", "release_strict"}:
        return "release"
    raise ValueError("authored_quality_profile_unknown")


def _with_quality_profile(
    profile: CompositionProfile,
    quality_profile_id: QualityProfileId,
) -> CompositionProfile:
    if quality_profile_id == "development":
        return profile
    return CompositionProfile(
        profile_id=profile.profile_id,
        text_mode=profile.text_mode,
        allow_summarization=profile.allow_summarization,
        strict_dom_gate=profile.strict_dom_gate,
        max_repair_cycles=profile.max_repair_cycles,
        require_atomic_publication=profile.require_atomic_publication,
        quality_profile_id="release",
        require_source_readback=True,
        require_visual_review=True,
        require_performance=True,
        require_native_coverage=True,
        minimum_native_coverage_ratio=0.70,
    )


@dataclass(frozen=True, slots=True)
class SlideCapacity:
    """A deterministic envelope for one slide's visible composition."""

    slide_index: int
    role: str
    title_max_chars: int
    body_max_chars: int
    max_visible_text_chars: int
    max_images: int
    max_qids: int
    # The authored design contract treats 15px as the inclusive 11pt floor.
    # Keeping the capacity gate one pixel higher made a valid bounded repair
    # fail after it fixed the actual clipping defect.
    min_font_px: int = 15
    source_image_count: int = 0

    def with_source_images(self, count: int) -> "SlideCapacity":
        """Return the same plan after binding the already-admitted image offer."""

        count = max(0, int(count))
        # Source pages may legitimately contain several screenshots.  Once the
        # offer is known, reserve a bounded gallery and reduce text budget rather
        # than letting the author discover the collision after rendering.
        if count <= 1:
            body = self.body_max_chars
            visible = self.max_visible_text_chars
        else:
            body = max(150, self.body_max_chars - min(180, (count - 1) * 28))
            visible = max(220, self.max_visible_text_chars - min(220, (count - 1) * 34))
        return SlideCapacity(
            slide_index=self.slide_index,
            role=self.role,
            title_max_chars=self.title_max_chars,
            body_max_chars=body,
            max_visible_text_chars=visible,
            max_images=max(self.max_images, count),
            max_qids=self.max_qids,
            min_font_px=self.min_font_px,
            source_image_count=count,
        )

    def prompt_brief(self, profile: CompositionProfile) -> str:
        source_rule = {
            "verbatim": (
                "Source text is verbatim authority: do not omit, paraphrase, translate, "
                "or invent it. If the envelope cannot contain it, the slide must fail "
                "closed instead of silently changing the source."
            ),
            "outline": (
                "Source text is an explicit outline: preserve every key fact and order, "
                "but concise rephrasing is allowed inside this envelope."
            ),
            "authored": (
                "Prioritize the slide's stated claim and evidence; shorten low-value "
                "wording before reducing type size."
            ),
        }[profile.text_mode]
        image_rule = (
            f"Reserve up to {self.max_images} visible image slot(s)"
            if self.max_images
            else "Use no image slots"
        )
        decoration_rule = (
            " Decorative paint, including rings, glows, and corner outlines, must fit "
            "fully inside the viewport and every clipping ancestor, including rounded corners. "
            "Adjust decoration position or size without changing text or core content; "
            "never rely on overflow clipping to crop decoration."
            if profile.profile_id == "ordinary"
            else ""
        )
        return (
            "SERVER-FROZEN COMPOSITION ENVELOPE (do not negotiate): "
            "viewport 1280x720; keep every painted target inside the viewport; "
            f"combined text across all title owners <= {self.title_max_chars} chars; "
            f"combined text across all body owners <= {self.body_max_chars} chars "
            "(do not repeat the same words in nested owners), and total visible text "
            "across title, body, and supporting owners <= "
            f"{self.max_visible_text_chars} chars; {image_rule}; "
            f"keep semantic targets <= {self.max_qids}; never use text below "
            f"{self.min_font_px}px. {source_rule}{decoration_rule}"
        )


@dataclass(frozen=True, slots=True)
class AuthoredCapacityPlan:
    """Immutable deck-level plan produced before the first provider call."""

    profile: CompositionProfile
    slides: tuple[SlideCapacity, ...]

    def at(self, index: int) -> SlideCapacity:
        return self.slides[index]

    def briefs(self) -> tuple[str, ...]:
        return tuple(item.prompt_brief(self.profile) for item in self.slides)


_ROLE_BUDGETS: dict[str, tuple[int, int, int, int]] = {
    # Cover slides commonly carry presenter/date metadata plus one compact
    # evidence panel. Count browser-owned content targets, not layout wrappers.
    "COVER": (58, 220, 280, 22),
    # Closing slides combine the final claim, release rule, and audit/operations
    # copy. Chromium geometry remains the hard overflow authority.
    "CLOSING": (62, 270, 350, 20),
    "SECTION": (46, 150, 220, 12),
    "AGENDA": (58, 300, 380, 28),
    "PROBLEM": (64, 310, 400, 28),
    "PILLARS": (58, 290, 380, 32),
    "OUTCOMES": (54, 260, 340, 26),
    "ROADMAP": (56, 285, 370, 30),
    "ARCHITECTURE": (54, 280, 370, 34),
}


def _role_budget(role: str) -> tuple[int, int, int, int]:
    normalized = (role or "").strip().upper()
    return _ROLE_BUDGETS.get(normalized, (60, 300, 390, 30))


def build_authored_capacity_plan(
    contents: Sequence[str],
    roles: Sequence[str],
    profile: CompositionProfile,
) -> AuthoredCapacityPlan:
    """Build slide envelopes without consulting a provider or mutable state."""

    if len(contents) != len(roles):
        raise ValueError("authored capacity contents and roles must match")
    slides: list[SlideCapacity] = []
    for index, role in enumerate(roles):
        title, body, visible, qids = _role_budget(role)
        if profile.profile_id == "source-outline":
            body = max(170, int(body * 0.84))
            visible = max(240, int(visible * 0.84))
        elif profile.profile_id == "source-verbatim":
            # Verbatim has no summarization escape hatch.  Its larger envelope is
            # still finite so an impossible source page is rejected before LLM.
            body = int(body * 1.55)
            visible = int(visible * 1.55)
        slides.append(
            SlideCapacity(
                slide_index=index,
                role=role,
                title_max_chars=title,
                body_max_chars=body,
                max_visible_text_chars=visible,
                max_images=8 if profile.profile_id != "ordinary" else 1,
                max_qids=qids,
            )
        )
    return AuthoredCapacityPlan(profile=profile, slides=tuple(slides))


def resolve_composition_profile(request: Any) -> CompositionProfile:
    """Resolve one profile for both first-pass authoring and later repairs."""

    quality_profile_id = _resolve_quality_profile_id(request)
    if bool(getattr(request, "_source_content_authored_reflow", False)):
        policy = getattr(request, "content_policy", None)
        text_mode = str(getattr(policy, "text", "verbatim") or "verbatim").strip().lower()
        if text_mode == "verbatim":
            return _with_quality_profile(
                CompositionProfile("source-verbatim", "verbatim", False, True, 2),
                quality_profile_id,
            )
        return _with_quality_profile(
            CompositionProfile("source-outline", "outline", True, True, 2),
            quality_profile_id,
        )
    return _with_quality_profile(
        CompositionProfile("ordinary", "authored", True, True, 1),
        quality_profile_id,
    )


def capacity_overflow(
    contents: Sequence[str], plan: AuthoredCapacityPlan
) -> tuple[tuple[int, int, int], ...]:
    """Report only source-verbatim impossibilities; outline/ordinary may condense."""

    if plan.profile.profile_id != "source-verbatim":
        return ()
    result: list[tuple[int, int, int]] = []
    for index, content in enumerate(contents):
        actual = len(re.sub(r"\s+", " ", str(content or "")).strip())
        limit = plan.at(index).max_visible_text_chars
        if actual > limit:
            result.append((index, actual, limit))
    return tuple(result)


def capacity_repair_instructions(
    role: str,
    violations: Sequence[str],
    *,
    allow_text_reduction: bool = True,
) -> tuple[str, ...]:
    """Translate measured capacity defects into bounded, role-safe repair work."""

    slide_role = (role or "SLIDE").strip().upper() or "SLIDE"
    instructions: list[str] = []
    for raw_violation in violations:
        violation = str(raw_violation or "").strip()
        measured = re.fullmatch(r"([^:]+):(\d+)>(\d+)", violation)
        if violation.startswith("browser_text_not_visible:"):
            target = violation.partition(":")[2] or "target"
            detail = (
                f"{slide_role} target {target} is not visible. Move it inside the "
                "viewport and ancestor clip bounds; keep it visible and retain its semantic role."
            )
        elif measured and measured.group(1) == "browser_body":
            excess = int(measured.group(2)) - int(measured.group(3))
            if allow_text_reduction:
                detail = (
                    f"{slide_role} body exceeds its limit by {excess} chars. Shorten body-owned "
                    "copy while preserving the key claim, evidence, and semantic role meanings."
                )
            else:
                detail = (
                    f"{slide_role} body exceeds its limit by {excess} chars. Recompose without "
                    "changing source text or semantic roles; leave the slide fail-closed if impossible."
                )
        elif measured and measured.group(1) == "qids":
            excess = int(measured.group(2)) - int(measured.group(3))
            detail = (
                f"{slide_role} has {excess} excess content target(s). Consolidate low-value "
                "body targets; never relabel title/body/image/supporting roles to bypass the limit."
            )
        else:
            detail = (
                f"{slide_role} capacity defect {violation or 'unknown'}. Repair the measured "
                "defect without hiding content or relabeling title/body/image/supporting roles."
            )
        instructions.append(detail[:200])
    return tuple(instructions)


def _finite_positive_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _normalized_rendered_text(item: dict[str, Any]) -> str:
    value = item.get("rendered_text", item.get("renderedText", item.get("text", "")))
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _normalized_capacity_owned_text(item: dict[str, Any]) -> str | None:
    """Count direct owners without changing full-text visual safety evidence."""

    values = [item[key] for key in ("owned_text", "ownedText") if key in item]
    if not values:
        # Older telemetry can overcount nested owners, but never undercounts
        # them. Preserve that conservative behavior without inventing evidence.
        return _normalized_rendered_text(item)
    if any(not isinstance(value, str) for value in values):
        return None
    normalized = [re.sub(r"\s+", " ", value).strip() for value in values]
    if any(value != normalized[0] for value in normalized[1:]):
        return None
    return normalized[0]


class _AuthoredStartTagCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.tags: list[tuple[str, list[tuple[str, str | None]], str]] = []
        self.owns_text: list[bool] = []
        self.owned_text_parts: list[list[str]] = []
        self.header_title_text_owners: set[int] = set()
        self._stack: list[tuple[str, int]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized = tag.lower()
        index = len(self.tags)
        self.tags.append((normalized, attrs, self.get_starttag_text()))
        self.owns_text.append(False)
        self.owned_text_parts.append([])
        if normalized not in {
            "area", "base", "br", "col", "embed", "hr", "img", "input",
            "link", "meta", "param", "source", "track", "wbr",
        }:
            self._stack.append((normalized, index))

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        self.tags.append((tag.lower(), attrs, self.get_starttag_text()))
        self.owns_text.append(False)
        self.owned_text_parts.append([])

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index][0] == normalized:
                self._stack = self._stack[:index]
                return

    def handle_data(self, data: str) -> None:
        if not data.strip() or any(
            tag in {"script", "style", "template"} for tag, _ in self._stack
        ):
            return
        for _tag, index in reversed(self._stack):
            if _attribute_map(self.tags[index][1]).get("data-qid", "").strip():
                self.owns_text[index] = True
                self.owned_text_parts[index].append(data)
                owner_seen = False
                for _ancestor_tag, ancestor_index in reversed(self._stack):
                    owner_seen |= ancestor_index == index
                    part = _attribute_map(self.tags[ancestor_index][1]).get("data-header-part", "").strip().lower()
                    if part:
                        if part == "title" and owner_seen:
                            self.header_title_text_owners.add(index)
                        break
                return

    def handle_entityref(self, name: str) -> None:
        self.handle_data(html_lib.unescape(f"&{name};"))

    def handle_charref(self, name: str) -> None:
        self.handle_data(html_lib.unescape(f"&#{name};"))


@dataclass(slots=True)
class _CapacityReducerNode:
    tag: str
    qid: str
    attrs: dict[str, str]
    start: int
    end: int | None = None
    parent_qid: str | None = None
    descendant_qids: list[str] | None = None


class _CapacityReducerParser(HTMLParser):
    """Locate exact removable spans without normalizing provider HTML."""

    _VOID_TAGS = {
        "area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr",
    }

    def __init__(self, source: str) -> None:
        super().__init__(convert_charrefs=False)
        self.source = source
        self.nodes: list[_CapacityReducerNode] = []
        self._stack: list[tuple[str, int | None]] = []
        self._line_offsets = [0]
        self._line_offsets.extend(
            match.end() for match in re.finditer(r"\n", source)
        )

    def _offset(self) -> int:
        line, column = self.getpos()
        return self._line_offsets[line - 1] + column

    def _nearest_qid(self) -> str | None:
        for _tag, node_index in reversed(self._stack):
            if node_index is not None:
                return self.nodes[node_index].qid
        return None

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        normalized = tag.lower()
        mapped = _attribute_map(attrs)
        qid = mapped.get("data-qid", "").strip()
        node_index = None
        if qid:
            node_index = len(self.nodes)
            self.nodes.append(
                _CapacityReducerNode(
                    tag=normalized,
                    qid=qid,
                    attrs=mapped,
                    start=self._offset(),
                    parent_qid=self._nearest_qid(),
                    descendant_qids=[],
                )
            )
            for _ancestor_tag, ancestor_index in self._stack:
                if ancestor_index is not None:
                    descendants = self.nodes[ancestor_index].descendant_qids
                    if descendants is not None:
                        descendants.append(qid)
        if normalized not in self._VOID_TAGS:
            self._stack.append((normalized, node_index))

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        self.handle_starttag(tag, attrs)
        if self._stack and self._stack[-1][0] == tag.lower():
            _tag, node_index = self._stack.pop()
            if node_index is not None:
                self.nodes[node_index].end = self._tag_end(self._offset())

    def _tag_end(self, start: int) -> int:
        end = self.source.find(">", start)
        return len(self.source) if end < 0 else end + 1

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        for index in range(len(self._stack) - 1, -1, -1):
            stack_tag, node_index = self._stack[index]
            if stack_tag != normalized:
                continue
            if node_index is not None:
                self.nodes[node_index].end = self._tag_end(self._offset())
            self._stack = self._stack[:index]
            return


def _attribute_map(attrs: Sequence[tuple[str, str | None]]) -> dict[str, str]:
    return {
        str(name).lower(): "" if value is None else str(value)
        for name, value in attrs
    }


def _insert_or_replace_attribute(
    raw_tag: str, attrs: dict[str, str], name: str, value: str
) -> str:
    if name in attrs:
        pattern = re.compile(
            rf"(\s{re.escape(name)})(?:\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s>]+))?(?=\s|/?>)",
            re.IGNORECASE,
        )
        return pattern.sub(
            lambda match: f'{match.group(1)}="{value}"', raw_tag, count=1
        )
    match = re.search(r"\s*/?>\s*$", raw_tag)
    if match is None:
        return raw_tag
    return f'{raw_tag[:match.start()]} {name}="{value}"{raw_tag[match.start():]}'


def _is_title_role(value: str) -> bool:
    role = value.strip().lower()
    return role == "title" or role.startswith("title") or role.endswith("title")


def _is_body_role(value: str) -> bool:
    role = value.strip().lower()
    return role == "body" or role.startswith("body") or role.endswith("body")


def _is_supporting_role(value: str) -> bool:
    role = value.strip().lower()
    return role == "supporting" or role.startswith("supporting") or role.endswith(
        "supporting"
    )


def _is_primary_title_candidate(tag: str, attrs: dict[str, str]) -> bool:
    if tag in {"h1", "h2"}:
        return True
    if attrs.get("data-header-part", "").strip().lower() == "title":
        return True
    qid = attrs.get("data-qid", "").strip().lower()
    primary = ("cover.title", "header.title", "slide.title")
    return qid in {"title", "text.title", *primary} or qid.endswith(
        ".title.text"
    ) or any(
        qid.startswith(f"{prefix}.") for prefix in primary
    )


def _is_supporting_candidate(
    attrs: dict[str, str], *, owned_text: str = "", capacity_only: bool = False
) -> bool:
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


def ensure_authored_semantic_roles(html: str) -> str:
    """Stamp deterministic roles onto the exact HTML rendered by Chromium.

    Deterministic image, primary-title, and supporting QID classifications take
    precedence over model-declared values. Other non-empty declarations remain
    authoritative unless they mark directly owned text as decoration or retain
    the legacy auto-stamped ``body`` role on a target with no directly owned
    text. Missing roles become ``body`` only when the QID owns visible text;
    structural and decorative targets become ``decoration``. Attribute-only
    stamping is idempotent and happens before render so HTML, PNG, and browser
    evidence share one digest-bound source.
    """

    if not isinstance(html, str):
        raise TypeError("authored HTML must be a string")
    parser = _AuthoredStartTagCollector()
    parser.feed(html)
    parser.close()
    cursor = 0
    parts: list[str] = []
    for tag_index, (tag, raw_attrs, raw_tag) in enumerate(parser.tags):
        index = html.find(raw_tag, cursor)
        if index < 0:
            continue
        parts.append(html[cursor:index])
        attrs = _attribute_map(raw_attrs)
        replacement = raw_tag
        if attrs.get("data-qid", "").strip():
            declared = attrs.get("data-semantic-role", "").strip()
            owns_text = parser.owns_text[tag_index]
            owned_text = "".join(parser.owned_text_parts[tag_index])
            if tag == "img":
                role = "image"
            elif owns_text and (_is_primary_title_candidate(tag, attrs) or tag_index in parser.header_title_text_owners):
                role = "title"
            elif _is_supporting_candidate(attrs, owned_text=owned_text):
                role = "supporting"
            elif declared.lower() == "decoration" and owns_text:
                role = "body"
            elif declared.lower() == "body" and not owns_text:
                # Early semantic rollout checkpoints stamped every missing role
                # as body, including empty shapes and structural wrappers. The
                # direct-text ownership rule deterministically migrates them.
                role = "decoration"
            elif declared.lower() == "title" and not owns_text:
                role = "decoration"
            else:
                role = declared or ("body" if owns_text else "decoration")
            if role != declared:
                replacement = _insert_or_replace_attribute(
                    raw_tag, attrs, "data-semantic-role", role
                )
        parts.append(replacement)
        cursor = index + len(raw_tag)
    parts.append(html[cursor:])
    return "".join(parts)


def canonicalize_authored_capacity_html(html: str) -> str:
    """Use the render-bound owner namespace before counting or reducing text."""

    return ensure_authored_semantic_roles(ensure_authored_qids(html))


def aggregate_owner_capacity_violations(
    html: str,
    capacity: SlideCapacity,
) -> tuple[str, ...]:
    """Reject nearest-QID aggregate excess; Chromium still owns visual safety."""

    # Imported at the call boundary: the helper reuses this module's canonical
    # owner parser without creating an import-time dependency cycle.
    from services.authored_author_capacity_recovery import owner_capacity_measurements

    _normalized, measured, _owners = owner_capacity_measurements(html)
    return tuple(
        f"{key}:{measured[key]}>{limit}"
        for key, limit in {
            "owner_qids": capacity.max_qids,
            "owner_title": capacity.title_max_chars,
            "owner_body": capacity.body_max_chars,
            "owner_visible_text": capacity.max_visible_text_chars,
            "owner_images": capacity.max_images,
        }.items()
        if measured[key] > limit
    )


def semantic_repair_boundary_violations(
    original_html: str,
    candidate_html: str,
    *,
    allow_primary_title_shortening: bool = False,
    allow_bounded_body_shortening: bool = False,
) -> tuple[str, ...]:
    """Validate one bounded repair without granting structural authority.

    QIDs and roles are immutable. The existing CLOSING reducer may withdraw at
    most three explicitly marked low-priority body/supporting leaves; otherwise
    every owner remains. A structured patch may shorten at most three such
    owners. Source-owned and title/header text always remains byte-equivalent
    after whitespace normalization.
    """

    def snapshot(source: str):
        normalized = ensure_authored_semantic_roles(source)
        parser = _AuthoredStartTagCollector()
        parser.feed(normalized)
        parser.close()
        owners: dict[str, tuple[str, str, str, str, bool]] = {}
        order: list[str] = []
        duplicates: set[str] = set()
        source_bindings: list[tuple[str, str]] = []
        for index, (tag, raw_attrs, _raw_tag) in enumerate(parser.tags):
            attrs = _attribute_map(raw_attrs)
            qid = attrs.get("data-qid", "").strip()
            source_block_id = attrs.get("data-source-block-id", "").strip()
            if source_block_id:
                source_bindings.append((qid, source_block_id))
            if not qid:
                continue
            if qid in owners:
                duplicates.add(qid)
                continue
            role = attrs.get("data-semantic-role", "").strip().lower()
            text = re.sub(
                r"\s+", " ", "".join(parser.owned_text_parts[index])
            ).strip()
            header_part = attrs.get("data-header-part", "").strip().lower()
            owners[qid] = (
                role,
                text,
                source_block_id,
                header_part,
                _is_primary_title_candidate(tag, attrs),
            )
            order.append(qid)
        return owners, tuple(order), tuple(source_bindings), duplicates

    original, original_order, original_sources, original_duplicates = snapshot(
        original_html
    )
    candidate, candidate_order, candidate_sources, candidate_duplicates = snapshot(
        candidate_html
    )
    violations: list[str] = []
    if original_duplicates or candidate_duplicates:
        violations.append("semantic_qid_duplicate")
    if candidate_sources != original_sources:
        violations.append("semantic_source_owner_changed")

    marker = re.search(
        rf"<!--\s*{re.escape(_CAPACITY_REDUCER_MARKER)}:([^>]*)-->",
        candidate_html,
        flags=re.IGNORECASE,
    )
    declared_removed = {
        value.strip() for value in (marker.group(1).split(",") if marker else ())
        if value.strip()
    }
    removed = set(original) - set(candidate)
    added = set(candidate) - set(original)
    if added:
        violations.append("semantic_qid_added")
    if removed != declared_removed or len(removed) > 3:
        violations.append("semantic_qid_removed_outside_bounded_reducer")

    source_bound_slide = bool(original_sources) or (
        "data-source-block-id" in original_html.lower()
    )
    unscoped_source_owner = (
        "data-source-block-id" in original_html.lower() and not original_sources
    )
    for qid in sorted(removed):
        role, _text, source_block_id, header_part, _primary_title = original[qid]
        if (
            source_bound_slide
            or source_block_id
            or header_part
            or _is_title_role(role)
            or not (_is_body_role(role) or _is_supporting_role(role))
            or _LOW_PRIORITY_OWNER_QID.search(qid) is None
        ):
            violations.append(f"semantic_owner_removal_forbidden:{qid}")

    retained_order = tuple(qid for qid in original_order if qid not in removed)
    if candidate_order != retained_order:
        violations.append("semantic_qid_order_changed")

    changed_text_qids: list[str] = []
    shortened_primary_titles: list[str] = []
    for qid in sorted(set(original) & set(candidate)):
        (
            original_role,
            original_text,
            original_source,
            original_header,
            original_primary_title,
        ) = original[qid]
        (
            candidate_role,
            candidate_text,
            candidate_source,
            candidate_header,
            candidate_primary_title,
        ) = candidate[qid]
        if candidate_role != original_role:
            violations.append(f"semantic_role_changed:{qid}")
        if (candidate_source, candidate_header) != (original_source, original_header):
            violations.append(f"semantic_owner_binding_changed:{qid}")
        if candidate_text == original_text:
            continue
        changed_text_qids.append(qid)
        title_shortening_allowed = bool(
            allow_primary_title_shortening
            and not source_bound_slide
            and original_primary_title
            and candidate_primary_title
            and _is_title_role(original_role)
            and not original_header
            and not original_source
            and candidate_text
            and len(candidate_text) < len(original_text)
        )
        if title_shortening_allowed:
            shortened_primary_titles.append(qid)
            continue
        if (
            unscoped_source_owner
            or original_source
            or original_header
            or _is_title_role(original_role)
            or not (_is_body_role(original_role) or _is_supporting_role(original_role))
            or (
                not _is_supporting_role(original_role)
                and not (
                    allow_bounded_body_shortening
                    and _is_body_role(original_role)
                )
                and _LOW_PRIORITY_OWNER_QID.search(qid) is None
            )
            or not candidate_text
            or len(candidate_text) >= len(original_text)
        ):
            violations.append(f"semantic_text_rewrite_forbidden:{qid}")
    if len(shortened_primary_titles) > 1:
        violations.append("semantic_primary_title_rewrite_too_broad")
    if len(
        [qid for qid in changed_text_qids if qid not in shortened_primary_titles]
    ) > 3:
        violations.append("semantic_text_rewrite_too_broad")
    return tuple(dict.fromkeys(violations))


def _shorten_at_sentence_or_word_boundary(text: str, maximum: int) -> str:
    """Return a non-empty strict prefix ending at a sentence/word boundary."""

    normalized = re.sub(r"\s+", " ", text).strip()
    if maximum < 1 or len(normalized) <= maximum:
        return normalized
    prefix = normalized[: maximum + 1]
    sentence_ends = [
        match.end()
        for match in re.finditer(r"[.!?。！？](?=\s|$)", prefix)
        if match.end() <= maximum
    ]
    if sentence_ends:
        shortened = normalized[: sentence_ends[-1]].strip()
        if shortened:
            return shortened
    word_break = prefix.rfind(" ", 1, maximum + 1)
    if word_break > 0:
        return normalized[:word_break].rstrip(" ,;:，；：")
    first_word_end = normalized.find(" ")
    if first_word_end > 0:
        return normalized[:first_word_end].rstrip(" ,;:，；：")
    return normalized


def _rewrite_owner_segment_text(segment: str, shortened: str) -> str | None:
    """Keep inline markup while retaining the shortened owned-text prefix."""

    inner_start = segment.find(">") + 1
    inner_end = segment.rfind("<")
    if inner_start <= 0 or inner_end < inner_start:
        return None
    inner = segment[inner_start:inner_end]
    if re.search(r"<(?:script|style|template)\b", inner, flags=re.IGNORECASE):
        return None

    remaining_words = shortened.split()
    parts = re.split(r"(<[^>]+>)", inner)
    rewritten: list[str] = []
    for part in parts:
        if not part:
            continue
        if part.startswith("<") and part.endswith(">"):
            rewritten.append(part)
            continue
        words = re.sub(r"\s+", " ", html_lib.unescape(part)).strip().split()
        keep = min(len(words), len(remaining_words))
        if keep:
            if words[:keep] != remaining_words[:keep]:
                return None
            rewritten.append(html_lib.escape(" ".join(words[:keep]), quote=False))
            del remaining_words[:keep]
        else:
            rewritten.append("")
    if remaining_words:
        return None
    return segment[:inner_start] + "".join(rewritten) + segment[inner_end:]


def reduce_authored_owner_text_capacity(
    html: str,
    capacity: SlideCapacity,
    *,
    profile_id: str,
    allow_summarization: bool,
) -> str:
    """Deterministically shorten at most three text owners after call exhaustion."""

    if (
        profile_id != "ordinary"
        or not allow_summarization
        or _OWNER_TEXT_REDUCER_MARKER in html
        or "data-source-block-id" in html.lower()
    ):
        return html
    normalized = canonicalize_authored_capacity_html(html)
    violations = aggregate_owner_capacity_violations(normalized, capacity)
    measured: dict[str, tuple[int, int]] = {}
    for violation in violations:
        match = re.fullmatch(
            r"(owner_body|owner_visible_text):(\d+)>(\d+)", violation
        )
        if match is None:
            return html
        measured[match.group(1)] = (int(match.group(2)), int(match.group(3)))
    body_excess = max(
        0,
        measured.get("owner_body", (0, 0))[0]
        - measured.get("owner_body", (0, 0))[1],
    )
    visible_excess = max(
        0,
        measured.get("owner_visible_text", (0, 0))[0]
        - measured.get("owner_visible_text", (0, 0))[1],
    )
    if body_excess <= 0 and visible_excess <= 0:
        return html

    collector = _AuthoredStartTagCollector()
    collector.feed(normalized)
    collector.close()
    owned_text_by_qid: dict[str, str] = {}
    for index, (_tag, raw_attrs, _raw_tag) in enumerate(collector.tags):
        attrs = _attribute_map(raw_attrs)
        qid = attrs.get("data-qid", "").strip()
        if qid:
            owned_text_by_qid[qid] = re.sub(
                r"\s+", " ", "".join(collector.owned_text_parts[index])
            ).strip()

    parser = _CapacityReducerParser(normalized)
    parser.feed(normalized)
    parser.close()
    candidates: list[tuple[_CapacityReducerNode, str, bool, bool]] = []
    for node in parser.nodes:
        role = node.attrs.get("data-semantic-role", "").strip().lower()
        text = owned_text_by_qid.get(node.qid, "")
        segment = normalized[node.start : node.end] if node.end is not None else ""
        is_body = _is_body_role(role)
        is_supporting = _is_supporting_role(role)
        if (
            node.end is None
            or node.descendant_qids
            or not text
            or not (is_body or is_supporting)
            or _is_title_role(role)
            or node.attrs.get("data-header-part")
            or node.attrs.get("data-source-block-id")
            or re.search(
                r"<(?:script|style|template)\b", segment, flags=re.IGNORECASE
            )
        ):
            continue
        candidates.append((node, text, is_body, is_supporting))

    selected: list[tuple[_CapacityReducerNode, str]] = []
    remaining = list(candidates)
    while (body_excess > 0 or visible_excess > 0) and len(selected) < 3:
        eligible = [item for item in remaining if item[2] or body_excess <= 0]
        if not eligible:
            break
        eligible.sort(
            key=lambda item: (
                0 if (body_excess > 0 and item[2]) else (0 if item[3] else 1),
                -len(item[1]),
                item[0].qid,
            )
        )
        node, text, is_body, _is_supporting = eligible[0]
        remaining.remove(eligible[0])
        required = max(body_excess if is_body else 0, visible_excess)
        shortened = _shorten_at_sentence_or_word_boundary(
            text,
            max(1, len(text) - required),
        )
        if not shortened or len(shortened) >= len(text):
            continue
        reduction = len(text) - len(shortened)
        selected.append((node, shortened))
        if is_body:
            body_excess = max(0, body_excess - reduction)
        visible_excess = max(0, visible_excess - reduction)
    if body_excess > 0 or visible_excess > 0 or not selected:
        return html

    reduced = normalized
    for node, shortened in sorted(
        selected, key=lambda item: item[0].start, reverse=True
    ):
        segment = reduced[node.start : node.end]
        replacement = _rewrite_owner_segment_text(segment, shortened)
        if replacement is None:
            return html
        reduced = reduced[: node.start] + replacement + reduced[node.end :]
    insertion = re.search(r"</head\s*>", reduced, flags=re.IGNORECASE)
    if insertion is None:
        return html
    marker = (
        f"<!-- {_OWNER_TEXT_REDUCER_MARKER}:"
        + ",".join(sorted(node.qid for node, _text in selected))
        + " -->"
    )
    reduced = reduced[: insertion.start()] + marker + reduced[insertion.start() :]
    if aggregate_owner_capacity_violations(reduced, capacity):
        return html
    if semantic_repair_boundary_violations(
        normalized,
        reduced,
        allow_bounded_body_shortening=True,
    ):
        return html
    return reduced


_HEADER_EYEBROW_CLIP_REPAIR_MARKER = "authored-header-eyebrow-clip-repair-v1"


def repair_authored_header_eyebrow_clip(
    html: str,
    browser_metrics: Any,
    header_contract: Any,
    role: str,
    *,
    allow_immutable_checkpoint_geometry: bool = False,
) -> str:
    """Translate one contract eyebrow into its measured safe clip interval.

    This is deliberately narrower than a general geometry repair. It accepts
    only the exact ``header.eyebrow`` contract part, an ancestor-caused top-edge
    glyph clip of at most four pixels, and Chromium's proof that a translation
    is isolated, composable, stacking-stable, and inside the header band. New
    slides must match the current contract. A caller that proved the input was
    hydrated from an immutable checkpoint may instead bind the measured band to
    that HTML's exact ``data-header-band`` declaration.
    """

    if not isinstance(html, str) or _HEADER_EYEBROW_CLIP_REPAIR_MARKER in html:
        return html
    current_contract_eligible = bool(
        header_contract is not None
        and getattr(header_contract, "applies_to_role", lambda _role: False)(role)
        and getattr(getattr(header_contract, "eyebrow", None), "enabled", False)
    )
    if not current_contract_eligible and not allow_immutable_checkpoint_geometry:
        return html

    parser = _AuthoredStartTagCollector()
    parser.feed(html)
    parser.close()
    eyebrow_tags = []
    band_tags = []
    for _tag, raw_attrs, _raw_tag in parser.tags:
        attrs = _attribute_map(raw_attrs)
        if attrs.get("data-qid", "").strip() == "header.eyebrow":
            eyebrow_tags.append(attrs)
        if attrs.get("data-qid", "").strip() == "header.band":
            band_tags.append(attrs)
    if len(eyebrow_tags) != 1 or (
        eyebrow_tags[0].get("data-header-part", "").strip().lower() != "eyebrow"
        or eyebrow_tags[0].get("data-semantic-role", "").strip().lower()
        != "supporting"
    ):
        return html
    if len(band_tags) != 1 or (
        band_tags[0].get("data-header-part", "").strip().lower() != "band"
    ):
        return html

    metrics = normalize_browser_metrics(browser_metrics)
    elements = [item for item in metrics.get("elements", ()) if isinstance(item, dict)]
    target = next((item for item in elements if item.get("qid") == "header.eyebrow"), None)
    band = next((item for item in elements if item.get("qid") == "header.band"), None)
    if target is None or band is None:
        return html
    if not (
        target.get("css_visible") is True
        and target.get("glyph_clip_cause") == "ancestor"
        and target.get("glyph_clipped") is True
        and target.get("line_box_clipped") is True
        and target.get("offset_parent_qid") == "header.band"
        and str(target.get("semantic_role", "")).lower() == "supporting"
    ):
        return html

    computed = target.get("computed_style")
    probe = target.get("static_translation_probe")
    target_box = target.get("bbox")
    band_box = band.get("bbox")
    fragments = target.get("text_fragments")
    if not all(isinstance(value, dict) for value in (computed, probe, target_box, band_box)):
        return html
    if not isinstance(fragments, list) or not fragments:
        return html
    if str(computed.get("transform", "none")).strip().lower() != "none":
        return html
    try:
        font_size = float(target.get("font_size"))
        band_y = float(band_box["y"])
        band_height = float(band_box["height"])
        target_y = float(target_box["y"])
        target_height = float(target_box["height"])
        fragment_top = min(float(item["y"]) for item in fragments if isinstance(item, dict))
    except (KeyError, TypeError, ValueError):
        return html
    if not all(
        math.isfinite(value)
        for value in (font_size, band_y, band_height, target_y, target_height, fragment_top)
    ):
        return html
    strict_contract_geometry = bool(
        current_contract_eligible
        and abs(font_size - float(header_contract.eyebrow.font_size_px)) <= 0.5
        and float(header_contract.band_min_px)
        <= band_height
        <= float(header_contract.band_max_px)
    )
    if not strict_contract_geometry:
        declared_band = band_tags[0].get("data-header-band", "").strip()
        if not (
            allow_immutable_checkpoint_geometry
            and re.fullmatch(r"\d+", declared_band)
            and font_size >= 12
            and band_height == float(int(declared_band))
        ):
            return html

    shift_y = int(math.ceil(band_y - fragment_top))
    safe = probe.get("safeTranslationIntervalPx")
    if not isinstance(safe, dict) or not (1 <= shift_y <= 4):
        return html
    try:
        safe_min_y = float(safe["minY"])
        safe_max_y = float(safe["maxY"])
    except (KeyError, TypeError, ValueError):
        return html
    if not (
        probe.get("layoutIsolation") is True
        and probe.get("stackingStable") is True
        and probe.get("transformComposable") is True
        and safe_min_y <= shift_y <= safe_max_y
        and band_y <= target_y + shift_y
        and target_y + target_height + shift_y <= band_y + band_height
    ):
        return html

    shifted_bottom = target_y + target_height + shift_y
    for sibling in probe.get("siblingBoundsPx", ()):
        if not isinstance(sibling, dict):
            return html
        try:
            sibling_y = float(sibling["y"])
        except (KeyError, TypeError, ValueError):
            return html
        if target_y < sibling_y and shifted_bottom > sibling_y:
            return html

    insertion = re.search(r"</head\s*>", html, flags=re.IGNORECASE)
    if insertion is None:
        return html
    rule = (
        f"<style>/* {_HEADER_EYEBROW_CLIP_REPAIR_MARKER} */"
        '[data-qid="header.eyebrow"][data-header-part="eyebrow"]'
        f'{{transform:translateY({shift_y}px)!important}}</style>'
    )
    return html[: insertion.start()] + rule + html[insertion.start() :]


def _declared_html_qids(html: str) -> tuple[str, ...]:
    parser = _AuthoredStartTagCollector()
    parser.feed(str(html or ""))
    parser.close()
    return tuple(
        qid
        for _tag, raw_attrs, _raw_tag in parser.tags
        if (qid := _attribute_map(raw_attrs).get("data-qid", "").strip())
    )


def capacity_violations(
    html: str,
    browser_metrics: Any,
    capacity: SlideCapacity,
) -> tuple[str, ...]:
    """Return deterministic, publication-blocking envelope violations.

    The prompt envelope is advisory to the model; this function is the hard
    admission check against the exact HTML and browser telemetry that will be
    published.  It deliberately reports only measurable limits (semantic
    targets, visible text, images, and font floor) and leaves geometry defects
    to ``inspect_authored_dom``.
    """

    violations: list[str] = []
    nodes = build_compact_dom_map(html)

    image_count = len(re.findall(r"<img\b", html, flags=re.IGNORECASE))
    if image_count > capacity.max_images:
        violations.append(f"images:{image_count}>{capacity.max_images}")

    # Strip non-visible markup and collapse whitespace.  This is intentionally
    # conservative: text duplicated in nested nodes is counted once here,
    # while the browser payload remains the authority for clipping geometry.
    visible_source = re.sub(
        r"<(?:script|style|template)\b[^>]*>.*?</(?:script|style|template)>",
        " ",
        str(html or ""),
        flags=re.IGNORECASE | re.DOTALL,
    )
    visible_source = html_lib.unescape(re.sub(r"<[^>]+>", " ", visible_source))
    visible_source = re.sub(r"\s+", " ", visible_source).strip()
    headings = re.findall(
        r"<h[1-2]\b[^>]*>(.*?)</h[1-2]>",
        str(html or ""),
        flags=re.IGNORECASE | re.DOTALL,
    )
    heading_text = html_lib.unescape(re.sub(r"<[^>]+>", " ", " ".join(headings)))
    heading_text = re.sub(r"\s+", " ", heading_text).strip()
    body_chars = max(0, len(visible_source) - len(heading_text))

    metrics = normalize_browser_metrics(browser_metrics)
    metric_elements = metrics.get("elements", ())
    if metric_elements:
        # The browser payload is the publication authority.  HTML counts above
        # remain useful diagnostics, but a string containing a title/image can
        # never satisfy this gate without a corresponding measured element.
        qids: list[str] = []
        role_nodes: dict[str, list[dict[str, Any]]] = {"title": [], "body": []}
        browser_images = 0
        browser_content_targets = 0
        browser_body_candidates = 0
        browser_visible_text_parts: list[str] = []
        for item in metric_elements:
            if not isinstance(item, dict):
                violations.append("browser_element_invalid")
                continue
            qid = str(item.get("qid", "") or "").strip()
            if not qid:
                violations.append("browser_qid_missing")
            qids.append(qid)
            role = str(item.get("semantic_role", item.get("semanticRole", item.get("role", ""))) or "").strip().lower()
            tag = str(item.get("tag", "")).lower()
            rendered = _normalized_rendered_text(item)
            owned_value = item.get("has_owned_text", item.get("hasOwnedText"))
            owned_capacity_text = _normalized_capacity_owned_text(item)
            if owned_capacity_text is None or (
                ("owned_text" in item or "ownedText" in item)
                and owned_value is True
                and not owned_capacity_text
            ):
                violations.append(f"browser_owned_text_unverified:{qid or 'unknown'}")
            owns_text = (
                owned_value is True
                if isinstance(owned_value, bool)
                else bool(rendered)
            )
            is_image = tag == "img"
            role_bucket = None
            if _is_title_role(role):
                role_bucket = "title"
            elif _is_body_role(role):
                role_bucket = "body"
            is_supporting = _is_supporting_role(role) or _is_supporting_candidate(
                {"data-qid": qid},
                owned_text=rendered,
                capacity_only=True,
            )
            is_decoration = role == "decoration"
            owns_content_text = owns_text and not is_decoration
            if owns_content_text and owned_capacity_text:
                browser_visible_text_parts.append(owned_capacity_text)
            if (
                owns_content_text
                and role_bucket in {"title", "body"}
                and not is_supporting
            ) or is_image:
                browser_content_targets += 1
            if role_bucket and owns_content_text and not is_image and not is_supporting:
                role_nodes[role_bucket].append(item)
            if owns_content_text and role_bucket != "title" and not is_supporting:
                browser_body_candidates += 1
            if is_image:
                browser_images += 1
                if not (
                    role == "image"
                    or role.startswith("image")
                    or role.endswith("image")
                ):
                    violations.append(f"browser_image_role_missing:{qid or 'unknown'}")
                image_value = item.get("image")
                image = image_value if isinstance(image_value, Mapping) else item
                natural_width = _finite_positive_number(image.get("naturalWidth", image.get("natural_width", 0)))
                natural_height = _finite_positive_number(image.get("naturalHeight", image.get("natural_height", 0)))
                image_visible = image.get("cssVisible", image.get("css_visible"))
                if (
                    image.get("complete") is not True
                    or natural_width is None
                    or natural_height is None
                    or image_visible is not True
                ):
                    violations.append("browser_image_unverified")
            visibility_value = item.get("css_visible", item.get("cssVisible", item.get("visible")))
            visible = visibility_value is True
            clipped = any(
                item.get(key) is True
                for key in ("clipped", "glyph_clipped", "glyphClipped", "line_box_clipped", "lineBoxClipped")
            )
            clipping_values = [
                item.get("clipped"),
                item.get("glyph_clipped", item.get("glyphClipped")),
                item.get("line_box_clipped", item.get("lineBoxClipped")),
            ]
            if (owns_content_text or is_image) and not isinstance(visibility_value, bool):
                violations.append(f"browser_visibility_unverified:{qid or 'unknown'}")
            if (owns_content_text or is_image) and not all(isinstance(value, bool) for value in clipping_values):
                violations.append(f"browser_clipping_unverified:{qid or 'unknown'}")
            bbox = item.get("bbox", item.get("rect"))
            bbox_invalid = not isinstance(bbox, dict) or any(key not in bbox for key in ("x", "y", "width", "height"))
            if not bbox_invalid:
                try:
                    bbox_invalid = any(
                        not math.isfinite(float(bbox[key]))
                        or (key in {"width", "height"} and float(bbox[key]) <= 0)
                        for key in ("x", "y", "width", "height")
                    )
                except (TypeError, ValueError):
                    bbox_invalid = True
            if (owns_content_text or is_image) and bbox_invalid:
                violations.append(f"browser_geometry_unverified:{qid or 'unknown'}")
            if owns_content_text and not role:
                violations.append(f"browser_semantic_role_missing:{qid or 'unknown'}")
            if owns_content_text and (not visible or clipped):
                violations.append(f"browser_text_not_visible:{qid or 'unknown'}")
            if owns_content_text:
                font_size = item.get("font_size", item.get("fontSize"))
                font_loaded = item.get("font_loaded", item.get("fontLoaded")) is True
                if (
                    not font_loaded
                    or not isinstance(font_size, (int, float))
                    or float(font_size) <= 0
                ):
                    violations.append(f"browser_font_unverified:{qid or 'unknown'}")
                else:
                    font_floor = 12 if is_supporting else capacity.min_font_px
                    if 0 < float(font_size) < font_floor:
                        prefix = "supporting_font" if is_supporting else "font"
                        violations.append(
                            f"{prefix}:{float(font_size):g}<{font_floor}"
                        )
        if len(qids) != len(set(qids)):
            violations.append("browser_qid_duplicate")
        if browser_content_targets > capacity.max_qids:
            violations.append(
                f"qids:{browser_content_targets}>{capacity.max_qids}"
            )
        # Each item is an independently owned browser text run. Separators
        # inserted here would be synthetic characters that were never rendered.
        browser_visible_chars = sum(len(value) for value in browser_visible_text_parts)
        if browser_visible_chars > capacity.max_visible_text_chars:
            violations.append(
                f"visible_text:{browser_visible_chars}>{capacity.max_visible_text_chars}"
            )
        expected_qids = set(_declared_html_qids(html))
        observed_qids = {qid for qid in qids if qid}
        if expected_qids != observed_qids:
            # Every authored target must have a corresponding Chromium record;
            # static HTML qids cannot substitute for missing browser telemetry.
            violations.append("browser_qid_mismatch")
        if browser_images != image_count:
            violations.append(f"browser_images:{browser_images}!={image_count}")
        if heading_text and not any(
            _normalized_rendered_text(item)
            for item in role_nodes["title"]
        ):
            violations.append("browser_title_role_missing")
        if browser_body_candidates and not any(
            _normalized_rendered_text(item)
            for item in role_nodes["body"]
        ):
            violations.append("browser_body_role_missing")
        browser_title_chars = sum(
            len(_normalized_capacity_owned_text(item) or "") for item in role_nodes["title"]
        )
        browser_body_chars = sum(
            len(_normalized_capacity_owned_text(item) or "") for item in role_nodes["body"]
        )
        if browser_title_chars > capacity.title_max_chars:
            violations.append(
                f"browser_title:{browser_title_chars}>{capacity.title_max_chars}"
            )
        if browser_body_chars > capacity.body_max_chars:
            violations.append(
                f"browser_body:{browser_body_chars}>{capacity.body_max_chars}"
            )
        for role, nodes_for_role in role_nodes.items():
            for item in nodes_for_role:
                if not _normalized_rendered_text(item):
                    violations.append(f"browser_{role}_text_missing")
    elif nodes or image_count or visible_source:
        # A non-empty authored slide without browser telemetry must fail closed;
        # static HTML text and regex image counts cannot satisfy this gate.
        violations.append("browser_metrics_missing")
        if len(visible_source) > capacity.max_visible_text_chars:
            violations.append(
                f"visible_text:{len(visible_source)}>{capacity.max_visible_text_chars}"
            )
        if len(nodes) > capacity.max_qids:
            violations.append(f"qids:{len(nodes)}>{capacity.max_qids}")
        if heading_text and len(heading_text) > capacity.title_max_chars:
            violations.append(f"title:{len(heading_text)}>{capacity.title_max_chars}")
        if body_chars > capacity.body_max_chars:
            violations.append(f"body:{body_chars}>{capacity.body_max_chars}")
    return tuple(violations)


def bounded_capacity_repair_eligible(
    html: str,
    browser_metrics: Any,
    capacity: SlideCapacity,
    *,
    profile_id: str,
) -> bool:
    """Return whether the existing deterministic reducers may repair a slide.

    This is deliberately narrower than "has a capacity violation".  It exposes
    the same fail-closed boundary used by the reducers without weakening QID,
    font, visible-text, semantic ownership, or title-edge safety gates.  A
    caller may attempt at most one fresh Chromium recapture after this check;
    any residual violation is terminal for that bounded repair cycle.
    """

    violations = capacity_violations(html, browser_metrics, capacity)
    if not violations:
        return False
    if violations == ("browser_text_not_visible:header.eyebrow",):
        return True
    if str(profile_id or "").strip().lower() != "ordinary":
        return False
    if capacity.role.strip().upper() != "CLOSING":
        return False
    if reduce_authored_owner_text_capacity(
        html,
        capacity,
        profile_id="ordinary",
        allow_summarization=True,
    ) != html:
        return True
    return any(
        reducer(
            html,
            browser_metrics,
            capacity,
            profile_id=profile_id,
        )
        != html
        for reducer in (
            reduce_authored_closing_capacity,
            reduce_authored_closing_title_punctuation,
        )
    )


def reduce_authored_closing_capacity(
    html: str,
    browser_metrics: Any,
    capacity: SlideCapacity,
    *,
    profile_id: str,
) -> str:
    """Remove only bounded, low-priority CLOSING body targets.

    The reducer is intentionally not a general summarizer. It runs only for an
    ordinary closing slide whose *only* measured failures are the body-character
    and content-target caps. It never changes roles or source-bound content and
    removes at most three leaf body targets before Chromium remeasurement.
    """

    if (
        profile_id != "ordinary"
        or capacity.role.strip().upper() != "CLOSING"
        or _CAPACITY_REDUCER_MARKER in html
        or "data-source-block-id" in html.lower()
    ):
        return html
    violations = capacity_violations(html, browser_metrics, capacity)
    measured: dict[str, tuple[int, int]] = {}
    for violation in violations:
        match = re.fullmatch(r"(qids|browser_body):(\d+)>(\d+)", violation)
        if match is None:
            return html
        measured[match.group(1)] = (int(match.group(2)), int(match.group(3)))
    if not measured or not {"qids", "browser_body"}.issuperset(measured):
        return html
    qid_excess = max(
        0,
        measured.get("qids", (0, 0))[0] - measured.get("qids", (0, 0))[1],
    )
    body_excess = max(
        0,
        measured.get("browser_body", (0, 0))[0]
        - measured.get("browser_body", (0, 0))[1],
    )
    if qid_excess <= 0 and body_excess <= 0:
        return html

    normalized = normalize_browser_metrics(browser_metrics)
    metrics_by_qid = {
        str(item.get("qid") or "").strip(): item
        for item in normalized.get("elements", ())
        if isinstance(item, dict) and str(item.get("qid") or "").strip()
    }
    parser = _CapacityReducerParser(html)
    parser.feed(html)
    parser.close()
    nodes_by_qid = {node.qid: node for node in parser.nodes}

    grouped_items: dict[str | None, int] = {}
    for node in parser.nodes:
        metric = metrics_by_qid.get(node.qid, {})
        if (
            re.search(r"(?:^|[._-])item\d+$", node.qid, flags=re.IGNORECASE)
            and _is_body_role(str(metric.get("semantic_role", "")))
        ):
            grouped_items[node.parent_qid] = grouped_items.get(node.parent_qid, 0) + 1

    candidates: list[tuple[int, int, str, _CapacityReducerNode, int, int]] = []
    for node in parser.nodes:
        metric = metrics_by_qid.get(node.qid)
        if metric is None or node.end is None or node.tag not in {
            "div", "p", "li", "span", "strong", "em", "small",
        }:
            continue
        role = str(metric.get("semantic_role", "")).strip().lower()
        rendered = _normalized_rendered_text(metric)
        owns_text = metric.get("has_owned_text", metric.get("hasOwnedText")) is True
        if not (_is_body_role(role) and owns_text and rendered):
            continue
        descendant_nodes = [
            nodes_by_qid[qid]
            for qid in (node.descendant_qids or ())
            if qid in nodes_by_qid
        ]
        if node.attrs.get("data-source-block-id") or any(
            child.attrs.get("data-source-block-id") for child in descendant_nodes
        ):
            continue
        descendant_metrics = [
            metrics_by_qid[qid]
            for qid in (node.descendant_qids or ())
            if qid in metrics_by_qid
        ]
        if any(
            str(item.get("semantic_role", "")).strip().lower() != "decoration"
            or str(item.get("tag", "")).strip().lower() == "img"
            for item in descendant_metrics
        ):
            continue

        qid_lower = node.qid.lower()
        if re.search(
            r"(?:^|[._-])(cue|footer|note|aside|detail|tagline)$", qid_lower
        ):
            priority = 0
            item_number = 0
        else:
            item_match = re.search(r"(?:^|[._-])item(\d+)$", qid_lower)
            if item_match is None or grouped_items.get(node.parent_qid, 0) < 4:
                continue
            priority = 1
            item_number = int(item_match.group(1))
        candidates.append(
            (priority, -item_number, node.qid, node, len(rendered), 1)
        )

    selected: list[_CapacityReducerNode] = []
    group_removals: dict[str | None, int] = {}
    for _priority, _neg_item, _qid, node, body_reduction, target_reduction in sorted(
        candidates, key=lambda item: item[:3]
    ):
        if len(selected) >= 3:
            break
        is_list_item = bool(
            re.search(r"(?:^|[._-])item\d+$", node.qid, flags=re.IGNORECASE)
        )
        if is_list_item:
            removed = group_removals.get(node.parent_qid, 0)
            if grouped_items.get(node.parent_qid, 0) - removed <= 3:
                continue
            group_removals[node.parent_qid] = removed + 1
        selected.append(node)
        qid_excess -= target_reduction
        body_excess -= body_reduction
        if qid_excess <= 0 and body_excess <= 0:
            break
    if qid_excess > 0 or body_excess > 0 or not selected:
        return html

    reduced = html
    for node in sorted(selected, key=lambda item: item.start, reverse=True):
        reduced = reduced[: node.start] + reduced[node.end :]
    insertion = re.search(r"</head\s*>", reduced, flags=re.IGNORECASE)
    if insertion is None:
        return html
    removed_qids = ",".join(sorted(node.qid for node in selected))
    marker = f"<!-- {_CAPACITY_REDUCER_MARKER}:{removed_qids} -->"
    return reduced[: insertion.start()] + marker + reduced[insertion.start() :]


def reduce_authored_closing_title_punctuation(
    html: str,
    browser_metrics: Any,
    capacity: SlideCapacity,
    *,
    profile_id: str,
) -> str:
    """Remove at most two non-semantic punctuation marks from CLOSING titles."""

    if (
        profile_id != "ordinary"
        or capacity.role.strip().upper() != "CLOSING"
        or _TITLE_PUNCTUATION_REDUCER_MARKER in html
        or "data-source-block-id" in html.lower()
    ):
        return html
    violations = capacity_violations(html, browser_metrics, capacity)
    if len(violations) != 1:
        return html
    match = re.fullmatch(r"browser_title:(\d+)>(\d+)", violations[0])
    if match is None:
        return html
    excess = int(match.group(1)) - int(match.group(2))
    if excess < 1 or excess > 2:
        return html

    normalized = normalize_browser_metrics(browser_metrics)
    title_qids = [
        str(item.get("qid") or "").strip()
        for item in normalized.get("elements", ())
        if isinstance(item, dict)
        and str(item.get("semantic_role") or "").strip().lower() == "title"
        and item.get("has_owned_text", item.get("hasOwnedText")) is True
    ]
    parser = _CapacityReducerParser(html)
    parser.feed(html)
    parser.close()
    nodes = {node.qid: node for node in parser.nodes}
    remaining = excess
    repaired = html
    for qid in reversed(title_qids):
        node = nodes.get(qid)
        if node is None or node.end is None:
            continue
        segment = repaired[node.start : node.end]
        if segment.count("<") != 2:
            continue
        inner_start = segment.find(">") + 1
        inner_end = segment.rfind("<")
        text = segment[inner_start:inner_end]
        revised = text
        for pattern in (r"[.!?。]$", r"[,，]"):
            if remaining <= 0:
                break
            revised, count = re.subn(pattern, "", revised, count=1)
            remaining -= count
        if revised == text:
            continue
        segment = segment[:inner_start] + revised + segment[inner_end:]
        repaired = repaired[: node.start] + segment + repaired[node.end :]
        if remaining <= 0:
            break
    if remaining:
        return html
    insertion = re.search(r"</head\s*>", repaired, flags=re.IGNORECASE)
    if insertion is None:
        return html
    marker = f"<!-- {_TITLE_PUNCTUATION_REDUCER_MARKER} -->"
    return repaired[: insertion.start()] + marker + repaired[insertion.start() :]


def repair_authored_layout_alignment(html: str, *, role: str) -> str:
    """Repair bounded alignment defects without changing slide content.

    The repair is intentionally limited to the exact authored contracts that
    caused the observed PROBLEM and CLOSING imbalance: a zero-inset 22px
    eyebrow box, and a header-body root that omitted the CSS class carrying the
    deck's symmetric 72px margins. It also contracts the exact rotated
    evidence-arrow tip whose painted bounds escape a 72px connector gutter.
    Source-bound HTML is never rewritten.
    """

    normalized_role = role.strip().upper()
    if (
        normalized_role not in {"PROBLEM", "CLOSING"}
        or "data-source-block-id" in html.lower()
    ):
        return html

    repaired = html
    applied: list[str] = []
    if normalized_role == "PROBLEM":
        eyebrow_rule = re.search(
            r"(?P<prefix>\.eyebrow\s*\{)(?P<body>[^}]*)\}",
            repaired,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if eyebrow_rule is not None:
            declarations = eyebrow_rule.group("body")
            if re.search(r"(?:^|;)\s*height\s*:\s*22px\s*;", declarations) and re.search(
                r"(?:^|;)\s*padding-top\s*:\s*0(?:px)?\s*;", declarations
            ):
                declarations = re.sub(
                    r"(?:^|;)\s*height\s*:\s*22px\s*;",
                    ";",
                    declarations,
                    count=1,
                    flags=re.IGNORECASE,
                )
                declarations = re.sub(
                    r"padding-top\s*:\s*0(?:px)?\s*;",
                    "padding-top:34px;",
                    declarations,
                    count=1,
                    flags=re.IGNORECASE,
                )
                repaired = (
                    repaired[: eyebrow_rule.start()]
                    + eyebrow_rule.group("prefix")
                    + declarations
                    + "}"
                    + repaired[eyebrow_rule.end() :]
                )
                applied.append("header-inset")

        if (
            _CONNECTOR_CLEARANCE_MARKER not in repaired
            and 'data-qid="evidence.bridge"' in repaired
            and 'data-qid="evidence.bridge.tip"' in repaired
            and 'data-qid="evidence.measure.main"' in repaired
            and re.search(
                r"\.evidence\s*\{[^}]*grid-template-columns\s*:\s*1fr\s+72px\s+1fr\s*;",
                repaired,
                flags=re.IGNORECASE | re.DOTALL,
            )
            and re.search(
                r"\.bridge-tip\s*\{[^}]*right\s*:\s*-1px\s*;[^}]*top\s*:\s*-6px\s*;"
                r"[^}]*width\s*:\s*14px\s*;[^}]*height\s*:\s*14px\s*;[^}]*"
                r"transform\s*:\s*rotate\(45deg\)\s*;?",
                repaired,
                flags=re.IGNORECASE | re.DOTALL,
            )
        ):
            insertion = re.search(r"</head\s*>", repaired, flags=re.IGNORECASE)
            if insertion is not None:
                rule = (
                    f'<style>/* {_CONNECTOR_CLEARANCE_MARKER} */'
                    '[data-qid="evidence.bridge"]'
                    '{width:44px!important;justify-self:start!important}</style>'
                )
                repaired = (
                    repaired[: insertion.start()]
                    + rule
                    + repaired[insertion.start() :]
                )
                applied.append("connector-clearance")

    if normalized_role == "CLOSING" and re.search(
        r"\.body\s*\{[^}]*\bmargin\s*:\s*28px\s+72px\s+0\b",
        repaired,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        body_root = re.search(
            r"<(?P<tag>div|main|section)\b(?P<attrs>[^>]*\bdata-header-part\s*=\s*(?P<quote>['\"])body(?P=quote)[^>]*)>",
            repaired,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if body_root is not None:
            attrs = body_root.group("attrs")
            class_match = re.search(
                r"\bclass\s*=\s*(?P<quote>['\"])(?P<classes>.*?)(?P=quote)",
                attrs,
                flags=re.IGNORECASE | re.DOTALL,
            )
            if class_match is None:
                replacement = (
                    f'<{body_root.group("tag")} class="body"{attrs}>'
                )
            else:
                classes = class_match.group("classes").split()
                if "body" in {item.casefold() for item in classes}:
                    replacement = body_root.group(0)
                else:
                    updated = " ".join([*classes, "body"])
                    updated_attrs = (
                        attrs[: class_match.start("classes")]
                        + updated
                        + attrs[class_match.end("classes") :]
                    )
                    replacement = f'<{body_root.group("tag")}{updated_attrs}>'
            if replacement != body_root.group(0):
                repaired = (
                    repaired[: body_root.start()]
                    + replacement
                    + repaired[body_root.end() :]
                )
                applied.append("body-class")

    if not applied:
        return html
    insertion = re.search(r"</head\s*>", repaired, flags=re.IGNORECASE)
    if insertion is None:
        return html
    marker = f"<!-- {_LAYOUT_ALIGNMENT_MARKER}:{','.join(applied)} -->"
    return repaired[: insertion.start()] + marker + repaired[insertion.start() :]


def repair_authored_owned_text_paint_order(
    html: str, browser_metrics: Any, *, profile_id: str = "ordinary"
) -> str:
    """Lift one measured text-only flex/grid item above empty sibling paint.

    A static flex/grid item's z-index changes paint order without introducing a
    containing block or touching geometry. This is only a repair candidate: the
    caller must recapture and run the unchanged quality/semantic admission.
    """

    if (
        profile_id != "ordinary"
        or not isinstance(html, str)
        or _OWNED_TEXT_PAINT_ORDER_MARKER in html
        or "data-source-" in html.lower()
        or not isinstance(browser_metrics, Mapping)
        or browser_metrics.get("dom_metrics_version") != "authored-dom-metrics-v16-owned-text"
        or browser_metrics.get("width") != 1280 or browser_metrics.get("height") != 720
        or browser_metrics.get("fonts_ready") is not True
        or browser_metrics.get("source_digest") != "sha256:" + sha256(html.encode("utf-8")).hexdigest()
        or re.fullmatch(r"sha256:[0-9a-f]{64}", str(browser_metrics.get("render_digest", ""))) is None
        or re.search(
            r"<(?:script|link|iframe|object|embed)\b|::?(?:before|after|first-letter|first-line)\b|"
            r"@import\b|(?:^|[;{])\s*(?:animation[\w-]*|transition[\w-]*|content|contain|isolation|perspective|will-change)\s*:",
            html, re.IGNORECASE,
        )
    ):
        return html

    class DirectParents(_AuthoredStartTagCollector):
        def __init__(self) -> None:
            super().__init__()
            self.parents: list[int | None] = []
            self.valid = True

        def handle_starttag(self, tag, attrs) -> None:
            self.parents.append(self._stack[-1][1] if self._stack else None)
            names = [name.lower() for name, _value in attrs]
            self.valid &= len(names) == len(set(names)) and not any(name.startswith("on") for name in names)
            super().handle_starttag(tag, attrs)

        def handle_startendtag(self, tag, attrs) -> None:
            self.parents.append(self._stack[-1][1] if self._stack else None)
            names = [name.lower() for name, _value in attrs]
            self.valid &= len(names) == len(set(names)) and not any(name.startswith("on") for name in names)
            super().handle_startendtag(tag, attrs)

        def handle_endtag(self, tag) -> None:
            self.valid &= bool(self._stack and self._stack[-1][0] == tag.lower())
            super().handle_endtag(tag)

    parser = DirectParents()
    try:
        parser.feed(html)
        parser.close()
    except (TypeError, ValueError):
        return html
    if not parser.valid or parser._stack:
        return html
    declared = [(_attribute_map(attrs).get("data-qid", ""), index) for index, (_tag, attrs, _raw) in enumerate(parser.tags)]
    qids = [qid for qid, _index in declared if qid]
    elements = browser_metrics.get("elements")
    if (
        len(qids) != len(set(qids))
        or any(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", qid) is None for qid in qids)
        or not isinstance(elements, list)
        or any(not isinstance(item, Mapping) or item.get("source_block_id") not in (None, "") for item in elements)
    ):
        return html
    measured_qids = [item.get("qid") for item in elements]
    if any(not isinstance(qid, str) for qid in measured_qids) or len(measured_qids) != len(set(measured_qids)) or set(measured_qids) != set(qids):
        return html
    metrics = {item["qid"]: item for item in elements}
    indices = {qid: index for qid, index in declared if qid}

    def number(value):
        try:
            return float(value) if type(value) in (int, float) and math.isfinite(value) else None
        except OverflowError:
            return None

    def box(value):
        if not isinstance(value, Mapping):
            return None
        values = tuple(number(value.get(key)) for key in ("x", "y", "width", "height"))
        return values if all(v is not None for v in values) and values[2] > 0 and values[3] > 0 else None

    def intersects(a, b):
        return min(a[0] + a[2], b[0] + b[2]) > max(a[0], b[0]) and min(a[1] + a[3], b[1] + b[3]) > max(a[1], b[1])

    def stable(item):
        style, effects, measured = item.get("computed_style"), item.get("visual_effects"), item.get("measurement")
        return (
            all(isinstance(value, Mapping) for value in (style, effects, measured))
            and all(measured.get(key) is True for key in ("geometry", "stacking", "transform", "visual_effects"))
            and item.get("css_visible") is True and number(item.get("effective_opacity")) == 1
            and style.get("opacity") == "1" and style.get("visibility") == "visible" and style.get("zIndex") == "auto"
            and style.get("backgroundBlendMode") == "normal"
            and all(effects.get(key) == value for key, value in (("filter", "none"), ("backdropFilter", "none"), ("clipPath", "none"), ("maskImage", "none"), ("mixBlendMode", "normal")))
            and item.get("intentional") is False and item.get("allow_overlap", False) is False
        )

    candidates: list[tuple[str, str]] = []
    for qid in qids:
        target = metrics[qid]
        evidence = target.get("occlusion_measurement_evidence")
        fraction = number(target.get("continuous_occluded_fraction"))
        if fraction is None or not 0.12 < fraction <= 1:
            continue
        index = indices[qid]
        parent_index = parser.parents[index]
        if parent_index is None or parent_index in (index,):
            return html
        parent_attrs = _attribute_map(parser.tags[parent_index][1])
        parent_qid = parent_attrs.get("data-qid", "")
        parent = metrics.get(parent_qid)
        attrs = _attribute_map(parser.tags[index][1])
        owned_text = re.sub(r"\s+", " ", "".join(parser.owned_text_parts[index])).strip()
        occluders = target.get("occlusion_occluder_qids")
        probe = target.get("static_translation_probe")
        target_box = box(target.get("bbox"))
        parent_box = box(parent.get("bbox")) if parent is not None else None
        if (
            parent is None or target_box is None or parent_box is None
            or not stable(target) or not stable(parent)
            or parser.tags[index][0] not in {"div", "p"} or index in parser.parents
            or not owned_text or target.get("has_owned_text") is not True or target.get("has_owned_paint") is not True
            or "owned_text" not in target or _normalized_capacity_owned_text(target) != owned_text
            or not isinstance(target.get("rendered_text"), str)
            or re.sub(r"\s+", " ", target["rendered_text"]).strip() != owned_text
            or attrs.get("data-semantic-role") not in {"body", "supporting", "title"}
            or target.get("semantic_role") != attrs.get("data-semantic-role")
            or target.get("tag") != parser.tags[index][0] or parent.get("tag") != parser.tags[parent_index][0]
            or target.get("parent_qid") != parent_qid
            or parent["computed_style"].get("display") not in {"flex", "inline-flex", "grid", "inline-grid"}
            or parent["computed_style"].get("position") != "relative"
            or parent["computed_style"].get("transform") != "none"
            or target["computed_style"].get("position") != "static"
            or target["computed_style"].get("display") != "block"
            or target["computed_style"].get("backgroundImage") != "none"
            or target["computed_style"].get("transform") != "none"
            or any(target["computed_style"].get(edge) != "auto" for edge in ("top", "right", "bottom", "left"))
            or not isinstance(probe, Mapping) or probe.get("source") != "chrome"
            or any(probe.get(key) is not True for key in ("telemetryComplete", "layoutIsolation", "stackingStable", "transformComposable"))
            or probe.get("unsupportedReason") is not None
            or any(number(probe.get(key)) != 0 for key in ("flowImpactPx", "maxSiblingDeltaPx", "translationErrorPx"))
            or box(probe.get("targetBoundsPx")) != target_box or box(probe.get("parentBoundsPx")) != parent_box
            or not isinstance(evidence, Mapping) or evidence.get("status") != "measured"
            or evidence.get("stage") != "chrome.owned_paint_sampling" or evidence.get("metric") != "occlusion" or evidence.get("qid") != qid
            or evidence.get("reason") is not None or target.get("occlusion_measurement_error") is not None
            or evidence.get("render_digest") != browser_metrics.get("render_digest") or evidence.get("source_digest") != browser_metrics.get("render_digest")
            or target["measurement"].get("occlusion") is not True
            or target["measurement"].get("clipping") is not True
            or any(target.get(key) is not False for key in ("clipped", "glyph_clipped", "line_box_clipped"))
            or number(target.get("occlusion_unknown_fraction")) != 0 or number(target.get("occlusion_unbound_count")) != 0
            or not isinstance(occluders, list) or not 1 <= len(occluders) <= 4
            or any(not isinstance(value, str) for value in occluders) or len(set(occluders)) != len(occluders)
            or not (parent_box[0] <= target_box[0] and parent_box[1] <= target_box[1] and target_box[0] + target_box[2] <= parent_box[0] + parent_box[2] and target_box[1] + target_box[3] <= parent_box[1] + parent_box[3])
        ):
            return html
        known, covered = number(evidence.get("known")), number(evidence.get("covered"))
        occluded = number(target.get("occluded_fraction"))
        fragments = target.get("text_fragments")
        if (
            known is None or known <= 0 or covered is None or not 0 < covered <= known
            or any(type(evidence.get(key)) is not int for key in ("known", "covered", "sampled"))
            or number(evidence.get("sampled")) != known or number(target.get("occlusion_known_sample_count")) != known
            or number(target.get("occlusion_sample_count")) != known or number(target.get("occlusion_confirmed_covered_sample_count")) != covered
            or occluded is None or not fraction <= occluded <= 1 or abs(occluded - covered / known) > 1e-9
            or number(evidence.get("known_occluded_fraction")) != occluded
            or target.get("occlusion_probe_version") != "owned-paint-v3"
            or target.get("occlusion_sensor_version") != "owned-paint-v5-inset-retry"
            or evidence.get("sensor_version") != target.get("occlusion_sensor_version")
            or not isinstance(fragments, list) or not fragments or any(box(fragment) is None for fragment in fragments)
            or re.search(r"(?:^|;)\s*z-index\s*:[^;]*!important", attrs.get("style", ""), re.IGNORECASE)
        ):
            return html
        if any(not (target_box[0] <= fragment[0] and target_box[1] <= fragment[1] and fragment[0] + fragment[2] <= target_box[0] + target_box[2] and fragment[1] + fragment[3] <= target_box[1] + target_box[3]) for fragment in (box(value) for value in fragments)):
            return html
        for item_qid in (qid, *occluders):
            item = metrics.get(item_qid)
            item_index = indices.get(item_qid)
            proof = item.get("containing_block_proof") if item is not None else None
            if (
                item is None or item_index is None or parser.parents[item_index] != parent_index
                or not isinstance(proof, Mapping) or proof.get("source") != "chrome"
                or proof.get("telemetryComplete") is not True or proof.get("exactBoundOffsetParent") is not True
                or proof.get("offsetParentQid") != parent_qid or item.get("offset_parent_qid") != parent_qid
                or proof.get("offsetParentTag") != parser.tags[parent_index][0] or box(proof.get("offsetParentBoundsPx")) != parent_box
            ):
                return html
            if item_qid == qid:
                continue
            shape_attrs = _attribute_map(parser.tags[item_index][1])
            shape_box = box(item.get("bbox"))
            shape_style = item.get("computed_style", {})
            if not isinstance(shape_style, Mapping):
                return html
            transform = str(shape_style.get("transform", ""))
            matrix = transform[7:-1].split(",") if transform.startswith("matrix(") and transform.endswith(")") else []
            try:
                safe_transform = transform == "none" or len(matrix) == 6 and all(math.isfinite(float(value)) for value in matrix)
            except ValueError:
                safe_transform = False
            if (
                not stable(item) or parser.tags[item_index][0] != "div" or item.get("tag") != "div" or item_index in parser.parents
                or item.get("parent_qid") != parent_qid or item.get("has_owned_text") is not False or item.get("has_owned_paint") is not True
                or parser.owns_text[item_index] or item.get("owned_text") != "" or item.get("rendered_text") != "" or item.get("text_fragments") != []
                or shape_attrs.get("data-semantic-role") not in {"supporting", "decoration"} or item.get("semantic_role") != shape_attrs.get("data-semantic-role")
                or re.search(r"chart|table|graph|plot|sparkline|axis|legend", " ".join(shape_attrs.values()), re.IGNORECASE)
                or any(name.startswith("data-image-") or name in {"aria-label", "role", "alt"} for name in shape_attrs)
                or shape_style.get("position") != "absolute" or shape_style.get("display") != "block" or shape_style.get("backgroundImage") != "none" or not safe_transform
                or shape_box is None or not intersects(target_box, shape_box)
                or number(item.get("effective_z_index")) is None or number(target.get("effective_z_index")) is None or item["effective_z_index"] <= target["effective_z_index"]
            ):
                return html
        ancestors = set()
        cursor = parent_index
        while cursor is not None:
            ancestors.add(cursor)
            cursor = parser.parents[cursor]
        for other_qid, other in metrics.items():
            if other_qid in {qid, *occluders} or indices[other_qid] in ancestors:
                continue
            other_box = box(other.get("bbox"))
            if other_box is None or intersects(target_box, other_box):
                return html
        candidates.append((parent_qid, qid))
    insertion = re.search(r"</head\s*>", html, flags=re.IGNORECASE)
    if len(candidates) != 1 or insertion is None:
        return html
    parent_qid, qid = candidates[0]
    rule = f'<style>/* {_OWNED_TEXT_PAINT_ORDER_MARKER} */[data-qid="{parent_qid}"]>[data-qid="{qid}"]{{z-index:1!important}}</style>'
    return html[:insertion.start()] + rule + html[insertion.start():]


def blocking_safety_issues(report: Any) -> tuple[Any, ...]:
    """Return only binary publication-safety defects from a DOM report.

    Typography availability and contrast remain design-quality signals.  A
    clipped, escaped, overflowing or unintentionally overlapping object is a
    publication blocker regardless of whether VQA was requested.
    """

    safety_categories = {"overflow", "clipping", "off_canvas", "overlap"}
    return tuple(
        issue
        for issue in getattr(report, "issues", ())
        if getattr(issue, "category", None) in safety_categories
    )


__all__ = [
    "AUTHORED_CAPACITY_BOUNDED_REPAIR_REVISION",
    "AuthoredCapacityPlan",
    "CompositionProfile",
    "CompositionProfileId",
    "QualityProfileId",
    "SlideCapacity",
    "build_authored_capacity_plan",
    "blocking_safety_issues",
    "aggregate_owner_capacity_violations",
    "canonicalize_authored_capacity_html",
    "capacity_overflow",
    "capacity_repair_instructions",
    "capacity_violations",
    "bounded_capacity_repair_eligible",
    "ensure_authored_semantic_roles",
    "repair_authored_layout_alignment",
    "repair_authored_owned_text_paint_order",
    "reduce_authored_owner_text_capacity",
    "semantic_repair_boundary_violations",
    "reduce_authored_closing_capacity",
    "reduce_authored_closing_title_punctuation",
    "resolve_composition_profile",
]
