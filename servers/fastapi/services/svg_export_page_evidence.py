"""The export receipt's ``authoring`` and ``geometry_font`` domains for a generated SVG deck.

A source-preserving deck is judged from its source-preservation receipts
(``svg_export_quality.source_svg_public_quality_receipt``). Every other SVG
deck is judged here from the evidence its generation already persisted per
page: ``slide.content.svgReceipt.quality``, the dict
``services.svg_renderer.render_svg_with_metrics`` measured in Chromium and the
driver's author loop (``generation_core/drivers/svg/render.py``) completed with
its acceptance verdict, plus ``svgReceipt.terminal`` when the terminal policy
substituted the page (``generation_core/drivers/svg/terminal.py``).

Each domain is fail-closed with three outcomes:

* ``passed``  -- every page carries the evidence, it was measured on exactly
  the SVG bytes being exported (``quality.source_digest``), and it passed;
* ``failed`` + ``svg_*_evidence_missing`` -- a page has no such evidence;
* ``failed`` + ``svg_*_evidence_stale``   -- a page's evidence was measured on
  other bytes (the page changed after measurement without re-measurement);
* ``failed`` + ``svg_*_failed``           -- the evidence says the page failed.

``authoring`` asks whether each exported page is the author loop's own
accepted page: not a terminal substitute (fallback or flag-only) and accepted
by the loop's final verdict (``quality.passed``: contract-valid SVG, required
numeric claims present, offered images and icons used, no residual measured
failure). ``geometry_font`` asks whether Chromium measured each page
completely, found no geometry failure, and loaded every text run's font.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import json

from services.svg_contract import SVG_CONTRACT_VERSION, source_digest
from services.svg_renderer import SVG_RENDERER_VERSION
from utils.hashing import sha256_prefixed

AUTHORING_CHECKER = f"svg-author-loop/{SVG_CONTRACT_VERSION}"
_MEASURED_KEYS = ("source_digest", "renderer_version", "contract_version", "measurement_complete",
                  "geometry_passed", "passed")


def _digest(value) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    return sha256_prefixed(encoded)


def _receipt(slide) -> Mapping:
    content = slide.content if isinstance(slide.content, Mapping) else {}
    receipt = content.get("svgReceipt")
    return receipt if isinstance(receipt, Mapping) else {}


def _quality(slide) -> Mapping | None:
    quality = _receipt(slide).get("quality")
    if not isinstance(quality, Mapping) or any(key not in quality for key in _MEASURED_KEYS):
        return None
    if (quality.get("renderer_version") != SVG_RENDERER_VERSION
            or quality.get("contract_version") != SVG_CONTRACT_VERSION):
        return None  # measured by a checker this code does not know how to read
    return quality


def _font_loaded(quality: Mapping) -> bool:
    fonts = quality.get("trusted_font_materialization")
    return isinstance(fonts, Mapping) and fonts.get("worker_materialized") is True


def _authoring_ok(slide, quality: Mapping) -> bool:
    return _receipt(slide).get("terminal") is None and quality.get("passed") is True


def _geometry_ok(slide, quality: Mapping) -> bool:
    return (quality.get("measurement_complete") is True and quality.get("geometry_passed") is True
            and _font_loaded(quality))


def _page_domain(slides: Sequence, *, name: str, checker: str, page_ok, fields) -> dict:
    if not slides:
        return dict(required=True, status="failed", reasonCode=f"svg_{name}_evidence_missing")
    qualities = [_quality(slide) for slide in slides]
    if any(quality is None for quality in qualities):
        return dict(required=True, status="failed", reasonCode=f"svg_{name}_evidence_missing")
    page_digests = [source_digest(slide.svg_content or "") for slide in slides]
    if any(quality["source_digest"] != digest for quality, digest in zip(qualities, page_digests, strict=True)):
        return dict(required=True, status="failed", reasonCode=f"svg_{name}_evidence_stale")
    failed = [index for index, (slide, quality) in enumerate(zip(slides, qualities, strict=True))
              if not page_ok(slide, quality)]
    evidence = dict(
        checkerVersion=checker, inputDigest=_digest(page_digests),
        outputDigest=_digest([fields(slide, quality) for slide, quality in zip(slides, qualities, strict=True)]),
        measurements={"pageCount": len(slides), "verifiedPages": len(slides) - len(failed),
                      "failedPages": ",".join(str(index) for index in failed)},
        thresholds={"allPagesRequired": True, "maxFailedPages": 0})
    if failed:
        return dict(required=True, status="failed", reasonCode=f"svg_{name}_failed", evidence=evidence)
    return dict(required=True, status="passed", evidence=evidence)


def svg_authoring(slides: Sequence) -> dict:
    """``authoring`` from each page's author-loop verdict and terminal-policy record."""

    return _page_domain(slides, name="authoring", checker=AUTHORING_CHECKER, page_ok=_authoring_ok,
                        fields=lambda slide, quality: {
                            "sourceDigest": quality["source_digest"], "passed": quality["passed"],
                            "terminal": _receipt(slide).get("terminal"),
                            "failures": quality.get("failures") or []})


def svg_geometry_font(slides: Sequence) -> dict:
    """``geometry_font`` from each page's Chromium measurement and font materialization."""

    return _page_domain(slides, name="geometry_font", checker=SVG_RENDERER_VERSION, page_ok=_geometry_ok,
                        fields=lambda slide, quality: {
                            "sourceDigest": quality["source_digest"],
                            "metricsDigest": quality.get("metrics_digest"),
                            "measurementComplete": quality["measurement_complete"],
                            "geometryPassed": quality["geometry_passed"],
                            "fontMaterialization": quality.get("trusted_font_materialization")})


__all__ = ["AUTHORING_CHECKER", "svg_authoring", "svg_geometry_font"]
