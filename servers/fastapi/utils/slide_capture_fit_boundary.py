"""Measured parent-border and terminal-card growth within protected outer bounds."""

BOUNDARY_MEMBERS = r"""
  _boundaryVisible(node) {
    const style = getComputedStyle(node), box = this._box(node);
    return style.display !== 'none' && !['hidden', 'collapse'].includes(style.visibility) &&
      Number(style.opacity) > 0 && box.width > 0 && box.height > 0;
  },
  _boundaryTerminal(node) {
    const parent = node.parentElement; if (!parent) return false;
    const box = this._box(node), siblings = Array.from(parent.children), index = siblings.indexOf(node);
    return !siblings.slice(index + 1).some(other => {
      if (!other.hasAttribute('data-qid') || !this._boundaryVisible(other)) return false;
      const b = this._box(other);
      return Math.min(b.right, box.right) > Math.max(b.left, box.left) && b.top >= box.top;
    });
  },
  _boundaryNeed(node) {
    if (!this._plainGeometry(node) || this._struct(node).explicitHeight === null ||
        getComputedStyle(node).boxSizing !== 'border-box') return null;
    const box = this._box(node), style = getComputedStyle(node), border = this._px(style.borderBottomWidth);
    const padding = this._px(style.paddingBottom), leaves = this._allLeaves(node).filter(leaf => this._boundaryVisible(leaf));
    const inkBottom = Math.max(box.top, ...leaves.map(leaf => this._ink(leaf)).filter(Boolean).map(ink => ink.bottom));
    let childBottom = box.top;
    for (const child of Array.from(node.children)) {
      if (!child.hasAttribute('data-qid') || !this._boundaryVisible(child) || this._protected(child)) continue;
      if (child.getAttribute('data-allow-overlap') === 'true') continue;
      const cs = getComputedStyle(child);
      if (!['static', 'relative'].includes(cs.position)) continue;
      if (this._isCardLike(child) || this._px(cs.borderBottomWidth) > 0) childBottom = Math.max(childBottom, this._box(child).bottom);
    }
    const edge = box.bottom - border, childEscape = childBottom - edge, textEscape = inkBottom - edge;
    const childBoundary = childEscape > this.C.geometry_tolerance_px;
    const smallLastText = textEscape > this.C.geometry_tolerance_px && textEscape <= this.C.min_spacing_px * 4 &&
      this._isCardLike(node) && this._boundaryTerminal(node);
    if (!childBoundary && !smallLastText) return null;
    // A child already owns its padding. Align its border with the parent's inner
    // border; only escaped text needs an additional parent text inset.
    const requiredBottom = Math.max(childBottom + border, inkBottom + border + (smallLastText ? padding : 0));
    return {kind: childBoundary ? 'child_boundary' : 'terminal_card', required: Math.max(0,
      requiredBottom - box.bottom),
      child_escape_px: Math.max(0, childEscape), text_escape_px: Math.max(0, textEscape)};
  },
  _boundaryRegions(qids, regions) {
    for (const node of qids) {
      if (!this._budgetLeft()) break;
      if (this._struct(node).explicitHeight === null) continue;
      this._measurements += 1;
      const need = this._boundaryNeed(node); if (!need) continue;
      const existing = regions.find(region => region.card === node);
      if (existing) existing.boundary = need.kind;
      else regions.push({card: node, leaves: this._allLeaves(node), conflicts: [], boundary: need.kind,
        escape_px: Math.max(need.child_escape_px, need.text_escape_px)});
    }
    return regions;
  },
  _boundarySafeBottom(node, root) {
    let outer = node === root ? root.parentElement : root, found = false;
    for (let depth = 0; outer && depth <= 4; depth += 1, outer = outer.parentElement) {
      if (outer === document.body || outer === document.documentElement || this._struct(outer).explicitHeight !== null) {
        found = true; break;
      }
    }
    if (!found || !outer) return null;
    const os = getComputedStyle(outer), bounds = this._box(outer), box = this._box(node);
    let safe = Math.min(innerHeight, bounds.bottom - this._px(os.borderBottomWidth) - this._px(os.paddingBottom));
    for (const other of Array.from(document.querySelectorAll('[data-qid]'))) {
      if (other === node || other.contains(node) || !this._boundaryVisible(other)) continue;
      const b = this._box(other);
      if (Math.min(b.right, box.right) <= Math.max(b.left, box.left)) continue;
      const slot = this._slotFor(other);
      if (slot && slot.y >= box.top) safe = Math.min(safe, slot.y - this.C.min_spacing_px);
      if (node.contains(other)) {
        if (this._protected(other) && b.top >= box.top)
          safe = Math.min(safe, b.top - this.C.min_spacing_px);
        continue;
      }
      if (b.top >= box.bottom - this.C.geometry_tolerance_px &&
          (this._isCardLike(other) || this._allLeaves(other).length || this._protected(other)))
        safe = Math.min(safe, b.top - this.C.min_spacing_px);
    }
    return safe;
  },
  _boundaryPlan(region, baseline) {
    const plan = [], handled = new Set(), measurements = []; let rootGrowth = null;
    const members = region.members.filter(member => member.boundary);
    for (const member of members) {
      if (!this._budgetLeft()) return {invalid: 'budget_exhausted', plan: [], handled, measurements};
      this._measurements += 1;
      const node = member.card, need = this._boundaryNeed(node); handled.add(node);
      if (!need) continue;
      const before = baseline.boxes.get(node), box = this._box(node), safe = this._boundarySafeBottom(node, region.card);
      const cap = Math.min(before.height * 0.50, baseline.rootBox.height * 0.10);
      const required = this._ceil(need.required, 3), available = safe === null ? 0 : Math.max(0, safe - box.bottom);
      measurements.push({qid: node.getAttribute('data-qid'), kind: need.kind, required_growth_px: required,
        available_growth_px: this._round(available, 3), growth_cap_px: this._round(cap, 3), safe_bottom_px: safe});
      if (safe === null || required > available + this.C.geometry_tolerance_px)
        return {invalid: 'parent_space_unavailable', plan: [], handled, measurements};
      const target = this._ceil(box.height + required, 3);
      if (target - before.height > cap + this.C.geometry_tolerance_px)
        return {invalid: 'parent_growth_cap', plan: [], handled, measurements};
      plan.push({node, kind: 'spacing', declarations: 'height:' + target + 'px;flex-shrink:0', reductions: []});
      if (node === region.card) rootGrowth = {qid: node.getAttribute('data-qid'), from_height: before.height,
        target_height: target, safe_bottom: safe, max_growth_px: cap};
    }
    return {plan, handled, measurements, rootGrowth};
  },
  _rootGrowthHard(node, before, growth) {
    if (!growth || growth.qid !== node.getAttribute('data-qid') || this._protected(node)) return false;
    const after = this._box(node), tolerance = this.C.geometry_tolerance_px;
    return ['left', 'top', 'width'].every(key => Math.abs(after[key] - before[key]) <= tolerance) &&
      after.height >= before.height && after.height - before.height <= growth.max_growth_px + tolerance &&
      Math.abs(after.height - growth.target_height) <= tolerance && after.bottom <= growth.safe_bottom + tolerance;
  },
  _committedRootGrowthAllowed(node, before) {
    const operation = this._last.operations.find(item => item.qid === node.getAttribute('data-qid') && item.root_growth);
    return Boolean(operation && this._rootGrowthHard(node, before, operation.root_growth));
  },
"""

__all__ = ["BOUNDARY_MEMBERS"]
