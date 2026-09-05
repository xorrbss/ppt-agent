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

#: The default prefix. A caller that adds a SECOND structured call to a stage
#: that already has one passes its own instead: two different call sites inside
#: one usage scope are two logical calls, and sharing the default would make
#: the later one look like a replay of the earlier.
DEFAULT_PREFIX = "structured"

#: The rounds of the loop, in the order they are reached.
INITIAL_ROUND = "initial"
PARSE_REPAIR_ROUND = "parse-repair"
SCHEMA_REPAIR_ROUND = "schema-repair"
SCHEMA_PARSE_REPAIR_ROUND = "schema-parse-repair"

INITIAL = f"{DEFAULT_PREFIX}.{INITIAL_ROUND}"
PARSE_REPAIR = f"{DEFAULT_PREFIX}.{PARSE_REPAIR_ROUND}"
SCHEMA_REPAIR = f"{DEFAULT_PREFIX}.{SCHEMA_REPAIR_ROUND}"
SCHEMA_PARSE_REPAIR = f"{DEFAULT_PREFIX}.{SCHEMA_PARSE_REPAIR_ROUND}"


def structured_semantic_slot(
    *,
    validation_attempt: int,
    parse_attempt: int,
    suffix: str = "",
    prefix: str = DEFAULT_PREFIX,
) -> str:
    """Name the slot for one round of the structured loop.

    ``validation_attempt`` counts JSON-schema validation feedback loops and
    ``parse_attempt`` counts parse retries inside one of them; the first round
    of both is the initial call. ``suffix`` is appended verbatim and is empty
    for every caller that runs the stage once. ``prefix`` names the CALL SITE
    and is the default for every caller that is the only structured call in
    its stage.
    """

    if validation_attempt == 0:
        round_name = INITIAL_ROUND if parse_attempt == 0 else PARSE_REPAIR_ROUND
    else:
        round_name = (
            SCHEMA_REPAIR_ROUND if parse_attempt == 0 else SCHEMA_PARSE_REPAIR_ROUND
        )
    return f"{prefix}.{round_name}{suffix}"
