"""Stable bounds and priority policy for repair-context construction."""

DEFECT_PRIORITY = {
    ("high", "dom.clipping"): 0,
    ("high", "dom.overflow"): 1,
    ("high", "dom.unintended_overlap"): 2,
    ("high", "dom.off_canvas"): 3,
    ("medium", "dom.unintended_overlap"): 4,
    ("medium", "dom.clipping"): 5,
    ("medium", "dom.overflow"): 6,
    ("medium", "dom.off_canvas"): 7,
    ("high", "constraint.containment.violated"): 0,
    ("high", "constraint.readability.violated"): 1,
}

TEXT_KINDS = frozenset({"title", "text", "list", "metric", "table"})

MAX_OPERATIONS = 8
MAX_AFFECTED_BLOCKS = 4
MAX_GEOMETRY_COMPOSITIONS = 4
