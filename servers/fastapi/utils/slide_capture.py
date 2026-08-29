"""Headless per-slide capture for the vision-QA pass. Pure subprocess chrome
screenshot (no browser dependency — consistent with export spawning an external
binary) + a deterministic PIL crop: the /pdf-maker page stacks slides at exactly
1280x720 with gap 0 / margin 0, so slide i is the box (0, i*720, 1280, (i+1)*720)."""

import asyncio
import atexit
import base64
import binascii
import hashlib
import html as html_lib
import io
import json
import logging
import math
import os
import pathlib
import re
import signal
import subprocess
import tempfile
import threading
import time
import urllib.parse
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional

import aiohttp

from utils.authored_dom_quality import ensure_authored_qids
from utils.renderer_internal_capability import issue_internal_render_capability
from utils.renderer_isolation import (
    MAX_RESPONSE_BYTES,
    AdmittedHtml,
    RendererDisabledError,
    RendererPolicyError,
    admit_authored_html,
    admit_internal_instrumented_html,
    admit_static_output_html,
    build_worker_environment,
    encode_request,
    ensure_renderer_enabled,
    env_enabled,
    renderer_mode,
    renderer_requires_external_worker,
    worker_command,
)

SLIDE_W = 1280
SLIDE_H = 720
LOGGER = logging.getLogger(__name__)
_BLOCKED_RENDERER_URL_PATTERNS = [
    "http://*",
    "https://*",
    "file://*",
    "ftp://*",
    "ws://*",
    "wss://*",
]

def _kill_process_tree(proc: "subprocess.Popen") -> None:
    """Kill a chrome process AND its renderer/gpu children. `subprocess` timeout only
    kills the direct child, orphaning chrome's helper processes — and leaked chromes
    starve later launches (every subsequent render then hangs). Tree-kill prevents the
    cascade."""
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            proc.kill()


def _run_chrome_screenshot(args: List[str], timeout: int) -> bytes:
    """Run a headless-chrome screenshot to completion, killing the whole process tree
    on timeout so a hung render leaks nothing. Callers check for the output file to
    decide success/failure — a timed-out render simply leaves no file."""
    popen_kwargs: dict = {"stdout": subprocess.PIPE, "stderr": subprocess.PIPE}
    if os.name == "nt":
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        popen_kwargs["start_new_session"] = True
    proc = subprocess.Popen(args, **popen_kwargs)
    try:
        stdout, _ = proc.communicate(timeout=timeout)
        return stdout or b""
    except subprocess.TimeoutExpired:
        _kill_process_tree(proc)
        try:
            stdout, _ = proc.communicate(timeout=5)
            return stdout or b""
        except Exception:
            return b""


_METRICS_ELEMENT_ID = "__codex_authored_dom_metrics"
_METRICS_PATTERN = re.compile(
    rf'<meta\b(?=[^>]*\bid\s*=\s*["\']{_METRICS_ELEMENT_ID}["\'])'
    rf'[^>]*\bcontent\s*=\s*(["\'])(.*?)\1[^>]*>',
    re.IGNORECASE,
)
_internal_capability_lock = threading.Lock()
_internal_capabilities: "OrderedDict[str, str]" = OrderedDict()
_INTERNAL_CAPABILITY_LIMIT = 64


def _register_internal_render_source(source: str) -> None:
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    token = issue_internal_render_capability(source)
    with _internal_capability_lock:
        _internal_capabilities[digest] = token
        _internal_capabilities.move_to_end(digest)
        while len(_internal_capabilities) > _INTERNAL_CAPABILITY_LIMIT:
            _internal_capabilities.popitem(last=False)


def _admit_render_source(source: str) -> AdmittedHtml:
    """Select a fail-closed admission profile for the exact in-memory source."""

    try:
        return admit_authored_html(source)
    except RendererPolicyError:
        digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
        with _internal_capability_lock:
            capability = _internal_capabilities.pop(digest, None)
        if capability is not None:
            return admit_internal_instrumented_html(source, capability)
        return admit_static_output_html(source)


def _source_render_digests(html: str) -> tuple[str, str]:
    """Return distinct source and qid-enriched render identities.

    DOM evidence is only reusable when both the imported source and the exact
    render projection are unchanged.  Keeping the two digests explicit avoids
    treating a source-preserving instrumentation change as equivalent evidence.
    """

    source = hashlib.sha256(html.encode("utf-8")).hexdigest()
    rendered = hashlib.sha256(ensure_authored_qids(html).encode("utf-8")).hexdigest()
    return f"sha256:{source}", f"sha256:{rendered}"


def _metric_evidence_cache_key(
    html: str, *, qid: str = "*", metric: str = "*"
) -> str:
    """Build the scoped identity used by browser metric caches/evidence atoms."""

    source_digest, render_digest = _source_render_digests(html)
    return _metric_evidence_cache_key_from_digests(
        source_digest, render_digest, qid=qid, metric=metric
    )


