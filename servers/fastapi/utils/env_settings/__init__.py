"""Typed accessor clusters over ``utils.get_env`` for unregistered env knobs.

One module per cluster (``mem0``, ``public_service``, ...). Each module reads
only through ``utils.get_env`` so admin-managed values are honoured for
registered names, declares ``ENV_NAMES`` (every name it reads) for the
internal-knob ledger test, and imports nothing from ``services`` or ``api``.
"""
