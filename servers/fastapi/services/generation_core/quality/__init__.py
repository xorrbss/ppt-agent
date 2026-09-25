"""Format-neutral page quality shared by the SVG and HTML (authored) drivers.

Flag classification, the contact-sheet vision advisory and the terminal-slide
policy moved here from ``services/authored_*`` once the SVG driver became their
second caller; each old path is an aliasing facade. ``page_outcome`` and
``terminal_policy`` are the two seams the SVG driver reports through. See
``docs/generation-core-architecture-20260925.md`` section 2.1.
"""
