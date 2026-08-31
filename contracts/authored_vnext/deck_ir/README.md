# Deck IR v1 contracts

This directory contains the JSON Schemas generated from the strict runtime
models in `servers/fastapi/services/authored_vnext/deck_ir/contracts.py`.
Unknown fields are rejected at every object boundary. The canonical fixture is
sealed with `canonical-json-unicode-nfc-f64-v1`.

Regenerate with `python contracts/authored_vnext/deck_ir/generate.py` from the
repository root. Generated schemas are reviewable product contracts; runtime
validation remains authoritative.
