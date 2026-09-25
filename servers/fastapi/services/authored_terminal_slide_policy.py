"""Facade: moved to ``services/generation_core/quality/terminal_slide_policy.py``; this path aliases that module."""
import sys

from services.generation_core.quality import terminal_slide_policy as _moved

sys.modules[__name__] = _moved
