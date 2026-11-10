"""Content-free stage-error diagnostics validated at the persistence boundary.

The owner-capacity diagnostic schema and ``safe_owner_capacity_diagnostic``
moved unchanged from ``authored_author_capacity_recovery`` (which re-exports
them) so ``AuthoredStageError.as_safe_dict``, which every driver's page
failure runs, does not import the authored composition policy.
"""

from __future__ import annotations

import re
from typing import Any

DIAGNOSTIC_SCHEMA = "authored-owner-capacity-diagnostic/v1"
DIAGNOSTIC_OWNER_LIMIT = 32
DIAGNOSTIC_ROLES = frozenset({"title", "body", "supporting", "image", "decoration", "other"})
DIAGNOSTIC_TAGS = frozenset({
    "h1", "h2", "h3", "h4", "h5", "h6", "p", "span", "div", "section",
    "li", "td", "th", "img", "text", "other",
})
CAPACITY_KEYS = ("owner_qids", "owner_title", "owner_body", "owner_visible_text", "owner_images")


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
