"""HTML5 tree normalization for authored slide HTML."""

from __future__ import annotations

from utils.authored_html_tree.parser_adapter import NormalizationProfile, parse_authored_html
from utils.authored_html_tree.raw_diagnostics import (
    RawAuthoredHtmlViolation,
    RawDiagnostics,
    diagnose_raw_authored_html,
)
from utils.authored_html_tree.receipts import ReceiptCollector
from utils.authored_html_tree.rewrite_rules import apply_rewrite_rules
from utils.authored_html_tree.serializer import serialize_authored_html
from utils.authored_void_end_tag_normalize import html5_void_end_tag_receipts


def normalize_authored_html_tree(
    html: str,
    *,
    profile: NormalizationProfile,
) -> tuple[str, list[dict[str, str | int]]]:
    """Diagnose, parse, rewrite, and canonically serialize authored HTML."""

    diagnostics = diagnose_raw_authored_html(html)
    if diagnostics.violations:
        raise RawAuthoredHtmlViolation(diagnostics)
    parsed = parse_authored_html(html, profile=profile)
    receipts = ReceiptCollector()
    receipts.extend_legacy(html5_void_end_tag_receipts(parsed.errors))
    apply_rewrite_rules(parsed.root, receipts)
    return serialize_authored_html(parsed), receipts.entries()


__all__ = [
    "NormalizationProfile",
    "RawAuthoredHtmlViolation",
    "RawDiagnostics",
    "diagnose_raw_authored_html",
    "normalize_authored_html_tree",
]
