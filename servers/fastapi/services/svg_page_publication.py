"""The publication boundary for SVG bytes and their measured receipt.

An ORM page and a field mapping (constructor or CAS update) use the same
setter. Validation precedes either write, so a rejected measurement cannot
partially mutate a page or weaken its caller's compare-and-swap predicate.
"""
from collections.abc import Mapping, MutableMapping
from copy import deepcopy

from services.svg_contract import source_digest
from utils.hashing import sha256_hex

#: Payload key of publication's page flag (``drivers/svg/quality.REVIEW_FLAG_KEY``).
REVIEW_FLAG_KEY = "review_flag"
#: Reason prefix of a design-review verdict (``drivers/svg/quality.DESIGN_REASON_PREFIX``).
DESIGN_REASON_PREFIX = "design:"

_DERIVE = object()


def _reasons(entry: Mapping) -> list[str]:
    raw = entry.get("reasons")
    reasons = raw if isinstance(raw, list) else [entry.get("reason")]
    return [reason for reason in dict.fromkeys(reasons) if isinstance(reason, str) and reason]


def without_design_reasons(entry):
    """``entry`` without its design-review verdict, or None when nothing else remains.

    A design verdict describes the bytes a review saw; every other reason on
    the entry (the source readback's ``source_visibility``) is kept as it was.
    """
    if not isinstance(entry, Mapping):
        return None
    reasons = _reasons(entry)
    kept = [reason for reason in reasons if not reason.startswith(DESIGN_REASON_PREFIX)]
    if not kept:
        return None
    if kept == reasons:
        return dict(entry)
    # The entry's own reason was the verdict; what remains was never a remake.
    return {**entry, "reason": kept[0], "reasons": kept, "remakeAttempted": False,
            "remakeOutcome": "not_attempted"}


def replace_design_verdict(entry, verdict):
    """``entry`` with its design-review verdict replaced by ``verdict`` (None: cleared)."""
    kept = without_design_reasons(entry)
    if verdict is None:
        return kept
    if kept is None:
        return dict(verdict)
    from services.generation_core.quality.flag_classification import merge_flag_reasons
    return merge_flag_reasons(verdict, *_reasons(kept))


def bytes_changed(receipt: Mapping, svg_content: str) -> bool:
    """Whether ``svg_content`` is not the page the receipt's evidence describes."""
    recorded = receipt.get("source_digest")
    return isinstance(recorded, str) and recorded != sha256_hex(svg_content.encode("utf-8"))


def current_review_flag(receipt: Mapping, svg_content: str):
    """The receipt's review flag as it applies to ``svg_content``.

    A design-review verdict is stale once the page's bytes change (a remake,
    a renumbering, an edit): it is dropped, never carried onto bytes nobody
    reviewed. Everything else on the flag stays.
    """
    flag = receipt.get(REVIEW_FLAG_KEY)
    if not isinstance(flag, Mapping):
        return None
    return without_design_reasons(flag) if bytes_changed(receipt, svg_content) else dict(flag)


#: Receipt entries that record the page position they were raised at.
_POSITIONED_KEYS = ("terminal", REVIEW_FLAG_KEY, "flag")


def reposition_flags(receipt, index: int):
    """``receipt`` with its flag entries naming position ``index``.

    A flag records the position its page had when it was raised, and
    classification joins a flag to its page by that position
    (``classify_page_outcomes``); a mutation that moves a page moves its flags
    with it. Returns ``receipt`` itself when nothing moved.
    """
    if not isinstance(receipt, Mapping) or not any(
            isinstance(receipt.get(key), Mapping) and receipt[key].get("index") != index
            for key in _POSITIONED_KEYS):
        return receipt
    return {**receipt, **{key: {**receipt[key], "index": index} for key in _POSITIONED_KEYS
                          if isinstance(receipt.get(key), Mapping)}}


def failure_placeholder_page(receipt: Mapping) -> bool:
    """A terminal failure placeholder: not the source page, it claims no source receipt."""
    from services.generation_core.quality.flag_classification import FAILURE_PLACEHOLDER_OUTCOME
    terminal = receipt.get("terminal") if isinstance(receipt, Mapping) else None
    return isinstance(terminal, Mapping) and terminal.get("remakeOutcome") == FAILURE_PLACEHOLDER_OUTCOME


