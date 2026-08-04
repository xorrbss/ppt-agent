"""Headless per-slide capture for the vision-QA pass. Pure subprocess chrome
screenshot (no browser dependency — consistent with export spawning an external
binary) + a deterministic PIL crop: the /pdf-maker page stacks slides at exactly
1280x720 with gap 0 / margin 0, so slide i is the box (0, i*720, 1280, (i+1)*720)."""

import asyncio
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
import time
import urllib.parse
from collections import OrderedDict
from typing import Any, Dict, List, Mapping, Optional

from utils.authored_dom_quality import ensure_authored_qids

SLIDE_W = 1280
SLIDE_H = 720
LOGGER = logging.getLogger(__name__)

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


def _metrics_cache_key(html: str) -> str:
    return hashlib.sha256(
        f"{DOM_METRICS_VERSION}\0{html}".encode("utf-8")
    ).hexdigest()


def _instrument_authored_html(html: str) -> tuple[str, str]:
    """Add stable edit ids and an invisible, same-render browser probe."""
    enriched = ensure_authored_qids(html)
    key = _metrics_cache_key(html)
    if _METRICS_ELEMENT_ID in enriched:
        return enriched, key
    script = rf"""<script data-codex-dom-probe>
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
    const ownedTextRects = (node) => {{
      const rects = [];
      const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
      for (let text = walker.nextNode(); text; text = walker.nextNode()) {{
        if (!String(text.textContent || '').trim()) continue;
        if (text.parentElement?.closest('[data-qid]') !== node) continue;
        const range = document.createRange(); range.selectNodeContents(text);
        for (const rect of range.getClientRects()) {{
          if (rect.width > 0 && rect.height > 0) rects.push({{x: rect.x, y: rect.y, width: rect.width, height: rect.height}});
        }}
      }}
      return rects;
    }};
    const visibleClip = (node) => {{
      let result = {{left: 0, top: 0, right: innerWidth, bottom: innerHeight}};
      for (let current = node; current; current = current.parentElement) {{
        const style = getComputedStyle(current);
        if (['hidden', 'clip', 'scroll', 'auto'].includes(style.overflowX) || ['hidden', 'clip', 'scroll', 'auto'].includes(style.overflowY)) {{
          const rect = current.getBoundingClientRect();
          result = {{left: Math.max(result.left, rect.left), top: Math.max(result.top, rect.top), right: Math.min(result.right, rect.right), bottom: Math.min(result.bottom, rect.bottom)}};
        }}
      }}
      return result;
    }};
    const glyphClip = (node) => {{
      if (!String(node.textContent || '').trim()) return {{glyph: false, line: false}};
      const range = document.createRange(); range.selectNodeContents(node);
      const clip = visibleClip(node); const rects = Array.from(range.getClientRects());
      const outside = (rect) => rect.left < clip.left - .5 || rect.top < clip.top - .5 || rect.right > clip.right + .5 || rect.bottom > clip.bottom + .5;
      // Inline elements legitimately report clientHeight=0 even though their
      // line boxes are fully visible.  The clip intersection already includes
      // every clipping/scrolling ancestor (and the viewport), so use rendered
      // range escape as the sound signal for both glyph and line-box clipping.
      const escaped = rects.some(outside);
      return {{glyph: escaped, line: escaped}};
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
      const targetBefore = node.getBoundingClientRect();
      const parentBefore = node.parentElement.getBoundingClientRect();
      const siblingsBefore = siblings.map((item) => item.getBoundingClientRect());
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
      const plainRect = (rect) => ({{x: rect.x, y: rect.y, width: rect.width, height: rect.height}});
      return {{
        source: 'chrome', telemetryComplete: true, position: 'static',
        transformComposable: true, layoutIsolation: isolated,
        translationErrorPx, flowImpactPx, maxSiblingDeltaPx,
        targetBoundsPx: plainRect(targetBefore), parentBoundsPx: plainRect(parentBefore)
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
    const occlusion = (node, rect, points) => {{
      if (rect.width <= 0 || rect.height <= 0) return {{fraction: 1, continuous: 1, step: 0, unknown: 0}};
      // At most 40x40 samples per target, but never coarser than roughly 4 CSS
      // pixels on ordinary slide objects.  This catches thin continuous covers
      // that the former fixed 5x5 probe could miss completely.
      const cols = Math.max(5, Math.min(40, Math.ceil(rect.width / 4)));
      const rows = Math.max(5, Math.min(40, Math.ceil(rect.height / 4)));
      const cells = Array.from({{length: rows}}, () => Array(cols).fill(-1));
      let covered = 0, sampled = 0, unknown = 0;
      for (let row = 0; row < rows; row++) for (let col = 0; col < cols; col++) {{
        const x = rect.left + rect.width * (col + .5) / cols;
        const y = rect.top + rect.height * (row + .5) / rows;
        if (!pointInPolygon(x, y, points)) continue;
        sampled += 1;
        if (x < 0 || y < 0 || x >= innerWidth || y >= innerHeight) {{ cells[row][col] = 1; covered += 1; continue; }}
        const stack = document.elementsFromPoint(x, y);
        const owner = stack.findIndex((candidate) => candidate === node || node.contains(candidate));
        if (owner < 0) {{ cells[row][col] = -2; unknown += 1; continue; }}
        const isCovered = stack.slice(0, owner).some((candidate) => candidate !== node && !node.contains(candidate));
        cells[row][col] = isCovered ? 1 : 0;
        if (isCovered) covered += 1;
      }}
      if (!sampled || unknown / sampled > .05) return {{fraction: null, continuous: null, step: Math.max(rect.width / cols, rect.height / rows), unknown: sampled ? unknown / sampled : 1}};
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
      return {{fraction: covered / (sampled - unknown), continuous: largest / (sampled - unknown), step: Math.max(rect.width / cols, rect.height / rows), unknown: unknown / sampled}};
    }};
    const elements = allQids.map((node, domIndex) => {{
      const rect = node.getBoundingClientRect();
      const style = getComputedStyle(node);
      const staticTranslationProbe = staticTransformProbe(node, style);
      const glyph = glyphClip(node);
      const contrast = contrastRatio(node, style);
      const textRects = ownedTextRects(node);
      const q = quad(node, rect);
      const occultation = occlusion(node, rect, q.points);
      const connector = connectorGeometry(node);
      const effectValues = {{
        transform: style.transform, filter: style.filter, backdropFilter: style.backdropFilter,
        mixBlendMode: style.mixBlendMode, clipPath: style.clipPath,
        maskImage: style.maskImage || style.webkitMaskImage || 'none'
      }};
      const complexEffect = Object.entries(effectValues).some(([name, value]) =>
        value && value !== 'none' && !(name === 'mixBlendMode' && value === 'normal'));
      const explicitZ = Number.parseInt(style.zIndex, 10);
      const image = node.tagName === 'IMG' ? {{
        src: node.currentSrc || node.getAttribute('src') || '', complete: Boolean(node.complete),
        naturalWidth: Number(node.naturalWidth || 0), naturalHeight: Number(node.naturalHeight || 0)
      }} : undefined;
      let fontLoaded = true;
      try {{ fontLoaded = !document.fonts || document.fonts.check(style.font); }} catch (_) {{}}
      return {{
        qid: node.getAttribute('data-qid'), tag: node.tagName.toLowerCase(),
        bbox: {{x: rect.x, y: rect.y, width: rect.width, height: rect.height}},
        clientWidth: node.clientWidth, clientHeight: node.clientHeight,
        scrollWidth: node.scrollWidth, scrollHeight: node.scrollHeight,
        rendered_text: node.innerText || node.textContent || '',
        parent_qid: node.parentElement?.closest('[data-qid]')?.getAttribute('data-qid') || null,
        clipped: glyph.glyph || (['hidden', 'clip'].includes(style.overflowX) && node.scrollWidth > node.clientWidth + 1) ||
          (['hidden', 'clip'].includes(style.overflowY) && node.scrollHeight > node.clientHeight + 1),
        glyph_clipped: glyph.glyph, line_box_clipped: glyph.line,
        quad: q.points, rotation_degrees: q.measured ? rotation(style) : null,
        occluded_fraction: occultation.fraction,
        continuous_occluded_fraction: occultation.continuous,
        occlusion_sample_step_px: occultation.step,
        occlusion_unknown_fraction: occultation.unknown,
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
          left: style.left, top: style.top, width: style.width, height: style.height,
          transform: style.transform, display: style.display,
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
      fonts_ready: !document.fonts || document.fonts.status === 'loaded', elements
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


def _append_probe_style(html: str, css: str) -> str:
    style = f"<style data-codex-pixel-probe>{css}</style>"
    closing = re.search(r"</head\s*>", html, flags=re.IGNORECASE)
    if closing:
        return html[: closing.start()] + style + html[closing.start() :]
    return style + html


def _pixel_probe_document(html: str) -> str:
    """Build paired 1280x720 Chrome frames for background and glyph evidence.

    The first frame preserves every painted layer but removes text ink.  The
    second preserves layout/transforms/filters/masks while rendering text as a
    white coverage mask on black.  Keeping both frames in one Chrome screenshot
    avoids cross-process font and animation skew.
    """
    enriched = ensure_authored_qids(html)
    background = _append_probe_style(
        enriched,
        """
        [data-qid], [data-qid] *, [data-qid]::before, [data-qid]::after {
          color: transparent !important;
          -webkit-text-fill-color: transparent !important;
          text-shadow: none !important;
        }
        """,
    )
    mask = _append_probe_style(
        enriched,
        """
        html, body { background: #000 !important; }
        *:not(html):not(body) {
          background-color: transparent !important;
          background-image: none !important;
          border-color: transparent !important;
          box-shadow: none !important;
          outline-color: transparent !important;
          color: transparent !important;
          -webkit-text-fill-color: transparent !important;
          text-shadow: none !important;
        }
        img, video, canvas { visibility: hidden !important; }
        svg * { fill: transparent !important; stroke: transparent !important; }
        [data-qid], [data-qid] * {
          color: #fff !important;
          -webkit-text-fill-color: #fff !important;
        }
        [data-qid] text { fill: #fff !important; }
        """,
    )
    first = html_lib.escape(background, quote=True)
    second = html_lib.escape(mask, quote=True)
    return f"""<!doctype html><html><head><meta charset=\"utf-8\"><style>
      html,body{{margin:0;width:{SLIDE_W}px;height:{SLIDE_H * 2}px;overflow:hidden;background:#000}}
      iframe{{display:block;border:0;width:{SLIDE_W}px;height:{SLIDE_H}px}}
    </style></head><body><iframe srcdoc=\"{first}\"></iframe><iframe srcdoc=\"{second}\"></iframe></body></html>"""


def _relative_luminance(pixel: tuple[int, int, int]) -> float:
    channels = []
    for component in pixel:
        value = component / 255.0
        channels.append(
            value / 12.92
            if value <= 0.04045
            else ((value + 0.055) / 1.055) ** 2.4
        )
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _pixel_ratio(first: tuple[int, int, int], second: tuple[int, int, int]) -> float:
    one, two = _relative_luminance(first), _relative_luminance(second)
    return (max(one, two) + 0.05) / (min(one, two) + 0.05)


def _enrich_pixel_contrast(
    metrics: Mapping[str, Any], rendered: "Any", probe: "Any"
) -> Mapping[str, Any]:
    """Attach conservative per-glyph contrast from paired Chrome pixels.

    Only high-coverage mask pixels are used, avoiding anti-aliased edge pixels
    that would make every glyph appear to have a 1:1 contrast edge.  The 5th
    percentile across glyph cores intentionally represents the worst sustained
    background region, so a title spanning a light and dark gradient cannot pass
    based on its best-looking letters.
    """
    result = json.loads(json.dumps(metrics))
    background = probe.crop((0, 0, SLIDE_W, SLIDE_H)).convert("RGB")
    glyph_mask = probe.crop((0, SLIDE_H, SLIDE_W, SLIDE_H * 2)).convert("L")
    foreground = rendered.convert("RGB")
    elements = result.get("elements")
    if not isinstance(elements, list):
        return result
    for item in elements:
        if not isinstance(item, dict):
            continue
        measurement = item.get("measurement")
        if not isinstance(measurement, dict):
            measurement = {}
            item["measurement"] = measurement
        effects = item.get("visual_effects")
        has_complex_effect = isinstance(effects, Mapping) and any(
            value and str(value) not in {"none", "normal"}
            for value in effects.values()
        )
        if has_complex_effect:
            measurement["visual_effects"] = True
        if not item.get("requires_pixel_contrast"):
            continue
        fragments = item.get("text_fragments")
        if not isinstance(fragments, list):
            item["contrast_ratio"] = None
            measurement["contrast"] = False
            item["contrast_measurement_error"] = "text-fragments-unavailable"
            continue
        samples: list[tuple[int, float]] = []
        for fragment in fragments:
            if not isinstance(fragment, Mapping):
                continue
            try:
                left = max(0, math.floor(float(fragment.get("x", 0))))
                top = max(0, math.floor(float(fragment.get("y", 0))))
                right = min(
                    SLIDE_W,
                    math.ceil(
                        float(fragment.get("x", 0))
                        + float(fragment.get("width", 0))
                    ),
                )
                bottom = min(
                    SLIDE_H,
                    math.ceil(
                        float(fragment.get("y", 0))
                        + float(fragment.get("height", 0))
                    ),
                )
            except (TypeError, ValueError):
                continue
            for y in range(top, bottom):
                for x in range(left, right):
                    coverage = glyph_mask.getpixel((x, y))
                    if coverage >= 16:
                        samples.append(
                            (
                                coverage,
                                _pixel_ratio(
                                    foreground.getpixel((x, y)),
                                    background.getpixel((x, y)),
                                ),
                            )
                        )
        if len(samples) < 4:
            item["contrast_ratio"] = None
            measurement["contrast"] = False
            item["contrast_measurement_error"] = "glyph-mask-insufficient"
            continue
        strongest = max(value for value, _ in samples)
        threshold = max(64, int(strongest * 0.72))
        core = sorted(ratio for value, ratio in samples if value >= threshold)
        if len(core) < 4:
            item["contrast_ratio"] = None
            measurement["contrast"] = False
            item["contrast_measurement_error"] = "glyph-core-insufficient"
            continue
        percentile_index = min(len(core) - 1, max(0, int(len(core) * 0.05)))
        item["contrast_ratio"] = round(core[percentile_index], 6)
        item["contrast_sample_count"] = len(core)
        item["contrast_measurement_method"] = "chrome-paired-pixel-glyph-v1"
        item.pop("contrast_measurement_error", None)
        measurement["contrast"] = True
    return result


def get_cached_browser_metrics(html: str) -> Mapping[str, Any] | None:
    """Return metrics captured by the same Chrome invocation as the PNG render."""
    key = _metrics_cache_key(html)
    metrics = _browser_metrics_cache.get(key)
    if metrics is not None:
        _browser_metrics_cache.move_to_end(key)
    return metrics


def _cache_browser_metrics(html: str, metrics: Mapping[str, Any]) -> None:
    """Store metrics under the same authored HTML identity as its rendered PNG."""
    key = _metrics_cache_key(html)
    _browser_metrics_cache[key] = metrics
    _browser_metrics_cache.move_to_end(key)
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

    key = _metrics_cache_key(html)
    task = _browser_metrics_inflight.get(key)
    if task is None:
        async def recollect() -> Mapping[str, Any] | None:
            await _render_html_to_png_uncached(html, timeout=timeout)
            return get_cached_browser_metrics(html)

        task = asyncio.create_task(recollect())
        _browser_metrics_inflight[key] = task
    try:
        return await asyncio.shield(task)
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
) -> tuple[bytes, Mapping[str, Any] | None]:
    """Render one slide and return telemetry tied to the same HTML cache identity.

    This is the preferred API for quality-review callers.  It preserves the legacy
    bytes-only renderer while making cache divergence explicit and recoverable.
    """
    png = await render_html_to_png(html, timeout=timeout)
    return png, await ensure_browser_metrics(html, timeout=timeout)


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


def _playwright_headless_shell_candidates() -> List[pathlib.Path]:
    """Return installed Playwright headless-shell executables, newest first.

    Desktop Chrome can hand a headless request to an already-running browser and
    exit successfully without writing a screenshot. Playwright's standalone
    headless shell does not share the user's browser profile/process, so it is the
    most reliable automatic choice for server-side slide rendering.
    """
    roots: List[pathlib.Path] = []
    configured_root = os.getenv("PLAYWRIGHT_BROWSERS_PATH")
    if configured_root and configured_root != "0":
        roots.append(pathlib.Path(configured_root).expanduser())

    local_app_data = os.getenv("LOCALAPPDATA")
    if local_app_data:
        roots.append(pathlib.Path(local_app_data) / "ms-playwright")
    roots.extend(
        [
            pathlib.Path.home() / ".cache" / "ms-playwright",
            pathlib.Path.home() / "Library" / "Caches" / "ms-playwright",
        ]
    )

    found: List[pathlib.Path] = []
    seen = set()
    for root in roots:
        try:
            resolved_root = root.resolve()
        except OSError:
            resolved_root = root
        if resolved_root in seen or not root.is_dir():
            continue
        seen.add(resolved_root)
        for candidate in root.glob(
            "chromium_headless_shell-*/*/chrome-headless-shell*"
        ):
            if candidate.name not in {
                "chrome-headless-shell",
                "chrome-headless-shell.exe",
            }:
                continue
            if candidate.is_file():
                found.append(candidate)

    def revision(candidate: pathlib.Path) -> int:
        for part in candidate.parts:
            prefix = "chromium_headless_shell-"
            if part.startswith(prefix):
                try:
                    return int(part[len(prefix) :])
                except ValueError:
                    return -1
        return -1

    return sorted(found, key=revision, reverse=True)


def find_chrome() -> Optional[str]:
    for env in ("CHROME_PATH", "PUPPETEER_EXECUTABLE_PATH", "CHROMIUM_PATH"):
        p = os.getenv(env)
        if p and os.path.isfile(p):
            return p
    headless_shells = _playwright_headless_shell_candidates()
    if headless_shells:
        return str(headless_shells[0])
    for cand in (
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
    ):
        if os.path.isfile(cand):
            return cand
    return None


async def capture_slides(
    presentation_id: str,
    n_slides: int,
    base_url: str = "http://127.0.0.1:5000",
    timeout: int = 90,
) -> List[bytes]:
    """Capture the deck via headless chrome and slice it into per-slide PNG bytes.
    Raises RuntimeError if no chrome is found or the screenshot fails."""
    from PIL import Image

    chrome = find_chrome()
    if not chrome:
        raise RuntimeError(
            "No chrome/chromium found for slide capture (set CHROME_PATH)."
        )
    n = max(1, int(n_slides))
    height = n * SLIDE_H
    url = f"{base_url}/pdf-maker?id={presentation_id}"
    # ignore_cleanup_errors: the detached headless-chrome child can outlive the
    # screenshot and keep the --user-data-dir profile locked; on Windows that turns
    # temp-dir teardown into a PermissionError. The PNG is already captured by then,
    # so leave the locked profile files for the OS to reap instead of crashing.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        out = os.path.join(td, "deck.png")
        args = [
            chrome,
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            # Isolated profile so the invocation always spawns its OWN headless
            # instance. Without it, on a desktop where the user already has Chrome
            # open, chrome.exe hands the request to the running instance and returns
            # immediately (exit 0, NO screenshot) → every slide degrades to a blank
            # placeholder. --no-first-run/--no-default-browser-check keep the fresh
            # profile from doing first-run setup that stalls under concurrency.
            f"--user-data-dir={os.path.join(td, 'profile')}",
            "--no-first-run",
            "--no-default-browser-check",
            "--hide-scrollbars",
            "--force-device-scale-factor=1",
            f"--window-size={SLIDE_W},{height}",
            f"--screenshot={out}",
            "--virtual-time-budget=20000",
            url,
        ]
        await asyncio.to_thread(_run_chrome_screenshot, args, timeout)
        if not await _wait_for_output(out):
            raise RuntimeError("chrome screenshot produced no output file")
        img = Image.open(out).convert("RGB")
        slides: List[bytes] = []
        for i in range(n):
            top = i * SLIDE_H
            crop = img.crop((0, top, SLIDE_W, top + SLIDE_H))
            buf = io.BytesIO()
            crop.save(buf, format="PNG")
            slides.append(buf.getvalue())
        return slides


async def _render_html_to_png_uncached(html: str, timeout: int = 60) -> bytes:
    """Render a self-contained HTML document to an exact 1280x720 PNG via headless
    chrome. Used by the authored-mode pipeline (the HTML is authored in-memory, not
    served from /pdf-maker). Raises RuntimeError if no chrome is found or the
    screenshot fails.

    Bounded by the process-wide render semaphore (AUTHORED_RENDER_CONCURRENCY) so EVERY
    caller — the deck render AND the vision-QA re-render — shares one cap on concurrent
    headless-chrome subprocesses (the M×N hang cascade guard)."""
    from PIL import Image

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
            instrumented_html, _ = _instrument_authored_html(html)
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(instrumented_html)
            args = [
                chrome,
                "--headless=new",
                "--disable-gpu",
                "--no-sandbox",
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
                        "--no-sandbox",
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
                _cache_browser_metrics(html, metrics)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()


async def render_html_to_png(html: str, timeout: int = 60) -> bytes:
    """Render one authored slide, reusing successful identical renders in-process.

    Vision QA and repeated generation/export attempts can request the exact same HTML.
    A small memory-bounded LRU avoids relaunching Chrome, while the in-flight table
    prevents concurrent duplicate requests from doing the same work twice.
    """
    if RENDER_CACHE_SIZE <= 0:
        return await _render_html_to_png_uncached(html, timeout=timeout)

    key = hashlib.sha256(
        f"{RENDER_CACHE_VERSION}\0{timeout}\0{html}".encode("utf-8")
    ).hexdigest()
    cached = _render_cache.get(key)
    if cached is not None:
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
RENDER_CACHE_VERSION = "authored-render-v6-connector-geometry"
_render_cache: "OrderedDict[str, bytes]" = OrderedDict()
_render_inflight: Dict[str, "asyncio.Task[bytes]"] = {}
DOM_METRICS_VERSION = "authored-dom-metrics-v8-connector-geometry"
DOM_METRICS_CACHE_SIZE = max(1, RENDER_CACHE_SIZE)
_browser_metrics_cache: "OrderedDict[str, Mapping[str, Any]]" = OrderedDict()
_browser_metrics_inflight: Dict[str, "asyncio.Task[Mapping[str, Any] | None]"] = {}
RENDER_CONCURRENCY = _env_int("AUTHORED_RENDER_CONCURRENCY", 4)
_render_sem: Optional[asyncio.Semaphore] = None


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
