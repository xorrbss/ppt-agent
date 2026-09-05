"""Semantic slot names for the structured-output call loop.

The slot is what gives a provider call its durable identity: it is hashed into
the logical call ordinal that ``services/public_operation_admission.py``
reserves against, so two calls sharing a slot inside one usage scope are the
same logical call. Every repair round of the structured loop therefore gets
its own slot, and a caller that deliberately runs the same stage twice passes
a suffix so its second run does not reopen the first one's row.

That last case is not hypothetical. The Stage A content brief retries once
after a timeout, and the first attempt leaves a terminal row behind. Without a
suffix the retry is refused with ``public_provider_terminal_replay_unsupported``
for a provider that cannot replay, which turns a brief timeout into a failed
deck instead of the documented fallback to the raw source.
"""

from __future__ import annotations

INITIAL = "structured.initial"
PARSE_REPAIR = "structured.parse-repair"
SCHEMA_REPAIR = "structured.schema-repair"
SCHEMA_PARSE_REPAIR = "structured.schema-parse-repair"


def structured_semantic_slot(
    *, validation_attempt: int, parse_attempt: int, suffix: str = ""
) -> str:
    """Name the slot for one round of the structured loop.

    ``validation_attempt`` counts JSON-schema validation feedback loops and
    ``parse_attempt`` counts parse retries inside one of them; the first round
    of both is the initial call. ``suffix`` is appended verbatim and is empty
    for every caller that runs the stage once.
    """

    if validation_attempt == 0:
        base = INITIAL if parse_attempt == 0 else PARSE_REPAIR
    else:
        base = SCHEMA_REPAIR if parse_attempt == 0 else SCHEMA_PARSE_REPAIR
    return f"{base}{suffix}"