def source_visibility_flagged(receipt: Mapping) -> bool:
    """Publication flagged the page because the browser source readback did not verify it."""
    from services.generation_core.quality.flag_classification import SOURCE_VISIBILITY_REASON
    if not isinstance(receipt, Mapping):
        return False
    return any(isinstance(entry, Mapping) and SOURCE_VISIBILITY_REASON in _reasons(entry)
               for entry in (receipt.get(REVIEW_FLAG_KEY), receipt.get("terminal"), receipt.get("flag")))


def quality_failure_flagged(receipt: Mapping) -> bool:
    """The page's measured quality failure publishes flagged for review, not refused."""
    from services.generation_core.quality.flag_classification import REVIEW_REQUIRED
    quality = (receipt.get("quality") if isinstance(receipt, Mapping) else None) or {}
    return quality.get("passed") is not True and quality.get("classification") == REVIEW_REQUIRED


def validate_readability_publication(policy, quality) -> None:
    """``validate_svg_readability_publication``, with a flagged page's outcome kept as a flag.

    A page whose measured quality failure publishes flagged
    (``quality_failure_flagged``) must still carry a readability measurement
    bound to exactly this policy -- that is integrity; whether it passed is the
    page's quality, which its flag already reports.
    """
    from services.document_canvas_generation import (
        validate_svg_readability_policy, validate_svg_readability_publication)
    if not quality_failure_flagged({"quality": quality}):
        validate_svg_readability_publication(policy, quality)
        return
    value = validate_svg_readability_policy(policy)
    if value is None:
        return
    measured = quality.get("physical_readability") if isinstance(quality, Mapping) else None
    if (not isinstance(measured, Mapping) or measured.get("policy_digest") != value["digest"]
            or measured.get("policy") != value):
        raise ValueError("svg_readability_publication_failed")


def _page_flag(terminal, review_flag):
    from services.generation_core.drivers.svg.quality import _page_flag as merged_page_flag
    return merged_page_flag({"terminal": terminal, REVIEW_FLAG_KEY: review_flag})


def _classified(*, svg_content, quality: Mapping, index: int, png_path, terminal, review_flag):
    from services.generation_core.quality.page_outcome import PageOutcome, classify_page_outcomes

    assessment = {**quality, "passed": quality.get("passed") is True
                  and quality.get("measurement_complete") is True}
    flag = _page_flag(terminal if isinstance(terminal, Mapping) else None, review_flag)
    return classify_page_outcomes([PageOutcome(
        index=index, source_format="svg", source=svg_content, png_path=png_path,
        quality=assessment, attempts=0,
        failures=tuple(item for item in quality.get("failures", []) if isinstance(item, Mapping)),
        flag={**flag, "index": index} if isinstance(flag, Mapping) else None,
    )])[0]


def _refresh_review_flag(receipt: dict, svg_content: str, quality, review_flag) -> None:
    """Keep the receipt's review flag, and the classified copy of it, on its bytes."""
    flag = current_review_flag(receipt, svg_content) if review_flag is _DERIVE else review_flag
    if flag == receipt.get(REVIEW_FLAG_KEY) and review_flag is _DERIVE:
        return
    if flag is None:
        receipt.pop(REVIEW_FLAG_KEY, None)
    else:
        receipt[REVIEW_FLAG_KEY] = deepcopy(flag)
    previous = receipt.get("flag")
    if isinstance(previous, Mapping) and isinstance(quality, Mapping):
        # ``flag`` is classification's record of the page's flag: re-derive it
        # from the same inputs classification reads, never keep the old one.
        index = previous.get("index") if type(previous.get("index")) is int else 0
        outcome = _classified(svg_content=svg_content, quality=quality, index=index, png_path=None,
                              terminal=receipt.get("terminal"), review_flag=flag)
        if outcome.flag is None:
            receipt.pop("flag", None)
        else:
            receipt["flag"] = dict(outcome.flag)


