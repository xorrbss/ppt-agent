"""Worker-side Chromium session shared by the renders of one worker process.

The framed worker protocol (``utils.renderer_frame_codec``) lets one renderer
process serve many requests. This module is what makes that worth doing: it
keeps a single supervised Chromium alive for the process and renders each
request through it, instead of paying a browser cold start per slide.

Isolation is unchanged per render and is the reason the session is scoped to
the process rather than to the deployment: the parent opens one process per
``deck_scope``, and inside it every request still gets a fresh disposable
``Target.createBrowserContext`` + target + session which are disposed before
the response is written (see ``utils.slide_capture_pipe_render``). What is
reused is the process, its network-none container, and its throwaway profile
directory -- never a page, a context, or any renderer state across requests,
and never anything across deck scopes, because those never share a process.
"""

from __future__ import annotations

import pathlib
import shutil
import tempfile
from typing import Any, Mapping, Sequence

from utils.renderer_isolation import AdmittedHtml
from models.canvas_defaults import DEFAULT_CANVAS_HEIGHT_PX, DEFAULT_CANVAS_WIDTH_PX


def _pipe_bindings() -> dict[str, Any]:
    """Resolve the renderer's Chrome bindings from ``utils.slide_capture``.

    Looked up at call time, not import time: ``utils.slide_capture`` imports
    this module's siblings at its own module bottom, and tests monkeypatch
    ``find_chrome`` on the module object.
    """

    from utils import slide_capture

    return {
        "blocked_urls": slide_capture._BLOCKED_RENDERER_URL_PATTERNS,
        "chrome_pipe": slide_capture._ChromePipe,
        "find_chrome": slide_capture.find_chrome,
        "instrument_html": slide_capture._instrument_authored_html,
        "runtime_value": slide_capture._runtime_value,
    }


def render_admitted_html_over_pipe(
    admitted: AdmittedHtml, timeout: int, *,
    width_px: int = DEFAULT_CANVAS_WIDTH_PX, height_px: int = DEFAULT_CANVAS_HEIGHT_PX,
) -> tuple[bytes, Mapping[str, Any] | None]:
    """Render one admitted document in a private, single-use Chrome process."""

    from utils.slide_capture_pipe_render import render_admitted_html_over_pipe as render_one

    return render_one(
        admitted, timeout, width_px=width_px, height_px=height_px,
        **_pipe_bindings(),
    )


def render_admitted_htmls_over_pipe(
    admitted_htmls: Sequence[AdmittedHtml], timeout: int, *,
    width_px: int = DEFAULT_CANVAS_WIDTH_PX, height_px: int = DEFAULT_CANVAS_HEIGHT_PX,
) -> list[tuple[bytes, Mapping[str, Any] | None]]:
    """Worker-only operation batch using one Chrome pipe and fresh targets."""

    from utils.slide_capture_pipe_render import render_admitted_htmls_over_pipe as render_many

    return render_many(
        admitted_htmls, timeout, width_px=width_px, height_px=height_px,
        **_pipe_bindings(),
    )


def print_admitted_htmls_to_pdf_over_pipe(
    admitted_htmls: Sequence[AdmittedHtml], timeout: int, *,
    width_px: int, height_px: int, paper_width_in: float, paper_height_in: float,
) -> list[bytes]:
    """Worker-only vector PDF batch: same pipe and isolation, printToPDF capture."""

    from utils.slide_capture_pipe_pdf import print_admitted_htmls_to_pdf_over_pipe as print_many

    bindings = _pipe_bindings()
    bindings.pop("runtime_value")
    return print_many(
        admitted_htmls, timeout, width_px=width_px, height_px=height_px,
        paper_width_in=paper_width_in, paper_height_in=paper_height_in,
        **bindings,
    )


class _RetainedTransport:
    """Adapter that hides ``close()`` from a single-batch render helper.

    ``render_admitted_htmls_over_pipe`` owns the transport it is handed and
    closes it when its batch ends. A session outlives its batches, so it hands
    over this wrapper and closes the real transport itself.
    """

    def __init__(self, transport: Any) -> None:
        self._transport = transport

    def command(self, *args: Any, **kwargs: Any) -> Mapping[str, Any]:
        return self._transport.command(*args, **kwargs)

    def close(self) -> None:  # noqa: D401 - deliberately inert
        """No-op: the owning session closes the real transport."""


class RendererChromiumSession:
    """One long-lived Chromium for the renders served by one worker process."""

    def __init__(self) -> None:
        self._transport: Any = None
        self._profile_root: str | None = None
        self._renders = 0

    @property
    def render_count(self) -> int:
        return self._renders

    @property
    def is_started(self) -> bool:
        return self._transport is not None

    def render(
        self, admitted: AdmittedHtml, timeout: int, *,
        width_px: int = DEFAULT_CANVAS_WIDTH_PX, height_px: int = DEFAULT_CANVAS_HEIGHT_PX,
    ) -> tuple[bytes, Mapping[str, Any] | None]:
        from utils.slide_capture_pipe_render import render_admitted_html_over_pipe as render_one

        bindings = dict(_pipe_bindings())
        bindings["chrome_pipe"] = self._chrome_pipe
        result = render_one(
            admitted, timeout, width_px=width_px, height_px=height_px,
            **bindings,
        )
        self._renders += 1
        return result

    def _chrome_pipe(self, chrome: str, _profile_path: pathlib.Path) -> _RetainedTransport:
        # The per-batch temporary profile path offered by the render helper is
        # deliberately ignored: it is removed when that batch returns, while
        # this session's Chromium must keep its profile for its whole life.
        if self._transport is None:
            from utils import slide_capture

            self._profile_root = tempfile.mkdtemp(prefix="authored-renderer-session-")
            self._transport = slide_capture._ChromePipe(
                chrome, pathlib.Path(self._profile_root) / "profile"
            )
        return _RetainedTransport(self._transport)

    def close(self) -> None:
        transport, self._transport = self._transport, None
        profile_root, self._profile_root = self._profile_root, None
        try:
            if transport is not None:
                transport.close()
        finally:
            if profile_root:
                shutil.rmtree(profile_root, ignore_errors=True)


__all__ = [
    "RendererChromiumSession",
    "print_admitted_htmls_to_pdf_over_pipe",
    "render_admitted_html_over_pipe",
    "render_admitted_htmls_over_pipe",
]
