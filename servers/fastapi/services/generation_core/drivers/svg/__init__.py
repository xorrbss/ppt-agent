"""SVG driver: author (``author.py``) -> render (``render.py``) per page,
orchestrated by the deck-level loop in ``collect.py``, publication in
``publish.py``. Split out of ``services/svg_presentation_generate.py`` and
``services/svg_presentation_publish.py`` (P0 split, see
``docs/generation-core-architecture-20260925.md``).

Deliberately no re-exports here: ``collect.py`` imports its sibling
``author``/``render`` submodules through this package, so importing
``collect`` here too would be a circular import. Import
``services.generation_core.drivers.svg.collect._generate`` (or the
``services.svg_presentation_generate`` facade) and
``services.generation_core.drivers.svg.publish._publish`` (or the
``services.svg_presentation_publish`` facade) directly.
"""
from __future__ import annotations
