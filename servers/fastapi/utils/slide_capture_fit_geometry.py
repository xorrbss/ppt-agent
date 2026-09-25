"""Bounded collision seeds and fixed-root geometry for authored local fitting."""

GEOMETRY_MEMBERS = r"""
  _protected(node) {
    for (let current = node; current && current !== document.body; current = current.parentElement) {
      if (current.matches('[data-allow-overlap="true"]') || current.tagName === 'FOOTER') return true;
      if (current.hasAttribute('data-header-part') && current.getAttribute('data-header-part') !== 'body') return true;
    }
    return Boolean(this._slotFor(node));
  },
  _plainGeometry(node) {
    const s = getComputedStyle(node);
    return this._eligible(node) && !this._protected(node) && s.transform === 'none' &&
      String(s.scale || 'none') === 'none' && String(s.zoom || '1') === '1' &&
      String(s.rotate || 'none') === 'none' && !['fixed', 'sticky'].includes(s.position) &&
      s.display !== 'none' && String(s.writingMode || 'horizontal-tb') === 'horizontal-tb' &&
      String(s.animationName || 'none').split(',').every(value => value.trim() === 'none') &&
      String(s.transitionDuration || '0s').split(',').every(value => this._px(value) === 0);
  },
  _constrainsWidth(node) {
    const names = ['width', 'min-width', 'max-width', 'inline-size', 'min-inline-size',
      'max-inline-size', 'flex', 'flex-basis', 'flex-grow', 'flex-shrink'];
    if (node.style && names.some(name => String(node.style.getPropertyValue(name) || '').trim())) return true;
    let declared = false, unknown = false;
    const visit = rules => {
      for (const rule of Array.from(rules || [])) {
        let nested = null; try { nested = rule.cssRules; } catch (_) { unknown = true; continue; }
        if (nested) { visit(nested); if (declared) return; continue; }
        const constrained = rule && rule.style && names.some(name =>
          String(rule.style.getPropertyValue(name) || '').trim());
        let matched = false; try { matched = constrained ? node.matches(rule.selectorText) : false; }
        catch (_) { unknown = true; continue; }
        if (matched) { declared = true; return; }
      }
    };
    for (const sheet of Array.from(document.styleSheets || [])) {
      let rules = null; try { rules = sheet.cssRules; } catch (_) { unknown = true; continue; }
      visit(rules); if (declared) break;
    }
    return declared || unknown;
  },
  _intrinsicTextReflow(node, before, after, candidate, baseline) {
    const targets = candidate && candidate.typographyNodes;
    const typography = targets instanceof Map ? targets.get(node) : null;
    if (!typography || !this._replayable(node) ||
        this._protected(node) || this._slotFor(node) || !this._plainGeometry(node) ||
        node.children.length || !this._ownedTextNodes(node).length || this._constrainsWidth(node)) return false;
    const style = getComputedStyle(node), parent = node.parentElement;
    const actualFontPx = this._px(style.fontSize);
    if (!(actualFontPx < typography.beforeFontPx - 1e-6) ||
        Math.abs(actualFontPx - typography.targetFontPx) > 0.05) return false;
    if (!parent || !['static', 'relative'].includes(style.position)) return false;
    const parentStyle = getComputedStyle(parent);
    const inlineFlow = ['inline', 'inline-block'].includes(style.display) &&
      !['flex', 'grid'].includes(parentStyle.display);
    const blockifiedFlexItem = style.display === 'block' && parentStyle.display === 'flex' &&
      parentStyle.flexDirection === 'row' && parentStyle.flexWrap === 'nowrap' &&
      this._number(style.flexGrow, -1) === 0 && this._number(style.flexShrink, -1) === 1 &&
      style.flexBasis === 'auto';
    if ((!inlineFlow && !blockifiedFlexItem) ||
        style.backgroundImage !== 'none' || style.boxShadow !== 'none' || Number(style.opacity) !== 1) return false;
    const background = String(style.backgroundColor || '').replace(/ /g, '').toLowerCase();
    if (background && !this.C.transparent_backgrounds.includes(background)) return false;
    if (['Top', 'Right', 'Bottom', 'Left'].some(side => this._px(style['border' + side + 'Width']) > 0)) return false;
    for (const pseudo of ['::before', '::after']) {
      const content = String(getComputedStyle(node, pseudo).content || '').trim().toLowerCase();
      if (content && !['none', 'normal', '""', "''"].includes(content)) return false;
    }
    const parentBefore = baseline.boxes.get(parent), parentAfter = parentBefore && this._box(parent);
    if (!parentBefore || Math.abs(parentAfter.left - parentBefore.left) > this.C.geometry_tolerance_px ||
        Math.abs(parentAfter.width - parentBefore.width) > this.C.geometry_tolerance_px) return false;
    const widthReduction = before.width - after.width;
    return widthReduction >= -this.C.geometry_tolerance_px &&
      after.height <= before.height + this.C.geometry_tolerance_px &&
      Math.abs(after.left - before.left) <= Math.max(0, widthReduction) + this.C.geometry_tolerance_px &&
      before.left >= parentBefore.left - this.C.geometry_tolerance_px &&
      before.right <= parentBefore.right + this.C.geometry_tolerance_px &&
      after.left >= parentAfter.left - this.C.geometry_tolerance_px &&
      after.right <= parentAfter.right + this.C.geometry_tolerance_px;
  },
  _allLeaves(root) {
    return [root].concat(Array.from(root.querySelectorAll('[data-qid]')))
      .filter(node => this._eligible(node) && this._ownedTextNodes(node).length > 0);
  },
  _intentionalContent(node) {
    if (!node) return false;
    const role = String(node.getAttribute('data-semantic-role') ||
      node.getAttribute('data-role') || node.getAttribute('role') || '').toLowerCase();
    return node.matches('[data-allow-overlap="true"], [data-intentional-crop="true"]') ||
      role === 'decoration';
  },
  _nestedContentContainment(region) {
    const root = region.card, defects = new Set(region.defectLeaves || []), targets = [];
    for (const member of region.members || []) {
      for (const node of [member.card].concat(Array.from(member.card.querySelectorAll('[data-qid]')))) {
        if (!this._eligible(node) || this._intentionalContent(node) || !this._isCardLike(node)) continue;
        if (Array.from(defects).some(leaf => node.contains(leaf))) targets.push({node, kind: 'flow_box_escape'});
      }
    }
    for (const leaf of defects) if (!this._intentionalContent(leaf)) targets.push({node: leaf, kind: 'text_escape'});
    const seen = new Set();
    for (const target of targets) {
      if (seen.has(target.node)) continue; seen.add(target.node);
      const measured = target.kind === 'text_escape' ? this._ink(target.node) : this._box(target.node);
      if (!measured) continue;
      for (let ancestor = target.node.parentElement; ancestor && root.contains(ancestor);
          ancestor = ancestor.parentElement) {
        if (this._intentionalContent(ancestor)) break;
        if (!this._eligible(ancestor) || !this._isCardLike(ancestor)) continue;
        const boundary = this._box(ancestor);
        const excessRight = measured.right - boundary.right;
        const excessBottom = measured.bottom - boundary.bottom;
        if (Math.max(excessRight, excessBottom) <= this.C.escape_tolerance_px) continue;
        return {reason: target.kind, diagnostic: {
          qid: target.node.getAttribute('data-qid'), boundary_qid: ancestor.getAttribute('data-qid'),
          property: 'nested_content_containment', excess_px_x: this._round(Math.max(0, excessRight), 3),
          excess_px_y: this._round(Math.max(0, excessBottom), 3)}};
      }
    }
    return null;
  },
  _paintRects(root, descendants) {
    const result = [], nodes = descendants ? [root].concat(Array.from(root.querySelectorAll('[data-qid]'))) : [root];
    for (const node of nodes) {
      const style = getComputedStyle(node), box = this._box(node);
      if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) continue;
      const bg = String(style.backgroundColor || '').replace(/ /g, '').toLowerCase();
      if ((bg && !this.C.transparent_backgrounds.includes(bg)) || style.backgroundImage !== 'none' ||
          this.C.painted_tags.includes(node.tagName.toLowerCase())) result.push(box);
      for (const side of ['Top', 'Bottom', 'Left', 'Right']) {
        const size = this._px(style['border' + side + 'Width']);
        const color = String(style['border' + side + 'Color'] || '').replace(/ /g, '').toLowerCase();
        if (!(size > 0) || this.C.transparent_backgrounds.includes(color)) continue;
        result.push({left: side === 'Right' ? box.right - size : box.left,
          right: side === 'Left' ? box.left + size : box.right,
          top: side === 'Bottom' ? box.bottom - size : box.top,
          bottom: side === 'Top' ? box.top + size : box.bottom});
      }
      for (const text of this._ownedTextNodes(node)) {
        const range = document.createRange(); range.selectNodeContents(text);
        for (const rect of range.getClientRects()) if (rect.width > 0 && rect.height > 0) result.push(rect);
      }
    }
    return result;
  },
  _collision(first, second) {
    // Range and painted-edge evidence only: a transparent container's bbox is not ink.
    const a = this._paintRects(first, true), b = this._paintRects(second, true);
    let depth = 0;
    for (const x of a) for (const y of b) {
      const width = Math.min(x.right, y.right) - Math.max(x.left, y.left);
      const height = Math.min(x.bottom, y.bottom) - Math.max(x.top, y.top);
      if (width > this.C.geometry_tolerance_px && height > 0.5) depth = Math.max(depth, height);
    }
    return depth;
  },
  _shallowPaintContact(first, second, depth) {
    const tolerance = this.C.geometry_tolerance_px;
    if (depth > this.C.min_spacing_px + tolerance) return false;
    for (const [source, opposite] of [[first, second], [second, first]]) {
      const leaves = this._allLeaves(source), paint = this._paintRects(opposite, true);
      for (const leaf of leaves) {
        for (const text of this._ownedTextNodes(leaf)) {
          const range = document.createRange(); range.selectNodeContents(text);
          for (const rect of range.getClientRects()) {
            if (rect.width <= 0 || rect.height <= 0) continue;
            const centerY = (rect.top + rect.bottom) / 2;
            // Only this pair's paint crossing a text fragment's horizontal
            // center line retains a shallow seed; edge contact is insufficient.
            if (paint.some(box => centerY >= box.top && centerY <= box.bottom &&
                Math.min(rect.right, box.right) - Math.max(rect.left, box.left) > tolerance)) return false;
          }
        }
      }
    }
    return true;
  },
  _morphPanelSeparationPlan(panels, baseline) {
    const changes = new Map(), diagnostics = [];
    for (const pair of baseline.siblings) {
      if (pair.depth > 0 || !panels.some(panel => panel.contains(pair.first) && panel.contains(pair.second))) continue;
      const parent = pair.first.parentElement;
      if (!parent || parent !== pair.second.parentElement) continue;
      const style = getComputedStyle(parent);
      if (style.display !== 'block' && !(style.display === 'flex' && style.flexDirection === 'column')) continue;
      if (![pair.first, pair.second].every(node => this._plainGeometry(node) &&
          ['static', 'relative'].includes(getComputedStyle(node).position))) continue;
      if (this._box(pair.second).top < this._box(pair.first).top) continue;
      const depth = this._collision(pair.first, pair.second); if (!(depth > 0)) continue;
      const added = depth + this.C.min_spacing_px;
      changes.set(pair.second, Math.max(changes.get(pair.second) || 0, added));
      diagnostics.push({qid: pair.first.getAttribute('data-qid'), other_qid: pair.second.getAttribute('data-qid'),
        overlap_px: this._round(depth, 3), added_spacing_px: this._round(added, 3)});
    }
    return {plan: Array.from(changes, ([node, added]) => ({node, kind: 'spacing',
      declarations: 'margin-top:' + this._ceil(this._px(getComputedStyle(node).marginTop) + added, 3) + 'px',
      reductions: []})), diagnostics};
  },
  _bottomBand(node) {
    const style = getComputedStyle(node);
    // Chromium resolves auto inset values for absolute boxes. Require an authored
    // bottom declaration so top-positioned containers are not mistaken for bands.
    let bottom = node.style.getPropertyValue('bottom');
    if (!bottom) {
      const visit = rules => {
        for (const rule of Array.from(rules || [])) {
          if (rule.style && rule.style.getPropertyValue('bottom')) {
            try { if (node.matches(rule.selectorText)) bottom = rule.style.getPropertyValue('bottom'); } catch (_) {}
          }
          if (rule.conditionText && rule.constructor && rule.constructor.name === 'CSSMediaRule' &&
              !matchMedia(rule.conditionText).matches) continue;
          let nested = null; try { nested = rule.cssRules; } catch (_) {}
          if (nested) visit(nested);
        }
      };
      for (const sheet of Array.from(document.styleSheets || [])) {
        let rules = null; try { rules = sheet.cssRules; } catch (_) {}
        if (rules) visit(rules);
      }
    }
    return style.position === 'absolute' && bottom && bottom.trim() !== 'auto' && style.bottom !== 'auto' &&
      this._allLeaves(node).length > 0 && this._plainGeometry(node);
  },
  _collisionSeeds(qids) {
    const seeds = [], seen = new Set();
    const add = (first, second, kind, source) => {
      if (!first || !second || first === second || first.contains(second) || second.contains(first) ||
          this._protected(first) || this._protected(second)) return;
      if (kind === 'bottom_band' && !this._allLeaves(first).length) return;
      const depth = this._collision(first, second); if (!(depth > 0)) return;
      if (kind === 'bottom_band' && this._shallowPaintContact(first, second, depth)) {
        this._last.parent_exclusions.push({qid: first.getAttribute('data-qid'), other_qid: second.getAttribute('data-qid'),
          reason: 'shallow_painted_contact', intersection_px: this._round(depth, 3)});
        return;
      }
      const key = [first.getAttribute('data-qid'), second.getAttribute('data-qid')].sort().join('|');
      if (seen.has(key)) return; seen.add(key);
      seeds.push({first, second, kind, depth, source, intentional: false});
    };
    for (const parent of qids) {
      if (!this._budgetLeft() || seeds.length > this.C.max_regions) break;
      const children = Array.from(parent.children).filter(node => node.hasAttribute('data-qid') && this._plainGeometry(node));
      for (let i = 0; i < children.length; i += 1) for (let j = i + 1; j < children.length; j += 1) {
        const first = children[i], second = children[j];
        const a = getComputedStyle(first), b = getComputedStyle(second);
        if (a.position === 'absolute' && b.position === 'absolute' &&
            first.offsetParent === parent && second.offsetParent === parent &&
            a.whiteSpace === 'nowrap' && b.whiteSpace === 'nowrap' &&
            this._allLeaves(first).length && this._allLeaves(second).length &&
            Math.abs(this._box(first).top - this._box(second).top) <= this.C.geometry_tolerance_px)
          add(first, second, 'absolute_rows', 'painted_sibling_intersection');
        else if (this._bottomBand(second)) add(first, second, 'bottom_band', 'painted_band_intersection');
        else if (this._bottomBand(first)) add(second, first, 'bottom_band', 'painted_band_intersection');
      }
    }
    for (const issue of Array.isArray(this.P.geometry_issues) ? this.P.geometry_issues : []) {
      const ids = issue.qids || [];
      const first = qids.find(node => node.getAttribute('data-qid') === String(issue.target_qid || issue.qid || ids[0] || ''));
      const second = qids.find(node => node.getAttribute('data-qid') === String(issue.occluder_qid || issue.other_qid || ids[1] || ''));
      add(first, second, 'quality_issue', String(issue.code || issue.kind || 'quality_geometry'));
    }
    return seeds;
  },
  _scopeRegion(root, members, collisions) {
    const leaves = this._allLeaves(root), defects = new Set();
    for (const member of members) for (const leaf of member.leaves) {
      const ink = this._ink(leaf), box = this._box(member.card);
      if (ink && (ink.right > box.right + this.C.escape_tolerance_px || ink.bottom > box.bottom + this.C.escape_tolerance_px) ||
          (member.conflicts || []).some(conflict => conflict.owned.includes(leaf))) defects.add(leaf);
    }
    for (const member of members) if (member.flow_box_escape_px > this.C.escape_tolerance_px)
      for (const leaf of member.leaves) defects.add(leaf);
    for (const seed of collisions) {
      const paint = this._paintRects(seed.second, true);
      for (const leaf of this._allLeaves(seed.first)) {
        const ink = this._ink(leaf);
        if (ink && paint.some(rect => this._intersects(ink, rect))) defects.add(leaf);
      }
      if (seed.kind === 'absolute_rows') for (const node of [seed.first, seed.second])
        if (this._absoluteTextEligible(node, {collisions: [seed]})) defects.add(node);
    }
    const protectedQids = [root].concat(Array.from(root.querySelectorAll('[data-qid]')))
      .filter(node => this._protected(node)).map(node => node.getAttribute('data-qid')).filter(Boolean);
    return {card: root, leaves, members, collisions, defectLeaves: Array.from(defects),
      conflicts: members.flatMap(member => member.conflicts || []),
      escape_px: Math.max(0, ...members.map(member => member.escape_px || 0), ...collisions.map(seed => seed.depth)),
      protectedQids: Array.from(new Set(protectedQids)), scope: true};
  },
  _fitScopes(regions, qids) {
    const collisionSeeds = this._collisionSeeds(qids), scopes = [];
    if (!this._budgetLeft()) this._last.unresolved_reasons.push('budget_exhausted');
    this._last.geometry_seeds = collisionSeeds.map(seed => ({qid: seed.first.getAttribute('data-qid'),
      other_qid: seed.second.getAttribute('data-qid'), kind: seed.kind, measurement: seed.source,
      intersection_px: this._round(seed.depth, 2), intentional_overlap: seed.intentional}));
    for (const region of regions) {
      const roots = this._parentRoots(region.card);
      scopes.push(this._scopeRegion(roots.length ? this._slackRoot(roots) : region.card, [region], []));
    }
    for (const seed of collisionSeeds) {
      let common = seed.first.parentElement;
      while (common && !common.contains(seed.second)) common = common.parentElement;
      const roots = []; let current = common;
      for (let depth = 0; current && current !== document.body && depth < 4; depth += 1, current = current.parentElement) {
        if (this._protected(current)) {
          this._last.parent_exclusions.push({qid: current.getAttribute('data-qid'),
            seed_qid: seed.first.getAttribute('data-qid'), other_qid: seed.second.getAttribute('data-qid'),
            ancestor_distance: depth, reason: 'protected_boundary'});
          break;
        }
        if (this._parentRootEligible(current, seed.first)) roots.push(current);
      }
      let root = roots.length ? this._slackRoot(roots) : null;
      root = this._morphScopeRoot(common, root, seed);
      if (!root) {
        this._last.parent_exclusions.push({qid: seed.first.getAttribute('data-qid'), reason: 'collision_root_unavailable'});
        this._last.unresolved_reasons.push('parent_region_unavailable'); continue;
      }
      scopes.push(this._scopeRegion(root, [], [seed]));
    }
    const merged = [];
    for (const scope of scopes) {
      const overlaps = merged.filter(other => scope.card.contains(other.card) || other.card.contains(scope.card));
      let root = scope.card, members = scope.members.slice(), collisions = scope.collisions.slice();
      for (const other of overlaps) {
        if (other.card.contains(root)) root = other.card;
        members.push(...other.members); collisions.push(...other.collisions);
        merged.splice(merged.indexOf(other), 1);
      }
      merged.push(this._scopeRegion(root, members, collisions));
    }
    const ordered = merged.sort((a, b) => qids.indexOf(a.card) - qids.indexOf(b.card));
    this._last.scopes = ordered.map(scope => ({root_qid: scope.card.getAttribute('data-qid'),
      target_qids: Array.from(new Set(scope.defectLeaves.map(node => node.getAttribute('data-qid')).filter(Boolean))),
      seed_qids: Array.from(new Set(scope.collisions.flatMap(seed => [seed.first, seed.second])
        .map(node => node.getAttribute('data-qid')).filter(Boolean))),
      protected_qids: scope.protectedQids, allowed_axes: ['y'],
      allowed_properties: ['font-size', 'line-height', 'padding-top', 'padding-bottom',
        'margin-top', 'margin-bottom', 'row-gap', 'height', 'min-height', 'top', 'bottom',
        'flex-shrink', 'align-self', 'align-items', 'grid-template-rows', 'min-width'],
      preserved_properties: ['left', 'width']}));
    return ordered;
  },
  _scopeHard(region, baseline, candidate) {
    this._measurements += 1;
    const root = region.card, maxMove = baseline.rootBox.height * 0.10;
    if (!this._sameBox(baseline.rootBox, this._box(root)) &&
        !this._rootGrowthHard(root, baseline.rootBox, candidate && candidate.rootGrowth)) return {ok: false, reason: 'outside_geometry'};
    for (const [node, box] of baseline.outside) if (!this._sameBox(box, this._box(node)))
      return {ok: false, reason: 'outside_geometry'};
    let displacement = 0;
    const morph = candidate && candidate.morph;
    if (morph) {
      const hard = this._morphHard(morph, baseline); if (!hard.ok) return hard;
    }
    for (const [node, before] of baseline.boxes) {
      const after = this._box(node), movement = Math.abs(after.top - before.top), growth = Math.abs(after.height - before.height);
      const internalReflow = morph && morph.internalReflowNodes.has(node);
      const labelMorph = morph && morph.labelNodes.has(node);
      const intrinsicTextReflow = this._intrinsicTextReflow(node, before, after, candidate, baseline);
      const horizontal = !labelMorph && !intrinsicTextReflow && (Math.abs(after.left - before.left) > this.C.geometry_tolerance_px ||
        Math.abs(after.width - before.width) > this.C.geometry_tolerance_px);
      const moved = !internalReflow && movement > maxMove + this.C.geometry_tolerance_px;
      const protectedChanged = this._protected(node) && !this._sameBox(before, after);
      if (horizontal || moved || protectedChanged) {
        const property = horizontal ? (Math.abs(after.left - before.left) > this.C.geometry_tolerance_px ? 'left' : 'width') :
          moved ? 'top' : ['left', 'top', 'width', 'height'].find(key => Math.abs(after[key] - before[key]) > this.C.geometry_tolerance_px);
        const limit = moved && !horizontal ? maxMove : this.C.geometry_tolerance_px;
        return {ok: false, reason: 'parent_preservation', qid: node.getAttribute('data-qid'), property,
          delta: this._round(after[property] - before[property], 3), cap: this._round(limit, 3),
          morph_allowed: Boolean(morph && morph.allowedNodes.has(node)),
          rule: horizontal ? 'fixed_horizontal' : moved ? 'vertical_movement_cap' : 'protected_box'};
      }
      displacement = Math.max(displacement, movement, growth);
      if (node !== root && this._isCardLike(node) && this._allLeaves(node).length &&
          this._escape(node, this._allLeaves(node), this.C.geometry_tolerance_px) >
            Math.max(this.C.escape_tolerance_px, baseline.escapes.get(node) || 0) + this.C.geometry_tolerance_px + 1e-6)
        return {ok: false, reason: 'nested_escape', diagnostic: {
          qid: node.getAttribute('data-qid'), property: 'nested_escape',
          before_escape_px: this._round(baseline.escapes.get(node) || 0, 3),
          after_escape_px: this._round(this._escape(node, this._allLeaves(node), this.C.geometry_tolerance_px), 3),
          allowed_px: this._round(Math.max(this.C.escape_tolerance_px, baseline.escapes.get(node) || 0) +
            this.C.geometry_tolerance_px + 1e-6, 6),
          is_morph_donor_or_panel: Boolean(morph && (node === morph.donor || morph.panels.includes(node)))}};
    }
    for (const member of region.members) {
      const leaves = this._regionLeaves(member.card);
      if (member.boundary && this._boundaryNeed(member.card)) return {ok: false, reason: 'flow_box_escape'};
      if (this._escape(member.card, leaves, this.C.geometry_tolerance_px) > this.C.escape_tolerance_px ||
          this._flowBoxEscape(member.card) > this.C.escape_tolerance_px || this._flowHeightConflicts(member.card, leaves).length)
        return {ok: false, reason: 'text_escape'};
    }
    const containment = this._nestedContentContainment(region);
    if (containment) return {ok: false, reason: containment.reason, diagnostic: containment.diagnostic};
    for (const seed of region.collisions) {
      const afterDepth = this._collision(seed.first, seed.second);
      if (afterDepth > 0) return {ok: false, reason: 'overlap', diagnostic: {
        qid: seed.first.getAttribute('data-qid'), other_qid: seed.second.getAttribute('data-qid'),
        kind: seed.kind, before_depth_px: this._round(seed.depth, 3), after_depth_px: this._round(afterDepth, 3)}};
    }
    const fixedRootLeaves = morph ? region.leaves.filter(leaf => !morph.donor.contains(leaf)) : region.leaves;
    if (this._escape(root, fixedRootLeaves, this.C.geometry_tolerance_px) > this.C.escape_tolerance_px)
      return {ok: false, reason: 'text_escape'};
    if (morph) {
      const style = getComputedStyle(root), right = baseline.rootBox.right - this._px(style.borderRightWidth);
      const bottom = baseline.rootBox.bottom - this._px(style.borderBottomWidth);
      const originalInks = Array.from(baseline.inks.entries()).filter(([node, ink]) => ink && morph.donor.contains(node)).map(([, ink]) => ink);
      const originalBottom = Math.max(bottom, ...originalInks.map(ink => ink.bottom));
      for (const leaf of region.leaves.filter(node => morph.donor.contains(node))) {
        const ink = this._ink(leaf); if (!ink) continue;
        if (ink.right > right + this.C.escape_tolerance_px || ink.bottom > originalBottom + this.C.geometry_tolerance_px)
          return {ok: false, reason: 'text_escape', qid: leaf.getAttribute('data-qid')};
      }
    }
    for (const pair of baseline.siblings) {
      const afterDepth = this._collision(pair.first, pair.second);
      if (afterDepth > pair.depth + 0.5) return {ok: false, reason: 'new_overlap', diagnostic: {
        qid: pair.first.getAttribute('data-qid'), other_qid: pair.second.getAttribute('data-qid'),
        property: 'new_overlap', before_depth_px: this._round(pair.depth, 3), after_depth_px: this._round(afterDepth, 3)}};
    }
    for (const leaf of region.leaves) {
      if (!baseline.occluded.get(leaf) && this._textOccluded(leaf)) return {ok: false, reason: 'occlusion'};
      const ink = this._boundaryTextInk(leaf); if (!ink) continue;
      for (let node = leaf; node && root.contains(node); node = node.parentElement) {
        const style = getComputedStyle(node), box = this._box(node);
        if (['hidden', 'clip'].includes(style.overflowX) && (ink.left < box.left - 0.5 || ink.right > box.right + 0.5) ||
            ['hidden', 'clip'].includes(style.overflowY) && (ink.top < box.top - 0.5 || ink.bottom > box.bottom + 0.5))
          return {ok: false, reason: 'clipping'};
      }
      if (ink.left < -0.5 || ink.top < -0.5 || ink.right > innerWidth + 0.5 || ink.bottom > innerHeight + 0.5)
        return {ok: false, reason: 'off_canvas'};
    }
    if (!this._fontHard(region.leaves)) return {ok: false, reason: 'font_or_hierarchy'};
    return {ok: true, displacement: displacement / Math.max(1, baseline.rootBox.height)};
  },
"""

__all__ = ["GEOMETRY_MEMBERS"]
