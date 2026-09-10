"""Vertical-spacing candidate policy and JavaScript members for local fitting."""

from __future__ import annotations

LIGHT_SPACING_CANDIDATES = ((1.0, 1.0), (1.0, 0.85))
FONT_ONLY_CANDIDATES = ((0.975, 1.0), (0.95, 1.0), (0.925, 1.0), (0.90, 1.0))
COMBINED_CANDIDATES = (
    (1.0, 0.70),
    (1.0, 0.55),
    (1.0, 0.50),
    (1.0, 0.40),
    (0.975, 0.85),
    (0.95, 0.70),
    (0.925, 0.55),
    (0.90, 0.40),
)


SPACING_MEMBERS = r"""
  _flowHeightConflicts(card, leaves) {
    const conflicts = [];
    const parents = [card].concat(Array.from(card.querySelectorAll('[data-qid]')));
    for (const parent of parents) {
      const ps = this._struct(parent);
      if (ps.display !== 'block' && !(ps.display === 'flex' && ps.flexDirection === 'column')) continue;
      const children = Array.from(parent.children).filter((node) => {
        if (!node.hasAttribute('data-qid') || !this._eligible(node)) return false;
        const item = this._struct(node);
        return ['static', 'relative'].includes(item.position) && item.transform === 'none' &&
          item.display !== 'none' && item.marginTop >= 0 && item.marginBottom >= 0;
      });
      for (let index = 0; index + 1 < children.length; index += 1) {
        const first = children[index], next = children[index + 1];
        const a = first.getBoundingClientRect(), b = next.getBoundingClientRect();
        if (this._struct(first).explicitHeight === null || this._isCardLike(first)) continue;
        if (b.top < a.bottom - this.C.geometry_tolerance_px ||
            Math.min(a.right, b.right) <= Math.max(a.left, b.left)) continue;
        const owned = leaves.filter((leaf) => first.contains(leaf));
        const inks = owned.map((leaf) => this._ink(leaf)).filter(Boolean);
        const bottom = inks.length ? Math.max.apply(null, inks.map((ink) => ink.bottom)) : a.top;
        if (bottom > b.top + this.C.escape_tolerance_px) conflicts.push({first, next, owned});
      }
    }
    return conflicts;
  },
  _spacingPlan(nodes, factor, conflicts, relaxHeights) {
    const plan = [];
    for (const node of nodes) {
      const item = this._struct(node), declarations = [], reductions = [];
      const flow = ['static', 'relative'].includes(item.position) ||
        (item.position === 'absolute' && this._plainGeometry(node) && node.offsetParent &&
          this._parentRootEligible(node.offsetParent, node));
      const verticalGap = item.display === 'grid' ||
        (item.display === 'flex' && item.flexDirection === 'column');
      let removedPadding = 0;
      if (flow) for (const name of ['paddingTop', 'paddingBottom', 'marginTop', 'marginBottom', 'rowGap']) {
        if ((node === nodes[0] && name.startsWith('margin')) || (name === 'rowGap' && !verticalGap)) continue;
        const value = item[name];
        if (!(value > this.C.min_spacing_px)) continue;
        const reduced = this._round(Math.max(this.C.min_spacing_px, value * factor), 2);
        if (reduced === value) continue;
        declarations.push(this._cssName(name) + ':' + reduced + 'px');
        reductions.push({name, before: value, after: reduced});
        if (name.startsWith('padding')) removedPadding += value - reduced;
      }
      if (node !== nodes[0] && removedPadding > 0) {
        if (item.minHeight > 0) declarations.push('min-height:' +
          this._round(Math.max(0, item.minHeight - removedPadding), 2) + 'px');
        if (item.ownText && item.explicitHeight !== null) declarations.push('height:' +
          this._round(Math.max(0, item.explicitHeight - removedPadding), 2) + 'px');
      }
      if (relaxHeights && conflicts.some((conflict) => conflict.first === node)) {
        declarations.push('height:auto', 'flex-shrink:0');
      }
      if (declarations.length) plan.push({node, kind: 'spacing', declarations: declarations.join(';'), reductions});
    }
    return plan;
  },
"""


__all__ = [
    "COMBINED_CANDIDATES",
    "FONT_ONLY_CANDIDATES",
    "LIGHT_SPACING_CANDIDATES",
    "SPACING_MEMBERS",
]
