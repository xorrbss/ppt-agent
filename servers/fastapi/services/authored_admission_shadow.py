"""Bounded same-input admission parity, without source text or attempt tokens."""

from __future__ import annotations

from services.authored_admission_policy import PRODUCER_INVALID, UNCODED_ERROR

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
import hashlib
import json
import logging
import re

from services.authored_admission_policy import PRODUCERS


LOGGER = logging.getLogger(__name__)
SHADOW_PREFIX = "authored_admission_shadow "
_CODE = re.compile(r"[a-z][a-z0-9_]{0,127}\Z")
_CLASS_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.]{0,63}\Z")
#: Source block ids are structured, content-addressed identifiers
#: (``source-block:<unit>:<order>:<digest>``, optionally ``:run:<a>-<b>``), so
#: this charset admits an id and nothing that reads as source text.
_BLOCK_ID = re.compile(r"[A-Za-z0-9_.:\-]{1,96}\Z")
#: How many ids one row may name. A rejection is diagnosable from the first
#: few nodes; the row stays a bounded ledger entry rather than a slide dump.
MAX_BLOCK_ID_SAMPLE = 4


def bounded_block_ids(values: Sequence[str] | None) -> tuple[str, ...]:
    """A bounded, content-free sample of the block ids a verdict named.

    ``blockCount`` alone said how many DOM nodes a rejection was about but
    never which, so a production rejection could not be traced to a node from
    the logs -- the ids live only on the receipt, which needs the deck's
    database row to read. A short sample travels with the row instead.

    An id that is not shaped like one is replaced by a digest: it is still a
    stable handle for "the same unnamed block again", and it cannot carry text.
    """

    sample: list[str] = []
    for value in list(values or ())[:MAX_BLOCK_ID_SAMPLE]:
        text = str(value)
        sample.append(
            text
            if _BLOCK_ID.fullmatch(text)
            else hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]
        )
    return tuple(sample)


def bounded_code(value: object) -> str:
    return value if isinstance(value, str) and _CODE.fullmatch(value) else UNCODED_ERROR


def bounded_error(error: BaseException | None) -> tuple[str, str]:
    """The exception's class name and a digest of its message, never its text.

    An ``authored_admission_internal_error`` row used to name no exception at
    all, so the only way to learn what escaped the gate was to reproduce the
    deck.  The class is a bounded identifier and the digest is content-free, so
    both can travel in a shadow line and in the deck receipt: two occurrences of
    the same defect share a digest, and a new one is visibly new.
    """

    if error is None:
        return "", ""
    name = type(error).__qualname__
    payload = f"{type(error).__module__}.{name}:{error}".encode("utf-8", "replace")
    return (
        name if _CLASS_NAME.fullmatch(name) else UNCODED_ERROR,
        hashlib.sha256(payload).hexdigest()[:16],
    )


@dataclass
class AdmissionShadow:
    #: Every row now names its predicate and block count as well as its two
    #: verdicts, so one deck holds more distinct rows than the five-field row
    #: did.  The ledger is a bounded counter either way; this keeps a real
    #: series from spending its budget before the rare rows appear.
    max_rows: int = 256
    pairs: Counter = field(default_factory=Counter)
    dropped: int = 0

    def record(
        self,
        *,
        producer: str,
        existing_verdict: str,
        existing_code: str,
        gate_verdict: str,
        gate_code: str,
        predicate: str = "",
        block_count: int = 0,
        block_ids: Sequence[str] | None = None,
        error: BaseException | None = None,
    ) -> None:
        if producer not in PRODUCERS:
            raise ValueError(PRODUCER_INVALID)
        error_name, error_digest = bounded_error(error)
        sample = bounded_block_ids(block_ids)
        row = {
            "producer": producer,
            "existingVerdict": existing_verdict,
            "existingCode": bounded_code(existing_code) if existing_code else "",
            "gateVerdict": gate_verdict,
            "gateCode": bounded_code(gate_code) if gate_code else "",
            # Which step of the ordered admission decided the verdict, and how
            # many source blocks it names.  Both are already on the receipt
            # (``AdmissionReceipt.predicate``/``block_ids``) and both are
            # content-free, so a verdict difference can be attributed from the
            # log alone instead of by reading the code and counting adjacency.
            "predicate": bounded_code(predicate) if predicate else "",
            "blockCount": max(0, int(block_count or 0)),
            # Named only when a verdict actually carried ids: an admitted row
            # has none, so the ledger's row cardinality is still driven by the
            # verdicts and not by one distinct row per slide.
            "blockIds": sample,
            "gateError": error_name,
            "gateErrorDigest": error_digest,
        }
        key = tuple(row.items())
        if key in self.pairs or len(self.pairs) < self.max_rows:
            self.pairs[key] += 1
        else:
            self.dropped += 1
        LOGGER.info("%s%s", SHADOW_PREFIX, json.dumps(row, sort_keys=True, separators=(",", ":")))

    def as_dict(self) -> dict:
        return {
            "schemaVersion": 1,
            "rows": [dict(key, count=count) for key, count in self.pairs.items()],
            "dropped": self.dropped,
            "count": sum(self.pairs.values()) + self.dropped,
        }
