"""A template selection stores its heavy material by reference.

A ``template-redesign`` selection used to carry the whole template package and
its canvas adaptations inline (15 MB for a real 101-page proposal deck: font
bytes, preserved decoration images, frames). It was copied into the job
payload, the job's style snapshot, the workspace operation and the
presentation theme, and on SQLite every heartbeat rewrote those rows; runs
failed with ``database is locked``
(docs/svg-template-real-deck-registration-design-20261103.md, sections 7-9).

A v2 selection keeps the immutable identity instead: template pin, version id,
canvas, frame map, the small style projection and ``material_key``, a digest of
the version and of every adaptation digest. The package and adaptations are
loaded once per process by :func:`ensure_selection_material` (owner-scoped,
digest-verified by the template loader) and read synchronously through
:func:`template_package` / :func:`template_adaptations`. A v2 selection whose
material is not loaded fails loudly; it is never read as empty.

v1 selections (inline material, decks admitted before v2) stay valid and are
read as before.
"""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from threading import Lock

from utils.hashing import canonical_json_digest

SELECTION_SCHEMA_V1 = "svg-template-selection/v1"
SELECTION_SCHEMA_V2 = "svg-template-selection/v2"
SELECTION_SCHEMAS = frozenset({SELECTION_SCHEMA_V1, SELECTION_SCHEMA_V2})
#: Loaded materials per process; a material is one template version at one canvas.
_CACHE_LIMIT = 8
_cache: OrderedDict[str, dict] = OrderedDict()
_lock = Lock()


def material_key(selection: dict) -> str:
    """Identity of a full selection's material: the version and its adaptation digests."""
    package = selection.get("package") or {}
    adaptations = selection.get("adaptations") or {}
    return canonical_json_digest({
        "version_id": selection.get("version_id"),
        "package_digest": package.get("digest"),
        "adaptations": {frame_id: (adaptation or {}).get("digest")
                        for frame_id, adaptation in sorted(adaptations.items())},
    })


def _remember(key: str, package: dict, adaptations: dict) -> None:
    with _lock:
        _cache[key] = {"package": package, "adaptations": adaptations}
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_LIMIT:
            _cache.popitem(last=False)


def compact_selection(full: dict) -> dict:
    """The v2 form of a freshly resolved (loader-verified) full selection.

    The material is remembered for this process, so the selection is usable
    at once. ``digest`` is recomputed over the compact form.
    """
    key = material_key(full)
    _remember(key, deepcopy(full["package"]), deepcopy(full["adaptations"]))
    compact = {name: deepcopy(value) for name, value in full.items()
               if name not in {"package", "adaptations", "digest", "schema_version"}}
    compact.update(schema_version=SELECTION_SCHEMA_V2, material_key=key)
    compact["digest"] = canonical_json_digest(compact)
    return compact


def _material(selection: dict) -> dict:
    if selection.get("schema_version") == SELECTION_SCHEMA_V1:
        return selection
    with _lock:
        material = _cache.get(selection.get("material_key"))
        if material is not None:
            _cache.move_to_end(selection["material_key"])
    if material is None:
        raise ValueError("svg_template_material_not_loaded")
    return material


def template_package(selection: dict) -> dict:
    return _material(selection)["package"]


def template_adaptations(selection: dict) -> dict:
    return _material(selection)["adaptations"]


def material_loaded(selection: dict) -> bool:
    try:
        _material(selection)
    except ValueError:
        return False
    return True


async def ensure_selection_material(session, selection: dict | None) -> None:
    """Load a v2 selection's material for this process (owner-scoped, verified).

    Called at every asynchronous entry point that reads a frozen template
    snapshot (generation admission and replay, export, publication, page
    remake, header operations). The archived pin is still honoured, as it is
    for generation recovery.
    """
    if not isinstance(selection, dict) or selection.get("schema_version") != SELECTION_SCHEMA_V2:
        return
    if material_loaded(selection):
        return
    from services.svg_templates.catalog import resolve_template_selection

    current = await resolve_template_selection(session, pin=selection["template_pin"], mode=selection["mode"],
        canvas=selection["canvas"], allow_archived=True)
    if (current.get("version_id") != selection.get("version_id")
            or material_key(current) != selection.get("material_key")):
        raise ValueError("svg_template_material_mismatch")
    _remember(selection["material_key"], current["package"], current["adaptations"])


async def ensure_snapshot_material(session, snapshot: dict | None) -> None:
    await ensure_selection_material(session, (snapshot or {}).get("svg_template"))


def clear_material_cache() -> None:
    """Tests only."""
    with _lock:
        _cache.clear()
