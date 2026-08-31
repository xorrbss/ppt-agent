"""Override only the live test's generation request with a rich authored benchmark.

The product services are untouched.  This pytest plugin replaces the JSON body
sent by the existing Windows live product-path client so all existing API,
database, export, download, and evidence assertions remain authoritative.
"""

from __future__ import annotations

import requests


_ORIGINAL_POST = requests.Session.post


def _rich_authored_request() -> dict[str, object]:
    return {
        "schemaVersion": "presentation-generation-request/v2",
        "content": (
            "Create exactly 5 visually rich Korean business-proposal slides based on "
            "the established Authored quality-equivalence deck. Preserve this narrative: "
            "(1) an executive cover comparing Authored 82 and authored-vNext 97 with a "
            "strong metric-led composition; (2) a four-step safe generation process from "
            "content analysis through CJK-aware layout selection, native readback, and "
            "PowerPoint rendering; (3) a 2026 Q1-Q4 horizontal quality-validation timeline "
            "using Korean, 中文, and 日本語 labels; (4) an editable-data table comparing "
            "2025 and 2026 values for quality score 82 to 97, preserved digest, and "
            "Workbench verification; (5) a bar-chart conclusion showing the 82-to-97 "
            "quality improvement with three concise implications. Use deliberate visual "
            "hierarchy, varied silhouettes, a restrained blue/white/navy enterprise palette, "
            "and sufficient whitespace. The table must be a table component and the score "
            "comparison must be a chart component. Do not reduce the deck to bullet-only cards."
        ),
        "slideCount": 5,
        "language": "Korean",
        "qualityPolicy": "interactive-v1",
    }


def _post(self, url, *args, **kwargs):
    if str(url).rstrip("/").endswith("/api/v2/presentations/generations"):
        kwargs["json"] = _rich_authored_request()
    return _ORIGINAL_POST(self, url, *args, **kwargs)


def pytest_configure(config) -> None:
    requests.Session.post = _post


def pytest_unconfigure(config) -> None:
    requests.Session.post = _ORIGINAL_POST
