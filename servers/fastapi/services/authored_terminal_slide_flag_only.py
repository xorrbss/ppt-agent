"""Facade: moved to ``services/generation_core/quality/terminal_slide_flag_only.py``; this path aliases that module."""
import sys

from services.generation_core.quality import terminal_slide_flag_only as _moved

sys.modules[__name__] = _moved
