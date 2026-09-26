"""CSS and attribute admission checks for the renderer."""

from __future__ import annotations

import html as html_lib
import re

from utils.renderer_css_policy import ALLOWED_CSS_PROPERTIES
from utils.renderer_worker_policy import RendererPolicyError

MAX_HTML_BYTES = 2 * 1024 * 1024
_ALLOWED_CSS_PROPERTIES = ALLOWED_CSS_PROPERTIES
#: How much of a rejected declaration's value is echoed into the policy
#: error. Enough to identify the declaration, short enough that untrusted
#: model output cannot flood a log line.
_REJECTED_VALUE_LOG_CHARS = 120

_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_CSS_BLOCK = re.compile(r"([^{}]+)\{([^{}]*)\}", re.DOTALL)
_UNSAFE_CSS = re.compile(
    r"@(?:import|font-face|namespace|supports|document|page|keyframes)|"
    r"(?:url|image-set|cross-fade|element|expression)\s*\(|"
    r"-moz-binding|behavior\s*:|javascript\s*:|file\s*:|https?\s*:",
    re.IGNORECASE,
)
_FONT_FAMILY = re.compile(r"(?:^|[;{])\s*font-family\s*:\s*([^;}]+)", re.IGNORECASE)


def duplicate_attribute_counts(
    attrs: list[tuple[str, str | None]],
) -> list[tuple[str, int]]:
    """Return duplicate attribute names in first-seen order with occurrence counts."""

    counts: dict[str, int] = {}
    duplicate_order: list[str] = []
    for raw_name, _raw_value in attrs:
        name = raw_name.lower()
        counts[name] = counts.get(name, 0) + 1
        if counts[name] == 2:
            duplicate_order.append(name)
    return [(name, counts[name]) for name in duplicate_order]


def _validate_css(css: str) -> set[str]:
    normalized = html_lib.unescape(_CSS_COMMENT.sub("", css))
    if len(normalized.encode("utf-8")) > MAX_HTML_BYTES:
        raise RendererPolicyError("CSS exceeds authored source limit")
    if _UNSAFE_CSS.search(normalized):
        raise RendererPolicyError("CSS contains an external, active, or font resource")
    declarations: list[str] = []
    blocks = list(_CSS_BLOCK.finditer(normalized))
    if "{" in normalized or "}" in normalized:
        residue = _CSS_BLOCK.sub("", normalized)
        if "{" in residue or "}" in residue:
            raise RendererPolicyError("CSS contains unbalanced or nested rules")
        declarations.extend(match.group(2) for match in blocks)
    else:
        declarations.append(normalized)
    families: set[str] = set()
    for declaration_block in declarations:
        for declaration in declaration_block.split(";"):
            if not declaration.strip():
                continue
            if ":" not in declaration:
                raise RendererPolicyError("CSS declaration is malformed")
            property_name, value = declaration.split(":", 1)
            property_name = property_name.strip().lower()
            if property_name not in _ALLOWED_CSS_PROPERTIES:
                # The value is reported alongside the name because the allowlist
                # is value-blind: `visibility: visible` and `visibility: hidden`
                # are rejected identically, and only the value says whether the
                # property should be admitted or whether it would hide content
                # from the hybrid-export measurer. Without it every rejection
                # investigation stops at a guess. Truncated because a rejected
                # declaration is untrusted model output on a log line.
                raise RendererPolicyError(
                    "CSS property is not allowed: "
                    f"{property_name}: {value.strip()[:_REJECTED_VALUE_LOG_CHARS]}"
                )
            if len(value) > 8192 or "\\" in property_name or "\\" in value:
                raise RendererPolicyError("CSS value exceeds policy")
    for match in _FONT_FAMILY.finditer(";" + normalized):
        family_value = match.group(1).strip()
        if len(family_value) > 512:
            raise RendererPolicyError("font-family declaration exceeds policy")
        families.update(
            item.strip().strip("\"'").lower()
            for item in family_value.split(",")
            if item.strip()
        )
    return families
