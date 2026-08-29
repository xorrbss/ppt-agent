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
from html.parser import HTMLParser
from typing import Any, Literal, Sequence

from utils.authored_dom_quality import build_compact_dom_map, normalize_browser_metrics


CompositionProfileId = Literal["ordinary", "source-verbatim", "source-outline"]
QualityProfileId = Literal["development", "release"]
AUTHORED_CAPACITY_BOUNDED_REPAIR_REVISION = "authored-capacity-bounded-repair/v1"
_CAPACITY_REDUCER_MARKER = "authored-closing-capacity-reducer-v1"
_TITLE_PUNCTUATION_REDUCER_MARKER = "authored-closing-title-punctuation-reducer-v1"
_LAYOUT_ALIGNMENT_MARKER = "authored-layout-alignment-repair-v1"
_CONNECTOR_CLEARANCE_MARKER = "authored-connector-clearance-repair-v1"


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
    min_font_px: int = 16
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
        return (
            "SERVER-FROZEN COMPOSITION ENVELOPE (do not negotiate): "
            "viewport 1280x720; keep every painted target inside the viewport; "
            f"one-line title target <= {self.title_max_chars} chars; "
            f"body target <= {self.body_max_chars} chars and total visible text <= "
            f"{self.max_visible_text_chars} chars; {image_rule}; "
            f"keep semantic targets <= {self.max_qids}; never use text below "
            f"{self.min_font_px}px. {source_rule}"
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


class _AuthoredStartTagCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.tags: list[tuple[str, list[tuple[str, str | None]], str]] = []
        self.owns_text: list[bool] = []
        self.owned_text_parts: list[list[str]] = []
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
            elif _is_primary_title_candidate(tag, attrs) and owns_text:
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
            if owns_content_text and rendered:
                browser_visible_text_parts.append(rendered)
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
            len(_normalized_rendered_text(item)) for item in role_nodes["title"]
        )
        browser_body_chars = sum(
            len(_normalized_rendered_text(item)) for item in role_nodes["body"]
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
    "capacity_overflow",
    "capacity_repair_instructions",
    "capacity_violations",
    "ensure_authored_semantic_roles",
    "repair_authored_layout_alignment",
    "reduce_authored_closing_capacity",
    "reduce_authored_closing_title_punctuation",
    "resolve_composition_profile",
]
