"""Measured vertical-space plans, evaluated by the shared reversible search."""

from utils.slide_capture_fit_boundary import BOUNDARY_MEMBERS

PARENT_MEMBERS = BOUNDARY_MEMBERS + r"""
  _parentRootEligible(root, seed) {
    if (!root || !root.hasAttribute('data-qid') || !this._plainGeometry(root)) return false;
    const qid = root.getAttribute('data-qid'), s = getComputedStyle(root), item = this._struct(root);
    if (document.querySelectorAll('[data-qid="' + CSS.escape(qid) + '"]').length !== 1 ||
        item.explicitHeight === null || !['block', 'flex', 'grid'].includes(s.display) ||
        s.boxSizing !== 'border-box' || !['static', 'relative', 'absolute'].includes(s.position)) return false;
    if (s.position === 'absolute' && (!root.offsetParent || !this._plainGeometry(root.offsetParent) &&
        root.offsetParent !== document.body)) return false;
    if (root.querySelector('img,canvas,video,object,embed,iframe,svg')) return false;
    return this._box(root).height > 0 && this._box(root).width > 0;
  },
  _parentRoots(seed) {
    const result = []; let current = seed.parentElement;
    const depth = Math.min(4, Math.max(1, this._number(this.P.parent_reflow_max_depth, 4)));
    for (let index = 0; current && current !== document.body && index < depth; index += 1, current = current.parentElement) {
      const protectedBoundary = this._protected(current);
      if (protectedBoundary) {
        this._last.parent_exclusions.push({qid: current.getAttribute('data-qid'), seed_qid: seed.getAttribute('data-qid'),
          ancestor_distance: index + 1, reason: 'protected_boundary'});
        break;
      }
      if (this._parentRootEligible(current, seed)) result.push(current);
      else this._last.parent_exclusions.push({qid: current.getAttribute('data-qid'), seed_qid: seed.getAttribute('data-qid'),
        ancestor_distance: index + 1, reason: this._struct(current).explicitHeight === null ?
          'fixed_boundary_unavailable' : 'unsupported_containing_block'});
    }
    return result;
  },
  _slackRoot(roots) {
    // The nearest fixed-height ancestor is the natural scope, except when it is
    // a grid: the space a grown row needs comes from the grid's following
    // siblings, which live in the grid's parent, and materializeGrowth only
    // reclaims them when the grid is not the root (sharedGrid). Step up once.
    const nearest = roots[0];
    if (roots.length > 1 && getComputedStyle(nearest).display === 'grid' &&
        roots[1] === nearest.parentElement) return roots[1];
    return nearest;
  },
  _parentSnapshot(root, qids) {
    const elements = [root].concat(Array.from(root.querySelectorAll('[data-qid]'))), leaves = this._allLeaves(root);
    const siblings = [];
    for (const parent of elements) {
      const children = Array.from(parent.children).filter(node => node.hasAttribute('data-qid') && !this._protected(node));
      for (let i = 0; i < children.length; i += 1) for (let j = i + 1; j < children.length; j += 1)
        siblings.push({first: children[i], second: children[j], depth: this._collision(children[i], children[j])});
    }
    return {rootBox: this._box(root), boxes: new Map(elements.map(node => [node, this._box(node)])),
      inks: new Map(leaves.map(node => [node, this._ink(node)])),
      siblings,
      outside: new Map(qids.filter(node => node !== root && !root.contains(node)).map(node => [node, this._box(node)])),
      escapes: new Map(elements.map(node => [node, this._escape(node, this._allLeaves(node), 0)])),
      occluded: new Map(leaves.map(node => [node, this._textOccluded(node)]))};
  },
  _mergePlans(first, second) {
    const merged = new Map();
    for (const item of first.concat(second)) {
      const key = item.node.getAttribute('data-qid') + ':' + item.kind, previous = merged.get(key);
      merged.set(key, previous ? Object.assign({}, item, {
        declarations: previous.declarations + ';' + item.declarations,
        reductions: (previous.reductions || []).concat(item.reductions || [])}) : Object.assign({}, item));
    }
    return Array.from(merged.values());
  },
  _availableBottom(node, root, moving) {
    const box = this._box(node), rootBox = this._box(root), style = getComputedStyle(root);
    let safe = Math.min(rootBox.bottom - this._px(style.paddingBottom) - this._px(style.borderBottomWidth), innerHeight);
    for (const other of Array.from(root.querySelectorAll('[data-qid]'))) {
      if (other === node || node.contains(other) || other.contains(node) || moving.has(other) ||
          Array.from(moving).some(parent => parent.contains(other))) continue;
      const b = this._box(other);
      if (b.top < box.bottom - this.C.geometry_tolerance_px ||
          Math.min(b.right, box.right) <= Math.max(b.left, box.left)) continue;
      if (this._protected(other) || this._isCardLike(other) || this._ownedTextNodes(other).length)
        safe = Math.min(safe, b.top - this.C.min_spacing_px);
    }
    for (const run of this._runs) {
      const slot = this._slotFor(run.node);
      if (slot && slot.y >= box.bottom) safe = Math.min(safe, slot.y - this.C.min_spacing_px);
    }
    return safe;
  },
  _parentPlan(region, baseline) {
    const boundary = this._boundaryPlan(region, baseline); if (boundary.invalid) return boundary;
    const root = region.card, plan = boundary.plan.slice(), measurements = boundary.measurements.slice(), moving = new Set(), growthRequests = new Map();
    const add = (node, declarations) => {
      if (!this._plainGeometry(node)) return false;
      plan.push({node, kind: 'spacing', declarations, reductions: []}); moving.add(node); return true;
    };
    const moveDown = (node, amount) => {
      const s = getComputedStyle(node);
      if (s.position !== 'absolute' || !node.offsetParent || !root.contains(node.offsetParent)) return false;
      return add(node, 'top:' + this._round(this._box(node).top - this._box(node.offsetParent).top -
        this._px(getComputedStyle(node.offsetParent).borderTopWidth) + amount, 3) + 'px;bottom:auto');
    };
    const grow = (nodes, required) => {
      const key = nodes.map(node => node.getAttribute('data-qid')).sort().join('|');
      const previous = growthRequests.get(key);
      // Every request is measured against the same pre-space DOM. Shared row
      // requests therefore take their maximum, not a sum or a last-write win.
      growthRequests.set(key, {nodes, required: Math.max(required, previous ? previous.required : 0),
        requests: (previous ? previous.requests : []).concat(required)});
      return null;
    };
    const materializeGrowth = (nodes, required, requests) => {
      for (const node of nodes) moving.add(node);
      const grid = nodes[0].parentElement;
      const sharedGrid = nodes.every(node => node.parentElement === grid) && grid !== root &&
        getComputedStyle(grid).display === 'grid' && this._struct(grid).explicitHeight !== null;
      const siblings = sharedGrid && grid.parentElement === root ? Array.from(root.children) : [];
      const following = siblings.slice(siblings.indexOf(grid) + 1).filter(node => node.hasAttribute('data-qid'));
      let reclaimed = 0;
      for (const node of following) {
        if (!this._plainGeometry(node) || !['static', 'relative'].includes(getComputedStyle(node).position)) continue;
        const box = this._box(node), s = getComputedStyle(node), inks = this._allLeaves(node).map(leaf => this._ink(leaf)).filter(Boolean);
        if (!inks.length || this._struct(node).explicitHeight === null) continue;
        const minimum = Math.max(...inks.map(ink => ink.bottom)) - Math.min(...inks.map(ink => ink.top)) +
          this._px(s.paddingTop) + this._px(s.paddingBottom) + this._px(s.borderTopWidth) + this._px(s.borderBottomWidth);
        const slack = Math.max(0, box.height - minimum - this.C.min_spacing_px);
        const take = Math.min(slack, Math.max(0, required - reclaimed));
        if (take > 0 && add(node, 'height:' + this._ceil(box.height - take, 3) + 'px;flex-shrink:0')) reclaimed += take;
      }
      if (sharedGrid) moving.add(grid);
      for (const node of following) moving.add(node);
      const boundary = following.length ? this._availableBottom(following[following.length - 1], root, moving) -
        this._box(following[following.length - 1]).bottom + reclaimed :
        Math.min(...nodes.map(node => this._availableBottom(node, root, moving) - this._box(node).bottom));
      const available = Math.max(0, boundary);
      const cap = Math.min(...nodes.map(node => Math.min(baseline.boxes.get(node).height * 0.50, baseline.rootBox.height * 0.10)));
      measurements.push({qid: nodes[0].getAttribute('data-qid'), required_growth_px: this._round(required, 3),
        available_growth_px: this._round(available, 3), growth_cap_px: this._round(cap, 3),
        shared_row_qids: nodes.map(node => node.getAttribute('data-qid')),
        merged_required_growth_px: requests.map(value => this._round(value, 3))});
      if (required > available + this.C.geometry_tolerance_px) return 'parent_space_unavailable';
      if (required > cap + this.C.geometry_tolerance_px) return 'parent_growth_cap';
      const height = Math.max(...nodes.map(node => this._box(node).height)) + required;
      for (const node of nodes) if (!add(node, 'height:' + this._ceil(height, 3) + 'px;flex-shrink:0')) return 'unsupported_geometry';
      if (sharedGrid && !add(grid, 'height:' + this._ceil(this._box(grid).height + required, 3) + 'px;flex-shrink:0'))
        return 'unsupported_geometry';
      return null;
    };
    for (const seed of region.collisions) {
      if (seed.kind === 'absolute_rows') {
        const first = this._box(seed.first), second = this._box(seed.second);
        const amount = Math.max(0, first.bottom + this.C.min_spacing_px - second.top);
        const available = this._availableBottom(seed.second, root, new Set([seed.first, seed.second])) - second.bottom;
        measurements.push({qid: seed.second.getAttribute('data-qid'), required_growth_px: amount,
          available_growth_px: available, growth_cap_px: baseline.rootBox.height * 0.10});
        if (amount > available + this.C.geometry_tolerance_px) return {invalid: 'parent_space_unavailable', measurements};
        if (amount > baseline.rootBox.height * 0.10 + this.C.geometry_tolerance_px)
          return {invalid: 'parent_growth_cap', measurements};
        if (!moveDown(seed.second, amount)) return {invalid: 'unsupported_geometry', measurements};
      } else if (this._bottomBand(seed.second)) {
        const band = this._box(seed.second), inks = this._paintRects(seed.first, true);
        const relevant = inks.filter(box => Math.min(box.right, band.right) > Math.max(box.left, band.left));
        const bottom = relevant.length ? Math.max(...relevant.map(box => box.bottom)) : band.top;
        const required = Math.max(0, bottom + this.C.min_spacing_px - band.top);
        if (!(required > 0)) continue;
        const owner = seed.second.offsetParent;
        if (owner && owner !== root && root.contains(owner)) {
          const grid = owner.parentElement, peers = grid && getComputedStyle(grid).display === 'grid' ?
            Array.from(grid.children).filter(node => node.hasAttribute('data-qid') &&
              Math.abs(this._box(node).top - this._box(owner).top) <= this.C.geometry_tolerance_px) : [owner];
          const invalid = grow(peers, required); if (invalid) return {invalid, measurements};
        } else {
          const available = this._availableBottom(seed.second, root, new Set([seed.second])) - band.bottom;
          measurements.push({qid: seed.second.getAttribute('data-qid'), required_growth_px: required,
            available_growth_px: available, growth_cap_px: baseline.rootBox.height * 0.10});
          if (required > available + this.C.geometry_tolerance_px) return {invalid: 'parent_space_unavailable', measurements};
          if (!moveDown(seed.second, required)) return {invalid: 'unsupported_geometry', measurements};
        }
      }
    }
    for (const member of region.members) {
      const card = member.card; if (card === root || boundary.handled.has(card)) continue;
      const required = Math.max(0, this._escape(card, this._regionLeaves(card), 0));
      if (required <= this.C.escape_tolerance_px) continue;
      const parent = card.parentElement, peers = parent && getComputedStyle(parent).display === 'grid' ?
        Array.from(parent.children).filter(node => node.hasAttribute('data-qid') &&
          Math.abs(this._box(node).top - this._box(card).top) <= this.C.geometry_tolerance_px) : [card];
      const invalid = grow(peers, required + this.C.geometry_tolerance_px); if (invalid) return {invalid, measurements};
    }
    for (const request of growthRequests.values()) {
      const invalid = materializeGrowth(request.nodes, request.required, request.requests);
      if (invalid) return {invalid, measurements};
    }
    return {plan: this._mergePlans([], plan), measurements, rootGrowth: boundary.rootGrowth};
  },
"""

__all__ = ["PARENT_MEMBERS"]
