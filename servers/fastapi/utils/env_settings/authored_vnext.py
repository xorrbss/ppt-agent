"""Feature flags and modes for the authored-vNext surfaces.

Layering: this module may import only stdlib, ``utils.get_env``,
``utils.env_parse`` and (type only) ``utils.runtime_settings``.  It never
imports ``services`` or ``api``.  Nothing is read at import time.

Boolean contract (one per flag, see each accessor's docstring):

* **opt-in** flags (default off) use :func:`utils.env_parse.is_truthy`:
  only ``1/true/yes/on`` (case-insensitive, stripped) enable; anything else,
  including unset, keeps the flag off.  Before 2026-09-29 most of these
  compared ``.lower() == "true"``, so ``1``/``yes``/``on`` now also enable.
* **default-on** flags use :func:`utils.get_env.env_bool` with
  ``default=True``: ``0/false/no/off`` disable, ``1/true/yes/on`` enable, and
  unrecognised text keeps the default.  Before 2026-09-29
  ``AUTHORED_VNEXT_MASTER_ENABLED`` compared ``== "true"``, so ``1``/``yes``
  and typos *disabled* the feature; they now enable / keep it enabled.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from utils.env_parse import is_truthy
from utils.get_env import env_bool, get_setting_env

if TYPE_CHECKING:
    from utils.runtime_settings import RuntimeSettings

ENV_NAMES: tuple[str, ...] = (
    "AUTHORED_VNEXT_MASTER_ENABLED",
    "AUTHORED_VNEXT_REPOSITORY_MODE",
    "AUTHORED_VNEXT_DEFAULT_COMPANY_STANDARD_ID",
    "DECK_IR_ALLOW_DEV_FIXTURE",
    "DECK_IR_GENERATION_ENABLED",
    "AUTHORED_RETAINED_REFERENCE_ROOT",
)

DEFAULT_REPOSITORY_MODE = "sql"
DEFAULT_COMPANY_STANDARD_ID = "master:ibiz-proposal-blue"


def _opt_in_env(name: str, settings: RuntimeSettings | None) -> bool:
    return is_truthy(get_setting_env(name, settings))


# --- opt-in flags (default off; only 1/true/yes/on enable) -----------------


def is_deck_ir_dev_fixture_allowed(settings: RuntimeSettings | None = None) -> bool:
    """Opt-in: permits the deterministic dev-fixture Deck Director (unchanged contract)."""
    return _opt_in_env("DECK_IR_ALLOW_DEV_FIXTURE", settings)


def is_deck_ir_generation_enabled(settings: RuntimeSettings | None = None) -> bool:
    """Opt-in admission switch for Deck IR routing; default is legacy authored (unchanged)."""
    return _opt_in_env("DECK_IR_GENERATION_ENABLED", settings)


# --- default-on flags (0/false/no/off disable) ------------------------------


def is_authored_vnext_master_enabled(settings: RuntimeSettings | None = None) -> bool:
    """Default on: DeckMasterContract router and company-standard pins."""
    return env_bool("AUTHORED_VNEXT_MASTER_ENABLED", True, settings=settings)


# --- modes and identifiers --------------------------------------------------


def get_authored_vnext_repository_mode(
    settings: RuntimeSettings | None = None,
) -> str:
    """``sql`` (default) or ``file``; stripped and lower-cased, blank -> default."""
    value = (get_setting_env("AUTHORED_VNEXT_REPOSITORY_MODE", settings) or "").strip().lower()
    return value or DEFAULT_REPOSITORY_MODE


def get_authored_vnext_default_company_standard_id(
    settings: RuntimeSettings | None = None,
) -> str:
    """Master id auto-applied on first use; unset -> the bundled default, blank -> ``""``."""
    value = get_setting_env("AUTHORED_VNEXT_DEFAULT_COMPANY_STANDARD_ID", settings)
    if value is None:
        return DEFAULT_COMPANY_STANDARD_ID
    return value.strip()


def get_authored_retained_reference_root(
    settings: RuntimeSettings | None = None,
) -> str | None:
    """Directory holding retained reference PPTX skills; ``None`` when unset."""
    return get_setting_env("AUTHORED_RETAINED_REFERENCE_ROOT", settings)
