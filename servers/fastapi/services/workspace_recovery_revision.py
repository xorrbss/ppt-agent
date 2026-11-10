"""Recovery revisions a workspace generation operation may request.

Moved unchanged from ``authored_composition_policy`` (which re-exports it) so
the workspace timeline projection reads it without importing that policy.
"""

from __future__ import annotations

AUTHORED_CAPACITY_BOUNDED_REPAIR_REVISION = "authored-capacity-bounded-repair/v1"
