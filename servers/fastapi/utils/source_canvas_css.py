"""Validation-only expansion of safe responsive master stylesheet branches."""
from __future__ import annotations

import tinycss2

from utils.renderer_worker_policy import RendererPolicyError


def flatten_layout_conditions(css: str) -> str:
    """Do not evaluate breakpoints here; Chromium owns their source viewport.

    Only media/container grouping is flattened, and every branch is submitted
    to the existing CSS property/value admission. Resource and active at-rules
    remain present so the existing policy rejects them.
    """
    if "@media" not in css.lower() and "@container" not in css.lower():
        return css

    def flatten(rules: list, depth: int = 0) -> str:
        if depth > 16:
            raise RendererPolicyError("source canvas CSS nesting limit exceeded")
        result: list[str] = []
        for rule in rules:
            if rule.type == "error":
                raise RendererPolicyError("source canvas CSS malformed")
            if rule.type == "at-rule" and rule.lower_at_keyword in {"media", "container"}:
                if rule.content is None:
                    raise RendererPolicyError("source canvas conditional CSS has no body")
                result.append(flatten(tinycss2.parse_rule_list(rule.content), depth + 1))
            else:
                result.append(tinycss2.serialize([rule]))
        return "".join(result)

    return flatten(tinycss2.parse_stylesheet(css))
