"""Bounded interior hit tests for a plain one-pixel viewport-edge surface."""

THIN_VIEWPORT_HIT_TEST_JS = r"""
    const thinViewportInteriorSamples = (node, rect, points, textRects, surfaceRects, x, y) => {
      const close = (a, b) => Math.abs(a - b) <= 1e-6;
      const vertical = rect.height > 0 && rect.height <= 1 && close(y, rect.top + rect.height / 2) &&
        (close(rect.top, 0) || close(rect.bottom, innerHeight));
      const horizontal = rect.width > 0 && rect.width <= 1 && close(x, rect.left + rect.width / 2) &&
        (close(rect.left, 0) || close(rect.right, innerWidth));
      if ((!vertical && !horizontal) || textRects.length || surfaceRects.length !== 1 || node.children.length ||
          node.namespaceURI !== 'http://www.w3.org/1999/xhtml' || node.tagName !== 'DIV' ||
          replacedPaintTags.has(node.tagName) || node.getClientRects().length !== 1) return [];
      const corners = [[rect.left, rect.top], [rect.right, rect.top], [rect.right, rect.bottom], [rect.left, rect.bottom]];
      if (!points || points.length !== 4 || points.some((p, i) => !close(p.x, corners[i][0]) || !close(p.y, corners[i][1]))) return [];
      const paint = surfaceRects[0];
      if (!close(paint.x, rect.left) || !close(paint.y, rect.top) || !close(paint.width, rect.width) || !close(paint.height, rect.height)) return [];
      const style = getComputedStyle(node), color = rgba(style.backgroundColor);
      if (!color || color[3] !== 1 || style.backgroundImage !== 'none' || style.backgroundClip !== 'border-box' ||
          style.boxShadow !== 'none' || style.outlineStyle !== 'none' || style.borderImageSource !== 'none' ||
          style.pointerEvents === 'none' || ['inline', 'list-item'].includes(style.display) ||
          ['Top', 'Right', 'Bottom', 'Left'].some(side => parseFloat(style['border' + side + 'Width']) !== 0) ||
          ['TopLeft', 'TopRight', 'BottomRight', 'BottomLeft'].some(corner => style['border' + corner + 'Radius'] !== '0px') ||
          ['::before', '::after'].some(pseudo => !['none', 'normal'].includes(getComputedStyle(node, pseudo).content))) return [];
      for (let ancestor = node; ancestor; ancestor = ancestor.parentElement) {
        const css = getComputedStyle(ancestor);
        if (css.transform !== 'none' || (css.translate || 'none') !== 'none' || (css.rotate || 'none') !== 'none' ||
            (css.scale || 'none') !== 'none' || css.perspective !== 'none' || !['normal', '1'].includes(css.zoom || '1') ||
            css.filter !== 'none' || css.backdropFilter !== 'none' || css.clipPath !== 'none' || css.clip !== 'auto' ||
            (css.maskImage || css.webkitMaskImage || 'none') !== 'none' || css.mixBlendMode !== 'normal' ||
            Number(css.opacity) !== 1 || css.visibility !== 'visible' || css.animationName !== 'none' ||
            css.transitionDuration.split(',').some(value => parseFloat(value) !== 0)) return [];
        if ((css.contain || 'none').split(/\s+/).some(value => ['paint', 'content', 'strict'].includes(value)) ||
            (css.contentVisibility || 'visible') !== 'visible' || (css.overflowClipMargin || '0px') !== '0px') return [];
        if (['hidden', 'clip', 'scroll', 'auto'].some(value => [css.overflowX, css.overflowY].includes(value)) && (
            ['TopLeft', 'TopRight', 'BottomRight', 'BottomLeft'].some(corner => css['border' + corner + 'Radius'] !== '0px') ||
            ['Top', 'Right', 'Bottom', 'Left'].some(side => parseFloat(css['border' + side + 'Width']) !== 0))) return [];
      }
      const clip = visibleClip(node).bounds;
      if (!(x > clip.left && x < clip.right && y > clip.top && y < clip.bottom)) return [];
      // Prefer the vertical axis at a 1x1 corner; never exceed two probes.
      const candidates = vertical ? [[x, y - .25], [x, y + .25]] : [[x - .25, y], [x + .25, y]];
      return candidates.filter(([cx, cy]) =>
        cx > rect.left && cx < rect.right && cy > rect.top && cy < rect.bottom &&
        cx >= 0 && cx < innerWidth && cy >= 0 && cy < innerHeight &&
        cx > clip.left && cx < clip.right && cy > clip.top && cy < clip.bottom);
    };
"""
