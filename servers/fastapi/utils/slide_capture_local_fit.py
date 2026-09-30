"""Request-only policy transport through existing isolated renderer paths.

The browser fit runner is the only judge of whether a raster element's
embedded text has been verified. This module never makes that call: it only
carries `image_text_evidence` producer records into the browser policy and,
after the runner reports its verdicts, copies them back onto the matching
elements in `metrics`.
"""
from __future__ import annotations

import base64
import json
import re
import uuid
from collections.abc import Mapping
from typing import Any

from services.authored_local_fit_policy import POLICY_REVISION, font_policy
from models.canvas_defaults import DEFAULT_CANVAS_HEIGHT_PX, DEFAULT_CANVAS_WIDTH_PX

_MARKER = re.compile(r"<!--codex-local-fit-v1[2345]:([A-Za-z0-9_=-]+)-->")


def policy_request_html(html: str, policy: Mapping[str, Any], *, fit: bool) -> str:
    if _MARKER.search(html):
        raise ValueError("reserved local fit request marker in authored source")
    payload = {**policy, "fit": bool(fit), "enabled": bool(fit)}
    encoded = base64.urlsafe_b64encode(json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).decode("ascii")
    revision = str(policy.get("policy_revision") or POLICY_REVISION)
    font_policy(revision)
    marker_revision = (
        "15" if revision.startswith("authored-local-fit/user-font-v1/")
        else revision.rsplit("/v", 1)[-1].replace(".", "")
    )
    # A comment changes no selectors, authored styles or visual DOM structure.
    return html + f"<!--codex-local-fit-v{marker_revision}:" + encoded + "-->"


def policy_from_request(html: str) -> dict[str, Any] | None:
    markers = list(_MARKER.finditer(html))
    if not markers:
        return None
    if len(markers) != 1:
        raise ValueError("invalid local fit policy marker")
    policy = json.loads(base64.urlsafe_b64decode(markers[0].group(1)))
    if not isinstance(policy, dict):
        raise ValueError("unsupported local fit policy")
    try:
        font_policy(str(policy.get("policy_revision") or ""))
    except ValueError as exc:
        raise ValueError("unsupported local fit policy") from exc
    return policy


def measured_html(html: str, metrics: Mapping[str, Any] | None) -> str:
    """Use the measured revision for existing contrast/glyph side probes too."""
    report = (metrics or {}).get("fit")
    edits = report.get("edits") if isinstance(report, Mapping) else None
    if not edits:
        return html
    from utils.authored_fit_edits import apply_fit_edits
    return apply_fit_edits(html, edits)


def local_fit_render_identity(html: str, policy: Mapping[str, Any], *, fit: bool) -> str:
    from services.authored_local_fit_policy import digest
    from utils.authored_font_identity import installed_font_identity
    from utils import slide_capture
    # Baseline belongs to acceptance identity. A fit-disabled render of the
    # same final bytes has the same pixels for two different original sizes.
    policy_revision = str(policy.get("policy_revision") or POLICY_REVISION)
    font_policy(policy_revision)
    return digest({"renderer": slide_capture._render_cache_identity(html).digest,
                   "installed_fonts": installed_font_identity(),
                   "fit_mode": bool(fit), "policy_revision": policy_revision,
                   "export_scale_manifest": policy["export_scale_manifest"]})


def merge_image_text_verdicts(metrics: Mapping[str, Any], report: Mapping[str, Any]) -> dict[str, Any]:
    """Copy the runner's image/text verdicts onto their matching elements.

    The browser fit runner (see `utils/slide_capture_fit_runner.py`) is the
    only judge of whether a raster element's text has been verified; this
    function makes no such decision itself. It copies each verified record
    from `report["image_text"]` onto the element with the matching qid, and
    leaves everything else untouched: unverified records write nothing, an
    element that already carries `image_text_evidence` is left as-is, a
    record whose qid matches no element is ignored, and no other element
    field is modified. Input mappings are not mutated; a new metrics dict
    (with a new elements list) is returned, preserving element order.
    """
    verdicts = {
        str(item.get("qid") or ""): item
        for item in (report.get("image_text") or [])
        if isinstance(item, Mapping) and item.get("verified") is True
    }
    elements = metrics.get("elements")
    if not verdicts or not isinstance(elements, list):
        return dict(metrics)

    merged_elements = []
    for element in elements:
        verdict = (
            verdicts.get(str(element.get("qid") or ""))
            if isinstance(element, Mapping)
            else None
        )
        if verdict is None or "image_text_evidence" in element:
            merged_elements.append(element)
            continue
        scope = verdict.get("scope")
        status = "no_text" if scope == "verified_no_text" else "font_floor_verified"
        merged_elements.append({
            **element,
            "image_text_evidence": {
                "status": status,
                "scope": scope,
                "asset_identity": verdict.get("asset_identity"),
            },
        })
    return {**metrics, "elements": merged_elements}


