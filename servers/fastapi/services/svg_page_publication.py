"""The publication boundary for SVG bytes and their measured receipt.

An ORM page and a field mapping (constructor or CAS update) use the same
setter. Validation precedes either write, so a rejected measurement cannot
partially mutate a page or weaken its caller's compare-and-swap predicate.
"""
from collections.abc import Mapping, MutableMapping
from copy import deepcopy

from services.svg_contract import source_digest
from utils.hashing import sha256_hex


def set_svg_page(target, *, svg_content: str, content: dict, quality: dict | None = None,
                 png: bytes | None = None) -> None:
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
    from services.document_canvas_generation import validate_svg_readability_publication

    readability_policy = receipt.get("readability_policy")
    measured_policy = (measured.get("physical_readability") or {}).get("policy")
    if readability_policy is not None and measured_policy != readability_policy:
        raise ValueError("svg_readability_policy_mismatch")
    validate_svg_readability_publication(readability_policy or measured_policy, measured)
    if measured.get("measurement_status") == "unmeasured":
        if "source_digest" in measured or measured.get("measurement_complete") is True:
            raise ValueError("svg_page_unmeasured_quality_invalid")
    elif measured.get("source_digest") != source_digest(svg_content):
        raise ValueError("svg_page_measurement_digest_mismatch")
    png_digest = sha256_hex(png) if png is not None else None
    if (png_digest is not None and measured.get("png_digest") is not None
            and measured["png_digest"] != "sha256:" + png_digest):
        raise ValueError("svg_page_measurement_png_digest_mismatch")
    canonical_digest = sha256_hex(svg_content.encode("utf-8"))
    capture_matches = png_digest is not None and receipt.get("png_sha256") == png_digest
    if ((receipt.get("source_digest") != canonical_digest and not capture_matches)
            or png_digest is not None and not capture_matches):
        # A previous capture cannot describe newly published bytes. Callers
        # may attach a fresh checkpoint after this validation boundary.
        receipt.pop("png_sha256", None)
        receipt.pop("png_path", None)
    if png_digest is not None:
        receipt["png_sha256"] = png_digest
    if quality is not None:
        receipt["quality"] = deepcopy(quality)
    receipt["source_digest"] = canonical_digest
    updated["svgReceipt"] = receipt
    fields = {"svg_content": svg_content, "content": updated}
    if isinstance(target, MutableMapping):
        target.update(fields)
    else:
        target.svg_content = fields["svg_content"]
        target.content = fields["content"]


def classify_svg_page_quality(*, svg_content: str, quality: Mapping, index: int,
                              png_path: str, receipt: Mapping) -> dict:
    """Classify a new measurement without clearing a terminal page's flag."""
    from services.generation_core.quality.page_outcome import PageOutcome, classify_page_outcomes

    measured = deepcopy(dict(quality))
    assessment = {**measured, "passed": measured.get("passed") is True
                  and measured.get("measurement_complete") is True}
    terminal = receipt.get("terminal")
    flag = terminal if isinstance(terminal, Mapping) else receipt.get("flag")
    outcome = classify_page_outcomes([PageOutcome(
        index=index, source_format="svg", source=svg_content, png_path=png_path,
        quality=assessment, attempts=0,
        failures=tuple(item for item in measured.get("failures", []) if isinstance(item, Mapping)),
        flag={**flag, "index": index} if isinstance(flag, Mapping) else None,
    )])[0]
    measured.update(verified=outcome.verified, classification=outcome.classification)
    return measured
