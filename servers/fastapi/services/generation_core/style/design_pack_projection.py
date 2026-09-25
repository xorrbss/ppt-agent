"""Whether a design pack's rules can project into an svg template snapshot.

A design pack (``services/authored_vnext/design_pack/models.py``) is not a
brand-token catalogue; every ``DesignFrame`` is defined as an executable patch
onto one already-existing document part -- ``EditableSlot.patch_handle`` names
a concrete ``pptx-shape``, ``authored-qid`` or ``scene-element`` selector into
that frame's own ``source_part``, and schema v2 requires every source-native
slot to carry one (``design_pack_source_native_slots_missing`` /
``design_pack_slot_not_executable`` in ``DesignPackStructure``). SVG's
per-slide authoring has no such source document to patch against outside its
own separate source-restyle/raster-reconstruction admission path (already
served by ``utils.svg_template_rules``, unrelated to design packs). That is a
structural property of every design pack the current schema can express, not
a per-instance gap, so this check needs no repository lookup: it names the
missing capability instead of returning a blanket "not supported".
"""
from __future__ import annotations


class DesignPackProjectionError(ValueError):
    """A design pack's rules cannot project into an svg template snapshot."""


def assert_design_pack_projects() -> None:
    """Raise with a specific, structural reason. Never returns a rule set today.

    Kept as a function (not a module-level constant) so a future design-pack
    schema revision that adds a geometry-only, patch-free frame kind has one
    place to add the corresponding accept path.
    """

    raise DesignPackProjectionError("svg_design_pack_requires_source_native_patch_target")


__all__ = ["DesignPackProjectionError", "assert_design_pack_projects"]