def set_svg_page(target, *, svg_content: str, content: dict, quality: dict | None = None,
                 png: bytes | None = None, review_flag=_DERIVE) -> None:
    """Write one page with matching quality, or an explicit unmeasured marker.

    Fresh renderer quality must measure the canonical SVG, using the
    renderer's materialized_svg argument for authorized image derivatives.
    Unmeasured quality has measurement_status='unmeasured' and no digest;
    the export evidence reader therefore reports evidence_missing.

    The page's review flag follows its bytes (``current_review_flag``) unless
    the caller passes the flag it classified the new bytes with.
    """
    updated = deepcopy(content)
    receipt = dict(updated.get("svgReceipt") or {})
    measured = quality if quality is not None else receipt.get("quality")
    if not isinstance(measured, Mapping):
        raise ValueError("svg_page_measurement_required")
    readability_policy = receipt.get("readability_policy")
    measured_policy = (measured.get("physical_readability") or {}).get("policy")
    if readability_policy is not None and measured_policy != readability_policy:
        raise ValueError("svg_readability_policy_mismatch")
    validate_readability_publication(readability_policy or measured_policy, measured)
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
    _refresh_review_flag(receipt, svg_content, measured, review_flag)
    receipt["source_digest"] = canonical_digest
    updated["svgReceipt"] = receipt
    fields = {"svg_content": svg_content, "content": updated}
    if isinstance(target, MutableMapping):
        target.update(fields)
    else:
        target.svg_content = fields["svg_content"]
        target.content = fields["content"]


def classify_svg_page_quality(*, svg_content: str, quality: Mapping, index: int,
                              png_path: str, receipt: Mapping, review_flag=_DERIVE) -> dict:
    """Classify a new measurement without clearing a terminal page's flag.

    The page's flag is what classification reads at publication: its
    ``terminal`` record and its review flag as it applies to ``svg_content``
    (``current_review_flag``, or the flag the caller is about to publish) --
    never the classified copy ``flag``, which describes the bytes it was
    classified on.
    """
    measured = deepcopy(dict(quality))
    flag = current_review_flag(receipt, svg_content) if review_flag is _DERIVE else review_flag
    outcome = _classified(svg_content=svg_content, quality=measured, index=index, png_path=png_path,
                          terminal=receipt.get("terminal"), review_flag=flag)
    measured.update(verified=outcome.verified, classification=outcome.classification)
    return measured


def svg_deck_quality_fields(slides, svg_meta: Mapping) -> dict:
    """``theme.svg``'s page-quality fields, rebuilt from the published pages.

    The one rule publication writes them with: ``reviewPages``/``qualityStatus``
    from ``review_page_indices`` over each page's recorded quality, and
    ``flaggedSlides``/``qualitySummary`` from the classification of each page's
    flag (each entry names its slide row).
    """
    from services.generation_core.drivers.svg.quality import page_outcome
    from services.generation_core.quality.page_outcome import (
        classify_page_outcomes, deck_quality_summary, review_page_indices)

    receipts = [((slide.content or {}).get("svgReceipt") or {}) for slide in slides]
    qualities = [receipt.get("quality") or {} for receipt in receipts]
    review = review_page_indices(qualities)
    outcomes = classify_page_outcomes([
        page_outcome(index, {**receipt, "svg_content": slide.svg_content})
        for index, (slide, receipt) in enumerate(zip(slides, receipts, strict=True))])
    summary = deck_quality_summary(outcomes)
    summary.pop("flaggedSlides")
    # A flag records the position its page had when it was raised; a reorder
    # or a deletion moves the page, so each entry names the page's position now.
    flagged = [{**dict(outcome.flag), "index": outcome.index,
                **({"slideId": str(slides[outcome.index].id)}
                   if getattr(slides[outcome.index], "id", None) is not None else {})}
               for outcome in outcomes if outcome.flag is not None]
    prior = svg_meta.get("qualitySummary") if isinstance(svg_meta.get("qualitySummary"), Mapping) else {}
    return dict(quality=deepcopy(qualities), reviewPages=review,
                qualityStatus="review_required" if review else "passed",
                qualitySummary={**deepcopy(dict(prior)), **summary}, flaggedSlides=flagged)
