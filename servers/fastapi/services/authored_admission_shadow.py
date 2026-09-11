"""Bounded same-input admission parity, without source text or attempt tokens."""

from __future__ import annotations

from services.authored_admission_policy import PRODUCER_INVALID, UNCODED_ERROR

from collections import Counter
from dataclasses import dataclass, field
import json
import logging
import re

from services.authored_admission_policy import PRODUCERS


LOGGER = logging.getLogger(__name__)
SHADOW_PREFIX = "authored_admission_shadow "
_CODE = re.compile(r"[a-z][a-z0-9_]{0,127}\Z")


def bounded_code(value: object) -> str:
    return value if isinstance(value, str) and _CODE.fullmatch(value) else UNCODED_ERROR


@dataclass
class AdmissionShadow:
    max_rows: int = 64
    pairs: Counter = field(default_factory=Counter)
    dropped: int = 0

    def record(
        self, *, producer: str, existing_verdict: str, existing_code: str, gate_verdict: str, gate_code: str
    ) -> None:
        if producer not in PRODUCERS:
            raise ValueError(PRODUCER_INVALID)
        row = {
            "producer": producer,
            "existingVerdict": existing_verdict,
            "existingCode": bounded_code(existing_code) if existing_code else "",
            "gateVerdict": gate_verdict,
            "gateCode": bounded_code(gate_code) if gate_code else "",
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
