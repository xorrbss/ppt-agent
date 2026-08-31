"""Conservative own-paint domains for simple, untransformed CSS polygons."""

POLYGON_PAINT_DOMAIN_JS = r"""
    const polygonSurfaceRect = (node, rect) => {
      const style = getComputedStyle(node);
      if (style.clipPath === 'none') return rect;
      const unsupported = reason => ({...rect, polygon_domain: {status: 'unsupported', reason}});
      const qid = node.getAttribute('data-qid');
      if (node.namespaceURI !== 'http://www.w3.org/1999/xhtml' || node.tagName !== 'DIV' ||
          node.children.length || ownedTextNodes(node).length || node.getClientRects().length !== 1 ||
          ['inline', 'list-item'].includes(style.display) ||
          typeof qid !== 'string' || qid.match(/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/)?.[0] !== qid ||
          allQids.filter(owner => owner.getAttribute('data-qid') === qid).length !== 1)
        return unsupported('polygon_surface_box_unsupported');
      if (![rect.x, rect.y, rect.width, rect.height].every(Number.isFinite) || rect.width <= 0 || rect.height <= 0)
        return unsupported('polygon_geometry_unresolved');
      const zero = value => /^0(?:\.0+)?(?:px|%)$/.test(value);
      if (['TopLeft', 'TopRight', 'BottomRight', 'BottomLeft'].some(corner =>
            !style['border' + corner + 'Radius'].trim().split(/\s+/).every(zero)) ||
          ['Top', 'Right', 'Bottom', 'Left'].some(side => !zero(style['border' + side + 'Width'])) ||
          style.boxShadow !== 'none' || style.outlineStyle !== 'none' || style.borderImageSource !== 'none' ||
          style.textShadow !== 'none' || parseFloat(style.webkitTextStrokeWidth) > 0 ||
          ['::before', '::after'].some(pseudo => !['none', 'normal'].includes(getComputedStyle(node, pseudo).content)))
        return unsupported('polygon_paint_extent_unsupported');
      for (let ancestor = node; ancestor; ancestor = ancestor.parentElement) {
        const css = getComputedStyle(ancestor);
        if (ancestor.namespaceURI !== 'http://www.w3.org/1999/xhtml' || css.transform !== 'none' ||
            (css.translate || 'none') !== 'none' || (css.rotate || 'none') !== 'none' ||
            (css.scale || 'none') !== 'none' || css.perspective !== 'none' || css.offsetPath !== 'none' ||
            !['normal', '1'].includes(css.zoom || '1') || css.filter !== 'none' || css.backdropFilter !== 'none' ||
            (ancestor !== node && css.clipPath !== 'none') ||
            (css.maskImage || css.webkitMaskImage || 'none') !== 'none' || css.clip !== 'auto' ||
            css.mixBlendMode !== 'normal') return unsupported('polygon_mapping_unsupported');
      }
      // Only the default border-box, four finite vertices and a strictly
      // convex winding are supported. Never infer URL/path/fill-rule geometry.
      const match = /^polygon\(([^()]*)\)$/.exec(style.clipPath);
      const pairs = match?.[1].split(',').map(value => value.trim().split(/\s+/));
      if (!pairs || pairs.length !== 4 || pairs.some(pair => pair.length !== 2))
        return unsupported('polygon_syntax_unsupported');
      const length = (value, extent) => {
        const parsed = /^(\d+(?:\.\d+)?|\.\d+)(px|%)$/.exec(value);
        const result = parsed ? Number(parsed[1]) * (parsed[2] === '%' ? extent / 100 : 1) : NaN;
        return Number.isFinite(result) && result >= 0 && result <= extent ? result : NaN;
      };
      const vertices = pairs.map(([x, y]) => ({x: length(x, rect.width), y: length(y, rect.height)}));
      if (vertices.some(point => !Number.isFinite(point.x) || !Number.isFinite(point.y)))
        return unsupported('polygon_vertex_unresolved');
      const cross = (a, b, c) => (b.x - a.x) * (c.y - b.y) - (b.y - a.y) * (c.x - b.x);
      const turns = vertices.map((a, index) => cross(a, vertices[(index + 1) % 4], vertices[(index + 2) % 4]));
      const winding = Math.sign(turns[0]);
      if (!winding || turns.some(turn => !Number.isFinite(turn) || Math.abs(turn) < 1e-6 || Math.sign(turn) !== winding))
        return unsupported('polygon_not_strictly_convex');
      return {...rect, polygon_domain: {status: 'supported', vertices, winding}};
    };
    const polygonEdgeDistances = (x, y, rect) => {
      const {vertices, winding} = rect.polygon_domain;
      const localX = x - rect.x, localY = y - rect.y;
      return vertices.map((a, index) => {
        const b = vertices[(index + 1) % vertices.length];
        return winding * ((b.x - a.x) * (localY - a.y) - (b.y - a.y) * (localX - a.x)) /
          Math.hypot(b.x - a.x, b.y - a.y);
      });
    };
    const pointInPolygonSurface = (x, y, rect) => {
      if (rect.polygon_domain?.status !== 'supported') return true;
      // Keep a full CSS-pixel exterior band. This is a conservative superset
      // of own polygon ink, not a replacement hit test or an ancestor clip.
      return polygonEdgeDistances(x, y, rect).every(distance => distance >= -1);
    };
    const pointInOwnedPaintSurface = (x, y, rect) =>
      pointInOwnedSurface(x, y, rect) && pointInPolygonSurface(x, y, rect);
    const polygonAAInteriorSamples = (node, x, y, textRects, surfaceRects, originalStack) => {
      if (textRects.length || surfaceRects.length !== 1) return null;
      const rect = surfaceRects[0];
      if (rect.polygon_domain?.status !== 'supported') return null;
      const distances = polygonEdgeDistances(x, y, rect);
      if (distances.filter(value => value < 0).length !== 1 || distances.some(value => value < -1)) return null;
      const clip = visibleClip(node).bounds;
      const insideClip = (px, py) => px > clip.left && px < clip.right && py > clip.top && py < clip.bottom;
      if (!insideClip(x, y)) return [];
      for (let ancestor = node.parentElement; ancestor; ancestor = ancestor.parentElement) {
        const css = getComputedStyle(ancestor);
        if (/(?:^|\s)(?:paint|content|strict)(?:\s|$)/.test(css.contain) || css.contentVisibility !== 'visible') return [];
        if (css.overflowX !== 'visible' || css.overflowY !== 'visible') {
          // The original hit stack must prove the exact rounded clipping
          // ancestor contains this point. Zero border makes its hit contour
          // the padding clip contour; never repair a parent-clipped sample.
          if (!originalStack.includes(ancestor) || css.overflowClipMargin !== '0px' ||
              ['Top', 'Right', 'Bottom', 'Left'].some(side => css['border' + side + 'Width'] !== '0px')) return [];
        }
      }
      const {vertices, winding} = rect.polygon_domain;
      const edge = distances.findIndex(distance => distance < 0);
      const a = vertices[edge], b = vertices[(edge + 1) % vertices.length];
      const length = Math.hypot(b.x - a.x, b.y - a.y);
      if (!Number.isFinite(length) || length <= 0) return [];
      // The inward unit normal increases signed distance by exactly inset;
      // unlike coordinate-wise centre signs it spends no step along the edge.
      const nx = -winding * (b.y - a.y) / length, ny = winding * (b.x - a.x) / length;
      return [.5, 1].map(inset => [
        x + nx * inset, y + ny * inset
      ]).filter(([px, py]) => insideClip(px, py) &&
        pointInOwnedSurface(px, py, rect) && polygonEdgeDistances(px, py, rect).every(distance => distance >= 0));
    };
"""
