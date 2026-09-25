"""Shared generation core: style, assets, quality and runtime modules used by
both the svg and (eventually) authored drivers.

See docs/generation-core-architecture-20260925.md for the target shape. A
module lives here only once both drivers call it; until then it stays behind
a facade at its historical import path.
"""
