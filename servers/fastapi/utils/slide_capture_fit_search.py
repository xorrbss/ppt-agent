"""Browser fragment for bounded authored local-fit candidate search."""

SEARCH_MEMBERS = r"""
  _fontPlan(regionLeaves, defectLeaves, requestedFactor, region) {
    if (requestedFactor >= 1) return {plan: [], shrink: 0, factors: []};
    const ptPerPx = this._number(this.P.pt_per_css_px,
      this._number(this.P.export_scale_manifest && this.P.export_scale_manifest.pt_per_css_px,
        this.C.default_pt_per_css_px));
    const regionSet = new Set(regionLeaves), defectSet = new Set(defectLeaves);
    const selectedGroups = new Set(this._runs.filter((item) => defectSet.has(item.node))
      .map((item) => item.group_id));
    const grouped = new Map(), plan = [], factors = [];
    for (const run of this._runs.filter((item) => regionSet.has(item.node) && selectedGroups.has(item.group_id))) {
      if (!grouped.has(run.group_id)) grouped.set(run.group_id, []);
      grouped.get(run.group_id).push(run);
    }
    for (const runs of grouped.values()) {
      if (runs.some((run) => !run.editable)) return {invalid: 'unsupported_geometry'};
      if (runs.some((run) => ['title', 'heading'].includes(run.role) && !defectSet.has(run.node)))
        return {invalid: 'hierarchy_unfit'};
      const lower = Math.max(...runs.map((run) => this._minimumPt(run.original_pt) / run.original_pt));
      const factor = Math.max(requestedFactor, lower); factors.push(factor);
      for (const node of Array.from(new Set(runs.map((run) => run.node)))) {
        if (this._runs.some((run) => run.node === node && !run.editable))
          return {invalid: 'unsupported_geometry'};
        const item = this._struct(node);
        if (item.transform !== 'none' || item.scale !== 'none' || item.zoom !== '1' ||
            (['absolute', 'fixed'].includes(item.position) && !this._absoluteTextEligible(node, region)))
          return {invalid: 'unsupported_geometry'};
        const nodeRuns = runs.filter((run) => run.node === node);
        const floor = this._number(this.P.floor_pt, this.C.default_floor_pt);
        const targets = nodeRuns.map((run) => run.original_pt < floor ? floor : run.original_pt * factor);
        if (Math.max(...targets) !== Math.min(...targets)) return {invalid: 'font_unverifiable'};
        const scale = this._effectiveScale(node);
        if (!(scale > 0)) return {invalid:'font_unverifiable'};
        const targetPt = Math.max(...targets), targetPx = this._ceil(targetPt / (ptPerPx * scale), 4);
        const currentPx = this._px(getComputedStyle(node).fontSize);
        if ((requestedFactor >= 1 && currentPx * scale * ptPerPx >= Math.max(...targets)) ||
            currentPx === targetPx) continue;
        const snap = this._snapshot(node), parts = ['font-size:' + targetPx + 'px'];
        const lineHeight = String(snap.lineHeight || '').trim().toLowerCase();
        if (lineHeight.endsWith('px')) {
          const ratio = snap.fontSize > 0 ? targetPx / snap.fontSize : 1;
          parts.push('line-height:' + this._ceil(this._cssPx(lineHeight) * ratio, 4) + 'px');
        }
        plan.push({node: node, kind: 'text', declarations: parts.join(';')});
      }
    }
    const shrink = factors.length ? Math.max(...factors.map((factor) => Math.max(0, 1 - factor))) : 0;
    return {plan: plan, shrink: shrink, factors: factors};
  },
  _candidatePlan(region, fontFactor, spacingFactor) {
    const nodes = [region.card].concat(Array.from(region.card.querySelectorAll('[data-qid]')));
    if (nodes.some((node) => {
      return !this._eligible(node) ||
        node.getAttribute('data-intentional-overlap') === 'true';
    })) return {invalid: 'unsupported_geometry'};
    const spacingNodes = nodes.filter((node) => {
      const item = this._struct(node);
      return !this._protected(node) && item.transform === 'none' && item.scale === 'none' && item.zoom === '1' &&
        (!region.scope || region.members.some(member => member.card === node || member.card.contains(node)) ||
          region.collisions.some(seed => {
            if (seed.second === node || seed.second.contains(node) || seed.first === node) return true;
            return (region.defectLeaves || []).filter(leaf => seed.first.contains(leaf)).some(leaf => {
              let branch = leaf;
              while (branch.parentElement && branch.parentElement !== seed.first) branch = branch.parentElement;
              return branch === node || branch.contains(node);
            });
          }));
    });
    const defectLeaves = (region.defectLeaves || region.leaves).filter((leaf) => {
      if (this._slotFor(leaf)) return false;
      if (getComputedStyle(leaf).position === 'absolute' && !this._absoluteTextEligible(leaf, region)) return false;
      if (region.defectLeaves) return !this._protected(leaf);
      const ink = this._ink(leaf), box = this._box(region.card);
      if (ink && (ink.right > box.right + this.C.escape_tolerance_px ||
          ink.bottom > box.bottom + this.C.escape_tolerance_px)) return true;
      return region.conflicts.some((conflict) => conflict.owned.includes(leaf));
    });
    if (fontFactor < 1 && defectLeaves.some((leaf) => {
      const item = this._struct(leaf);
      return item.transform !== 'none' || item.scale !== 'none' || item.zoom !== '1' ||
        (['absolute', 'fixed'].includes(item.position) && !this._absoluteTextEligible(leaf, region));
    })) return {invalid: 'unsupported_geometry'};
    const font = this._fontPlan(region.leaves, defectLeaves, fontFactor, region);
    if (font.invalid) return font;
    const spacing = this._spacingPlan(
      spacingNodes, spacingFactor, region.conflicts, spacingFactor < 1 || fontFactor === 1
    );
    const maxSpacingReduction = spacing.length ? Math.max(0, ...spacing.flatMap((item) =>
      item.reductions.map((value) => value.before <= 0 ? 0 : 1 - value.after / value.before))) : 0;
    return {plan: font.plan.concat(spacing), shrink: font.shrink,
      spacing_reduction: maxSpacingReduction, factors: font.factors};
  },
  _signature(region, plan) {
    return [region.card.getAttribute('data-qid')].concat(plan.map((item) =>
      [item.node.getAttribute('data-qid'), item.kind, item.declarations].join('|')).sort()).join('||');
  },
  _fontHard(leaves) {
    const leafSet = new Set(leaves), ptPerPx = this._number(this.P.pt_per_css_px,
      this._number(this.P.export_scale_manifest && this.P.export_scale_manifest.pt_per_css_px,
        this.C.default_pt_per_css_px));
    const floor = this._number(this.P.floor_pt, this.C.default_floor_pt), finalByGroup = new Map();
    const factorsByGroup = new Map();
    for (const run of this._runs.filter((item) => leafSet.has(item.node))) {
      const scale = this._effectiveScale(run.measure_node), finalPt = scale === null ? null :
        this._px(getComputedStyle(run.measure_node).fontSize) * scale * ptPerPx;
      if (!(finalPt > 0) || finalPt < this._minimumPt(run.original_pt)) return false;
      if (run.original_pt >= floor && finalPt > run.original_pt) return false;
      const previous = finalByGroup.get(run.group_id);
      finalByGroup.set(run.group_id, previous === undefined ? finalPt : Math.min(previous, finalPt));
      if (!factorsByGroup.has(run.group_id)) factorsByGroup.set(run.group_id, []);
      factorsByGroup.get(run.group_id).push(finalPt / run.original_pt);
    }
    for (const factors of factorsByGroup.values()) {
      const first = factors[0];
      if (factors.some((factor) => Math.abs(factor - first) > 1e-10)) return false;
    }
    const groups = this._groups.filter((group) => finalByGroup.has(group.group_id));
    const rank = (role) => ['title', 'heading'].includes(role) ? 3 :
      ['supporting', 'caption', 'note'].includes(role) ? 1 :
      role === 'body' || role === 'label' ? 2 : 0;
    for (const first of groups) for (const second of groups) {
      if (!first.card_qid || first.card_qid !== second.card_qid || rank(first.role) === 0 ||
          rank(second.role) === 0 || rank(first.role) <= rank(second.role)) continue;
      if (!(first.original_pt > second.original_pt * 1.01)) continue;
      const originalRatio = first.original_pt / second.original_pt;
      if (finalByGroup.get(first.group_id) / finalByGroup.get(second.group_id) <
          Math.max(1.01, 0.90 * originalRatio)) return false;
    }
    return true;
  },
  _regionBaseline(region, qids) {
    const cardBox = this._box(region.card), outside = new Map();
    for (const node of qids) if (node !== region.card && !region.card.contains(node)) outside.set(node, this._box(node));
    const nested = Array.from(region.card.querySelectorAll('[data-qid]')).filter((node) => this._isCardLike(node));
    const nestedEscapes = new Map(nested.map((node) => [node, this._escape(node, this._regionLeaves(node), 0)]));
    const movable = [region.card].concat(Array.from(region.card.querySelectorAll('[data-qid]')));
    const movableBoxes = new Map(movable.map((node) => [node, this._box(node)]));
    return {cardBox: cardBox, outside: outside, nestedEscapes: nestedEscapes,
      movableBoxes: movableBoxes,
      occluded: new Map(region.leaves.map((leaf) => [leaf, this._textOccluded(leaf)])),
      cardHeight: Math.max(1, cardBox.height)};
  },
  _textOccluded(leaf) {
    for (const text of this._ownedTextNodes(leaf)) {
      const range = document.createRange(); range.selectNodeContents(text);
      for (const rect of range.getClientRects()) {
        if (rect.width <= 0 || rect.height <= 0) continue;
        const points = [[rect.left + rect.width / 2, rect.top + rect.height / 2],
          [rect.left + Math.min(2, rect.width / 2), rect.top + rect.height / 2],
          [rect.right - Math.min(2, rect.width / 2), rect.top + rect.height / 2]];
        for (const point of points) {
          const stack = document.elementsFromPoint ? document.elementsFromPoint(point[0], point[1]) :
            [document.elementFromPoint(point[0], point[1])];
          for (const candidate of stack) {
            if (!candidate) continue;
            if (candidate === leaf || leaf.contains(candidate) || candidate.contains(leaf)) break;
            const paints = this._paintRects(candidate, false).some(box => point[0] >= box.left &&
              point[0] <= box.right && point[1] >= box.top && point[1] <= box.bottom);
            if (paints) return true;
          }
        }
      }
    }
    return false;
  },
  _hardRegion(region, baseline) {
    this._measurements += 1;
    if (!this._sameBox(baseline.cardBox, this._box(region.card))) return {ok: false, reason: 'outside_geometry'};
    for (const [node, box] of baseline.outside) if (!this._sameBox(box, this._box(node)))
      return {ok: false, reason: 'outside_geometry'};
    if (this._escape(region.card, region.leaves, this.C.geometry_tolerance_px) > this.C.escape_tolerance_px)
      return {ok: false, reason: 'text_escape'};
    if (this._flowBoxEscape(region.card) > this.C.escape_tolerance_px)
      return {ok: false, reason: 'flow_box_escape'};
    if (this._flowHeightConflicts(region.card, region.leaves).length)
      return {ok: false, reason: 'flow_height'};
    for (const [node, escape] of baseline.nestedEscapes) {
      if (this._escape(node, this._regionLeaves(node), 0) > Math.max(this.C.escape_tolerance_px, escape) + 0.1)
        return {ok: false, reason: 'nested_escape'};
    }
    for (const leaf of region.leaves) {
      const ink = this._ink(leaf); if (!ink) continue;
      if (!baseline.occluded.get(leaf) && this._textOccluded(leaf)) return {ok: false, reason: 'occlusion'};
      if (ink.left < -0.5 || ink.top < -0.5 || ink.right > innerWidth + 0.5 || ink.bottom > innerHeight + 0.5)
        return {ok: false, reason: 'off_canvas'};
      const style = getComputedStyle(leaf), box = this._box(leaf);
      if (['hidden', 'clip'].includes(style.overflowX) && (ink.left < box.left - 0.5 || ink.right > box.right + 0.5))
        return {ok: false, reason: 'clipping'};
      if (['hidden', 'clip'].includes(style.overflowY) && (ink.top < box.top - 0.5 || ink.bottom > box.bottom + 0.5))
        return {ok: false, reason: 'clipping'};
    }
    if (!this._fontHard(region.leaves)) return {ok: false, reason: 'font_or_hierarchy'};
    let displacement = 0;
    for (const [node, before] of baseline.movableBoxes) {
      const after = this._box(node); displacement = Math.max(displacement, Math.abs(after.top - before.top));
    }
    return {ok: true, displacement: displacement / baseline.cardHeight};
  },
  _candidateCost(candidate, displacement) {
    const requestedMax = this._preferredMaxShrink();
    const maxShrink = requestedMax > 0 ? requestedMax : this.C.default_max_shrink;
    const T = candidate.shrink / maxShrink, S = candidate.spacing_reduction / 0.60, D = displacement;
    const M = candidate.morph_loss || 0;
    return {T: T, S: S, D: D, M: M, total: 0.60 * T + 0.30 * S + 0.10 * D + 0.10 * M};
  },
  _rulesHaveImportant(rules) {
    for (const rule of Array.from(rules || [])) {
      if (rule && rule.style) for (const property of Array.from(rule.style))
        if (rule.style.getPropertyPriority(property) === 'important') return true;
      let nested = null; try { nested = rule && rule.cssRules; } catch (_) { return true; }
      if (nested && this._rulesHaveImportant(nested)) return true;
    }
    return false;
  },
  _candidateLowerBound(candidate) {
    for (const sheet of Array.from(document.styleSheets || [])) {
      let rules = null; try { rules = sheet.cssRules; } catch (_) { return 0; }
      if (this._rulesHaveImportant(rules)) return 0;
    }
    let provenReduction = 0;
    for (const item of candidate.plan.filter((value) => value.kind === 'spacing')) {
      if (item.node.style && Array.from(item.node.style).some((property) =>
          item.node.style.getPropertyPriority(property) === 'important')) return 0;
      const style = getComputedStyle(item.node);
      const animated = String(style.animationName || 'none').split(',')
        .some((value) => value.trim() !== 'none');
      const transitioning = String(style.transitionDuration || '0s').split(',').some((value) => {
        const text = value.trim().toLowerCase();
        return text.endsWith('ms') ? this._number(text.slice(0, -2), 0) > 0 :
          text.endsWith('s') ? this._number(text.slice(0, -1), 0) > 0 : false;
      });
      if (animated || transitioning) continue;
      for (const value of item.reductions || []) if (value.before > 0) {
        const conservativeAfter = value.after + 0.0001;
        provenReduction = Math.max(provenReduction, Math.max(0, 1 - conservativeAfter / value.before));
      }
    }
    return 0.30 * (provenReduction / 0.60);
  },
  _measuredCost(candidate, plan, displacement) {
    const ptPerPx = this._number(this.P.pt_per_css_px,
      this._number(this.P.export_scale_manifest && this.P.export_scale_manifest.pt_per_css_px,
        this.C.default_pt_per_css_px));
    const textNodes = new Set(plan.filter((item) => item.kind === 'text').map((item) => item.node));
    let shrink = 0, spacingReduction = 0;
    for (const run of this._runs.filter((item) => textNodes.has(item.node))) {
      const scale = this._effectiveScale(run.measure_node);
      const finalPt = scale === null ? 0 :
        this._px(getComputedStyle(run.measure_node).fontSize) * scale * ptPerPx;
      shrink = Math.max(shrink, Math.max(0, 1 - finalPt / run.original_pt));
    }
    for (const item of plan.filter((value) => value.kind === 'spacing')) {
      const style = getComputedStyle(item.node);
      for (const value of item.reductions) {
        const finalPx = this._px(style[value.name]);
        if (value.before > 0) spacingReduction = Math.max(spacingReduction, Math.max(0, 1 - finalPx / value.before));
      }
    }
    return this._candidateCost({shrink: shrink, spacing_reduction: spacingReduction,
      morph_loss: candidate.morph ? candidate.morph.loss : 0}, displacement);
  },
  _better(first, second) {
    if (!second) return true;
    if (first.cost.total !== second.cost.total) return first.cost.total < second.cost.total;
    if (first.cost.T !== second.cost.T) return first.cost.T < second.cost.T;
    const properties = plan => new Set(plan.flatMap(item => item.declarations.split(';')
      .filter(Boolean).map(declaration => item.node.getAttribute('data-qid') + ':' + declaration.split(':')[0]))).size;
    const firstCount = properties(first.plan), secondCount = properties(second.plan);
    if (firstCount !== secondCount) return firstCount < secondCount;
    return first.order < second.order;
  },
  _evaluate(region, baseline, fontFactor, spacingFactor, phase, seen, best, useSpace) {
    const order = this._candidateOrder++, preferredMaxShrink = this._preferredMaxShrink();
    const rootQid = region.card.getAttribute('data-qid');
    const requestedExtendedShrink = fontFactor < 1 - preferredMaxShrink - 1e-10;
    const record = {candidate_id: [rootQid, phase, order].join(':'), phase: phase,
      root_qid: rootQid, target_qids: Array.from(new Set((region.defectLeaves || [])
        .map(node => node.getAttribute('data-qid')).filter(Boolean))),
      font_factor: fontFactor, spacing_factor: spacingFactor, uses_parent_space: Boolean(useSpace),
      extended_shrink: requestedExtendedShrink,
      order: order, outcome: null, lower_bound: null, cost: null};
    if (fontFactor < 1 - preferredMaxShrink && !this._allowsExtendedShrink()) {
      record.outcome = 'removed:relative_shrink_limit';
      record.rejection = this._candidateRejection('relative_shrink_limit', 'plan', {
        requested_factor: fontFactor, preferred_max_shrink: preferredMaxShrink,
        policy_revision: this._policyRevision(), floor_pt: this._number(this.P.floor_pt, this.C.default_floor_pt)});
      this._last.candidates.push(record);
      this._last.telemetry.policy_removed += 1; return null;
    }
    const candidate = this._candidatePlan(region, fontFactor, spacingFactor);
    record.actual_font_factors = candidate.factors || [];
    record.floor_clamped = (candidate.factors || []).some(factor => factor > fontFactor + 1e-10);
    candidate.extendedShrink = requestedExtendedShrink &&
      (candidate.factors || []).some(factor => 1 - factor > preferredMaxShrink + 1e-10);
    if (!candidate.invalid) record.extended_shrink = candidate.extendedShrink;
    if (candidate.invalid) {
      record.outcome = 'removed:' + candidate.invalid;
      record.rejection = this._candidateRejection(candidate.invalid, 'plan', {
        policy_revision: this._policyRevision()});
      this._last.candidates.push(record);
      this._last.telemetry.policy_removed += 1; return null;
    }
    const saved = this._save(Array.from(baseline.boxes.keys()));
    this._applyPlan(candidate.plan);
    if (useSpace) {
      let space = this._parentPlan(region, baseline); record.space = space.measurements || [];
      if (space.invalid === 'parent_space_unavailable' && region.collisions.some(seed => seed.kind === 'absolute_rows')) {
        space = this._morphPlan(region, baseline, candidate.plan, fontFactor); record.morph = space.diagnostic;
        if (space.morph) candidate.morph = space.morph;
      }
      if (space.invalid) {
        this._restore(saved); record.outcome = 'removed:' + space.invalid;
        record.rejection = this._candidateRejection(space.invalid, 'parent_plan', {
          measurements: record.space, rule: space.limit_reason || space.invalid});
        this._last.candidates.push(record);
        return space.invalid === 'budget_exhausted' ? 'budget' : null;
      }
      if (space.rootGrowth) candidate.rootGrowth = space.rootGrowth;
      candidate.plan = this._mergePlans(candidate.plan, space.plan); this._applyPlan(candidate.plan);
    }
    const signature = this._signature(region, candidate.plan);
    if (seen.has(signature)) {
      this._restore(saved);
      record.outcome = 'removed:duplicate_declarations';
      record.rejection = this._candidateRejection('duplicate_declarations', 'deduplicate');
      this._last.candidates.push(record);
      this._last.telemetry.duplicates += 1; return null;
    }
    seen.add(signature);
    const lower = this._candidateLowerBound(candidate); record.lower_bound = this._round(lower, 8);
    if (best && lower > best.cost.total) {
      this._restore(saved);
      record.outcome = 'pruned:strict_cost_lb';
      record.rejection = {code: 'reject.dominated', stage: 'cost_lower_bound',
        lower_bound: record.lower_bound, selected_cost: this._round(best.cost.total, 8)};
      this._last.candidates.push(record);
      this._last.telemetry.pruned += 1; return null;
    }
    if (!this._budgetLeft()) {
      this._restore(saved);
      record.outcome = 'unmeasured:budget';
      record.rejection = this._candidateRejection('budget_exhausted', 'measurement');
      this._last.candidates.push(record); return 'budget';
    }
    const hard = this._scopeHard(region, baseline, candidate);
    this._last.telemetry.evaluated += 1;
    if (!hard.ok) {
      if (record.morph) record.morph.outcome = 'rejected:' + hard.reason;
      if (hard.qid) record.geometry_rejection = {qid: hard.qid, property: hard.property,
        delta: hard.delta, cap: hard.cap, limit: hard.cap, morph_allowed: hard.morph_allowed, rule: hard.rule};
      if (hard.diagnostic) record.geometry_rejection = hard.diagnostic;
      this._restore(saved); record.outcome = 'rejected:' + hard.reason;
      record.rejection = this._candidateRejection(hard.reason, 'hard_gate', record.geometry_rejection);
      this._last.candidates.push(record); return null;
    }
    const cost = this._measuredCost(candidate, candidate.plan, hard.displacement); this._restore(saved);
    record.outcome = 'passed'; record.cost = {T: this._round(cost.T, 6), S: this._round(cost.S, 6),
      D: this._round(cost.D, 6), M: this._round(cost.M || 0, 6), total: this._round(cost.total, 6)};
    if (record.morph) record.morph.outcome = 'passed';
    this._last.candidates.push(record);
    record.declarations = candidate.plan.map(item => ({qid: item.node.getAttribute('data-qid'),
      kind: item.kind, declarations: item.declarations}));
    return {candidate: candidate, plan: candidate.plan, cost: cost, order: order, record: record,
      fontFactor: fontFactor, spacingFactor: spacingFactor, phase: phase, useSpace: useSpace};
  },
  _commitRegion(region, baseline, selected) {
    if (!this._budgetLeft()) {
      this._last.unresolved_reasons.push('budget_exhausted'); return 'budget';
    }
    const saved = this._save(Array.from(baseline.boxes.keys()));
    this._applyPlan(selected.plan); const hard = this._scopeHard(region, baseline, selected.candidate);
    if (!hard.ok) {
      this._restore(saved); selected.record.outcome = 'rejected:commit:' + hard.reason;
      selected.record.rejection = this._candidateRejection(hard.reason, 'commit', hard.diagnostic || {
        qid: hard.qid, property: hard.property, delta: hard.delta, cap: hard.cap, rule: hard.rule});
      return false;
    }
    const qid = region.card.getAttribute('data-qid');
    const hasText = selected.plan.some((item) => item.kind === 'text');
    const hasSpacing = selected.plan.some((item) => item.kind === 'spacing');
    const operation = {kind: selected.useSpace && !hasText ? 'parent_reflow' : hasText && hasSpacing ? 'combined' :
      hasText ? 'text_scaled' : 'spacing_adjusted', qid: qid,
      font_factor: selected.fontFactor, spacing_factor: selected.spacingFactor,
      cost: this._round(selected.cost.total, 6)};
    if (selected.candidate.morph) operation.morph = selected.candidate.morph.diagnostic;
    if (selected.candidate.rootGrowth) operation.root_growth = selected.candidate.rootGrowth;
    this._last.operations.push(operation);
    selected.record.outcome = 'committed'; selected.record.commit = {status: 'accepted', operation: operation.kind};
    this._last.cards.push({qid: qid, escape_px: this._round(region.escape_px, 2),
      outcome: operation.kind, font_factor: selected.fontFactor, spacing_factor: selected.spacingFactor});
    for (const run of this._runs.filter((item) => region.card.contains(item.node))) this._committedRuns.add(run.run_id);
    return true;
  },
  _fitRegion(region, qids) {
    if (this._runs.some((run) => this._committedRuns.has(run.run_id) && region.card.contains(run.node))) {
      this._last.unresolved_reasons.push('unsupported_geometry'); return false;
    }
    const baseline = this._parentSnapshot(region.card, qids), seen = new Set();
    // Every space and font candidate is measured from this one snapshot and
    // competes in one pool; no candidate kind can commit before another runs.
    const base = [[1, 1, true], [1, 0.85, true]]
      .concat(this.C.light_candidates.concat(this.C.font_candidates, this.C.combined_candidates)
        .map(pair => [pair[0], pair[1], false]))
      .concat([[1, 0.70, true], [1, 0.40, true], [0.975, 0.85, true], [0.95, 0.70, true], [0.90, 0.40, true]]);
    const extended = this._extendedFontFactors(region).flatMap(factor =>
      [[factor, 1, false], [factor, 0.70, false], [factor, 0.70, true]]);
    const trials = base.concat(extended), bestByKind = {space: null, preferred: null, extended: null};
    let best = null;
    for (const pair of trials) {
      const phase = pair[2] ? 'E' : pair[1] === 1 ? 'B' : 'C';
      const selected = this._evaluate(region, baseline, pair[0], pair[1], phase, seen, best, pair[2]);
      if (selected === 'budget') {
        this._last.partial = true; this._last.unresolved_reasons.push('budget_exhausted'); return false;
      }
      if (selected) {
        const kind = selected.candidate.extendedShrink ? 'extended' :
          selected.useSpace && !selected.plan.some(item => item.kind === 'text') ? 'space' : 'preferred';
        if (this._better(selected, bestByKind[kind])) bestByKind[kind] = selected;
        if (this._better(selected, best)) best = selected;
      }
    }
    const rootQid = region.card.getAttribute('data-qid'), comparison = this._extendedComparison(rootQid);
    const passedExtended = this._last.candidates.filter(item => item.root_qid === rootQid &&
      item.extended_shrink === true && item.outcome === 'passed');
    const comparable = [bestByKind.space, bestByKind.preferred].filter(Boolean);
    if (passedExtended.length && !comparison.comparison_complete) {
      for (const record of passedExtended) {
        record.outcome = 'rejected:font_extended_comparison_incomplete';
        record.rejection = {code: 'reject.font_extended_comparison_incomplete', stage: 'comparison'};
        record.extended_shrink_basis = comparison;
      }
      if (best && best.candidate.extendedShrink)
        best = comparable.reduce((selected, candidate) => this._better(candidate, selected) ? candidate : selected, null);
    } else if (best && best.candidate.extendedShrink) {
      if (comparable.some(candidate => candidate.cost.total <= best.cost.total + 1e-10))
        best = comparable.reduce((selected, candidate) => this._better(candidate, selected) ? candidate : selected, null);
      else best.record.extended_shrink_basis = Object.assign({}, comparison, {
        preferred_max_shrink: this._preferredMaxShrink(),
        floor_pt: this._number(this.P.floor_pt, this.C.default_floor_pt)});
    }
    if (best) {
      const committed = this._commitRegion(region, baseline, best);
      if (committed === true) return true;
      if (committed === 'budget') return false;
    }
    const records = this._last.candidates.filter(item => item.root_qid === rootQid);
    const rejectionCounts = {};
    for (const item of records) if (item.rejection && item.rejection.code)
      rejectionCounts[item.rejection.code] = (rejectionCounts[item.rejection.code] || 0) + 1;
    this._last.search_termination.push({code: 'search.candidates_exhausted', root_qid: rootQid,
      evaluated_candidates: records.filter(item => ['passed', 'committed'].includes(item.outcome) ||
        String(item.outcome || '').startsWith('rejected:')).length,
      unevaluated_candidates: records.filter(item => String(item.outcome || '').startsWith('unmeasured:')).length,
      rejection_counts: rejectionCounts,
      remaining_issue: region.collisions.length ? 'defect.content_occluded' : 'defect.text_clipped'});
    if (rejectionCounts['reject.font_unverifiable']) this._last.unresolved_reasons.push('font_unverifiable');
    if (rejectionCounts['reject.relative_shrink_limit']) this._last.unresolved_reasons.push('relative_shrink_limit');
    this._last.unresolved_reasons.push('candidates_exhausted');
    this._last.cards.push({qid: region.card.getAttribute('data-qid'),
      escape_px: this._round(region.escape_px, 2), outcome: 'unresolved'});
    return false;
  },
"""

__all__ = ["SEARCH_MEMBERS"]
