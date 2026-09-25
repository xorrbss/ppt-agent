"""Facade: moved to ``services/generation_core/quality/vision_contact_sheet.py``; this path aliases that module."""
import sys

from services.generation_core.quality import vision_contact_sheet as _moved

sys.modules[__name__] = _moved
