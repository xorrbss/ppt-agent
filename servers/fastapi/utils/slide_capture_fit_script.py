"""STUB: replaced by the fit fragment module.

Placeholder for the in-page fitter fragment, which is written in parallel by
another session.  It exposes exactly the names the probe and the API
reconstruction code against; the real module replaces this file wholesale.
"""

from __future__ import annotations

FIT_CLASS = "codex-fit-v1"
FIT_VERSION = "authored-in-render-fit/v1"

_EMPTY_REPORT = (
    "{version: '"
    + FIT_VERSION
    + "', requested: false, fit_state: %s, partial: false, error: null, "
    "cards: [], edits: []}"
)


def fit_script_fragment() -> str:
    """Return the in-page fitter as plain JavaScript (single braces)."""
    # STUB: replaced by the fit fragment module.
    return (
        "const __codexFit = {\n"
        "  reset() {},\n"
        "  apply(pass) { return " + (_EMPTY_REPORT % "pass") + "; },\n"
        "  report() { return " + (_EMPTY_REPORT % "'provisional'") + "; }\n"
        "};\n"
    )


__all__ = ["FIT_CLASS", "FIT_VERSION", "fit_script_fragment"]
