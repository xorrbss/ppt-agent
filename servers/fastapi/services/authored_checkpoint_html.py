"""The one place that reads a slide checkpoint's downstream HTML.

``author_html`` and ``illustrated_html`` are both ``Text`` columns on
``authored_slide_checkpoints``, and for the ordinary slide -- no illustration,
no quality-loop rewrite -- they hold the same document.  Every authored slide
therefore wrote its whole HTML into the database twice.

``AUTHORED_CHECKPOINT_HTML_DEDUPE_ENABLED`` stores ``NULL`` for the second copy
when it is byte-identical to the first.  That is only safe while every reader
comes through :func:`checkpoint_stage_html`, which restores the fallback using
the stage status: ``illustrated_html IS NULL`` with a completed stage means
"the same document as ``author_html``", while a stage that has not completed
still means "nothing written yet".  The status is what makes the two cases
distinguishable, and ``start_stage`` resets every downstream status to
``pending`` before a re-author, so a fallback can never reach across a
re-author into a newer ``author_html``.

The knob stays **false** until the last reader is routed
(``services/authored_presentation_pipeline.py``, owned elsewhere); with it off
this module returns exactly what the column holds.
"""

from __future__ import annotations

from typing import Any

from utils.get_env_programme import is_authored_checkpoint_html_dedupe_enabled


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def checkpoint_stage_html(checkpoint: Any, *, stage: str = "render", publishable: bool = False) -> str | None:
    """The HTML ``stage`` published for this slide, or ``None``.

    ``stage`` names the checkpoint stage whose completion makes the collapsed
    ``author_html`` fallback meaningful: ``illustrate`` for the illustration
    resume path, ``render`` for everything downstream of it.
    """

    if checkpoint is None:
        return None
    if publishable:
        from services.authored_admission_checkpoint import enforce_receipts
        from services.authored_admission_context import digest_html

        if enforce_receipts():
            receipt = (getattr(checkpoint, "error", None) or {}).get("admission", {})
            html = checkpoint_stage_html(checkpoint, stage=stage)
            if (not receipt.get("publishable") or html is None or receipt.get("htmlDigest") != digest_html(html)
                    or receipt.get("attemptToken") != getattr(checkpoint, "attempt_token", None)):
                return None
    stored = _text(getattr(checkpoint, "illustrated_html", None))
    if stored is not None:
        return stored
    status = getattr(checkpoint, f"{stage}_status", None)
    if status not in ("completed", "skipped"):
        return None
    return _text(getattr(checkpoint, "author_html", None))


def collapses_to_author_html(checkpoint: Any, html: str | None) -> bool:
    """Whether ``html`` may be stored as ``NULL`` because it repeats the author's.

    Byte equality only: a single character of difference keeps the second copy,
    so the fallback can never return a document the stage did not publish.
    """

    if html is None or not is_authored_checkpoint_html_dedupe_enabled():
        return False
    return _text(getattr(checkpoint, "author_html", None)) == html


__all__ = ["checkpoint_stage_html", "collapses_to_author_html"]
