"""Freeze an admitted master in its source viewport using the renderer probe.

The returned DOM retains text, assets, QIDs and hierarchy. No screenshot is
embedded and no browser is started outside the existing renderer boundary.
"""
from __future__ import annotations

import json
import math
from collections.abc import Mapping

from lxml import html as lxml_html

from utils.renderer_css_policy import ALLOWED_CSS_PROPERTIES

NORMALIZATION_VERSION = "source-viewport-computed/v1"
_MARKER = "document-canvas-source-normalization"


def normalization_probe_js() -> str:
    properties = json.dumps(sorted(ALLOWED_CSS_PROPERTIES))
    return r"""
    const effectiveFontSize = (node) => {
      let matrix = new DOMMatrix();
      for (let current = node; current; current = current.parentElement) {
        const cs = getComputedStyle(current);
        let local = cs.transform === 'none' ? new DOMMatrix() : new DOMMatrix(cs.transform);
        if (!local.is2D) return null;
        if (cs.scale && cs.scale !== 'none') {
          const parts = cs.scale.split(/\s+/).map(Number);
          local = new DOMMatrix().scale(parts[0], parts[1] ?? parts[0]).multiply(local);
        }
        if (cs.rotate && cs.rotate !== 'none') {
          const parts = cs.rotate.trim().split(/\s+/), token = parts.pop();
          if (parts.length && parts.join(' ') !== 'z' && parts.join(' ') !== '0 0 1') return null;
          const factor = token.endsWith('turn') ? 360 : token.endsWith('rad') && !token.endsWith('grad') ? 180/Math.PI : token.endsWith('grad') ? 0.9 : 1;
          const angle = parseFloat(token) * factor;
          if (!Number.isFinite(angle)) return null;
          local = new DOMMatrix().rotate(angle).multiply(local);
        }
        matrix = local.multiply(matrix);
      }
      const {a,b,c,d} = matrix, sum = a*a+b*b+c*c+d*d;
      const discriminant = Math.max(0, sum*sum - 4*(a*d-b*c)**2);
      const scale = Math.sqrt(Math.max(0, (sum-Math.sqrt(discriminant))/2));
      return parseFloat(getComputedStyle(node).fontSize) * scale;
    };
    const effectiveOwnedFontSize = (node, texts) => {
      const sizes = texts.map(text => effectiveFontSize(text.parentElement));
      if (sizes.some(size => size === null || !Number.isFinite(size))) return null;
      return sizes.length ? Math.min(...sizes) : effectiveFontSize(node);
    };
    const freezeSourceViewport = () => {
      if (!document.querySelector('meta[name="document-canvas-source-normalization"]')) return null;
      const allowed = new Set(PROPERTIES);
      const copy = document.documentElement.cloneNode(true);
      const originals = [document.documentElement, ...document.documentElement.querySelectorAll('*')];
      const clones = [copy, ...copy.querySelectorAll('*')];
      for (let i = 0; i < originals.length; i++) {
        const node = originals[i], clone = clones[i];
        if (['SCRIPT','STYLE','META','LINK','HEAD','TITLE'].includes(node.tagName)) continue;
        const cs = getComputedStyle(node);
        clone.removeAttribute('style');
        for (const property of cs) {
          if (allowed.has(property)) clone.style.setProperty(property, cs.getPropertyValue(property));
        }
        // Generated content was never admitted as editable source content.
        for (const pseudo of ['::before', '::after']) {
          const content = getComputedStyle(node, pseudo).content;
          if (content && !['none','normal','""'].includes(content))
            throw new Error('source_native_canvas_generated_content_unsupported');
        }
      }
      copy.querySelectorAll('script,style,link,meta[http-equiv],meta[name="document-canvas-source-normalization"],meta[id="__codex_authored_dom_metrics"]').forEach(node => node.remove());
      // Keep the original body (including transforms, background and QIDs) as
      // an ordinary source-local element; the new body owns only placement.
      const oldBody = copy.querySelector('body');
      const local = document.createElement('div');
      for (const attr of oldBody.attributes) local.setAttribute(attr.name, attr.value);
      while (oldBody.firstChild) local.appendChild(oldBody.firstChild);
      const outer = document.createElement('body');
      outer.style.backgroundColor = getComputedStyle(document.documentElement).backgroundColor;
      outer.style.backgroundImage = getComputedStyle(document.documentElement).backgroundImage;
      const viewport = document.createElement('div');
      viewport.style.cssText = `position:relative;width:${innerWidth}px;height:${innerHeight}px;margin:0;padding:0;transform:matrix(1,0,0,1,0,0);transform-origin:0 0`;
      // Fixed right/bottom anchors now resolve against the original viewport,
      // even after the outer body is placed on a differently shaped target.
      // Any nearer original transformed ancestor remains inside this wrapper.
      viewport.appendChild(local);
      outer.appendChild(viewport);
      oldBody.replaceWith(outer);
      return {version:'source-viewport-computed/v1', width:innerWidth, height:innerHeight,
        html:'<!DOCTYPE html>' + copy.outerHTML};
    };
    """.replace("PROPERTIES", properties)


async def normalize_source_viewport(html: str, *, width: float, height: float) -> tuple[str, dict]:
    """Resolve relative CSS and breakpoints before moving the master to A4."""
    from utils.slide_capture import render_html_to_png_with_metrics

    if not all(math.isfinite(value) and 1 <= value <= 7680 for value in (width, height)):
        raise ValueError("source_native_canvas_source_viewport_invalid")
    document = lxml_html.document_fromstring(html)
    head = document.find("head")
    if head is None:
        head = lxml_html.Element("head")
        document.insert(0, head)
    marker = lxml_html.Element("meta", name=_MARKER, content=NORMALIZATION_VERSION)
    head.append(marker)
    source = lxml_html.tostring(document, encoding="unicode", method="html")
    # CSS viewport dimensions are integers. Fractional source rectangles remain
    # exact in the contain plan; they do not change the viewport's CSS rounding.
    viewport = (round(width), round(height))
    capture = await render_html_to_png_with_metrics(source, width_px=viewport[0], height_px=viewport[1])
    metrics = capture.browser_metrics or {}
    frozen = metrics.get("source_viewport_normalization")
    if not metrics.get("fonts_ready") or not isinstance(frozen, Mapping):
        raise ValueError("source_native_canvas_source_measurement_missing")
    if (frozen.get("version") != NORMALIZATION_VERSION
            or (frozen.get("width"), frozen.get("height")) != viewport
            or not isinstance(frozen.get("html"), str) or not frozen["html"]):
        raise ValueError("source_native_canvas_source_measurement_invalid")
    return frozen["html"], {
        "version": NORMALIZATION_VERSION,
        "sourceViewport": {"width": viewport[0], "height": viewport[1]},
        "sourceMeasurementDigest": capture.metrics_digest,
    }
