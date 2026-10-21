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

TEXT_INK_PROBE_VERSION = "glyph-ink/v3"

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
      const source = String(text.textContent || '');
      const count = node.getNumberOfChars();
      const whitespace = getComputedStyle(node).whiteSpace;
      if (whitespace === 'pre') {
        // SVG XML whitespace preserves character addresses while TAB/LF/CR
        // paint as spaces. Keep every address, including indentation and
        // repeated trailing whitespace, and retain the normal advance guards.
        const preserved = source.replace(/[\t\n\r]/g, ' ');
        return preserved.length === count ? preserved : null;
      }
      if (whitespace && !['normal', 'nowrap'].includes(whitespace)) return null;
      const collapsed = source.replace(/[\t\n\r ]+/g, ' ');
      if (collapsed.length === count) return collapsed;
      const trimmed = collapsed.trim();
      return trimmed.length === count ? trimmed : null;
    };
    const inkLanguageContext = (node) => {
      for (let current = node; current; current = current.parentElement) {
        const language = current.getAttribute('lang') ??
          current.getAttributeNS('http://www.w3.org/XML/1998/namespace', 'lang');
        if (language !== null) return {language: language || 'und', explicit: true};
      }
      // No language attribute means the SVG language is unspecified; the
      // browser UI locale is not an author-supplied shaping language.
      return {language: 'und', explicit: false};
    };
    const inkLanguage = (node) => inkLanguageContext(node).language;
    const inkSourceTextContext = (node) => {
      for (let current = node; current; current = current.parentElement) {
        if (current instanceof SVGTextElement) return String(current.textContent || '');
      }
      return '';
    };
    const localizedInkWitness = (node, context, segment, isolated, layoutAdvance, scale) => {
      const source = inkLanguageContext(node);
      if (source.explicit || !('lang' in context) || segment !== '\u00b7' ||
          !/\p{Script=Hangul}/u.test(inkSourceTextContext(node)) ||
          Math.abs(isolated.width * scale - layoutAdvance) <= 1) {
        return {metrics: isolated, language: source.language};
      }
      // A tspan keeps its original text owner's shaping context. Measure with
      // that script context only in the same loaded face, and require its
      // advance to reproduce the actual SVG character before accepting ink.
      const previous = context.lang;
      let localized;
      try {
        context.lang = 'ko';
        localized = context.measureText(segment);
      } finally {
        context.lang = previous;
      }
      return Number.isFinite(localized.width) && Math.abs(localized.width * scale - layoutAdvance) <= 1
        ? {metrics: localized, language: 'ko'} : {metrics: isolated, language: source.language};
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
      // A detached canvas cannot inherit the SVG's language. Language changes
      // localized glyph shaping (including U+00B7 in Noto Sans KR), so bind it
      // before measuring and still require the SVG advance to agree.
      if ('lang' in context) context.lang = inkLanguage(node);
      else context.canvas.setAttribute('lang', inkLanguage(node));
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
      let isolatedInkClusters = 0, pairInkClusters = 0;
      const exposeGlyphInk = document.body.classList.contains('codex-timeline-ink-v1');
      const glyphInkClusters = exposeGlyphInk ? [] : null;
      const pairInkWitnesses = exposeGlyphInk ? [] : null;
      const segmenter = new Intl.Segmenter(undefined, {granularity: 'grapheme'});
      const clusters = Array.from(segmenter.segment(content), ({segment, index}) => ({segment, index}));
      const expectedGlyphClusters = clusters.filter(({segment}) => segment.trim()).length;
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
        const initial = context.measureText(segment);
        const layoutAdvance = end.x - start.x;
        const localized = localizedInkWitness(node, context, segment, initial, layoutAdvance, k);
        const isolated = localized.metrics, glyphLanguage = localized.language;
        if ('fontKerning' in context) context.fontKerning = kerning;
        const pair = next ? context.measureText(segment + next.segment) : null;
        const nextWidth = next ? context.measureText(next.segment).width : 0;
        const isolatedMatches = Number.isFinite(isolated.width) &&
          Math.abs(isolated.width * k - layoutAdvance) <= 1;
        const pairAdvance = pair ? pair.width - nextWidth : NaN;
        const pairMatches = Number.isFinite(pairAdvance) &&
          Math.abs(pairAdvance * k - layoutAdvance) <= 1;
        const witness = isolatedMatches ? isolated : pairMatches ? pair : null;
        if (exposeGlyphInk && isolatedMatches &&
            [isolated.actualBoundingBoxLeft, isolated.actualBoundingBoxRight,
             isolated.actualBoundingBoxAscent, isolated.actualBoundingBoxDescent].every(Number.isFinite)) {
          const x = start.x - isolated.actualBoundingBoxLeft * k;
          const y = start.y - isolated.actualBoundingBoxAscent * k;
          const width = (isolated.actualBoundingBoxLeft + isolated.actualBoundingBoxRight) * k;
          const height = (isolated.actualBoundingBoxAscent + isolated.actualBoundingBoxDescent) * k;
          if (width > 0 && height > 0) glyphInkClusters.push({index, length: segment.length,
            x, y, width, height, basis: 'isolated-matching-advance', language: glyphLanguage});
        }
        const cellLeft = Math.min(start.x, end.x), cellRight = Math.max(start.x, end.x);
        if (witness && [witness.actualBoundingBoxLeft, witness.actualBoundingBoxRight,
                        witness.actualBoundingBoxAscent, witness.actualBoundingBoxDescent].every(Number.isFinite)) {
          if (isolatedMatches) isolatedInkClusters += 1;
          else {
            pairInkClusters += 1;
            if (exposeGlyphInk) pairInkWitnesses.push({index, length: segment.length,
              contextLength: segment.length + next.segment.length, nextIndex: next.index,
              x: start.x - witness.actualBoundingBoxLeft * k,
              y: start.y - witness.actualBoundingBoxAscent * k,
              width: (witness.actualBoundingBoxLeft + witness.actualBoundingBoxRight) * k,
              height: (witness.actualBoundingBoxAscent + witness.actualBoundingBoxDescent) * k,
              basis: 'matching-pair-advance', language: inkLanguage(node)});
          }
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
        height: bottom - top + 2 * pad, cellClusters,
        ...(exposeGlyphInk && pad === 0 ? {glyphInkClusters, pairInkWitnesses,
          glyphPaintCoverage: {expected: expectedGlyphClusters, isolated: isolatedInkClusters,
            pair: pairInkClusters, cell: cellClusters,
            complete: isolatedInkClusters + pairInkClusters === expectedGlyphClusters && cellClusters === 0},
          glyphInkCoverage: {expected: expectedGlyphClusters, measured: glyphInkClusters.length,
            complete: glyphInkClusters.length === expectedGlyphClusters,
            language: inkLanguage(node)}} : {})};
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