def _metric_evidence_cache_key_from_digests(
    source_digest: str,
    render_digest: str,
    *,
    qid: str = "*",
    metric: str = "*",
) -> str:
    payload = "\0".join(
        (
            DOM_METRICS_VERSION,
            OCCLUSION_SENSOR_VERSION,
            qid,
            metric,
            source_digest,
            render_digest,
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _metrics_cache_key(html: str) -> str:
    return _metric_evidence_cache_key(html)


def _instrument_authored_html(
    html: str,
    *,
    script_nonce: str | None = None,
    issue_capability: bool = True,
) -> tuple[str, str]:
    """Add stable edit ids and an invisible, same-render browser probe."""
    enriched = ensure_authored_qids(html)
    source_digest = "sha256:" + hashlib.sha256(html.encode("utf-8")).hexdigest()
    render_digest = "sha256:" + hashlib.sha256(enriched.encode("utf-8")).hexdigest()
    key = _metric_evidence_cache_key_from_digests(source_digest, render_digest)
    if _METRICS_ELEMENT_ID in enriched:
        if issue_capability:
            _register_internal_render_source(enriched)
        return enriched, key
    nonce_attribute = (
        f' nonce="{html_lib.escape(script_nonce, quote=True)}"' if script_nonce else ""
    )
    script = rf"""<script data-codex-dom-probe{nonce_attribute}>
(() => {{
  const done = () => {{
    const px = (value) => {{ const parsed = parseFloat(value || '0'); return Number.isFinite(parsed) ? parsed : 0; }};
    const rgba = (value) => {{
      const match = String(value || '').match(/rgba?\(\s*([\d.]+)[, ]+\s*([\d.]+)[, ]+\s*([\d.]+)(?:\s*[,/]\s*([\d.]+))?\s*\)/i);
      return match ? [Number(match[1]), Number(match[2]), Number(match[3]), match[4] === undefined ? 1 : Number(match[4])] : null;
    }};
    const composite = (front, back) => {{
      const alpha = front[3] + back[3] * (1 - front[3]);
      if (alpha <= 0) return [255, 255, 255, 1];
      return [0, 1, 2].map((index) => (front[index] * front[3] + back[index] * back[3] * (1 - front[3])) / alpha).concat(alpha);
    }};
    const effectiveBackground = (node) => {{
      const layers = [];
      for (let current = node; current; current = current.parentElement) {{
        const style = getComputedStyle(current);
        // A single sampled color is not sound evidence for gradients, images,
        // blend modes, filters, or backdrop effects.  Return unknown so hard
        // readability constraints fail closed instead of accepting a guessed
        // white background.
        if (style.backgroundImage !== 'none' || style.mixBlendMode !== 'normal' ||
            style.backgroundBlendMode !== 'normal' || style.filter !== 'none' ||
            style.backdropFilter !== 'none') return null;
        const color = rgba(style.backgroundColor);
        if (color && color[3] > 0) layers.push(color);
      }}
      return layers.reverse().reduce((back, front) => composite(front, back), [255, 255, 255, 1]);
    }};
    const luminance = (color) => {{
      const channel = (value) => {{ value /= 255; return value <= .04045 ? value / 12.92 : Math.pow((value + .055) / 1.055, 2.4); }};
      return .2126 * channel(color[0]) + .7152 * channel(color[1]) + .0722 * channel(color[2]);
    }};
    const contrastRatio = (node, style) => {{
      const foreground = rgba(style.color); const background = effectiveBackground(node);
      if (!foreground || !background) return null;
      const paintedForeground = composite(foreground, background);
      const first = luminance(paintedForeground), second = luminance(background);
      return (Math.max(first, second) + .05) / (Math.min(first, second) + .05);
    }};
    const ownedTextNodes = (node) => {{
      const result = [];
      const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
      for (let text = walker.nextNode(); text; text = walker.nextNode()) {{
        if (!String(text.textContent || '').trim()) continue;
        if (text.parentElement?.closest('[data-qid]') !== node) continue;
        result.push(text);
      }}
      return result;
    }};
    const ownedTextRectCache = new WeakMap();
    const ownedTextRects = (node) => {{
      const cached = ownedTextRectCache.get(node);
      if (cached) return cached;
      const rects = [];
      for (const text of ownedTextNodes(node)) {{
        const range = document.createRange(); range.selectNodeContents(text);
        for (const rect of range.getClientRects()) {{
          if (rect.width > 0 && rect.height > 0) rects.push({{x: rect.x, y: rect.y, width: rect.width, height: rect.height}});
        }}
      }}
      ownedTextRectCache.set(node, rects);
      return rects;
    }};
    const replacedPaintTags = new Set(['IMG', 'PICTURE', 'SVG', 'CANVAS', 'VIDEO', 'OBJECT', 'EMBED']);
    const visibleColor = (value) => {{
      const color = rgba(value);
      return color !== null && color[3] > .001;
    }};
    const ownsSurfacePaint = (node, style = getComputedStyle(node)) => {{
      if (style.visibility === 'hidden' || Number(style.opacity || 1) <= .001) return false;
      if (replacedPaintTags.has(node.tagName)) return true;
      if (style.backgroundImage !== 'none' || visibleColor(style.backgroundColor)) return true;
      if (style.boxShadow !== 'none' || style.outlineStyle !== 'none') return true;
      return ['Top', 'Right', 'Bottom', 'Left'].some((side) =>
        px(style[`border${{side}}Width`]) > 0 &&
        !['none', 'hidden'].includes(style[`border${{side}}Style`]) &&
        visibleColor(style[`border${{side}}Color`])
      );
    }};
    const ownedSurfaceRectCache = new WeakMap();
    const ownedSurfaceRects = (node) => {{
      const cached = ownedSurfaceRectCache.get(node);
      if (cached) return cached;
      const rects = [node, ...Array.from(node.querySelectorAll('*'))]
        .filter((candidate) => candidate.closest('[data-qid]') === node && ownsSurfacePaint(candidate))
        .map((candidate) => candidate.getBoundingClientRect())
        .filter((rect) => rect.width > 0 && rect.height > 0)
        .map((rect) => ({{x: rect.x, y: rect.y, width: rect.width, height: rect.height}}));
      ownedSurfaceRectCache.set(node, rects);
      return rects;
    }};
    const pointInRect = (x, y, rect) =>
      x >= rect.x && x <= rect.x + rect.width && y >= rect.y && y <= rect.y + rect.height;
    const candidatePaintsAt = (candidate, x, y) => {{
      const style = getComputedStyle(candidate);
      if (style.visibility === 'hidden' || Number(style.opacity || 1) <= .001) return false;
      if (ownsSurfacePaint(candidate, style)) return true;
      const owner = candidate.closest?.('[data-qid]');
      if (!owner) return false;
      return ownedTextRects(owner).some((rect) => pointInRect(x, y, rect));
    }};
    const visibleClip = (node) => {{
      let result = {{left: 0, top: 0, right: innerWidth, bottom: innerHeight}};
      let cause = 'viewport';
      for (let current = node; current; current = current.parentElement) {{
        const style = getComputedStyle(current);
        if (['hidden', 'clip', 'scroll', 'auto'].includes(style.overflowX) || ['hidden', 'clip', 'scroll', 'auto'].includes(style.overflowY)) {{
          const rect = current.getBoundingClientRect();
          result = {{left: Math.max(result.left, rect.left), top: Math.max(result.top, rect.top), right: Math.min(result.right, rect.right), bottom: Math.min(result.bottom, rect.bottom)}};
          cause = current === node ? 'self' : 'ancestor';
        }}
      }}
      return {{bounds: result, cause}};
    }};
    const glyphClip = (node) => {{
      const textNodes = ownedTextNodes(node);
      if (!textNodes.length) return {{glyph: false, line: false, cause: 'none'}};
      const clip = visibleClip(node); const rects = [];
      for (const text of textNodes) {{
        const range = document.createRange(); range.selectNodeContents(text);
        rects.push(...Array.from(range.getClientRects()));
      }}
      const outside = (rect) => rect.left < clip.bounds.left - .5 || rect.top < clip.bounds.top - .5 || rect.right > clip.bounds.right + .5 || rect.bottom > clip.bounds.bottom + .5;
      // Inline elements legitimately report clientHeight=0 even though their
      // line boxes are fully visible.  The clip intersection already includes
      // every clipping/scrolling ancestor (and the viewport), so use rendered
      // range escape as the sound signal for both glyph and line-box clipping.
      const escaped = rects.some(outside);
      return {{glyph: escaped, line: escaped, cause: escaped ? clip.cause : 'none'}};
    }};
    const quad = (node, rect) => {{
      try {{
        const value = node.getBoxQuads?.()[0];
        if (value) return {{points: [value.p1, value.p2, value.p3, value.p4].map((point) => ({{x: point.x, y: point.y}})), measured: true}};
      }} catch (_) {{}}
      try {{
        const style = getComputedStyle(node);
        const matrix = new DOMMatrixReadOnly(style.transform === 'none' ? undefined : style.transform);
        const width = node.offsetWidth || rect.width, height = node.offsetHeight || rect.height;
        const local = [new DOMPoint(0, 0), new DOMPoint(width, 0), new DOMPoint(width, height), new DOMPoint(0, height)]
          .map((point) => point.matrixTransform(matrix));
        const minX = Math.min(...local.map((point) => point.x));
        const minY = Math.min(...local.map((point) => point.y));
        return {{points: local.map((point) => ({{x: rect.left + point.x - minX, y: rect.top + point.y - minY}})), measured: matrix.is2D}};
      }} catch (_) {{
        return {{points: null, measured: false}};
      }}
    }};
    const rotation = (style) => {{
      try {{ const matrix = new DOMMatrixReadOnly(style.transform === 'none' ? undefined : style.transform); return Math.atan2(matrix.b, matrix.a) * 180 / Math.PI; }} catch (_) {{ return null; }}
    }};
    const connectorGeometry = (node) => {{
      const polyline = node.matches('svg')
        ? node.querySelector('polyline[data-slidespec-connector="true"]')
        : null;
      if (!polyline) return undefined;
      try {{
        const matrix = polyline.getScreenCTM();
        const list = polyline.points;
        if (!matrix || !list || list.numberOfItems < 2) return {{measured: false}};
        const pointsPx = Array.from({{length: list.numberOfItems}}, (_, index) => {{
          const point = list.getItem(index);
          const transformed = new DOMPoint(point.x, point.y).matrixTransform(matrix);
          return {{x: transformed.x, y: transformed.y}};
        }});
        return {{
          measured: true,
          pointsPx,
          startPx: pointsPx[0],
          endPx: pointsPx[pointsPx.length - 1]
        }};
      }} catch (_) {{
        return {{measured: false}};
      }}
    }};
    const allQids = Array.from(document.querySelectorAll('[data-qid]'));
    const rectDelta = (left, right) => Math.max(
      Math.abs(left.x - right.x), Math.abs(left.y - right.y),
      Math.abs(left.width - right.width), Math.abs(left.height - right.height)
    );
    const staticTransformProbe = (node, style) => {{
      if (style.position !== 'static' || style.transform !== 'none' || !node.parentElement) return null;
      const siblings = Array.from(node.parentElement.children).filter((item) => item !== node);
      if (siblings.length > 64) return {{
        source: 'chrome', telemetryComplete: false, position: 'static',
        transformComposable: false, layoutIsolation: false,
        reason: 'static_translation.sibling_limit'
      }};
      const plainRect = (rect) => ({{x: rect.x, y: rect.y, width: rect.width, height: rect.height}});
      const targetBefore = node.getBoundingClientRect();
      const parentBefore = node.parentElement.getBoundingClientRect();
      const siblingsBefore = siblings.map((item) => item.getBoundingClientRect());
      let containment = {{left: 0, top: 0, right: innerWidth, bottom: innerHeight}};
      let unsupportedReason = null;
      for (let current = node; current; current = current.parentElement) {{
        const currentStyle = getComputedStyle(current);
        const mask = currentStyle.maskImage || currentStyle.webkitMaskImage || 'none';
        if (currentStyle.clipPath !== 'none' || mask !== 'none') unsupportedReason ||= 'repair.static_translation.non_rectangular_clip';
        if (currentStyle.filter !== 'none' || currentStyle.backdropFilter !== 'none') unsupportedReason ||= 'repair.static_translation.filter_bounds';
        if (current !== node && currentStyle.transform !== 'none') unsupportedReason ||= 'repair.static_translation.ancestor_transform';
        if (['hidden', 'clip', 'scroll', 'auto'].includes(currentStyle.overflowX) ||
            ['hidden', 'clip', 'scroll', 'auto'].includes(currentStyle.overflowY)) {{
          const rect = current.getBoundingClientRect();
          containment = {{
            left: Math.max(containment.left, rect.left), top: Math.max(containment.top, rect.top),
            right: Math.min(containment.right, rect.right), bottom: Math.min(containment.bottom, rect.bottom)
          }};
        }}
      }}
      const descendantStacking = Array.from(node.querySelectorAll('*')).some((item) => {{
        const childStyle = getComputedStyle(item);
        return childStyle.position !== 'static' && childStyle.zIndex !== 'auto';
      }});
      if (descendantStacking) unsupportedReason ||= 'repair.static_translation.descendant_stacking_context';
      const previousValue = node.style.getPropertyValue('transform');
      const previousPriority = node.style.getPropertyPriority('transform');
      let targetAfter, parentAfter, siblingsAfter;
      try {{
        node.style.setProperty('transform', 'translate(1px, 1px)', 'important');
        targetAfter = node.getBoundingClientRect();
        parentAfter = node.parentElement.getBoundingClientRect();
        siblingsAfter = siblings.map((item) => item.getBoundingClientRect());
      }} finally {{
        if (previousValue) node.style.setProperty('transform', previousValue, previousPriority);
        else node.style.removeProperty('transform');
      }}
      const translationErrorPx = Math.max(
        Math.abs((targetAfter.x - targetBefore.x) - 1),
        Math.abs((targetAfter.y - targetBefore.y) - 1),
        Math.abs(targetAfter.width - targetBefore.width),
        Math.abs(targetAfter.height - targetBefore.height)
      );
      const flowImpactPx = rectDelta(parentBefore, parentAfter);
      const maxSiblingDeltaPx = siblingsAfter.reduce(
        (largest, item, index) => Math.max(largest, rectDelta(siblingsBefore[index], item)), 0
      );
      const isolated = translationErrorPx <= .25 && flowImpactPx <= .25 && maxSiblingDeltaPx <= .25;
      const safeTranslationIntervalPx = {{
        minX: containment.left - targetBefore.left,
        maxX: containment.right - targetBefore.right,
        minY: containment.top - targetBefore.top,
        maxY: containment.bottom - targetBefore.bottom
      }};
      return {{
        source: 'chrome', telemetryComplete: true, position: 'static',
        transformComposable: unsupportedReason === null, layoutIsolation: isolated,
        translationErrorPx, flowImpactPx, maxSiblingDeltaPx,
        targetBoundsPx: plainRect(targetBefore), parentBoundsPx: plainRect(parentBefore),
        containmentBoundsPx: {{x: containment.left, y: containment.top, width: Math.max(0, containment.right - containment.left), height: Math.max(0, containment.bottom - containment.top)}},
        safeTranslationIntervalPx,
        siblingBoundsPx: siblingsBefore.map(plainRect),
        stackingStable: !descendantStacking,
        unsupportedReason
      }};
    }};
    const pointInPolygon = (x, y, points) => {{
      if (!points || points.length < 3) return true;
      let inside = false;
      for (let i = 0, j = points.length - 1; i < points.length; j = i++) {{
        const a = points[i], b = points[j];
        if (((a.y > y) !== (b.y > y)) && x < (b.x - a.x) * (y - a.y) / ((b.y - a.y) || 1e-9) + a.x) inside = !inside;
      }}
      return inside;
    }};
    const occlusion = (node, rect, points, textRects, surfaceRects) => {{
      const qid = node.getAttribute('data-qid');
      const sensorVersion = '{OCCLUSION_SENSOR_VERSION}';
      const evidence = (status, reason, details = {{}}) => ({{
        qid, metric: 'occlusion', sensor_version: sensorVersion,
        source_digest: '{source_digest}', render_digest: '{render_digest}',
        cache_key: [qid, 'occlusion', sensorVersion, '{source_digest}', '{render_digest}'].join('\u0000'),
        stage: 'chrome.owned_paint_sampling', status, reason, ...details
      }});
      const empty = (values, status, reason) => ({{
        fraction: values.fraction, continuous: values.continuous,
        step: values.step, unknown: values.unknown,
        sampled: 0, known: 0, coarseSampled: 0, ownedRectSampled: 0,
        covered: values.covered || 0, occluderQids: [], unbound: 0,
        evidence: evidence(status, reason, {{sampled: 0, known: 0, covered: values.covered || 0}})
      }});
      if (rect.width <= 0 || rect.height <= 0) return empty(
        {{fraction: 1, continuous: 1, step: 0, unknown: 0, covered: 0}},
        'measured', 'degenerate_target_bbox_assumed_occluded');
      const ownedPaintRects = [
        ...surfaceRects.map((paintRect, index) => ({{paintRect, group: `surface:${{index}}`}})),
        ...textRects.map((paintRect, index) => ({{paintRect, group: `text:${{index}}`}}))
      ];
      if (!ownedPaintRects.length) return empty(
        {{fraction: 0, continuous: 0, step: 0, unknown: 0}},
        'not_applicable', 'no_owned_paint');
      // Preserve the parent grid for broad/continuous-cover estimates.  Then
      // independently seed every owned surface and every text fragment.  A
      // full-width footer with a 14px page-number glyph can therefore no longer
      // fall between all 40 parent-grid columns and become sampled=0.
      const cols = Math.max(5, Math.min(40, Math.ceil(rect.width / 4)));
      const rows = Math.max(5, Math.min(40, Math.ceil(rect.height / 4)));
      const cells = Array.from({{length: rows}}, () => Array(cols).fill(-1));
      const samples = [];
      const addSample = (x, y, group, origin) => {{
        if (!pointInPolygon(x, y, points)) return false;
        const row = Math.max(0, Math.min(rows - 1, Math.floor((y - rect.top) / rect.height * rows)));
        const col = Math.max(0, Math.min(cols - 1, Math.floor((x - rect.left) / rect.width * cols)));
        samples.push({{x, y, row, col, group, origin, state: -1}});
        return true;
      }};
      for (let row = 0; row < rows; row++) for (let col = 0; col < cols; col++) {{
        const x = rect.left + rect.width * (col + .5) / cols;
        const y = rect.top + rect.height * (row + .5) / rows;
        if (!ownedPaintRects.some((item) => pointInRect(x, y, item.paintRect))) continue;
        addSample(x, y, null, 'coarse');
      }}
      for (const {{paintRect, group}} of ownedPaintRects) {{
        const localCols = Math.max(1, Math.min(3, Math.ceil(paintRect.width / 4)));
        const localRows = Math.max(1, Math.min(3, Math.ceil(paintRect.height / 4)));
        let added = false;
        for (let row = 0; row < localRows; row++) for (let col = 0; col < localCols; col++) {{
          const x = paintRect.x + paintRect.width * (col + .5) / localCols;
          const y = paintRect.y + paintRect.height * (row + .5) / localRows;
          added = addSample(x, y, group, 'owned-rect') || added;
        }}
        // Rotated fragment bounding boxes can put every local grid point just
        // outside the owner quad.  Try the physical rect center explicitly and
        // keep a concrete no-sample reason if even that is outside.
        if (!added) addSample(
          paintRect.x + paintRect.width / 2,
          paintRect.y + paintRect.height / 2,
          group, 'owned-rect-center');
      }}
      let covered = 0, unknown = 0, unbound = 0;
      const occluderQids = new Set();
      const groupCovered = new Map();
      for (const sample of samples) {{
        const {{x, y, row, col, group}} = sample;
        if (x < 0 || y < 0 || x >= innerWidth || y >= innerHeight) {{
          sample.state = 1; cells[row][col] = 1; covered += 1;
          if (group !== null) groupCovered.set(group, (groupCovered.get(group) || 0) + 1);
          continue;
        }}
        let hitX = x, hitY = y;
        let stack = document.elementsFromPoint(hitX, hitY);
        let owner = stack.findIndex((candidate) => candidate === node || node.contains(candidate));
        // Anti-aliased glyph/border edge samples can miss the owner by a
        // subpixel even though the adjacent owned paint is measurable. Retry
        // two bounded points toward the target centre before declaring an
        // unknown atom; the normal occlusion threshold remains unchanged.
        if (owner < 0) {{
          for (const inset of [.5, 1]) {{
            hitX = x + Math.sign(rect.left + rect.width / 2 - x) * inset;
            hitY = y + Math.sign(rect.top + rect.height / 2 - y) * inset;
            stack = document.elementsFromPoint(hitX, hitY);
            owner = stack.findIndex((candidate) => candidate === node || node.contains(candidate));
            if (owner >= 0) break;
          }}
        }}
        if (owner < 0) {{
          sample.state = -2; if (cells[row][col] === -1) cells[row][col] = -2;
          unknown += 1; continue;
        }}
        const occluders = stack.slice(0, owner).filter((candidate) =>
          candidate !== node && !node.contains(candidate) && candidatePaintsAt(candidate, hitX, hitY));
        const isCovered = occluders.length > 0;
        for (const candidate of occluders) {{
          const ownerNode = candidate.closest?.('[data-qid]');
          const ownerQid = ownerNode?.getAttribute('data-qid');
          if (ownerQid) occluderQids.add(ownerQid); else unbound += 1;
        }}
        sample.state = isCovered ? 1 : 0;
        if (isCovered) {{
          cells[row][col] = 1; covered += 1;
          if (group !== null) groupCovered.set(group, (groupCovered.get(group) || 0) + 1);
        }} else if (cells[row][col] !== 1) cells[row][col] = 0;
      }}
      const sampled = samples.length;
      const known = sampled - unknown;
      const coarseSampled = samples.filter((sample) => sample.origin === 'coarse').length;
      const ownedRectSampled = sampled - coarseSampled;
      const common = {{
        step: Math.max(rect.width / cols, rect.height / rows),
        unknown: sampled ? unknown / sampled : 1,
        sampled, known, coarseSampled, ownedRectSampled, covered,
        occluderQids: Array.from(occluderQids).sort(), unbound
      }};
      if (!sampled) return {{
        fraction: null, continuous: null, ...common,
        evidence: evidence('unknown', 'no_sample_on_owned_paint', {{sampled, known, covered}})
      }};
      if (!known || unknown / sampled > .05) return {{
        fraction: null, continuous: null, ...common,
        evidence: evidence('unknown', !known ? 'owner_absent_from_hit_test' : 'excessive_unknown_hit_test', {{
          sampled, known, covered, known_occluded_fraction: known ? covered / known : null
        }})
      }};
      let largest = 0;
      const seen = new Set();
      for (let row = 0; row < rows; row++) for (let col = 0; col < cols; col++) {{
        const key = `${{row}}:${{col}}`;
        if (cells[row][col] !== 1 || seen.has(key)) continue;
        let size = 0; const queue = [[row, col]]; seen.add(key);
        while (queue.length) {{
          const [r, c] = queue.pop(); size += 1;
          for (const [nr, nc] of [[r-1,c],[r+1,c],[r,c-1],[r,c+1]]) {{
            const next = `${{nr}}:${{nc}}`;
            if (nr >= 0 && nr < rows && nc >= 0 && nc < cols && cells[nr][nc] === 1 && !seen.has(next)) {{ seen.add(next); queue.push([nr,nc]); }}
          }}
        }}
        largest = Math.max(largest, size);
      }}
      const largestOwnedGroup = Math.max(0, ...groupCovered.values());
      const fraction = covered / known;
      return {{
        fraction, continuous: Math.max(largest, largestOwnedGroup) / known, ...common,
        evidence: evidence('measured', null, {{sampled, known, covered, known_occluded_fraction: fraction}})
      }};
    }};
    const elements = allQids.map((node, domIndex) => {{
      const rect = node.getBoundingClientRect();
      const style = getComputedStyle(node);
      const offsetParent = node.offsetParent;
      const offsetParentQid = offsetParent?.getAttribute?.('data-qid') || null;
      const offsetParentRect = offsetParent?.getBoundingClientRect?.();
      const staticTranslationProbe = staticTransformProbe(node, style);
      const glyph = glyphClip(node);
      const contrast = contrastRatio(node, style);
      const textRects = ownedTextRects(node);
      const surfaceRects = ownedSurfaceRects(node);
      const q = quad(node, rect);
      const occultation = occlusion(node, rect, q.points, textRects, surfaceRects);
      const connector = connectorGeometry(node);
      const effectValues = {{
        transform: style.transform, filter: style.filter, backdropFilter: style.backdropFilter,
        mixBlendMode: style.mixBlendMode, clipPath: style.clipPath,
        maskImage: style.maskImage || style.webkitMaskImage || 'none'
      }};
      const complexEffect = Object.entries(effectValues).some(([name, value]) =>
        value && value !== 'none' && !(name === 'mixBlendMode' && value === 'normal'));
      const explicitZ = Number.parseInt(style.zIndex, 10);
      let effectiveOpacity = 1;
      let cssVisible = true;
      for (let ancestor = node; ancestor; ancestor = ancestor.parentElement) {{
        const ancestorStyle = getComputedStyle(ancestor);
        const opacity = Number.parseFloat(ancestorStyle.opacity || '1');
        if (Number.isFinite(opacity)) effectiveOpacity *= opacity;
        if (ancestorStyle.display === 'none' ||
            ancestorStyle.visibility === 'hidden' ||
            ancestorStyle.visibility === 'collapse') cssVisible = false;
      }}
      const intersectionWidth = Math.max(0, Math.min(rect.right, innerWidth) - Math.max(rect.left, 0));
      const intersectionHeight = Math.max(0, Math.min(rect.bottom, innerHeight) - Math.max(rect.top, 0));
      const viewportIntersectionRatio = rect.width > 0 && rect.height > 0
        ? (intersectionWidth * intersectionHeight) / (rect.width * rect.height)
        : 0;
      const image = node.tagName === 'IMG' ? {{
        src: node.currentSrc || node.getAttribute('src') || '', complete: Boolean(node.complete),
        naturalWidth: Number(node.naturalWidth || 0), naturalHeight: Number(node.naturalHeight || 0),
        cssVisible, effectiveOpacity, viewportIntersectionRatio
      }} : undefined;
      let fontLoaded = true;
      try {{ fontLoaded = !document.fonts || document.fonts.check(style.font); }} catch (_) {{}}
      return {{
        qid: node.getAttribute('data-qid'), tag: node.tagName.toLowerCase(),
        semantic_role: node.getAttribute('data-semantic-role') || node.getAttribute('data-role') || node.getAttribute('role') || null,
        source_block_id: node.getAttribute('data-source-block-id'),
        css_visible: cssVisible, effective_opacity: effectiveOpacity,
        viewport_intersection_ratio: viewportIntersectionRatio,
        bbox: {{x: rect.x, y: rect.y, width: rect.width, height: rect.height}},
        clientWidth: node.clientWidth, clientHeight: node.clientHeight,
        scrollWidth: node.scrollWidth, scrollHeight: node.scrollHeight,
        rendered_text: node.innerText || node.textContent || '',
        parent_qid: node.parentElement?.closest('[data-qid]')?.getAttribute('data-qid') || null,
        offset_parent_qid: offsetParentQid,
        containing_block_proof: {{
          source: 'chrome', telemetryComplete: true,
          exactBoundOffsetParent: Boolean(offsetParent && offsetParentQid),
          offsetParentQid,
          offsetParentTag: offsetParent?.tagName?.toLowerCase?.() || null,
          offsetParentBoundsPx: offsetParentRect ? {{
            x: offsetParentRect.x, y: offsetParentRect.y,
            width: offsetParentRect.width, height: offsetParentRect.height
          }} : null
        }},
        clipped: glyph.glyph || (['hidden', 'clip'].includes(style.overflowX) && node.scrollWidth > node.clientWidth + 1) ||
          (['hidden', 'clip'].includes(style.overflowY) && node.scrollHeight > node.clientHeight + 1),
        glyph_clipped: glyph.glyph, line_box_clipped: glyph.line,
        glyph_clip_cause: glyph.cause,
        has_owned_text: textRects.length > 0,
        has_owned_paint: surfaceRects.length > 0 || textRects.length > 0,
        quad: q.points, rotation_degrees: q.measured ? rotation(style) : null,
        occluded_fraction: occultation.fraction,
        continuous_occluded_fraction: occultation.continuous,
        occlusion_sample_step_px: occultation.step,
        occlusion_sample_count: occultation.sampled,
        occlusion_known_sample_count: occultation.known,
        occlusion_coarse_sample_count: occultation.coarseSampled,
        occlusion_owned_rect_sample_count: occultation.ownedRectSampled,
        occlusion_confirmed_covered_sample_count: occultation.covered,
        occlusion_unknown_fraction: occultation.unknown,
        // Compatibility contract consumed by existing detectors.  The concrete
        // sensor revision is separate so caches invalidate without making the
        // proven owned-paint-v3 payload shape unreadable during rollout.
        occlusion_probe_version: '{OCCLUSION_PROBE_CONTRACT_VERSION}',
        occlusion_sensor_version: '{OCCLUSION_SENSOR_VERSION}',
        occlusion_measurement_evidence: occultation.evidence,
        occlusion_measurement_error: occultation.fraction === null ? occultation.evidence.reason : null,
        occlusion_occluder_qids: occultation.occluderQids,
        occlusion_unbound_count: occultation.unbound,
        effective_z_index: (Number.isFinite(explicitZ) ? explicitZ * 100000 : 0) + domIndex,
        contrast_ratio: contrast,
        text_fragments: textRects,
        requires_pixel_contrast: textRects.length > 0 && contrast === null,
        visual_effects: effectValues,
        measurement: {{geometry: true, transform: q.measured, clipping: true, contrast: contrast !== null, occlusion: occultation.fraction !== null, stacking: true, visual_effects: !complexEffect}},
        intentional: node.hasAttribute('data-allow-overlap') || node.hasAttribute('data-intentional-crop'),
        unintentional: node.hasAttribute('data-quality-unintentional'),
        font_loaded: fontLoaded, image,
        connector_geometry: connector,
        static_translation_probe: staticTranslationProbe,
        font_size: px(style.fontSize),
        computed_style: {{
          color: style.color, backgroundColor: style.backgroundColor,
          backgroundImage: style.backgroundImage, backgroundBlendMode: style.backgroundBlendMode,
          mixBlendMode: style.mixBlendMode, filter: style.filter,
          backdropFilter: style.backdropFilter, fontSize: style.fontSize,
          fontFamily: style.fontFamily, fontWeight: style.fontWeight, lineHeight: style.lineHeight,
          textAlign: style.textAlign, overflow: style.overflow,
          overflowX: style.overflowX, overflowY: style.overflowY, objectFit: style.objectFit,
          objectPosition: style.objectPosition, position: style.position,
          left: style.left, top: style.top, right: style.right, bottom: style.bottom,
          width: style.width, height: style.height,
          minWidth: style.minWidth, maxWidth: style.maxWidth,
          minHeight: style.minHeight, maxHeight: style.maxHeight,
          boxSizing: style.boxSizing, transform: style.transform,
          transformOrigin: style.transformOrigin, display: style.display,
          visibility: style.visibility, opacity: style.opacity, zIndex: style.zIndex,
          gap: style.gap, rowGap: style.rowGap, columnGap: style.columnGap,
          paddingTop: style.paddingTop, paddingRight: style.paddingRight,
          paddingBottom: style.paddingBottom, paddingLeft: style.paddingLeft,
          alignItems: style.alignItems, justifyContent: style.justifyContent,
          gridTemplateColumns: style.gridTemplateColumns, gridColumn: style.gridColumn,
          gridRow: style.gridRow, whiteSpace: style.whiteSpace,
          overflowWrap: style.overflowWrap, textOverflow: style.textOverflow,
          flexDirection: style.flexDirection
        }}
      }};
    }});
    const payload = {{
      width: window.innerWidth, height: window.innerHeight,
      fonts_ready: !document.fonts || document.fonts.status === 'loaded',
      source_digest: '{source_digest}', render_digest: '{render_digest}',
      dom_metrics_version: '{DOM_METRICS_VERSION}', metrics_cache_key: '{key}', elements
    }};
    let marker = document.getElementById('{_METRICS_ELEMENT_ID}');
    if (!marker) {{ marker = document.createElement('meta'); marker.id = '{_METRICS_ELEMENT_ID}'; document.head.appendChild(marker); }}
    marker.setAttribute('content', encodeURIComponent(JSON.stringify(payload)));
  }};
  const failed = (error) => {{
    let marker = document.getElementById('{_METRICS_ELEMENT_ID}');
    if (!marker) {{ marker = document.createElement('meta'); marker.id = '{_METRICS_ELEMENT_ID}'; document.head.appendChild(marker); }}
    marker.setAttribute('content', encodeURIComponent(JSON.stringify({{width: innerWidth, height: innerHeight, fonts_ready: false, elements: [], probe_error: String(error && (error.stack || error.message) || error)}})));
  }};
  const run = () => {{
    try {{ done(); }} catch (error) {{ failed(error); }}
    // Publish synchronously so --dump-dom can never race an unresolved font
    // promise.  If web fonts settle within Chrome's virtual-time budget, refresh
    // the same marker with final glyph geometry before the dump/screenshot.
    try {{ if (document.fonts && document.fonts.ready) document.fonts.ready.then(done).catch(failed); }} catch (_) {{}}
  }};
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', run, {{once: true}}); else run();
}})();
</script>"""
    closing = re.search(r"</body\s*>", enriched, flags=re.IGNORECASE)
    if closing:
        enriched = enriched[: closing.start()] + script + enriched[closing.start() :]
    else:
        enriched += script
    if issue_capability:
        _register_internal_render_source(enriched)
    return enriched, key


def _extract_browser_metrics(dumped_dom: bytes) -> Mapping[str, Any] | None:
    if not dumped_dom:
        return None
    text = dumped_dom.decode("utf-8", errors="replace")
    match = _METRICS_PATTERN.search(text)
    if not match:
        return None
    try:
        raw = urllib.parse.unquote(html_lib.unescape(match.group(2)))
        value = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return value if isinstance(value, Mapping) else None


from utils.slide_capture_pixel_probe import (
    _append_probe_style,
    _enrich_pixel_contrast,
    _pixel_probe_document,
    _pixel_ratio,
    _relative_luminance,
)



def _peek_cached_browser_metrics(html: str) -> Mapping[str, Any] | None:
    key = _metrics_cache_key(html)
    metrics = _browser_metrics_cache.get(key)
    if metrics is not None:
        _browser_metrics_cache.move_to_end(key)
    return metrics


def get_cached_browser_metrics(html: str) -> Mapping[str, Any] | None:
    """Return metrics captured by the same Chrome invocation as the PNG render."""
    metrics = _peek_cached_browser_metrics(html)
    if metrics is not None:
        _count_runtime("metrics_cache_hits")
    return metrics


def _cache_browser_metrics(html: str, metrics: Mapping[str, Any]) -> None:
    """Store metrics under the same authored HTML identity as its rendered PNG."""
    # The in-page probe runs over nonce-bearing admitted HTML, but its source
    # identity is the exact pre-admission render request (the same contract as
    # ``AdmittedHtml.source_digest`` and ``RenderCapture.html_digest``).
    # Preserve the admitted/instrumented projection separately as render_digest.
    metrics = {
        **dict(metrics),
        "source_digest": "sha256:" + hashlib.sha256(html.encode("utf-8")).hexdigest(),
    }
    key = _metrics_cache_key(html)
    _browser_metrics_cache[key] = metrics
    _browser_metrics_cache.move_to_end(key)
    try:
        metric_bytes = len(json.dumps(metrics, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8"))
    except Exception:
        metric_bytes = 0
    _count_runtime("metrics_cache_bytes", metric_bytes)
    while len(_browser_metrics_cache) > DOM_METRICS_CACHE_SIZE:
        _browser_metrics_cache.popitem(last=False)


async def ensure_browser_metrics(
    html: str, timeout: int = 60
) -> Mapping[str, Any] | None:
    """Return browser telemetry for ``html``, recollecting it when a PNG outlives it.

    PNG and DOM caches have different retention limits by design, so a render-cache
    hit cannot assume the corresponding probe payload remains resident.  Recollect
    with the exact instrumented render path when necessary.  A failed extraction is
    intentionally returned as ``None``; the DOM inspector will escalate it instead
    of interpreting it as a clean slide.
    """
    cached = get_cached_browser_metrics(html)
    if cached is not None:
        return cached

    # A cache miss means the PNG outlived its DOM telemetry and requires a
    # second browser pass; expose that duplicate work in benchmark evidence.
    _count_runtime("metrics_recollection_requests")

    key = _metrics_cache_key(html)
    task = _browser_metrics_inflight.get(key)
    if task is None:
        async def recollect() -> Mapping[str, Any] | None:
            if renderer_requires_external_worker():
                # Production keeps the renderer isolation boundary: the
                # external worker owns both pixels and browser telemetry.
                await _render_html_to_png_uncached(html, timeout=timeout)
            else:
                # A retry after process recovery has no in-memory metrics even
                # when its authored PNG checkpoint survived.  Do not route this
                # bounded recollection through the shared CDP runtime: a page
                # waiting on fonts can poison that process and strand every
                # gathered slide.  The one-shot headless-shell path has its own
                # profile, process-tree timeout, virtual-time budget, and the
                # same instrumented DOM payload used by the primary renderer.
                admitted = _admit_render_source(html)
                await _render_html_to_png_subprocess(
                    admitted.html,
                    timeout=timeout,
                    admitted_source=True,
                    cache_html=html,
                    script_nonce=admitted.nonce,
                )
            return _peek_cached_browser_metrics(html)

        task = asyncio.create_task(recollect())
        _browser_metrics_inflight[key] = task
    try:
        # The renderer owns its own timeout, but a wedged browser semaphore or
        # detached Windows Chrome child can otherwise leave this shared task
        # pending forever and block the entire FastAPI event loop during
        # canonical Scene publication.  Bound the outer wait as well and cancel
        # the poisoned in-flight entry so a later retry can recollect cleanly.
        return await asyncio.wait_for(task, timeout=max(1, timeout) + 15)
    except Exception as error:
        # Rendering an already-cached PNG remains useful even if a best-effort
        # telemetry recollection cannot start.  Consumers receive ``None`` and
        # route the slide to detailed VQA rather than seeing a false clean result.
        LOGGER.warning("authored DOM metrics recollection failed: %s", error)
        return None
    finally:
        if _browser_metrics_inflight.get(key) is task and task.done():
            _browser_metrics_inflight.pop(key, None)


async def render_html_to_png_with_metrics(
    html: str, timeout: int = 60
) -> "RenderCapture":
    """Render one slide and return telemetry tied to the same HTML cache identity.

    This is the preferred API for quality-review callers.  It preserves the legacy
    bytes-only renderer while making cache divergence explicit and recoverable.
    """
    # Preserve renderer admission semantics even when the atomic result cache
    # contains a prior successful capture.
    ensure_renderer_enabled()
    html_digest = "sha256:" + hashlib.sha256(html.encode("utf-8")).hexdigest()
    cached_capture = _peek_cached_capture(html, timeout)
    if cached_capture is not None:
        _count_runtime("requests")
        _count_runtime("capture_cache_hits")
        _count_runtime("capture_results")
        if cached_capture.browser_metrics is not None:
            _count_runtime("capture_metric_results")
        return RenderCapture.from_payload(
            html_digest=cached_capture.html_digest,
            png=cached_capture.png,
            browser_metrics=cached_capture.browser_metrics,
            runtime_counters=get_render_runtime_counters(),
        )
    cached_metrics = _peek_cached_browser_metrics(html)
    png = await render_html_to_png(html, timeout=timeout)
    if cached_metrics is not None:
        _count_runtime("metrics_cache_hits")
        metrics = cached_metrics
    else:
        captured_metrics = _peek_cached_browser_metrics(html)
        if captured_metrics is not None:
            metrics = captured_metrics
        else:
            metrics = await ensure_browser_metrics(html, timeout=timeout)
    _count_runtime("capture_results")
    _count_runtime("capture_png_bytes", len(png))
    if metrics is not None:
        _count_runtime("capture_metric_results")
    capture = RenderCapture.from_payload(
        html_digest=html_digest, png=png, browser_metrics=metrics,
        runtime_counters=get_render_runtime_counters(),
    )
    _cache_capture(html, timeout, capture)
    return capture


@dataclass(frozen=True, slots=True)
class RenderCapture:
    """Atomic result for one HTML render.

    ``html_digest``, PNG bytes and browser metrics are produced/selected as one
    value so callers cannot accidentally pair telemetry from a different HTML
    revision.  ``__iter__`` retains the historical ``png, metrics = ...`` API.
    ``runtime_counters`` is a snapshot for measuring duplicate browser/I/O work
    across large decks without retaining process objects in the result.
    """

    html_digest: str
    png: bytes
    browser_metrics: Mapping[str, Any] | None
    png_digest: str
    metrics_digest: str | None
    runtime_counters: Mapping[str, int]

    @classmethod
    def from_payload(
        cls,
        *,
        html_digest: str,
        png: bytes,
        browser_metrics: Mapping[str, Any] | None,
        runtime_counters: Mapping[str, int],
    ) -> "RenderCapture":
        canonical = json.dumps(
            browser_metrics, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, default=str,
        ).encode("utf-8") if browser_metrics is not None else None
        return cls(
            html_digest=html_digest,
            png=png,
            browser_metrics=browser_metrics,
            png_digest="sha256:" + hashlib.sha256(png).hexdigest(),
            metrics_digest=("sha256:" + hashlib.sha256(canonical).hexdigest()) if canonical is not None else None,
            runtime_counters=dict(runtime_counters),
        )

    def __iter__(self):
        # Backward compatible with all existing callers.
        yield self.png
        yield self.browser_metrics

    @property
    def metrics(self) -> Mapping[str, Any] | None:
        """Short alias for callers that refer to browser telemetry as metrics."""
        return self.browser_metrics

    def as_dict(self, *, include_payload: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {
            "htmlDigest": self.html_digest,
            "pngDigest": self.png_digest,
            "metricsDigest": self.metrics_digest,
            "browserMetricsCaptured": self.browser_metrics is not None,
            "runtimeCounters": dict(self.runtime_counters),
        }
        if include_payload:
            result["pngBase64"] = base64.b64encode(self.png).decode("ascii")
            result["browserMetrics"] = self.browser_metrics
        return result


def _capture_cache_key(html: str, timeout: int) -> str:
    # Timeout only bounds work; it does not alter a successful deterministic
    # render.  Excluding it lets retries with a larger budget reuse the same
    # atomic capture instead of relaunching Chromium.
    return hashlib.sha256(
        f"{RENDER_CACHE_VERSION}\0{DOM_METRICS_VERSION}\0{OCCLUSION_SENSOR_VERSION}\0{html}".encode("utf-8")
    ).hexdigest()


def _peek_cached_capture(html: str, timeout: int) -> RenderCapture | None:
    key = _capture_cache_key(html, timeout)
    capture = _capture_cache.get(key)
    if capture is not None:
        _capture_cache.move_to_end(key)
    return capture


def _cache_capture(html: str, timeout: int, capture: RenderCapture) -> None:
    # Keep only a small atomic-result tail.  PNG bytes are shared with the render
    # LRU by reference, while this tail protects the corresponding metrics from
    # DOM-cache eviction without retaining an unbounded second image cache.
    # A missing metrics payload is an explicit unknown and must remain
    # retryable; caching that state would permanently suppress a later
    # recollection after a transient browser probe failure.
    if CAPTURE_CACHE_SIZE <= 0 or capture.browser_metrics is None:
        return
    key = _capture_cache_key(html, timeout)
    _capture_cache[key] = capture
    _capture_cache.move_to_end(key)
    while len(_capture_cache) > CAPTURE_CACHE_SIZE:
        _capture_cache.popitem(last=False)


async def _wait_for_output(out: str, timeout: float = 15.0) -> bool:
    """Poll for the --screenshot file after the chrome process we waited on exits.
    On Windows chrome.exe is a thin launcher: it spawns the real browser child and
    returns, so `communicate()` can complete BEFORE the detached child finishes
    writing the screenshot. Checking once immediately races that write and spuriously
    reports 'no output file' (→ blank placeholder). Poll briefly instead."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if os.path.isfile(out) and os.path.getsize(out) > 0:
            return True
        await asyncio.sleep(0.25)
    return os.path.isfile(out) and os.path.getsize(out) > 0


from utils.slide_capture_chrome_discovery import _playwright_headless_shell_candidates, find_chrome



_runtime_counter_lock = threading.Lock()
_runtime_counters: dict[str, int] = {
    "requests": 0,
    "render_cache_hits": 0,
    "metrics_cache_hits": 0,
    "capture_cache_hits": 0,
    "metrics_recollection_requests": 0,
    "chrome_process_launches": 0,
    "session_reuse_hits": 0,
    "session_restarts": 0,
    "page_sessions_opened": 0,
    "page_sessions_closed": 0,
    # Capture-level counters make duplicate browser collection and payload I/O
    # measurable for 32/80+ slide decks without exposing mutable cache state.
    "capture_results": 0,
    "capture_metric_results": 0,
    "capture_png_bytes": 0,
    "render_cache_bytes": 0,
    "metrics_cache_bytes": 0,
}
_chrome_identity_cache: tuple[str, int, int, str] | None = None


def _count_runtime(name: str, amount: int = 1) -> None:
    with _runtime_counter_lock:
        _runtime_counters[name] = _runtime_counters.get(name, 0) + amount


def get_render_runtime_counters() -> dict[str, int]:
    """Return a read-only snapshot of cache/process/session activity."""

    with _runtime_counter_lock:
        snapshot = dict(_runtime_counters)
    # Current resident payload size is intentionally derived outside the counter
    # lock; cache mutation is event-loop serialized and these values are only
    # benchmark evidence, never admission decisions.
    snapshot["render_cache_entries"] = len(_render_cache)
    snapshot["render_cache_bytes_current"] = sum(len(value) for value in _render_cache.values())
    snapshot["metrics_cache_entries"] = len(_browser_metrics_cache)
    snapshot["metrics_cache_bytes_current"] = sum(
        len(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8"))
        for value in _browser_metrics_cache.values()
    )
    snapshot["capture_cache_entries"] = len(_capture_cache)
    snapshot["capture_cache_bytes_current"] = sum(
        len(value.png) for value in _capture_cache.values()
    )
    return snapshot


def chrome_runtime_digest() -> str:
    """Digest the resolved Chrome binary identity used by incremental caches."""

    global _chrome_identity_cache
    chrome = find_chrome()
    if not chrome:
        return "sha256:" + hashlib.sha256(b"chrome:unavailable").hexdigest()
    path = pathlib.Path(chrome).resolve()
    try:
        stat = path.stat()
        size, modified = int(stat.st_size), int(stat.st_mtime_ns)
    except OSError:
        size, modified = -1, -1
    cached = _chrome_identity_cache
    if cached is not None and cached[:3] == (str(path), size, modified):
        return cached[3]
    try:
        completed = subprocess.run(
            [str(path), "--version"],
            capture_output=True,
            check=False,
            timeout=10,
            creationflags=(
                getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            ),
        )
        version = (completed.stdout or completed.stderr).decode(
            "utf-8", errors="replace"
        ).strip()
    except Exception:
        version = "version-unavailable"
    payload = f"{path}\0{size}\0{modified}\0{version}".encode("utf-8")
    result = "sha256:" + hashlib.sha256(payload).hexdigest()
    _chrome_identity_cache = (str(path), size, modified, result)
    return result


class _ReusableChromeRuntime:
    """One isolated Chrome process serving bounded, disposable CDP pages."""

    def __init__(
        self,
        process: subprocess.Popen[bytes],
        profile: tempfile.TemporaryDirectory[str],
        port: int,
    ) -> None:
        self.process = process
        self.profile = profile
        self.port = port
        self.session_count = 0

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def alive(self) -> bool:
        return self.process.poll() is None

    def close(self) -> None:
        if self.process.poll() is None:
            _kill_process_tree(self.process)
            try:
                self.process.communicate(timeout=5)
            except Exception:
                pass
        self.profile.cleanup()


_reusable_chrome_lock = threading.Lock()
_reusable_chrome: _ReusableChromeRuntime | None = None


def _launch_reusable_chrome() -> _ReusableChromeRuntime:
    if renderer_requires_external_worker():
        raise RendererPolicyError(
            "production renderer cannot launch in-process reusable Chrome"
        )
    chrome = find_chrome()
    if not chrome:
        raise RuntimeError(
            "No chrome/chromium found for slide capture (set CHROME_PATH)."
        )
    profile = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    profile_path = pathlib.Path(profile.name)
    args = [
        chrome,
        "--headless=new",
        "--disable-gpu",
        "--disable-background-networking",
        "--disable-component-update",
        "--disable-domain-reliability",
        "--disable-sync",
        "--metrics-recording-only",
        "--no-pings",
        "--remote-debugging-port=0",
        "--remote-debugging-address=127.0.0.1",
        f"--user-data-dir={profile_path}",
        "--no-first-run",
        "--no-default-browser-check",
        "--hide-scrollbars",
        "--force-device-scale-factor=1",
        "about:blank",
    ]
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        kwargs["start_new_session"] = True
    process = subprocess.Popen(args, **kwargs)
    port_file = profile_path / "DevToolsActivePort"
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if port_file.is_file():
            try:
                port = int(port_file.read_text(encoding="utf-8").splitlines()[0])
                _count_runtime("chrome_process_launches")
                return _ReusableChromeRuntime(process, profile, port)
            except (OSError, ValueError, IndexError):
                pass
        if process.poll() is not None:
            break
        time.sleep(0.05)
    if process.poll() is None:
        _kill_process_tree(process)
    profile.cleanup()
    raise RuntimeError("reusable Chrome did not publish DevToolsActivePort")


def _get_reusable_chrome_sync() -> _ReusableChromeRuntime:
    global _reusable_chrome
    with _reusable_chrome_lock:
        if _reusable_chrome is not None and _reusable_chrome.alive():
            _reusable_chrome.session_count += 1
            _count_runtime("session_reuse_hits")
            return _reusable_chrome
        if _reusable_chrome is not None:
            _count_runtime("session_restarts")
            _reusable_chrome.close()
        _reusable_chrome = _launch_reusable_chrome()
        _reusable_chrome.session_count = 1
        return _reusable_chrome


def _discard_reusable_chrome_sync(runtime: _ReusableChromeRuntime) -> None:
    global _reusable_chrome
    with _reusable_chrome_lock:
        if _reusable_chrome is runtime:
            _count_runtime("session_restarts")
            runtime.close()
            _reusable_chrome = None


def _close_reusable_chrome() -> None:
    global _reusable_chrome
    with _reusable_chrome_lock:
        runtime, _reusable_chrome = _reusable_chrome, None
    if runtime is not None:
        runtime.close()


atexit.register(_close_reusable_chrome)


async def capture_slides(
    presentation_id: str,
    n_slides: int,
    base_url: str = "http://127.0.0.1:5000",
    timeout: int = 90,
) -> List[bytes]:  # noqa: ASYNC109
    from utils.slide_capture_batch import capture_slides_impl

    return await capture_slides_impl(
        presentation_id,
        n_slides,
        base_url=base_url,
        timeout=timeout,
        env_enabled=env_enabled,
        find_chrome=find_chrome,
        run_chrome_screenshot=_run_chrome_screenshot,
        wait_for_output=_wait_for_output,
    )


async def _cdp_command(
    websocket: aiohttp.ClientWebSocketResponse,
    command_id: int,
    method: str,
    params: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    await websocket.send_json(
        {"id": command_id, "method": method, "params": dict(params or {})}
    )
    while True:
        message = await websocket.receive()
        if message.type != aiohttp.WSMsgType.TEXT:
            raise RuntimeError(f"Chrome DevTools websocket closed during {method}")
        payload = json.loads(message.data)
        if payload.get("id") != command_id:
            continue
        if "error" in payload:
            raise RuntimeError(f"Chrome DevTools {method} failed: {payload['error']}")
        result = payload.get("result", {})
        return result if isinstance(result, Mapping) else {}


from utils.slide_capture_pipe import (
    _ChromePipe,
    _runtime_value,
    _SpawnedProcess,
    _spawn_chrome_pipe_posix,
)



def _render_admitted_html_over_pipe(
    admitted: AdmittedHtml, timeout: int
) -> tuple[bytes, Mapping[str, Any] | None]:
    from utils.slide_capture_pipe_render import render_admitted_html_over_pipe

    return render_admitted_html_over_pipe(
        admitted, timeout,
        blocked_urls=_BLOCKED_RENDERER_URL_PATTERNS,
        chrome_pipe=_ChromePipe,
        find_chrome=find_chrome,
        instrument_html=_instrument_authored_html,
        runtime_value=_runtime_value,
    )


async def _render_html_in_reusable_chrome(
    html: str, timeout: int, *, script_nonce: str | None = None,
    cache_html: str | None = None,
) -> tuple[bytes, Mapping[str, Any] | None]:
    from utils.slide_capture_reusable_render import render_html_in_reusable_chrome

    return await render_html_in_reusable_chrome(
        html, timeout, script_nonce=script_nonce, cache_html=cache_html,
        blocked_urls=_BLOCKED_RENDERER_URL_PATTERNS,
        cache_metrics=_cache_browser_metrics,
        cdp_command=_cdp_command,
        count_runtime=_count_runtime,
        discard_runtime=_discard_reusable_chrome_sync,
        enrich_contrast=_enrich_pixel_contrast,
        get_runtime=_get_reusable_chrome_sync,
        instrument_html=_instrument_authored_html,
        metrics_element_id=_METRICS_ELEMENT_ID,
        pixel_probe_document=_pixel_probe_document,
        runtime_value=_runtime_value,
    )


async def _render_html_to_png_subprocess(
    html: str,
    timeout: int = 60,
    *,
    admitted_source: bool = False,
    cache_html: str | None = None,
    script_nonce: str | None = None,
) -> bytes:
    """Render a self-contained HTML document to an exact 1280x720 PNG via headless
    chrome. Used by the authored-mode pipeline (the HTML is authored in-memory, not
    served from /pdf-maker). Raises RuntimeError if no chrome is found or the
    screenshot fails.

    Bounded by the process-wide render semaphore (AUTHORED_RENDER_CONCURRENCY) so EVERY
    caller — the deck render AND the vision-QA re-render — shares one cap on concurrent
    headless-chrome subprocesses (the M×N hang cascade guard)."""
    from PIL import Image

    if not admitted_source and not env_enabled("AUTHORED_RENDERER_LEGACY_ENABLED"):
        raise RendererPolicyError("legacy file renderer is disabled")
    chrome = find_chrome()
    if not chrome:
        raise RuntimeError(
            "No chrome/chromium found for slide capture (set CHROME_PATH)."
        )
    async with _get_render_sem():
        # ignore_cleanup_errors: see capture_slides — the detached chrome child can
        # hold the profile lock past screenshot capture, which would otherwise fail
        # temp-dir teardown on Windows after the PNG is already read.
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            html_path = os.path.join(td, "slide.html")
            out = os.path.join(td, "slide.png")
            instrumented_html, _ = _instrument_authored_html(
                html,
                script_nonce=script_nonce,
                issue_capability=False,
            )
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(instrumented_html)
            args = [
            chrome,
            "--headless=new",
            "--disable-gpu",
            "--disable-background-networking",
            "--disable-component-update",
            "--disable-domain-reliability",
            "--disable-sync",
            "--metrics-recording-only",
            "--no-pings",
                # Isolated profile — see capture_slides: without it chrome.exe hands
                # off to a user's already-running Chrome and writes no screenshot,
                # silently blanking every authored slide.
                f"--user-data-dir={os.path.join(td, 'profile')}",
                "--no-first-run",
                "--no-default-browser-check",
                "--hide-scrollbars",
                "--force-device-scale-factor=1",
                f"--window-size={SLIDE_W},{SLIDE_H}",
                f"--screenshot={out}",
                "--dump-dom",
                "--virtual-time-budget=20000",
                pathlib.Path(html_path).as_uri(),
            ]
            _count_runtime("chrome_process_launches")
            dumped_dom = await asyncio.to_thread(_run_chrome_screenshot, args, timeout)
            if not await _wait_for_output(out):
                raise RuntimeError("chrome screenshot produced no output file")
            metrics = _extract_browser_metrics(dumped_dom)
            img = Image.open(out).convert("RGB").crop((0, 0, SLIDE_W, SLIDE_H))
            if metrics is not None:
                requires_pixel_probe = any(
                    isinstance(item, Mapping)
                    and bool(item.get("requires_pixel_contrast"))
                    for item in metrics.get("elements", ())
                )
                if requires_pixel_probe:
                    probe_html_path = os.path.join(td, "pixel-probe.html")
                    probe_out = os.path.join(td, "pixel-probe.png")
                    with open(probe_html_path, "w", encoding="utf-8") as file:
                        file.write(_pixel_probe_document(html))
                    probe_args = [
                        chrome,
                        "--headless=new",
                        "--disable-gpu",
                        "--disable-background-networking",
                        "--disable-component-update",
                        "--disable-domain-reliability",
                        "--disable-sync",
                        "--metrics-recording-only",
                        "--no-pings",
                        f"--user-data-dir={os.path.join(td, 'pixel-profile')}",
                        "--no-first-run",
                        "--no-default-browser-check",
                        "--hide-scrollbars",
                        "--force-device-scale-factor=1",
                        f"--window-size={SLIDE_W},{SLIDE_H * 2}",
                        f"--screenshot={probe_out}",
                        "--virtual-time-budget=20000",
                        pathlib.Path(probe_html_path).as_uri(),
                    ]
                    try:
                        _count_runtime("chrome_process_launches")
                        await asyncio.to_thread(
                            _run_chrome_screenshot, probe_args, timeout
                        )
                        if not await _wait_for_output(probe_out):
                            raise RuntimeError(
                                "chrome pixel probe produced no output file"
                            )
                        probe_image = Image.open(probe_out).convert("RGB")
                        if probe_image.size[0] < SLIDE_W or probe_image.size[1] < SLIDE_H * 2:
                            raise RuntimeError(
                                f"chrome pixel probe has unexpected size {probe_image.size}"
                            )
                        metrics = _enrich_pixel_contrast(metrics, img, probe_image)
                    except Exception as error:
                        # The primary render remains valid.  Leave contrast null and
                        # the measurement flag false so hard readability checks are
                        # unknown/fail-closed rather than accepting guessed pixels.
                        LOGGER.warning("authored pixel contrast probe failed: %s", error)
                _cache_browser_metrics(cache_html or html, metrics)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()


from utils.slide_capture_worker_client import (
    RendererIsolationError,
    _decode_worker_response,
    _render_html_in_isolated_worker,
)



async def _render_admitted_html_in_worker(
    admitted: AdmittedHtml, *, timeout: int
) -> tuple[bytes, Mapping[str, Any] | None]:
    """Low-level worker-only CDP entry; never starts another worker or file render."""

    if os.getenv("AUTHORED_RENDERER_WORKER") != "1":
        raise RendererIsolationError("worker-only renderer called outside isolation boundary")
    # This worker owns no author/network work and handles one request.  Keeping
    # POSIX FD inheritance on its main thread avoids threaded spawn hazards.
    return _render_admitted_html_over_pipe(admitted, timeout)


async def _render_html_to_png_uncached(html: str, timeout: int = 60) -> bytes:
    """Render through isolation by default; legacy file input requires two opt-ins."""

    ensure_renderer_enabled()
    mode = renderer_mode()
    admitted = _admit_render_source(html)
    if mode == "legacy":
        # Kept only for legacy read/export migrations.  New authored requests use
        # isolated mode and never auto-downgrade here after a worker failure.
        return await _render_html_to_png_subprocess(html, timeout=timeout)

    # A production renderer never enters the in-process Chrome branch. It must
    # cross the external non-root/read-only/network-none worker boundary, and a
    # worker failure is returned without a local downgrade.
    if renderer_requires_external_worker() or env_enabled(
        "AUTHORED_RENDERER_LEGACY_ENABLED"
    ):
        async with _get_render_sem():
            rendered, metrics = await _render_html_in_isolated_worker(html, timeout)
        if metrics is not None:
            # Keep the worker's atomic PNG/metrics pair. Otherwise callers of
            # render_html_to_png_with_metrics recollect DOM telemetry in a
            # second browser invocation after the PNG render.
            _cache_browser_metrics(html, metrics)
        return rendered

    # Development uses one isolated Chrome process with disposable page sessions.
    # Each attempted browser boundary independently acquires the process-wide
    # limiter, so a failed reusable session cannot overlap its one-shot fallback.
    if CHROME_REUSE_ENABLED:
        try:
            async with _get_render_sem():
                rendered, metrics = await _render_html_in_reusable_chrome(
                    admitted.html,
                    timeout,
                    script_nonce=admitted.nonce,
                    cache_html=html,
                )
            if metrics is not None:
                _cache_browser_metrics(html, metrics)
            return rendered
        except Exception as error:
            LOGGER.warning("reusable authored Chrome failed; using one-shot: %s", error)

    return await _render_html_to_png_subprocess(
        admitted.html,
        timeout=timeout,
        admitted_source=True,
        cache_html=html,
        script_nonce=admitted.nonce,
    )


async def render_html_to_png(html: str, timeout: int = 60) -> bytes:
    """Render one authored slide, reusing successful identical renders in-process.

    Vision QA and repeated generation/export attempts can request the exact same HTML.
    A small memory-bounded LRU avoids relaunching Chrome, while the in-flight table
    prevents concurrent duplicate requests from doing the same work twice.
    """
    ensure_renderer_enabled()
    _count_runtime("requests")
    if RENDER_CACHE_SIZE <= 0:
        return await _render_html_to_png_uncached(html, timeout=timeout)

    key = hashlib.sha256(
        f"{RENDER_CACHE_VERSION}\0{timeout}\0{html}".encode("utf-8")
    ).hexdigest()
    cached = _render_cache.get(key)
    if cached is not None:
        _count_runtime("render_cache_hits")
        _render_cache.move_to_end(key)
        return cached

    task = _render_inflight.get(key)
    if task is None:
        task = asyncio.create_task(_render_html_to_png_uncached(html, timeout=timeout))
        _render_inflight[key] = task
    try:
        rendered = await asyncio.shield(task)
    finally:
        if _render_inflight.get(key) is task and task.done():
            _render_inflight.pop(key, None)

    _render_cache[key] = rendered
    _render_cache.move_to_end(key)
    _count_runtime("render_cache_bytes", len(rendered))
    metrics = _peek_cached_browser_metrics(html)
    if metrics is not None:
        _cache_capture(
            html,
            timeout,
            RenderCapture.from_payload(
                html_digest="sha256:" + hashlib.sha256(html.encode("utf-8")).hexdigest(),
                png=rendered,
                browser_metrics=metrics,
                runtime_counters=get_render_runtime_counters(),
            ),
        )
    while len(_render_cache) > RENDER_CACHE_SIZE:
        _render_cache.popitem(last=False)
    return rendered


# Bound concurrent headless-chrome subprocesses PROCESS-WIDE (not per deck): each render
# is a separate chrome process, and M concurrent decks would otherwise spawn M×N browsers
# and exhaust memory/CPU (the observed hang cascade). One shared semaphore, acquired at the
# single chokepoint (render_html_to_png), caps total chrome across all authored generations
# and all callers in this process. Tune per deploy via AUTHORED_RENDER_CONCURRENCY
# (low-RAM hosts: 2; beefy: 8).
def _env_int(name: str, default: int) -> int:
    try:
        v = int(os.getenv(name, "").strip())
        return v if v > 0 else default
    except (TypeError, ValueError):
        return default


def _env_nonnegative_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, "").strip())
        return value if value >= 0 else default
    except (TypeError, ValueError):
        return default


RENDER_CACHE_SIZE = _env_nonnegative_int("AUTHORED_RENDER_CACHE_SIZE", 32)
# This changes whenever rendering semantics change. In particular, v3 prevents
# Reuse of PNGs created under the former minimum-font rendering policy is unsafe.
RENDER_CACHE_VERSION = "authored-render-v8-owned-paint-cdp"
_render_cache: "OrderedDict[str, bytes]" = OrderedDict()
_render_inflight: Dict[str, "asyncio.Task[bytes]"] = {}
CAPTURE_CACHE_SIZE = max(0, min(RENDER_CACHE_SIZE, 8))
_capture_cache: "OrderedDict[str, RenderCapture]" = OrderedDict()
OCCLUSION_PROBE_CONTRACT_VERSION = "owned-paint-v3"
OCCLUSION_SENSOR_VERSION = "owned-paint-v5-inset-retry"
DOM_METRICS_VERSION = "authored-dom-metrics-v15-source-block-visibility"
DOM_METRICS_CACHE_SIZE = max(1, RENDER_CACHE_SIZE)
_browser_metrics_cache: "OrderedDict[str, Mapping[str, Any]]" = OrderedDict()
_browser_metrics_inflight: Dict[str, "asyncio.Task[Mapping[str, Any] | None]"] = {}
RENDER_CONCURRENCY = _env_int("AUTHORED_RENDER_CONCURRENCY", 4)
CHROME_REUSE_ENABLED = os.getenv("AUTHORED_CHROME_REUSE", "1").strip().lower() not in {
    "0",
    "false",
    "off",
    "no",
}
_render_sem: Optional[asyncio.Semaphore] = None


def reset_render_runtime(
    *, clear_caches: bool = True, retire_session: bool = True
) -> None:
    """Reset benchmark-visible render state without resetting lifetime counters.

    Callers must only reset between batches.  Killing a shared Chrome process or
    clearing an LRU while a render owns it would make the benchmark itself race
    production work, so an active in-flight request is rejected explicitly.
    """

    active = any(not task.done() for task in _render_inflight.values()) or any(
        not task.done() for task in _browser_metrics_inflight.values()
    )
    if active:
        raise RuntimeError("cannot reset authored render runtime while requests are active")
    if retire_session:
        _close_reusable_chrome()
    if clear_caches:
        _render_cache.clear()
        _browser_metrics_cache.clear()
        _capture_cache.clear()
        with _internal_capability_lock:
            _internal_capabilities.clear()


def _get_render_sem() -> asyncio.Semaphore:
    # Created lazily inside the running loop (first use is always within an async call).
    global _render_sem
    if _render_sem is None:
        _render_sem = asyncio.Semaphore(RENDER_CONCURRENCY)
    return _render_sem


def _placeholder_png() -> bytes:
    """A plain neutral 1280x720 PNG used when a single slide fails to render, so the
    deck still assembles (N valid images) instead of aborting on one bad slide."""
    from PIL import Image

    img = Image.new("RGB", (SLIDE_W, SLIDE_H), (248, 250, 252))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


async def render_html_list_to_pngs(htmls: List[str], timeout: int = 60) -> List[bytes]:
    """Render many authored HTML slides to PNG bytes (order preserved), with per-slide
    resilience: a slide that fails to render becomes a neutral placeholder so one failure
    can't abort the whole deck. Concurrency is bounded inside render_html_to_png."""

    async def render_one(index: int, html: str) -> tuple[bytes, bool]:
        try:
            return await render_html_to_png(html, timeout=timeout), False
        except (RendererDisabledError, RendererPolicyError, RendererIsolationError):
            # Security/isolation failures are deck-level failures.  Publishing a
            # fresh neutral placeholder would replace the last-known-good artifact
            # and make a failed isolation attempt look successful.
            raise
        except Exception as exc:
            LOGGER.warning(
                "Authored slide render failed at index %d: %s", index, exc
            )
            return _placeholder_png(), True

    results = list(
        await asyncio.gather(*[render_one(index, html) for index, html in enumerate(htmls)])
    )
    if results and all(failed for _, failed in results):
        raise RuntimeError(
            "Every authored slide failed to render. Install Playwright's Chromium "
            "headless shell or set CHROME_PATH to a working headless browser."
        )
    return [png for png, _ in results]
# ruff: noqa: E402, F401
