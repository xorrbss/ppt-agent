"""Deterministic context construction for authored SlideSpec repairs.

The public executor keeps the historical ``_repair_context`` facade.  This
package owns the indexed browser/layout state and the bounded projection bands
that build its JSON-compatible result.
"""

from services.slidespec_repair_context.builder import build_repair_context
from services.slidespec_repair_context.numeric import off_canvas_fraction

__all__ = ["build_repair_context", "off_canvas_fraction"]
