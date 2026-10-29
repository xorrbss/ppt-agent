"""Font availability, contrast and owned text paint in the Chromium probe."""

FONT_AND_PAINT_JS = (
r"""
    const fontFamilies = (value) => {
      const families = []; let current = '', quote = null, escaped = false;
      for (const character of String(value || '')) {
        if (escaped) { current += character; escaped = false; continue; }
        if (character === '\\') { current += character; escaped = true; continue; }
        if (quote) {
          current += character;
          if (character === quote) quote = null;
          continue;
        }
        if (character === '"' || character === "'") { current += character; quote = character; conti"""
r"""nue; }
        if (character === ',') {
          if (current.trim()) families.push(current.trim().replace(/^['"]|['"]$/g, ''));
          current = ''; continue;
        }
        current += character;
      }
      if (current.trim()) families.push(current.trim().replace(/^['"]|['"]$/g, ''));
      return families;
    };
    const familyAvailable = (family, text) => {
      const canvas = document.createElement('canvas'), context = canvas.getContext('2d');
      if (!context) return false;
      const sample = String(text || '').trim() || 'AaMmWw09\uD55C\uAE00\u6F22\u5B57';
      return ['monospace', 'serif', 'sans-serif'].some((fallback) => {
        context.font = '72px ' + fallback; const base = context.measureText(sample).width;
        context.font = '72px "__codex_missing_font__",' + fallback;
        const missing = context.measureText(sample).width;
        context.font = '72px ' + JSON.stringify(family) + ',' + fallback;
        const actual = context.measureText(sample).width;
        return Math.abs(actual - base) > 0.01 && Math.abs(actual - missing) > 0.01;
      });
    };
    const fontEvidence = (style, text) => {
      if (!document.fonts) return {loaded: true, family: null, source: 'font-api-unavailable'};
      try {
        if (document.fonts.status !== 'loaded') return {loaded: false, family: null, source: 'fonts-"""
r"""pending'};
        const generic = new Set(['serif', 'sans-serif', 'monospace', 'cursive', 'fantasy', 'system-ui']);
        for (const family of fontFamilies(style.fontFamily)) {
          const normalized = family.toLowerCase();
          if (generic.has(normalized)) return {loaded: true, family, source: 'generic-fallback'};
          const loadedFace = Array.from(document.fonts).some((font) =>
            String(font.family || '').replace(/^['"]|['"]$/g, '').toLowerCase() === normalized &&
            font.status === 'loaded');
          if (loadedFace) {
            const descriptor = [style.fontStyle, style.fontWeight, style.fontSize,
              JSON.stringify(family)].join(' ');
            if (document.fonts.check(descriptor, String(text || '').trim() || 'M'))
              return {loaded: true, family, source: 'loaded-font-face'};
          }
          if (familyAvailable(family, text))
            return {loaded: true, family, source: 'installed-local-font'};
        }
        return {loaded: false, family: null, source: 'no-available-family'};
      } catch (_) { return {loaded: false, family: null, source: 'font-probe-error'}; }
    };
    const rgba = (value) => {
      const match = String(value || '').match(/rgba?\(\s*([\d.]+)[, ]+\s*([\d.]+)[, ]+\s*([\d.]+)(?:"""
r"""\s*[,/]\s*([\d.]+))?\s*\)/i);
      return match ? [Number(match[1]), Number(match[2]), Number(match[3]), match[4] === undefined ?"""
r""" 1 : Number(match[4])] : null;
    };
    const composite = (front, back) => {
      const alpha = front[3] + back[3] * (1 - front[3]);
      if (alpha <= 0) return [255, 255, 255, 1];
      return [0, 1, 2].map((index) => (front[index] * front[3] + back[index] * back[3] * (1 - front["""
r"""3])) / alpha).concat(alpha);
    };
    const effectiveBackground = (node) => {
      const layers = [];
      for (let current = node; current; current = current.parentElement) {
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
      }
      return layers.reverse().reduce((back, front) => composite(front, back), [255, 255, 255, 1]);
    };
    const luminance = (color) => {
      const channel = (value) => { value /= 255; return value <= .04045 ? value / 12.92 : Math.pow(("""
r"""value + .055) / 1.055, 2.4); };
      return .2126 * channel(color[0]) + .7152 * channel(color[1]) + .0722 * channel(color[2]);
    };
    const contrastRatio = (node, style) => {
      const foreground = rgba(style.color); const background = effectiveBackground(node);
      if (!foreground || !background) return null;
      const paintedForeground = composite(foreground, background);
      const first = luminance(paintedForeground), second = luminance(background);
      return (Math.max(first, second) + .05) / (Math.min(first, second) + .05);
    };
    const isTrustedFontResourceText = (text) => {
      const style = text.parentElement?.closest?.('style[data-presenton-trusted-font-css]');
      return style?.namespaceURI === 'http://www.w3.org/2000/svg' &&
        style.localName === 'style' &&
        style.parentElement?.namespaceURI === 'http://www.w3.org/2000/svg' &&
        style.parentElement.localName === 'svg' &&
        /^sha256:[0-9a-f]{64}$/.test(style.getAttribute('data-presenton-trusted-font-css') || '');
    };
    const ownedTextNodes = (node) => {
      const result = [];
      const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
      for (let text = walker.nextNode(); text; text = walker.nextNode()) {
        if (!String(text.textContent || '').trim()) continue;
        if (isTrustedFontResourceText(text)) continue;
        if (text.parentElement?.closest('[data-qid]') !== node) continue;
        result.push(text);
      }
      return result;
    };
    const ownedTextRectCache = new WeakMap();
    const ownedTextRects = (node) => {
      const cached = ownedTextRectCache.get(node);
      if (cached) return cached;
      const rects = [];
      for (const text of ownedTextNodes(node)) {
        const range = document.createRange(); range.selectNodeContents(text);
        for (const rect of range.getClientRects()) {
          if (rect.width > 0 && rect.height > 0) rects.push({x: rect.x, y: rect.y, width: rect.width"""
r""", height: rect.height});
        }
      }
      ownedTextRectCache.set(node, rects);
      return rects;
    };
    const replacedPaintTags = new Set(['IMG', 'PICTURE', 'SVG', 'CANVAS', 'VIDEO', 'OBJECT', 'EMBED']);
    const visibleColor = (value) => {
      const color = rgba(value);
      return color !== null && color[3] > .001;
    };
    const ownsSurfacePaint = (node, style = getComputedStyle(node)) => {
      if (style.visibility === 'hidden' || Number(style.opacity || 1) <= .001) return false;
      if (replacedPaintTags.has(node.tagName)) return true;
      if (style.backgroundImage !== 'none' || visibleColor(style.backgroundColor)) return true;
      if (style.boxShadow !== 'none' || style.outlineStyle !== 'none') return true;
      return ['Top', 'Right', 'Bottom', 'Left'].some((side) =>
        px(style[`border${side}Width`]) > 0 &&
        !['none', 'hidden'].includes(style[`border${side}Style`]) &&
        visibleColor(style[`border${side}Color`])
      );
    };
"""
)
