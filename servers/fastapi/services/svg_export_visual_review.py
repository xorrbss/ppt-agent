"""The export receipt's ``visual_review`` domain for an SVG deck.

Filled from what publication recorded: the deck's contact-sheet vision
advisory (``theme.svg.advisory``, see
``services/generation_core/quality/advisory_record.py``) and each page's
classification in ``svgReceipt.quality``. The public contract
(``models.public_v2_contracts.VisualReviewQualityV3``) has no ``flagged`` status and
forbids extra evidence keys, so the agreed field shape maps onto it as:

* ``passed``      -- the advisory ran on exactly the pages being exported and no
  page is classified for review; evidence carries
  ``measurements.advisoryDigest``;
* ``failed`` + ``svg_visual_review_flagged`` -- same evidence, but a page is
  classified for review (the "flagged" outcome);
* ``failed`` + ``svg_visual_advisory_stale`` -- the advisory reviewed other
  page bytes (a page changed after publication) or its record is corrupt;
* ``failed`` + ``svg_visual_advisory_unavailable`` -- the advisory did not run
  (or was skipped) for this deck.

"Flagged" means exactly what publication reported as flagged: the pages
``review_page_indices`` puts in ``theme.svg.reviewPages`` (generation's
``quality_pages.flagged``). The advisory's own findings are recorded as
``measurements.advisoryFlaggedPages`` but never flag the deck: the advisory is
advisory by contract (``generation_core/quality/vision_advisory.py``, D22 in
``docs/authored-quality-speed-programme-design-20260905.md``: "never a gate"),
and counting it here made a deck generation reported as 0 flagged pages fail
export review as flagged. What the advisory contributes to release is proof
that a visual review ran on exactly these bytes.

Visual review is required for an SVG release whatever happened upstream, so
a missing advisory is ``failed``, not the contract's ``unavailable``: that
status must be ``required: false``, and ``release_readiness`` would then read
a deck nobody looked at as releasable.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping

from services.generation_core.quality.advisory_record import (
    ADVISORY_RECORD_SCHEMA,
    advisory_flagged_pages,
    advisory_ran,
    advisory_record,
    content_digest,
    record_matches,
)
from services.generation_core.quality.page_outcome import review_page_indices
from utils.hashing import sha256_hex
from services.svg_contract import source_digest

PAGE_ADVISORY_SCHEMA = "svg-page-vision-advisory/v1"


def _receipt(slide):
    return (slide.content or {}).get("svgReceipt") or {}


def page_measurement_current(slide) -> bool:
    """Bind the completed measurement to both the saved SVG and render."""
    receipt = _receipt(slide)
    quality = receipt.get("quality") or {}
    png = receipt.get("png_sha256")
    return (isinstance(png, str) and len(png) == 64
            and all(c in "0123456789abcdef" for c in png)
            and quality.get("measurement_complete") is True
            and quality.get("measurement_status") != "unmeasured"
            and quality.get("source_digest") == source_digest(slide.svg_content or "")
            and quality.get("png_digest") == "sha256:" + png)


def page_advisory_record(vision: Mapping, *, page_index: int, page_digest: str, png_digest: str) -> dict:
    """One independently sealed advisory answer for exactly one saved render."""
    body = dict(schemaVersion=PAGE_ADVISORY_SCHEMA, index=page_index,
                pageDigest=page_digest, pngDigest=png_digest, vision=dict(vision))
    return {**body, "digest": content_digest(body)}


def split_page_advisories(vision: Mapping, slides, indices) -> list[dict]:
    """Map contact-sheet local positions back onto durable deck indices."""
    if not advisory_ran({"vision": vision}):
        return []
    records = []
    for local, index in enumerate(indices):
        findings = [{**entry, "index": index} for entry in vision.get("slides", [])
                    if isinstance(entry, Mapping) and entry.get("index") == local]
        answer = {key: value for key, value in vision.items() if key != "slides"}
        if "designGate" in answer:
            answer["designGateDigest"] = answer["designGate"]["digest"]
            for key in ("designGate", "designReview", "reviewGroups"):
                answer.pop(key, None)
        answer.update(slideCount=1, slides=findings)
        if "reviewedPageIndices" in vision:
            answer["reviewedPageIndices"] = [index]
        if "reviewedPngDigests" in vision:
            answer["reviewedPngDigests"] = [vision["reviewedPngDigests"][local]]
        records.append(page_advisory_record(answer, page_index=index,
            page_digest=sha256_hex((slides[index].svg_content or "").encode()),
            png_digest=_receipt(slides[index]).get("png_sha256") or ""))
    return records


def current_page_advisories(slides, records) -> dict[int, dict]:
    """Keep only intact per-page evidence that describes the current drawing."""
    if not isinstance(records, list):
        return {}
    current = {}
    for record in records:
        if not isinstance(record, Mapping):
            continue
        index = record.get("index")
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(slides):
            continue
        body = {key: record.get(key) for key in ("schemaVersion", "index", "pageDigest", "pngDigest", "vision")}
        slide, receipt = slides[index], _receipt(slides[index])
        png_digest = receipt.get("png_sha256") or ""
        if (record.get("schemaVersion") != PAGE_ADVISORY_SCHEMA
                or record.get("digest") != content_digest(body)
                or record.get("pageDigest") != sha256_hex((slide.svg_content or "").encode())
                or record.get("pngDigest") != png_digest
                or not isinstance(png_digest, str) or len(png_digest) != 64
                or any(c not in "0123456789abcdef" for c in png_digest)
                or not page_measurement_current(slide)
                or not advisory_ran(record)):
            continue
        if any(not isinstance(entry, Mapping) or isinstance(entry.get("index"), bool)
               or entry.get("index") != index for entry in record["vision"]["slides"]):
            continue
        # Duplicate records are ambiguous rather than evidence to be merged.
        if index in current:
            current[index] = None
        else:
            current[index] = dict(record)
    return {index: record for index, record in current.items() if record is not None}


def legacy_page_advisories(slides, advisory) -> list[dict]:
    """Reuse only existing PNG-bound records; legacy SVG-only proof stays legacy."""
    if not advisory_ran(advisory) or not record_matches(advisory, slide_page_digests(slides)):
        return []
    if "pageAdvisories" in advisory:
        return list(current_page_advisories(slides, advisory["pageAdvisories"]).values())
    return []


def assemble_page_advisory(slides, records) -> dict | None:
    current = current_page_advisories(slides, records)
    if not slides or len(current) != len(slides):
        return None
    visions = [current[index]["vision"] for index in range(len(slides))]
    vision = dict(promptVersion=PAGE_ADVISORY_SCHEMA, model=";".join(sorted({str(v.get("model") or "") for v in visions})),
                  slideCount=len(slides), slides=[entry for v in visions for entry in v.get("slides", [])],
                  pageRecordDigests=[current[index]["digest"] for index in range(len(slides))])
    result = advisory_record(vision, slide_page_digests(slides))
    result["pageAdvisories"] = [current[index] for index in range(len(slides))]
    return result


def svg_quality_status(slides, advisory, *, page_advisories=None) -> dict:
    """Keep drawing availability, measurements and advisory findings distinct."""
    count = len(slides)
    measured, stale, failed = [], [], []
    for index, slide in enumerate(slides):
        quality = _receipt(slide).get("quality") or {}
        if quality.get("source_digest") != source_digest(slide.svg_content or ""):
            if quality.get("source_digest"):
                stale.append(index)
            continue
        if quality.get("measurement_status") == "unmeasured" or quality.get("measurement_complete") is not True:
            continue
        if not page_measurement_current(slide):
            stale.append(index)
            continue
        measured.append(index)
        if quality.get("passed") is not True or quality.get("verified") is not True:
            failed.append(index)
    if page_advisories is None and isinstance(advisory, Mapping):
        page_advisories = advisory.get("pageAdvisories")
    records = page_advisories or []
    current = current_page_advisories(slides, records)
    # A legacy deck record proves SVG identity only. It cannot establish that
    # today's saved PNGs were reviewed, even when every SVG still matches.
    coverage = sorted(current)
    flagged = sorted(index for index, record in current.items() if advisory_flagged_pages(record))
    return dict(advisoryOnly=True,
        artifact=dict(status="available" if count and all(s.svg_content for s in slides) else "unavailable", pageCount=count),
        measured=dict(status="stale" if stale else "failed" if failed else "passed" if count and len(measured) == count else "unmeasured",
                      currentPages=measured, stalePages=stale, failedPages=failed),
        semanticCurrent=dict(status="current" if count and len(coverage) == count else "partial" if coverage else "stale" if advisory_ran(advisory) else "unavailable",
                             currentPages=coverage),
        designFindings=dict(status="flagged" if flagged else "clear" if coverage else "unavailable", flaggedPages=flagged),
        reviewedPages=coverage, totalPages=count)


def slide_page_digests(slides) -> list[str]:
    """What the advisory record binds to: the digest of each exported SVG."""

    return [sha256_hex((slide.svg_content or "").encode()) for slide in slides]


def _review_pages(slides) -> list[int]:
    return review_page_indices([((slide.content or {}).get("svgReceipt") or {}).get("quality") or {}
                                for slide in slides])


def _generation_source_review(slides, advisory, expected_binding, context):
    """Consume the intact original source-PNG producer; never mint page receipts."""
    from services.generation_core.drivers.svg.quality import _current_gate
    from services.svg_authoring_profile import read_quality_recovery_policy
    if not isinstance(context, Mapping) or not isinstance(expected_binding, Mapping):
        return None
    snapshot, publication, producer = (context.get(key) for key in ("snapshot", "publication", "producer"))
    if not all(isinstance(value, Mapping) for value in (snapshot, publication, producer)):
        return None
    try:
        policy = read_quality_recovery_policy(snapshot)
        gate = _current_gate(advisory, [({**_receipt(slide), "svg_content": slide.svg_content}, None) for slide in slides])
    except (ValueError, TypeError, KeyError):
        return None
    if gate is None or policy is None:
        return None
    body = {key: value for key, value in publication.items() if key != "digest"}
    binding = gate.get("binding") or {}
    vision = advisory["vision"]
    pngs = [_receipt(slide).get("png_sha256") for slide in slides]
    if (gate.get("policy") != policy or gate.get("status") != "passed"
            or policy["version"] == 2 and gate.get("designImprovementStatus") != "satisfied"
            or gate.get("reason") is not None
            or gate.get("pageCoverageComplete") is not True or not slides
            or len(gate.get("pages", [])) != len(slides)
            or any(page.get("evaluated") is not True or page.get("mandatoryEvidenceValid") is not True
                   or page.get("status") != "passed" for page in gate.get("pages", []))
            or any(not page_measurement_current(slide) for slide in slides)
            or publication.get("schema") != "svg-quality-recovery-publication/v1"
            or publication.get("digest") != content_digest(body)
            or publication.get("gateDigest") != gate["digest"]
            or gate.get("snapshotDigest") != content_digest(snapshot)
            or publication.get("snapshotDigest") != gate["snapshotDigest"]
            or publication.get("parentOperationId") != binding.get("operationId")
            or publication.get("parentJobId") != binding.get("jobId")
            or publication.get("sourceRevision") != expected_binding.get("sourceRevision")
            or publication.get("sourceGenerationRevision") != expected_binding.get("sourceRevision")
            or publication.get("sourceContentDigests") != [content_digest((slide.content or {}).get("source")) for slide in slides]
            or publication.get("inputDigests") != [_receipt(slide).get("input_digest") for slide in slides]
            or producer.get("state") != "succeeded" or producer.get("kind") != "presentation.generate"
            or producer.get("operationId") != binding.get("operationId")
            or producer.get("sourceRevision") != expected_binding.get("sourceRevision")
            or any(producer.get(key) != expected_binding.get(key) for key in ("presentationId", "ownerId", "ownerScope"))
            or any(binding.get(key) != expected_binding.get(key) for key in ("presentationId", "ownerId"))
            or vision.get("reviewedPageIndices") != list(range(len(slides)))
            or vision.get("reviewedPngDigests") != pngs):
        return None
    return gate


def svg_visual_review(*, slides, advisory: Mapping | None, expected_binding: Mapping | None = None) -> dict:
    source_context = None
    if isinstance(expected_binding, Mapping):
        expected_binding = dict(expected_binding)
        source_context = expected_binding.pop("_generationSourceReview", None)
    if not advisory_ran(advisory):
        return dict(required=True, status="failed", reasonCode="svg_visual_advisory_unavailable")
    digests = slide_page_digests(slides)
    # Changed SVG bytes invalidate even a legacy answer before PNG coverage
    # is considered; missing PNG proof describes only otherwise-current SVGs.
    if not record_matches(advisory, digests):
        return dict(required=True, status="failed", reasonCode="svg_visual_advisory_stale")
    vision = advisory["vision"]
    from services.generation_core.quality.vision_advisory_prompt import DESIGN_REVIEW_PROMPT_VERSION
    design_review = vision.get("promptVersion") == DESIGN_REVIEW_PROMPT_VERSION
    producer_gate = _generation_source_review(slides, advisory, expected_binding, source_context) if design_review else None
    if "pageAdvisories" not in advisory:
        if producer_gate is None or producer_gate["policy"]["version"] != 2:
            return dict(required=True, status="failed", reasonCode="svg_visual_advisory_png_evidence_missing")
        flagged, review = advisory_flagged_pages(advisory), _review_pages(slides)
        evidence = dict(checkerVersion=DESIGN_REVIEW_PROMPT_VERSION,
            inputDigest=content_digest({"svgDigests": digests, "pngDigests": producer_gate["pngDigests"]}),
            outputDigest=content_digest({"producerAdvisoryDigest": advisory["digest"], "producerGateDigest": producer_gate["digest"]}),
            measurements=dict(advisoryDigest=advisory["digest"], pageCount=len(slides),
                advisoryFlaggedPages=len(flagged), reviewPages=len(review), model=str(vision.get("model") or ""),
                reviewScope="source-svg-png-design/v1", designGateDigest=producer_gate["digest"],
                sourceProducerOperationId=producer_gate["binding"]["operationId"]), thresholds={"maxReviewPages": 0})
        return dict(required=True, status="failed" if review else "passed",
                    **({"reasonCode": "svg_visual_review_flagged"} if review else {}), evidence=evidence)
    current = current_page_advisories(slides, advisory["pageAdvisories"])
    if not slides or len(current) != len(slides):
        return dict(required=True, status="failed", reasonCode="svg_visual_advisory_stale")
    if design_review:
        from services.generation_core.drivers.svg.quality import _current_gate
        from services.svg_authoring_profile import validate_quality_recovery_policy
        payloads = [({**_receipt(slide), "svg_content": slide.svg_content}, None) for slide in slides]
        gate = _current_gate(advisory, payloads)
        try:
            policy = validate_quality_recovery_policy(gate.get("policy")) if gate else None
        except ValueError:
            policy = None
        if (gate is None or gate.get("status") != "passed" or policy is None
                or policy["version"] == 2 and producer_gate is None
                or gate.get("pageCoverageComplete") is not True
                or (gate.get("binding") or {}).get("presentationId") != (expected_binding or {}).get("presentationId", (gate.get("binding") or {}).get("presentationId"))
                or any(current[index]["vision"].get("designGateDigest") != gate["digest"] for index in range(len(slides)))):
            return dict(required=True, status="failed", reasonCode="svg_quality_recovery_review_required")
    page_findings = [entry for index in range(len(slides)) for entry in current[index]["vision"]["slides"]]
    models = ";".join(sorted({str(current[index]["vision"].get("model") or "") for index in range(len(slides))}))
    page_records = [current[index]["digest"] for index in range(len(slides))]
    if (vision.get("slides") != page_findings or str(vision.get("model") or "") != models
            or ("pageRecordDigests" in vision and vision["pageRecordDigests"] != page_records)):
        return dict(required=True, status="failed", reasonCode="svg_visual_advisory_stale")
    if expected_binding is not None:
        binding = advisory.get("operationBinding")
        if not isinstance(binding, Mapping):
            return dict(required=True, status="failed", reasonCode="svg_visual_advisory_operation_binding_missing")
        unsigned = {key: value for key, value in binding.items() if key != "digest"}
        if (binding.get("schemaVersion") != "svg-visual-review-operation-binding/v1"
                or binding.get("digest") != content_digest(unsigned)
                or not binding.get("operationId")
                or any(binding.get(key) != value for key, value in expected_binding.items())
                or binding.get("sourceDigests") != digests
                or binding.get("pngDigests") != [_receipt(slide).get("png_sha256") for slide in slides]
                or binding.get("pageRecordDigests") != page_records):
            return dict(required=True, status="failed", reasonCode="svg_visual_advisory_operation_binding_invalid")
        if design_review and (gate.get("binding") or {}).get("operationId") != binding.get("operationId"):
            return dict(required=True, status="failed", reasonCode="svg_visual_advisory_operation_binding_invalid")
        if any(current[index]["vision"].get("promptVersion") != (
                    DESIGN_REVIEW_PROMPT_VERSION if design_review else "svg-required-visual-review/v1")
               or current[index]["vision"].get("reviewedPageIndices") != [index]
               or current[index]["vision"].get("reviewedPngDigests") != [_receipt(slides[index]).get("png_sha256")]
               for index in range(len(slides))):
            return dict(required=True, status="failed", reasonCode="svg_visual_advisory_review_coverage_incomplete")
    flagged, review = advisory_flagged_pages(advisory), _review_pages(slides)
    evidence = dict(
        checkerVersion=str(vision.get("promptVersion") or ADVISORY_RECORD_SCHEMA),
        inputDigest=content_digest({"svgDigests": digests,
            "pngDigests": [_receipt(slide).get("png_sha256") for slide in slides]}),
        outputDigest=content_digest({"vision": vision,
            "pageRecordDigests": [current[index]["digest"] for index in range(len(slides))]}),
        measurements={"advisoryDigest": advisory["digest"], "pageCount": len(digests),
                      "advisoryFlaggedPages": len(flagged), "reviewPages": len(review),
                      "model": str(vision.get("model") or "")},
        thresholds={"maxReviewPages": 0})
    if design_review:
        evidence["measurements"].update(reviewScope="source-svg-png-design/v1", designGateDigest=gate["digest"])
    if review:
        return dict(required=True, status="failed", reasonCode="svg_visual_review_flagged",
                    evidence=evidence)
    return dict(required=True, status="passed", evidence=evidence)


def release_readiness(domains: Iterable[Mapping]) -> str:
    """``ready`` exactly when every required domain passed."""

    return "ready" if all(
        domain.get("required") is not True or domain.get("status") == "passed" for domain in domains
    ) else "blocked"


__all__ = ["release_readiness", "slide_page_digests", "svg_visual_review", "svg_quality_status",
           "page_advisory_record", "split_page_advisories", "current_page_advisories",
           "legacy_page_advisories", "assemble_page_advisory"]
