"""Bounded source-content preservation claim sealed into quality receipts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from services.authored_vnext.master.digests import canonical_digest


@dataclass(frozen=True)
class SourceContentPreservationInventory:
    """Bounded preservation claim for the ``text-image`` restyle mode.

    This mode proves verbatim text and each image *occurrence*.  It deliberately
    does not claim that source tables, charts, or original geometry survived a
    template restyle.  A future structural-preservation capability must add its
    own evidence and contract rather than changing this claim in place.
    """

    source_mode: str
    source_complete_claim: bool
    preserved: tuple[str, ...]
    excluded: tuple[str, ...]
    excluded_source_object_counts: dict[str, int]
    text_occurrences: tuple[tuple[str, str], ...]
    image_occurrences: tuple[tuple[str, str], ...]

    def as_payload(self) -> dict[str, Any]:
        """Return the exact bounded source-content claim sealed for publication."""

        return {
            "sourceMode": self.source_mode,
            "sourceCompleteClaim": self.source_complete_claim,
            "preserved": list(self.preserved),
            "excluded": list(self.excluded),
            "excludedSourceObjectCounts": self.excluded_source_object_counts,
            "textOccurrences": [
                {"sourceBlockId": block_id, "contentDigest": content_digest}
                for block_id, content_digest in self.text_occurrences
            ],
            "imageOccurrences": [
                {"sourceBlockId": block_id, "contentDigest": content_digest}
                for block_id, content_digest in self.image_occurrences
            ],
        }

    @property
    def occurrence_inventory_digest(self) -> str:
        return canonical_digest(
            {
                "schemaVersion": "source-content-text-image-inventory/v1",
                **self.as_payload(),
            }
        )

    @property
    def text_occurrence_count(self) -> int:
        return len(self.text_occurrences)

    @property
    def image_occurrence_count(self) -> int:
        return len(self.image_occurrences)


__all__ = ["SourceContentPreservationInventory"]
