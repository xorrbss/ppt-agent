"""Skip markers for tests that need a host asset the repository does not ship.

Each marker is evaluated against the real capability, never against the
platform name, so a host that does provide the asset still runs the test.
"""

import pytest

from services.authored_vnext.generation.retained_reference import (
    OSB_RETAINED_REFERENCE_ID,
    _reference_path,
)
from services.authored_vnext.generation.scene_compiler import _system_font_path


def _osb_retained_reference_available() -> bool:
    try:
        return _reference_path(OSB_RETAINED_REFERENCE_ID) is not None
    except ValueError:
        return False


#: The authored-vnext scene compiler measures text with the Authored-owned
#: font file ``Malgun Gothic`` (a proprietary Windows face, not vendored).
requires_malgun_gothic_font = pytest.mark.skipif(
    _system_font_path("Malgun Gothic") is None,
    reason="requires a readable Malgun Gothic font file (Windows face, not vendored)",
)

#: The retained OSB reference deck (``osb-template-24-layouts.pptx``) is a
#: proprietary customer template that is not committed to the repository.
requires_osb_retained_reference = pytest.mark.skipif(
    not _osb_retained_reference_available(),
    reason="requires the retained OSB reference PPTX (osb-template-24-layouts.pptx, not committed)",
)


_ONE_SHOT_PROBE_HTML = "<!doctype html><html><body><main>probe</main></body></html>"
_one_shot_render_probe: list[str | None] = []


def skip_unless_one_shot_headless_render() -> None:
    """Skip when the host browser cannot complete a one-shot headless screenshot.

    The authored DOM-metrics recollection and the headless-shell font checks
    drive ``chrome --headless --screenshot/--dump-dom`` with a virtual-time
    budget. Some hosts (a root container with a newer full Chrome, a sandbox
    without a usable headless GPU path) never finish that call. The probe runs
    the real renderer once per process on a trivial page, so a host where it
    works still runs the test, and a real regression in the renderer itself is
    not masked on such a host.
    """
    import asyncio

    from utils import slide_capture

    if not _one_shot_render_probe:
        try:
            asyncio.run(
                slide_capture._render_html_to_png_subprocess(
                    _ONE_SHOT_PROBE_HTML, timeout=25, admitted_source=True
                )
            )
        except Exception as error:  # noqa: BLE001 - any failure means "host cannot render"
            _one_shot_render_probe.append(f"{type(error).__name__}: {error}"[:200])
        else:
            _one_shot_render_probe.append(None)
    reason = _one_shot_render_probe[0]
    if reason is not None:
        pytest.skip(f"host browser cannot complete a one-shot headless render ({reason})")