async def capture_with_policy(html: str, timeout: int, *, deck_scope: str | None,
                              fit: bool, policy: Mapping[str, Any], fresh: bool,
                              width_px: int = DEFAULT_CANVAS_WIDTH_PX, height_px: int = DEFAULT_CANVAS_HEIGHT_PX):
    from services.authored_local_fit_policy import html_digest, image_text_evidence_for_html
    from utils import slide_capture as capture
    from utils.authored_fit_edits import apply_fit_edits
    from utils.authored_font_identity import installed_font_identity

    scale = policy.get("export_scale_manifest")
    scale = scale if isinstance(scale, Mapping) else {}
    if (
        int(scale.get("viewport_width", width_px)) != width_px
        or int(scale.get("viewport_height", height_px)) != height_px
    ):
        raise ValueError("local fit canvas mismatch")

    # The producer supplies image/text evidence for the exact HTML being
    # rendered; a caller-supplied non-empty list is trusted as-is instead.
    image_text_evidence = policy.get("image_text_evidence") or image_text_evidence_for_html(html)
    policy = {**policy, "installed_font_identity": installed_font_identity(),
              "image_text_evidence": image_text_evidence}
    canonical_request = policy_request_html(html, policy, fit=fit)
    request_html = (policy_request_html(html, {**policy, "_document_nonce": uuid.uuid4().hex}, fit=fit)
                    if fresh else canonical_request)
    measured = await capture.render_html_to_png_with_metrics(
        request_html, timeout=timeout, deck_scope=deck_scope, fresh=fresh,
        width_px=width_px, height_px=height_px)
    metrics = dict(measured.browser_metrics or {})
    report = metrics.get("fit")
    if not isinstance(report, Mapping) or report.get("fit_state") != "final":
        raise ValueError("local fit final observation unavailable")
    metrics = merge_image_text_verdicts(metrics, report)
    edits = report.get("edits") or []
    if not fit and edits:
        raise ValueError("fit-disabled renderer returned edits")
    final_html = apply_fit_edits(html, edits) if edits else html
    metrics["local_fit_candidate_only"] = bool(edits)
    if edits:
        report = {**report, "fit_proposal": True, "candidate_html_digest": html_digest(final_html)}
    # Browser edits describe a proposal; only a fresh fit-disabled capture can
    # establish evidence for the reconstructed candidate HTML.
    source, rendered = capture._source_render_digests(html)
    metrics.update(source_digest=source, render_digest=rendered,
                   metrics_cache_key=capture._metric_evidence_cache_key(
                       html, width_px=width_px, height_px=height_px))
    metrics["local_fit_render_identity"] = local_fit_render_identity(html, policy, fit=fit)
    metrics["local_fit_mode"] = bool(fit)
    return capture.RenderCapture.from_payload(
        html_digest=html_digest(html), png=measured.png, browser_metrics=metrics,
        runtime_counters=capture.get_render_runtime_counters(),
        fitted_html=final_html if final_html != html else None, fit_report=dict(report))


def observation_script(metrics_element_id: str) -> str:
    return _OBSERVATION_JS.replace("__METRICS_ID__", metrics_element_id)


_OBSERVATION_JS = r"""
  let domRevision = 0, finalIdentity = null, finalObservations = 0, duplicateFinalSkipped = 0;
  const relevantMutation = record => {
    const target = record.target;
    if (target && target.id === '__METRICS_ID__') return false;
    if (record.type === 'childList' && [...record.addedNodes, ...record.removedNodes].every(
        node => node.id === '__METRICS_ID__')) return false;
    return true;
  };
  const countMutations = records => { if (records.some(relevantMutation)) domRevision += 1; };
  const observer = new MutationObserver(countMutations);
  observer.observe(document.documentElement, {attributes:true, childList:true, subtree:true, characterData:true});
  const stateIdentity = () => {
    countMutations(observer.takeRecords());
    return JSON.stringify([domRevision, innerWidth, innerHeight,
      document.fonts ? [...document.fonts].map(f => [f.family,f.style,f.weight,f.status]) : [],
      [...document.images].map(i => [i.currentSrc,i.complete,i.naturalWidth,i.naturalHeight]),
      performance.getEntriesByType('resource').map(r => [r.name,r.responseEnd]),
      document.getAnimations().filter(a => a.playState === 'running').map(a => a.currentTime)]);
  };
  const observe = () => {
    const ready = (!document.fonts || document.fonts.status === 'loaded') &&
      [...document.images].every(image => image.complete);
    const identity = stateIdentity();
    if (ready && identity === finalIdentity) {
      duplicateFinalSkipped += 1;
      const marker = document.getElementById('__METRICS_ID__');
      if (marker) {
        const payload = JSON.parse(decodeURIComponent(marker.getAttribute('content')));
        payload.observation_telemetry = {final_observations:finalObservations,
          duplicate_final_skipped:duplicateFinalSkipped, dom_revision:domRevision};
        marker.setAttribute('content', encodeURIComponent(JSON.stringify(payload)));
      }
      return;
    }
    try {
      done(ready ? 'final' : 'provisional');
      if (ready) { finalObservations += 1; finalIdentity = stateIdentity(); }
      const marker = document.getElementById('__METRICS_ID__');
      const payload = JSON.parse(decodeURIComponent(marker.getAttribute('content')));
      payload.observation_telemetry = {final_observations:finalObservations,
        duplicate_final_skipped:duplicateFinalSkipped, dom_revision:domRevision};
      marker.setAttribute('content', encodeURIComponent(JSON.stringify(payload)));
    } catch (error) { failed(error); }
  };
  window.__codexObserveFinal = observe;
  const run = () => {
    observe();
    try { if (document.fonts && document.fonts.ready) document.fonts.ready.then(observe).catch(failed); } catch (_) {}
    for (const image of document.images) {
      if (!image.complete) { image.addEventListener('load', observe, {once:true});
        image.addEventListener('error', observe, {once:true}); }
    }
  };
"""
