"""The publication boundary for SVG bytes and their measured receipt.

An ORM page and a field mapping (constructor or CAS update) use the same
setter. Validation precedes either write, so a rejected measurement cannot
partially mutate a page or weaken its caller's compare-and-swap predicate.
"""
from collections.abc import Mapping, MutableMapping
from copy import deepcopy

from services.svg_contract import source_digest
from utils.hashing import sha256_hex


def set_svg_page(target, *, svg_content: str, content: dict, quality: dict | None = None) -> None:
    """Write one page with matching quality, or an explicit unmeasured marker.

    Fresh renderer quality must measure the canonical SVG, using the
    renderer's materialized_svg argument for authorized image derivatives.
    Unmeasured quality has measurement_status='unmeasured' and no digest;
    the export evidence reader therefore reports evidence_missing.
    """
    updated = deepcopy(content)
    receipt = dict(updated.get("svgReceipt") or {})
    measured = quality if quality is not None else receipt.get("quality")
    if not isinstance(measured, Mapping):
        raise ValueError("svg_page_measurement_required")
    if measured.get("measurement_status") == "unmeasured":
        if "source_digest" in measured or measured.get("measurement_complete") is True:
            raise ValueError("svg_page_unmeasured_quality_invalid")
    elif measured.get("source_digest") != source_digest(svg_content):
        raise ValueError("svg_page_measurement_digest_mismatch")
    if quality is not None:
        receipt["quality"] = deepcopy(quality)
    receipt["source_digest"] = sha256_hex(svg_content.encode("utf-8"))
    updated["svgReceipt"] = receipt
    fields = {"svg_content": svg_content, "content": updated}
    if isinstance(target, MutableMapping):
        target.update(fields)
    else:
        target.svg_content = fields["svg_content"]
        target.content = fields["content"]
