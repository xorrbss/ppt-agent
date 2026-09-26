"""Bounded, read-only alpha sampling for source-fit raster images."""

IMAGE_ALPHA_PAINT_JS = (
r"""
    const sourceImageAlphaEnabled = Boolean(document.querySelector('[data-source-fit-policy="compose"""
r"""r-source-fit/v2"]'));
    const sourceImageAlphaCache = new WeakMap();
    const sourceImageAlphaData = (node) => {
      if (!sourceImageAlphaEnabled || node.tagName !== 'IMG') return null;
      if (sourceImageAlphaCache.has(node)) return sourceImageAlphaCache.get(node);
      sourceImageAlphaCache.set(node, null);
      if (!node.complete || !node.naturalWidth || !node.naturalHeight ||
          node.naturalWidth * node.naturalHeight > 16777216) return null;
      const style = getComputedStyle(node);
      // Additional paint or non-affine mapping keeps the previous conservative
      // rectangle verdict. Never infer transparent pixels from unsupported CSS.
      if (style.backgroundImage !== 'none' || visibleColor(style.backgroundColor) ||
          style.boxShadow !== 'none' || style.outlineStyle !== 'none' ||
          ['Top','Right','Bottom','Left'].some(side => px(style['border'+side+'Width']) > 0 || px(st"""
r"""yle['padding'+side]) > 0)) return null;
      let opacity = 1;
      for (let current = node; current; current = current.parentElement) {
        const css = getComputedStyle(current);
        if (css.transform !== 'none' || css.translate !== 'none' || css.rotate !== 'none' ||
            css.scale !== 'none' || css.perspective !== 'none' || !['normal','1'].includes(css.zoom) ||
            css.filter !== 'none' || css.backdropFilter !== 'none' || css.mixBlendMode !== 'normal' ||
            css.clipPath !== 'none' || (css.maskImage || css.webkitMaskImage || 'none') !== 'none') """
r"""return null;
        opacity *= Number(css.opacity || 1);
      }
      const rect = node.getBoundingClientRect();
      if (!(rect.width > 0 && rect.height > 0)) return null;
      let width = rect.width, height = rect.height;
      if (style.objectFit !== 'fill') {
        const contain = Math.min(rect.width / node.naturalWidth, rect.height / node.naturalHeight);
        const scale = style.objectFit === 'contain' ? contain
          : style.objectFit === 'cover' ? Math.max(rect.width / node.naturalWidth, rect.height / nod"""
r"""e.naturalHeight)
          : style.objectFit === 'none' ? 1 : style.objectFit === 'scale-down' ? Math.min(1, contain)"""
r""" : null;
        if (scale === null) return null;
        width = node.naturalWidth * scale; height = node.naturalHeight * scale;
      }
      const position = (style.objectPosition || '50% 50%').trim().split(/\s+/);
      if (position.length !== 2 || position.some(value => !/^-?[\d.]+%$/.test(value))) return null;
      try {
        const canvas = document.createElement('canvas');
        canvas.width = node.naturalWidth; canvas.height = node.naturalHeight;
        const context = canvas.getContext('2d', {willReadFrequently: true});
        if (!context) return null;
        context.drawImage(node, 0, 0);
        const value = {pixels: context.getImageData(0, 0, canvas.width, canvas.height).data,
          naturalWidth: canvas.width, naturalHeight: canvas.height, width, height, opacity,
          x: rect.x + (rect.width - width) * parseFloat(position[0]) / 100,
          y: rect.y + (rect.height - height) * parseFloat(position[1]) / 100};
        sourceImageAlphaCache.set(node, value);
        return value;
      } catch (_) { return null; }
    };
    const sourceImageAlphaAt = (node, x, y) => {
      const image = sourceImageAlphaData(node);
      if (!image) return null;
      const u = (x - image.x) / image.width, v = (y - image.y) / image.height;
      if (u < 0 || v < 0 || u >= 1 || v >= 1) return 0;
      const sx = u * image.naturalWidth - .5, sy = v * image.naturalHeight - .5;
      // Maximum neighbouring alpha is conservative at antialiased boundaries.
      let alpha = 0;
      for (const yy of [Math.floor(sy), Math.ceil(sy)]) for (const xx of [Math.floor(sx), Math.ceil("""
r"""sx)]) {
        const px = Math.max(0, Math.min(image.naturalWidth - 1, xx));
        const py = Math.max(0, Math.min(image.naturalHeight - 1, yy));
        alpha = Math.max(alpha, image.pixels[(py * image.naturalWidth + px) * 4 + 3]);
      }
      return alpha / 255 * image.opacity;
    };
"""
)
