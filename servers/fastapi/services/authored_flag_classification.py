"""Facade: moved to ``services/generation_core/quality/flag_classification.py``; this path aliases that module."""
import sys

from services.generation_core.quality import flag_classification as _moved

sys.modules[__name__] = _moved
