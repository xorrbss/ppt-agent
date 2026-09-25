"""Facade: moved to services.generation_core.assets.illustration_prompt (generation-core C3)."""
import sys
from services.generation_core.assets import illustration_prompt as _moved
sys.modules[__name__] = _moved
