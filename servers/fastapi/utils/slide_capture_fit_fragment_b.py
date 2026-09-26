# ruff: noqa: E501
"""Browser local-fit script fragment, part B."""

FRAGMENT_B = r"""      const target = String(slot.qid || '');
      let owns = false;
      for (let current = node; current && current !== document.body; current = current.parentElement) {
        if (String(current.getAttribute && current.getAttribute('data-qid') || '') === target) {
          owns = true; break;
        }
      }
      if (!owns) continue;
      const raw = slot.bounds || slot.bbox || slot;
      const x = this._number(raw.x, this._number(raw.left, NaN));
      const y = this._number(raw.y, this._number(raw.top, NaN));
      const width = this._number(raw.width, NaN), height = this._number(raw.height, NaN);
      if ([x, y, width, height].every(Number.isFinite) && width > 0 && height > 0)
        return {kind: String(slot.kind || 'fixed_chrome'), x: x, y: y, width: width, height: height};
    }
    const contract = this.P.page_number_slot_contract;
    if (contract && contract.detector_revision === 'authored-footer-number/v1') {
      for (let owner = node; owner && owner !== document.body; owner = owner.parentElement) {
        if (!owner.hasAttribute('data-qid')) continue;
        const rect = owner.getBoundingClientRect(), font = this._px(getComputedStyle(node).fontSize);
        const text = String(owner.innerText || owner.textContent || '').trim();
        if (rect.left <= 2 && rect.right >= innerWidth - 4 &&
            innerHeight - rect.bottom >= 0 && innerHeight - rect.bottom <= this.C.page_number_max_distance_px &&
            rect.top >= innerHeight * this.C.page_number_min_top_fraction && font > 0 &&
            font <= this.C.page_number_max_font_px && /^\d{1,3}$/.test(text) &&
            Number(text) === contract.expected_number) {
          return {kind:'page_number', x:0, y:this.C.numbered_content_bottom_px,
            width:innerWidth, height:innerHeight - this.C.numbered_content_bottom_px,
            proof:{detector_revision:contract.detector_revision, owner_qid:owner.getAttribute('data-qid'),
              expected_number:contract.expected_number}};
        }
      }
    }
    return null;
  },
  _inside(box, slot) {
    const tolerance = this.C.geometry_tolerance_px;
    return box.left >= slot.x - tolerance && box.top >= slot.y - tolerance &&
      box.right <= slot.x + slot.width + tolerance && box.bottom <= slot.y + slot.height + tolerance;
  },
  _normalizationPlan() {
    const floor = this._number(this.P.floor_pt, this.C.default_floor_pt);
    const ptPerPx = this._number(this.P.pt_per_css_px,
      this._number(this.P.export_scale_manifest && this.P.export_scale_manifest.pt_per_css_px,
        this.C.default_pt_per_css_px));
    const plan = [], failures = [], seen = new Set();
    for (const run of this._runs) {
      const minimum = this._minimumPt(run.original_pt), current = run.current_pt;
      if (current !== null && current >= minimum) continue;
      const node = run.node, qid = run.owner_qid;
      if (seen.has(qid)) continue; seen.add(qid);
      if (!run.editable || this._runs.some((item) => item.node === node && !item.editable)) {
        failures.push('unsupported_geometry'); continue;
      }
      if (!this._replayable(node)) { failures.push('reconstruction_mismatch'); continue; }
      const position = this._struct(node).position, slot = this._slotFor(node), card = this._nearestCard(node);
      if (['absolute', 'fixed'].includes(position) && !slot) {
        failures.push('unsupported_geometry'); continue;
      }
      if (!['absolute', 'fixed'].includes(position) && !card) {
        failures.push('font_floor_unfit'); continue;
      }
      const targetPt = Math.max(floor, ...this._runs.filter((item) => item.node === node)
        .map((item) => this._minimumPt(item.original_pt)));
      const scale = this._effectiveScale(node);
      if (!(scale > 0)) { failures.push('font_unverifiable'); continue; }
      const targetPx = this._ceil(targetPt / (ptPerPx * scale), 4), snap = this._snapshot(node);
      const parts = ['font-size:' + targetPx + 'px'];
      const lineHeight = String(snap.lineHeight || '').trim().toLowerCase();
      if (lineHeight.endsWith('px') && this._cssPx(lineHeight) < targetPx)
        parts.push('line-height:' + this._ceil(targetPx, 4) + 'px');
      plan.push({node: node, kind: 'text', declarations: parts.join(';'), slot: slot,
        operation: {kind: current < floor ? 'floor_normalized' : 'baseline_restored', qid: qid,
          fixed_slot: slot,
          from_pt: this._round(current || 0, 4), to_pt: this._round(targetPt, 4)}});
    }
    return {plan: plan, failures: failures};
  },
  _applyPlan(plan) {
    if (plan.some((item) => !this._replayable(item.node)))
      throw new Error('source_qid_unavailable');
    const changed = new Set();
    for (const item of plan) {
      const entry = this._entry(item.node); entry[item.kind] = item.declarations;
      this._write(item.node); changed.add(item.node);
    }
    return Array.from(changed);
  },
  _box(node) {
    const rect = node.getBoundingClientRect();
    return {left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom,
      width: rect.width, height: rect.height};
  },
  _sameBox(first, second) {
    return ['left', 'top', 'width', 'height'].every((key) =>
      Math.abs(first[key] - second[key]) <= this.C.geometry_tolerance_px);
  },
  _intersects(first, second) {
    return Math.min(first.right, second.right) > Math.max(first.left, second.left) &&
      Math.min(first.bottom, second.bottom) > Math.max(first.top, second.top);
  },
  _validateFixed(plan, before) {
    for (const item of plan.filter((value) => value.slot)) {
      const box = this._box(item.node); if (!this._inside(box, item.slot)) return false;
      for (const [other, oldBox] of before) {
        if (other === item.node || item.node.contains(other) || other.contains(item.node)) continue;
        const now = this._box(other);
        if (!this._intersects(oldBox, before.get(item.node)) && this._intersects(now, box)) return false;
      }
    }
    return true;
  },
  _globalGeometryHard(before, qids) {
    const affected = new Set();
    for (const card of this._last.cards) {
      const node = qids.find((item) => item.getAttribute('data-qid') === card.qid);
      if (node) affected.add(node);
    }
    for (const operation of this._last.operations.filter((item) =>
      item.kind === 'floor_normalized' || item.kind === 'baseline_restored')) {
      const node = qids.find((item) => item.getAttribute('data-qid') === operation.qid);
      const card = node && this._nearestCard(node); if (card) affected.add(card);
    }
    for (const node of qids) {
      const slot = this._slotFor(node);
      if (slot) { if (!this._inside(this._box(node), slot)) return false; else continue; }
      const owner = Array.from(affected).find((card) => card === node || card.contains(node));
      if (owner && owner !== node) continue;
      const initial = before.get(node);
      if (initial && !this._sameBox(initial, this._box(node)) && !this._committedRootGrowthAllowed(node, initial)) return false;
    }
    return true;
  },
  _regionLeaves(card) {
    return Array.from(card.querySelectorAll('[data-qid]')).filter((node) => {
      if (!this._eligible(node) || this._ownedTextNodes(node).length === 0) return false;
      for (let current = node; current && current !== card; current = current.parentElement) {
        if (['absolute', 'fixed'].includes(this._struct(current).position)) return false;
      }
      return true;
    });
  },
  _regions(qids) {
    const found = [];
    for (const card of qids.filter((node) => {
      const item = this._struct(node);
      const fixedFlowContainer = item.explicitHeight !== null &&
        ['block', 'flex', 'grid'].includes(item.display) &&
        ['static', 'relative'].includes(item.position);
      return this._isCardLike(node, item) || fixedFlowContainer;
    })) {
      const leaves = this._regionLeaves(card); if (!leaves.length) continue;
      const conflicts = this._flowHeightConflicts(card, leaves), escape = this._escape(card, leaves, 0);
      const flowBoxEscape = this._flowBoxEscape(card);
      if (escape > this.C.escape_tolerance_px || flowBoxEscape > this.C.escape_tolerance_px || conflicts.length)
        found.push({card: card, leaves: leaves, conflicts: conflicts,
          escape_px: Math.max(escape, flowBoxEscape), flow_box_escape_px: flowBoxEscape});
    }
    const set = new Set(found.map((item) => item.card));
    return found.filter((item) => !Array.from(set).some((child) => child !== item.card && item.card.contains(child)))
      .sort((left, right) => qids.indexOf(left.card) - qids.indexOf(right.card));
  },
__LOCAL_SEARCH_MEMBERS__
  _finalRuns() {
    const ptPerPx = this._number(this.P.pt_per_css_px,
      this._number(this.P.export_scale_manifest && this.P.export_scale_manifest.pt_per_css_px,
        this.C.default_pt_per_css_px));
    return this._runs.map((run) => {
      const scale = this._effectiveScale(run.measure_node), finalPt = scale === null ? null :
        this._px(getComputedStyle(run.measure_node).fontSize) * scale * ptPerPx;
      const fontEvidence = this._fontEvidence(run.measure_node,
        String(run.text_node && run.text_node.textContent || ''));
      return {run_id: run.run_id, owner_qid: run.owner_qid, content_digest: run.content_digest,
        text_node_ordinal: run.text_node_ordinal, start_offset: run.start_offset, end_offset: run.end_offset,
        ancestor_qids: run.ancestor_qids,
        group_id: run.group_id, role: run.role, original_pt: run.original_pt || 0,
        final_pt: finalPt,
        minimum_allowed_pt: this._minimumPt(run.original_pt),
        preferred_minimum_pt: this._preferredMinimumPt(run.original_pt), exceeds_preferred_shrink:
          finalPt !== null && finalPt < this._preferredMinimumPt(run.original_pt) - 1e-10,
        shrink_ratio: finalPt === null ? null : this._round(Math.max(0, 1 - finalPt / run.original_pt), 6),
        font_loaded: fontEvidence.loaded, resolved_font_family: fontEvidence.family,
        font_resolution_source: fontEvidence.source};
    });
  },
  _editList() {
    const qids = Array.from(document.querySelectorAll('[data-qid]'));
    return Array.from(this._applied.entries()).map(([node, entry]) => ({node: node,
      qid: entry.qid || node.getAttribute('data-qid'), declarations: this._declarations(entry)}))
      .filter((item) => item.declarations).sort((left, right) => qids.indexOf(left.node) - qids.indexOf(right.node))
      .map((item) => ({qid: item.qid, declarations: item.declarations}));
  },
  _status() {
    if (this._last.unresolved_reasons.length || this._last.partial || this._last.error) return 'unresolved';
    const kinds = new Set(this._last.operations.map((item) => item.kind));
    const floor = kinds.has('floor_normalized') || kinds.has('baseline_restored');
    const spacing = kinds.has('spacing_adjusted') || kinds.has('parent_reflow');
    const text = kinds.has('text_scaled'), combined = kinds.has('combined');
    if (combined || (spacing && text)) return 'combined';
    if (text) return 'text_scaled'; if (spacing) return 'spacing_adjusted'; if (floor) return 'floor_normalized';
    return 'unchanged';
  },
  _restoreAll() {
    for (const [node, snap] of this._snapshots) {
      if (snap.style === null) node.removeAttribute('style'); else node.setAttribute('style', snap.style);
    }
    this._applied = new Map(); this._committedRuns = new Set();
  },
__LOCAL_RUNNER_MEMBERS__
};
"""
