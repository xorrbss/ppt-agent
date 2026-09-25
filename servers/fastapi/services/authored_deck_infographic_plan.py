"""Facade: moved to services.generation_core.assets.infographic_plan (generation-core C3)."""
import sys
from services.generation_core.assets import infographic_plan as _moved
sys.modules[__name__] = _moved
