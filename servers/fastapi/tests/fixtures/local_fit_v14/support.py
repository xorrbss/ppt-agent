"""Shared driver for the real-Chrome local-fit v1.4 behaviour tests.

Loaded by path from the two ``test_slide_capture_fit_v14_chrome*`` modules so
they can share one render cache without adding a package to ``tests/unit``.
"""

from __future__ import annotations

import asyncio
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from services.authored_local_fit_policy import html_digest, local_fit_policy
from utils.authored_fit_edits import apply_fit_edits
from utils.slide_capture import render_html_to_png_with_metrics

FIXTURES = Path(__file__).resolve().parent
V14 = "authored-local-fit/v1.4"
V15 = "authored-local-fit/v1.5"
_QID = re.compile(r'data-qid="([^"]+)"')


def load(name: str) -> str:
    return (FIXTURES / f"{name}.html").read_text(encoding="utf-8")


def policy_for(html: str, revision: str = V15) -> dict[str, Any]:
    digest = html_digest(html)
    return local_fit_policy(
        author_revision=digest, original_html_digest=digest,
        slide_index=0, policy_revision=revision,
    )


def render(html: str, policy: dict[str, Any], *, fit: bool):
    return asyncio.run(render_html_to_png_with_metrics(
        html, timeout=60, fit=fit, fit_policy=policy, fresh=True,
    ))


@lru_cache(maxsize=None)
def fit(name: str, revision: str = V15):
    """Render a fixture once per (fixture, policy) and share the capture."""
    html = load(name)
    policy = policy_for(html, revision)
    return html, policy, render(html, policy, fit=True)


def report_of(capture) -> dict[str, Any]:
    report = capture.fit_report
    assert isinstance(report, dict), "the fitter returned no final report"
    return report


def bboxes(capture) -> dict[str, dict[str, float]]:
    return {
        item["qid"]: item["bbox"]
        for item in (capture.browser_metrics or {}).get("elements", [])
        if item.get("qid")
    }


def bottom(box: dict[str, float]) -> float:
    return box["y"] + box["height"]


def rejection_codes(report: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for candidate in report.get("candidates", []):
        code = (candidate.get("rejection") or {}).get("code")
        if code:
            counts[code] = counts.get(code, 0) + 1
    return counts


def candidates_with(report: dict[str, Any], code: str) -> list[dict[str, Any]]:
    return [
        candidate for candidate in report.get("candidates", [])
        if (candidate.get("rejection") or {}).get("code") == code
    ]


def is_subsequence(needle: str, haystack: str) -> bool:
    position = 0
    for character in needle:
        position = haystack.find(character, position)
        if position < 0:
            return False
        position += 1
    return True


def assert_byte_preserving(original: str, fitted: str, edits: list[dict[str, str]]) -> None:
    """The fitted source differs only by appended ``style`` declarations.

    The fixtures carry no authored ``style`` attributes, so every edit must
    appear as an inserted `` style="..."`` token (edits arrive in document
    order, so the first remaining occurrence is that edit's) whose removal
    yields the original bytes; the original is therefore a subsequence.
    """

    assert edits, "a changed capture must report its edits"
    assert fitted == apply_fit_edits(original, edits)
    assert _QID.findall(original) == _QID.findall(fitted)
    stripped = fitted
    for edit in edits:
        token = f' style="{edit["declarations"]}"'
        assert token in stripped, (edit, token)
        stripped = stripped.replace(token, "", 1)
    assert stripped == original
    assert is_subsequence(original, fitted)


def strip_timing(report: dict[str, Any]) -> dict[str, Any]:
    result = dict(report)
    telemetry = dict(result.get("telemetry") or {})
    telemetry.pop("fit_search_ms", None)
    result["telemetry"] = telemetry
    return result
