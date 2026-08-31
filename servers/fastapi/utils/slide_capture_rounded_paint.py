"""Conservative viewport domains for ordinary rounded CSS surface paint."""

ROUNDED_PAINT_DOMAIN_JS = r"""
    const independentRoundedChild = (node) => {
      const children = Array.from(node.children);
      if (node.tagName !== 'DIV' || children.length < 1 || children.length > 2 || ownedTextNodes(node).length) return false;
      const parentQid = node.getAttribute('data-qid');
      // Match authored_dom_quality's patch-addressable QID grammar, without
      // accepting a duplicate owner or changing any child's independent probe.
      const uniqueQid = qid => typeof qid === 'string' &&
        qid.match(/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/)?.[0] === qid &&
        allQids.filter(owner => owner.getAttribute('data-qid') === qid).length === 1;
      return uniqueQid(parentQid) && children.every(child => {
        const childQid = child.getAttribute('data-qid');
        return child.namespaceURI === 'http://www.w3.org/1999/xhtml' && child.tagName === 'DIV' &&
          !child.children.length && parentQid !== childQid && uniqueQid(childQid);
      });
    };
    const roundedSurfaceRect = (node) => {
      const bounds = node.getBoundingClientRect();
      const rect = {x: bounds.x, y: bounds.y, width: bounds.width, height: bounds.height};
      const style = getComputedStyle(node);
      const length = (value, extent) => {
        const match = /^(\d+(?:\.\d+)?|\.\d+)(px|%)$/.exec(value);
        return match ? Number(match[1]) * (match[2] === '%' ? extent / 100 : 1) : null;
      };
      const radii = ['TopLeft', 'TopRight', 'BottomRight', 'BottomLeft'].map(corner => {
        const values = style['border' + corner + 'Radius'].trim().split(/\s+/);
        return values.length > 2 ? [null, null] : [length(values[0], rect.width), length(values[1] || values[0], rect.height)];
      });
      if (radii.every(pair => pair.every(value => value === 0))) return rect;
      const unsupported = reason => ({...rect, rounded_domain: {status: 'unsupported', reason}});
      if (radii.some(pair => pair.some(value => value === null || !Number.isFinite(value))) ||
          rect.width <= 0 || rect.height <= 0) return unsupported('radius_unresolved');
      if (node.namespaceURI !== 'http://www.w3.org/1999/xhtml' ||
          ['IMG', 'PICTURE', 'SVG', 'CANVAS', 'VIDEO', 'OBJECT', 'EMBED', 'TABLE', 'TD', 'TH'].includes(node.tagName) ||
          (node.children.length && !independentRoundedChild(node)) ||
          node.getClientRects().length !== 1 || ['inline', 'list-item'].includes(style.display))
        return unsupported('surface_box_unsupported');
      if (style.boxShadow !== 'none' || style.outlineStyle !== 'none' || style.borderImageSource !== 'none' ||
          style.backgroundImage !== 'none' || style.textShadow !== 'none' || parseFloat(style.webkitTextStrokeWidth) > 0 ||
          style.backgroundClip.split(',').some(value => !['border-box', 'padding-box', 'content-box'].includes(value.trim())) ||
          ['::before', '::after'].some(pseudo => !['none', 'normal'].includes(getComputedStyle(node, pseudo).content)))
        return unsupported('surface_paint_extent_unsupported');
      for (let ancestor = node; ancestor; ancestor = ancestor.parentElement) {
        const css = getComputedStyle(ancestor);
        if (css.transform !== 'none' || (css.translate || 'none') !== 'none' ||
            (css.rotate || 'none') !== 'none' || (css.scale || 'none') !== 'none' || css.perspective !== 'none' ||
            !['normal', '1'].includes(css.zoom || '1') || css.filter !== 'none' || css.backdropFilter !== 'none' ||
            css.clipPath !== 'none' || (css.maskImage || css.webkitMaskImage || 'none') !== 'none' ||
            css.clip !== 'auto' || css.mixBlendMode !== 'normal') return unsupported('surface_mapping_unsupported');
      }
      // CSS Backgrounds: scale all radii by the smallest side/sum ratio when
      // adjacent radii overlap. A zero axis is treated as square at sampling.
      const ratio = (extent, sum) => sum > 0 ? extent / sum : 1;
      const factor = Math.min(1,
        ratio(rect.width, radii[0][0] + radii[1][0]), ratio(rect.width, radii[3][0] + radii[2][0]),
        ratio(rect.height, radii[0][1] + radii[3][1]), ratio(rect.height, radii[1][1] + radii[2][1]));
      return {...rect, rounded_domain: {status: 'supported', radii: radii.map(pair => pair.map(value => value * factor))}};
    };
    const pointInOwnedSurface = (x, y, rect) => {
      if (x < rect.x || y < rect.y || x > rect.x + rect.width || y > rect.y + rect.height) return false;
      if (rect.rounded_domain?.status !== 'supported') return true;
      const localX = x - rect.x, localY = y - rect.y;
      const corners = [
        [localX, localY], [rect.width - localX, localY],
        [rect.width - localX, rect.height - localY], [localX, rect.height - localY]
      ];
      return rect.rounded_domain.radii.every(([rx, ry], index) => {
        const [cx, cy] = corners[index];
        if (!rx || !ry || cx >= rx || cy >= ry) return true;
        // Keep a one-device-pixel boundary band for antialiasing. Only points
        // beyond even this expanded ellipse are provably outside surface ink.
        return ((cx - rx) / (rx + 1)) ** 2 + ((cy - ry) / (ry + 1)) ** 2 <= 1;
      });
    };
"""
