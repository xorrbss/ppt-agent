"""Facade: moved to services.generation_core.assets.infographic_assets (generation-core C3)."""
import sys
from services.generation_core.assets import infographic_assets as _moved
sys.modules[__name__] = _moved
