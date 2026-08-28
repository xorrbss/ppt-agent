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
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Literal, Sequence

from utils.authored_dom_quality import build_compact_dom_map, normalize_browser_metrics


CompositionProfileId = Literal["ordinary", "source-verbatim", "source-outline"]


@dataclass(frozen=True, slots=True)
class CompositionProfile:
    """Request-level quality policy shared by first authoring and repair."""

    profile_id: CompositionProfileId
    text_mode: Literal["authored", "verbatim", "outline"]
    allow_summarization: bool
    strict_dom_gate: bool
    max_repair_cycles: int
    require_atomic_publication: bool = True


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

    if bool(getattr(request, "_source_content_authored_reflow", False)):
        policy = getattr(request, "content_policy", None)
        text_mode = str(getattr(policy, "text", "verbatim") or "verbatim").strip().lower()
        if text_mode == "verbatim":
            return CompositionProfile(
                "source-verbatim", "verbatim", False, True, 2
            )
        return CompositionProfile("source-outline", "outline", True, True, 2)
    return CompositionProfile("ordinary", "authored", True, True, 1)


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

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag.lower(), attrs, self.get_starttag_text()))

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        self.tags.append((tag.lower(), attrs, self.get_starttag_text()))


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
    return qid in {"title", "text.title", *primary} or any(
        qid.startswith(f"{prefix}.") for prefix in primary
    )


def _is_supporting_candidate(attrs: dict[str, str]) -> bool:
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
    return chart_annotation or any(
        token in exact
        or token.startswith("metric")
        or token.startswith("label")
        or token.startswith("num")
        for token in tokens
    )


def ensure_authored_semantic_roles(html: str) -> str:
    """Stamp deterministic roles onto the exact HTML rendered by Chromium.

    Deterministic image, primary-title, and supporting QID classifications take
    precedence over model-declared values. Other non-empty declarations remain
    authoritative, and missing roles become ``body``. Attribute-only stamping
    is idempotent and happens before render so HTML, PNG, and browser evidence
    share one digest-bound source.
    """

    if not isinstance(html, str):
        raise TypeError("authored HTML must be a string")
    parser = _AuthoredStartTagCollector()
    parser.feed(html)
    parser.close()
    cursor = 0
    parts: list[str] = []
    for tag, raw_attrs, raw_tag in parser.tags:
        index = html.find(raw_tag, cursor)
        if index < 0:
            continue
        parts.append(html[cursor:index])
        attrs = _attribute_map(raw_attrs)
        replacement = raw_tag
        if attrs.get("data-qid", "").strip():
            declared = attrs.get("data-semantic-role", "").strip()
            if tag == "img":
                role = "image"
            elif _is_primary_title_candidate(tag, attrs):
                role = "title"
            elif _is_supporting_candidate(attrs):
                role = "supporting"
            else:
                role = declared or "body"
            if role != declared:
                replacement = _insert_or_replace_attribute(
                    raw_tag, attrs, "data-semantic-role", role
                )
        parts.append(replacement)
        cursor = index + len(raw_tag)
    parts.append(html[cursor:])
    return "".join(parts)


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
            if owns_text and rendered:
                browser_visible_text_parts.append(rendered)
            role_bucket = None
            if _is_title_role(role):
                role_bucket = "title"
            elif _is_body_role(role):
                role_bucket = "body"
            is_supporting = _is_supporting_role(role)
            if (owns_text and role_bucket in {"title", "body"}) or is_image:
                browser_content_targets += 1
            if role_bucket and owns_text and not is_image:
                role_nodes[role_bucket].append(item)
            if owns_text and role_bucket != "title" and not is_supporting:
                browser_body_candidates += 1
            if is_image:
                browser_images += 1
                if not (
                    role == "image"
                    or role.startswith("image")
                    or role.endswith("image")
                ):
                    violations.append(f"browser_image_role_missing:{qid or 'unknown'}")
                image = item.get("image") if isinstance(item.get("image"), dict) else item
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
            if (owns_text or is_image) and not isinstance(visibility_value, bool):
                violations.append(f"browser_visibility_unverified:{qid or 'unknown'}")
            if (owns_text or is_image) and not all(isinstance(value, bool) for value in clipping_values):
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
            if (owns_text or is_image) and bbox_invalid:
                violations.append(f"browser_geometry_unverified:{qid or 'unknown'}")
            if owns_text and not role:
                violations.append(f"browser_semantic_role_missing:{qid or 'unknown'}")
            if owns_text and (not visible or clipped):
                violations.append(f"browser_text_not_visible:{qid or 'unknown'}")
            if owns_text:
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
    "AuthoredCapacityPlan",
    "CompositionProfile",
    "CompositionProfileId",
    "SlideCapacity",
    "build_authored_capacity_plan",
    "blocking_safety_issues",
    "capacity_overflow",
    "capacity_violations",
    "ensure_authored_semantic_roles",
    "resolve_composition_profile",
]
