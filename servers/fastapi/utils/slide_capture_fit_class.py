"""The in-render fit opt-in class, read by every render request.

``utils/slide_capture_fit.py`` checks each render request for it, fit or not,
so it lives apart from the fitter script it selects
(``utils/slide_capture_fit_in_render_script.py``, which re-exports it).
"""

from __future__ import annotations

#: Opt-in class on ``<html>``: a global attribute needs no markup-allowlist change, and
#: the model cannot know the name, so no authored stylesheet can target it.
FIT_CLASS = "codex-fit-v1"

__all__ = ["FIT_CLASS"]
