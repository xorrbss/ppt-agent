"""Bounded receipts for authored HTML tree normalization."""

from __future__ import annotations

from collections import OrderedDict

MAX_RECEIPT_DETAIL_CHARS = 120
MAX_RECEIPT_ENTRIES = 64


def _bounded_detail(detail: object) -> str:
    return str(detail).replace("\x00", "\\0")[:MAX_RECEIPT_DETAIL_CHARS]


class ReceiptCollector:
    """Aggregate a finite rule/detail vocabulary into bounded receipt entries."""

    def __init__(self) -> None:
        self._counts: OrderedDict[tuple[str, str], int] = OrderedDict()
        self._overflow_count = 0

    def add(self, rule: str, count: int = 1, detail: object = "") -> None:
        if count <= 0:
            return
        key = (_bounded_detail(rule), _bounded_detail(detail))
        if key in self._counts:
            self._counts[key] += count
        elif len(self._counts) < MAX_RECEIPT_ENTRIES - 1:
            self._counts[key] = count
        else:
            self._overflow_count += count

    def extend_legacy(self, records: list[dict]) -> None:
        """Convert existing CSS records to the common rule/count/detail shape."""

        for record in records:
            parts = []
            if record.get("detail"):
                parts.append(str(record["detail"]))
            if record.get("property"):
                parts.append(str(record["property"]))
            if record.get("value_class"):
                parts.append(str(record["value_class"]))
            if record.get("at_rule"):
                parts.append(str(record["at_rule"]))
            self.add(
                str(record.get("rule", "normalization")),
                int(record.get("count", 0)),
                ":".join(parts),
            )

    def entries(self) -> list[dict[str, str | int]]:
        entries = [
            {"rule": rule, "count": count, "detail": detail}
            for (rule, detail), count in self._counts.items()
        ]
        if self._overflow_count:
            entries.append(
                {
                    "rule": "normalization_receipts_truncated",
                    "count": self._overflow_count,
                    "detail": f"max_entries={MAX_RECEIPT_ENTRIES}",
                }
            )
        return entries


__all__ = [
    "MAX_RECEIPT_DETAIL_CHARS",
    "MAX_RECEIPT_ENTRIES",
    "ReceiptCollector",
]
