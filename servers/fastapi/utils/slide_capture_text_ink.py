"""Glyph ink boxes for SVG text runs in the Chromium probe.

``ownedTextRects`` reports ``Range.getClientRects()``, which for SVG text is
the face's full hhea ascent+descent cell, not the painted glyphs.  This probe
adds one ink box per owned text fragment: every grapheme cluster is measured
with canvas ``measureText().actualBoundingBox*`` in the same computed font and
placed in the cluster's laid-out cell (``getStartPositionOfChar`` through the
screen CTM). A cluster without a matching canvas advance keeps only its own
full-height cell; the fragment's box is the union of those cluster boxes.

An entry is ``null`` whenever the ink cannot be established soundly; the
consumer then keeps the line box for that fragment, which is the previous
behavior.  The array is aligned one-to-one with ``ownedTextRects(node)`` and is
only produced for SVG text content elements, so HTML payloads are unchanged.

Requires ``px``, ``ownedTextNodes`` and ``fontEvidence`` from the probe scope.
"""

TEXT_INK_PROBE_VERSION = "glyph-ink/v2"

TEXT_INK_JS = r"""
    const inkCanvasContext = (() => {
      try { return document.createElement('canvas').getContext('2d'); } catch (_) { return null; }
    })();
    const inkListValue = (element, name) => {
      const value = element.getAttribute(name);
      return value === null ? [] : value.trim().split(/[\s,]+/).filter(Boolean);
    };
    // Per-glyph layout that a single canvas measurement cannot reproduce, or paint
    // that can extend past the glyph outlines.
    const inkLayoutUntrusted = (node) => {
      let insideText = true;
      for (let current = node; current; current = current.parentElement) {
        const style = getComputedStyle(current);
        if (style.filter !== 'none' || style.textShadow !== 'none' ||
            (style.textDecorationLine && style.textDecorationLine !== 'none')) return true;
        if (!insideText) continue;
        if (!(current instanceof SVGTextContentElement) || current instanceof SVGTextPathElement)
          return true;
        if (current.hasAttribute('textLength') || current.hasAttribute('lengthAdjust')) return true;
        if (['x', 'y', 'dx', 'dy'].some((name) => inkListValue(current, name).length > 1)) return true;
        const rotate = inkListValue(current, 'rotate');
        if (rotate.length > 1 || (rotate.length === 1 && Number(rotate[0]) !== 0)) return true;
        if (current instanceof SVGTextElement) insideText = false;
      }
      return insideText;
    };
    const inkWeight = (value) => {
      const named = {normal: 400, bold: 700};
      return String(value).trim().split(/\s+/).map((part) => named[part] ?? Number(part));
    };
    // The face that paints the run must be a loaded @font-face covering the
    // computed weight and style, so neither side synthesizes bold or italics.
    const inkFontUntrusted = (style, content) => {
      if (style.writingMode !== 'horizontal-tb' || style.direction !== 'ltr') return true;
      if (style.textTransform !== 'none' || style.fontVariant !== 'normal') return true;
      if (style.fontFeatureSettings !== 'normal' || style.fontVariationSettings !== 'normal') return true;
      if (!['normal', '100%'].includes(style.fontStretch)) return true;
      if (style.fontSizeAdjust && style.fontSizeAdjust !== 'none') return true;
      const font = fontEvidence(style, content);
      if (font.loaded !== true || font.source !== 'loaded-font-face' || !font.family) return true;
      const family = font.family.toLowerCase();
      const weight = inkWeight(style.fontWeight)[0];
      return !Array.from(document.fonts).some((face) => {
        if (face.status !== 'loaded') return false;
        if (String(face.family || '').replace(/^['"]|['"]$/g, '').toLowerCase() !== family) return false;
        if (String(face.style || 'normal') !== style.fontStyle) return false;
        const range = inkWeight(face.weight || 'normal');
        return range.every(Number.isFinite) && weight >= range[0] && weight <= range[range.length - 1];
      });
    };
    const inkContent = (node, text) => {
      // Match the addressable characters Chromium laid out, else refuse.
      const collapsed = String(text.textContent || '').replace(/[\t\n\r ]+/g, ' ');
      const count = node.getNumberOfChars();
      if (collapsed.length === count) return collapsed;
      const trimmed = collapsed.trim();
      return trimmed.length === count ? trimmed : null;
    };
    const inkBox = (node, text, rect) => {
      const context = inkCanvasContext;
      if (!context || typeof context.measureText !== 'function') return null;
      if (!(node instanceof SVGTextContentElement) || node.childElementCount !== 0) return null;
      if (text.parentElement !== node || inkLayoutUntrusted(node)) return null;
      const style = getComputedStyle(node);
      const content = inkContent(node, text);
      if (!content || !content.trim() || inkFontUntrusted(style, content)) return null;
      const ctm = node.getScreenCTM();
      if (!ctm || !(ctm.a > 0) || Math.abs(ctm.b) > 1e-6 || Math.abs(ctm.c) > 1e-6 ||
          Math.abs(ctm.a - ctm.d) > 1e-4 * ctm.a) return null;
      const k = ctm.a;
      context.font = '1px __codex_ink_sentinel__';
      const sentinel = context.font;
      context.font = [style.fontStyle, style.fontWeight, style.fontSize, style.fontFamily].join(' ');
      if (context.font === sentinel) return null;
      if ('fontKerning' in context) context.fontKerning = style.fontKerning === 'none' ? 'none' : 'auto';
      const spacing = (value) => (value === 'normal' ? '0px' : value);
      if ('letterSpacing' in context && 'wordSpacing' in context) {
        context.letterSpacing = spacing(style.letterSpacing);
        context.wordSpacing = spacing(style.wordSpacing);
      } else if (spacing(style.letterSpacing) !== '0px' || spacing(style.wordSpacing) !== '0px') {
        return null;
      }
      // Horizontal placement comes from SVG layout. Canvas measurements are
      // witnesses for each cluster's advance and vertical ink only.
      if (typeof Intl === 'undefined' || typeof Intl.Segmenter !== 'function') return null;
      const toScreen = (point) => new DOMPoint(point.x, point.y).matrixTransform(ctm);
      const run = context.measureText(' ');
      const origin = toScreen(node.getStartPositionOfChar(0));
      const fontAscent = run.fontBoundingBoxAscent * k;
      const fontDescent = run.fontBoundingBoxDescent * k;
      if (![run.fontBoundingBoxAscent, run.fontBoundingBoxDescent, origin.x, origin.y].every(Number.isFinite))
        return null;
      // Self-checks: the canvas face must reproduce the laid-out line box.
      if (Math.abs((run.fontBoundingBoxAscent + run.fontBoundingBoxDescent) * k - rect.height) > 1) return null;
      if (Math.abs(origin.y - fontAscent - rect.y) > 1 || Math.abs(origin.x - rect.x) > 1) return null;
      let left = Infinity, right = -Infinity, top = Infinity, bottom = -Infinity;
      let cellClusters = 0;
      const segmenter = new Intl.Segmenter(undefined, {granularity: 'grapheme'});
      const clusters = Array.from(segmenter.segment(content), ({segment, index}) => ({segment, index}));
      const kerning = context.fontKerning;
      for (let position = 0; position < clusters.length; position += 1) {
        const {segment, index} = clusters[position];
        if (!segment.trim()) continue;
        const next = clusters[position + 1];
        const start = toScreen(node.getStartPositionOfChar(index));
        const end = next ? toScreen(node.getStartPositionOfChar(next.index))
          : toScreen(node.getEndPositionOfChar(index + segment.length - 1));
        if (![start.x, start.y, end.x, end.y].every(Number.isFinite)) return null;
        if (Math.abs(start.y - origin.y) > 0.01) return null;
        if ('fontKerning' in context) context.fontKerning = 'none';
        const isolated = context.measureText(segment);
        if ('fontKerning' in context) context.fontKerning = kerning;
        const pair = next ? context.measureText(segment + next.segment) : null;
        const nextWidth = next ? context.measureText(next.segment).width : 0;
        const layoutAdvance = end.x - start.x;
        const isolatedMatches = Number.isFinite(isolated.width) &&
          Math.abs(isolated.width * k - layoutAdvance) <= 1;
        const pairAdvance = pair ? pair.width - nextWidth : NaN;
        const pairMatches = Number.isFinite(pairAdvance) &&
          Math.abs(pairAdvance * k - layoutAdvance) <= 1;
        const witness = isolatedMatches ? isolated : pairMatches ? pair : null;
        const cellLeft = Math.min(start.x, end.x), cellRight = Math.max(start.x, end.x);
        if (witness && [witness.actualBoundingBoxLeft, witness.actualBoundingBoxRight,
                        witness.actualBoundingBoxAscent, witness.actualBoundingBoxDescent].every(Number.isFinite)) {
          left = Math.min(left, cellLeft - Math.max(0, witness.actualBoundingBoxLeft * k));
          right = Math.max(right, cellRight + Math.max(0, (witness.actualBoundingBoxRight - witness.width) * k));
          top = Math.min(top, start.y - witness.actualBoundingBoxAscent * k);
          bottom = Math.max(bottom, start.y + witness.actualBoundingBoxDescent * k);
        } else {
          cellClusters += 1;
          left = Math.min(left, cellLeft);
          right = Math.max(right, cellRight);
          top = Math.min(top, start.y - fontAscent);
          bottom = Math.max(bottom, start.y + fontDescent);
        }
      }
      if (!(right > left && bottom > top)) return null;
      let pad = 0;
      if (style.stroke !== 'none' && Number(style.strokeOpacity || '1') > 0) {
        if (!/^[\d.]+px$/.test(style.strokeWidth)) return null;
        const miter = ['miter', 'miter-clip', 'arcs'].includes(style.strokeLinejoin)
          ? Math.max(1, Number(style.strokeMiterlimit) || 4) : 1;
        pad = px(style.strokeWidth) * k * miter / 2;
      }
      return {x: left - pad, y: top - pad, width: right - left + 2 * pad,
        height: bottom - top + 2 * pad, cellClusters};
    };
    const ownedTextInkRects = (node) => {
      if (!(node instanceof SVGTextContentElement)) return null;
      const result = [];
      for (const text of ownedTextNodes(node)) {
        const range = document.createRange(); range.selectNodeContents(text);
        const rects = Array.from(range.getClientRects()).filter((rect) => rect.width > 0 && rect.height > 0);
        let ink = null;
        // More than one client rect means the run was split (chunks, bidi):
        // one string-level measurement cannot place it.
        if (rects.length === 1) {
          try { ink = inkBox(node, text, rects[0]); } catch (_) { ink = null; }
        }
        for (let index = 0; index < rects.length; index += 1) result.push(ink);
      }
      return result;
    };
"""
