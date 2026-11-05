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
