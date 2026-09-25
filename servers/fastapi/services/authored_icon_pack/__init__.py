"""Facade: moved to services.generation_core.assets.icon_pack (generation-core C3).

Pre-registers every submodule under the old package name before aliasing the
package itself, so ``import services.authored_icon_pack.provider`` resolves
to the SAME module object as ``services.generation_core.assets.icon_pack.provider``
rather than importing it a second time (which would split monkeypatches and
module-level locks/caches between two live copies).
"""
import importlib
import sys

from services.generation_core.assets import icon_pack as _moved

_SUBMODULES = (
    "checks",
    "inline",
    "library",
    "provider",
    "scene",
    "service",
    "sheet",
    "store",
)

for _name in _SUBMODULES:
    sys.modules[f"{__name__}.{_name}"] = importlib.import_module(
        f"services.generation_core.assets.icon_pack.{_name}"
    )

sys.modules[__name__] = _moved
