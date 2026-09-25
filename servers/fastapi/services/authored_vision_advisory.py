"""Facade: moved to ``services/generation_core/quality/vision_advisory.py``; this path aliases that module."""
import sys

from services.generation_core.quality import vision_advisory as _moved

sys.modules[__name__] = _moved
