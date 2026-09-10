"""Semantic-gated label morphology and measured donor-space candidate bundles."""

MORPH_MEMBERS = r"""
  _morphExplicitSemantics(node, boundary) {
    const plainRoles = new Set(['body', 'label', 'caption', 'supporting', 'decoration', '']);
    for (let current = node; current; current = current.parentElement) {
      const role = String(current.getAttribute('role') || '').trim().toLowerCase();
      const semantic = String(current.getAttribute('data-semantic-role') || '').trim().toLowerCase();
      if ((role && !['none', 'presentation'].includes(role)) || !plainRoles.has(semantic) ||
          current.matches('button,a,input,select,textarea,summary,meter,progress') ||
          ['aria-pressed', 'aria-checked', 'aria-selected', 'aria-current', 'data-status', 'data-state',
            'data-semantic-color', 'tabindex'].some(name => current.hasAttribute(name))) return true;
      if (current === boundary) break;
    }
    return false;
  },
  _morphRgb(value) {
    const match = String(value || '').trim().match(/^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)(?:\s*,\s*([\d.]+))?\s*\)$/i);
    if (!match) return null;
    const channels = match.slice(1, 4).map(Number), alpha = match[4] === undefined ? 1 : Number(match[4]);
    return channels.every(value => Number.isFinite(value) && value >= 0 && value <= 255) &&
      Number.isFinite(alpha) && alpha >= 0 && alpha <= 1 ? {channels, alpha} : null;
  },
  _morphContrast(node) {
    const text = this._morphRgb(getComputedStyle(node).color);
    if (!text || text.alpha !== 1) return {reason: 'text_color_unverifiable'};
    let background = null;
    for (let current = node; current; current = current.parentElement) {
      const style = getComputedStyle(current);
      if (Number(style.opacity) !== 1 || String(style.backgroundImage || 'none') !== 'none' ||
          String(style.filter || 'none') !== 'none' || String(style.backdropFilter || 'none') !== 'none' ||
          String(style.mixBlendMode || 'normal') !== 'normal') return {reason: 'background_effect_unverifiable'};
      const color = this._morphRgb(style.backgroundColor);
      if (!color || (color.alpha !== 0 && color.alpha !== 1)) return {reason: 'background_color_unverifiable'};
      if (!background && color.alpha === 1) background = color;
    }
    if (!background) return {reason: 'background_unresolved'};
    const luminance = color => color.channels.map(value => {
      const channel = value / 255;
      return channel <= 0.04045 ? channel / 12.92 : Math.pow((channel + 0.055) / 1.055, 2.4);
    }).reduce((sum, value, index) => sum + value * [0.2126, 0.7152, 0.0722][index], 0);
    const foregroundL = luminance(text), backgroundL = luminance(background);
    return {ratio: (Math.max(foregroundL, backgroundL) + 0.05) / (Math.min(foregroundL, backgroundL) + 0.05),
      foreground: text.channels, background: background.channels};
  },
  _morphContrastHard(morph) {
    let minimum = Infinity;
    for (const [label, before] of morph.contrasts) {
      const after = this._morphContrast(label);
      const detail = after.reason || (after.ratio < 4.5 ? 'below_4_5' :
        after.ratio + 1e-10 < before.ratio ? 'contrast_decreased' : null);
      if (detail) {
        morph.diagnostic.semantic_guard = {passed: false, reason: 'semantic_contrast', detail,
          qid: label.getAttribute('data-qid'), before: before.ratio, after: after.ratio || null};
        return {ok: false, reason: 'semantic_contrast'};
      }
      minimum = Math.min(minimum, after.ratio);
    }
    morph.diagnostic.semantic_guard = {passed: true, reason: 'uniform_label_decoration_only',
      minimum_contrast: this._round(minimum, 4)};
    return {ok: true};
  },
  _absoluteTextEligible(node, region) {
    if (!region || !this._plainGeometry(node) || getComputedStyle(node).position !== 'absolute' ||
        !this._ownedTextNodes(node).length || !region.collisions.some(seed => seed.kind === 'absolute_rows' &&
          (seed.first === node || seed.second === node))) return false;
    const ink = this._ink(node), box = this._box(node), style = getComputedStyle(node);
    if (!ink) return false;
    return ink.right > box.right + this.C.escape_tolerance_px || ink.bottom > box.bottom + this.C.escape_tolerance_px ||
      (['hidden', 'clip'].includes(style.overflowX) && (ink.left < box.left - 0.5 || ink.right > box.right + 0.5)) ||
      (['hidden', 'clip'].includes(style.overflowY) && (ink.top < box.top - 0.5 || ink.bottom > box.bottom + 0.5));
  },
  _morphReceiver(root, node) {
    let receiver = node;
    while (receiver && receiver.parentElement !== root) receiver = receiver.parentElement;
    return receiver && this._parentRootEligible(receiver, node) ? receiver : null;
  },
  _morphDonors(root, receiver) {
    if (!receiver || !this._budgetLeft()) return [];
    const box = this._box(receiver);
    return Array.from(root.children).filter(node => {
      if (node === receiver || !this._parentRootEligible(node, receiver)) return false;
      const b = this._box(node), style = getComputedStyle(node);
      return ['grid', 'flex'].includes(style.display) && b.top >= box.bottom - this.C.geometry_tolerance_px &&
        Math.min(b.right, box.right) > Math.max(b.left, box.left) && !this._protected(node);
    }).slice(0, 4);
  },
  _morphScopeRoot(common, fallback, seed) {
    if (seed.kind !== 'absolute_rows') return fallback;
    // The common containing block plus at most four parent links is inspected.
    let current = common;
    for (let links = 0; current && current !== document.body && links <= 4; links += 1, current = current.parentElement) {
      if (!this._parentRootEligible(current, seed.first)) continue;
      const receiver = this._morphReceiver(current, seed.second);
      if (receiver && this._morphDonors(current, receiver).length) return current;
    }
    return fallback;
  },
  _morphLabelGroup(group) {
    const labels = Array.from(group.children);
    const layout = getComputedStyle(group);
    if (labels.length < 4 || labels.length > 16 || !this._plainGeometry(group) ||
        layout.display !== 'flex' || layout.flexDirection !== 'row' || layout.flexWrap !== 'wrap') return null;
    const signature = node => {
      const s = getComputedStyle(node);
      return ['color', 'backgroundColor', 'backgroundImage', 'borderTopColor', 'borderRightColor',
        'borderBottomColor', 'borderLeftColor', 'borderTopWidth', 'borderRightWidth', 'borderBottomWidth',
        'borderLeftWidth', 'borderTopStyle', 'borderRightStyle', 'borderBottomStyle', 'borderLeftStyle',
        'borderTopLeftRadius', 'borderTopRightRadius', 'borderBottomLeftRadius', 'borderBottomRightRadius',
        'paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft', 'opacity', 'fontFamily', 'fontSize',
        'fontWeight', 'lineHeight', 'letterSpacing', 'textDecorationLine'].map(key => String(s[key]))
        .concat(node.getAttribute('data-semantic-role') || '').join('|');
    };
    const first = signature(labels[0]), tops = [];
    for (const label of labels) {
      const text = String(label.textContent || '').trim(), s = getComputedStyle(label);
      if (this._morphExplicitSemantics(label, group.parentElement) ||
          !label.hasAttribute('data-qid') || !this._plainGeometry(label) || !this._ownedTextNodes(label).length ||
          label.querySelector('*') || !text || text.length > 60 || /[\r\n.!?。！？]/.test(text) ||
          !['static', 'relative'].includes(s.position) || s.backgroundImage !== 'none' ||
          s.boxShadow !== 'none' || signature(label) !== first ||
          label.hasAttribute('data-semantic-color') || label.hasAttribute('data-status') ||
          label.hasAttribute('aria-current') || label.hasAttribute('aria-selected')) return null;
      const top = this._box(label).top;
      if (!tops.some(value => Math.abs(value - top) <= this.C.geometry_tolerance_px)) tops.push(top);
    }
    return tops.length >= 2 ? {group, labels} : null;
  },
  _morphSemanticPlan(donor) {
    const panels = Array.from(donor.children), labels = [], groups = [], plan = [];
    if (panels.length < 2 || panels.some(node => !node.hasAttribute('data-qid'))) return {invalid: 'donor_columns_unsupported'};
    const top = this._box(panels[0]).top;
    const add = (node, declarations, reductions) => plan.push({node, kind: 'spacing', declarations, reductions: reductions || []});
    for (const panel of panels) {
      const ps = getComputedStyle(panel);
      if (this._morphExplicitSemantics(panel, donor) || !this._plainGeometry(panel) ||
          Math.abs(this._box(panel).top - top) > this.C.geometry_tolerance_px ||
          !['static', 'relative'].includes(ps.position) || ps.backgroundImage !== 'none' ||
          panel.querySelector('img,svg,canvas,video,object,embed,iframe,table')) return {invalid: 'donor_semantic_guard'};
      const children = Array.from(panel.children);
      if (!children.length || children.some(child => this._morphExplicitSemantics(child, donor) ||
          !child.hasAttribute('data-qid') || !this._plainGeometry(child)))
        return {invalid: 'donor_semantic_guard'};
      for (const child of children) {
        const group = this._morphLabelGroup(child);
        if (group) {
          groups.push(group); labels.push(...group.labels);
          add(child, 'display:flex;flex-wrap:nowrap;column-gap:' + this.C.min_spacing_px +
            'px;row-gap:0;align-items:baseline;margin-top:' + this.C.min_spacing_px + 'px;margin-bottom:0');
          for (const label of group.labels) add(label,
            'display:inline-block;white-space:nowrap;flex:0 0 auto;background-color:transparent;' +
            'border-color:transparent;border-radius:0;padding:0;margin-top:0;margin-bottom:0');
        } else {
          const cs = getComputedStyle(child);
          if (!this._ownedTextNodes(child).length || child.querySelector('*') ||
              this._isCardLike(child) || !['static', 'relative'].includes(cs.position))
            return {invalid: 'donor_nested_or_semantic_content'};
          const parts = [], reductions = [];
          for (const name of ['marginTop', 'marginBottom']) {
            const before = this._px(cs[name]); if (before <= this.C.min_spacing_px) continue;
            parts.push(this._cssName(name) + ':' + this.C.min_spacing_px + 'px');
            reductions.push({name, before, after: this.C.min_spacing_px});
          }
          if (parts.length) add(child, parts.join(';'), reductions);
        }
      }
      const reductions = ['paddingTop', 'paddingBottom'].filter(name => this._px(ps[name]) > this.C.min_spacing_px)
        .map(name => ({name, before: this._px(ps[name]), after: this.C.min_spacing_px}));
      add(panel, 'height:auto;min-height:0;min-width:0;align-self:start;' + reductions.map(value =>
        this._cssName(value.name) + ':' + value.after + 'px').join(';'), reductions);
    }
    if (groups.length !== 1) return {invalid: 'donor_label_group_ambiguous'};
    add(donor, 'height:auto;min-height:0;align-items:start;grid-template-rows:auto');
    const contrasts = new Map();
    for (const label of labels) {
      const contrast = this._morphContrast(label);
      if (contrast.reason) return {invalid: 'semantic_contrast', detail: contrast.reason};
      contrasts.set(label, contrast);
    }
    return {plan, panels, groups, labels, contrasts, semantic_guard: 'uniform_label_decoration_only'};
  },
  _morphLabelsHard(morph) {
    const donorBox = this._box(morph.donor), donorStyle = getComputedStyle(morph.donor);
    for (const entry of morph.groups) {
      const group = entry.group, box = this._box(group), s = getComputedStyle(group);
      const left = box.left + this._px(s.paddingLeft) + this._px(s.borderLeftWidth);
      const right = box.right - this._px(s.paddingRight) - this._px(s.borderRightWidth);
      let previous = null, lineTop = null;
      for (const label of entry.labels) {
        const ink = this._ink(label), b = this._box(label);
        if (!ink || ink.left < Math.max(left, donorBox.left + this._px(donorStyle.paddingLeft)) - 0.5 ||
            ink.right > Math.min(right, donorBox.right - this._px(donorStyle.paddingRight)) + 0.5 ||
            ink.top < donorBox.top - 0.5 || ink.bottom > donorBox.bottom + 0.5 ||
            (previous && b.left < previous.right + this.C.min_spacing_px - 0.5)) return false;
        if (lineTop !== null && Math.abs(ink.top - lineTop) > this.C.geometry_tolerance_px) return false;
        if (ink.left < b.left - 0.5 || ink.right > b.right + 0.5 || ink.bottom > b.bottom + 0.5) return false;
        lineTop = lineTop === null ? ink.top : lineTop; previous = b;
      }
    }
    return true;
  },
  _morphLanePlan(seed, root, receiver, baseline) {
    const rail = seed.second.parentElement, lane = rail && rail.parentElement, lanes = lane && lane.parentElement;
    if (!rail || !lane || !lanes || !receiver.contains(lanes) || getComputedStyle(lane).display !== 'grid' ||
        !this._plainGeometry(lane) || this._struct(lane).explicitHeight === null ||
        this._struct(lanes).explicitHeight === null || seed.first.parentElement !== rail ||
        seed.first.offsetParent !== rail || seed.second.offsetParent !== rail) return {invalid: 'lane_relationship_unsupported'};
    const first = this._box(seed.first), second = this._box(seed.second), railBox = this._box(rail);
    const delta = Math.max(0, first.bottom + this.C.min_spacing_px - second.top);
    const laneBox = this._box(lane), railStyle = getComputedStyle(rail);
    const growth = Math.max(0, second.bottom + delta + this._px(railStyle.marginBottom) - laneBox.bottom);
    const cap = baseline.rootBox.height * 0.10;
    if (growth > Math.min(baseline.boxes.get(receiver).height * 0.50, cap) + this.C.geometry_tolerance_px ||
        delta > cap + this.C.geometry_tolerance_px) return {invalid: 'parent_growth_cap'};
    const plan = [], add = (node, declarations) => plan.push({node, kind: 'spacing', declarations, reductions: []});
    add(seed.second, 'top:' + this._round(second.top - railBox.top - this._px(railStyle.borderTopWidth) + delta, 3) + 'px;bottom:auto');
    if (growth > 0) {
      for (let node = lane; node && node !== receiver; node = node.parentElement) {
        if (!this._plainGeometry(node) || this._struct(node).explicitHeight === null)
          return {invalid: 'lane_ancestor_unsupported'};
        add(node, 'height:' + this._ceil(this._box(node).height + growth, 3) + 'px;flex-shrink:0');
      }
      add(receiver, 'height:' + this._ceil(this._box(receiver).height + growth, 3) + 'px');
      const lanesBox = this._box(lanes);
      for (const child of Array.from(lanes.children)) {
        const b = this._box(child), cs = getComputedStyle(child);
        if (child.hasAttribute('data-qid') && this._plainGeometry(child) && cs.position === 'absolute' &&
            !this._allLeaves(child).length && this._struct(child).explicitHeight !== null &&
            Math.abs(b.top - lanesBox.top) <= this.C.geometry_tolerance_px &&
            Math.abs(b.height - lanesBox.height) <= this.C.geometry_tolerance_px)
          add(child, 'height:' + this._ceil(b.height + growth, 3) + 'px');
      }
    }
    return {plan, growth, delta};
  },
  _morphBaseScope(region, baseline, basePlan) {
    const nodes = new Set(), root = region.card;
    const properties = new Set(['font-size', 'line-height', 'padding-top', 'padding-bottom',
      'margin-top', 'margin-bottom', 'row-gap', 'min-height', 'height', 'flex-shrink']);
    const add = node => {
      for (const child of [node].concat(Array.from(node.querySelectorAll('[data-qid]'))))
        if (child !== root && baseline.boxes.has(child) && !this._protected(child)) nodes.add(child);
    };
    for (const item of basePlan) {
      if (item.node === root || !root.contains(item.node) || !baseline.boxes.has(item.node) ||
          this._protected(item.node) || !['text', 'spacing'].includes(item.kind) ||
          item.declarations.split(';').filter(Boolean).some(value => !properties.has(value.split(':')[0].trim())))
        return {invalid: 'morph_base_scope_unsupported'};
      add(item.node);
      for (let child = item.node, parent = child.parentElement; parent && parent !== root;
          child = parent, parent = parent.parentElement) {
        const style = getComputedStyle(parent), flow = getComputedStyle(child).position;
        if (!['static', 'relative'].includes(flow)) break;
        const siblings = Array.from(parent.children), index = siblings.indexOf(child);
        if (style.display === 'block' || style.display === 'grid' ||
            style.display === 'flex' && style.flexDirection === 'column')
          for (const sibling of siblings.slice(index + 1))
            if (['static', 'relative'].includes(getComputedStyle(sibling).position)) add(sibling);
        if (this._struct(parent).explicitHeight !== null) break;
        if (!baseline.boxes.has(parent) || this._protected(parent)) break;
        nodes.add(parent);
      }
    }
    return {nodes};
  },
  _morphPanelSizing(panels) {
    const heights = [], leadingPlan = [];
    for (const panel of panels) {
      const box = this._box(panel), style = getComputedStyle(panel), inks = [];
      for (const leaf of this._allLeaves(panel)) {
        let visible = true;
        for (let node = leaf; node && panel.contains(node); node = node.parentElement) {
          const cs = getComputedStyle(node);
          if (cs.display === 'none' || ['hidden', 'collapse'].includes(cs.visibility) || Number(cs.opacity) === 0) {
            visible = false; break;
          }
        }
        const ink = visible && this._ink(leaf); if (ink) inks.push(ink);
      }
      if (!inks.length) return {invalid: 'morph_panel_ink_unavailable'};
      const top = Math.min(...inks.map(ink => ink.top)), bottom = Math.max(...inks.map(ink => ink.bottom));
      const borderTop = this._px(style.borderTopWidth), borderBottom = this._px(style.borderBottomWidth);
      const paddingTop = Math.max(this.C.min_spacing_px, this._px(style.paddingTop));
      const paddingBottom = Math.max(this.C.min_spacing_px, this._px(style.paddingBottom));
      const leading = Math.max(0, box.top + borderTop + paddingTop - top);
      if (leading > this.C.geometry_tolerance_px) {
        const first = panel.firstElementChild;
        if (!first || !first.hasAttribute('data-qid') || !this._plainGeometry(first))
          return {invalid: 'morph_panel_leading_unsupported'};
        leadingPlan.push({node: first, kind: 'spacing', declarations: 'margin-top:' +
          this._ceil(this._px(getComputedStyle(first).marginTop) + leading, 3) + 'px', reductions: []});
      }
      const topInset = Math.max(paddingTop, top - box.top - borderTop);
      heights.push(this._ceil(borderTop + topInset + (bottom - top) + paddingBottom + borderBottom, 3));
    }
    return {heights, leadingPlan};
  },
  _morphPlan(region, baseline, basePlan, fontFactor) {
    const seeds = region.collisions.filter(seed => seed.kind === 'absolute_rows');
    const diagnostic = {outcome: 'unsupported', semantic_guard: false, needed_px: null, reclaimed_px: null,
      donor_qid: null, receiver_qid: null, morph_nodes: []};
    if (seeds.length !== 1) return {invalid: 'morph_collision_scope_unsupported', diagnostic};
    const seed = seeds[0], root = region.card, receiver = this._morphReceiver(root, seed.second);
    if (!receiver) return {invalid: 'morph_receiver_unavailable', diagnostic};
    diagnostic.receiver_qid = receiver.getAttribute('data-qid');
    const first = this._box(seed.first), second = this._box(seed.second);
    const delta = Math.max(0, first.bottom + this.C.min_spacing_px - second.top);
    const available = Math.max(0, this._availableBottom(seed.second, root, new Set([seed.first, seed.second])) - second.bottom);
    const needed = Math.max(0, delta - available) + this.C.min_spacing_px;
    diagnostic.needed_px = this._round(needed, 3); diagnostic.current_available_px = this._round(available, 3);
    const donors = this._morphDonors(root, receiver);
    let selected = null, contrastRejected = false;
    for (const donor of donors) {
      if (!this._measure()) return {invalid: 'budget_exhausted', diagnostic};
      const guard = this._morphSemanticPlan(donor);
      if (!guard.invalid) { selected = {donor, guard}; break; }
      diagnostic.outcome = guard.invalid;
      if (guard.invalid === 'semantic_contrast') {
        contrastRejected = true;
        diagnostic.semantic_guard = {passed: false, reason: 'semantic_contrast', detail: guard.detail};
      }
    }
    if (!selected) return {invalid: contrastRejected ? 'semantic_contrast' : 'morph_semantic_guard', diagnostic};
    const {donor, guard} = selected, before = this._box(donor);
    const beforePanelHeights = guard.panels.map(panel => this._box(panel).height);
    const beforeRowHeight = Math.max(before.height, ...beforePanelHeights);
    const baselineVisualBottom = Math.max(baseline.boxes.get(donor).bottom,
      ...guard.panels.map(panel => baseline.boxes.get(panel).bottom));
    const baseScope = this._morphBaseScope(region, baseline, basePlan);
    if (baseScope.invalid) return {invalid: baseScope.invalid, diagnostic};
    diagnostic.donor_qid = donor.getAttribute('data-qid'); diagnostic.semantic_guard = guard.semantic_guard;
    diagnostic.morph_nodes = guard.labels.map(node => node.getAttribute('data-qid'));
    const font = this._fontPlan(guard.labels, guard.labels, fontFactor, region);
    if (font.invalid) return {invalid: font.invalid, diagnostic};
    let plan = this._mergePlans(guard.plan, font.plan);
    this._applyPlan(this._mergePlans(basePlan, plan));
    if (!this._measure()) return {invalid: 'budget_exhausted', diagnostic};
    const morph = {groups: guard.groups, contrasts: guard.contrasts, labelNodes: new Set(guard.labels), donor, receiver, loss: 1,
      allowedNodes: new Set([donor, receiver].concat(Array.from(donor.querySelectorAll('[data-qid]')),
        Array.from(receiver.querySelectorAll('[data-qid]')), Array.from(baseScope.nodes))),
      internalReflowNodes: new Set(Array.from(donor.querySelectorAll('[data-qid]')).filter(node => !guard.panels.includes(node))),
      panels: guard.panels, beforeRowHeight, baselineVisualBottom, diagnostic};
    const contrast = this._morphContrastHard(morph);
    if (!contrast.ok) return {invalid: contrast.reason, diagnostic};
    if (!this._morphLabelsHard(morph)) return {invalid: 'morph_labels_do_not_fit', diagnostic};
    const separation = this._morphPanelSeparationPlan(guard.panels, baseline);
    diagnostic.sibling_gap_repairs = separation.diagnostics;
    if (separation.plan.length) {
      plan = this._mergePlans(plan, separation.plan); this._applyPlan(this._mergePlans(basePlan, plan));
      if (!this._measure()) return {invalid: 'budget_exhausted', diagnostic};
    }
    let sizing = this._morphPanelSizing(guard.panels);
    if (sizing.invalid) return {invalid: sizing.invalid, diagnostic};
    if (sizing.leadingPlan.length) {
      plan = this._mergePlans(plan, sizing.leadingPlan); this._applyPlan(this._mergePlans(basePlan, plan));
      if (!this._measure()) return {invalid: 'budget_exhausted', diagnostic};
      sizing = this._morphPanelSizing(guard.panels);
      if (sizing.invalid || sizing.leadingPlan.length) return {invalid: sizing.invalid || 'morph_content_escape', diagnostic};
    }
    plan = this._mergePlans(plan, guard.panels.map((panel, index) => ({node: panel, kind: 'spacing',
      declarations: 'height:' + sizing.heights[index] + 'px', reductions: []})));
    this._applyPlan(this._mergePlans(basePlan, plan));
    if (!this._measure()) return {invalid: 'budget_exhausted', diagnostic};
    for (const panel of guard.panels) {
      const content = this._morphPanelContentHard(panel); if (!content.ok) return {invalid: content.reason, diagnostic};
    }
    // Natural content height is a lower bound, not the desired compaction.
    // Retain the rest of the row as panel whitespace and reclaim only the deficit.
    const minimumRowHeight = Math.max(this._box(donor).height, ...guard.panels.map(panel => this._box(panel).height));
    const targetRowHeight = this._ceil(Math.max(minimumRowHeight, beforeRowHeight - needed), 3);
    plan = this._mergePlans(plan, [donor].concat(guard.panels).map(node => ({node, kind: 'spacing',
      declarations: 'height:' + targetRowHeight + 'px' + (node === donor ? '' : ';align-self:stretch'), reductions: []})));
    this._applyPlan(this._mergePlans(basePlan, plan));
    if (!this._measure()) return {invalid: 'budget_exhausted', diagnostic};
    for (const panel of guard.panels) {
      const content = this._morphPanelContentHard(panel); if (!content.ok) return {invalid: content.reason, diagnostic};
    }
    const after = this._box(donor), heights = guard.panels.map(panel => this._box(panel).height);
    const reclaimed = beforeRowHeight - Math.max(after.height, ...heights);
    diagnostic.row_before_height_px = this._round(beforeRowHeight, 3);
    diagnostic.row_after_height_px = this._round(Math.max(after.height, ...heights), 3);
    diagnostic.minimum_required_row_height_px = this._round(minimumRowHeight, 3);
    diagnostic.target_row_height_px = this._round(targetRowHeight, 3);
    diagnostic.reclaimed_px = this._round(reclaimed, 3); diagnostic.panel_heights_px = heights.map(value => this._round(value, 3));
    if (reclaimed + this.C.geometry_tolerance_px < needed) return {invalid: 'morph_space_insufficient', diagnostic};
    if (Math.abs(reclaimed - needed) > this.C.geometry_tolerance_px)
      return {invalid: 'morph_reclaim_mismatch', diagnostic};
    const lane = this._morphLanePlan(seed, root, receiver, baseline);
    if (lane.invalid) return {invalid: lane.invalid, diagnostic};
    morph.layoutNodes = new Set([donor, receiver, seed.first, seed.second].concat(guard.panels, lane.plan.map(item => item.node)));
    plan = this._mergePlans(plan, lane.plan); this._applyPlan(this._mergePlans(basePlan, plan));
    if (!this._measure()) return {invalid: 'budget_exhausted', diagnostic};
    const donorHeight = this._ceil(Math.max(after.height, ...heights), 3), receiverBox = this._box(receiver);
    const targetTop = Math.max(before.top, receiverBox.bottom + this.C.min_spacing_px);
    const rootBox = this._box(root), donorStyle = getComputedStyle(donor), cap = baseline.rootBox.height * 0.10;
    const rootStyle = getComputedStyle(root), rootSafeBottom = rootBox.bottom - this._px(rootStyle.paddingBottom) -
      this._px(rootStyle.borderBottomWidth);
    const allowedBottom = Math.max(rootSafeBottom, baselineVisualBottom);
    diagnostic.boundary = {root_safe_bottom_px: this._round(rootSafeBottom, 3),
      baseline_visual_bottom_px: this._round(baselineVisualBottom, 3),
      allowed_bottom_px: this._round(allowedBottom, 3), final_visual_bottom_px: this._round(targetTop + donorHeight, 3)};
    morph.allowedBottom = allowedBottom;
    if (reclaimed > cap + this.C.geometry_tolerance_px || targetTop - before.top > cap + this.C.geometry_tolerance_px ||
        Math.abs(donorHeight - baseline.boxes.get(donor).height) > cap + this.C.geometry_tolerance_px ||
        targetTop + donorHeight > allowedBottom + this.C.geometry_tolerance_px)
      return {invalid: 'morph_fixed_boundary', diagnostic};
    const parts = ['height:' + donorHeight + 'px'];
    for (const panel of guard.panels) plan = this._mergePlans(plan,
      [{node: panel, kind: 'spacing', declarations: 'height:' + donorHeight + 'px;align-self:stretch', reductions: []}]);
    if (donorStyle.position === 'absolute' && donor.offsetParent === root)
      parts.push('top:' + this._round(targetTop - rootBox.top - this._px(getComputedStyle(root).borderTopWidth), 3) + 'px', 'bottom:auto');
    else if (Math.abs(this._box(donor).top - targetTop) > this.C.geometry_tolerance_px)
      return {invalid: 'morph_donor_position_unsupported', diagnostic};
    plan = this._mergePlans(plan, [{node: donor, kind: 'spacing', declarations: parts.join(';'), reductions: []}]);
    diagnostic.outcome = 'measured'; diagnostic.receiver_growth_px = this._round(lane.growth, 3);
    return {plan, morph, diagnostic};
  },
  _morphHard(morph, baseline) {
    const contrast = this._morphContrastHard(morph); if (!contrast.ok) return contrast;
    if (!this._morphLabelsHard(morph)) return {ok: false, reason: 'morph_labels_do_not_fit'};
    morph.diagnostic.morph_panel_compaction = [];
    const visualBottom = Math.max(this._box(morph.donor).bottom, ...morph.panels.map(panel => this._box(panel).bottom));
    morph.diagnostic.boundary.final_visual_bottom_px = this._round(visualBottom, 3);
    if (visualBottom > morph.allowedBottom + this.C.geometry_tolerance_px)
      return {ok: false, reason: 'morph_fixed_boundary'};
    for (const panel of morph.panels) {
      const content = this._morphPanelContentHard(panel); if (!content.ok) return content;
    }
    for (const container of [morph.donor].concat(morph.panels))
      if (this._escape(container, this._allLeaves(container), this.C.geometry_tolerance_px) >
          Math.max(this.C.escape_tolerance_px, baseline.escapes.get(container) || 0) + this.C.geometry_tolerance_px + 1e-6)
        return {ok: false, reason: 'morph_content_escape', qid: container.getAttribute('data-qid')};
    for (const [node, before] of baseline.boxes) {
      const after = this._box(node);
      if (!morph.allowedNodes.has(node) && !this._sameBox(before, after)) {
        const property = ['left', 'top', 'width', 'height'].find(key => Math.abs(after[key] - before[key]) > this.C.geometry_tolerance_px);
        return {ok: false, reason: 'morph_outside_geometry', qid: node.getAttribute('data-qid'), property,
          delta: this._round(after[property] - before[property], 3), cap: this.C.geometry_tolerance_px};
      }
      const cap = baseline.rootBox.height * 0.10;
      if (morph.layoutNodes.has(node) && Math.abs(after.height - before.height) > cap + this.C.geometry_tolerance_px) {
        const rowReclaimed = morph.beforeRowHeight - Math.max(this._box(morph.donor).height,
          ...morph.panels.map(panel => this._box(panel).height));
        const panelShrink = morph.panels.includes(node) && after.height < before.height &&
          morph.diagnostic.semantic_guard.passed === true &&
          Math.abs(after.left - before.left) <= this.C.geometry_tolerance_px &&
          Math.abs(after.width - before.width) <= this.C.geometry_tolerance_px &&
          Math.abs(after.top - before.top) <= cap + this.C.geometry_tolerance_px &&
          rowReclaimed >= 0 && rowReclaimed <= cap + this.C.geometry_tolerance_px;
        if (panelShrink) {
          const content = this._morphPanelContentHard(node); if (!content.ok) return content;
          morph.diagnostic.morph_panel_compaction.push({qid: node.getAttribute('data-qid'),
            height_delta: this._round(after.height - before.height, 3),
            row_reclaimed: this._round(rowReclaimed, 3), row_limit: this._round(cap, 3)});
        } else return {ok: false, reason: 'parent_preservation', qid: node.getAttribute('data-qid'), property: 'height',
          delta: this._round(after.height - before.height, 3), cap: this._round(cap, 3),
          morph_allowed: true, rule: 'layout_height_cap'};
      }
    }
    return {ok: true};
  },
  _morphPanelContentHard(panel) {
    const box = this._box(panel), style = getComputedStyle(panel), tolerance = this.C.geometry_tolerance_px;
    const bounds = {left: box.left + this._px(style.borderLeftWidth) + this._px(style.paddingLeft),
      right: box.right - this._px(style.borderRightWidth) - this._px(style.paddingRight),
      top: box.top + this._px(style.borderTopWidth) + this._px(style.paddingTop),
      bottom: box.bottom - this._px(style.borderBottomWidth) - this._px(style.paddingBottom)};
    const inks = [];
    for (const leaf of this._allLeaves(panel)) {
      const ink = this._ink(leaf); if (!ink) continue;
      if (ink.left < bounds.left - tolerance || ink.right > bounds.right + tolerance ||
          ink.top < bounds.top - tolerance || ink.bottom > bounds.bottom + tolerance || this._textOccluded(leaf))
        return {ok: false, reason: 'morph_content_escape', qid: leaf.getAttribute('data-qid')};
      if (inks.some(other => Math.min(other.right, ink.right) - Math.max(other.left, ink.left) > tolerance &&
          Math.min(other.bottom, ink.bottom) - Math.max(other.top, ink.top) > tolerance))
        return {ok: false, reason: 'morph_content_overlap', qid: leaf.getAttribute('data-qid')};
      inks.push(ink);
    }
    return {ok: true};
  },
"""

__all__ = ["MORPH_MEMBERS"]
